@echo off
rem Starts GhostCue in the system tray, without a console window.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m ghostcue %*
