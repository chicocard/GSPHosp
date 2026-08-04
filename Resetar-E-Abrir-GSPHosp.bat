@echo off
REM ============================================================
REM   GSPHosp - RESET ADMIN + ABRIR APP - 1 CLIQUE 2 EM 1
REM   Compativel Windows 10/11 - PowerShell 5.1 ou 7.
REM ============================================================
REM   O QUE FAZ (tudo automaticamente, sem precisar de 2 scripts):
REM     0. (NOVO!) Apaga DuckDB corrompido (.duckdb/wal/shm) - resolve erro WAL do Wizard.
REM     1. Fecha o GSPHosp se estiver aberto (libera SQLite).
REM     2. Roda Resetar-Admin-GSPHosp.py - CRIA ou RESETA para admin GSPHosp 2026.
REM     3. LOGO EM SEGUIDA abre o GSPHosp com Abrir-GSPHosp.bat.
REM ============================================================
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title GSPHosp: Resetar admin + Abrir - 1 clique

REM ----------------------------------------------------------
REM  LOG PERSISTENTE (mesmo no duplo clique, o erro fica aqui!)
REM ----------------------------------------------------------
set "LOGFILE=%TEMP%\GSPHosp_Launcher.log"
if exist "%LOGFILE%" del /F /Q "%LOGFILE%" 2>nul
call :LOG "============================================================"
call :LOG "GSPHosp Launcher iniciado: %DATE% %TIME%"
call :LOG "============================================================"

set "PROJDIR=%~dp0"
cd /d "%PROJDIR%" || (
    call :LOG [ERRO] Nao foi possivel mudar para a pasta: %PROJDIR%
    echo [ERRO] Nao foi possivel mudar para a pasta: %PROJDIR%
    pause
    exit /b 1
)

set "PYEXE=%PROJDIR%.venv\Scripts\python.exe"
set "RESETSCRIPT=%PROJDIR%Resetar-Admin-GSPHosp.py"
set "ABRIRSCRIPT=%PROJDIR%Abrir-GSPHosp.bat"

call :LOG "PROJDIR = %PROJDIR%"
call :LOG "PYEXE   = %PYEXE%"
call :LOG "RESET   = %RESETSCRIPT%"
call :LOG "ABRIR   = %ABRIRSCRIPT%"

REM --- 1) Validacoes ---
if not exist "%PYEXE%" (
    call :LOG "[ERRO] Python venv NAO ENCONTRADO: %PYEXE%"
    echo.
    echo [ERRO] Python venv nao encontrado:
    echo   %PYEXE%
    echo.
    echo Rode 1 vez:
    echo   py -3 -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -e .[ui]
    echo.
    pause
    exit /b 2
)
if not exist "%RESETSCRIPT%" (
    call :LOG "[ERRO] Script reset NAO ENCONTRADO: %RESETSCRIPT%"
    echo [ERRO] Script nao encontrado: %RESETSCRIPT%
    pause
    exit /b 3
)
if not exist "%ABRIRSCRIPT%" (
    call :LOG "[ERRO] Script abrir NAO ENCONTRADO: %ABRIRSCRIPT%"
    echo [ERRO] Script nao encontrado: %ABRIRSCRIPT%
    pause
    exit /b 4
)

REM ----------------------------------------------------------
REM  PASSO 0 - Apaga DuckDB corrompido
REM ----------------------------------------------------------
echo.
echo ============================================================
echo  [PASSO 0/4] - Apagando DuckDB corrompido ...
echo  =  .duckdb  /  .duckdb-wal  /  .duckdb-shm
echo  =  BASE DE USUARIOS gsphosp_users.db CONTINUA INTACTA
echo ============================================================
call :LOG "Passo 0: Apagar DuckDB corrompido (WAL/shm)"

set "GSP=%USERPROFILE%\.gsphosp"
if not exist "%GSP%" (
    call :LOG "  Pasta %GSP% ainda nao existe - nada a apagar."
    echo   Pasta ainda nao criada - OK.
    goto :PASSO0_FIM
)
if exist "%GSP%\gsphosp.duckdb" (
    del /F /Q "%GSP%\gsphosp.duckdb" 2>nul
    call :LOG "  - apagou gsphosp.duckdb"
    echo   - apagou gsphosp.duckdb
)
if exist "%GSP%\gsphosp.duckdb-wal" (
    del /F /Q "%GSP%\gsphosp.duckdb-wal" 2>nul
    call :LOG "  - apagou gsphosp.duckdb-wal - WAL corrompido ERA O ERRO DA TUA TELA"
    echo   - apagou gsphosp.duckdb-wal - WAL corrompido ERA O ERRO DA TUA TELA
)
if exist "%GSP%\gsphosp.duckdb-shm" (
    del /F /Q "%GSP%\gsphosp.duckdb-shm" 2>nul
    call :LOG "  - apagou gsphosp.duckdb-shm"
    echo   - apagou gsphosp.duckdb-shm
)
:PASSO0_FIM
call :LOG "Passo 0 concluido."
echo   [SUCESSO PASSO 0] DuckDB zerado. Novo DB limpo criado na proxima abertura.
timeout /T 1 /NOBREAK >nul

