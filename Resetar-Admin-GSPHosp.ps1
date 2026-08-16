<#
.SYNOPSIS
    GSPHosp utility: Diagnostica e RESETA login admin / GSPHosp@2026.
    Compativel com Windows PowerShell 5.1 e PowerShell 7+.
.DESCRIPTION
    1. Fecha processos python/GSPHosp abertos (remove lock do SQLite).
    2. Roda Resetar-Admin-GSPHosp.py (diagnostico + reset + migracao usuarios).
    3. Duplo clique funciona (ExecutionPolicy Bypass).
.EXAMPLE
    PowerShell (qualquer versao):
        .\Resetar-Admin-GSPHosp.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"

$PROJDIR = $PSScriptRoot
$PYEXE   = Join-Path $PROJDIR ".venv\Scripts\python.exe"
$SCRIPT  = Join-Path $PROJDIR "Resetar-Admin-GSPHosp.py"

# 1) UTF-8 (melhor effort nas duas versoes PS, mas SEM caracteres nao-ASCII no script)
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { [Console]::InputEncoding  = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"

# 2) Garante existencia arquivos
if (-not (Test-Path $PYEXE)) { throw "[ERRO] Python do venv nao encontrado: $PYEXE. Rode 1 vez: py -3 -m venv .venv ; pip install -e .[ui]" }
if (-not (Test-Path $SCRIPT)) { throw "[ERRO] Script Python nao encontrado: $SCRIPT" }

# 3) Mata processos python para liberar lock SQLite WAL (GSPHosp aberto?)
Write-Host "--- 1/3) Fechando processos GSPHosp/Python abertos (liberar lock SQLite) ---" -ForegroundColor Cyan
$matou = $false
Get-Process | Where-Object { $_.ProcessName -match "^pythonw?$" } | ForEach-Object {
    try {
        Write-Host ("  -> PID=" + $_.Id + "  Proc=" + $_.ProcessName + "  StartTime=" + $_.StartTime + "  (Stop)")
        Stop-Process -Id $_.Id -Force -ErrorAction Stop
        $matou = $true
    } catch {
        Write-Host ("  -> Skip PID=" + $_.Id + ": " + $_) -ForegroundColor Yellow
    }
}
if (-not $matou) { Write-Host "  (nenhum python aberto - DB deve estar livre)" -ForegroundColor Gray }
Start-Sleep -Milliseconds 600

# 4) Roda script Python principal
Write-Host ""
Write-Host "--- 2/3) Executando Resetar-Admin-GSPHosp.py (diagnostico + reset + migracao) ---" -ForegroundColor Cyan
Set-Location $PROJDIR
& $PYEXE $SCRIPT
$EXITCODE = $LASTEXITCODE
Write-Host ""
Write-Host ("--- 3/3) Concluido. exit_code=" + $EXITCODE + " ---") -ForegroundColor Cyan

# 5) Deixa o terminal aberto se foi duplo clique (para usuario ler resultado)
if ($EXITCODE -ne 0) {
    Write-Host "[!] exit_code != 0. Pressione qualquer tecla para fechar esta janela..." -ForegroundColor Yellow
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
exit $EXITCODE
