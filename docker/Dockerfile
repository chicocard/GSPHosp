# syntax=docker/dockerfile:1
# GSPHosp em contêiner: o app Qt roda num display virtual (Xvfb) e é exibido no
# navegador do usuário via noVNC em http://localhost:5800 — nada de instalar Python,
# Qt ou dependências na máquina. Dados persistem no volume /data.
#
# Construir (na raiz do projeto):  docker build -f docker/Dockerfile -t gsphosp:dev .
# Rodar:                           docker compose up -d   (ver docker-compose.yml)

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Bibliotecas de sistema: Qt 6 (xcb) + QtWebEngine (Chromium) + display virtual + VNC/noVNC
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl locales tini supervisor \
      xvfb x11vnc openbox novnc websockify x11-xserver-utils \
      fonts-dejavu-core fonts-liberation fonts-noto-core \
      libgl1 libegl1 libopengl0 libglib2.0-0 libfontconfig1 libfreetype6 libdbus-1-3 \
      libxkbcommon0 libxkbcommon-x11-0 libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-icccm4 \
      libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-render0 \
      libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
      libxrender1 libxi6 libxtst6 libxrandr2 libxcomposite1 libxdamage1 libxcursor1 \
      libxss1 libxext6 libxfixes3 libsm6 libice6 libnss3 libnspr4 libasound2 libdrm2 \
      libgbm1 libcups2 libxshmfence1 libgl1-mesa-dri \
    && sed -i 's/^# *pt_BR.UTF-8/pt_BR.UTF-8/' /etc/locale.gen && locale-gen \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=pt_BR.UTF-8 LC_ALL=pt_BR.UTF-8 \
    DISPLAY=:0 \
    QT_QPA_PLATFORM=xcb \
    LIBGL_ALWAYS_SOFTWARE=1 \
    GALLIUM_DRIVER=llvmpipe \
    QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage" \
    QTWEBENGINE_DISABLE_SANDBOX=1 \
    GSPHOSP_HOME=/data \
    GSPHOSP_OUTPUT=/data/output \
    GSPHOSP_RES=1600x900

# Usuário sem privilégios para o app (uid 1000 = mesmo dos volumes montados em Linux)
RUN useradd --create-home --uid 1000 --shell /bin/bash gsphosp \
    && mkdir -p /data /opt/gsphosp/app && chown -R gsphosp:gsphosp /data /opt/gsphosp

WORKDIR /opt/gsphosp/app

# Dependências Python primeiro (cache de camada): pyproject/requirements mudam menos que o código
COPY pyproject.toml requirements*.txt ./
RUN if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

# Código do app
COPY --chown=gsphosp:gsphosp . .
RUN pip install -e . \
    && python -c "import papahr, PySide6, polars, duckdb; print('ok', PySide6.__version__)"

# Pilha gráfica e supervisão
COPY docker/supervisord.conf /etc/supervisor/conf.d/gsphosp.conf
COPY docker/openbox-rc.xml /opt/gsphosp/openbox-rc.xml
COPY docker/entrypoint.sh /opt/gsphosp/entrypoint.sh
COPY docker/novnc-index.html /usr/share/novnc/index.html
RUN chmod +x /opt/gsphosp/entrypoint.sh

VOLUME ["/data"]
EXPOSE 5800
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:5800/ >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/gsphosp/entrypoint.sh"]
