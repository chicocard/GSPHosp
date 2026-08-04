@echo off
REM ============================================================
REM   GSPHosp - RESET de login admin (duplo-clique no Explorer!)
REM   Compativel com Windows 10/11 - sem precisar PowerShell 7.
REM ============================================================
setlocal
chcp 65001 >nul
title GSPHosp: Resetar login admin / Diagnostico

set "PROJDIR=%~dp0"
cd /d "%PROJDIR%"

set "PYEXE=%PROJDIR%.venv\Scripts\python.exe"
set "SCRIPT=%PROJDIR%Resetar-Admin-GSPHosp.py"

REM --- 1) Validacoes basicas ---
if not exist "%PYEXE%" (
    echo [ERRO] Python do ambiente virtual NAO EXISTE:
    echo   %PYEXE%
    echo.
    echo Rode UMA VEZ (no terminal do projeto):
    echo   py -3 -m venv .venv   ^&^&   .\.venv\Scripts\python.exe -m pip install -e .[ui]
    echo.
    pause
    exit /b 2
)
if not exist "%SCRIPT%" (
    echo [ERRO] Script Python nao encontrado: %SCRIPT%
    pause
    exit /b 3
)

REM --- 2) Mata processos python (libera lock do SQLite se app estiver aberto) ---
echo.
echo [1/3] Matando processos GSPHosp/Python abertos (liberar DB)...
taskkill /F /IM python.exe   /T  >nul 2>&1
taskkill /F /IM pythonw.exe  /T  >nul 2>&1
timeout /T 1 /NOBREAK >nul

REM --- 3) Forca UTF-8 no Python e roda o script principal ---
echo.
echo [2/3] Executando diagnostico + reset admin...
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

"%PYEXE%" "%SCRIPT%"
set "EXITCODE=%ERRORLEVEL%"

echo.
echo [3/3] Concluido. exit_code=%EXITCODE%
echo.
if "%EXITCODE%"=="0" (
    echo SUCESSO! Agora abra o GSPHosp e logue com:
    echo    Username : admin
    echo    Senha    : GSPHosp@2026
) else (
    echo FALHA. Verifique acima a mensagem de erro.
    echo    * Feche o app GSPHosp se ele estiver aberto e TENTE NOVAMENTE.
)
echo.
pause
endlocal & exit /b %EXITCODE%
