@echo off
rem Windows: double-click this file to start Veuly.
cd /d "%~dp0"
py -3 app.py || python app.py
pause
