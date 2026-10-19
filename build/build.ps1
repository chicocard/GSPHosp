<#
build.ps1 — gera o instalador do GSPHosp na sua máquina.

Uso (PowerShell, na raiz do projeto):
    .\build\build.ps1 -Version 1.0.0
    .\build\build.ps1 -Version 1.0.0 -Console      # GSPHosp.exe com janela de console (diagnóstico)
    .\build\build.ps1 -Version 1.0.0 -SkipInstaller # só a pasta dist\GSPHosp (pacote portátil)

Pré-requisitos:
    * Python 3.12 instalado (py -3.12 funciona). O PyInstaller não acompanha versões recém-lançadas
      do Python; 3.12 é a versão estável para empacotar.
    * Inno Setup 6.3+ (https://jrsoftware.org/isdl.php) para gerar o Setup.exe. Sem ele, o script
      termina na pasta portátil.

O script cria um ambiente separado (.venv-build), instala o projeto e o PyInstaller, compila
com build\GSPHosp.spec e chama o ISCC. Nada é alterado no .venv de desenvolvimento.
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$Console,
    [switch]$SkipInstaller,
    [string]$PythonLauncher = "py -3.12"
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $raiz
Write-Host "== GSPHosp build $Version em $raiz" -ForegroundColor Cyan

# 1. ambiente de build
$venv = Join-Path $raiz ".venv-build"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "-- criando .venv-build com $PythonLauncher"
    Invoke-Expression "$PythonLauncher -m venv `"$venv`""
}
& $py -m pip install --upgrade pip wheel | Out-Null
if (Test-Path (Join-Path $raiz "requirements.txt")) { & $py -m pip install -r requirements.txt }
& $py -m pip install -e . pyinstaller
& $py -c "import PySide6, polars, duckdb; print('PySide6', PySide6.__version__, '| polars', polars.__version__, '| duckdb', duckdb.__version__)"

# 2. arquivo de versão do executável (propriedades do .exe no Windows)
$partes = ($Version -split '[^0-9]' | Where-Object { $_ -ne '' } | Select-Object -First 4)
while ($partes.Count -lt 4) { $partes += '0' }
$tupla = ($partes -join ', ')
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tupla), prodvers=($tupla), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', 'NESCON/UFMG - Orbitaai'),
      StringStruct('FileDescription', 'GSPHosp - Gestao em Saude Populacional Hospitalar'),
      StringStruct('FileVersion', '$Version'),
      StringStruct('ProductName', 'GSPHosp'),
      StringStruct('ProductVersion', '$Version'),
      StringStruct('OriginalFilename', 'GSPHosp.exe')])]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])]
)
"@ | Set-Content -Path (Join-Path $raiz "build\versao.txt") -Encoding UTF8

# 3. PyInstaller
if (Test-Path (Join-Path $raiz "dist\GSPHosp")) { Remove-Item (Join-Path $raiz "dist\GSPHosp") -Recurse -Force }
$args = @("build\GSPHosp.spec", "--noconfirm", "--clean")
if ($Console) { $args += @("--", "--console") }
Write-Host "-- pyinstaller $($args -join ' ')"
& $py -m PyInstaller @args
if (-not (Test-Path (Join-Path $raiz "dist\GSPHosp\GSPHosp.exe"))) { throw "PyInstaller nao gerou dist\GSPHosp\GSPHosp.exe" }

# 4. teste de fumaça: o executável abre e fecha?
Write-Host "-- teste de fumaca (abre o app por 10 s)"
$p = Start-Process -FilePath (Join-Path $raiz "dist\GSPHosp\GSPHosp.exe") -PassThru
Start-Sleep -Seconds 10
if ($p.HasExited -and $p.ExitCode -ne 0) { throw "GSPHosp.exe terminou com codigo $($p.ExitCode); veja %LOCALAPPDATA%\GSPHosp\logs\launcher.log" }
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }

$tamanho = [math]::Round((Get-ChildItem (Join-Path $raiz "dist\GSPHosp") -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "-- pasta portatil pronta: dist\GSPHosp ($tamanho MB)" -ForegroundColor Green
if ($SkipInstaller) { exit 0 }

# 5. Inno Setup
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup nao encontrado. Instale de https://jrsoftware.org/isdl.php e rode de novo, ou use a pasta dist\GSPHosp como pacote portatil."
    exit 0
}
& $iscc "/DAppVersion=$Version" (Join-Path $raiz "build\instalador.iss")
$setup = Join-Path $raiz "dist\GSPHosp-Setup-$Version.exe"
if (Test-Path $setup) {
    $mb = [math]::Round((Get-Item $setup).Length / 1MB)
    Write-Host "== Instalador pronto: $setup ($mb MB)" -ForegroundColor Green
} else { throw "ISCC nao gerou $setup" }
