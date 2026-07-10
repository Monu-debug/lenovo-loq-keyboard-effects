"""
Keyboard Backlight Effects Controller for Lenovo laptops (IdeaPad / LOQ / Legion)
================================================================================
Interfaces with Lenovo Vantage's IdeaNotebookAddin DLLs to control keyboard backlight.
Uses a persistent PowerShell subprocess for low-latency command execution.
Includes a global keyboard hook to react to keystrokes in real-time.
"""

import os
import sys
import time
import threading
import ctypes
from ctypes import wintypes
import subprocess
import glob
import random
import datetime
import json
import winreg
import pystray
from PIL import Image, ImageDraw
from flask import Flask, render_template, jsonify, request

# Configure Flask templates path to support PyInstaller --onefile bundle
if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    app = Flask(__name__, template_folder=template_folder)
else:
    app = Flask(__name__)

# Define hook-related types and signatures for ctypes
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)
    ]

# Declare HHOOK type
if not hasattr(wintypes, 'HHOOK'):
    wintypes.HHOOK = wintypes.HANDLE

# Declare HOOKPROC to return c_void_p (64-bit on x64 systems) to prevent overflow crash!
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# Set up user32 and kernel32 signatures for pointer safety
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HMODULE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_void_p

kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

# ---------------------------------------------------------------------------
#  Admin check & self-elevation
# ---------------------------------------------------------------------------

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

# ---------------------------------------------------------------------------
#  Dynamic DLL path finder
# ---------------------------------------------------------------------------

def find_lenovo_dlls():
    """Locate the latest version of Lenovo Vantage's IdeaNotebookAddin DLLs."""
    paths = glob.glob(r"C:\ProgramData\Lenovo\Vantage\Addins\IdeaNotebookAddin\*\KeyboardContract.dll")
    if paths:
        paths.sort()
        latest_contract = paths[-1]
        folder = os.path.dirname(latest_contract)
        return {
            "contract": latest_contract,
            "addin": os.path.join(folder, "IdeaNotebookAddin.dll"),
            "json": os.path.join(folder, "Newtonsoft.Json.dll")
        }
    return None

# ---------------------------------------------------------------------------
#  Keyboard Backlight Controller via Persistent PowerShell
# ---------------------------------------------------------------------------

