@echo off
rem ============================================================
rem  Testar-GSPHosp.bat — roda os testes FASE 1 (sem GUI)
rem  Resultado detalhado: TESTES-RESULTADO.txt (UTF-8)
rem ============================================================
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERRO: .venv nao encontrado em %~dp0.venv
    pause
    exit /b 1
)
".venv\Scripts\python.exe" Testar-GSPHosp.py
echo.
echo ------------------------------------------------------------
echo Abrindo TESTES-RESULTADO.txt ...
if exist "TESTES-RESULTADO.txt" start notepad "TESTES-RESULTADO.txt"
pause
