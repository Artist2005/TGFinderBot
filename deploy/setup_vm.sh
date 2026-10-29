#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/tgfinderbot"

echo "=== [1/5] Оновлення пакетів системи (Ubuntu/Debian) ==="
sudo apt-get update -y
sudo apt-get install -y git python3 python3-venv python3-pip gcc libxml2-dev libxslt1-dev

echo "=== [2/5] Налаштування віртуального середовища Python ==="
cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== [3/5] Перевірка файлу .env ==="
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "⚠️ Створено $APP_DIR/.env із шаблону. Впишіть свій BOT_TOKEN командою: nano $APP_DIR/.env"
fi

echo "=== [4/5] Реєстрація systemd-сервісу tgfinderbot ==="
sudo cp "$APP_DIR/deploy/tgfinderbot.service" /etc/systemd/system/tgfinderbot.service
sudo systemctl daemon-reload
sudo systemctl enable tgfinderbot

echo "=== [5/5] Запуск бота ==="
sudo systemctl restart tgfinderbot
sudo systemctl status tgfinderbot --no-pager