class KeyboardBacklightController:
    """Controls Lenovo keyboard backlight via IdeaNotebookAddin.dll using persistent PowerShell."""

    def __init__(self):
        self.method = None
        self.current_level = 2  # 0=off, 1=dim, 2=bright
        self.max_level = 2
        self._lock = threading.Lock()
        self.ps_proc = None
        self._admin = is_admin()
        self.system_model = self._get_system_model()
        
        self.detect_method()

    def _get_system_model(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS")
            model = winreg.QueryValueEx(key, "SystemProductName")[0]
            winreg.CloseKey(key)
            return model.strip()
        except Exception:
            return "Lenovo Laptop"

    def detect_method(self):
        """Initialize the persistent PowerShell controller or fall back."""
        dlls = find_lenovo_dlls()
        if dlls:
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                self.ps_proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    startupinfo=startupinfo,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                
                init_script = f"""
                [System.Reflection.Assembly]::LoadFrom('{dlls["contract"]}') | Out-Null
                [System.Reflection.Assembly]::LoadFrom('{dlls["json"]}') | Out-Null
                $asm = [System.Reflection.Assembly]::LoadFrom('{dlls["addin"]}')
                $agentType = $asm.GetType('IdeaNotebookAddin.IdeaNotebookAgent')
                $agent = $agentType.GetMethod('GetInstance', [System.Reflection.BindingFlags]::Public -bor [System.Reflection.BindingFlags]::Static).Invoke($null, $null)
                $setBacklightMethod = $agentType.GetMethods() | Where-Object {{ $_.Name -eq 'SetBacklight' -and $_.GetParameters().Count -eq 2 }}

                function Set-KbdBacklight([string]$level) {{
                    $status = $agentType.GetMethod('GetBacklightStatus', [System.Reflection.BindingFlags]::Public -bor [System.Reflection.BindingFlags]::Instance).Invoke($agent, $null)
                    $list = $status.GetType().GetProperty('List').GetValue($status)
                    $items = $list.GetType().GetProperty('Items').GetValue($list)
                    foreach ($item in $items) {{
                        $keyVal = $item.GetType().GetProperty('key').GetValue($item)
                        if ($keyVal -eq 'KeyboardBacklightStatus') {{
                            $item.GetType().GetProperty('value').SetValue($item, $level)
                        }}
                    }}
                    $req = New-Object Lenovo.Modern.Contracts.Keyboard.KeyboardSettingsRequest
                    $req.List = $list
                    $jsonPayload = [Newtonsoft.Json.JsonConvert]::SerializeObject($req)
                    $resp = $setBacklightMethod.Invoke($agent, @($jsonPayload, $null))
                    Write-Output "OK:$level"
                }}
                """
                self.ps_proc.stdin.write(init_script + "\n")
                self.ps_proc.stdin.flush()
                
                self._send_command("Level_2")
                self.method = "lenovo_vantage_dll"
                return
            except Exception as e:
                print(f"Error initializing Vantage DLL control: {e}")
                self.close_ps()

        self.method = "keyboard_leds"

    def _send_command(self, level_str):
        """Send command to persistent PowerShell stdin."""
        if not self.ps_proc:
            return False
        try:
            self.ps_proc.stdin.write(f"Set-KbdBacklight '{level_str}'\n")
            self.ps_proc.stdin.flush()
            response = self.ps_proc.stdout.readline().strip()
            return response == f"OK:{level_str}"
        except Exception as e:
            print(f"PowerShell communication error: {e}")
            return False

    def close_ps(self):
        """Properly close the PowerShell process."""
        if self.ps_proc:
            try:
                self.ps_proc.stdin.write("exit\n")
                self.ps_proc.stdin.flush()
                self.ps_proc.wait(timeout=2)
            except:
                try:
                    self.ps_proc.kill()
                except:
                    pass
            self.ps_proc = None

    def set_backlight(self, on):
        """Turn backlight fully on or off."""
        if on:
            self.set_brightness(self.max_level)
        else:
            self.set_brightness(0)

    def set_brightness(self, level):
        """Set brightness: 0=off, 1=dim, 2=bright."""
        level = max(0, min(level, self.max_level))
        with self._lock:
            try:
                if self.method == "lenovo_vantage_dll":
                    level_map = {0: "Off", 1: "Level_1", 2: "Level_2"}
                    self._send_command(level_map[level])
                else:
                    self._set_keyboard_leds(level)
            except Exception as e:
                print(f"Set brightness error: {e}")
            self.current_level = level

    def _set_keyboard_leds(self, level):
        """Fallback: toggle Caps/Num/Scroll Lock LEDs."""
        on = level > 0
        VK_NUMLOCK = 0x90
        VK_CAPITAL = 0x14
        VK_SCROLL = 0x91
        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP = 0x0002
        for vk in [VK_NUMLOCK, VK_CAPITAL, VK_SCROLL]:
            state = ctypes.windll.user32.GetKeyState(vk) & 1
            if (on and not state) or (not on and state):
                ctypes.windll.user32.keybd_event(vk, 0x45, KEYEVENTF_EXTENDEDKEY, 0)
                ctypes.windll.user32.keybd_event(vk, 0x45, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)

    def get_status(self):
        names = {
            "lenovo_vantage_dll": "Lenovo Vantage Interface (DLL Mode)",
            "keyboard_leds": "Keyboard LEDs (Caps/Num/Scroll) - Fallback Mode",
        }
        return {
            "method": self.method,
            "method_display": names.get(self.method, self.method),
            "current_level": self.current_level,
            "max_level": self.max_level,
            "is_admin": self._admin,
            "system_model": self.system_model,
        }

# ---------------------------------------------------------------------------
#  Effects Engine
# ---------------------------------------------------------------------------

class EffectEngine:
    EFFECTS_META = [
        {"id": "blink",     "name": "Blink",        "icon": "💡",     "desc": "Classic on / off blinking"},
        {"id": "breathe",   "name": "Breathe",      "icon": "🌊",   "desc": "Smooth fade in and out"},
        {"id": "strobe",    "name": "Strobe",        "icon": "⚡",    "desc": "Rapid strobe flashing"},
        {"id": "heartbeat", "name": "Heartbeat",     "icon": "💓", "desc": "Double-pulse heartbeat rhythm"},
        {"id": "sos",       "name": "SOS",           "icon": "🆘",       "desc": "Morse code SOS signal"},
        {"id": "disco",     "name": "Disco",         "icon": "🪩",     "desc": "Random chaotic flashing"},
        {"id": "lightning", "name": "Lightning",     "icon": "🌩️",   "desc": "Random lightning strikes"},
        {"id": "pulse",     "name": "Pulse",         "icon": "📡",     "desc": "Quick flash, slow fade"},
        {"id": "candle",    "name": "Candle",        "icon": "🕯️",    "desc": "Flickering candle flame"},
        {"id": "binary",    "name": "Binary Clock",  "icon": "🔢",    "desc": "Blinks seconds in binary"},
        {"id": "reactive",  "name": "React",         "icon": "⌨️",     "desc": "On all time, blinks off on typing"},
        {"id": "music",     "name": "Music Reactive", "icon": "🎵",     "desc": "Reacts to computer music beats"},
    ]

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.running = False
        self.current_effect = None
        self.speed = 1.0
        self.mode = 1
        self.hold_behavior = 1 # 1 = Constant blink, 2 = Stay active until held key released
        self._stop = threading.Event()
        self._thread = None
        
        # Keyboard Hook fields
        self._hook_handle = None
        self._hook_thread = None
        self._keypress_event = threading.Event()
        self._keyrelease_event = threading.Event()
        self._hook_proc = None
        self._pressed_keys = set()
        self._keys_lock = threading.Lock()

        # Audio stream fields
        self.sensitivity = 0.5
        self.audio_stream = None
        self.audio_history = []
        self.last_beat_time = 0.0
        self.beat_timer = None

    def start(self, effect, speed=1.0, mode=1, hold_behavior=1):
        self.stop()
        self.current_effect = effect
        self.speed = max(0.1, min(speed, 5.0))
        self.mode = mode
        self.hold_behavior = hold_behavior
        with self._keys_lock:
            self._pressed_keys.clear()
        self.running = True
        self._stop.clear()
        
        # Start global keyboard hook if reactive mode is selected
        if self.current_effect == "reactive":
            self._start_keyboard_hook()
            
        # Start loopback recording if music reactive mode is selected
        if self.current_effect == "music":
            self.start_audio()
            
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        self._stop.set()
        
        # Terminate keyboard hook if active
        self._stop_keyboard_hook()
        
        # Terminate audio recording if active
        self.stop_audio()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self.ctrl.set_backlight(True)
        self.current_effect = None

    def _wait(self, secs):
        self._stop.wait(secs)
        return not self.running

    def _loop(self):
        fns = {
            "blink": self._blink, "breathe": self._breathe,
            "strobe": self._strobe, "heartbeat": self._heartbeat,
            "sos": self._sos, "disco": self._disco,
            "lightning": self._lightning, "pulse": self._pulse,
            "candle": self._candle, "binary": self._binary,
            "reactive": self._reactive, "music": self._music,
        }
        fn = fns.get(self.current_effect)
        if not fn:
            return
        try:
            while self.running:
                if fn():
                    break
        except Exception as exc:
            print(f"Effect error: {exc}")
        finally:
            self.ctrl.set_backlight(True)

    # -- Keyboard Hook Management -------------------------------------------

    def _start_keyboard_hook(self):
        """Starts the low-level global Windows keyboard hook thread."""
        self._keypress_event.clear()
        self._hook_thread = threading.Thread(target=self._hook_message_loop, daemon=True)
        self._hook_thread.start()

    def _stop_keyboard_hook(self):
        """Terminates the hook loop and removes the global hook."""
        if self._hook_handle:
            # Post a quit message to the hook message loop thread
            user32.PostThreadMessageW(self._hook_thread.ident, 0x0012, 0, 0) # WM_QUIT = 0x0012
            self._hook_handle = None
        self._keypress_event.clear()

    def _hook_message_loop(self):
        """Standard Windows message loop that handles keyboard events."""
        def hook_cb(nCode, wParam, lParam):
            try:
                if nCode >= 0 and self.running:
                    kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = kbd.vkCode
                    
                    if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        with self._keys_lock:
                            is_repeat = vk in self._pressed_keys
                            if not is_repeat:
                                self._pressed_keys.add(vk)
                        self._keypress_event.set()
                    elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                        with self._keys_lock:
                            self._pressed_keys.discard(vk)
                            if len(self._pressed_keys) == 0:
                                self._keyrelease_event.set()
            except Exception as e:
                print(f"Keyboard hook exception: {e}")
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        # Keep a reference to prevent GC crash
        self._hook_proc = HOOKPROC(hook_cb)
        hmod = kernel32.GetModuleHandleW(None)
        
        self._hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_proc,
            hmod,
            0
        )

        if not self._hook_handle:
            print(f"Global Keyboard Hook failed! Error: {kernel32.GetLastError()}")
            return

        msg = wintypes.MSG()
        while self.running and user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
            
        user32.UnhookWindowsHookEx(self._hook_handle)

    # -- Effects ------------------------------------------------------------

    def _blink(self):
        d = 0.5 / self.speed
        self.ctrl.set_backlight(True)
        if self._wait(d): return True
        self.ctrl.set_backlight(False)
        if self._wait(d): return True

    def _breathe(self):
        d = 0.35 / self.speed
        self.ctrl.set_brightness(0)
        if self._wait(d): return True
        self.ctrl.set_brightness(1)
        if self._wait(d): return True
        self.ctrl.set_brightness(2)
        if self._wait(d * 1.5): return True
        self.ctrl.set_brightness(1)
        if self._wait(d): return True

    def _strobe(self):
        d = 0.08 / self.speed
        self.ctrl.set_backlight(True)
        if self._wait(d): return True
        self.ctrl.set_backlight(False)
        if self._wait(d): return True

    def _heartbeat(self):
        b, p, r = 0.12 / self.speed, 0.15 / self.speed, 0.7 / self.speed
        self.ctrl.set_brightness(2)
        if self._wait(b): return True
        self.ctrl.set_brightness(0)
        if self._wait(p): return True
        self.ctrl.set_brightness(2)
        if self._wait(b): return True
        self.ctrl.set_brightness(0)
        if self._wait(r): return True

    def _sos(self):
        dot, dash = 0.15 / self.speed, 0.45 / self.speed
        gap, lgap, wgap = 0.15 / self.speed, 0.45 / self.speed, 1.0 / self.speed
        def send(durs):
            for d in durs:
                self.ctrl.set_backlight(True)
                if self._wait(d): return True
                self.ctrl.set_backlight(False)
                if self._wait(gap): return True
            return False
        if send([dot]*3): return True
        if self._wait(lgap): return True
        if send([dash]*3): return True
        if self._wait(lgap): return True
        if send([dot]*3): return True
        if self._wait(wgap): return True

    def _disco(self):
        self.ctrl.set_backlight(True)
        if self._wait(random.uniform(0.04, 0.25) / self.speed): return True
        self.ctrl.set_backlight(False)
        if self._wait(random.uniform(0.04, 0.25) / self.speed): return True

    def _lightning(self):
        self.ctrl.set_backlight(False)
        if self._wait(random.uniform(0.4, 1.8) / self.speed): return True
        for _ in range(random.randint(1, 3)):
            self.ctrl.set_backlight(True)
            if self._wait(random.uniform(0.03, 0.1) / self.speed): return True
            self.ctrl.set_backlight(False)
            if self._wait(random.uniform(0.04, 0.12) / self.speed): return True

    def _pulse(self):
        self.ctrl.set_brightness(2)
        if self._wait(0.1 / self.speed): return True
        self.ctrl.set_brightness(1)
        if self._wait(0.15 / self.speed): return True
        self.ctrl.set_brightness(0)
        if self._wait(0.5 / self.speed): return True

    def _candle(self):
        self.ctrl.set_backlight(random.random() < 0.75)
        if self._wait(random.uniform(0.05, 0.2) / self.speed): return True

    def _binary(self):
        bits = format(datetime.datetime.now().second, "06b")
        for b in bits:
            if not self.running: return True
            self.ctrl.set_backlight(b == "1")
            if self._wait(0.35 / self.speed): return True
        self.ctrl.set_backlight(False)
        if self._wait(0.6 / self.speed): return True

    def _reactive(self):
        """Keyboard lights react to keypresses based on the selected mode."""
        m = str(self.mode)
        if m == "2":
            base, active = 1, 2
        elif m == "3":
            base, active = 2, 1
        elif m == "4":
            base, active = 0, 1
        elif m == "5":
            base, active = 0, 2
        else: # Default/Mode 1
            base, active = 2, 0
            
        self.ctrl.set_brightness(base)
        self._keypress_event.clear()
        self._keyrelease_event.clear()
        
        # Block until a key is pressed (with a timeout so we check running state periodically)
        if self._keypress_event.wait(timeout=0.2):
            # Reaction
            self.ctrl.set_brightness(active)
            
            if str(self.hold_behavior) == "2":
                # Solid Hold: Stay active as long as any keys are pressed
                min_dur = 0.10 / self.speed
                self._wait(min_dur)
                
                with self._keys_lock:
                    keys_held = len(self._pressed_keys) > 0
                
                if keys_held:
                    self._keyrelease_event.wait(timeout=5.0) # Safety timeout
                
                self.ctrl.set_brightness(base)
                self._keypress_event.clear()
                self._keyrelease_event.clear()
                self._wait(0.04)
            else:
                # Constant Blinking
                dur = 0.12 / self.speed
                self._wait(dur)
                self.ctrl.set_brightness(base)
                self._keypress_event.clear()
                self._wait(0.04)

    def get_status(self):
        return {
            "running": self.running,
            "current_effect": self.current_effect,
            "speed": self.speed,
            "mode": self.mode,
            "hold_behavior": self.hold_behavior,
            "sensitivity": self.sensitivity,
        }

    # -- Music Reactive (Audio Capture & Beat Detection) -----------------------

    def start_audio(self):
        self.stop_audio()
        self.audio_history = []
        self.last_beat_time = 0.0
        self.beat_timer = None
        
        # Set default base level for the sub-mode on start
        base_level = self.get_music_default_level(self.mode)
        self.ctrl.set_brightness(1 if base_level == "Level_1" else (2 if base_level == "Level_2" else 0))

        def get_loopback_device():
            try:
                import sounddevice as sd
                devices = sd.query_devices()
                wasapi_info = sd.query_hostapis()
                wasapi_idx = -1
                for idx, api in enumerate(wasapi_info):
                    if api['name'] == 'Windows WASAPI':
                        wasapi_idx = idx
                        break
                if wasapi_idx == -1:
                    return sd.default.device[0]
                
                # Check for loopback device matching current default output device
                default_output = sd.default.device[1]
                default_output_name = devices[default_output]['name']
                
                for idx, d in enumerate(devices):
                    if d['hostapi'] == wasapi_idx and d['max_input_channels'] > 0:
                        if 'loopback' in d['name'].lower() or default_output_name in d['name']:
                            return idx
                return sd.default.device[0]
            except Exception:
                return None

        try:
            import sounddevice as sd
            device = get_loopback_device()
            self.audio_stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=44100,
                blocksize=1024,
                callback=self._audio_callback
            )
            self.audio_stream.start()
            print(f"Audio stream started on device: {device}")
        except Exception as e:
            print(f"Failed to start loopback stream, trying default input: {e}")
            try:
                import sounddevice as sd
                self.audio_stream = sd.InputStream(
                    channels=1,
                    samplerate=44100,
                    blocksize=1024,
                    callback=self._audio_callback
                )
                self.audio_stream.start()
                print("Audio stream started on default input.")
            except Exception as e2:
                print(f"All audio input streams failed: {e2}")

    def stop_audio(self):
        if self.beat_timer:
            self.beat_timer.cancel()
            self.beat_timer = None
        if self.audio_stream:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
            self.audio_stream = None

    def _audio_callback(self, indata, frames, time_info, status):
        if not self.running or self.current_effect != "music":
            return
        try:
            import numpy as np
            audio_data = indata.flatten()
            
            # Use Fast Fourier Transform (FFT) to extract bass frequencies (43Hz to 215Hz)
            # This isolates kick drums and bass drops from vocals/treble
            fft_vals = np.abs(np.fft.rfft(audio_data))
            bass_val = float(np.mean(fft_vals[1:6]))
            
            # Noise gate: ignore absolute silence or background microphone static
            if bass_val < 0.05:
                return
                
            self.audio_history.append(bass_val)
            if len(self.audio_history) > 43:
                self.audio_history.pop(0)

            avg_bass = sum(self.audio_history) / len(self.audio_history) if self.audio_history else 0
            
            # If the history contains mostly silence, ignore triggering to prevent static clicks
            if avg_bass < 0.03:
                return
            
            # Sensitivity mapping
            # Higher sensitivity = smaller threshold = triggers easily
            # Sensitivity slider: 0.1 to 1.0 (defaults to 0.5)
            threshold = 1.1 + (1.0 - self.sensitivity) * 1.5
            
            curr_time = time.time()
            # Beat check: current bass energy exceeds average bass energy by the threshold
            if bass_val > avg_bass * threshold and (curr_time - self.last_beat_time) > 0.15:
                self.last_beat_time = curr_time
                self.trigger_beat()
        except Exception as e:
            print(f"Audio beat callback error: {e}")

    def get_music_default_level(self, sub_mode):
        if sub_mode in (1, 4):
            return "Level_0"
        elif sub_mode == 2:
            return "Level_1"
        elif sub_mode in (3, 5):
            return "Level_2"
        return "Level_0"

    def trigger_beat(self):
        if self.beat_timer:
            self.beat_timer.cancel()
        
        sub_mode = self.mode
        if sub_mode == 6: # Random mix
            sub_mode = random.randint(1, 5)

        # 1. Always OFF but DIM on beat
        if sub_mode == 1:
            self.ctrl.set_brightness(1)
            self.beat_timer = threading.Timer(0.12, self.reset_backlight, [0])
        # 2. Always DIM but MAX on beat
        elif sub_mode == 2:
            self.ctrl.set_brightness(2)
            self.beat_timer = threading.Timer(0.12, self.reset_backlight, [1])
        # 3. Always BRIGHT (MAX) but DIM on beat
        elif sub_mode == 3:
            self.ctrl.set_brightness(1)
            self.beat_timer = threading.Timer(0.12, self.reset_backlight, [2])
        # 4. Always OFF but MAX on beat
        elif sub_mode == 4:
            self.ctrl.set_brightness(2)
            self.beat_timer = threading.Timer(0.12, self.reset_backlight, [0])
        # 5. Always MAX but OFF on beat
        elif sub_mode == 5:
            self.ctrl.set_brightness(0)
            self.beat_timer = threading.Timer(0.12, self.reset_backlight, [2])

        self.beat_timer.start()

    def reset_backlight(self, level):
        if self.running and self.current_effect == "music":
            self.ctrl.set_brightness(level)

    def _music(self):
        # Async stream handles the logic, wait here
        return self._wait(0.2)

