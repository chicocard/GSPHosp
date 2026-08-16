@echo off
REM ============================================================
REM  Abrir-GSPHosp.bat - Launcher PADRAO para Windows qualquer PC
REM  Compativel: Windows 10/11. NAO precisa de PowerShell 7.
REM  Funciona com duplo-clique do Explorer!
REM ============================================================
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title GSPHosp - Gestao em Saude Populacional Hospitalar

REM LOG PERSISTENTE (escreve no mesmo log do Resetar)
set "LOGFILE=%TEMP%\GSPHosp_Launcher.log"
if not exist "%LOGFILE%" (
    >> "%LOGFILE%" echo ============================================================
    >> "%LOGFILE%" echo GSPHosp Launcher iniciado: %DATE% %TIME%
    >> "%LOGFILE%" echo ============================================================
)

set "PROJDIR=%~dp0"
cd /d "%PROJDIR%" || (
    echo [ERRO] Nao foi possivel mudar para a pasta: %PROJDIR%
    >> "%LOGFILE%" echo [ERRO] cd /d falhou: %PROJDIR%
    pause
    exit /b 1
)

REM Python do venv
set "PYEXE=%PROJDIR%.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
    echo.
    echo [ERRO] Python do .venv NAO EXISTE: %PYEXE%
    echo.
    echo Rode UMA VIZ no terminal:
    echo   py -3 -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -e .[ui]
    echo.
    >> "%LOGFILE%" echo [ERRO] venv NAO EXISTE: %PYEXE%
    pause
    exit /b 1
)

REM Forca UTF-8 Python + path correto
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

echo.
echo ============================================================
echo   GSPHosp - Gestao em Saude Populacional Hospitalar
echo   Projeto : %PROJDIR%
echo   Python  : %PYEXE%
echo   Log     : %LOGFILE%
echo ============================================================
echo.
>> "%LOGFILE%" echo [Abrir-GSPHosp] PROJDIR=%PROJDIR%
>> "%LOGFILE%" echo [Abrir-GSPHosp] PYEXE=%PYEXE%
>> "%LOGFILE%" echo [Abrir-GSPHosp] Chamada: from papahr.ui.app import main

REM Executa o app (saida do console E log para debug)
"%PYEXE%" -c "from papahr.ui.app import main; import sys; raise SystemExit(main())"
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
    echo [OK] GSPHosp fechado normalmente exit_code=0
    >> "%LOGFILE%" echo [Abrir-GSPHosp] App OK exit_code=0
) else (
    echo [ATENCAO] App finalizou com exit_code=%EXITCODE%   (0 = OK)
    echo   Ver log: %LOGFILE%
    >> "%LOGFILE%" echo [Abrir-GSPHosp] App finalizou com exit_code=%EXITCODE%
)

>> "%LOGFILE%" echo ============================================================
>> "%LOGFILE%" echo Fim Abrir-GSPHosp %DATE% %TIME%
>> "%LOGFILE%" echo ============================================================
endlocal & exit /b %EXITCODE%
