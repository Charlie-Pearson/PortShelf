@echo off
rem PortShelf uninstaller: stops it, removes the shortcut and the start-with-Windows entry,
rem and optionally deletes its saved settings and history. Leaves your projects alone.
setlocal

echo Stopping PortShelf...
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | Where-Object CommandLine -match 'portshelf\.pyw' | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Removing Start Menu shortcut and start-with-Windows entry...
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\PortShelf.lnk" 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PortShelf /f >nul 2>nul

if exist "%APPDATA%\PortShelf" (
  choice /m "Also delete PortShelf's saved settings and history (%APPDATA%\PortShelf)"
  if not errorlevel 2 rmdir /s /q "%APPDATA%\PortShelf"
)

echo.
echo PortShelf is uninstalled. You can now delete this folder.
echo The Python packages it used are shared and were left installed. To remove them too:
echo     python -m pip uninstall psutil pystray Pillow six
pause
