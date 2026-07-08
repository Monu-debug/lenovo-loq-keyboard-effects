"""
Inspect Lenovo Gaming Lighting DLL
Loads Gaming.AdvancedLighting.dll and lists all public types and methods.
"""
import os
import sys
import glob
import subprocess

DLL_PATH = r"C:\ProgramData\Lenovo\Vantage\Addins\LenovoGamingUserAddin\*\Gaming.AdvancedLighting.dll"

def main():
    paths = glob.glob(DLL_PATH)
    if not paths:
        print("Error: Gaming.AdvancedLighting.dll not found on this system.")
        return
    paths.sort()
    latest_dll = paths[-1]
    print(f"Loading assembly: {latest_dll}\n")

    # Run reflective query via PowerShell
    ps_cmd = f"""
    [System.Reflection.Assembly]::LoadFrom('{latest_dll}') | Out-Null
    $types = [System.AppDomain]::CurrentDomain.GetAssemblies() | 
             Where-Object {{ $_.Location -eq '{latest_dll}' }} | 
             ForEach-Object {{ $_.GetTypes() }} | 
             Where-Object {{ $_.IsPublic }}
             
    foreach ($t in $types) {{
        Write-Output "Class: $($t.FullName)"
        $methods = $t.GetMethods([System.Reflection.BindingFlags]::Public -bor [System.Reflection.BindingFlags]::Instance -bor [System.Reflection.BindingFlags]::Static) | 
                   Where-Object {{ $_.DeclaringType.Assembly.Location -eq '{latest_dll}' }}
        foreach ($m in $methods) {{
            $params = $m.GetParameters() | ForEach-Object {{ "$($_.ParameterType.Name) $($_.Name)" }}
            $paramStr = [string]::Join(", ", $params)
            Write-Output "  Method: $($m.ReturnType.Name) $($m.Name)($paramStr)"
        }}
        $props = $t.GetProperties()
        foreach ($p in $props) {{
            Write-Output "  Property: $($p.PropertyType.Name) $($p.Name)"
        }}
        Write-Output ""
    }}
    """
    
    try:
        out = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps_cmd], text=True)
        print(out)
        with open("gaming_dll_dump.txt", "w", encoding="utf-8") as f:
            f.write(out)
        print("Saved details to gaming_dll_dump.txt")
    except Exception as e:
        print("Inspection failed:", e)

if __name__ == "__main__":
    main()
