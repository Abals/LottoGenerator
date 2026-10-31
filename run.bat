@echo off
setlocal
cd /d "%~dp0"
if not exist "lotto_app.py" (
    echo [ERROR] lotto_app.py was not found in this folder.
    goto :fail
)
call "%~dp0ensure_python.bat"
if errorlevel 1 goto :fail
for %%P in ("%PY%") do set "PYW=%%~dpPpythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0lotto_app.py"
) else (
    "%PY%" "%~dp0lotto_app.py"
    if errorlevel 1 goto :fail
)
exit /b 0

:fail
pause
exit /b 1