REM ----------------------------------------------------------
REM  PASSO 1 - Matar processos GSPHosp SOMENTE!
REM  - NUCA MAIS taskkill /IM python.exe (matava o proprio batch!)
REM  - Python one-liner seguro: mata so processos com
REM    "papahr.ui.app" ou "GSPHosp" no CommandLine
REM ----------------------------------------------------------
echo.
echo ============================================================
echo  [PASSO 1/4] Fechando processos GSPHosp abertos ...
echo ============================================================
call :LOG "Passo 1: MATA PROCESSOS SELETIVOS python (papahr.ui.app / GSPHosp) - NUNCA GERAL"

set "_MATAQTY=0"
if exist "%PYEXE%" (
    if exist "%PROJDIR%kill_gsphosp_processes.py" (
        "%PYEXE%" "%PROJDIR%kill_gsphosp_processes.py" 1>"%TEMP%\gsp_kill.txt" 2>nul
        for /f %%N in ('type "%TEMP%\gsp_kill.txt" 2^>nul') do set "_MATAQTY=%%~N"
        if exist "%TEMP%\gsp_kill.txt" del /F /Q "%TEMP%\gsp_kill.txt" 2>nul
    )
)
call :LOG "Passo 1: %_MATAQTY% processo(s) GSPHosp fechado(s)."
echo   %_MATAQTY% processo(s) fechado(s) (0 = nenhum GSPHosp rodando - OK).
timeout /T 1 /NOBREAK >nul

REM ----------------------------------------------------------
REM  PASSO 2 - Reset admin
REM ----------------------------------------------------------
echo.
echo ============================================================
echo  [PASSO 2/4] Resetando admin / GSPHosp 2026 ...
echo  (se houver usuarios PAPAHU antigos, MIGRAMOS para a nova base)
echo ============================================================
call :LOG "Passo 2: Roda Resetar-Admin-GSPHosp.py --auto-migrate --no-pause"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

"%PYEXE%" "%RESETSCRIPT%" --auto-migrate --no-pause >> "%LOGFILE%" 2>&1
set "EXIT_RESET=%ERRORLEVEL%"
call :LOG "Reset admin finalizou com exit_code=%EXIT_RESET%"

echo.
if "%EXIT_RESET%"=="0" (
    echo ============================================================
    echo  [SUCESSO PASSO 2] admin / GSPHosp 2026 confirmado!
    echo ============================================================
    call :LOG "Passo 2: SUCESSO admin/GSPHosp 2026."
) else (
    echo ============================================================
    echo  [ATENCAO] Reset retornou exit_code=%EXIT_RESET%
    echo   Ver log %LOGFILE% para detalhes.
    echo   Tentarei abrir o app mesmo assim...
    echo ============================================================
    call :LOG "Passo 2: ATENCAO exit_code=%EXIT_RESET% - veja log acima."
    echo.
    pause
)

REM ----------------------------------------------------------
REM  PASSO 3 - Abrir o GSPHosp (USAMOS START /B para nao fechar a janela)
REM ----------------------------------------------------------
echo.
echo ============================================================
echo  [PASSO 3/4] Abrindo GSPHosp - aguarde 2 a 4 segundos...
echo ============================================================
call :LOG "Passo 3: Abrir GSPHosp.bat via CALL / Aguarda 2s timeout antes"
timeout /T 2 /NOBREAK >nul

call :LOG "Passo 3: CALL %ABRIRSCRIPT% >> %LOGFILE% 2>&1"
call "%ABRIRSCRIPT%" >> "%LOGFILE%" 2>&1
set "EXIT_ABRIR=%ERRORLEVEL%"
call :LOG "Passo 3: Abrir-GSPHosp.bat finalizou exit_code=%EXIT_ABRIR%"

echo.
if "%EXIT_ABRIR%"=="0" (
    echo [OK] App encerrado normalmente exit_code=0
) else (
    echo [ATENCAO] App retornou exit_code=%EXIT_ABRIR%
    echo   Ver log: %LOGFILE%
)

call :LOG "============================================================"
call :LOG "Fim do launcher %DATE% %TIME% - exit reset=%EXIT_RESET% abrir=%EXIT_ABRIR%"
call :LOG "============================================================"

endlocal & exit /b %EXIT_ABRIR%

REM ============================================================
REM  SUB: LOG (escreve no arquivo e no console de um jeito
REM  que nao explode com caracteres especiais)
REM ============================================================
:LOG
setlocal EnableDelayedExpansion
set "MSG=%~1"
>> "!LOGFILE!" echo !MSG!
endlocal
exit /b 0
