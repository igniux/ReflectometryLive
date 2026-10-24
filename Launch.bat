@echo off
setlocal
set "APP_DIR=%~dp0"
set "PYTHON_EXE=%APP_DIR%.venv\Scripts\pythonw.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%APP_DIR%..\.reflectometry-venv\Scripts\pythonw.exe"
if not exist "%PYTHON_EXE%" (
    powershell.exe -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Python environment not found. Run Setup.bat first.', 'Reflectometry Live')" >nul 2>&1
    exit /b 1
)
start "" "%PYTHON_EXE%" "%APP_DIR%launch_gui.pyw" %*
exit /b 0
