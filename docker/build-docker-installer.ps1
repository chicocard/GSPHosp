<#
build-docker-installer.ps1 — constrói a imagem do GSPHosp e o instalador "GSPHosp-Docker-Setup".

Uso (PowerShell, na raiz do projeto, com Docker Desktop aberto):
    .\docker\build-docker-installer.ps1 -Version 1.0.0                 # imagem local + instalador que puxa do ghcr.io
    .\docker\build-docker-installer.ps1 -Version 1.0.0 -ComImagem      # instalador offline com a imagem embutida (~1 GB)
    .\docker\build-docker-installer.ps1 -Version 1.0.0 -Push           # também publica a imagem no ghcr.io (docker login antes)

Pré-requisitos: Docker Desktop; Inno Setup 6.3+ (só para o Setup.exe).
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$ComImagem,
    [switch]$Push,
    [string]$Registro = "ghcr.io/chicocard/gsphosp"
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $raiz
$tag = "${Registro}:${Version}"

Write-Host "== imagem $tag" -ForegroundColor Cyan
docker build -f docker\Dockerfile -t $tag -t "${Registro}:latest" --build-arg BUILDKIT_INLINE_CACHE=1 .
if ($LASTEXITCODE -ne 0) { throw "docker build falhou" }

Write-Host "== teste de fumaca: sobe o conteiner e espera a porta 5800"
docker rm -f gsphosp-smoke 2>$null | Out-Null
docker run -d --name gsphosp-smoke -p 127.0.0.1:5801:5800 --shm-size 1g $tag | Out-Null
$ok = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 2
    try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:5801/" -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { $ok = $true; break } } catch {}
}
$app = docker exec gsphosp-smoke sh -c "tail -n 5 /data/logs/gsphosp.log 2>/dev/null"
docker rm -f gsphosp-smoke | Out-Null
if (-not $ok) { throw "o conteiner nao respondeu na porta 5800; ultimas linhas do app:`n$app" }
Write-Host "   ok (noVNC respondeu)" -ForegroundColor Green

if ($Push) {
    Write-Host "== push $tag"
    docker push $tag; docker push "${Registro}:latest"
}

New-Item -ItemType Directory -Force -Path dist | Out-Null
$defs = @("/DAppVersion=$Version")
if ($ComImagem) {
    Write-Host "== docker save → dist\gsphosp-image.tar"
    docker save -o dist\gsphosp-image.tar $tag
    $defs += "/DComImagem"
}

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { Write-Warning "Inno Setup nao encontrado; a imagem esta pronta ($tag) mas o Setup.exe nao foi gerado."; exit 0 }
& $iscc @defs docker\instalador-docker.iss
$setup = "dist\GSPHosp-Docker-Setup-$Version.exe"
if (Test-Path $setup) { Write-Host "== instalador pronto: $setup ($([math]::Round((Get-Item $setup).Length / 1MB)) MB)" -ForegroundColor Green } else { throw "ISCC nao gerou $setup" }
