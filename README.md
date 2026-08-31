# Feedback Bot — Telegram moderation + GitHub Pages Mini App + Shieldstral

Финальная сборка без отдельной WebPanel.

В этой версии исправлена критическая ошибка SQLite:

`RuntimeError: threads can only be started once`

Она возникала из-за двойного ожидания одного и того же `aiosqlite.Connection`. База теперь открывается через асинхронный context manager и корректно закрывается после каждого запроса.

## 1. Что делает программа

### Telegram

- обычный чат пользователя с ботом;
- раздел «Вопросы» для обычных обращений;
- апелляция появляется только при активном бане;
- статистика пользователя;
- правила;
- админские команды;
- поиск пользователя по `@username` или ID;
- предупреждения;
- бан/разбан;
- мут/снятие мута;
- удаление сообщений;
- антиспам;
- фильтр запрещённых слов;
- аудит действий;
- бот может быть модератором подключённых групп.

### Mini App

`app/` — статический Telegram Mini App.

Он специально отделён от Python backend:

- GitHub Pages публикует только HTML/CSS/JS;
- токен Telegram туда не попадает;
- SQLite туда не попадает;
- Python-бот продолжает работать на Windows или Raspberry Pi 5;
- Telegram открывает Mini App по HTTPS URL GitHub Pages.

### Shieldstral

При `AI_ENABLED=true` стартовый скрипт пытается запустить локальный `llama.cpp` сервер с:

`Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M`

Именно такой способ запуска указан на странице модели: `llama serve -hf ...:Q4_K_M`. Модель содержит Q4_K_M GGUF около 2.15 GB и предназначена для локальной text/image moderation. urlСтраница Shieldstral-1.0-3B-GGUF на Hugging Facehttps://huggingface.co/Metabaron6/Shieldstral-1.0-3B-GGUF

Бот обращается к локальному OpenAI-compatible API:

`http://127.0.0.1:9931/v1`

и проверяет текстовые сообщения в группах.

Shieldstral — именно классификатор безопасности, а не обычная чат-модель. Его официальный интерфейс использует `<Instruction>`, `<Query>`, `<Document>` и бинарный ответ `yes/no`; поэтому в этой сборке ему передаётся отдельная политика модерации. urlОфициальная документация Shieldstralhttps://huggingface.co/mistralai/Shieldstral-1.0-3B

По умолчанию AI-действие — `warn`, чтобы модель не могла сразу забанить пользователя из-за единичной ошибки классификации.

---

# 2. Windows — запуск

Откройте CMD или PowerShell в папке проекта.

```powershell
cd C:\Users\ВАШ_ПОЛЬЗОВАТЕЛЬ\Documents\feedback_bot
.\start.bat
```

При первом запуске программа:

1. создаст `venv`;
2. установит зависимости;
3. создаст `.env` из `.env.example`;
4. при включённом AI проверит `llama` / `llama-server`;
5. при наличии WinGet попробует установить `llama.cpp`;
6. запустит Shieldstral на порту `9931`;
7. запустит Telegram-бота.

При первом запуске Shieldstral llama.cpp скачает примерно 2.15 GB Q4_K_M. Это нормально.

После первого запуска модель будет использоваться из локального кеша.

## Windows tray

Для запуска с иконкой в системном трее:

```powershell
.\venv\Scripts\python.exe tray.py
```

Tray запускает именно `start.bat`, поэтому Shieldstral тоже запускается автоматически.

---

# 3. Raspberry Pi 5 / Linux Trixie

Скопируйте проект, например:

```bash
/home/jdh-admin/feedback_bot
```

Запуск:

```bash
cd ~/feedback_bot
chmod +x start.sh
./start.sh
```

При первом запуске `start.sh`:

1. создаст Python virtualenv;
2. установит зависимости;
3. создаст `.env`, если его ещё нет;
4. проверит `llama` / `llama-server`;
5. если возможно, установит официальный llama CLI;
6. запустит Shieldstral на `127.0.0.1:9931`;
7. запустит Telegram-бота.

Логи Shieldstral сохраняются в:

```text
shieldstral.log
```

---

# 4. Автозапуск Raspberry Pi

В архиве есть:

```text
feedback-bot.service
```

Он рассчитан на пользователя `jdh-admin` и каталог:

```text
/home/jdh-admin/feedback_bot
```

Установите:

```bash
sudo cp feedback-bot.service /etc/systemd/system/feedback-bot.service
sudo systemctl daemon-reload
sudo systemctl enable feedback-bot
sudo systemctl start feedback-bot
```

Проверка:

```bash
systemctl status feedback-bot
```

Логи:

```bash
journalctl -u feedback-bot -f
```

