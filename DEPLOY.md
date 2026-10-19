# GSPHosp — empacotamento e distribuição (instalador Windows)

Objetivo: gestores instalam o GSPHosp com um `Setup.exe` baixado da página de Releases
do GitHub, sem Python, Git ou linha de comando. O próprio app baixa a base do CNES.

## O que este pacote acrescenta ao projeto

```
build/GSPHosp.spec            PyInstaller (onedir): Qt + QtWebEngine + Polars + DuckDB + dados do pacote
build/launcher.py             ponto de entrada do .exe (pastas de dados do usuário, log de inicialização)
build/instalador.iss          Inno Setup: instala por usuário em %LOCALAPPDATA%\Programs\GSPHosp
build/build.ps1               gera dist\GSPHosp\ (portátil) e dist\GSPHosp-Setup-<versão>.exe
build/icone.ico               ícone do app
.github/workflows/build-windows.yml   tag v* → GitHub Actions compila e publica a Release
papahr/dashboard/cnes_download.py     download/extração da Base de Dados do CNES (FTP DATASUS)
papahr/ui/cnes_dialog.py              diálogo "Baixar base do CNES" com progresso
tests/test_cnes_download.py           7 testes
```

Validado: a spec foi compilada e o executável aberto (offscreen) com o widget do mapa, QSS,
malha, QtWebEngine e ponte QWebChannel funcionando. O FTP do DATASUS não era acessível no
ambiente de montagem; `cnes_download.py` foi testado com ZIP sintético e download simulado.

## Antes de distribuir — bloqueadores

Estes itens aparecem no log de 17/10 e impedem entregar o app a terceiros:

1. **`Senha resetada: user=admin ... new_role=ADMIN` na abertura.** Se isso é automático, qualquer
   pessoa que abrir o app vira administrador. Localizar com
   `Select-String -Pattern "Senha resetada" -Path .\papahr\**\*.py` e remover a chamada do
   fluxo de inicialização; deixar o reset só atrás de um comando explícito.
2. **`[DEBUG _do_login] ... identity=... pwd_len=...`** grava dados de login em arquivo. Remover.
3. **Saídas dentro da pasta do programa.** `output\`, cache e DuckDB precisam ir para as pastas do
   usuário. O launcher já cria e exporta `GSPHOSP_HOME` (`%LOCALAPPDATA%\GSPHosp`) e
   `GSPHOSP_OUTPUT` (`Documentos\GSPHosp\output`); o app precisa lê-las (prompt abaixo).
4. **CNES.** O loader procura em `C:\DICIONARIO_DADOS\`; precisa procurar também em
   `%LOCALAPPDATA%\GSPHosp\cnes` e oferecer o diálogo de download quando não achar.
5. **`.gitignore`**: `output/`, `.venv/`, `.venv-build/`, `build/versao.txt`, `dist/`, `*.spec.bak`.

## Prompt de integração (TRAE/Antigravity)

> No GSPHosp, preparar o app para rodar instalado (PyInstaller), sem mudar o pipeline:
>
> 1. Onde o app define a pasta de saída (`output\`), a pasta de cache e o caminho do DuckDB, usar
>    `os.environ.get("GSPHOSP_OUTPUT")`, `os.environ.get("GSPHOSP_HOME")` quando existirem; manter
>    os caminhos atuais como fallback quando as variáveis não estiverem definidas (modo desenvolvimento).
> 2. Em `papahr/dashboard/cnes_loader.py`, ao auto-descobrir pastas `BASE_DE_DADOS_CNES_*`, procurar
>    em `papahr.dashboard.cnes_download.pastas_cnes_candidatas()` (primeiro `%LOCALAPPDATA%\GSPHosp\cnes`,
>    depois `C:\DICIONARIO_DADOS`). Se nenhuma for encontrada, o Passo 1 do Wizard mostra um botão
>    "Baixar base do CNES" que abre `papahr.ui.cnes_dialog.CnesDownloadDialog`; ao aceitar, usar
>    `dlg.pasta_resultado` como pasta do CNES e recarregar o loader.
> 3. Remover do fluxo de inicialização qualquer chamada que redefina a senha do admin e o log
>    `[DEBUG _do_login]`. A senha inicial do admin deve ser definida na primeira execução, numa tela
>    "Criar senha do administrador", com obrigação de troca no primeiro login de outros usuários.
> 4. Em `papahr/__main__.py` garantir que `main()` exista e que `python -m papahr` continue funcionando
>    (o `build/launcher.py` executa o módulo com `runpy`).
> 5. Não alterar `build/`, `cnes_download.py` nem `cnes_dialog.py`. Rodar `pytest tests -q` ao final.

## Gerar o instalador na sua máquina

Pré-requisitos (uma vez): Python 3.12 de [python.org](https://www.python.org/downloads/windows/)
(o PyInstaller demora a acompanhar versões novas do Python; 3.12 é a estável para empacotar) e
Inno Setup 6.3+ de [jrsoftware.org](https://jrsoftware.org/isdl.php).

```powershell
cd D:\papapapa\GSPHosp-Novo
.\build\build.ps1 -Version 1.0.0
```

O script cria `.venv-build` (separado do seu `.venv`), instala o projeto e o PyInstaller, compila,
abre o executável por 10 s como teste de fumaça e chama o Inno Setup. Resultado:
`dist\GSPHosp-Setup-1.0.0.exe` (cerca de 150 a 250 MB; QtWebEngine é a maior parte) e a pasta
portátil `dist\GSPHosp\`.

Se o executável não abrir: `.\build\build.ps1 -Version 1.0.0 -Console` gera a variante com janela
de console, que mostra o erro; o launcher também grava `%LOCALAPPDATA%\GSPHosp\logs\launcher.log`.

## Publicar pelo GitHub (recomendado)

Com o workflow no repositório, cada tag `vX.Y.Z` gera o instalador num runner Windows do GitHub
e o anexa à Release — não depende da sua máquina.

```powershell
git add -A
git commit -m "Empacotamento: PyInstaller, Inno Setup, download do CNES, workflow de release"
git push
git tag v1.0.0
git push origin v1.0.0
```

Em 10 a 15 minutos, a aba **Actions** mostra o build e **Releases** passa a ter
`GSPHosp-Setup-1.0.0.exe` e `GSPHosp-portatil-1.0.0.zip`. Repositório privado: o GitHub dá 2.000
minutos/mês gratuitos em runners Linux; o runner Windows consome a 2× — cada build custa ~20 a 30
minutos dessa cota. Repositório público: sem limite.

## Sem assinatura de código

O Windows exibirá o aviso do SmartScreen ("Windows protegeu o seu PC") na primeira execução do
instalador. O usuário clica em "Mais informações" → "Executar assim mesmo". Para eliminar o aviso
é preciso um certificado de assinatura de código (OV, ~US$ 200–400/ano, ou EV) e acrescentar
`signtool` ao `build.ps1` e ao workflow; a reputação do SmartScreen também melhora com o tempo
conforme mais instalações do mesmo arquivo ocorrem.

## Atualizações

Nova versão = nova tag. O instalador detecta a versão anterior pelo `AppId` e atualiza no lugar,
preservando `%LOCALAPPDATA%\GSPHosp` (CNES, cache, logs) e `Documentos\GSPHosp`. O manual de
utilização deve ganhar uma seção "Instalação pelo Setup.exe" apontando para a página de Releases.
