@echo off
rem PortShelf installer: installs its Python packages, adds a Start Menu shortcut, starts it.
rem Nothing here needs admin rights. Everything goes into your own user account.
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (
  echo Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run this again.
  pause
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || (
  echo PortShelf needs Python 3.11 or newer. You have:
  python --version
  pause
  exit /b 1
)

echo Installing psutil, pystray, Pillow and six (exact tested versions)...
python -m pip install --user --quiet -r requirements.txt || (
  echo Could not install the Python packages. See the messages above.
  pause
  exit /b 1
)

rem pythonw.exe runs PortShelf without a console window; find the one next to this python.exe.
set "PYW="
for /f "delims=" %%P in ('python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYW=%%P"
if not exist "%PYW%" (
  echo Could not find pythonw.exe next to your Python installation.
  pause
  exit /b 1
)
set "PS_DIR=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$q = [char]34; $lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'PortShelf.lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
  "$s.TargetPath = $env:PYW; $s.Arguments = $q + $env:PS_DIR + 'portshelf.pyw' + $q; $s.WorkingDirectory = $env:PS_DIR;" ^
  "$s.Description = 'Local dev servers at a glance'; $s.Save(); Write-Host ('Start Menu shortcut: ' + $lnk)"

start "" "%PYW%" "%~dp0portshelf.pyw"
echo.
echo PortShelf is running in the system tray (bottom-right, by the clock).
echo Tip: if you can't see it, click the small up-arrow by the clock and drag
echo the PortShelf icon onto the taskbar so it's always visible.
"%SystemRoot%\System32\timeout.exe" /t 6 >nul 2>nul
