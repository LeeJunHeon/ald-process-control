@echo off
rem ============================================================
rem  build.bat - POWDERALD control program build (PyInstaller onedir)
rem
rem  Output : dist\POWDERALD_Control\POWDERALD_Control.exe
rem  Bundle : frontend, assets, config\config.example.json only.
rem           Tests, __pycache__, config.json and data are NOT bundled (see build.spec).
rem  After the build, the exe is run once with --selftest (exit code 0 = pass).
rem  ASCII only on purpose: cmd reads this file the same way on any code page.
rem ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found on PATH.
  exit /b 1
)

python -m PyInstaller build.spec --clean --noconfirm
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed.
  exit /b 1
)

set EXE=dist\POWDERALD_Control\POWDERALD_Control.exe
if not exist "%EXE%" (
  echo [ERROR] %EXE% not found.
  exit /b 1
)

echo.
echo === selftest (--selftest) ===
start /wait "" "%EXE%" --selftest
if errorlevel 1 (
  echo [FAIL] selftest failed - see dist\POWDERALD_Control\data\logs
  exit /b 1
)
echo [PASS] selftest passed - one result line written to dist\POWDERALD_Control\data\logs
exit /b 0