# ---------------------------------------------------------------------------
#  Globals
# ---------------------------------------------------------------------------
controller = KeyboardBacklightController()
engine = EffectEngine(controller)

# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    return jsonify(controller=controller.get_status(), engine=engine.get_status())

@app.route("/api/detect", methods=["POST"])
def api_detect():
    controller.detect_method()
    return jsonify(controller.get_status())

@app.route("/api/effects")
def api_effects():
    return jsonify(EffectEngine.EFFECTS_META)

@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.json or {}
    effect = data.get("effect", "blink")
    speed = float(data.get("speed", 1.0))
    mode = data.get("mode", 1)
    hold_behavior = int(data.get("hold_behavior", 1))
    engine.start(effect, speed, mode, hold_behavior)
    return jsonify(status="started", effect=effect, speed=speed, mode=mode, hold_behavior=hold_behavior)

@app.route("/api/stop", methods=["POST"])
def api_stop():
    engine.stop()
    return jsonify(status="stopped")

@app.route("/api/speed", methods=["POST"])
def api_speed():
    data = request.json or {}
    engine.speed = max(0.1, min(float(data.get("speed", 1.0)), 5.0))
    return jsonify(speed=engine.speed)

@app.route("/api/sensitivity", methods=["POST"])
def api_sensitivity():
    data = request.json or {}
    engine.sensitivity = max(0.1, min(float(data.get("sensitivity", 0.5)), 1.0))
    return jsonify(sensitivity=engine.sensitivity)