Если каталог или имя Linux-пользователя другие — измените `User`, `WorkingDirectory` и `ExecStart` в service-файле.

---

# 5. .env

Минимально нужно указать:

```env
BOT_TOKEN=ТОКЕН_ОТ_BOTFATHER
ADMIN_USERNAMES=ваш_username
```

Для нескольких администраторов:

```env
ADMIN_USERNAMES=first_admin,second_admin
```

Можно использовать и ID:

```env
ADMIN_IDS=123456789,987654321
```

Для групп:

```env
MODERATION_CHAT_IDS=-1001234567890,-1009876543210
```

Важно: бот должен быть добавлен в группу и иметь права администратора, необходимые для удаления сообщений, ограничения и блокировки участников.

---

# 6. Настройки Shieldstral

В `.env`:

```env
AI_ENABLED=true
AI_BASE_URL=http://127.0.0.1:9931/v1
AI_API_KEY=local
AI_MODEL=
AI_THRESHOLD=0.70
AI_ACTION=warn
AI_MUTE_MINUTES=30
AI_CONCURRENCY=1
AI_MAX_CHARS=2500
AI_TIMEOUT_SECONDS=30

SHIELDSTRAL_HF=Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M
SHIELDSTRAL_PORT=9931
```

`AI_MODEL=` можно оставить пустым: бот сам запросит `/v1/models` у llama.cpp и возьмёт доступную модель.

### AI_ACTION

`log` — только записывать результат в аудит.

`delete` — удалять отмеченное сообщение.

`warn` — удалять + выдавать предупреждение. Если достигнут `MAX_WARNINGS`, пользователь блокируется.

`mute` — удалять + выдавать мут.

`ban` — удалять + блокировать.

Для первого тестирования рекомендуется:

```env
AI_ACTION=log
```

После проверки качества:

```env
AI_ACTION=warn
```

---

# 7. Что именно проверяет Shieldstral

Бот не просит модель оценивать всё подряд как «плохое».

Политика ориентирована на серьёзные нарушения:

- реальные угрозы насилия;
- тяжёлое целевое преследование;
- атаки на защищённые группы;
- сексуальная эксплуатация;
- сексуальный контент с несовершеннолетними;
- явное содействие серьёзному преступлению;
- инструкции, предназначенные для причинения реального вреда.

Обычная ругань, шутки, споры, критика и политические мнения специально исключены из AI-политики, чтобы не создавать лишние false positive.

Обычный фильтр `bad_words.txt` продолжает работать независимо от Shieldstral.

---

# 8. GitHub Pages — пошагово

## Шаг 1. Создайте репозиторий

На GitHub нажмите:

`New repository`

Например:

```text
feedback-bot
```

Репозиторий можно сделать публичным. Если вы используете GitHub Free, это самый простой вариант для Pages.

Не загружайте `.env`, `venv`, `data/bot.db` и токен.

В архиве `.gitignore` уже подготовлен.

## Шаг 2. Загрузите проект

В репозитории должна быть примерно такая структура:

```text
feedback-bot/
├── .github/
│   └── workflows/
│       └── pages.yml
├── app/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── bot.py
├── requirements.txt
├── start.bat
├── start.sh
├── tray.py
├── feedback-bot.service
├── .env.example
├── .gitignore
└── README.md
```

Самое важное для Pages:

```text
app/index.html
.github/workflows/pages.yml
```

## Шаг 3. Включите GitHub Pages

Откройте репозиторий:

`Settings` → `Pages`

В разделе `Build and deployment` найдите `Source`.

Выберите:

```text
GitHub Actions
```

Именно этот вариант нужен для workflow из архива. GitHub официально поддерживает публикацию Pages через Actions; workflow должен иметь `pages: write` и `id-token: write`, загружать Pages artifact и выполнять `deploy-pages`. citeturn2search0turn2search1

## Шаг 4. Сделайте первый push

После загрузки файлов откройте:

`Actions`

Там появится:

```text
Deploy Mini App to GitHub Pages
```

Откройте workflow.

Если всё зелёное — Pages опубликован.

GitHub Actions запускает workflow после push в `main`, а опубликованный адрес обычно имеет вид:

```text
https://ВАШ_USERNAME.github.io/ИМЯ_REPOSITORY/
```

GitHub описывает именно такую схему URL для repository Pages. citeturn2search2

## Шаг 5. Найдите настоящий URL

В GitHub откройте:

`Settings` → `Pages`

Там появится ссылка `Visit site`.

Скопируйте именно её.

Например:

```text
https://jdh-admin.github.io/feedback-bot/
```

Не добавляйте `/app`.

Workflow уже публикует содержимое папки `app` как корень сайта.

## Шаг 6. Вставьте URL в .env

