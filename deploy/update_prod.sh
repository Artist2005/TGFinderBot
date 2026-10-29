#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/tgfinderbot"
BRANCH="${1:-main}"

echo "🚀 Оновлення TGFinderBot на сервері ($APP_DIR, гілка: $BRANCH)..."
cd "$APP_DIR"

git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

source .venv/bin/activate
pip install --quiet -r requirements.txt

sudo systemctl restart tgfinderbot
sleep 2
sudo systemctl status tgfinderbot --no-pager -l | head -n 20
echo "✅ Деплой успішно завершено!"
