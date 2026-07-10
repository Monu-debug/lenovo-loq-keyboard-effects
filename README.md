# ⌨️ Lenovo LOQ Keyboard Effects Lab

A lightweight, modern web-based control panel to apply custom lighting effects (blinking, breathing, strobe, lightning, binary clock, etc.) to the white-backlit keyboards on Lenovo LOQ and IdeaPad Gaming laptops under Windows.

> [!IMPORTANT]
> **Tested Hardware & OS Configuration:**
> * **Model:** Lenovo LOQ 2024 (15IAX9) (Tested & Verified)
> * **Compatible Models:** Designed to support Lenovo LOQ 15IRX9 and other LOQ/IdeaPad Gaming laptops utilizing Vantage backlight DLLs.
> * **Operating System:** Windows 11 Home

This project bypasses traditional high-latency command prompts by communicating directly with Lenovo's VPC energy management driver (`\\.\EnergyDrv`) using the native Lenovo Vantage helper DLLs.

## 🚀 How it Works (Under the Hood)
Most automation tools try to toggle the keyboard backlight by spawning heavy WMI commands or simulating keys, which takes ~500ms and makes fast effects impossible. 

This program:
1. Locates the active `IdeaNotebookAddin` DLLs inside `C:\ProgramData\Lenovo\Vantage\Addins`.
2. Spins up a persistent PowerShell subprocess in the background.
3. Uses .NET reflection to call Vantage's private `IdeaNotebookAddin.IdeaNotebookAgent` API.
4. Toggles states directly in the energy driver in **under 15 milliseconds**, allowing fluid, synchronized visual effects.
5. Employs a pointer-safe, low-level global Windows keyboard hook (`SetWindowsHookExW`) via python `ctypes` to handle real-time reactive events instantly without system input lag.

---

## 🛠️ Setup & Running

### Option A: Standalone Desktop App (Recommended - No Python Required)
1. Download the compiled **`keyboard-effects.exe`**.
2. Double-click to run it.
3. Accept the **UAC Administrator prompt** (needed to communicate with the hardware driver).
4. The application opens directly as a **native desktop window** with the full control panel—no black command prompt windows or external browsers are opened!

### Option B: The One-Click Batch Way (Requires Python)
1. Ensure you have [Python](https://www.python.org/downloads/) installed.
2. Double-click `run.bat`.
3. Accept the **UAC Administrator prompt** and open your browser to **[http://localhost:5000](http://localhost:5000)**.

### Option C: Manual Terminal Execution
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
* ⌨️ **React:** Keypress reactive feedback with 5 selectable modes:
  1. *Normal ON (Blinks OFF):* Backlight stays ON, dips OFF briefly on keystroke.
  2. *Normal DIM (Flashes MAX):* Backlight stays DIM, flashes to full brightness on keystroke.
  3. *Normal MAX (Flashes DIM):* Backlight stays full brightness, dips to DIM briefly on keystroke.
  4. *Normal OFF (Flashes DIM):* Backlight stays OFF, flashes DIM briefly on keystroke.
  5. *Normal OFF (Flashes MAX):* Backlight stays OFF, flashes to full brightness on keystroke.

  *Includes 2 configurable key hold behaviors:*
  * **Auto-Repeat Blink (Flicker):** Constant rapid blinking when keys are held down (auto-repeat keydown events).
  * **Stay Active Until Released (Solid Hold):** Backlight stays in the active reaction state continuously as long as any keys are held down, returning to base state only after all keys are released.
