import os
import json
import logging
import sqlite3
import threading
import math
import csv
import io
from datetime import datetime, timedelta
from contextlib import contextmanager

import telebot
from telebot import types
from telebot.types import BotCommand
from flask import Flask, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Konfiguratsiya ---
TOKEN       = os.environ.get("BOT_TOKEN", "8505975357:AAEtUiLlhjg7joD-iJN2JPqj0fKmKyIYpw0")
SUPER_ADMIN = int(os.environ.get("ADMIN_ID", "5541008041"))
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://eshoonqulov-math-testbot.netlify.app/")
RUSH_WEB_APP_URL = os.environ.get("RUSH_WEB_APP_URL", "https://fluffy-kulfi-1a423c.netlify.app/")
_domain     = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
RAILWAY_URL = f"https://{_domain}" if _domain else os.environ.get("RAILWAY_URL", "")
DB_PATH     = os.environ.get("DB_PATH", "testlar_bazasi.db")
PORT        = int(os.environ.get("PORT", 5000))
CHANNEL_USERNAME = "@eshonqulov_math"

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=20)

# --- State Management ---
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

# --- Ma'lumotlar Bazasi (SQLite) ---
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
        code       TEXT PRIMARY KEY,
        creator_id INTEGER NOT NULL,
        answers    TEXT,
        deadline   TEXT DEFAULT '0',
        type       TEXT DEFAULT 'pdf',
        link       TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','+5 hours'))
    )""")
    try: db_exec("ALTER TABLE tests ADD COLUMN creator_id INTEGER DEFAULT 0")
    except: pass
    try: db_exec("ALTER TABLE tests ADD COLUMN created_at TEXT DEFAULT (datetime('now','+5 hours'))")
    except: pass

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
    db_exec("""CREATE TABLE IF NOT EXISTS rasch_answers (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        test_code   TEXT NOT NULL,
        answers_bin TEXT NOT NULL
    )""")
    log.info("Ma'lumotlar bazasi tayyor ✅")

init_db()

def clean_old_data():
    try:
        db_exec("DELETE FROM tests WHERE created_at <= datetime('now', '-7 days', '+5 hours')")
        db_exec("DELETE FROM results WHERE created_at <= datetime('now', '-7 days', '+5 hours')")
        db_exec("DELETE FROM rasch_answers WHERE test_code NOT IN (SELECT code FROM tests)")
    except Exception as e:
        log.error("Eski ma'lumotlarni tozalashda xato: %s", e)

# --- Yordamchi Funksiyalar ---
def progress_bar(score, total):
    if total == 0:
        return ""
    pct   = score / total
    green = int(pct * 10)
    return "🟩" * green + "⬜" * (10 - green) + f"  {int(pct * 100)}%"

def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📝 Odatiy test ishlash"),
        types.KeyboardButton("📈 MS test ishlash"),
    )
    kb.add(
        types.KeyboardButton("➕ Odatiy test qo'shish"),
        types.KeyboardButton("➕ MS test yaratish")
    )
    kb.add(
        types.KeyboardButton("📊 Natijalarim"),
        types.KeyboardButton("📊 Natijalarni olish")
    )
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

def go_home(msg):
    clear_state(msg.chat.id)
    safe_send(msg.chat.id, "🏠 Asosiy menyu:", reply_markup=main_menu())

# Yangilangan Telegram Menyusi
def set_commands():
    bot.set_my_commands([
        BotCommand("start", "Botni qayta ishga tushirish"),
        BotCommand("test", "Test ishlash"),
        BotCommand("testlarim", "Natijalarim"),
        BotCommand("edit", "Ismni o'zgartirish"),
        BotCommand("info", "Bot haqida")
    ])

set_commands()

# --- Majburiy A'zolik Tekshiruvi ---
def is_subscribed(user_id):
    if user_id == SUPER_ADMIN: return True
    try:
        status = bot.get_chat_member(CHANNEL_USERNAME, user_id).status
        return status in ['member', 'administrator', 'creator']
    except Exception:
        return False

def prompt_sub(chat_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ A'zo bo'lish", url=f"https://t.me/{CHANNEL_USERNAME[1:]}"))
    kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub"))
    safe_send(chat_id, "⚠️ **Botdan to'liq foydalanish uchun avval quyidagi kanalga a'zo bo'ling!**", parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
def cq_check_sub(call):
    if is_subscribed(call.from_user.id):
        bot.answer_callback_query(call.id, "✅ Rahmat! A'zo bo'ldingiz.", show_alert=True)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        m = types.Message(message_id=0, from_user=call.from_user, date=0, chat=call.message.chat, content_type='text', options={}, json_string="")
        cmd_start(m)
    else:
        bot.answer_callback_query(call.id, "❌ Hali a'zo bo'lmagansiz!", show_alert=True)

# --- Json Parser ---
def extract_answers_list(raw_data):
    try:
        data = json.loads(raw_data)
        if isinstance(data, list):
            return [str(x).strip().lower() for x in data]
        elif isinstance(data, dict):
            if "answers" in data:
                ans = data["answers"]
                if isinstance(ans, list):
                    return [str(x).strip().lower() for x in ans]
                elif isinstance(ans, dict):
                    return [str(v).strip().lower() for k, v in sorted(ans.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else item[0])]
            else:
                ans_items = {k: v for k, v in data.items() if str(k).isdigit()}
                if ans_items:
                    return [str(v).strip().lower() for k, v in sorted(ans_items.items(), key=lambda item: int(item[0]))]
                else:
                    return [str(v).strip().lower() for k, v in sorted(data.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else str(item[0])) if k != "code"]
        return [str(data).strip().lower()]
    except Exception:
        text = raw_data.strip().lower()
        if "," in text:
            return [x.strip() for x in text.split(",")]
        else:
            return list(text)

def extract_bin_from_analysis(analysis_text):
    """ Emojilardan foydalanib o'quvchining 1-0 ketma-ketligini qayta tiklaydi """
    bin_str = ""
    if not analysis_text: return ""
    for char in analysis_text:
        if char == '✅': bin_str += '1'
        elif char == '❌': bin_str += '0'
    return bin_str

