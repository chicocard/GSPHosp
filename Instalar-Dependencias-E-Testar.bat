@echo off
rem ============================================================
rem  Instalar-Dependencias-E-Testar.bat
rem  1) Instala as dependencias no .venv (faltavam - por isso o
rem     ModuleNotFoundError: No module named 'pydantic')
rem  2) Roda os testes FASE 1 (Testar-GSPHosp.py)
rem  Requer internet. Demora alguns minutos na 1a vez.
rem ============================================================
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERRO: .venv nao encontrado. Criando novo...
    py -3.11 -m venv .venv || python -m venv .venv
)
echo.
echo [1/4] Atualizando pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
echo.
echo [2/4] Instalando requisitos CORE (polars, pydantic, etc)...
".venv\Scripts\python.exe" -m pip install -r requirements-core.txt
if errorlevel 1 (
    echo ERRO na instalacao core. Verifique a internet e rode de novo.
    pause
    exit /b 1
)
echo.
echo [3/4] Instalando requisitos UI (PySide6, DuckDB, etc)...
".venv\Scripts\python.exe" -m pip install -r requirements-ui.txt
if errorlevel 1 (
    echo ERRO na instalacao UI. Verifique a internet e rode de novo.
    pause
    exit /b 1
)
echo.
echo [4/4] Rodando testes FASE 1...
".venv\Scripts\python.exe" Testar-GSPHosp.py
echo.
echo ------------------------------------------------------------
if exist "TESTES-RESULTADO.txt" start notepad "TESTES-RESULTADO.txt"
pause
