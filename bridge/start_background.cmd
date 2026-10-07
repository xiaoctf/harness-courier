@echo off
setlocal
set "BRIDGE_SCRIPT=%~dp0start_background.py"
if defined HARNESS_COURIER_PYTHON goto courier_python
if defined HARNESS_BRIDGE_PYTHON goto explicit_python
if exist "%~dp0..\.venv\Scripts\python.exe" goto local_venv
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
python "%BRIDGE_SCRIPT%" %*
exit /b %errorlevel%

:courier_python
"%HARNESS_COURIER_PYTHON%" "%BRIDGE_SCRIPT%" %*
exit /b %errorlevel%

:explicit_python
"%HARNESS_BRIDGE_PYTHON%" "%BRIDGE_SCRIPT%" %*
exit /b %errorlevel%

:local_venv
"%~dp0..\.venv\Scripts\python.exe" "%BRIDGE_SCRIPT%" %*
exit /b %errorlevel%

:python_launcher
py -3 "%BRIDGE_SCRIPT%" %*
exit /b %errorlevel%
