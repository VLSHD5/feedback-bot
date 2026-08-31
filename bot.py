import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import aiosqlite
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
    CallbackQuery, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup,
    MenuButtonWebApp, Message, WebAppInfo,
)

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Put your BotFather token into .env")

ADMIN_USERNAMES = {x.strip().lstrip("@").lower() for x in os.getenv("ADMIN_USERNAMES", "").split(",") if x.strip()}
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
CONFIGURED_MOD_IDS = {int(x) for x in os.getenv("MODERATION_CHAT_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
DB = BASE / os.getenv("DB_PATH", "data/bot.db")
BAD = BASE / os.getenv("BAD_WORDS_PATH", "data/bad_words.txt")
WEB = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
MAX_WARN = int(os.getenv("MAX_WARNINGS", "3"))
SPAM_N = int(os.getenv("ANTISPAM_MESSAGES", "5"))
SPAM_W = int(os.getenv("ANTISPAM_WINDOW", "10"))
PROF_MUTE = int(os.getenv("PROFANITY_MUTE_MINUTES", "30"))

AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
AI_BASE_URL = os.getenv("AI_BASE_URL", "http://127.0.0.1:9931/v1").rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "local").strip() or "local"
AI_MODEL = os.getenv("AI_MODEL", "").strip()
AI_ACTION = os.getenv("AI_ACTION", "warn").lower().strip()
AI_MUTE_MINUTES = int(os.getenv("AI_MUTE_MINUTES", "30"))
AI_CONCURRENCY = max(1, int(os.getenv("AI_CONCURRENCY", "1")))
AI_MAX_CHARS = max(100, int(os.getenv("AI_MAX_CHARS", "2500")))
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT_SECONDS", "30"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("feedback")
dp = Dispatcher()
windows = defaultdict(deque)
ai_sem = asyncio.Semaphore(AI_CONCURRENCY)
ai_session: aiohttp.ClientSession | None = None


def is_admin(u) -> bool:
    return bool(u) and (u.id in ADMIN_IDS or (u.username or "").lower() in ADMIN_USERNAMES)


def app_url(view: str | None = None) -> str | None:
    if not WEB.startswith("https://"):
        return None
    return f"{WEB}?view={view}" if view else WEB


def app_button(text: str, view: str | None = None):
    url = app_url(view)
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url)) if url else None


