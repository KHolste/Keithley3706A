@echo off
rem Startet den Keithley 3706A Thermoelement-Monitor.
rem Beim ersten Start werden fehlende Python-Pakete automatisch installiert.
setlocal
cd /d "%~dp0"

rem Python finden: bevorzugt der py-Launcher mit 3.11, sonst python im PATH
set "PY="
py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if not defined PY (
    py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python wurde nicht gefunden. Bitte Python 3.11 oder neuer installieren.
    pause
    exit /b 1
)

rem Abhaengigkeiten pruefen und ggf. installieren
%PY% -c "import PySide6, pyqtgraph, numpy, pyvisa" >nul 2>&1
if errorlevel 1 (
    echo Installiere benoetigte Pakete ...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Installation fehlgeschlagen.
        pause
        exit /b 1
    )
)

rem Ohne Konsolenfenster starten (pythonw), Fehler erscheinen als Dialog
set "PYW=%PY:py =pyw %"
if "%PY%"=="python" set "PYW=pythonw"
start "" %PYW% main.py
endlocal
