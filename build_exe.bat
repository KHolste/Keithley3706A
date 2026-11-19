@echo off
rem Erstellt eine eigenstaendige Programmversion (kein Python noetig) in dist\Keithley3706A-Monitor\
rem Auf dem Zielrechner wird nur eine VISA-Installation (NI-VISA / Keysight IO) benoetigt.
setlocal
cd /d "%~dp0"

set "PY="
py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if not defined PY (
    python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python wurde nicht gefunden.
    pause
    exit /b 1
)

%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

if not exist build mkdir build
%PY% -c "from keithley3706a.icons import save_ico; save_ico(r'build\app.ico')"
if errorlevel 1 goto :fail

%PY% -m PyInstaller --noconfirm --clean --windowed --name "Keithley3706A-Monitor" ^
    --icon build\app.ico --collect-submodules pyqtgraph ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick ^
    main.py
if errorlevel 1 goto :fail

echo.
echo Fertig: dist\Keithley3706A-Monitor\Keithley3706A-Monitor.exe
echo Zum Weitergeben den kompletten Ordner dist\Keithley3706A-Monitor kopieren.
pause
exit /b 0

:fail
echo Build fehlgeschlagen.
pause
exit /b 1
