# GSPHosp em Docker — imagem, instalador e publicação

O app Qt roda dentro de um contêiner, num display virtual, e aparece no navegador do usuário em
`http://localhost:5800`. Nada de Python, Qt ou dependências na máquina: só o Docker Desktop.
A mesma imagem serve para um servidor (VM na nuvem) — aí o usuário só recebe um endereço.

Validado neste pacote: a pilha Xvfb + openbox + x11vnc + noVNC + app foi executada com o
widget do mapa e verificada no navegador, inclusive clique no mapa. O `docker build` em si não
pôde rodar no ambiente de montagem (sem daemon); o workflow e o script de build fazem um teste
de fumaça automático (sobe o contêiner e exige resposta na porta 5800).

## Arquivos

```
docker/Dockerfile                 python:3.12-slim + libs Qt/QtWebEngine + Mesa (GL por software) + Xvfb/x11vnc/noVNC/supervisor
docker/supervisord.conf           ordem de subida: Xvfb → openbox → x11vnc → noVNC → python -m papahr
docker/openbox-rc.xml             janela do app maximizada e sem bordas
docker/entrypoint.sh              prepara /data e inicia o supervisord; "wait-x" espera o display
docker/novnc-index.html           http://localhost:5800/ abre direto o app (autoconnect)
docker-compose.yml                serviço gsphosp, porta 127.0.0.1:5800, volume gsphosp-data, saídas em ./output
.env.example                      versão da imagem, porta, resolução, pasta de saída
.dockerignore
docker/gsphosp-docker.ps1         start / stop / update / logs / status (usado pelos atalhos do instalador)
docker/instalador-docker.iss      Inno Setup: GSPHosp-Docker-Setup-<versão>.exe
docker/build-docker-installer.ps1 build da imagem + teste de fumaça + (opcional) push + instalador
.github/workflows/docker-image.yml tag v* → imagem em ghcr.io + instalador na Release
```

## O que o usuário final faz

1. Instala o Docker Desktop (uma vez; precisa de Windows 10/11 64 bits com virtualização
   habilitada e WSL 2 — o instalador do Docker cuida do WSL e pede reinício).
2. Executa `GSPHosp-Docker-Setup-<versão>.exe` (sem administrador). Se o Docker não estiver
   instalado, o instalador abre a página de download e orienta.
3. Clica em "GSPHosp" no menu Iniciar. Na primeira vez a imagem é baixada do GitHub
   (~1 GB; alguns minutos); depois o navegador abre em `http://localhost:5800` com o app.
   "GSPHosp – parar" desliga o contêiner; "GSPHosp – atualizar" baixa a versão nova.

Saídas (CSV/Parquet) aparecem em `Documentos\GSPHosp\output`. CNES, cache e logs ficam no
volume `gsphosp-data` e sobrevivem a atualizações.

## Limites desta forma de distribuição (decidir com isso em mente)

- **Docker Desktop é um pré-requisito pesado** para quem não é técnico: download de ~600 MB,
  WSL 2, virtualização na BIOS, reinício. Em máquinas corporativas costuma exigir TI.
- **Licença**: Docker Desktop é gratuito para uso pessoal, educação, código aberto e empresas
  pequenas (< 250 pessoas e < US$ 10 mi/ano). Órgãos públicos e instituições grandes precisam
  de assinatura; verifique o caso da UFMG/SES antes de distribuir. Alternativas gratuitas
  compatíveis com `docker compose`: Podman Desktop e Rancher Desktop.
- **Imagem de ~1 GB** (Qt + Chromium do QtWebEngine + Mesa) contra 150–250 MB do instalador nativo.
- **Desempenho gráfico**: o app renderiza por software e é transmitido por VNC; funciona bem em
  localhost, mas pan/zoom no mapa é menos fluido que no app nativo.
- **Sessão única por contêiner**: noVNC mostra o mesmo desktop para quem conectar. Para vários
  usuários num servidor, um contêiner (porta) por usuário, ou um gateway como Kasm/Guacamole.

Para gestores em desktops Windows, o instalador nativo (pasta `build/`, pacote anterior) é o
caminho de menor atrito. O Docker compensa quando a execução vai para um servidor ou quando a
TI do usuário já tem Docker como padrão. Os dois podem coexistir: a mesma tag gera os dois.

## Segurança

- O compose publica a porta só em `127.0.0.1`: ninguém na rede alcança o app. Para expor num
  servidor, troque por `0.0.0.0:5800:5800` **e** coloque autenticação na frente (noVNC não tem
  senha configurada aqui): um proxy reverso com login (Caddy/NGINX + basic auth ou SSO) e TLS.
- O app roda como usuário `gsphosp` (uid 1000), não root; o Chromium do QtWebEngine roda com
  `--no-sandbox`, que é o padrão viável em contêiner.
- Os mesmos bloqueadores do `DEPLOY.md` valem aqui: reset automático da senha do admin e log
  `[DEBUG _do_login]` precisam sair antes de qualquer distribuição.

## Construir e publicar

Na sua máquina (Docker Desktop aberto; Inno Setup para o Setup.exe):

```powershell
cd D:\papapapa\GSPHosp-Novo
.\docker\build-docker-installer.ps1 -Version 1.0.0            # imagem local + Setup.exe que puxa do ghcr.io
.\docker\build-docker-installer.ps1 -Version 1.0.0 -ComImagem # Setup.exe offline com a imagem embutida (~1 GB)
```

Pelo GitHub (recomendado): com o workflow no repositório, cada tag gera e publica tudo.

```powershell
git add -A
git commit -m "Docker: imagem noVNC, compose, instalador e workflow"
git push
git tag v1.0.0
git push origin v1.0.0
```

Depois do primeiro push da imagem, em GitHub → seu perfil → Packages → `gsphosp` → Package
settings → **Change visibility → Public**; sem isso o `docker pull` dos usuários pede login.
O workflow também aceita execução manual (Actions → "Imagem Docker + instalador" → Run workflow).

## Rodar num servidor (VM na nuvem)

```bash
mkdir -p ~/gsphosp && cd ~/gsphosp
curl -O https://raw.githubusercontent.com/chicocard/GSPHosp/main/docker-compose.yml
printf 'GSPHOSP_VERSION=1.0.0\nGSPHOSP_PORT=5800\nGSPHOSP_RES=1600x900\nGSPHOSP_OUTPUT_DIR=./output\n' > .env
docker compose up -d
```

Para acesso pela rede, edite `ports:` no compose e coloque um proxy com autenticação e TLS na
frente, como descrito em Segurança. Resolução maior: `GSPHOSP_RES=1920x1080` no `.env`.

## Diagnóstico

```powershell
docker compose ps
docker compose logs --tail 100
docker exec gsphosp tail -n 50 /data/logs/gsphosp.log   # log do app
docker exec gsphosp tail -n 50 /data/logs/supervisord.log
```

Tela preta no navegador = o app não desenhou (ver `gsphosp.log`); "Failed to connect" no noVNC =
contêiner parado ou porta ocupada (mude `GSPHOSP_PORT` no `.env`).
