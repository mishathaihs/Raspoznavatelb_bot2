## 0. Что у тебя уже есть

У тебя уже:

* папка проекта: `C:\Users\Haier\Desktop\GoogleDOCBOT`
* виртуалка `.venv` и зависимости (`pip install -r requirements.txt` ты уже делал)
* Tesseract стоит (OCR уже работал)
* Кодекс перевёл бота на **OAuth** (Drive + Sheets) и добавил скрипт `scripts\init_drive_oauth.py` и новые поля в конфиг.

Дальше всё делаем в этой папке.

---

## 1. Подготовить Google-аккаунт и проект в Google Cloud

> Всё делаем под тем аккаунтом Google, который будет **владеть таблицей и папкой на Диске** (бот теперь работает от лица этого пользователя через OAuth).

1. В браузере зайди в свой Google-аккаунт (тот, где таблица и Диск).
2. Перейди в **Google Cloud Console**: https://console.cloud.google.com (если не открывается в РФ — нужен VPN).
3. Слева сверху выбери проект → **«Новый проект»**.
4. Назови его, например: `Raspoznavatelb Bot` → **Создать**.
5. Убедись, что выбран **этот проект** (имя видно в шапке).

---

## 2. Включить Drive API и Sheets API

1. В Google Cloud слева открой **APIs & Services → Library** (Библиотека API).
2. Найди `Google Drive API` → открой → **Enable**.
3. Найди `Google Sheets API` → открой → **Enable**.

Оба API должны быть «Enabled».

---

## 3. Настроить OAuth consent screen (экран доступа)

1. Меню: **APIs & Services → OAuth consent screen**.
2. User Type / Тип пользователей: **External (Внешние)** → Continue.
3. Заполни минимум:
   * App name: `Raspoznavatelb Bot` (любое имя)
   * User support email: твой Gmail
   * Developer contact email: твой Gmail
4. Жми **Save and Continue** до шага Test users.
5. На **Test users** добавь свой e-mail Google.
6. Нажми **Save**.

Готово: приложение OAuth доступно только тебе как тестовому пользователю.

---

## 4. Создать OAuth client и скачать `credentials_drive.json`

1. Меню: **APIs & Services → Credentials (Учетные данные)**.
2. Сверху **Create credentials → OAuth client ID**.
3. Application type: **Desktop app**.
4. Название: любое (`Raspoznavatelb Desktop OAuth`).
5. Нажми **Create**, затем **Download JSON**.
6. Скопируй файл в папку проекта `C:\Users\Haier\Desktop\GoogleDOCBOT`.
7. Переименуй его в **`credentials_drive.json`** (или укажи своё имя в `.env`).

Итоговый путь:

```
C:\Users\Haier\Desktop\GoogleDOCBOT\credentials_drive.json
```

---

## 5. Настроить `.env` в проекте

> Переменные должны совпадать с README. Ниже — обязательные для OAuth-режима.

1. Открой/создай файл `.env` в `C:\Users\Haier\Desktop\GoogleDOCBOT`.
2. Добавь строки (подставь свои значения токенов и ID):

```env
# Телеграм-бот
TELEGRAM_BOT_TOKEN=твой_токен_бота

# OpenAI (если используется)
OPENAI_API_KEY=твой_ключ_или_оставь_пустым

# Режим авторизации Google
GOOGLE_AUTH_MODE=oauth

# Файлы OAuth
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=credentials_drive.json
GOOGLE_OAUTH_TOKEN_FILE=token_drive.json

# Google Sheets
GOOGLE_SHEETS_SPREADSHEET_ID=ID_таблицы
GOOGLE_SHEETS_WORKSHEET_TITLE=Имя_листа

# Google Drive
GOOGLE_DRIVE_FOLDER_ID=ID_папки_куда_складывать_файлы
```

Как получить ID:

* **ID таблицы Sheets** — открой таблицу, скопируй кусок между `/d/` и `/edit` в URL.
* **ID папки Drive** — открой папку, скопируй часть после `/folders/` в URL.

Сохрани `.env`.

---

## 6. Один раз пройти OAuth-авторизацию через скрипт

Нужно создать `token_drive.json` с refresh-токеном.

1. Открой **Командную строку** (Win+R → `cmd`).
2. Перейди в папку проекта:

```bat
cd %USERPROFILE%\Desktop\GoogleDOCBOT
```

3. Активируй виртуальное окружение:

```bat
.venv\Scripts\activate
```

4. Запусти скрипт авторизации:

```bat
python scripts\init_drive_oauth.py
```

В браузере выбери **тот же аккаунт**, где таблица и папка. Если видишь «Приложение не проверено» → **Дополнительно → Перейти…** → **Разрешить** доступ к Drive/Sheets. В конце окно можно закрыть.

Если вместо окна разрешения видишь «Ошибка 403: access_denied / приложение не прошло проверку», проверь:

1) На экране **OAuth consent screen** статус приложения должен быть **Testing**.
2) В разделе **Test users** добавлен твой Gmail, под которым проходишь OAuth.
3) Авторизуешься в браузере именно тем аккаунтом, который указан в Test users и который владеет таблицей/папкой.
4) После добавления в Test users заново запусти команду `python scripts\init_drive_oauth.py`.

Проверь, что появился файл:

```
C:\Users\Haier\Desktop\GoogleDOCBOT\token_drive.json
```

---

## 7. Запустить бота

1. (Если терминал закрыт) снова перейди в папку и активируй `.venv`:

```bat
cd %USERPROFILE%\Desktop\GoogleDOCBOT
.venv\Scripts\activate
```

2. На всякий случай поставь зависимости:

```bat
pip install -r requirements.txt
```

3. Стартуй бота:

```bat
python main.py
```

В логах должны быть строки `Start polling` и без ошибок про `storageQuotaExceeded` — теперь квота берётся из твоего аккаунта.

---

## 8. Проверка работы

1. В Telegram отправь `/start`, затем PDF/JPG/PNG с актом.
2. Ожидаемое:
   * в логах: `Received document...`, `OCR completed...`, `Parsed document...`, `Reserved order_number=...`
   * в Google Sheets появится строка на нужном листе
   * в папке Google Drive появится загруженный файл

Если видишь ошибки или `permission denied`, пришли логи — проверим права и токены.

---

## Чем новый способ отличается от старого сервисного аккаунта

* Авторизация идёт через браузерный OAuth (`GOOGLE_AUTH_MODE=oauth` по умолчанию), а не через сервисный аккаунт.
* Drive и Sheets используют один пользовательский токен (`token_drive.json`), поэтому файлы и строки записываются **от имени твоего аккаунта**, и расходуется твоя квота Google Drive.
* Старый режим `service_account` больше не поддерживается — если указать его в `.env`, бот упадёт с ошибкой конфигурации.
