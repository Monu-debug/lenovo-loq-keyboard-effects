"""
Keyboard Backlight Agent for Lenovo LOQ/IdeaPad Laptops
================================================================================
Silent background system tray agent.
Monitors config.json for settings and executes keyboard lighting effects.
"""

import os
import sys
import json
import time
import threading
import ctypes
import subprocess
from PIL import Image, ImageDraw
import pystray

# Import core hardware controller and effect engine from app.py
from app import KeyboardBacklightController, EffectEngine, is_admin

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Default configuration settings
DEFAULT_CONFIG = {
    "effect": "reactive",
    "speed": 1.0,
    "mode": 1,
    "hold_behavior": 2,
    "enabled": True
}

controller = None
engine = None
tray_icon = None
config_last_modified = 0.0
watcher_thread = None
running = True

def load_config():
    """Load config.json or create it with default values if it doesn't exist."""
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
        except Exception as e:
            print(f"Failed to create config: {e}")
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Failed to load config: {e}")
        return DEFAULT_CONFIG

def save_config(cfg):
    """Save config dict to config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"Failed to save config: {e}")

def apply_config(cfg):
    """Apply config.json settings to the lighting engine."""
    global engine, controller
    try:
        if not cfg.get("enabled", True):
            engine.stop()
            controller.set_backlight(True)
            print("Effects disabled.")
            return
        
        effect = cfg.get("effect", "reactive")
        speed = float(cfg.get("speed", 1.0))
        mode = int(cfg.get("mode", 1))
        hold_behavior = int(cfg.get("hold_behavior", 2))
        
        # Start or update the effect
        engine.start(effect, speed, mode, hold_behavior)
        print(f"Applied: {effect} | Speed: {speed}x | Mode: {mode} | Hold: {hold_behavior}")
    except Exception as e:
        print(f"Error applying config: {e}")

def create_tray_image():
    """Generates a dynamic system tray icon (keyboard outline with a glowing indicator)."""
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    
    # Draw keyboard contour (glassmorphic style violet/white)
    dc.rounded_rectangle([6, 18, 58, 46], radius=6, outline=(255, 255, 255, 220), width=3)
    
    # Draw glowing reactive keys in center
    dc.rectangle([12, 24, 20, 30], fill=(168, 85, 247, 255))
    dc.rectangle([24, 24, 40, 30], fill=(168, 85, 247, 255))
    dc.rectangle([44, 24, 52, 30], fill=(168, 85, 247, 255))
    dc.rectangle([12, 34, 52, 40], fill=(168, 85, 247, 255)) # Spacebar
    
    return image

def edit_config_dialog(icon, item):
    """Open config.json inside Notepad for easy local editing."""
    try:
        subprocess.Popen(["notepad.exe", CONFIG_FILE])
    except Exception as e:
        print(f"Could not open notepad: {e}")

def open_folder(icon, item):
    """Open the application folder in File Explorer."""
    try:
        os.startfile(os.path.dirname(os.path.abspath(__file__)))
    except Exception as e:
        print(f"Could not open folder: {e}")

def toggle_enabled(icon, item):
    """Toggle enabled status of effects."""
    cfg = load_config()
    cfg["enabled"] = not cfg.get("enabled", True)
    save_config(cfg)
    # Trigger watcher reload immediately
    config_watcher_tick()

def on_exit(icon, item):
    """Gracefully terminate background hooks, servers and exit."""
    global running, engine, controller
    running = False
    engine.stop()
    controller.close_ps()
    icon.stop()

def config_watcher_tick():
    """Checks if config.json was modified and applies it."""
    global config_last_modified
    try:
        if os.path.exists(CONFIG_FILE):
            mtime = os.path.getmtime(CONFIG_FILE)
            if mtime != config_last_modified:
                config_last_modified = mtime
                cfg = load_config()
                apply_config(cfg)
                
                # Update tray status title
                status_str = f"Status: {'Running' if cfg.get('enabled', True) else 'Idle'}"
                effect_str = f"Effect: {cfg.get('effect', 'None').upper()}"
                
                # Refresh menu
                tray_icon.title = f"LOQ Lighting Agent - {cfg.get('effect', 'None')}"
    except Exception as e:
        print(f"Watcher error: {e}")

def config_watcher_loop():
    """Background polling loop that checks for config changes."""
    global running
    while running:
        config_watcher_tick()
        time.sleep(1.0)

def main():
    global controller, engine, tray_icon, watcher_thread
    
    # Self-elevation check
    if not is_admin():
        print("Requesting Administrator privileges...")
        try:
            if getattr(sys, 'frozen', False):
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
        except Exception as e:
            print(f"UAC elevation failed: {e}")
            sys.exit(1)

    # Initialize Hardware controller and engine
    controller = KeyboardBacklightController()
    engine = EffectEngine(controller)
    
    # Pre-load config
    cfg = load_config()
    apply_config(cfg)

    # Create the System Tray Icon
    menu = pystray.Menu(
        pystray.MenuItem("⌨️ LOQ Keyboard Agent", lambda: None, enabled=False),
        pystray.MenuItem("Toggle Active Effects", toggle_enabled, checked=lambda item: load_config().get("enabled", True)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Edit Settings (config.json)", edit_config_dialog),
        pystray.MenuItem("Open Folder", open_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", on_exit)
    )
    
    tray_icon = pystray.Icon(
        name="LOQ Keyboard Agent",
        icon=create_tray_image(),
        title="LOQ Keyboard Agent",
        menu=menu
    )
    
    # Start config file watcher
    watcher_thread = threading.Thread(target=config_watcher_loop, daemon=True)
    watcher_thread.start()

    # Run system tray icon (blocks until stopped)
    tray_icon.run()

if __name__ == "__main__":
    main()
