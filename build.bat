@echo off
setlocal
rem ============================================================
rem  LottoGenerator.exe build script
rem  Put this file in the same folder as lotto_app.py and
rem  double-click it. Output: dist\LottoGenerator.exe
rem ============================================================

cd /d "%~dp0"

if exist "lotto_app.py" goto :find_python
echo [ERROR] lotto_app.py was not found in this folder:
echo         %CD%
echo         Put build.bat in the same folder as lotto_app.py and run it again.
goto :fail

:find_python

call "%~dp0ensure_python.bat"
if errorlevel 1 goto :fail

echo Using Python:
"%PY%" -c "import sys; print('  ' + sys.executable + '  (' + sys.version.split()[0] + ')')"
echo.

rem --- Install PyInstaller -------------------------------------------------
echo [1/2] Installing PyInstaller...
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 "%PY%" -m ensurepip --upgrade
"%PY%" -m pip install --upgrade --disable-pip-version-check pyinstaller
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed. Check the internet connection.
    goto :fail
)

rem --- Build ---------------------------------------------------------------
echo.
echo [2/2] Building LottoGenerator.exe ... (1-2 min)
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name LottoGenerator lotto_app.py
if errorlevel 1 (
    echo [ERROR] Build failed. See the messages above.
    goto :fail
)

echo.
echo ============================================================
echo  Done: %CD%\dist\LottoGenerator.exe
echo ============================================================
explorer "%CD%\dist"
pause
exit /b 0


:fail
echo.
echo  If this keeps failing, open Command Prompt in this folder and run:
echo    py -m pip install pyinstaller
echo    py -m PyInstaller --noconfirm --onefile --windowed --name LottoGenerator lotto_app.py
echo.
pause
exit /b 1
