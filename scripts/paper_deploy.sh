#!/bin/bash
# Nasadenie virtuálnych účtov: stiahne kód a reštartuje LEN ctrader-paper.
#
#   ssh hetzner '/home/marian/trading-bot/scripts/paper_deploy.sh'
#
# Živého bota (ctrader-bot) NEREŠTARTUJE — beží ďalej so starým kódom
# v pamäti. Nové súbory na disku si načíta až pri najbližšom vlastnom
# reštarte (bot_deploy.sh), preto sa sem nesmie dostať zmena živého bota,
# ktorá nie je pripravená ísť von.
set -eu
cd "$(dirname "$0")/.."

OWNER=$(stat -c '%U' .git)
asowner() { if [ "$(id -un)" = "$OWNER" ]; then "$@"; else sudo -u "$OWNER" "$@"; fi; }

echo "pred:  $(git log -1 --pretty='%h %s')"
asowner git pull --ff-only origin "$(git rev-parse --abbrev-ref HEAD)"
echo "po:    $(git log -1 --pretty='%h %s')"

echo
echo "reštartujem ctrader-paper…"
systemctl restart ctrader-paper
sleep 4
journalctl -u ctrader-paper --since "-1 min" --no-pager \
  | grep -E "Virtuálny účet|zahriate|ERROR|Traceback" | tail -12 || true
echo
systemctl is-active --quiet ctrader-paper \
  && echo "✅ ctrader-paper beží" || echo "⛔ ctrader-paper NEBEŽÍ — journalctl -u ctrader-paper"
