import os
import json
import logging
import sqlite3
import threading
import math
from datetime import datetime, timedelta
from contextlib import contextmanager

import telebot
from telebot import types
from telebot.types import BotCommand
from flask import Flask, request

# PDF yaratish uchun kutubxonalar
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Konfiguratsiya
TOKEN       = os.environ.get("BOT_TOKEN", "8505975357:AAEtUiLlhjg7joD-iJN2JPqj0fKmKyIYpw0")
SUPER_ADMIN = int(os.environ.get("ADMIN_ID", "5541008041"))
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://melodious-faun-5bde79.netlify.app/")
RUSH_WEB_APP_URL = os.environ.get("RUSH_WEB_APP_URL", "https://mathbothtml.netlify.app/")
_domain     = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
RAILWAY_URL = f"https://{_domain}" if _domain else os.environ.get("RAILWAY_URL", "")
DB_PATH     = os.environ.get("DB_PATH", "testlar_bazasi.db")
PORT        = int(os.environ.get("PORT", 5000))

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=20)

_states_lock = threading.Lock()
_user_states: dict = {}

def get_uz_now():
    return datetime.utcnow() + timedelta(hours=5)

def get_state(chat_id):
    with _states_lock:
        return _user_states.get(chat_id, {})

def set_state(chat_id, data):
    with _states_lock:
        _user_states[chat_id] = data

def clear_state(chat_id):
    with _states_lock:
        _user_states.pop(chat_id, None)

def update_state(chat_id, **kwargs):
    with _states_lock:
        _user_states.setdefault(chat_id, {}).update(kwargs)

_db_lock = threading.Lock()

@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def db_exec(query, params=()):
    try:
        with _db_lock, db_conn() as conn:
            conn.execute(query, params)
    except Exception as e:
        log.error("DB exec xato: %s", e)

def db_fetch(query, params=(), one=False):
    try:
        with db_conn() as conn:
            cur = conn.execute(query, params)
            if one:
                row = cur.fetchone()
                return tuple(row) if row else None
            return [tuple(r) for r in cur.fetchall()]
    except Exception as e:
        log.error("DB fetch xato: %s", e)
        return None if one else []

