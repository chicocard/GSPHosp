@echo off
REM Limpar-DuckDB-Corrompido-E-Reabrir.bat
REM Apaga os 3 arquivos do DuckDB corrompidos (.duckdb, .duckdb-wal, .duckdb-shm)
REM NAO TOCA NA BASE DE USUARIOS (gsphosp_users.db) nem na AUDITORIA!
REM Depois roda o reset do admin (opcional) e abre o GSPHosp.

setlocal
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"
echo.
echo ============================================================
echo   [PASSO 1/5] Fechando processos GSPHosp/Python abertos ...
echo ============================================================
echo.
taskkill /F /IM python.exe       2> nul
taskkill /F /IM pythonw.exe      2> nul
echo   (se nao havia python aberto, tudo bem - DB livre)
timeout /t 1 /nobreak > nul

echo.
echo ============================================================
echo   [PASSO 2/5] Apagando DuckDB corrompido (WAL/.duckdb/.shm)
echo   (BASE DE USUARIOS gsphosp_users.db NAO SERA TOCADA!)
echo ============================================================
echo.
set GSP=%USERPROFILE%\.gsphosp
if exist "%GSP%\gsphosp.duckdb" (
    echo   - apagar gsphosp.duckdb (OK)
    del /F /Q "%GSP%\gsphosp.duckdb" 2> nul
)
if exist "%GSP%\gsphosp.duckdb-wal" (
    echo   - apagar gsphosp.duckdb-wal (O WAL corrompido que estava crashando!)
    del /F /Q "%GSP%\gsphosp.duckdb-wal" 2> nul
)
if exist "%GSP%\gsphosp.duckdb-shm" (
    echo   - apagar gsphosp.duckdb-shm (OK)
    del /F /Q "%GSP%\gsphosp.duckdb-shm" 2> nul
)
if not exist "%GSP%\gsphosp.duckdb" (
    echo.
    echo   [SUCESSO PASSO 2] DuckDB zerado. Nova conexao limpa sera criada na proxima abertura.
) else (
    echo.
    echo   [AVISO] Arquivos ainda existem? Feche totalmente o GSPHosp e rode novamente.
)

echo.
echo ============================================================
echo   [PASSO 3/5] Validando se base de USUARIOS continua intacta
echo ============================================================
echo.
if exist "%GSP%\gsphosp_users.db" (
    echo   OK. gsphosp_users.db existe. admin / GSPHosp@2026 continuara funcionando.
    for %%A in ("%GSP%\gsphosp_users.db") do echo   Tamanho: %%~zA bytes
) else (
    echo   AVISO: base usuarios nao existe - sera criada na hora, admin/GSPHosp@2026 autocriado.
)

echo.
echo ============================================================
echo   [PASSO 4/5] Verificando venv e chamando reset do admin
echo ============================================================
echo.
set PY=".venv\Scripts\python.exe"
if not exist %PY% (
    echo .venv nao encontrado. Esperava: %PY%
    echo Abra um terminal no projeto e rode: py -3 -m venv .venv && pip install -e .[ui]
    pause
    exit /b 1
)
%PY% Resetar-Admin-GSPHosp.py --auto-migrate --no-pause
set EXITCODE=%ERRORLEVEL%

echo.
echo ============================================================
echo   [PASSO 5/5] Abrindo GSPHosp (aguarde 2-4 segundos) ...
echo ============================================================
echo.
echo.
echo ============================================================
echo   GSPHosp - Gestao em Saude Populacional Hospitalar
echo   Projeto : %~dp0
echo   Python  : %~f0.py ??? NAO - usando python venv.
echo ============================================================
echo.
REM Chama Abrir-GSPHosp.bat via call (evita bug CP1252)
call "Abrir-GSPHosp.bat"

endlocal
