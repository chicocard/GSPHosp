#!/bin/bash
# entrypoint do contêiner GSPHosp.
#   sem argumentos  → prepara /data e inicia o supervisord (Xvfb, openbox, x11vnc, noVNC, app)
#   wait-x <cmd...> → espera o display :0 existir e executa <cmd> (usado pelos programas do supervisord)
set -e

if [ "$1" = "wait-x" ]; then
  shift
  for i in $(seq 1 60); do
    [ -S /tmp/.X11-unix/X0 ] && break
    sleep 0.5
  done
  exec "$@"
fi

mkdir -p /data/logs /data/cache /data/cnes /data/output
chown -R gsphosp:gsphosp /data 2>/dev/null || true
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
echo "GSPHosp: abra http://localhost:5800 no navegador (resolução ${GSPHOSP_RES})"
exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
