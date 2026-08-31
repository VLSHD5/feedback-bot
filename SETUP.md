# Feedback Bot — установка и запуск

## 1. Что где работает

- `bot.py` — Telegram-бот, база, вопросы, апелляции, модерация и аудит.
- `app/` — Telegram Mini App.
- GitHub Pages — только интерфейс Mini App. Токен бота туда не помещается.
- Windows: `start.bat`.
- Raspberry Pi 5 / Linux Trixie: `start.sh`.
- Shieldstral запускается локально через llama.cpp и слушает `127.0.0.1:9931`.

## 2. GitHub Pages

Репозиторий уже содержит `.github/workflows/pages.yml`.
После push в `main` workflow собирает папку `app/` и публикует её как GitHub Pages.

### Вариант A — домен feedback-bot.com

В DNS домена оставьте/создайте:

1. TXT для проверки домена:
   - Host/Name: `_github-pages-challenge-VLSHD5`
   - Value: `f18994feb2c9ae41a6028388e1fb44`

2. Для корневого домена `feedback-bot.com` GitHub Pages обычно использует A-записи GitHub Pages. Если DNS-провайдер предлагает ALIAS/ANAME на Pages, это тоже подходит.

3. В GitHub откройте Settings → Pages и выберите источник `GitHub Actions`.

4. В Custom domain укажите `feedback-bot.com`.

5. Дождитесь `DNS check successful` и включите `Enforce HTTPS`, когда GitHub разрешит это.

В репозитории уже лежит `CNAME` со значением `feedback-bot.com`.

### Вариант B — без собственного домена

После первого успешного Pages deployment GitHub выдаст адрес вида:
`https://VLSHD5.github.io/feedback-bot/`

Тогда замените `WEBAPP_URL` в `.env` на этот HTTPS-адрес и перезапустите бота.

## 3. BotFather

Mini App нельзя открывать в Telegram с обычного `http://127.0.0.1` или `http://192.168.x.x`. Для Web App нужен HTTPS.

В этом проекте отдельный WebPanel не используется. Меню бота автоматически получает кнопку Mini App из `WEBAPP_URL` при старте.

После изменения `WEBAPP_URL` просто перезапустите бота.

## 4. Конфигурация .env

Скопируйте `.env.example` в `.env`.

Минимально обязательно заполнить:

`BOT_TOKEN=токен_из_BotFather`

И указать Telegram username владельца:

`ADMIN_USERNAMES=ваш_telegram_username`

Username указывается без `@`.

Можно также использовать числовой Telegram ID:

`ADMIN_IDS=123456789`

Не добавляйте BotFather token в GitHub.

## 5. Windows

Откройте PowerShell/CMD в папке проекта:

`start.bat`

Скрипт создаёт `venv`, ставит зависимости и запускает бота. При включённом `AI_ENABLED=true` он пытается запустить llama.cpp и Shieldstral на порту 9931.

Если Python 3.14 используется, это нормально для текущего набора зависимостей. Если сторонний пакет начнёт требовать более старый Python, используйте Python 3.12/3.13.

## 6. Raspberry Pi 5 / Raspberry Pi OS Trixie

Подготовка:

`sudo apt update`

`sudo apt install -y python3 python3-venv python3-pip curl git`

Клонирование:

`git clone https://github.com/VLSHD5/feedback-bot.git`

`cd feedback-bot`

Запуск:

`chmod +x start.sh`

`./start.sh`

Для постоянной работы используйте `feedback-bot.service` и systemd. Сервис должен запускаться из каталога проекта и иметь доступ к `.env`.

## 7. Shieldstral

Стартовые скрипты используют:

`Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M`

и локальный OpenAI-compatible endpoint:

`http://127.0.0.1:9931/v1`

Бот автоматически запрашивает `/models`, выбирает Shieldstral, если он доступен, и отправляет сообщения групп в `/chat/completions`.

Для групповой модерации бот должен быть администратором с правами удаления сообщений, ограничения участников и блокировки участников.

Подключение группы:

1. Добавьте бота в группу.
2. Дайте ему права администратора.
3. От имени владельца/админа выполните `/connect`.
4. После этого включается антиспам, фильтр слов и Shieldstral.

## 8. Исправленная база данных

Старый `RuntimeError: threads can only be started once` был связан с неправильным жизненным циклом подключения `aiosqlite`.

В новой версии соединение создаётся как обычный объект `aiosqlite.connect()` и закрывается один раз в каждом блоке работы с БД.

## 9. Пользовательская логика

- Обычное сообщение боту → раздел вопросов/обычный чат с администрацией.
- Если есть активный бан → появляется возможность апелляции.
- Апелляция содержит текст пользователя и причину бана в сообщении администратору.
- Ответ админа на сообщение обращения возвращается пользователю.
- Админские данные не показываются обычным пользователям.

## 10. Команды администратора

`/admin`

`/appeals`

`/user @username` или `/user 123456789`

`/audit`

`/members`

`/settings`

`/connect`

`/disconnect`

`/warn`

`/mute`

`/unmute`

`/ban`

`/unban`

`/del` — ответом на сообщение

`/setrules текст`

## 11. Важное ограничение Mini App

GitHub Pages — статический HTTPS-интерфейс. Он не должен напрямую подключаться к SQLite или к `127.0.0.1:9931` на Raspberry Pi.

Правильная схема:

Telegram → Mini App (GitHub Pages HTTPS) → Telegram Bot API / web_app_data → бот на Raspberry Pi/Windows.

Поэтому локальная модель и база остаются на вашем компьютере/Raspberry Pi, а GitHub Pages хранит только фронтенд.