# --- Asosiy Buyruqlar ---
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    clean_old_data() 
    clear_state(msg.chat.id)
    
    m = safe_send(msg.chat.id, "🎉 Xush kelibsiz!\n\n✏️ To'liq ism va familiyangizni kiriting:", reply_markup=types.ReplyKeyboardRemove())
    if m:
        bot.register_next_step_handler(m, _register_user)

def _register_user(msg):
    name = msg.text.strip() if msg.text else ""
    if not name or len(name) > 100 or is_back(name):
        m = safe_send(msg.chat.id, "❌ Iltimos, faqat to'liq ism va familiyangizni kiriting:")
        if m: bot.register_next_step_handler(m, _register_user)
        return
    db_exec("INSERT OR REPLACE INTO users (user_id, name) VALUES (?,?)", (msg.chat.id, name))
    safe_send(msg.chat.id, f"✅ Saqlandi! Asosiy menyu, *{name}*:",
              parse_mode="Markdown", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "🔙 Ortga qaytish")
def handle_back(msg):
    go_home(msg)

@bot.message_handler(commands=["edit"])
def handle_change_name(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    m = safe_send(msg.chat.id, "✏️ Yangi to'liq ism va familiyangizni kiriting:", reply_markup=types.ReplyKeyboardRemove())
    if m:
        bot.register_next_step_handler(m, _register_user)

@bot.message_handler(commands=["testlarim"])
@bot.message_handler(func=lambda m: m.text == "📊 Natijalarim")
def cmd_my_results(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    rows = db_fetch(
        "SELECT code, score, total, created_at FROM results "
        "WHERE user_id=? ORDER BY id DESC LIMIT 25",
        (msg.chat.id,)
    )
    if not rows:
        safe_send(msg.chat.id, "❌ Siz hali hech qanday test ishlamadingiz.", reply_markup=main_menu())
        return
    lines = ["📊 *Sizning natijalaringiz:*\n"]
    for i, row in enumerate(rows, 1):
        code, score, total, created_at = row[0], row[1], row[2], row[3]
        bar = progress_bar(score, total)
        lines.append(f"*{i}.* Kod: `{code}` — `{score}/{total}`\n{bar}\n_{created_at}_\n")
    safe_send(msg.chat.id, "\n".join(lines), parse_mode="Markdown", reply_markup=main_menu())

@bot.message_handler(commands=["info"])
def cmd_info(msg):
    text = (
        "ℹ️ *Bot haqida ma'lumot:*\n\n"
        "Ushbu bot matematika fanidan testlarni ishlash, MS (Rasch) tizimi bo'yicha baholash "
        "va natijalarni avtomatik hisoblash uchun mo'ljallangan.\n\n"
        "👨‍🏫 *Muallif:* Eshonqulov Akobir\n"
        "📢 *Kanal:* @Eshonqulov\\_math"
    )
    safe_send(msg.chat.id, text, parse_mode="Markdown")


# =========================================================
# --- YAKUNIY: GIBRID-RASCH (INTERPOLATSIYA USULI BILAN) ---
# =========================================================

def recalculate_ms_item_weights(code, total_q=55):
    """
    Rasch modeli bo'yicha savollar qiyinchiligini aniqlaydi.
    Eng qiyin savollarga yuqori og'irlik, osonlariga past og'irlik beradi.
    """
    rows = db_fetch("SELECT answers_bin FROM rasch_answers WHERE test_code=?", (code,))
    n_users = len(rows)
    
    # Agar hali hech kim ishlamagan bo'lsa, barcha savol teng qiyinchilikda
    if n_users == 0:
        return [1.0] * total_q

    pass_rates = []
    
    # Har bir savol uchun topilish foizini (pass rate) hisoblash
    for i in range(total_q):
        correct_count = sum(1 for row in rows if len(row[0]) > i and row[0][i] == '1')
        p = correct_count / n_users
        
        # Limitlar: 100% ni 95% ga, 0% ni 5% ga cheklaymiz (cheksizlik xatosi bermasligi uchun)
        p = max(0.05, min(0.95, p)) 
        pass_rates.append(p)
        
    # Logitlar (Qiyinchilik indeksi: b = ln((1-p)/p))
    logits = [math.log((1 - p) / p) for p in pass_rates]
    min_l, max_l = min(logits), max(logits)
    
    # Hamma savol bir xil topilgan bo'lsa
    if max_l == min_l:
        return [1.0] * total_q
        
    # Logitlarni 1.0 dan 5.0 gacha bo'lgan og'irliklarga (weight) o'tkazamiz
    weights = []
    for l in logits:
        w = 1.0 + ((l - min_l) / (max_l - min_l)) * 4.0
        weights.append(w)
        
    return weights


def calculate_ms_final_score(user_answers_bin, item_weights):
    """
    O'quvchining to'g'ri javoblari soni bo'yicha bazaviy qolipini olib, 
    u yechgan savollar qiyinchiligiga qarab oraliqdagi aniq o'nlik ballni beradi.
    """
    togri_soni = user_answers_bin.count('1')
    min_len = min(len(user_answers_bin), len(item_weights))
    
    # 100% to'g'ri topganlar uchun yoki 0 ta topganlar uchun istisnolar
    if togri_soni == min_len and togri_soni > 0:
        return 100.0, "A+"
    if togri_soni == 0:
        return 0.0, "—"
    
    # 1. ZONA CHEGARALARINI BELGILASH (Qoliplar)
    if togri_soni >= 42:
        min_ball, max_ball = 70.0, 100.0  # A+
    elif togri_soni >= 36:
        min_ball, max_ball = 65.0, 69.9   # A
    elif togri_soni >= 30:
        min_ball, max_ball = 60.0, 64.9   # B+
    elif togri_soni >= 26:
        min_ball, max_ball = 55.0, 59.9   # B
    elif togri_soni >= 21:
        min_ball, max_ball = 50.0, 54.9   # C+
    elif togri_soni >= 15:
        min_ball, max_ball = 46.0, 49.9   # C
    else:
        # Yiqilganlar zonasi (0 - 45.9) - To'g'ri soniga qarab pastki qism ham dinamik o'sadi
        base_min = (togri_soni / 15.0) * 40.0
        min_ball, max_ball = base_min, 45.9

    # 2. O'QUVCHINING XOM BALI (Faqat to'g'ri topilgan savollar og'irligi yig'indisi)
    user_w_sum = sum(item_weights[i] for i in range(min_len) if user_answers_bin[i] == '1')
    
    # 3. INTERPOLATSIYA UCHUN MIN/MAX CHEGARALAR
    # N ta to'g'ri javob uchun mumkin bo'lgan eng kam (eng osonlari) va eng ko'p (eng qiyinlari) ball
    sorted_w = sorted(item_weights[:min_len])
    min_w_sum = sum(sorted_w[:togri_soni])
    max_w_sum = sum(sorted_w[-togri_soni:])
    
    # 4. ORALIQDAGI FOIZNI HISOBLASH
    if max_w_sum == min_w_sum:
        ratio = 0.5 # Og'irliklar bir xil bo'lsa o'rta arifmetik
    else:
        ratio = (user_w_sum - min_w_sum) / (max_w_sum - min_w_sum)
        
    # Matematik xatoliklarning oldini olish
    ratio = max(0.0, min(1.0, ratio))
    
    # 5. YAKUNIY BALLNI CHIQARISH (Qolip ichiga o'tqazish)
    yakuniy_ball = min_ball + ratio * (max_ball - min_ball)
    yakuniy_ball = round(yakuniy_ball, 1)
    
    # 6. DARAJA BERISH
    if togri_soni < 15 or yakuniy_ball < 46.0:
        daraja = "—"
    elif 46.0 <= yakuniy_ball < 50.0:   daraja = "C"
    elif 50.0 <= yakuniy_ball < 55.0:   daraja = "C+"
    elif 55.0 <= yakuniy_ball < 60.0:   daraja = "B"
    elif 60.0 <= yakuniy_ball < 65.0:   daraja = "B+"
    elif 65.0 <= yakuniy_ball < 70.0:   daraja = "A"
    else:                               daraja = "A+"
        
    return yakuniy_ball, daraja


def get_daraja(ball):
    if ball >= 70: return "A+"
    elif 65 <= ball < 70: return "A"
    elif 60 <= ball < 65: return "B+"
    elif 55 <= ball < 60: return "B"
    elif 50 <= ball < 55: return "C+"
    elif 46 <= ball < 50: return "C"
    else: return "—"

# --- Student Test Solving ---
@bot.message_handler(commands=["test"])
@bot.message_handler(func=lambda m: m.text in ["📝 Odatiy test ishlash", "📈 MS test ishlash"])
def cmd_student(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    user = db_fetch("SELECT name FROM users WHERE user_id=?", (msg.chat.id,), one=True)
    if not user: return cmd_start(msg)
    
    set_state(msg.chat.id, {"action": "student_solve", "name": user[0]})
    m = safe_send(msg.chat.id, "🔢 Test kodini kiriting:", reply_markup=back_kb())
    if m: bot.register_next_step_handler(m, _student_code_entered)

def _student_code_entered(msg):
    if is_back(msg.text): return go_home(msg)
    code = msg.text.strip().upper()

    count = db_fetch("SELECT COUNT(*) FROM results WHERE user_id=? AND code=?", (msg.chat.id, code), one=True)
    if count and count[0] >= 1:
        safe_send(msg.chat.id, "⚠️ Siz bu testni allaqachon ishlagansiz!\nHar bir testga faqat *1 marta* javob yuborish mumkin.", reply_markup=main_menu())
        return

    row = db_fetch("SELECT answers, deadline, type, link FROM tests WHERE code=?", (code,), one=True)
    if not row:
        m = safe_send(msg.chat.id, "❌ Bunday kod topilmadi. Qaytadan kiriting:", reply_markup=back_kb())
        if m: bot.register_next_step_handler(m, _student_code_entered)
        return

    answers, deadline, test_type, html_link = row[0], row[1], row[2], row[3]
    
    try:
        correct_list = json.loads(answers)
        q_count = len(correct_list) if isinstance(correct_list, list) else len(answers)
    except:
        q_count = len(answers)

    update_state(msg.chat.id, code=code, correct=answers, type=test_type, html_link=html_link)

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if test_type == "rush":
        kb.add(types.KeyboardButton("📱 Javoblarni kiritish (MS)", web_app=types.WebAppInfo(url=f"{RUSH_WEB_APP_URL}?count={q_count}&v=5")))
    else:
        kb.add(types.KeyboardButton("📱 Javoblarni belgilash", web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?count={q_count}&v=5")))

    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
    safe_send(msg.chat.id, f"✅ *Test topildi!*\n🔢 Kod: `{code}`", parse_mode="Markdown", reply_markup=kb)

# --- Add Tests ---
@bot.message_handler(func=lambda m: m.text == "➕ Odatiy test qo'shish")
def user_add_pdf(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    clean_old_data()
    m = safe_send(msg.chat.id, "Kod va savol sonini bo'sh joy bilan kiriting\n_(Misol: 701 30)_", parse_mode="Markdown", reply_markup=back_kb())
    if m: bot.register_next_step_handler(m, _user_base_code_pdf)

def _user_base_code_pdf(msg):
    if is_back(msg.text): return go_home(msg)
    try:
        parts = msg.text.strip().split()
        code, count = parts[0].upper(), int(parts[1])
        set_state(msg.chat.id, {"action": "admin_save_deadline", "code": code, "count": count, "test_type": "pdf"})
        m = safe_send(msg.chat.id, "📅 Yopilish vaqtini kiriting\n_(Misol: 2026-12-31 18:00)_ yoki *0*", parse_mode="Markdown", reply_markup=back_kb())
        if m: bot.register_next_step_handler(m, _user_base_deadline)
    except:
        m = safe_send(msg.chat.id, "❌ Noto'g'ri format! Iltimos, qaytadan kiriting:", parse_mode="Markdown")
        if m: bot.register_next_step_handler(m, _user_base_code_pdf)

@bot.message_handler(func=lambda m: m.text == "➕ MS test yaratish")
def user_add_rush(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    clean_old_data()
    set_state(msg.chat.id, {"action": "admin_save_deadline", "count": 55, "test_type": "rush"})
    m = safe_send(msg.chat.id, "📅 Yopilish vaqtini kiriting\n_(Misol: 2026-12-31 18:00)_ yoki *0*", parse_mode="Markdown", reply_markup=back_kb())
    if m: bot.register_next_step_handler(m, _user_base_deadline)

def _user_base_deadline(msg):
    if is_back(msg.text): return go_home(msg)
    deadline = msg.text.strip()
    if deadline != "0":
        try: datetime.strptime(deadline, "%Y-%m-%d %H:%M")
        except:
            m = safe_send(msg.chat.id, "❌ Noto'g'ri format! (YYYY-MM-DD HH:MM) yoki 0:")
            if m: bot.register_next_step_handler(m, _user_base_deadline)
            return

    update_state(msg.chat.id, deadline=deadline, action="admin_save")
    state = get_state(msg.chat.id)
    kb   = types.ReplyKeyboardMarkup(resize_keyboard=True)

    test_type = state.get("test_type", "pdf")
    target_url = RUSH_WEB_APP_URL if test_type == "rush" else WEB_APP_URL

    if test_type == "rush":
        url_with_params = f"{target_url}?count=55&v=5"
        kb.add(types.KeyboardButton("🛠 Javoblarni kiritish", web_app=types.WebAppInfo(url=url_with_params)))
        kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
        safe_send(msg.chat.id, f"✅ *Tayyor!*\n📅 *Muddat:* {deadline}\n\nTugmani bosib ilovada **test kodini** va to'g'ri javoblarni kiriting 👇", parse_mode="Markdown", reply_markup=kb)
    else:
        url_with_params = f"{target_url}?count={state['count']}&v=5"
        kb.add(types.KeyboardButton("🛠 Javoblarni kiritish", web_app=types.WebAppInfo(url=url_with_params)))
        kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
        safe_send(msg.chat.id, f"✅ *Kod:* `{state.get('code', '')}` (Odatiy)\n📅 *Muddat:* {deadline}\n\nTugmani bosib to'g'ri javoblarni kiriting 👇", parse_mode="Markdown", reply_markup=kb)

# --- GET RESULTS & EXPORT (Reyting o'rni bilan) ---
@bot.message_handler(func=lambda m: m.text == "📊 Natijalarni olish")
def user_get_results(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    m = safe_send(msg.chat.id, "🔢 Natijalarini olmoqchi bo'lgan test kodini kiriting:", reply_markup=back_kb())
    if m: bot.register_next_step_handler(m, _user_export_results)

def _user_export_results(msg):
    if is_back(msg.text): return go_home(msg)
    code = msg.text.strip().upper()

    test_info = db_fetch("SELECT type, answers, creator_id FROM tests WHERE code=?", (code,), one=True)
    if not test_info:
        safe_send(msg.chat.id, "❌ Bu kod bo'yicha test topilmadi.", reply_markup=main_menu())
        return

    test_type, answers_raw, creator_id = test_info[0], test_info[1], test_info[2]
    
    if msg.chat.id != creator_id and msg.chat.id != SUPER_ADMIN:
        safe_send(msg.chat.id, "❌ Ushbu test natijalarini faqat uni yaratgan odam yuklab ololadi.", reply_markup=main_menu())
        return

    try:
        correct_list = json.loads(answers_raw)
        total_q = len(correct_list) if isinstance(correct_list, list) else len(answers_raw)
    except:
        total_q = len(answers_raw)

    rows = db_fetch("SELECT user_id, name, score, total, analysis_text, created_at FROM results WHERE code=? ORDER BY score DESC, created_at ASC", (code,))

    if not rows:
        safe_send(msg.chat.id, "❌ Bu test bo'yicha hech qanday natija topilmadi.", reply_markup=main_menu())
        return

    try:
        output = io.StringIO()
        # delimiter=';' excelda qatorlarning tartibli bo'linib chiqishini ta'minlaydi
        writer = csv.writer(output, delimiter=';')

        if test_type == "rush":
            # 1. Barcha abituriyentlar sonidan eng so'nggi aniq qiyinchilikni hisoblash
            item_weights = recalculate_ms_item_weights(code, total_q)
            evaluated_students = []
            
            # 2. Barchani qayta taroziga qoyamiz
            for r in rows:
                name, score, analysis_text = r[1], r[2], r[4]
                ans_bin = extract_bin_from_analysis(analysis_text)
                
                if not ans_bin or len(ans_bin) < total_q:
                    ans_bin = "1" * score + "0" * (total_q - score)
                
                ball, daraja = calculate_ms_final_score(ans_bin, item_weights)
                evaluated_students.append({
                    "name": name,
                    "score": score,
                    "ball": ball,
                    "daraja": daraja
                })
                
            # 3. O'quvchilarni yangi, haqqoniy MS Bali bo'yicha tartiblaymiz (Reyting)
            evaluated_students.sort(key=lambda x: (x["ball"], x["score"]), reverse=True)
            
            # 4. Sarlavha
            writer.writerow(["O'rni", "Ism va Familiya", "To'g'ri javob soni", "Yakuniy MS Ball", "Sertifikat Darajasi"])
            
            for idx, st in enumerate(evaluated_students, 1):
                writer.writerow([f"{idx}-o'rin", st["name"], st["score"], st["ball"], st["daraja"]])
                
        else:
            writer.writerow(["O'rni", "Ism va Familiya", "To'g'ri javob soni", "Foiz (%)", "Daraja"])
            for idx, r in enumerate(rows, 1):
                name, score, total = r[1], r[2], r[3]
                ball = round((score / total) * 100, 1) if total else 0.0
                daraja = get_daraja(ball)
                writer.writerow([f"{idx}-o'rin", name, score, f"{ball}%", daraja])

        csv_text = output.getvalue()
        # '\ufeff' (BOM) orqali Excel'da O'zbek harflari ieroglif bo'lib ketmasligini ta'minlaymiz
        csv_bytes = '\ufeff'.encode('utf8') + csv_text.encode('utf8')

        bot.send_document(
            chat_id=msg.chat.id,
            document=(f"{code}_Natijalar.csv", csv_bytes),
            caption=f"📊 *{code}* - test bo'yicha eng aniq reyting ro'yxati.\n\n_Barcha natijalar xatoliklarni tahlil qilish asosida qayta o'lchandi._",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
    except Exception as e:
        log.error(f"Fayl yaratish xatosi: {e}")
        safe_send(msg.chat.id, f"❌ Xatolik yuz berdi:\n`{str(e)}`", reply_markup=main_menu())

# --- Web App Handler (Data Receiver) ---
@bot.message_handler(content_types=["web_app_data"])
def handle_web_app(msg):
    raw_data = msg.web_app_data.data.strip()
    state = get_state(msg.chat.id)

    # 1. Test kiritilayotganda
    if state.get("action") == "admin_save":
        test_type = state.get("test_type", "pdf")
        test_code = state.get("code")

        try:
            data = json.loads(raw_data)
            if isinstance(data, dict) and "code" in data:
                test_code = str(data["code"]).strip().upper()
        except:
            pass

        if not test_code:
            safe_send(msg.chat.id, "❌ Xatolik: Test kodi kiritilmadi!", reply_markup=main_menu())
            clear_state(msg.chat.id)
            return

        answers_list = extract_answers_list(raw_data)
        answers_json_str = json.dumps(answers_list)

        db_exec("INSERT OR REPLACE INTO tests (code, creator_id, answers, deadline, type, link) VALUES (?,?,?,?,?,?)",
                (test_code, msg.chat.id, answers_json_str, state.get("deadline", "0"), test_type, ""))
        clear_state(msg.chat.id)
        
        safe_send(msg.chat.id, f"✅ Test bazaga muvaffaqiyatli saqlandi!\n🔢 Kod: `{test_code}`", parse_mode="Markdown", reply_markup=main_menu())
        
        if msg.chat.id != SUPER_ADMIN:
            user_name = db_fetch("SELECT name FROM users WHERE user_id=?", (msg.chat.id,), one=True)
            u_name = user_name[0] if user_name else str(msg.chat.id)
            notify_msg = f"🆕 *Yangi test yuklandi!*\n\n👤 *Yuklovchi:* {u_name}\n🔢 *Kod:* `{test_code}`\n📚 *Tur:* {test_type.upper()}"
            safe_send(SUPER_ADMIN, notify_msg, parse_mode="Markdown")
        return

    # 2. O'quvchi javob yuborganda
    if state.get("action") == "student_solve":
        user_name = state.get("name")
        code = state.get("code")
        test_type = state.get("type", "pdf")
        correct_answers_raw = state.get("correct", "")
        
        try:
            correct_answers = json.loads(correct_answers_raw)
            if not isinstance(correct_answers, list):
                correct_answers = list(str(correct_answers_raw).lower())
        except:
            correct_answers = list(str(correct_answers_raw).lower())

        total_q = len(correct_answers)
        user_answers = extract_answers_list(raw_data)
        
        while len(user_answers) < total_q:
            user_answers.append("")

        score = 0
        analysis_text = ""
        ans_bin = ""

        for i in range(total_q):
            u_a = str(user_answers[i]).replace(" ", "").lower()
            c_a = str(correct_answers[i]).replace(" ", "").lower()
            
            if u_a == c_a:
                score += 1
                ans_bin += "1"
                analysis_text += f"{i+1}.✅  "
            else:
                ans_bin += "0"
                disp_c_a = str(correct_answers[i]).strip().upper() if len(str(correct_answers[i]).strip()) == 1 else str(correct_answers[i]).strip()
                if not disp_c_a: disp_c_a = "-"
                analysis_text += f"{i+1}.❌({disp_c_a})  "

            if (i + 1) % 5 == 0:
                analysis_text += "\n"

        # Tizim xatolari va analizni Asosiy Jadvalga yozamiz
        db_exec("INSERT INTO results (user_id, name, code, score, total, analysis_text) VALUES (?,?,?,?,?,?)",
                (msg.chat.id, user_name, code, score, total_q, analysis_text))

        # Test MS bo'lsa, ball e'lon qilinishini kutadi
        if test_type == "rush":
            db_exec("INSERT INTO rasch_answers (test_code, answers_bin) VALUES (?,?)", (code, ans_bin))
            final_ms_ball_text = "Kutilmoqda ⏳"
            sertifikat_daraja_text = "Faylda e'lon qilinadi 📊"
        else:
            final_ms_ball = round((score / total_q) * 100, 1) if total_q else 0.0
            sertifikat_daraja = get_daraja(final_ms_ball)
            final_ms_ball_text = f"`{final_ms_ball}` ball"
            sertifikat_daraja_text = f"*{sertifikat_daraja}*"

        clear_state(msg.chat.id)

        # O'quvchiga yuboriladigan xabar
        result_msg = (
            f"📊 *Test yakunlandi!*\n\n"
            f"👤 *O'quvchi:* {user_name}\n"
            f"🔢 *Test kodi:* {code}\n"
            f"🎯 *To'g'ri javoblar:* {score} / {total_q} ta\n"
            f"📈 *To'plangan ball:* {final_ms_ball_text}\n"
            f"📜 *Sertifikat darajasi:* {sertifikat_daraja_text}\n\n"
        )
        
        if test_type == "rush":
            result_msg += "⚠️ _MS (Rasch) tizimidagi yakuniy ballaringiz barcha o'quvchilar testni ishlab bo'lgach, o'qituvchi tomonidan natijalar e'lon qilinganda ma'lum bo'ladi._\n\n"
            
        result_msg += f"📝 *Batafsil tahlil:*\n{analysis_text}"
        
        safe_send(msg.chat.id, result_msg, parse_mode="Markdown", reply_markup=main_menu())

        # Super admonga hisobot
        admin_msg = (
            f"📥 *Botda yangi test ishlash amalga oshdi!*\n\n"
            f"👤 *O'quvchi:* {user_name} (`{msg.chat.id}`)\n"
            f"🔢 *Test kodi:* {code}\n"
            f"🎯 *To'g'ri soni:* {score} / {total_q}\n"
            f"📈 *Ball / Daraja:* {final_ms_ball_text} / {sertifikat_daraja_text}\n\n"
            f"📝 *Tahlil:*\n{analysis_text}"
        )
        safe_send(SUPER_ADMIN, admin_msg, parse_mode="Markdown")
        return

    safe_send(msg.chat.id, "✅ Ma'lumot qabul qilindi.", reply_markup=main_menu())

# --- Flask Server ---
@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update = telebot.types.Update.de_json(request.get_data(as_text=True))
    bot.process_new_updates([update])
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