@app.route("/api/toggle", methods=["POST"])
def api_toggle():
    data = request.json or {}
    on = data.get("on", True)
    controller.set_backlight(on)
    return jsonify(status="on" if on else "off")

# ---------------------------------------------------------------------------
#  System Settings & Startup (Registry / Tray Configuration)
# ---------------------------------------------------------------------------

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME = "LOQKeyboardEffects"

def set_startup(enable=True):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
        if enable:
            exe_path = sys.executable
            if getattr(sys, 'frozen', False):
                cmd = f'"{exe_path}" --minimized'
            else:
                cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}" --minimized'
            winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, REG_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"Failed to set startup registry: {e}")
        return False

def get_startup_status():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, REG_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False

def load_settings():
    default = {"start_at_boot": False, "minimize_to_tray": True}
    if not os.path.exists(SETTINGS_FILE):
        return default
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=4)
    except Exception:
        pass

@app.route("/api/settings", methods=["GET"])
def get_settings():
    s = load_settings()
    s["start_at_boot"] = get_startup_status()
    return jsonify(s)

@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.json or {}
    s = load_settings()
    
    if "start_at_boot" in data:
        val = bool(data["start_at_boot"])
        s["start_at_boot"] = val
        set_startup(val)
        
    if "minimize_to_tray" in data:
        s["minimize_to_tray"] = bool(data["minimize_to_tray"])
        
    save_settings(s)
    return jsonify(status="success", settings=s)