def main_kb(u, banned: bool = False):
    rows = []
    w = app_button("Открыть приложение", "appeal" if banned else ("admin" if is_admin(u) else None))
    if w:
        rows.append([w])
    if banned and app_url("appeal"):
        rows.append([InlineKeyboardButton(text="Подать апелляцию", web_app=WebAppInfo(url=app_url("appeal")))])
    if is_admin(u) and app_url("admin"):
        rows.append([InlineKeyboardButton(text="Администрирование", web_app=WebAppInfo(url=app_url("admin")))])
    rows.append([InlineKeyboardButton(text="Моя статистика", callback_data="stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = await aiosqlite.connect(DB)
    c.row_factory = aiosqlite.Row
    return c


async def init_db():
    c = await db()
    try:
        await c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
            joined_at TEXT, last_seen TEXT, messages INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS bans(
            user_id INTEGER PRIMARY KEY, reason TEXT, banned_by INTEGER, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS appeals(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT,
            status TEXT DEFAULT 'pending', created_at TEXT, admin_id INTEGER, answer TEXT
        );
        CREATE TABLE IF NOT EXISTS questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT,
            status TEXT DEFAULT 'open', created_at TEXT, answer TEXT
        );
        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, actor_id INTEGER,
            action TEXT, target_id INTEGER, details TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS support_links(
            admin_message_id INTEGER PRIMARY KEY, user_id INTEGER, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS chats(
            chat_id INTEGER PRIMARY KEY, title TEXT, enabled INTEGER DEFAULT 1,
            joined_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT
        );
        """)
        for chat_id in CONFIGURED_MOD_IDS:
            await c.execute(
                "INSERT OR IGNORE INTO chats(chat_id,title,enabled,joined_at) VALUES(?,?,1,?)",
                (chat_id, str(chat_id), datetime.now(timezone.utc).isoformat()),
            )
        await c.commit()
    finally:
        await c.close()


async def ensure_user(u):
    now = datetime.now(timezone.utc).isoformat()
    c = await db()
    try:
        await c.execute(
            """INSERT INTO users(id,username,first_name,last_name,joined_at,last_seen)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username,
               first_name=excluded.first_name,last_name=excluded.last_name,last_seen=excluded.last_seen""",
            (u.id, u.username, u.first_name, u.last_name, now, now),
        )
        await c.commit()
    finally:
        await c.close()


async def get_ban(uid):
    c = await db()
    try:
        cur = await c.execute("SELECT * FROM bans WHERE user_id=?", (uid,))
        return await cur.fetchone()
    finally:
        await c.close()


async def get_user(uid):
    c = await db()
    try:
        cur = await c.execute("SELECT * FROM users WHERE id=?", (uid,))
        return await cur.fetchone()
    finally:
        await c.close()


async def resolve_user(value):
    value = value.strip().lstrip("@")
    if value.lstrip("-").isdigit():
        return int(value)
    c = await db()
    try:
        cur = await c.execute("SELECT id FROM users WHERE lower(username)=?", (value.lower(),))
        r = await cur.fetchone()
        return r["id"] if r else None
    finally:
        await c.close()


async def add_warning(uid):
    c = await db()
    try:
        await c.execute("INSERT OR IGNORE INTO users(id,warnings) VALUES(?,0)", (uid,))
        await c.execute("UPDATE users SET warnings=warnings+1 WHERE id=?", (uid,))
        cur = await c.execute("SELECT warnings FROM users WHERE id=?", (uid,))
        r = await cur.fetchone()
        await c.commit()
        return int(r["warnings"] if r else 0)
    finally:
        await c.close()


async def set_ban_record(uid, reason, actor):
    c = await db()
    try:
        await c.execute(
            "INSERT OR REPLACE INTO bans(user_id,reason,banned_by,created_at) VALUES(?,?,?,?)",
            (uid, reason, actor, datetime.now(timezone.utc).isoformat()),
        )
        await c.commit()
    finally:
        await c.close()


async def remove_ban_record(uid):
    c = await db()
    try:
        await c.execute("DELETE FROM bans WHERE user_id=?", (uid,))
        await c.commit()
    finally:
        await c.close()


async def write_audit(chat_id, actor_id, action, target_id=None, details=""):
    c = await db()
    try:
        await c.execute(
            "INSERT INTO audit(chat_id,actor_id,action,target_id,details,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, actor_id, action, target_id, details[:2000], datetime.now(timezone.utc).isoformat()),
        )
        await c.commit()
    finally:
        await c.close()


async def is_chat_enabled(chat_id):
    if chat_id in CONFIGURED_MOD_IDS:
        return True
    c = await db()
    try:
        cur = await c.execute("SELECT enabled FROM chats WHERE chat_id=?", (chat_id,))
        r = await cur.fetchone()
        return bool(r and r["enabled"])
    finally:
        await c.close()


async def get_setting(key, default=""):
    c = await db()
    try:
        cur = await c.execute("SELECT value FROM settings WHERE key=?", (key,))
        r = await cur.fetchone()
        return r["value"] if r else default
    finally:
        await c.close()


async def set_setting(key, value):
    c = await db()
    try:
        await c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
        await c.commit()
    finally:
        await c.close()


def contains_bad(text: str) -> bool:
    if not BAD.exists():
        return False
    low = text.lower()
    return any(x.strip().lower() in low for x in BAD.read_text("utf-8").splitlines() if x.strip() and not x.startswith("#"))


async def moderate_mute(bot, chat_id, uid, minutes, actor, reason):
    try:
        await bot.restrict_chat_member(
            chat_id, uid,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        )
        await write_audit(chat_id, actor, "mute", uid, f"{minutes} min | {reason}")
        return True
    except Exception as e:
        log.warning("mute failed: %s", e)
        return False


async def moderate_ban(bot, chat_id, uid, reason, actor):
    await set_ban_record(uid, reason, actor)
    try:
        await bot.ban_chat_member(chat_id, uid)
    except Exception as e:
        log.warning("ban failed: %s", e)
    await write_audit(chat_id, actor, "ban", uid, reason)


async def moderate_unban(bot, chat_id, uid, actor):
    await remove_ban_record(uid)
    try:
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
    except Exception as e:
        log.warning("unban failed: %s", e)
    await write_audit(chat_id, actor, "unban", uid, "")


async def ai_models():
    global AI_MODEL
    if not AI_ENABLED or ai_session is None:
        return False
    try:
        async with ai_session.get(f"{AI_BASE_URL}/models", headers={"Authorization": f"Bearer {AI_API_KEY}"}) as r:
            if r.status != 200:
                return False
            data = await r.json()
            models = data.get("data") or []
            if not models:
                return False
            ids = [str(x.get("id", "")) for x in models if x.get("id")]
            preferred = next((x for x in ids if "shieldstral" in x.lower()), ids[0])
            if not AI_MODEL or AI_MODEL not in ids:
                AI_MODEL = preferred
            return bool(AI_MODEL)
    except Exception as e:
        log.warning("AI discovery failed: %s", e)
        return False


AI_SYSTEM = """You are a Telegram trust-and-safety classifier. Reply with exactly YES or NO.
YES only for a clear serious moderation violation: credible violence threats, targeted severe harassment,
hateful attacks against a protected group, sexual exploitation or sexual content involving minors,
explicit promotion of serious criminal wrongdoing, or malicious instructions intended to cause real-world harm.
NO for ordinary disagreement, jokes, benign profanity, criticism, political opinions, or normal discussion."""


async def ai_check(text: str):
    if not AI_ENABLED or not text.strip() or len(text) > AI_MAX_CHARS or ai_session is None:
        return False, "skipped"
    async with ai_sem:
        if not AI_MODEL and not await ai_models():
            return False, "model-unavailable"
        payload = {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": AI_SYSTEM},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": 5,
            "stream": False,
        }
        try:
            async with ai_session.post(
                f"{AI_BASE_URL}/chat/completions", json=payload,
                headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                timeout=AI_TIMEOUT,
            ) as r:
                body = await r.text()
                if r.status != 200:
                    return False, f"http-{r.status}"
                data = json.loads(body)
                answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                m = re.search(r"\b(yes|no)\b", answer.lower())
                return bool(m and m.group(1) == "yes"), answer.strip()[:300]
        except Exception as e:
            log.warning("AI check failed: %s", e)
            return False, "exception"


async def ai_moderate(m: Message):
    if not AI_ENABLED or m.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    if not await is_chat_enabled(m.chat.id) or not m.from_user or is_admin(m.from_user) or m.from_user.is_bot:
        return
    text = (m.text or m.caption or "").strip()
    if not text or text.startswith("/"):
        return
    flagged, info = await ai_check(text)
    if not flagged:
        return
    await write_audit(m.chat.id, 0, "ai_flag", m.from_user.id, info)
    with contextlib.suppress(Exception):
        await m.delete()
    if AI_ACTION == "log":
        return
    if AI_ACTION == "delete":
        return
    if AI_ACTION == "mute":
        await moderate_mute(m.bot, m.chat.id, m.from_user.id, AI_MUTE_MINUTES, 0, "Shieldstral")
        return
    if AI_ACTION == "ban":
        await moderate_ban(m.bot, m.chat.id, m.from_user.id, "Shieldstral: нарушение правил", 0)
        return
    n = await add_warning(m.from_user.id)
    await write_audit(m.chat.id, 0, "ai_warn", m.from_user.id, f"{n}/{MAX_WARN}")
    if n >= MAX_WARN:
        await moderate_ban(m.bot, m.chat.id, m.from_user.id, "Shieldstral: превышен лимит предупреждений", 0)


async def setup_bot(bot: Bot):
    global ai_session
    ai_session = aiohttp.ClientSession()
    public = [
        BotCommand(command="start", description="Открыть меню"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="rules", description="Правила"),
        BotCommand(command="stats", description="Моя статистика"),
        BotCommand(command="appeal", description="Апелляция, если есть бан"),
        BotCommand(command="id", description="Показать мой ID"),
    ]
    await bot.set_my_commands(public, scope=BotCommandScopeDefault())
    if WEB.startswith("https://"):
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=WEB)))
        log.info("Mini App enabled: %s", WEB)
    else:
        log.warning("Mini App disabled: WEBAPP_URL must be an HTTPS URL")


async def setup_admin_commands(bot, uid):
    if uid not in ADMIN_IDS:
        return
    cmds = [
        BotCommand(command="admin", description="Администрирование"),
        BotCommand(command="appeals", description="Список апелляций"),
        BotCommand(command="user", description="Статистика пользователя"),
        BotCommand(command="audit", description="Аудит действий"),
        BotCommand(command="members", description="Участники и ограничения"),
        BotCommand(command="settings", description="Настройки чата"),
        BotCommand(command="connect", description="Подключить этот чат"),
        BotCommand(command="disconnect", description="Отключить модерацию"),
        BotCommand(command="warn", description="Предупреждение"),
        BotCommand(command="mute", description="Мут"),
        BotCommand(command="unmute", description="Снять мут"),
        BotCommand(command="ban", description="Бан"),
        BotCommand(command="unban", description="Разбан"),
        BotCommand(command="del", description="Удалить сообщение"),
        BotCommand(command="setrules", description="Изменить правила"),
    ]
    with contextlib.suppress(Exception):
        await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))


@dp.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user)
    await setup_admin_commands(m.bot, m.from_user.id)
    b = await get_ban(m.from_user.id)
    text = "Добро пожаловать."
    if b:
        text += f"\n\nУ вас активен бан.\nПричина: {b['reason'] or 'не указана'}.\nМожно подать апелляцию."
    else:
        text += " Напишите сюда сообщение — оно попадёт в вопросы администрации."
    await m.answer(text, reply_markup=main_kb(m.from_user, bool(b)))


@dp.message(Command("help"))
async def help_cmd(m: Message):
    t = "/start — меню\n/help — помощь\n/rules — правила\n/stats — моя статистика\n/appeal — апелляция при активном бане\n/id — мой ID"
    if is_admin(m.from_user):
        t += "\n\nАДМИН:\n/admin\n/appeals\n/user <id|username>\n/audit\n/members\n/settings\n/connect\n/disconnect\n/warn <id|username> [причина]\n/mute <id|username> [минуты] [причина]\n/unmute <id|username>\n/ban <id|username> [причина]\n/unban <id|username>\n/del — ответом на сообщение\n/setrules <текст>"
    await m.answer(t)


@dp.message(Command("id"))
async def id_cmd(m: Message):
    await m.answer(f"Ваш ID: {m.from_user.id}")


@dp.message(Command("rules"))
async def rules_cmd(m: Message):
    await m.answer(await get_setting("rules", "Правила пока не настроены."))


@dp.message(Command("stats"))
async def stats_cmd(m: Message):
    await ensure_user(m.from_user)
    u = await get_user(m.from_user.id)
    c = await db()
    try:
        cur = await c.execute("SELECT COUNT(*) n FROM appeals WHERE user_id=?", (m.from_user.id,))
        a = await cur.fetchone()
    finally:
        await c.close()
    b = await get_ban(m.from_user.id)
    await m.answer(
        f"Статистика\nID: {m.from_user.id}\nСообщений: {u['messages']}\n"
        f"Предупреждений: {u['warnings']}\nАктивный бан: {'да' if b else 'нет'}\nАпелляций: {a['n']}"
    )


@dp.message(Command("appeal"))
async def appeal_cmd(m: Message):
    b = await get_ban(m.from_user.id)
    if not b:
        await m.answer("Активного бана нет, поэтому раздел апелляции недоступен.")
        return
    await m.answer(f"Причина бана: {b['reason'] or 'не указана'}\nНапишите одним сообщением текст апелляции.")


@dp.message(Command("admin"))
async def admin_cmd(m: Message):
    if not is_admin(m.from_user):
        return
    await m.answer("Администрирование", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть Mini App", web_app=WebAppInfo(url=app_url("admin")))]]) if app_url("admin") else None)


async def private_question(m: Message):
    if m.chat.type != ChatType.PRIVATE or not m.text or m.text.startswith("/"):
        return False
    await ensure_user(m.from_user)
    c = await db()
    try:
        await c.execute("UPDATE users SET messages=messages+1 WHERE id=?", (m.from_user.id,))
        cur = await c.execute(
            "INSERT INTO questions(user_id,text,created_at) VALUES(?,?,?)",
            (m.from_user.id, m.text, datetime.now(timezone.utc).isoformat()),
        )
        qid = cur.lastrowid
        await c.commit()
    finally:
        await c.close()
    admins = ADMIN_IDS.copy()
    if not admins:
        await m.answer("Сообщение сохранено. Администратор пока не настроен.")
        return True
    header = f"Вопрос #{qid} от {('@'+m.from_user.username) if m.from_user.username else m.from_user.id}:\n{m.text}"
    for aid in admins:
        try:
            sent = await m.bot.send_message(aid, header)
            c2 = await db()
            try:
                await c2.execute("INSERT OR REPLACE INTO support_links(admin_message_id,user_id,created_at) VALUES(?,?,?)", (sent.message_id, m.from_user.id, datetime.now(timezone.utc).isoformat()))
                await c2.commit()
            finally:
                await c2.close()
        except Exception as e:
            log.warning("support forward failed: %s", e)
    await m.answer("Сообщение отправлено администрации. Ответ придёт сюда.")
    return True


@dp.message(F.web_app_data)
async def webapp_data(m: Message):
    try:
        payload = json.loads(m.web_app_data.data)
    except Exception:
        await m.answer("Не удалось прочитать действие Mini App.")
        return
    action = str(payload.get("action", ""))
    text = str(payload.get("text", "")).strip()
    await ensure_user(m.from_user)
    if action == "stats":
        await stats_cmd(m)
    elif action == "rules":
        await rules_cmd(m)
    elif action == "questions":
        await m.answer("Просто напишите сюда сообщение — это обычный чат с администрацией.")
    elif action == "appeal":
        b = await get_ban(m.from_user.id)
        if not b:
            await m.answer("Апелляция доступна только пользователям с активным баном.")
        elif text:
            c = await db()
            try:
                cur = await c.execute("INSERT INTO appeals(user_id,text,created_at) VALUES(?,?,?)", (m.from_user.id, text, datetime.now(timezone.utc).isoformat()))
                aid = cur.lastrowid
                await c.commit()
            finally:
                await c.close()
            for admin_id in ADMIN_IDS:
                with contextlib.suppress(Exception):
                    await m.bot.send_message(admin_id, f"Апелляция #{aid}\nПользователь: @{m.from_user.username or m.from_user.id}\nПричина бана: {b['reason'] or 'не указана'}\n\n{text}")
            await m.answer(f"Апелляция #{aid} отправлена администрации.")
        else:
            await m.answer("Напишите текст апелляции в поле формы.")
    elif action == "admin_appeals":
        if not is_admin(m.from_user): return
        c = await db()
        try:
            cur = await c.execute("SELECT * FROM appeals WHERE status='pending' ORDER BY id DESC LIMIT 15")
            rows = await cur.fetchall()
        finally:
            await c.close()
        if not rows:
            await m.answer("Новых апелляций нет.")
        else:
            await m.answer("\n\n".join(f"#{r['id']} | user {r['user_id']}\n{r['text'][:700]}" for r in rows))
    elif action == "audit":
        if not is_admin(m.from_user): return
        c = await db()
        try:
            cur = await c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 20")
            rows = await cur.fetchall()
        finally:
            await c.close()
        await m.answer("\n".join(f"{r['created_at'][:19]} | chat={r['chat_id']} | {r['action']} | target={r['target_id']} | {r['details'][:120]}" for r in rows) or "Аудит пуст.")
    elif action == "settings":
        if not is_admin(m.from_user): return
        chats = ", ".join(str(x) for x in sorted(CONFIGURED_MOD_IDS)) or "только подключённые через /connect"
        await m.answer(f"Настройки\nAI: {'on' if AI_ENABLED else 'off'}\nAI URL: {AI_BASE_URL}\nAI model: {AI_MODEL or 'auto-discovery'}\nAI action: {AI_ACTION}\nЛимит предупреждений: {MAX_WARN}\nМут за мат: {PROF_MUTE} мин\nЧаты из ENV: {chats}")
    elif action == "members":
        if not is_admin(m.from_user): return
        c = await db()
        try:
            cur = await c.execute("SELECT COUNT(*) n FROM users")
            users_n = (await cur.fetchone())["n"]
            cur = await c.execute("SELECT COUNT(*) n FROM bans")
            bans_n = (await cur.fetchone())["n"]
        finally:
            await c.close()
        await m.answer(f"Участники в базе: {users_n}\nАктивные баны: {bans_n}\nПодробные действия доступны командами /user и /audit.")
    elif action == "user_lookup":
        if not is_admin(m.from_user): return
        uid = await resolve_user(text)
        if not uid:
            await m.answer("Пользователь не найден. Сначала он должен написать боту или использовать ID.")
            return
        u = await get_user(uid); b = await get_ban(uid)
        await m.answer(f"Пользователь {uid}\nUsername: @{u['username'] or '—'}\nСообщений: {u['messages']}\nПредупреждений: {u['warnings']}\nБан: {'да' if b else 'нет'}\nПричина: {b['reason'] if b else '—'}")
    elif action == "warn_user":
        if not is_admin(m.from_user): return
        uid = await resolve_user(text)
        if uid:
            n = await add_warning(uid); await m.answer(f"Предупреждение выдано. Всего: {n}/{MAX_WARN}")
    elif action == "ban_user":
        if not is_admin(m.from_user): return
        uid = await resolve_user(text)
        if uid:
            reason = str(payload.get("reason", "Решение администратора"))
            await set_ban_record(uid, reason, m.from_user.id); await m.answer("Бан записан в статистику. Для фактического бана укажите чат и используйте /ban.")
    else:
        await m.answer("Действие не поддерживается.")


@dp.message(Command("connect"))
async def connect_cmd(m: Message):
    if m.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}: return
    if not is_admin(m.from_user): return
    c = await db()
    try:
        await c.execute("INSERT OR REPLACE INTO chats(chat_id,title,enabled,joined_at) VALUES(?,?,1,?)", (m.chat.id, m.chat.title or str(m.chat.id), 1, datetime.now(timezone.utc).isoformat()))
        await c.commit()
    finally: await c.close()
    await m.answer("Этот чат подключён к модерации.")
    await write_audit(m.chat.id, m.from_user.id, "connect")


@dp.message(Command("disconnect"))
async def disconnect_cmd(m: Message):
    if not is_admin(m.from_user): return
    c = await db()
    try:
        await c.execute("UPDATE chats SET enabled=0 WHERE chat_id=?", (m.chat.id,)); await c.commit()
    finally: await c.close()
    await m.answer("Модерация для этого чата отключена.")


@dp.message(Command("setrules"))
async def setrules_cmd(m: Message):
    if not is_admin(m.from_user): return
    value = m.text.partition(" ")[2].strip()
    if not value:
        await m.answer("Использование: /setrules текст правил")
        return
    await set_setting("rules", value); await m.answer("Правила сохранены.")


async def admin_target_command(m: Message, kind: str):
    if not is_admin(m.from_user): return
    parts = (m.text or "").split(maxsplit=2)
    uid = await resolve_user(parts[1]) if len(parts) > 1 else (m.reply_to_message.from_user.id if m.reply_to_message and m.reply_to_message.from_user else None)
    if not uid:
        await m.answer("Укажите @username или ID, либо ответьте на сообщение пользователя."); return
    reason = parts[2] if len(parts) > 2 else "Решение администратора"
    if kind == "warn":
        n = await add_warning(uid); await write_audit(m.chat.id, m.from_user.id, "warn", uid, reason); await m.answer(f"Предупреждение: {n}/{MAX_WARN}")
    elif kind == "ban":
        await moderate_ban(m.bot, m.chat.id, uid, reason, m.from_user.id); await m.answer("Пользователь заблокирован.")
    elif kind == "unban":
        await moderate_unban(m.bot, m.chat.id, uid, m.from_user.id); await m.answer("Пользователь разблокирован.")
    elif kind == "mute":
        minutes = 30
        if len(parts) > 2:
            bits = parts[2].split(maxsplit=1)
            if bits[0].isdigit(): minutes = max(1, int(bits[0])); reason = bits[1] if len(bits) > 1 else "Решение администратора"
        await moderate_mute(m.bot, m.chat.id, uid, minutes, m.from_user.id, reason); await m.answer(f"Мут: {minutes} мин.")
    elif kind == "unmute":
        try:
            await m.bot.restrict_chat_member(m.chat.id, uid, permissions=ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True))
            await write_audit(m.chat.id, m.from_user.id, "unmute", uid, "")
            await m.answer("Мут снят.")
        except Exception as e: await m.answer(f"Не удалось снять мут: {e}")


for _name in ("warn", "mute", "unmute", "ban", "unban"):
    dp.message.register(lambda m, _n=_name: admin_target_command(m, _n), Command(_name))


@dp.message(Command("del"))
async def del_cmd(m: Message):
    if not is_admin(m.from_user) or not m.reply_to_message: return
    with contextlib.suppress(Exception): await m.reply_to_message.delete()
    await write_audit(m.chat.id, m.from_user.id, "delete", m.reply_to_message.from_user.id if m.reply_to_message.from_user else None, "manual")


@dp.message(Command("user"))
async def user_cmd(m: Message):
    if not is_admin(m.from_user): return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2: await m.answer("/user @username или /user 123456789"); return
    uid = await resolve_user(parts[1])
    if not uid: await m.answer("Пользователь не найден."); return
    u = await get_user(uid); b = await get_ban(uid)
    await m.answer(f"ID: {uid}\nUsername: @{u['username'] or '—'}\nСообщений: {u['messages']}\nПредупреждений: {u['warnings']}\nБан: {'да' if b else 'нет'}\nПричина: {b['reason'] if b else '—'}")


@dp.message(Command("appeals"))
async def appeals_cmd(m: Message):
    if not is_admin(m.from_user): return
    c = await db()
    try:
        cur = await c.execute("SELECT * FROM appeals WHERE status='pending' ORDER BY id DESC LIMIT 20"); rows = await cur.fetchall()
    finally: await c.close()
    await m.answer("\n\n".join(f"#{r['id']} user={r['user_id']}\n{r['text'][:800]}" for r in rows) or "Апелляций нет.")


@dp.message(Command("audit"))
async def audit_cmd(m: Message):
    if not is_admin(m.from_user): return
    c = await db()
    try:
        cur = await c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 30"); rows = await cur.fetchall()
    finally: await c.close()
    await m.answer("\n".join(f"{r['created_at'][:19]} | {r['action']} | chat={r['chat_id']} | target={r['target_id']} | {r['details'][:100]}" for r in rows) or "Аудит пуст.")


@dp.message(Command("members"))
async def members_cmd(m: Message):
    if not is_admin(m.from_user): return
    c = await db()
    try:
        cur = await c.execute("SELECT COUNT(*) n FROM users"); users_n = (await cur.fetchone())["n"]
        cur = await c.execute("SELECT COUNT(*) n FROM bans"); bans_n = (await cur.fetchone())["n"]
    finally: await c.close()
    await m.answer(f"Участников в базе: {users_n}\nАктивных банов: {bans_n}\nИспользуйте /user для подробной информации.")


@dp.message(Command("settings"))
async def settings_cmd(m: Message):
    if not is_admin(m.from_user): return
    await m.answer(f"WEBAPP_URL: {WEB or 'disabled'}\nAI: {AI_ENABLED}\nAI_BASE_URL: {AI_BASE_URL}\nAI_MODEL: {AI_MODEL or 'auto'}\nAI_ACTION: {AI_ACTION}\nMAX_WARNINGS: {MAX_WARN}\nPROFANITY_MUTE_MINUTES: {PROF_MUTE}")


@dp.message(F.reply_to_message)
async def support_reply(m: Message):
    if not is_admin(m.from_user) or not m.reply_to_message:
        return
    c = await db()
    try:
        cur = await c.execute("SELECT user_id FROM support_links WHERE admin_message_id=?", (m.reply_to_message.message_id,)); r = await cur.fetchone()
    finally: await c.close()
    if not r: return
    uid = int(r["user_id"])
    try:
        await m.bot.send_message(uid, f"Ответ администрации:\n\n{m.text or '[сообщение]'}")
        await m.answer("Ответ отправлен пользователю.")
    except Exception as e: await m.answer(f"Не удалось отправить ответ: {e}")


@dp.message()
async def all_messages(m: Message):
    if m.from_user:
        await ensure_user(m.from_user)
    if await private_question(m):
        return
    if m.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP} or not m.from_user or m.from_user.is_bot:
        return
    if not await is_chat_enabled(m.chat.id):
        return
    c = await db()
    try:
        await c.execute("UPDATE users SET messages=messages+1 WHERE id=?", (m.from_user.id,)); await c.commit()
    finally: await c.close()
    text = (m.text or m.caption or "").strip()
    now = time.monotonic(); q = windows[(m.chat.id, m.from_user.id)]
    while q and now - q[0] > SPAM_W: q.popleft()
    q.append(now)
    if len(q) >= SPAM_N and not is_admin(m.from_user):
        await moderate_mute(m.bot, m.chat.id, m.from_user.id, 10, 0, "Антиспам")
        q.clear()
        with contextlib.suppress(Exception): await m.delete()
        return
    if text and contains_bad(text) and not is_admin(m.from_user):
        with contextlib.suppress(Exception): await m.delete()
        await moderate_mute(m.bot, m.chat.id, m.from_user.id, PROF_MUTE, 0, "Нецензурная лексика")
        return
    await ai_moderate(m)


async def main():
    await init_db()
    bot = Bot(TOKEN)
    me = await bot.get_me()
    log.info("Run polling for @%s id=%s", me.username, me.id)
    await setup_bot(bot)
    try:
        await dp.start_polling(bot)
    finally:
        if ai_session:
            await ai_session.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