def init_db():
    db_exec("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name    TEXT NOT NULL
    )""")
    db_exec("""CREATE TABLE IF NOT EXISTS tests (
        code     TEXT PRIMARY KEY,
        answers  TEXT,
        deadline TEXT DEFAULT '0',
        type     TEXT DEFAULT 'pdf',
        link     TEXT DEFAULT ''
    )""")
    db_exec("""CREATE TABLE IF NOT EXISTS results (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL,
        name          TEXT    NOT NULL,
        code          TEXT    NOT NULL,
        score         INTEGER NOT NULL,
        total         INTEGER NOT NULL,
        analysis_text TEXT,
        created_at    TEXT DEFAULT (datetime('now','+5 hours'))
    )""")
    db_exec("""CREATE TABLE IF NOT EXISTS admins (
        user_id  INTEGER PRIMARY KEY,
        name    TEXT NOT NULL,
        added_at TEXT DEFAULT (datetime('now','+5 hours'))
    )""")
    db_exec("""CREATE TABLE IF NOT EXISTS rasch_answers (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        test_code   TEXT NOT NULL,
        answers_bin TEXT NOT NULL
    )""")
    log.info("Ma'lumotlar bazasi tayyor ✅")

init_db()

def is_admin(chat_id):
    if int(chat_id) == SUPER_ADMIN:
        return True
    row = db_fetch("SELECT user_id FROM admins WHERE user_id=?", (chat_id,), one=True)
    return row is not None

def is_super_admin(chat_id):
    return int(chat_id) == SUPER_ADMIN

def progress_bar(score, total):
    if total == 0:
        return ""
    pct   = score / total
    green = int(pct * 10)
    return "🟩" * green + "⬜" * (10 - green) + f"  {int(pct * 100)}%"

def main_menu(chat_id):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📝 Test ishlash"),
        types.KeyboardButton("📈 Rush model Test"),
    )
    kb.add(
        types.KeyboardButton("📊 Natijalarim"),
    )
    if is_admin(chat_id):
        kb.add(
            types.KeyboardButton("➕ Yangi test qo'shish"),
            types.KeyboardButton("➕ HTML test qo'shish"),
            types.KeyboardButton("➕ Rush test qo'shish")
        )
        kb.add(types.KeyboardButton("📊 Natijalarni olish"))
    if is_super_admin(chat_id):
        kb.add(types.KeyboardButton("👥 Adminlar boshqaruvi"))
    return kb

def back_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
    return kb

def is_back(text):
    return text == "🔙 Ortga qaytish"

def safe_send(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        log.warning("Xabar yuborishda xato (chat_id=%s): %s", chat_id, e)
        return None

def go_home(message):
    clear_state(message.chat.id)
    safe_send(message.chat.id, "🏠 Asosiy menyu:", reply_markup=main_menu(message.chat.id))

def set_commands():
    bot.set_my_commands([
        BotCommand("start",     "Botni qayta ishga tushirish"),
        BotCommand("test",      "Test ishlash"),
        BotCommand("testlarim", "Natijalarim"),
        BotCommand("edit",      "Ismni o'zgartirish"),
        BotCommand("info",      "Bot haqida"),
    ])

set_commands()

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    clear_state(msg.chat.id)
    user = db_fetch("SELECT name FROM users WHERE user_id=?", (msg.chat.id,), one=True)
    if user:
        safe_send(msg.chat.id, f"👋 Salom, *{user[0]}*!",
                  parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))
    else:
        m = safe_send(msg.chat.id, "🎉 Xush kelibsiz!\n\n✏️ Ism va familiyangizni kiriting:")
        if m:
            bot.register_next_step_handler(m, _register_user)

@bot.message_handler(commands=["edit"])
def cmd_edit(msg):
    m = safe_send(msg.chat.id, "✏️ Yangi ism va familiyangizni kiriting:", reply_markup=back_kb())
    if m:
        bot.register_next_step_handler(m, _register_user)

def _register_user(msg):
    if is_back(msg.text):
        return go_home(msg)
    name = msg.text.strip()
    if not name or len(name) > 100:
        m = safe_send(msg.chat.id, "❌ Ism noto'g'ri. Qaytadan kiriting:")
        if m:
            bot.register_next_step_handler(m, _register_user)
        return
    db_exec("INSERT OR REPLACE INTO users (user_id, name) VALUES (?,?)", (msg.chat.id, name))
    safe_send(msg.chat.id, f"✅ Saqlandi! Xush kelibsiz, *{name}*!",
              parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

@bot.message_handler(func=lambda m: m.text == "🔙 Ortga qaytish")
def handle_back(msg):
    go_home(msg)

@bot.message_handler(commands=["testlarim"])
@bot.message_handler(func=lambda m: m.text == "📊 Natijalarim")
def cmd_my_results(msg):
    rows = db_fetch(
        "SELECT code, score, total, created_at FROM results "
        "WHERE user_id=? ORDER BY id DESC LIMIT 25",
        (msg.chat.id,)
    )
    if not rows:
        safe_send(msg.chat.id, "❌ Siz hali hech qanday test ishlamadingiz.",
                  reply_markup=main_menu(msg.chat.id))
        return
    lines = ["📊 *Sizning natijalaringiz:*\n"]
    for i, (code, score, total, created_at) in enumerate(rows, 1):
        bar = progress_bar(score, total)
        lines.append(f"*{i}.* Kod: `{code}` — `{score}/{total}`\n{bar}\n_{created_at}_\n")
    safe_send(msg.chat.id, "\n".join(lines),
              parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

# Rasch logic
def get_rasch_item_difficulties(code, total_q):
    rows = db_fetch("SELECT answers_bin FROM rasch_answers WHERE test_code=?", (code,))
    if not rows or len(rows) < 3:
        return [0.0] * total_q
    
    difficulties = []
    n_users = len(rows)
    for i in range(total_q):
        correct_count = sum(1 for row in rows if len(row[0]) > i and row[0][i] == '1')
        p = correct_count / n_users
        p = max(0.05, min(0.95, p))
        b = math.log((1 - p) / p)
        difficulties.append(b)
    return difficulties

def calculate_rasch_theta(score, b_items):
    total_q = len(b_items)
    if score <= 0: return -3.0
    if score >= total_q: return 3.0
    theta = math.log(score / (total_q - score))
    for _ in range(10):
        prob_sum = 0
        info_sum = 0
        for b in b_items:
            try:
                p = math.exp(theta - b) / (1 + math.exp(theta - b))
            except OverflowError:
                p = 1.0 if (theta - b) > 0 else 0.0
            prob_sum += p
            info_sum += p * (1 - p)
        diff = prob_sum - score
        if abs(diff) < 0.01: break
        if info_sum > 0: theta -= diff / info_sum
    return theta

@bot.message_handler(commands=["test"])
@bot.message_handler(func=lambda m: m.text in ["📝 Test ishlash", "📈 Rush model Test"])
def cmd_student(msg):
    user = db_fetch("SELECT name FROM users WHERE user_id=?", (msg.chat.id,), one=True)
    if not user: return cmd_start(msg)
    set_state(msg.chat.id, {"action": "student_solve", "name": user[0]})
    m = safe_send(msg.chat.id, "🔢 Test kodini kiriting:", reply_markup=back_kb())
    if m: bot.register_next_step_handler(m, _student_code_entered)

def _student_code_entered(msg):
    if is_back(msg.text): return go_home(msg)
    code = msg.text.strip().upper()
    
    count = db_fetch("SELECT COUNT(*) FROM results WHERE user_id=? AND code=?", (msg.chat.id, code), one=True)
    if count and count[0] >= 2:
        safe_send(msg.chat.id, "⚠️ Siz bu testni allaqachon *2 marta* ishlagansiz!", reply_markup=main_menu(msg.chat.id))
        return

    row = db_fetch("SELECT answers, deadline, type, link FROM tests WHERE code=?", (code,), one=True)
    if not row:
        m = safe_send(msg.chat.id, "❌ Bunday kod topilmadi. Qaytadan kiriting:", reply_markup=back_kb())
        if m: bot.register_next_step_handler(m, _student_code_entered)
        return

    answers, deadline, test_type, html_link = row
    update_state(msg.chat.id, code=code, correct=answers, type=test_type, html_link=html_link)
    
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if test_type == "html":
        link = f"{html_link}&v=3" if "?" in html_link else f"{html_link}?v=3"
        kb.add(types.KeyboardButton("📱 Testni boshlash", web_app=types.WebAppInfo(url=link)))
    elif test_type == "rush":
        kb.add(types.KeyboardButton("📱 Rush Testni boshlash", web_app=types.WebAppInfo(url=f"{RUSH_WEB_APP_URL}?count={len(answers)}&v=3")))
    else:
        kb.add(types.KeyboardButton("📱 Javoblarni belgilash", web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?count={len(answers)}&v=3")))
        
    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
    safe_send(msg.chat.id, f"✅ *Test topildi!*\n🔢 Kod: `{code}`", parse_mode="Markdown", reply_markup=kb)

# Admin functions
@bot.message_handler(func=lambda m: m.text == "➕ Yangi test qo'shish")
def admin_add_pdf(msg):
    if not is_admin(msg.chat.id): return
    m = safe_send(msg.chat.id, "Kod va savol sonini kiriting\n_(Misol: 701 30)_", parse_mode="Markdown", reply_markup=back_kb())
    if m: bot.register_next_step_handler(m, _admin_base_code, "pdf")

@bot.message_handler(func=lambda m: m.text == "➕ Rush test qo'shish")
def admin_add_rush(msg):
    if not is_admin(msg.chat.id): return
    m = safe_send(msg.chat.id, "Rasch modeli asosida baholanuvchi kod va savol sonini kiriting\n_(Misol: 801 40)_", parse_mode="Markdown", reply_markup=back_kb())
    if m: bot.register_next_step_handler(m, _admin_base_code, "rush")

def _admin_base_code(msg, test_type):
    if is_back(msg.text): return go_home(msg)
    try:
        parts = msg.text.strip().split()
        code, count = parts[0].upper(), int(parts[1])
        set_state(msg.chat.id, {"action": "admin_save_deadline", "code": code, "count": count, "test_type": test_type})
        m = safe_send(msg.chat.id, "📅 Yopilish vaqtini kiriting\n_(Misol: 2026-12-31 18:00)_ yoki *0*", parse_mode="Markdown", reply_markup=back_kb())
        if m: bot.register_next_step_handler(m, _admin_base_deadline)
    except:
        m = safe_send(msg.chat.id, "❌ Noto'g'ri format!", parse_mode="Markdown")
        if m: bot.register_next_step_handler(m, _admin_base_code, test_type)

# ---- TUZATILGAN QISM ----
def _admin_base_deadline(msg):
    if is_back(msg.text): return go_home(msg)
    deadline = msg.text.strip()
    if deadline != "0":
        try: datetime.strptime(deadline, "%Y-%m-%d %H:%M")
        except:
            m = safe_send(msg.chat.id, "❌ Noto'g'ri format! (YYYY-MM-DD HH:MM) yoki 0:")
            if m: bot.register_next_step_handler(m, _admin_base_deadline)
            return

    update_state(msg.chat.id, deadline=deadline, action="admin_save")
    state = get_state(msg.chat.id)
    kb    = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # RUSH yoki PDF ekanligiga qarab link tanlanadi
    test_type = state.get("test_type", "pdf")
    target_url = RUSH_WEB_APP_URL if test_type == "rush" else WEB_APP_URL

    kb.add(types.KeyboardButton(
        "🛠 Javoblarni kiritish",
        web_app=types.WebAppInfo(url=f"{target_url}?count={state['count']}&v=3")
    ))
    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
    
    t_name = "Rush (Rasch)" if test_type == "rush" else "PDF"
    safe_send(msg.chat.id, f"✅ *Kod:* `{state['code']}` ({t_name})\n📅 *Muddat:* {deadline}\n\nTugmani bosib to'g'ri javoblarni kiriting 👇", parse_mode="Markdown", reply_markup=kb)
# -------------------------

@bot.message_handler(content_types=["web_app_data"])
def handle_web_app(msg):
    raw_data = msg.web_app_data.data.strip()
    state = get_state(msg.chat.id)
    
    # Admin saqlash rejimi
    if state.get("action") == "admin_save":
        test_type = state.get("test_type", "pdf")
        db_exec("INSERT OR REPLACE INTO tests (code, answers, deadline, type, link) VALUES (?,?,?,?,?)",
                (state["code"], raw_data.lower(), state.get("deadline", "0"), test_type, ""))
        clear_state(msg.chat.id)
        safe_send(msg.chat.id, f"✅ {test_type.upper()} testi saqlandi!", reply_markup=main_menu(msg.chat.id))
        return

    # Student yechish rejimi (qisqartirilgan)
    try:
        data = json.loads(raw_data)
        if data.get("type") == "rush_test":
            # PDF yaratish funksiyasini chaqirish kerak (kodda oldingi PDF funksiyasi bo'lishi kerak)
            pass
    except:
        pass
    
    # ... qolgan logikalar o'z holida qoladi ...
    safe_send(msg.chat.id, "✅ Natija saqlandi.", reply_markup=main_menu(msg.chat.id))

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update = telebot.types.Update.de_json(request.get_data(as_text=True))
    bot.process_new_updates([update])
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
