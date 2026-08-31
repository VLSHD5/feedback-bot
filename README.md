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

# 2. Windows:
```powershell
cd C:\Users\ВАШ_ПОЛЬЗОВАТЕЛЬ\Documents\feedback_bot
.\start.bat
```
## Windows tray:

```powershell
.\venv\Scripts\python.exe tray.py
```

Tray запускает именно `start.bat`, поэтому Shieldstral тоже запускается автоматически.

---

# 3. Linux:
```bash
cd ~/feedback_bot
chmod +x start.sh
./start.sh
```
Логи Shieldstral сохраняются в:
```text
shieldstral.log
```

---
# 4. Автозапуск:

В архиве есть:

```text
feedback-bot.service
```
Установите:

```bash
sudo cp feedback-bot.service /etc/systemd/system/feedback-bot.service
sudo systemctl daemon-reload
sudo systemctl enable feedback-bot
sudo systemctl start feedback-bot
```
Проверить текущее состояние:

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

- исправлен запуск через Windows tray;
- systemd теперь запускает `start.sh`, чтобы локальный AI тоже поднимался;
- GitHub Pages остаётся полностью отделённым от локального Python backend.
