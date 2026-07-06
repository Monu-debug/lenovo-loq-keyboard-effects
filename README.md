# ⌨️ Lenovo LOQ Keyboard Effects Lab

A lightweight, modern web-based control panel to apply custom lighting effects (blinking, breathing, strobe, lightning, binary clock, etc.) to the white-backlit keyboards on Lenovo LOQ and IdeaPad Gaming laptops under Windows.

This project bypasses traditional high-latency command prompts by communicating directly with Lenovo's VPC energy management driver (`\\.\EnergyDrv`) using the native Lenovo Vantage helper DLLs.

## 🚀 How it Works (Under the Hood)
Most automation tools try to toggle the keyboard backlight by spawning heavy WMI commands or simulating keys, which takes ~500ms and makes fast effects impossible. 

This program:
1. Locates the active `IdeaNotebookAddin` DLLs inside `C:\ProgramData\Lenovo\Vantage\Addins`.
2. Spins up a persistent PowerShell subprocess in the background.
3. Uses .NET reflection to call Vantage's private `IdeaNotebookAddin.IdeaNotebookAgent` API.
4. Toggles states directly in the energy driver in **under 15 milliseconds**, allowing fluid, synchronized visual effects.

---

## 🛠️ Setup & Running

### Option A: The One-Click Way (Recommended)
1. Ensure you have [Python](https://www.python.org/downloads/) installed.
2. Double-click `run.bat`.
3. Accept the **UAC Administrator prompt** (needed to communicate with the hardware driver).
4. Open your browser to **[http://localhost:5000](http://localhost:5000)**.

### Option B: Manual Terminal Execution
Open your terminal as Administrator and run:
```bash
pip install -r requirements.txt
python app.py
```

---

## 🎨 Supported Effects
* 💡 **Blink:** Classic clean blinking.
* 🌊 **Breathe:** Smoothly cycles through `Off` -> `Dim` -> `Bright` -> `Dim`.
* ⚡ **Strobe:** High-speed flash strobing.
* 💓 **Heartbeat:** Natural double-pulse heart rhythm.
* 🌩️ **Lightning:** Atmospheric random storm strikes.
* 🔢 **Binary Clock:** Decodes the current seconds counter into binary blinks.
