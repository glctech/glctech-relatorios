#!/usr/bin/env bash
# Instalação do agente em /opt/glc-relatorio-trafego (executar com sudo)
set -euo pipefail
DEST=/opt/glc-relatorio-trafego
RUN_USER=${RUN_USER:-andre}
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update -qq && apt-get install -y -qq python3-venv fonts-dejavu-core

mkdir -p "$DEST"
cp -r "$SRC"/. "$DEST"/
[ -f "$DEST/config.env" ] || cp "$DEST/config.env.example" "$DEST/config.env"
chmod 600 "$DEST/config.env"

python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install -q --upgrade pip
"$DEST/venv/bin/pip" install -q -r "$DEST/requirements.txt"
chown -R "$RUN_USER":"$RUN_USER" "$DEST"
chmod 700 "$DEST/credentials"

sed "s/^User=.*/User=$RUN_USER/" "$DEST/deploy/glc-relatorio-trafego.service" > /etc/systemd/system/glc-relatorio-trafego.service
cp "$DEST/deploy/glc-relatorio-trafego.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now glc-relatorio-trafego.timer

echo
echo "Instalado. Próximos passos:"
echo " 1) Coloque a chave da service account em $DEST/credentials/service-account.json"
echo " 2) Preencha SMTP_PASSWORD em $DEST/config.env"
echo " 3) Teste: sudo -u $RUN_USER $DEST/venv/bin/python $DEST/glc_relatorio_trafego.py --no-email"
systemctl list-timers glc-relatorio-trafego.timer --no-pager
