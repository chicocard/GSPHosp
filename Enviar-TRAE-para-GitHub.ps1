<#
.SYNOPSIS
  Envia todo o projeto GSPHosp-Novo para o repositório chicocard/GSPHosp.git
  em um NOVO branch identificado como originado do TRAE (ambiente separado).

.DESCRIPTION
  - Garante que o remote "origin" aponta para https://github.com/chicocard/GSPHosp.git
  - Cria e faz checkout de um branch no formato:  TRAE/<data-hora>-<sufixo>
  - Detecta auth: GH_TOKEN env var, gh auth, ou credenciais do git do sistema
  - Faz commit de TUDO (exceto o que está em .gitignore)
  - Faz push do novo branch (não afeta main nem gsphosp-novo-codigo existente)

.PARAMETER Sufixo
  (Opcional) Texto curto para identificar o branch. Ex: "fix-abas-congeladas"
  Se não fornecido, usa "snapshot".

.PARAMETER MensagemCommit
  (Opcional) Mensagem do commit. Default é automática com data.

.EXAMPLE
  # Mais simples: cria branch TRAE/20260815-2130-snapshot
  .\Enviar-TRAE-para-GitHub.ps1

  # Com identificador do bug fix
  .\Enviar-TRAE-para-GitHub.ps1 -Sufixo "fix-bug-abas-congeladas-e-barGraph"
#>

[CmdletBinding()]
param(
    [string]$Sufixo = "snapshot",
    [string]$MensagemCommit = ""
)

$ErrorActionPreference = "Stop"
$RepoLocal = "d:\papapapa\GSPHosp-Novo"
$RepoRemoto = "https://github.com/chicocard/GSPHosp.git"
$PrefixoBranch = "TRAE"

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "  GSPHosp-Novo -> GitHub chicocard/GSPHosp   (NOVO branch TRAE)" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------
# 1. Garantir diretório correto e presença do git
# ------------------------------------------------------------
Write-Host "[1/7] Verificando ambiente..." -ForegroundColor Yellow

if (-not (Test-Path $RepoLocal)) {
    throw "Diretorio do projeto NAO encontrado: $RepoLocal"
}
Set-Location $RepoLocal
Write-Host "   Diretorio: $PWD" -ForegroundColor DarkGray

try {
    $gitv = git --version 2>$null
    if (-not $gitv) { throw }
    Write-Host "   Git OK: $gitv" -ForegroundColor DarkGray
}
catch {
    throw "Git não encontrado no PATH. Instale o Git for Windows: https://git-scm.com/"
}

# ------------------------------------------------------------
# 2. Inicializar repo (se necessário) e configurar remote
# ------------------------------------------------------------
Write-Host "[2/7] Configurando remote origin -> $RepoRemoto" -ForegroundColor Yellow

if (-not (Test-Path (Join-Path $RepoLocal ".git"))) {
    Write-Host "   .git nao encontrado. Executando git init..." -ForegroundColor DarkMagenta
    git init -q
    git config user.name  "Chico Card (TRAE)"
    git config user.email "chicocard+trae@users.noreply.github.com"
}
else {
    # Tenta preservar config de user.name/email existente
    $un = git config user.name
    $ue = git config user.email
    if ([string]::IsNullOrWhiteSpace($un)) { git config user.name  "Chico Card (TRAE)" }
    if ([string]::IsNullOrWhiteSpace($ue)) { git config user.email "chicocard+trae@users.noreply.github.com" }
}

$remotes = git remote
if ($remotes -contains "origin") {
    $urlAtual = git remote get-url origin
    if ($urlAtual -ne $RepoRemoto) {
        Write-Host "   origin existia ($urlAtual). Alterando para $RepoRemoto..." -ForegroundColor DarkMagenta
        git remote set-url origin $RepoRemoto
    }
    else {
        Write-Host "   origin já correto ($RepoRemoto)." -ForegroundColor DarkGray
    }
}
else {
    Write-Host "   origin não existia. Adicionando..." -ForegroundColor DarkMagenta
    git remote add origin $RepoRemoto
}

# ------------------------------------------------------------
# 3. Montar a URL com autenticação (GH_TOKEN > gh > padrão)
# ------------------------------------------------------------
Write-Host "[3/7] Resolvendo autenticação..." -ForegroundColor Yellow
$authMethod = ""
$remoteFinal = $RepoRemoto

$token = $env:GH_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) { $token = $env:GITHUB_TOKEN }
if ([string]::IsNullOrWhiteSpace($token)) { $token = $env:GIT_TOKEN }

if (-not [string]::IsNullOrWhiteSpace($token)) {
    # Injeta o token no remote temporariamente (não gravado no config)
    $authMethod = "GH_TOKEN (env)"
    $uri     = [System.Uri]$RepoRemoto
    $remoteFinal = "{0}://{1}:x-oauth-basic@{2}{3}" -f $uri.Scheme, $token, $uri.Host, $uri.AbsolutePath
    Write-Host "   Autenticando via variável GH_TOKEN..." -ForegroundColor DarkGreen
}
else {
    # Tenta usar gh CLI credential helper se autenticado
    $ghOk = $false
    try {
        $gh = Get-Command gh -ErrorAction Stop
        $ghStatus = gh auth status 2>&1
        if ($LASTEXITCODE -eq 0) {
            $authMethod = "gh CLI"
            Write-Host "   Autenticando via gh CLI (credenciais da sessão GitHub logada)." -ForegroundColor DarkGreen
            $ghOk = $true
        }
    } catch {}
    if (-not $ghOk) {
        # Fim: usará as credenciais padrão do sistema (Git Credential Manager / Windows Hello)
        $authMethod = "Git Credential Manager (ou credenciais padrão do sistema)"
        Write-Host "   GH_TOKEN e gh CLI não encontrados. Vai abrir janela de login GitHub." -ForegroundColor DarkYellow
    }
}

