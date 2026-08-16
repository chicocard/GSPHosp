<#
.SYNOPSIS
    Apaga 100% a base antiga PAPAHU (~/.papahr) para limpar apos rename.
.DESCRIPTION
    - 2 confirmações (primeiro "SIM", depois "APAGAR") para evitar acidentes.
    - Tenta mover para LIXEIRA (Windows 10/11) antes; senao deleta RD/S/Q.
    - FECHA processos python para liberar lock.
    - NUNCA toca na base NOVA GSPHosp ~/.gsphosp.
    - Compativel Windows PowerShell 5.1 e PowerShell 7+ (ASCII puro).
.EXAMPLE
    .\Limpar-Bases-Antigas-GSPHosp.ps1
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = "High")]
param()
$ErrorActionPreference = "Stop"

$HOME_DIR = [System.IO.Path]::GetFullPath($env:USERPROFILE)
$PASTA_ANTIGA = Join-Path $HOME_DIR ".papahr"
$PASTA_NOVA   = Join-Path $HOME_DIR ".gsphosp"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  GSPHosp - Limpeza base ANTIGA PAPAHU (~/.papahr)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Pasta a ser APAGADA: $PASTA_ANTIGA"
Write-Host "  Pasta PRESERVADA   : $PASTA_NOVA (NAO SERA TOCADA)"
Write-Host ""
Write-Host "  Medidas de seguranca:"
Write-Host "    - 2 confirmacoes manuais antes de apagar (SIM + APAGAR)"
Write-Host "    - Move para LIXEIRA primeiro (melhor recuperacao)"
Write-Host "    - Fecha processos Python/GSPHosp para liberar locks"
Write-Host ""

if (-not (Test-Path $PASTA_ANTIGA)) {
    Write-Host "[INFO] A pasta $PASTA_ANTIGA NAO EXISTE (ja foi apagada antes?)." -ForegroundColor Green
    Write-Host "Nada a fazer. Saindo."
    exit 0
}

# 2 confirmacoes manuais
$ans1 = Read-Host "Confirmacao 1/2: digite SIM para continuar"
if ($ans1.Trim().ToLowerInvariant() -ne "sim") {
    Write-Host "[Cancelado] Confirmacao 1 nao foi 'SIM'. Nada foi apagado." -ForegroundColor Yellow
    exit 0
}
$ans2 = Read-Host "Confirmacao 2/2 FINAL: digite APAGAR para deletar"
if ($ans2.Trim().ToLowerInvariant() -ne "apagar") {
    Write-Host "[Cancelado] Confirmacao 2 nao foi 'APAGAR'. Nada foi apagado." -ForegroundColor Yellow
    exit 0
}

# Mata processos python (libera lock SQLite WAL)
Write-Host ""
Write-Host "[1/3] Matando processos Python/GSPHosp..." -ForegroundColor DarkCyan
Get-Process | Where-Object { $_.ProcessName -match "^pythonw?$" } | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {}
}
Start-Sleep -Milliseconds 600

# Move para lixeira via Microsoft.VisualBasic (garantido PS 5.1/7)
Write-Host "[2/3] Movendo '$PASTA_ANTIGA' para a LIXEIRA..." -ForegroundColor DarkCyan
try {
    Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(
        $PASTA_ANTIGA,
        [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
        [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin
    )
} catch {
    Write-Host "  (Recycle Bin falhou: $_). Fallback RD/S/Q direto..." -ForegroundColor Yellow
    try { Remove-Item -LiteralPath $PASTA_ANTIGA -Recurse -Force -ErrorAction Stop }
    catch {
        Write-Host "[FALHOU] Nao consegui apagar '$PASTA_ANTIGA': $_" -ForegroundColor Red
        Write-Host "   -> Solucao: FECHE o GSPHosp se estiver aberto (lock WAL!) e rode de novo."
        exit 2
    }
}

if (Test-Path $PASTA_ANTIGA) {
    Write-Host "[FALHOU] '$PASTA_ANTIGA' ainda existe apos tentativa de apagar." -ForegroundColor Red
    exit 3
}
Write-Host "[OK] Pasta $PASTA_ANTIGA REMOVIDA com sucesso." -ForegroundColor Green

# Verifica a base nova continua intacta
Write-Host ""
Write-Host "[3/3] Verificando base NOVA GSPHosp intacta..." -ForegroundColor DarkCyan
if (Test-Path (Join-Path $PASTA_NOVA "gsphosp_users.db")) {
    $tam = (Get-Item (Join-Path $PASTA_NOVA "gsphosp_users.db")).Length
    Write-Host "[OK] Base NOVA gsphosp_users.db intacta, $tam bytes." -ForegroundColor Green
} else {
    Write-Host "[?] Base NOVA ainda nao existe (normal se reset ainda nao rodou)." -ForegroundColor Yellow
    Write-Host "   -> Rode 'Resetar-E-Abrir-GSPHosp.bat' em 2 cliques."
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  LIMPEZA CONCLUIDA 100%" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Pressione qualquer tecla para sair..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
exit 0
