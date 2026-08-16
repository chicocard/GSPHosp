@echo off
REM ============================================================
REM   GSPHosp - APAGA BASE PAPAHU ANTIGA (seguro, 2 confirmações)
REM   Compativel Windows 10/11.
REM ============================================================
setlocal
chcp 65001 >nul
title GSPHosp: Apagar base PAPAHU antiga (seguro, com confirmação!)

REM --- 1) Confirmacao 1 ---
echo.
echo =============================================================
echo   GSPHosp - Apagar base ANTIGA PAPAHU (limpeza)
echo =============================================================
echo.
echo   Este batch vai:
echo     1. FECHAR processos Python/GSPHosp abertos.
echo     2. Mover para LIXEIRA (se possivel) OU APAGAR definitivamente:
echo        %USERPROFILE%\.papahr\
echo        (toda a base PAPAHU de USUARIOS do app antigo)
echo.
echo     * A base NOVA GSPHosp em %USERPROFILE%\.gsphosp\ NAO SERA AFETADA DE NENHUMA FORMA.
echo     * A sua conta admin/GSPHosp@2026 continua intacta.
echo.
set /p CONF1=Digite SIM para CONTINUAR (nao confirma = cancelado): 
if /i not "%CONF1%"=="SIM" (
    echo.
    echo   [Cancelado] Voce nao digitou "SIM" (case-insensitive). Nada foi apagado.
    echo.
    pause
    exit /b 0
)

REM --- 2) Confirmacao 2 ---
echo.
echo   [ATENCAO] CONFIRMACAO FINAL.
echo   Vamos apagar: "%USERPROFILE%\.papahr"
echo.
set /p CONF2=Digite APAGAR para confirmar o delete: 
if /i not "%CONF2%"=="APAGAR" (
    echo.
    echo   [Cancelado] Operacao cancelada pelo usuario. Nada foi apagado.
    echo.
    pause
    exit /b 0
)

REM --- 3) Mata processos Python ---
echo.
echo   [Passo 1/3] Fechando processos GSPHosp/Python abertos para liberar locks...
taskkill /F /IM python.exe  /T >nul 2>&1
taskkill /F /IM pythonw.exe /T >nul 2>&1
timeout /T 1 /NOBREAK >nul

REM --- 4) Move para lixeira (se Recycle PS Module existir Windows 10+) senao deleta ---
set "PastaAntiga=%USERPROFILE%\.papahr"
if not exist "%PastaAntiga%" (
    echo.
    echo   [Passo 2/3] Pasta %PastaAntiga% NAO EXISTE (ja estava apagada). Nada para fazer.
    goto :fim
)

REM Tenta mover para lixeira via PowerShell 5.1+
where powershell >nul 2>&1
if not errorlevel 1 (
    echo   [Passo 2/3] Movendo %PastaAntiga% para a LIXEIRA (melhor recuperacao se precisar!)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName Microsoft.VisualBasic ; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('%PastaAntiga%', 'OnlyErrorDialogs', 'SendToRecycleBin')" 2>nul
    if not exist "%PastaAntiga%" goto :passo3ok
    echo     -> (Recycle Bin falhou. Apagando diretamente...)
)

REM Fallback direto: RD /S /Q
echo   [Passo 2/3 Fallback] RD /S /Q "%PastaAntiga%" ...
rd /s /q "%PastaAntiga%" 2>nul

:passo3ok
if exist "%PastaAntiga%" (
    echo.
    echo   [FALHOU] %PastaAntiga% ainda existe! (provavel app aberto = lock SQLite).
    echo   Solucao: FECHE o GSPHosp completamente e rode o batch novamente.
    echo.
    pause
    exit /b 3
)
echo   [Passo 2/3 SUCESSO] Pasta %PastaAntiga% REMOVIDA.

:fim
REM --- 5) Verifica a base nova continua intacta ---
set "PastaNova=%USERPROFILE%\.gsphosp"
echo.
echo   [Passo 3/3] Verificando a base NOVA GSPHosp continua intacta...
if exist "%PastaNova%\gsphosp_users.db" (
    echo     [OK] Base NOVA %PastaNova%\gsphosp_users.db INTACTA (tamanho=%~z1 bytes).
) else (
    echo     [?] Ainda nao existe a base NOVA. (normal se voce ainda nao abriu o GSPHosp apos reset!)
    echo         -> Basta abrir Resetar-E-Abrir-GSPHosp.bat que ele cria automaticamente admin/GSPHosp@2026.
)

REM --- 6) Finaliza ---
echo.
echo =============================================================
echo   LIMPEZA CONCLUIDA 100%.
echo =============================================================
echo.
echo   * PAPAHU (%PastaAntiga%) = apagado.
echo   * GSPHosp (%PastaNova%)  = intacto.
echo.
pause
endlocal & exit /b 0