# ---------------------------------------------------------------------------
#  Shutdown Hook
# ---------------------------------------------------------------------------

import atexit
@atexit.register
def cleanup():
    global tray_icon
    try:
        if tray_icon:
            tray_icon.stop()
    except Exception:
        pass
    engine.stop()
    controller.close_ps()

# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

LOADING_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Loading...</title>
    <style>
        body {
            background: #09070f;
            color: #f3f1f8;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            overflow: hidden;
            user-select: none;
        }
        .container {
            text-align: center;
        }
        .spinner {
            position: relative;
            width: 80px;
            height: 80px;
            margin: 0 auto 24px;
        }
        .spinner-outer {
            box-sizing: border-box;
            width: 100%;
            height: 100%;
            border: 4px solid rgba(168, 85, 247, 0.1);
            border-radius: 50%;
            border-left-color: #a855f7;
            animation: spin 1.2s cubic-bezier(0.5, 0, 0.5, 1) infinite;
        }
        .spinner-inner {
            box-sizing: border-box;
            position: absolute;
            width: 80%;
            height: 80%;
            top: 10%;
            left: 10%;
            border: 3px solid rgba(236, 72, 153, 0.05);
            border-radius: 50%;
            border-right-color: #ec4899;
            animation: spin-reverse 1.5s cubic-bezier(0.5, 0, 0.5, 1) infinite;
        }
        h2 {
            font-size: 20px;
            font-weight: 600;
            margin: 0 0 8px;
            letter-spacing: 0.5px;
            background: linear-gradient(135deg, #a855f7 0%, #ec4899 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        p {
            font-size: 13px;
            color: #9ca3af;
            margin: 0;
            font-weight: 400;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        @keyframes spin-reverse {
            0% { transform: rotate(360deg); }
            100% { transform: rotate(0deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="spinner">
            <div class="spinner-outer"></div>
            <div class="spinner-inner"></div>
        </div>
        <h2>LOQ Keyboard Effects Lab</h2>
        <p>Connecting to lighting interface engine...</p>
    </div>
    <script>
        function checkServer() {
            fetch('http://127.0.0.1:5000/api/status', { mode: 'no-cors' })
                .then(() => {
                    window.location.href = 'http://127.0.0.1:5000';
                })
                .catch(() => {
                    setTimeout(checkServer, 100);
                });
        }
        setTimeout(checkServer, 100);
    </script>
</body>
</html>
"""

window = None
allow_close = False
tray_icon = None

def create_tray_image():
    try:
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        dc = ImageDraw.Draw(image)
        # Draw violet-edged keyboard icon
        dc.rounded_rectangle([6, 18, 58, 46], radius=6, outline=(168, 85, 247, 255), width=3)
        dc.rectangle([12, 24, 20, 30], fill=(168, 85, 247, 255))
        dc.rectangle([24, 24, 40, 30], fill=(168, 85, 247, 255))
        dc.rectangle([44, 24, 52, 30], fill=(168, 85, 247, 255))
        dc.rectangle([12, 34, 52, 40], fill=(168, 85, 247, 255))
        return image
    except Exception:
        # Fallback to white rectangle if PIL fails
        return Image.new('RGB', (64, 64), (168, 85, 247))

def show_window(icon, item):
    global window
    if window:
        window.show()
        window.restore()

def exit_app(icon, item):
    global allow_close, window
    allow_close = True
    icon.stop()
    if window:
        window.destroy()

def setup_tray():
    global tray_icon
    try:
        menu = pystray.Menu(
            pystray.MenuItem("Show Dashboard", show_window, default=True),
            pystray.MenuItem("Exit App", exit_app)
        )
        tray_icon = pystray.Icon(
            name="LOQKeyboardEffects",
            icon=create_tray_image(),
            title="LOQ Keyboard Effects Lab",
            menu=menu
        )
        tray_icon.run()
    except Exception as e:
        print(f"Tray error: {e}")

def on_closing():
    global allow_close, window
    try:
        settings = load_settings()
        if settings.get("minimize_to_tray", True) and not allow_close:
            if window:
                window.hide()
            return False
    except Exception:
        pass
    return True

def run_flask():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    if not is_admin():
        print("Requesting Administrator privileges...")
        try:
            if getattr(sys, 'frozen', False):
                # Under PyInstaller, sys.executable points to the compiled EXE.
                # Running sys.executable with runas elevates it directly.
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable,
                    " ".join(sys.argv[1:]), None, 1
                )
            else:
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable,
                    f'"{os.path.abspath(__file__)}"', None, 1
                )
            sys.exit(0)
        except Exception:
            print("Could not elevate. Please right-click and Run as Administrator.")
            sys.exit(1)

    method_name = controller.get_status()["method_display"]
    print("")
    print("=" * 50)
    print("  Keyboard Backlight Effects Controller")
    print("  Running as ADMINISTRATOR")
    print("=" * 50)
    print(f"  Method : {method_name}")
    print(f"  URL    : http://localhost:5000")
    print("=" * 50)
    print("")

    # Start Flask server in background thread
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    # Start System Tray icon in background thread
    tray_thread = threading.Thread(target=setup_tray, daemon=True)
    tray_thread.start()

    # Determine if starting in minimized/silent mode (e.g. from windows boot registry)
    hidden_start = "--minimized" in sys.argv or "--silent" in sys.argv

    # Launch PyWebView desktop application window
    import webview
    window = webview.create_window(
        title="Lenovo LOQ Keyboard Effects Lab",
        html=LOADING_HTML,
        width=850,
        height=720,
        resizable=True,
        min_size=(600, 500),
        hidden=hidden_start
    )
    
    # Hook window closing event to handle tray minimize
    window.events.closing += on_closing
    
    webview.start()