На Windows/Raspberry Pi, где работает Python-бот, откройте `.env`:

```env
WEBAPP_URL=https://jdh-admin.github.io/feedback-bot/
```

Имя GitHub пользователя и репозитория замените на свои.

После этого перезапустите бота.

В логах должно быть:

```text
Mini App: https://...
```

После этого `/start` начнёт показывать кнопку Mini App.

---

# 9. Почему GitHub Pages не запускает самого бота

Это важно.

GitHub Pages — статический хостинг.

Он публикует:

```text
HTML
CSS
JavaScript
```

Но не запускает ваш Python-процесс и не хранит вашу SQLite-базу как backend.

Поэтому архитектура такая:

```text
Telegram
   │
   ├── Bot API ────────────────► Windows / Raspberry Pi
   │                              │
   │                              ├── bot.py
   │                              ├── SQLite
   │                              └── Shieldstral
   │
   └── Mini App HTTPS ─────────► GitHub Pages
                                  │
                                  └── HTML/CSS/JS
```

Это специально сделано так, чтобы Mini App работал с телефона и компьютера, а тяжёлая логика оставалась на твоём компьютере/Raspberry Pi.

GitHub прямо предупреждает, что Pages-сайт публично доступен в интернете, поэтому секреты и токены в репозиторий помещать нельзя. citeturn2search0

---

# 10. Важное ограничение текущего Mini App

GitHub Pages не имеет доступа к локальному:

```text
127.0.0.1:9931
```

и не имеет доступа к:

```text
SQLite
bot.py
```

Поэтому Mini App сейчас является статическим интерфейсом, а действия передаются обратно через Telegram WebApp API.

Если позже понадобится полноценная интерактивная Mini App с загрузкой пользователей, аудита, банов, настроек и ответов непосредственно внутри страницы, следующим этапом нужен публичный HTTPS API backend. Его можно будет разместить на Raspberry Pi через Cloudflare Tunnel, VPS или другой HTTPS reverse proxy.

Для первого теста это специально не требуется.

---

# 11. Проверка Telegram

После запуска:

```text
/start
```

Проверь:

1. Открывается меню.
2. Нажимается Mini App.
3. `Вопросы` работает как обычный чат.
4. При активном бане появляется `Подать апелляцию`.
5. Без бана апелляция не создаётся.
6. `/stats` показывает статистику.
7. `/user @username` работает у администратора.
8. `/audit` показывает действия.

---

# 12. Проверка Shieldstral

Сначала лучше поставить:

```env
AI_ACTION=log
```

Запустить бота и отправить в подключённую группу несколько обычных сообщений.

В логах должно появиться:

```text
Shieldstral: ready
```

Если модель ещё скачивается, первый запуск может занять значительное время.

Проверить локальный API можно:

Windows PowerShell:

```powershell
curl.exe http://127.0.0.1:9931/v1/models
```

Linux:

```bash
curl http://127.0.0.1:9931/v1/models
```

Если API отвечает JSON со списком моделей — llama.cpp работает.

После этого можно поставить:

```env
AI_ACTION=warn
```

и перезапустить бота.

---

# 13. Если Shieldstral не запускается

Проверь:

```text
shieldstral.log
```

или Windows окно `Shieldstral`.

Также вручную:

```text
llama serve -hf Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M --port 9931
```

После запуска:

```text
http://127.0.0.1:9931/v1/models
```

должен отвечать.

На Raspberry Pi 5 обязательно учитывать, что Q4_K_M — это около 2.15 GB только веса модели; дополнительная память нужна для самого inference. Автор GGUF указывает Q4_K_M как рекомендуемый баланс размера и качества. citeturn1search2

---

# 14. Безопасность

Никогда не коммитьте:

```text
.env
```

и особенно:

```text
BOT_TOKEN=...
```

Токен должен находиться только на Windows/Raspberry Pi.

GitHub Pages получает только статические файлы из `app/`.

---

# 15. Версия

`1.1.0-shieldstral`

Главные изменения относительно предыдущей сборки:

- исправлен `aiosqlite RuntimeError: threads can only be started once`;
- добавлен локальный Shieldstral через llama.cpp;
- Shieldstral автоматически запускается из `start.bat` / `start.sh`;
- добавлена автоматическая попытка установки llama.cpp;
- добавлена проверка `/v1/models`;
- добавлена AI-модерация сообщений в группах;
- AI-модерация работает асинхронно и не блокирует получение Telegram updates;
- добавлен безопасный режим `AI_ACTION=log`;
- исправлен запуск через Windows tray;
- systemd теперь запускает `start.sh`, чтобы локальный AI тоже поднимался;
- GitHub Pages остаётся полностью отделённым от локального Python backend.
