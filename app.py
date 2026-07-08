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
from flask import Flask, render_template, jsonify, request

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
                self.ps_proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
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
            
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        self._stop.set()
        
        # Terminate keyboard hook if active
        self._stop_keyboard_hook()
        
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
            "reactive": self._reactive,
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
        }

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

@app.route("/api/toggle", methods=["POST"])
def api_toggle():
    data = request.json or {}
    on = data.get("on", True)
    controller.set_backlight(on)
    return jsonify(status="on" if on else "off")

# ---------------------------------------------------------------------------
#  Shutdown Hook
# ---------------------------------------------------------------------------

import atexit
@atexit.register
def cleanup():
    engine.stop()
    controller.close_ps()

# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not is_admin():
        print("Requesting Administrator privileges...")
        try:
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
    print(f"  Stop   : Ctrl+C")
    print("=" * 50)
    print("")
    app.run(host="127.0.0.1", port=5000, debug=False)
