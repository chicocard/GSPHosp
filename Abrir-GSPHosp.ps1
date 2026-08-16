<#
.SYNOPSIS
    Inicializa o GSPHosp (Gestao em Saude Populacional Hospitalar).

.DESCRIPTION
    Script de bootstrap para abrir o GSPHosp.
    COMPATIVEL: Windows PowerShell 5.1 OU PowerShell 7+.
    1. Valida que a pasta D:\papapapa\PaPAHR-Python existe.
    2. Valida que o .venv\Scripts\python.exe existe (ambiente virtual).
    3. Forca UTF-8 no console e PYTHONUTF8=1.
    4. Abre a interface desktop do GSPHosp.

.EXAMPLE
    No PowerShell (qualquer versao):
        PS> .\Abrir-GSPHosp.ps1

    No Explorer (duplo clique):
        Clique direito do mouse - Run with PowerShell
#>
[CmdletBinding()]
param(
    [ValidateSet("gui","cli")]
    [string]$Modo = "gui",
    [switch]$ComVSCode,
    [switch]$ForceNovaJanela
)

$ErrorActionPreference = "Stop"

# Workaround UTF-8: PS 5.1 vs PS 7+ (sem caracteres >=128 no script para evitar parser bug)
if ($PSVersionTable.PSVersion.Major -lt 6) {
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    try { [Console]::InputEncoding  = [System.Text.Encoding]::UTF8 } catch {}
}

$ProjectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "."))
$PythonExe   = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$GuiName     = "GSPHosp (Gestao em Saude Populacional Hospitalar)"

if (-not (Test-Path $ProjectDir)) { throw "Diretorio do projeto nao encontrado: $ProjectDir" }
if (-not (Test-Path $PythonExe)) {
    Write-Host ""
    Write-Host "[ERRO] Python do ambiente virtual (.venv) NAO EXISTE em: $PythonExe" -ForegroundColor Red
    Write-Host "  -> Para corrigir, rode UMA VEZ apenas (na raiz do projeto):"  -ForegroundColor Yellow
    Write-Host "       py -3 -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e .[ui]" -ForegroundColor Gray
    throw "Ambiente virtual ausente."
}

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8            = "1"
$env:PYTHONIOENCODING      = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"

Set-Location $ProjectDir

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ("  " + $GuiName) -ForegroundColor Cyan
Write-Host ("  Projeto:   " + $ProjectDir) -ForegroundColor Gray
Write-Host ("  Python:    " + $PythonExe) -ForegroundColor Gray
Write-Host ("  Modo:      " + $Modo) -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

if ($ComVSCode) {
    code $ProjectDir
    Start-Sleep -Milliseconds 500
}

if ($Modo -eq "cli") {
    $argList = @("-m", "papahr", "--help")
    Write-Host "[i] Modo CLI via python -m papahr --help`n" -ForegroundColor DarkCyan
    & $PythonExe @argList
    exit $LASTEXITCODE
}

# Modo GUI
$entryPoint = "from papahr.ui.app import main ; import sys ; raise SystemExit(main())"
$argList    = @("-c", $entryPoint)

if ($ForceNovaJanela) {
    Start-Process -FilePath $PythonExe -ArgumentList $argList -WorkingDirectory $ProjectDir
    Write-Host "[OK] GSPHosp iniciado em NOVA janela. Pode fechar este terminal.`n" -ForegroundColor Green
    exit 0
}

Write-Host "[i] Abrindo interface desktop do GSPHosp... (aguarde 2-4s)`n" -ForegroundColor DarkCyan
try {
    & $PythonExe @argList
}
finally {
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Host ("`n[!] App finalizou com exit_code=" + $code + ". (0 = OK)") -ForegroundColor Yellow
    } else {
        Write-Host "`n[OK] GSPHosp fechado normalmente.`n" -ForegroundColor Green
    }
}
exit $code