# ------------------------------------------------------------
# 4. Criar nome do branch TRAE/<data>-<sufixo> e fazer checkout
# ------------------------------------------------------------
Write-Host "[4/7] Criando NOVO branch isolado..." -ForegroundColor Yellow

$ts = Get-Date -Format "yyyyMMdd-HHmm"
$SufixoLimpo = ($Sufixo -replace '[^a-zA-Z0-9_-]', '-').Trim('-')
if ([string]::IsNullOrWhiteSpace($SufixoLimpo)) { $SufixoLimpo = "snapshot" }
$BranchNome = "$PrefixoBranch/$ts-$SufixoLimpo"

# Garante que branch único (se já existir localmente, incrementa -2 -3 etc)
$suffix = 1
$BranchProcurado = $BranchNome
while ((git branch --list $BranchProcurado).Count -gt 0) {
    $suffix++
    $BranchProcurado = "$BranchNome-v$suffix"
}
$BranchNome = $BranchProcurado

Write-Host "   Nome do branch: $BranchNome" -ForegroundColor Green

git checkout -b $BranchNome 2>$null
if ($LASTEXITCODE -ne 0) {
    # Se branch já existe por algum motivo, checkout normal
    git checkout $BranchNome
}
Write-Host "   Checkout OK. HEAD agora em: $(git rev-parse --abbrev-ref HEAD)" -ForegroundColor DarkGray

# ------------------------------------------------------------
# 5. Stage + commit
# ------------------------------------------------------------
Write-Host "[5/7] Staging + commit..." -ForegroundColor Yellow

git add -A 2>&1 | Out-Null

$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace($status)) {
    Write-Host "   Nada mudou desde o último commit (working tree limpo)." -ForegroundColor DarkYellow
}
else {
    if ([string]::IsNullOrWhiteSpace($MensagemCommit)) {
        $MensagemCommit = "[TRAE $ts] Snapshot GSPHosp-Novo (ambiente TRAE) — $SufixoLimpo`n`n" + `
                          "Origem: Ambiente TRAE (local $env:USERNAME em $env:COMPUTERNAME)`n" + `
                          "Data : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss K')`n" + `
                          "Autenticação: $authMethod"
    }
    git commit -m $MensagemCommit 2>&1 | Out-Null
    $commitHash = git rev-parse --short HEAD
    Write-Host "   Commit OK. HEAD = $commitHash" -ForegroundColor Green
}

# ------------------------------------------------------------
# 6. Push (com remote temporário com token se houver)
# ------------------------------------------------------------
Write-Host "[6/7] Push do branch $BranchNome  ->  origin" -ForegroundColor Yellow
Write-Host "   Auth: $authMethod" -ForegroundColor DarkGray

$pushOk = $false
if (-not [string]::IsNullOrWhiteSpace($token)) {
    # push usando a URL com token (não persiste em git config)
    git push -u $remoteFinal HEAD`:refs/heads/$BranchNome --porcelain
    if ($LASTEXITCODE -eq 0) { $pushOk = $true }
}
else {
    git push -u origin HEAD --porcelain
    if ($LASTEXITCODE -eq 0) { $pushOk = $true }
}

if (-not $pushOk) {
    Write-Host ""
    Write-Host "[ERRO] O push FALHOU." -ForegroundColor Red
    Write-Host "   1. Verifique se seu usuário GitHub tem WRITE permission no chicocard/GSPHosp"
    Write-Host "   2. Crie um Personal Access Token (classic) com escopos 'repo' e 'workflow':"
    Write-Host "         https://github.com/settings/tokens"
    Write-Host "   3. Rode novamente, antes setando:"
    Write-Host "         `$env:GH_TOKEN = 'ghp_xxxxxxxxxxxxxxxxxxxx'"
    Write-Host "         .\Enviar-TRAE-para-GitHub.ps1 -Sufixo '$SufixoLimpo'"
    throw "Push falhou. Autorização/Permissão ou token inválido."
}

# ------------------------------------------------------------
# 7. Resumo final
# ------------------------------------------------------------
Write-Host ""
Write-Host "=" * 80 -ForegroundColor Green
Write-Host "  UPLOAD CONCLUÍDO COM SUCESSO." -ForegroundColor Green
Write-Host "=" * 80 -ForegroundColor Green
Write-Host ""
Write-Host "  Repositório remoto : $RepoRemoto"
Write-Host "  Branch novo        : $BranchNome"
Write-Host "  Commit HEAD (curto): $(git rev-parse --short HEAD)"
Write-Host "  Data commit        : $(git log -1 --format=%cd --date=short)"
Write-Host "  Autenticação usada : $authMethod"
Write-Host ""
Write-Host "  Link direto PR / comparar com main:" -ForegroundColor Cyan
Write-Host "     https://github.com/chicocard/GSPHosp/compare/main...$BranchNome"
Write-Host ""
Write-Host "  Para BAIXAR este branch em outra máquina:" -ForegroundColor DarkCyan
Write-Host "     git clone https://github.com/chicocard/GSPHosp.git"
Write-Host "     cd GSPHosp"
Write-Host "     git fetch origin $BranchNome"
Write-Host "     git checkout $BranchNome"
Write-Host ""
Write-Host "  Para VOLTAR ao branch original gsphosp-novo-codigo:" -ForegroundColor DarkCyan
Write-Host "     git checkout gsphosp-novo-codigo"
Write-Host ""
exit 0
