import asyncio
import contextlib
import logging
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import aiosqlite
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ChatPermissions

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')

TOKEN = os.getenv('BOT_TOKEN', '').strip()
if not TOKEN:
    raise RuntimeError('BOT_TOKEN is empty. Put your BotFather token into .env')

ADMIN_USERNAMES = {x.strip().lstrip('@').lower() for x in os.getenv('ADMIN_USERNAMES', '').split(',') if x.strip()}
ADMIN_IDS = {int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()}
MOD_IDS = {int(x) for x in os.getenv('MODERATION_CHAT_IDS', '').split(',') if x.strip().lstrip('-').isdigit()}
DB = BASE / os.getenv('DB_PATH', 'data/bot.db')
BAD = BASE / os.getenv('BAD_WORDS_PATH', 'data/bad_words.txt')
WEB = os.getenv('WEBAPP_URL', '').strip()
MAX_WARN = int(os.getenv('MAX_WARNINGS', '3'))
SPAM_N = int(os.getenv('ANTISPAM_MESSAGES', '5'))
SPAM_W = int(os.getenv('ANTISPAM_WINDOW', '10'))
PROF_MUTE = int(os.getenv('PROFANITY_MUTE_MINUTES', '30'))

AI_ENABLED = os.getenv('AI_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'on'}
AI_BASE_URL = os.getenv('AI_BASE_URL', 'http://127.0.0.1:9931/v1').rstrip('/')
AI_API_KEY = os.getenv('AI_API_KEY', 'local').strip() or 'local'
AI_MODEL = os.getenv('AI_MODEL', '').strip()
AI_THRESHOLD = float(os.getenv('AI_THRESHOLD', '0.70'))
AI_ACTION = os.getenv('AI_ACTION', 'warn').lower().strip()  # off, log, delete, warn, mute, ban
AI_MUTE_MINUTES = int(os.getenv('AI_MUTE_MINUTES', '30'))
AI_CONCURRENCY = max(1, int(os.getenv('AI_CONCURRENCY', '1')))
AI_MAX_CHARS = max(100, int(os.getenv('AI_MAX_CHARS', '2500')))
AI_TIMEOUT = float(os.getenv('AI_TIMEOUT_SECONDS', '30'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger('feedback')
dp = Dispatcher()
windows = defaultdict(deque)
ai_sem = asyncio.Semaphore(AI_CONCURRENCY)
ai_session: aiohttp.ClientSession | None = None


def admin(u):
    return bool(u) and (u.id in ADMIN_IDS or (u.username or '').lower() in ADMIN_USERNAMES)


def wb(text):
    if WEB.startswith(('https://', 'http://')):
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=WEB))
    return None


def kb(u, banned=False):
    rows = []
    w = wb('Открыть Mini App')
    if w:
        rows.append([w])
    rows += [
        [InlineKeyboardButton(text='Вопросы', callback_data='questions'), InlineKeyboardButton(text='Моя статистика', callback_data='stats')],
        [InlineKeyboardButton(text='Правила', callback_data='rules')],
    ]
    if banned:
        rows.append([InlineKeyboardButton(text='Подать апелляцию', callback_data='appeal')])
    if admin(u):
        if w:
            rows.append([InlineKeyboardButton(text='Админ-панель', web_app=WebAppInfo(url=WEB))])
        else:
            rows.append([InlineKeyboardButton(text='Админ-панель', callback_data='admin_soon')])
        rows.append([InlineKeyboardButton(text='Апелляции', callback_data='admin_appeals')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@asynccontextmanager
async def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = await aiosqlite.connect(DB)
    c.row_factory = aiosqlite.Row
    try:
        yield c
    finally:
        await c.close()


async def init():
    async with conn() as c:
        await c.executescript('''
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_at TEXT,
            messages INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS bans(
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            banned_by INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS appeals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            admin_id INTEGER,
            answer TEXT
        );
        CREATE TABLE IF NOT EXISTS questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            answer TEXT
        );
        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            actor_id INTEGER,
            action TEXT,
            target_id INTEGER,
            details TEXT,
            created_at TEXT
        );
        ''')
        await c.commit()


async def user(u):
    async with conn() as c:
        await c.execute(
            '''INSERT INTO users(id,username,first_name,last_name,joined_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username,
               first_name=excluded.first_name,last_name=excluded.last_name''',
            (u.id, u.username, u.first_name, u.last_name, datetime.now(timezone.utc).isoformat()),
        )
        await c.commit()


async def ban(uid):
    async with conn() as c:
        cur = await c.execute('SELECT * FROM bans WHERE user_id=?', (uid,))
        return await cur.fetchone()


async def warn(uid):
    async with conn() as c:
        await c.execute('UPDATE users SET warnings=warnings+1 WHERE id=?', (uid,))
        cur = await c.execute('SELECT warnings FROM users WHERE id=?', (uid,))
        r = await cur.fetchone()
        await c.commit()
        return r['warnings'] if r else 0


async def audit(chat, actor, action, target=None, details=''):
    async with conn() as c:
        await c.execute(
            'INSERT INTO audit(chat_id,actor_id,action,target_id,details,created_at) VALUES(?,?,?,?,?,?)',
            (chat, actor, action, target, details, datetime.now(timezone.utc).isoformat()),
        )
        await c.commit()


def bad(text):
    if not BAD.exists():
        return False
    low = text.lower()
    return any(x.strip().lower() in low for x in BAD.read_text('utf-8').splitlines() if x.strip() and not x.startswith('#'))


async def user_id(v):
    v = v.strip().lstrip('@')
    if v.lstrip('-').isdigit():
        return int(v)
    async with conn() as c:
        cur = await c.execute('SELECT id FROM users WHERE lower(username)=?', (v.lower(),))
        r = await cur.fetchone()
        return r['id'] if r else None


async def set_ban(uid, reason, actor_id):
    async with conn() as c:
        await c.execute(
            'INSERT OR REPLACE INTO bans(user_id,reason,banned_by,created_at) VALUES(?,?,?,?)',
            (uid, reason, actor_id, datetime.now(timezone.utc).isoformat()),
        )
        await c.commit()


async def apply_mute(bot: Bot, chat_id: int, uid: int, minutes: int, actor: int, reason: str):
    try:
        await bot.restrict_chat_member(
            chat_id,
            uid,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        )
        await audit(chat_id, actor, 'mute', uid, f'{minutes}m | {reason}')
        return True
    except Exception as e:
        log.warning('mute: %s', e)
        return False


async def apply_ban(bot: Bot, chat_id: int, uid: int, reason: str, actor: int):
    await set_ban(uid, reason, actor)
    try:
        await bot.ban_chat_member(chat_id, uid)
    except Exception as e:
        log.warning('ban: %s', e)
    await audit(chat_id, actor, 'ban', uid, reason)


async def ai_models():
    global AI_MODEL
    if not AI_ENABLED:
        return False
    headers = {'Authorization': f'Bearer {AI_API_KEY}'}
    try:
        async with ai_session.get(f'{AI_BASE_URL}/models', headers=headers) as r:
            if r.status != 200:
                return False
            data = await r.json()
            models = data.get('data') or []
            if not AI_MODEL and models:
                AI_MODEL = models[0].get('id', '')
            return bool(AI_MODEL)
    except Exception as e:
        log.warning('AI model discovery failed: %s', e)
        return False


AI_SYSTEM = (
    'Judge whether the Document meets the requirements based on the Query and the Instruction provided. '
    'Note that the answer can only be "yes" or "no".'
)
AI_INSTRUCTION = (
    'You are the safety moderator of a Telegram group. Mark as unsafe ONLY when the message clearly contains '
    'a serious moderation violation: credible threats of violence, targeted severe harassment, hateful attacks '
    'against a protected group, sexual exploitation or sexual content involving minors, explicit promotion of '
    'serious criminal wrongdoing, or clearly malicious instructions intended to cause real-world harm. '
    'Do not mark ordinary disagreement, jokes, harmless profanity, political opinions, criticism, or benign discussion as unsafe.'
)
AI_QUERY = 'Does this Telegram message violate the safety moderation policy above?'


async def ai_check(text: str):
    if not AI_ENABLED or not text.strip() or len(text) > AI_MAX_CHARS:
        return False, 0.0, 'disabled-or-skipped'
    async with ai_sem:
        if not AI_MODEL and not await ai_models():
            return False, 0.0, 'model-unavailable'
        payload = {
            'model': AI_MODEL,
            'messages': [
                {'role': 'system', 'content': AI_SYSTEM},
                {'role': 'user', 'content': f'<Instruction>\n{AI_INSTRUCTION}\n</Instruction>\n\n<Query>\n{AI_QUERY}\n</Query>\n\n<Document>\n{text}\n</Document>\n\nAnswer strictly "yes" or "no":'},
            ],
            'temperature': 0,
            'max_tokens': 5,
            'stream': False,
        }
        headers = {'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'}
        try:
            async with ai_session.post(f'{AI_BASE_URL}/chat/completions', json=payload, headers=headers, timeout=AI_TIMEOUT) as r:
                body = await r.text()
                if r.status != 200:
                    log.warning('AI HTTP %s: %s', r.status, body[:500])
                    return False, 0.0, f'http-{r.status}'
                data = __import__('json').loads(body)
                answer = ((data.get('choices') or [{}])[0].get('message') or {}).get('content', '')
                first = re.search(r'\b(yes|no)\b', answer.lower())
                if not first:
                    return False, 0.0, 'unparsed'
                flagged = first.group(1) == 'yes'
                # The GGUF endpoint may not expose the calibrated yes/no probability.
                # Keep the explicit result separate from the configured confidence threshold.
                return flagged, (1.0 if flagged else 0.0), answer.strip()
        except Exception as e:
            log.warning('AI check failed: %s', e)
            return False, 0.0, 'exception'


async def ai_moderate_message(m: Message):
    if not AI_ENABLED or admin(m.from_user):
        return
    text = (m.text or m.caption or '').strip()
    if not text or text.startswith('/'):
        return
    flagged, score, raw = await ai_check(text)
    if not flagged:
        return
    reason = f'Shieldstral yes; score={score:.2f}; {raw[:300]}'
    log.warning('AI flagged chat=%s user=%s: %s', m.chat.id, m.from_user.id, reason)
    await audit(m.chat.id, m.from_user.id, 'ai_flag', m.from_user.id, reason)
    if AI_ACTION == 'off' or AI_ACTION == 'log':
        return
    if AI_ACTION in {'delete', 'warn', 'mute', 'ban'}:
        with contextlib.suppress(Exception):
            await m.delete()
    if AI_ACTION == 'warn':
        n = await warn(m.from_user.id)
        await audit(m.chat.id, 0, 'ai_warn', m.from_user.id, f'{n}/{MAX_WARNINGS}')
        if n >= MAX_WARN:
            await apply_ban(m.bot, m.chat.id, m.from_user.id, 'Автоматическая модерация Shieldstral: превышен лимит предупреждений.', 0)
    elif AI_ACTION == 'mute':
        await apply_mute(m.bot, m.chat.id, m.from_user.id, AI_MUTE_MINUTES, 0, 'Shieldstral')
    elif AI_ACTION == 'ban':
        await apply_ban(m.bot, m.chat.id, m.from_user.id, 'Автоматическая модерация Shieldstral.', 0)


@dp.message(CommandStart())
async def start(m):
    await user(m.from_user)
    b = await ban(m.from_user.id)
    t = 'Добро пожаловать. Выберите нужный раздел.'
    if b:
        t += f"\n\nУ вас активен бан.\nПричина: {b['reason'] or 'не указана'}"
    await m.answer(t, reply_markup=kb(m.from_user, bool(b)))


@dp.message(Command('help'))
async def help_(m):
    t = '/start — меню\n/help — помощь\n/rules — правила\n/stats — моя статистика\n/id — мой ID'
    if admin(m.from_user):
        t += '\n\n/admin\n/user <username|id>\n/appeals\n/audit\n/settings\n/warn\n/mute\n/unmute\n/ban\n/unban\n/del'
    await m.answer(t)


@dp.message(Command('id'))
async def id_(m):
    await m.answer(f'Ваш ID: {m.from_user.id}')


@dp.message(Command('rules'))
async def rules(m):
    await m.answer('Правила чата пока не настроены.')


@dp.message(Command('stats'))
async def stats(m):
    await user(m.from_user)
    async with conn() as c:
        cur = await c.execute('SELECT * FROM users WHERE id=?', (m.from_user.id,))
        u = await cur.fetchone()
        cur = await c.execute('SELECT COUNT(*) n FROM appeals WHERE user_id=?', (m.from_user.id,))
        a = await cur.fetchone()
    await m.answer(
        f"Статистика\nID: {m.from_user.id}\nСообщений: {u['messages']}\nПредупреждений: {u['warnings']}\n"
        f"Активный бан: {'да' if await ban(m.from_user.id) else 'нет'}\nАпелляций: {a['n']}"
    )


@dp.message(Command('admin'))
async def admin_(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    w = wb('Открыть админ-панель')
    if w:
        return await m.answer('Административное управление.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[w]]))
    await m.answer('Mini App пока не подключён: WEBAPP_URL пуст.')


@dp.message(Command('user'))
async def user_cmd(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    p = (m.text or '').split(maxsplit=1)
    if len(p) < 2:
        return await m.answer('Использование: /user <username|id>')
    uid = await user_id(p[1])
    if not uid:
        return await m.answer('Пользователь не найден.')
    async with conn() as c:
        cur = await c.execute('SELECT * FROM users WHERE id=?', (uid,))
        u = await cur.fetchone()
        cur = await c.execute('SELECT COUNT(*) n FROM appeals WHERE user_id=?', (uid,))
        a = await cur.fetchone()
    b = await ban(uid)
    await m.answer(
        f"ID: {uid}\nUsername: @{u['username'] or '—'}\nСообщений: {u['messages']}\n"
        f"Предупреждений: {u['warnings']}\nБан: {'да' if b else 'нет'}\n"
        f"Причина: {b['reason'] if b else '—'}\nАпелляций: {a['n']}"
    )


@dp.callback_query(F.data == 'stats')
async def cb_stats(q):
    await q.answer()
    await stats(q.message)


@dp.callback_query(F.data == 'rules')
async def cb_rules(q):
    await q.answer()
    await rules(q.message)


@dp.callback_query(F.data == 'questions')
async def cb_q(q):
    await q.answer()
    await q.message.answer('Напишите вопрос следующим сообщением. Это обычный чат с ботом.')


@dp.callback_query(F.data == 'appeal')
async def cb_a(q):
    await q.answer()
    b = await ban(q.from_user.id)
    await q.message.answer('Активный бан найден. Напишите текст апелляции следующим сообщением.' if b else 'Активного бана нет. Раздел недоступен.')


@dp.callback_query(F.data == 'admin_soon')
async def admin_soon(q):
    await q.answer('Сначала настройте WEBAPP_URL.', show_alert=True)


@dp.callback_query(F.data == 'admin_appeals')
async def cb_aa(q):
    if not admin(q.from_user):
        return await q.answer('Доступ запрещён', show_alert=True)
    await q.answer()
    async with conn() as c:
        cur = await c.execute("SELECT * FROM appeals WHERE status='pending' ORDER BY id DESC LIMIT 10")
        r = await cur.fetchall()
    await q.message.answer('Новых апелляций нет.' if not r else '\n\n'.join(f"#{x['id']} | user {x['user_id']}\n{x['text'][:700]}" for x in r))


@dp.message(Command('appeals'))
async def appeals(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    async with conn() as c:
        cur = await c.execute("SELECT * FROM appeals WHERE status='pending' ORDER BY id DESC LIMIT 10")
        r = await cur.fetchall()
    await m.answer('Новых апелляций нет.' if not r else '\n\n'.join(f"#{x['id']} | user {x['user_id']}\n{x['text'][:700]}" for x in r))


@dp.message(Command('audit'))
async def audit_cmd(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    async with conn() as c:
        cur = await c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 20')
        r = await cur.fetchall()
    await m.answer('Аудит пуст.' if not r else '\n'.join(f"{x['created_at'][:19]} | {x['action']} | {x['actor_id']} -> {x['target_id'] or '-'} | {x['details'][:80]}" for x in r))


@dp.message(Command('settings'))
async def settings(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    await m.answer(
        f'Mini App: {"включён" if WEB else "ожидает WEBAPP_URL"}\n'
        f'Антиспам: {SPAM_N}/{SPAM_W}s\nМут за запрещённые слова: {PROF_MUTE} мин.\n'
        f'Shieldstral: {"включён" if AI_ENABLED else "выключен"}\nAI action: {AI_ACTION}'
    )


async def target(m):
    if m.reply_to_message:
        return m.reply_to_message.from_user.id
    p = (m.text or '').split()
    return await user_id(p[1]) if len(p) > 1 else None


@dp.message(Command('warn'))
async def warn_(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    uid = await target(m)
    if not uid:
        return await m.answer('Укажите пользователя или ответьте на его сообщение.')
    n = await warn(uid)
    await audit(m.chat.id, m.from_user.id, 'warn', uid, '')
    await m.answer(f'Предупреждение: {n}/{MAX_WARN}')


@dp.message(Command('ban'))
async def ban_(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    uid = await target(m)
    if not uid:
        return await m.answer('Укажите пользователя или ответьте на его сообщение.')
    reason = ' '.join((m.text or '').split()[2:]) or 'Без причины'
    chats = MOD_IDS or ({m.chat.id} if m.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) else set())
    for chat in chats:
        await apply_ban(m.bot, chat, uid, reason, m.from_user.id)
    await m.answer(f'Пользователь {uid} заблокирован. Причина: {reason}')


@dp.message(Command('unban'))
async def unban(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    uid = await target(m)
    if not uid:
        return await m.answer('Укажите пользователя или ответьте на сообщение.')
    async with conn() as c:
        await c.execute('DELETE FROM bans WHERE user_id=?', (uid,))
        await c.commit()
    for chat in MOD_IDS:
        with contextlib.suppress(Exception):
            await m.bot.unban_chat_member(chat, uid, only_if_banned=True)
        await audit(chat, m.from_user.id, 'unban', uid, '')
    await m.answer('Бан снят.')


@dp.message(Command('mute'))
async def mute(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    uid = await target(m)
    p = (m.text or '').split()
    minutes = int(p[2]) if len(p) > 2 and p[2].isdigit() else 10
    if not uid:
        return await m.answer('Укажите пользователя или ответьте на сообщение.')
    chats = MOD_IDS or ({m.chat.id} if m.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) else set())
    for chat in chats:
        await apply_mute(m.bot, chat, uid, minutes, m.from_user.id, 'manual')
    await m.answer(f'Мут на {minutes} мин.')


@dp.message(Command('unmute'))
async def unmute(m):
    if not admin(m.from_user):
        return await m.answer('Доступ запрещён.')
    uid = await target(m)
    if not uid:
        return await m.answer('Укажите пользователя или ответьте на сообщение.')
    chats = MOD_IDS or ({m.chat.id} if m.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) else set())
    for chat in chats:
        with contextlib.suppress(Exception):
            await m.bot.restrict_chat_member(chat, uid, permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True))
        await audit(chat, m.from_user.id, 'unmute', uid, '')
    await m.answer('Мут снят.')


@dp.message(Command('del'))
async def delete(m):
    if not admin(m.from_user) or not m.reply_to_message:
        return await m.answer('Доступ запрещён или ответьте на сообщение.')
    try:
        await m.bot.delete_message(m.chat.id, m.reply_to_message.message_id)
        await m.delete()
        await audit(m.chat.id, m.from_user.id, 'delete', m.reply_to_message.from_user.id, '')
    except Exception as e:
        await m.answer(str(e))


@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def group(m):
    await user(m.from_user)
    async with conn() as c:
        await c.execute('UPDATE users SET messages=messages+1 WHERE id=?', (m.from_user.id,))
        await c.commit()
    if admin(m.from_user):
        return

    now = time.monotonic()
    d = windows[(m.chat.id, m.from_user.id)]
    d.append(now)
    while d and now - d[0] > SPAM_W:
        d.popleft()
    if len(d) >= SPAM_N:
        with contextlib.suppress(Exception):
            await m.delete()
        await audit(m.chat.id, m.from_user.id, 'antispam', m.from_user.id, f'{len(d)}/{SPAM_W}s')
        return

    text = m.text or m.caption or ''
    if text and bad(text):
        with contextlib.suppress(Exception):
            await m.delete()
        n = await warn(m.from_user.id)
        await audit(m.chat.id, m.from_user.id, 'profanity', m.from_user.id, str(n))
        if n >= MAX_WARN:
            await apply_ban(m.bot, m.chat.id, m.from_user.id, 'Превышен лимит предупреждений.', 0)
        else:
            await apply_mute(m.bot, m.chat.id, m.from_user.id, PROF_MUTE, 0, 'profanity')
        return

    if AI_ENABLED and text:
        asyncio.create_task(ai_moderate_message(m))


@dp.message(F.chat.type == ChatType.PRIVATE)
async def private(m):
    await user(m.from_user)
    if (m.text or '').startswith('/'):
        return
    b = await ban(m.from_user.id)
    if b:
        async with conn() as c:
            await c.execute('INSERT INTO appeals(user_id,text,created_at) VALUES(?,?,?)', (m.from_user.id, m.text or '', datetime.now(timezone.utc).isoformat()))
            await c.commit()
        await m.answer('Апелляция сохранена и передана администрации.')
        for aid in ADMIN_IDS:
            with contextlib.suppress(Exception):
                await m.bot.send_message(aid, f'Новая апелляция от {m.from_user.id}:\n\n{(m.text or "")[:3000]}')
    else:
        async with conn() as c:
            await c.execute('INSERT INTO questions(user_id,text,created_at) VALUES(?,?,?)', (m.from_user.id, m.text or '', datetime.now(timezone.utc).isoformat()))
            await c.commit()
        for aid in ADMIN_IDS:
            with contextlib.suppress(Exception):
                await m.bot.send_message(aid, f'Вопрос от @{m.from_user.username or m.from_user.id}:\n\n{m.text}')
        await m.answer('Сообщение получено. Это обычный чат с администрацией.')


async def main():
    global ai_session
    await init()
    timeout = aiohttp.ClientTimeout(total=AI_TIMEOUT + 5)
    ai_session = aiohttp.ClientSession(timeout=timeout)
    bot = Bot(TOKEN)
    try:
        me = await bot.get_me()
        log.info('Run polling for bot @%s id=%s - %s', me.username, me.id, me.full_name)
        log.info('Mini App: %s', WEB or 'disabled (WEBAPP_URL empty)')
        if AI_ENABLED:
            ready = await ai_models()
            log.info('Shieldstral: %s | base=%s | model=%s | action=%s', 'ready' if ready else 'waiting for local server', AI_BASE_URL, AI_MODEL or '(auto)', AI_ACTION)
        await dp.start_polling(bot)
    finally:
        if ai_session:
            await ai_session.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
