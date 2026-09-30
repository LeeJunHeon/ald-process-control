@echo off
rem ============================================================
rem  build.bat — POWDERALD 제어 프로그램 빌드 (PyInstaller onedir)
rem
rem  결과: dist\POWDERALD_Control\POWDERALD_Control.exe
rem  번들: frontend · assets · config\config.example.json 만.
rem        테스트 · __pycache__ · config.json · data 는 넣지 않는다(build.spec).
rem  빌드 뒤 --selftest 를 자동으로 돌려 결과(0/1)를 알린다.
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [오류] python 을 찾을 수 없습니다.
  exit /b 1
)

python -m PyInstaller build.spec --clean --noconfirm
if errorlevel 1 (
  echo [오류] PyInstaller 빌드 실패
  exit /b 1
)

set EXE=dist\POWDERALD_Control\POWDERALD_Control.exe
if not exist "%EXE%" (
  echo [오류] %EXE% 가 없습니다.
  exit /b 1
)

echo.
echo === 자체 점검 (--selftest) ===
start /wait "" "%EXE%" --selftest
if errorlevel 1 (
  echo [실패] 자체 점검 실패 — dist\POWDERALD_Control\data\logs 를 확인하세요
  exit /b 1
)
echo [통과] 자체 점검 통과 — 결과 한 줄이 dist\POWDERALD_Control\data\logs 에 남았습니다
exit /b 0
