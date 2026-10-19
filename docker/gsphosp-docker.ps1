<#
gsphosp-docker.ps1 — controla o GSPHosp em Docker na máquina do usuário.
Instalado pelo GSPHosp-Docker-Setup; os atalhos do menu Iniciar chamam:

    gsphosp-docker.ps1 start     sobe o contêiner (puxa/carrega a imagem se preciso) e abre o navegador
    gsphosp-docker.ps1 stop      para o contêiner (dados preservados)
    gsphosp-docker.ps1 update    baixa a imagem da versão em .env e reinicia
    gsphosp-docker.ps1 logs      mostra os logs do contêiner
    gsphosp-docker.ps1 status    diagnóstico: Docker, contêiner, porta
#>
param([ValidateSet("start", "stop", "update", "logs", "status")][string]$Acao = "start")

$ErrorActionPreference = "Stop"
$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $pasta
$porta = 5800
if (Test-Path ".env") {
    $m = Select-String -Path ".env" -Pattern "^GSPHOSP_PORT=(\d+)" | Select-Object -First 1
    if ($m) { $porta = [int]$m.Matches[0].Groups[1].Value }
}
$url = "http://localhost:$porta/"

function Falha($msg) {
    Write-Host ""; Write-Host "ERRO: $msg" -ForegroundColor Red; Write-Host ""
    Write-Host "Pressione Enter para fechar"; [void](Read-Host); exit 1
}

function DockerPronto {
    # 1. cliente instalado?
    $cli = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $cli) {
        Write-Host "Docker Desktop não encontrado. Abrindo a página de download..." -ForegroundColor Yellow
        Start-Process "https://docs.docker.com/desktop/setup/install/windows-install/"
        Falha "Instale o Docker Desktop, reinicie o computador e abra o GSPHosp de novo."
    }
    # 2. daemon respondendo? se não, tenta abrir o Docker Desktop e espera até 3 min
    $ok = $false
    try { docker info 2>$null | Out-Null; $ok = ($LASTEXITCODE -eq 0) } catch { $ok = $false }
    if (-not $ok) {
        $exe = @("$env:ProgramFiles\Docker\Docker\Docker Desktop.exe", "$env:LOCALAPPDATA\Docker\Docker Desktop.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($exe) { Write-Host "Iniciando o Docker Desktop..." ; Start-Process $exe | Out-Null }
        for ($i = 0; $i -lt 90; $i++) {
            Start-Sleep -Seconds 2
            try { docker info 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { $ok = $true; break } } catch {}
            if ($i % 10 -eq 0) { Write-Host "  aguardando o Docker ($($i*2) s)..." }
        }
    }
    if (-not $ok) { Falha "O Docker Desktop não respondeu. Abra-o pelo menu Iniciar, espere o ícone da baleia ficar estável e tente de novo." }
}

function ImagemLocal {
    # imagem embutida no instalador (gsphosp-image.tar) ainda não carregada?
    $tar = Join-Path $pasta "gsphosp-image.tar"
    if (Test-Path $tar) {
        $img = (Get-Content .env | Select-String "^GSPHOSP_IMAGE=(.+)$").Matches[0].Groups[1].Value
        $tem = docker image inspect $img 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Carregando a imagem do GSPHosp (uma vez, pode levar alguns minutos)..."
            docker load -i $tar
            if ($LASTEXITCODE -ne 0) { Falha "docker load falhou." }
        }
    }
}

function EsperarPorta {
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Seconds 2
        if ($i % 5 -eq 0) { Write-Host "  aguardando o GSPHosp iniciar ($($i*2) s)..." }
    }
    return $false
}

switch ($Acao) {
    "start" {
        Write-Host "== GSPHosp (Docker)" -ForegroundColor Cyan
        DockerPronto
        ImagemLocal
        Write-Host "Subindo o contêiner..."
        docker compose up -d
        if ($LASTEXITCODE -ne 0) { Falha "docker compose up falhou. Rode 'gsphosp-docker.ps1 logs' para ver o motivo." }
        if (EsperarPorta) {
            Write-Host "Pronto: abrindo $url" -ForegroundColor Green
            Start-Process $url
        } else {
            Falha "O GSPHosp subiu mas não respondeu em $url. Veja os logs (atalho 'GSPHosp - logs')."
        }
    }
    "stop" {
        Write-Host "Parando o GSPHosp..."
        docker compose stop
        Write-Host "Parado. Os dados ficam guardados no volume gsphosp-data." -ForegroundColor Green
        Start-Sleep -Seconds 2
    }
    "update" {
        DockerPronto
        Write-Host "Baixando a imagem mais recente indicada em .env..."
        docker compose pull
        if ($LASTEXITCODE -ne 0) { Falha "docker compose pull falhou (sem internet ou imagem privada?)." }
        docker compose up -d
        if (EsperarPorta) { Write-Host "Atualizado." -ForegroundColor Green; Start-Process $url }
    }
    "logs" {
        docker compose logs --tail 200
        Write-Host ""; Write-Host "Logs internos: docker exec gsphosp tail -n 100 /data/logs/gsphosp.log"
        Write-Host "Pressione Enter para fechar"; [void](Read-Host)
    }
    "status" {
        Write-Host "docker: " -NoNewline; try { docker --version } catch { Write-Host "ausente" }
        try { docker info 2>$null | Out-Null; Write-Host "daemon: $(if ($LASTEXITCODE -eq 0) {'ok'} else {'parado'})" } catch { Write-Host "daemon: parado" }
        docker compose ps
        try { $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2; Write-Host "porta ${porta}: $($r.StatusCode)" } catch { Write-Host "porta ${porta}: sem resposta" }
        Write-Host "Pressione Enter para fechar"; [void](Read-Host)
    }
}
