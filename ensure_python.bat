@echo off
rem Shared bootstrap. Returns the verified interpreter in PY.
set "PY="
call :try py
call :try python
for /d %%D in ("%LOCALAPPDATA%\Python\*") do call :try "%%~fD\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try "%%~fD\python.exe"
for /d %%D in ("%ProgramFiles%\Python3*") do call :try "%%~fD\python.exe"
if defined PY exit /b 0

echo Python with tkinter was not found. Downloading and installing Python...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_python.ps1"
if errorlevel 1 exit /b 1
call :try "%LOCALAPPDATA%\Programs\Python\LottoGeneratorPython313\python.exe"
if defined PY exit /b 0
echo [ERROR] Python installation finished but verification failed.
exit /b 1

:try
if defined PY exit /b 0
rem Reject old Python and interpreters without Tk.
"%~1" -c "import sys, tkinter; assert sys.version_info >= (3, 8)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0
