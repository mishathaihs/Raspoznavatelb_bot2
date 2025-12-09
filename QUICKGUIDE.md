Краткий гайд по запуску бота на Beget VDS (Ubuntu) без git
========================================================

Суперкратко (5 шагов)
---------------------
1) SSH под `root`: `apt update && apt upgrade -y` и `apt install -y python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng`.
2) В файловом менеджере создайте `/opt/invoicebot` и загрузите туда всё из папки `Bot` + `.env`, `credentials_drive.json`, `token_drive.json` (если есть).
3) В SSH: `cd /opt/invoicebot && python3 -m venv .venv && source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt`.
4) Если нет `token_drive.json`: `cd /opt/invoicebot && source .venv/bin/activate && python scripts/init_drive_oauth.py`, открыть ссылку, выдать доступ Drive/Sheets, вставить код.
5) Проверить: `source /opt/invoicebot/.venv/bin/activate && python main.py`. Для автозапуска используйте блок systemd ниже.
1) Подготовка сервера (SSH под root)
- В панели Beget откройте «Доступ», возьмите IP/пароль.
- Подключитесь: `ssh root@<IP>`.
- Обновите ОС и поставьте зависимости:
  ```bash
  apt update && apt upgrade -y
  apt install -y python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
  ```

2) Загрузка файлов через «Менеджер файлов»
- В панели зайдите в `/opt` и создайте папку `invoicebot` (если нет).
- Загрузите архив или все файлы каталога `Bot` в `/opt/invoicebot/`, затем распакуйте. Итоговая структура:
  ```
  /opt/invoicebot/
    ├─ main.py
    ├─ requirements.txt
    ├─ bot/
    ├─ services/
    ├─ scripts/
    └─ ...
  ```
- Добавьте рядом `.env`, `credentials_drive.json`, `token_drive.json` (если токен уже есть). Пример `.env`:
  ```env
  TELEGRAM_BOT_TOKEN=...
  OPENAI_API_KEY=...
  GOOGLE_SHEETS_SPREADSHEET_ID=...
  GOOGLE_SHEETS_WORKSHEET_TITLE=...
  GOOGLE_AUTH_MODE=oauth
  GOOGLE_OAUTH_CLIENT_SECRETS_FILE=credentials_drive.json
  GOOGLE_OAUTH_TOKEN_FILE=token_drive.json
  GOOGLE_DRIVE_FOLDER_ID=...
  ALLOW_SHEETS_WITHOUT_DRIVE_FILE=false
  LOG_LEVEL=INFO
  ```

3) Установка зависимостей (SSH)
```bash
cd /opt/invoicebot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

4) OAuth (только если нет `token_drive.json`)
```bash
source /opt/invoicebot/.venv/bin/activate
cd /opt/invoicebot
python scripts/init_drive_oauth.py
```
Откройте ссылку, авторизуйтесь в Google, вставьте код в консоль — появится `token_drive.json`.
Если ошибка `invalid_scope`: удалите `token_drive.json`, убедитесь, что `credentials_drive.json` создан как Desktop App с включёнными API Drive и Sheets, запустите команду снова.

5) Тестовый запуск вручную
```bash
cd /opt/invoicebot
source .venv/bin/activate
python main.py
```

6) Автозапуск через systemd
```bash
cat <<'UNIT' > /etc/systemd/system/invoicebot.service
[Unit]
Description=Telegram Invoice Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/invoicebot
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/invoicebot/.venv/bin/python /opt/invoicebot/main.py
Restart=always
RestartSec=5
User=root
Group=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now invoicebot.service
```
Логи: `journalctl -u invoicebot.service -f`.

7) Обновление бота
- Замените файлы в `/opt/invoicebot/` через панель.
- В SSH:
  ```bash
  cd /opt/invoicebot
  source .venv/bin/activate
  pip install -r requirements.txt
  systemctl restart invoicebot.service
  ```
