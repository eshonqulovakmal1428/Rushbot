import os
import json
import logging
import sqlite3
import threading
import math
import csv
import io
import re
import subprocess
from datetime import datetime, timedelta
from contextlib import contextmanager

import telebot
from telebot import types
from telebot.types import BotCommand
from flask import Flask, request

# Yangi modullar
import google.generativeai as genai
from apscheduler.schedulers.background import BackgroundScheduler

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
CHANNEL_USERNAME = "@AkobirUstoz_math"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6LP7qJu2dfCqqga4EKKGX2yKiiEMrkDhHoGlly4A4C27g")

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=20)

genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-pro')

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
    db_exec("""CREATE TABLE IF NOT EXISTS ai_channels (
        chat_id TEXT PRIMARY KEY
    )""")
    # AI SOZLAMALARI UCHUN YANGI JADVAL
    db_exec("""CREATE TABLE IF NOT EXISTS ai_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    # Boshlang'ich sozlamalar
    db_exec("INSERT OR IGNORE INTO ai_settings (key, value) VALUES ('morning_time', '08:00')")
    db_exec("INSERT OR IGNORE INTO ai_settings (key, value) VALUES ('evening_time', '18:00')")
    default_prompt = "Siz Abituriyentlar (universitetga tayyorlanayotganlar) uchun matematika kanali ustozisiz. Bugungi kun uchun 1 ta qiziqarli matematik fakt yoki motivatsion qissa yozing. Hech qanday AI ekanligingizni bildirmang."
    db_exec("INSERT OR IGNORE INTO ai_settings (key, value) VALUES ('morning_prompt', ?)", (default_prompt,))
    
    log.info("Ma'lumotlar bazasi tayyor ✅")

init_db()

def get_setting(key, default=""):
    res = db_fetch("SELECT value FROM ai_settings WHERE key=?", (key,), one=True)
    return res[0] if res else default

def set_setting(key, value):
    db_exec("INSERT OR REPLACE INTO ai_settings (key, value) VALUES (?,?)", (key, value))

def clean_old_data():
    try:
        db_exec("DELETE FROM tests WHERE created_at <= datetime('now', '-7 days', '+5 hours')")
        db_exec("DELETE FROM results WHERE created_at <= datetime('now', '-7 days', '+5 hours')")
        db_exec("DELETE FROM rasch_answers WHERE test_code NOT IN (SELECT code FROM tests)")
    except Exception as e:
        log.error("Eski ma'lumotlarni tozalashda xato: %s", e)

# --- Yordamchi Funksiyalar ---
def progress_bar(score, total):
    if total == 0: return ""
    pct = score / total
    green = int(pct * 10)
    return "🟩" * green + "⬜️" * (10 - green) + f"  {int(pct * 100)}%"

def main_menu(user_id=None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("📝 Odatiy test ishlash"), types.KeyboardButton("📈 MS test ishlash"))
    kb.add(types.KeyboardButton("➕ Odatiy test qo'shish"), types.KeyboardButton("➕ MS test yaratish"))
    kb.add(types.KeyboardButton("📊 Natijalarim"), types.KeyboardButton("📊 Natijalarni olish"))
    if user_id == SUPER_ADMIN:
        kb.add(types.KeyboardButton("👑 Admin Panel"))
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
    safe_send(msg.chat.id, "🏠 Asosiy menyu:", reply_markup=main_menu(msg.chat.id))

def set_commands():
    bot.set_my_commands([
        BotCommand("start", "Botni qayta ishga tushirish"),
        BotCommand("test", "Test ishlash"),
        BotCommand("testlarim", "Natijalarim"),
        BotCommand("edit", "Ismni o'zgartirish"),
        BotCommand("info", "Bot haqida")
    ])

set_commands()

# --- AI VA AVTOMATLASHTIRISH BO'LIMI ---
def get_ai_channels():
    rows = db_fetch("SELECT chat_id FROM ai_channels")
    return [r[0] for r in rows]

def ai_morning_task():
    log.info("Ertalabki AI vazifasi ishga tushdi...")
    channels = get_ai_channels()
    if not channels: return

    prompt = get_setting('morning_prompt')
    try:
        response = ai_model.generate_content(prompt)
        text = response.text
        for ch in channels:
            safe_send(ch, text, parse_mode="Markdown")
    except Exception as e:
        log.error(f"AI Morning xatosi: {e}")

def ai_evening_task():
    log.info("Kechki AI vazifasi (PDF Test) ishga tushdi...")
    channels = get_ai_channels()
    if not channels: return

    now_uz = get_uz_now()
    test_code = f"MS{now_uz.strftime('%d%m')}"
    deadline_str = f"{now_uz.strftime('%Y-%m-%d')} 20:00"

    prompt = """Sen Oliy ta'lim muassasalariga kirish imtihonlari (DTM) uchun mutaxassis matematika ustozisan.
    Sening vazifang abituriyentlar uchun 10 ta sifatli, mantiqiy va 1-3 qadamli yechim talab qiladigan matematika test savollarini tuzish.
    
    QATIY QOIDALAR:
    1. Savollar siyqasi chiqqan, juda oson yoki standart bo'lmasin. Haqiqiy imtihon darajasida, fikrlashga undaydigan bo'lsin.
    2. Mavzular: Algebra va Geometriya.
    3. UMUMAN BO'LMASIN: Limit, Hosila, Integral, Oliy matematika mavzulari.
    4. Geometriya savollari uchun albatta TikZ kodidan foydalanib chizma bering.
    5. Xato ketmasligi uchun o'zingizni 2 marta tekshiring. Yechimi aniq chiqadigan savollar bo'lsin.
    6. Javob variantlari (A, B, C, D) ni bering.
    7. Hech qayerda AI, Gemini so'zlari ishlatilmasin.
    
    Chiqarish formati QAT'IY ravishda quyidagicha bo'lsin:
    ---LATEX---
    \begin{enumerate}
    \item Birinchi savol matni...
    \begin{enumerate}
        \item A varianti
        \item B varianti
        \item C varianti
        \item D varianti
    \end{enumerate}
    ...
    \end{enumerate}
    ---ANSWERS---
    A,B,C,D,A,B,C,D,A,B
    """
    try:
        response = ai_model.generate_content(prompt)
        content = response.text
        
        latex_part = re.search(r'---LATEX---\n(.*?)\n---ANSWERS---', content, re.DOTALL)
        answers_part = re.search(r'---ANSWERS---\n(.*)', content, re.DOTALL)
        
        if not latex_part or not answers_part:
            log.error("AI dan format noto'g'ri keldi.")
            return

        latex_content = latex_part.group(1).strip()
        answers_raw = answers_part.group(1).strip()
        answers_list = [ans.strip().lower() for ans in answers_raw.split(',') if ans.strip()]
        
        if len(answers_list) != 10:
            log.error(f"Javoblar soni 10 ta emas: {len(answers_list)}")
            return

        full_latex = r"""\documentclass[12pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[uzbek]{babel}
\usepackage{amsmath,amssymb,amsfonts,amsthm}
\usepackage{geometry}
\geometry{a4paper, left=15mm, right=15mm, top=20mm, bottom=20mm}
\usepackage{tikz}
\usepackage{fancyhdr}
\usepackage{xcolor}

\pagestyle{fancy}
\fancyhf{}
\lhead{\textbf{\textcolor{blue}{Matematika | Akobir ustoz}}}
\rhead{\textbf{\textcolor{blue}{+99891 320 04 01}}}
\cfoot{\textbf{Telegram Kanal: @Eshonqulov\_math}}
\renewcommand{\headrulewidth}{0.4pt}
\renewcommand{\footrulewidth}{0.4pt}

\begin{document}
\begin{center}
    \Large\textbf{Matematika Test - """ + test_code + r"""}
\end{center}
\vspace{5mm}
""" + latex_content + r"""
\end{document}
"""
        tex_filename = f"{test_code}.tex"
        pdf_filename = f"{test_code}.pdf"
        
        with open(tex_filename, "w", encoding="utf-8") as f:
            f.write(full_latex)
        
        subprocess.run(["pdflatex", "-interaction=nonstopmode", tex_filename], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if os.path.exists(pdf_filename):
            answers_json_str = json.dumps(answers_list)
            db_exec("INSERT OR REPLACE INTO tests (code, creator_id, answers, deadline, type, link) VALUES (?,?,?,?,?,?)",
                    (test_code, SUPER_ADMIN, answers_json_str, deadline_str, "rush", ""))
            
            caption = f"""✅ Test ishlanishga tayyor!
✅ Test kodi: {test_code}
🛑 Berilgan vaqt: 20:00gacha

Tanishlarga yuborib qo'yamiz

> https://t.me/Eshonqulov_rushtestbot
> https://t.me/Eshonqulov_rushtestbot

Shu botga Test kodini kiritib javoblaringizni yuborishingiz mumkin ✅
🛑 Ms test ishlash bo'limini tanlang

@Eshonqulov_math"""
            
            for ch in channels:
                with open(pdf_filename, "rb") as pdf_file:
                    bot.send_document(ch, pdf_file, caption=caption, parse_mode="Markdown")
            
            for ext in [".tex", ".pdf", ".aux", ".log"]:
                f_del = test_code + ext
                if os.path.exists(f_del): os.remove(f_del)
            
            log.info(f"AI PDF muvaffaqiyatli yuborildi: {test_code}")
        else:
            log.error("PDF generatsiya amalga oshmadi (pdflatex bormi?).")

    except Exception as e:
        log.error(f"AI Evening xatosi: {e}")

# Har daqiqada tekshirib turadigan Scheduler funksiyasi
def check_schedules():
    now_str = get_uz_now().strftime("%H:%M")
    m_time = get_setting('morning_time', '08:00')
    e_time = get_setting('evening_time', '18:00')
    
    if now_str == m_time:
        ai_morning_task()
    if now_str == e_time:
        ai_evening_task()

scheduler = BackgroundScheduler(timezone="Asia/Tashkent")
scheduler.add_job(check_schedules, 'cron', minute='*') # Har daqiqada 1 marta tekshiradi
scheduler.start()

# --- ADMIN PANEL BO'LIMI ---
@bot.message_handler(func=lambda m: m.text == "👑 Admin Panel" and m.chat.id == SUPER_ADMIN)
def admin_panel_menu(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("📢 AI Kanal Qo'shish"), types.KeyboardButton("🗑 AI Kanal O'chirish"))
    kb.add(types.KeyboardButton("⚙️ AI Sozlamalari"))
    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
    safe_send(msg.chat.id, "👨‍💻 *Admin panelga xush kelibsiz!*", parse_mode="Markdown", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "📢 AI Kanal Qo'shish" and m.chat.id == SUPER_ADMIN)
def admin_add_channel(msg):
    m = safe_send(msg.chat.id, "Qo'shmoqchi bo'lgan kanalning ID raqami yoki Usernamesini yuboring (Masalan: @kanal_nomi):", reply_markup=back_kb())
    bot.register_next_step_handler(m, _process_add_channel)

def _process_add_channel(msg):
    if is_back(msg.text): return go_home(msg)
    ch_id = msg.text.strip()
    db_exec("INSERT OR IGNORE INTO ai_channels (chat_id) VALUES (?)", (ch_id,))
    safe_send(msg.chat.id, f"✅ Kanal AI ro'yxatiga qo'shildi: {ch_id}\nBot ushbu kanalda administrator bo'lishi shart!", reply_markup=main_menu(msg.chat.id))

@bot.message_handler(func=lambda m: m.text == "🗑 AI Kanal O'chirish" and m.chat.id == SUPER_ADMIN)
def admin_remove_channel(msg):
    channels = db_fetch("SELECT chat_id FROM ai_channels")
    if not channels:
        safe_send(msg.chat.id, "Bazada hech qanday kanal yo'q.", reply_markup=main_menu(msg.chat.id))
        return
    text = "Hozirgi AI kanallar:\n" + "\n".join([r[0] for r in channels]) + "\n\nO'chirmoqchi bo'lgan kanalni yozing:"
    m = safe_send(msg.chat.id, text, reply_markup=back_kb())
    bot.register_next_step_handler(m, _process_remove_channel)

def _process_remove_channel(msg):
    if is_back(msg.text): return go_home(msg)
    ch_id = msg.text.strip()
    db_exec("DELETE FROM ai_channels WHERE chat_id=?", (ch_id,))
    safe_send(msg.chat.id, f"✅ Kanal AI ro'yxatidan o'chirildi: {ch_id}", reply_markup=main_menu(msg.chat.id))

# --- YANGI: AI SOZLAMALARI BO'LIMI ---
@bot.message_handler(func=lambda m: m.text == "⚙️ AI Sozlamalari" and m.chat.id == SUPER_ADMIN)
def ai_settings_menu(msg):
    m_time = get_setting('morning_time', '08:00')
    e_time = get_setting('evening_time', '18:00')
    prompt = get_setting('morning_prompt', 'Noma\'lum')

    text = (
        "⚙️ *AI Sozlamalari*\n\n"
        f"🌅 *Ertalabki matn vaqti:* `{m_time}`\n"
        f"🌃 *Kechki PDF test vaqti:* `{e_time}`\n\n"
        f"📝 *Ertalabki AI mavzusi (Prompt):*\n_{prompt[:150]}..._"
    )
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("⏰ Ertalabki vaqtni o'zgartirish"), types.KeyboardButton("⏰ Kechki vaqtni o'zgartirish"))
    kb.add(types.KeyboardButton("📝 Ertalabki mavzuni o'zgartirish"))
    kb.add(types.KeyboardButton("🚀 Ertalabkini hozir yuborish"), types.KeyboardButton("🚀 Kechkini hozir yuborish"))
    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
    safe_send(msg.chat.id, text, parse_mode="Markdown", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "⏰ Ertalabki vaqtni o'zgartirish" and m.chat.id == SUPER_ADMIN)
def change_m_time(msg):
    m = safe_send(msg.chat.id, "Ertalabki xabar yuboriladigan vaqtni HH:MM formatida yozing (Masalan: 08:30):", reply_markup=back_kb())
    bot.register_next_step_handler(m, lambda ms: save_setting(ms, 'morning_time', "Ertalabki vaqt"))

@bot.message_handler(func=lambda m: m.text == "⏰ Kechki vaqtni o'zgartirish" and m.chat.id == SUPER_ADMIN)
def change_e_time(msg):
    m = safe_send(msg.chat.id, "Kechki PDF test yuboriladigan vaqtni HH:MM formatida yozing (Masalan: 19:00):", reply_markup=back_kb())
    bot.register_next_step_handler(m, lambda ms: save_setting(ms, 'evening_time', "Kechki vaqt"))

@bot.message_handler(func=lambda m: m.text == "📝 Ertalabki mavzuni o'zgartirish" and m.chat.id == SUPER_ADMIN)
def change_prompt(msg):
    m = safe_send(msg.chat.id, "Sun'iy intellekt har kuni ertalab qanday mavzuda kontent yozishi kerakligini to'liq yozing:", reply_markup=back_kb())
    bot.register_next_step_handler(m, lambda ms: save_setting(ms, 'morning_prompt', "AI mavzusi"))

def save_setting(msg, key, name):
    if is_back(msg.text): return go_home(msg)
    new_val = msg.text.strip()
    set_setting(key, new_val)
    safe_send(msg.chat.id, f"✅ {name} muvaffaqiyatli o'zgartirildi: \n`{new_val}`", parse_mode="Markdown")
    ai_settings_menu(msg)

@bot.message_handler(func=lambda m: m.text == "🚀 Ertalabkini hozir yuborish" and m.chat.id == SUPER_ADMIN)
def test_morning_now(msg):
    safe_send(msg.chat.id, "⏳ Ertalabki AI matni tayyorlanib kanalga yuborilmoqda, biroz kuting...")
    threading.Thread(target=ai_morning_task).start()

@bot.message_handler(func=lambda m: m.text == "🚀 Kechkini hozir yuborish" and m.chat.id == SUPER_ADMIN)
def test_evening_now(msg):
    safe_send(msg.chat.id, "⏳ Kechki PDF test tuzilib kanalga yuborilmoqda, jarayon 30-40 soniya olishi mumkin...")
    threading.Thread(target=ai_evening_task).start()

# --- ESKI MAJBURIY A'ZOLIK VA BOSHQA FUNKSIYALAR ---
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

def extract_answers_list(raw_data):
    try:
        data = json.loads(raw_data)
        if isinstance(data, list): return [str(x).strip().lower() for x in data]
        elif isinstance(data, dict):
            if "answers" in data:
                ans = data["answers"]
                if isinstance(ans, list): return [str(x).strip().lower() for x in ans]
                elif isinstance(ans, dict): return [str(v).strip().lower() for k, v in sorted(ans.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else item[0])]
            else:
                ans_items = {k: v for k, v in data.items() if str(k).isdigit()}
                if ans_items: return [str(v).strip().lower() for k, v in sorted(ans_items.items(), key=lambda item: int(item[0]))]
                else: return [str(v).strip().lower() for k, v in sorted(data.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else str(item[0])) if k != "code"]
        return [str(data).strip().lower()]
    except Exception:
        text = raw_data.strip().lower()
        if "," in text: return [x.strip() for x in text.split(",")]
        else: return list(text)

def extract_bin_from_analysis(analysis_text):
    bin_str = ""
    if not analysis_text: return ""
    for char in analysis_text:
        if char == '✅': bin_str += '1'
        elif char == '❌': bin_str += '0'
    return bin_str

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    clean_old_data() 
    clear_state(msg.chat.id)
    m = safe_send(msg.chat.id, "🎉 Xush kelibsiz!\n\n✏️ To'liq ism va familiyangizni kiriting:", reply_markup=types.ReplyKeyboardRemove())
    if m: bot.register_next_step_handler(m, _register_user)

def _register_user(msg):
    name = msg.text.strip() if msg.text else ""
    if not name or len(name) > 100 or is_back(name):
        m = safe_send(msg.chat.id, "❌ Iltimos, faqat to'liq ism va familiyangizni kiriting:")
        if m: bot.register_next_step_handler(m, _register_user)
        return
    db_exec("INSERT OR REPLACE INTO users (user_id, name) VALUES (?,?)", (msg.chat.id, name))
    safe_send(msg.chat.id, f"✅ Saqlandi! Asosiy menyu, *{name}*:", parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

@bot.message_handler(func=lambda m: m.text == "🔙 Ortga qaytish")
def handle_back(msg):
    go_home(msg)

@bot.message_handler(commands=["edit"])
def handle_change_name(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    m = safe_send(msg.chat.id, "✏️ Yangi to'liq ism va familiyangizni kiriting:", reply_markup=types.ReplyKeyboardRemove())
    if m: bot.register_next_step_handler(m, _register_user)

@bot.message_handler(commands=["testlarim"])
@bot.message_handler(func=lambda m: m.text == "📊 Natijalarim")
def cmd_my_results(msg):
    if not is_subscribed(msg.chat.id): return prompt_sub(msg.chat.id)
    rows = db_fetch("SELECT code, score, total, created_at FROM results WHERE user_id=? ORDER BY id DESC LIMIT 25", (msg.chat.id,))
    if not rows:
        safe_send(msg.chat.id, "❌ Siz hali hech qanday test ishlamadingiz.", reply_markup=main_menu(msg.chat.id))
        return
    lines = ["📊 *Sizning natijalaringiz:*\n"]
    for i, row in enumerate(rows, 1):
        code, score, total, created_at = row[0], row[1], row[2], row[3]
        bar = progress_bar(score, total)
        lines.append(f"*{i}.* Kod: `{code}` — `{score}/{total}`\n{bar}\n_{created_at}_\n")
    safe_send(msg.chat.id, "\n".join(lines), parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

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

def recalculate_ms_item_weights_from_list(bins_list, total_q=55):
    n_users = len(bins_list)
    if n_users == 0: return [1.0] * total_q
    pass_rates = []
    for i in range(total_q):
        correct_count = sum(1 for b in bins_list if len(b) > i and b[i] == '1')
        p = correct_count / n_users
        p = max(0.05, min(0.95, p)) 
        pass_rates.append(p)
    logits = [math.log((1 - p) / p) for p in pass_rates]
    min_l, max_l = min(logits), max(logits)
    if max_l == min_l: return [1.0] * total_q
    weights = []
    for l in logits:
        w = 1.0 + ((l - min_l) / (max_l - min_l)) * 4.0
        weights.append(w)
    return weights

def recalculate_ms_item_weights(code, total_q=55):
    rows = db_fetch("SELECT answers_bin FROM rasch_answers WHERE test_code=?", (code,))
    bins_list = [r[0] for r in rows]
    return recalculate_ms_item_weights_from_list(bins_list, total_q)

def calculate_ms_final_score(user_answers_bin, item_weights):
    togri_soni = user_answers_bin.count('1')
    min_len = min(len(user_answers_bin), len(item_weights))
    if togri_soni == min_len and togri_soni > 0: return 100.0, "A+"
    if togri_soni == 0: return 0.0, "—"
    
    if togri_soni >= 42:
        step = (100.0 - 70.0) / (min_len - 42) if min_len > 42 else 30.0
        min_ball = 70.0 + (togri_soni - 42) * step
        max_ball = min_ball + step
        if max_ball > 100.0: max_ball = 100.0
    elif togri_soni >= 36:
        step = (69.9 - 65.0) / (41 - 36)
        min_ball = 65.0 + (togri_soni - 36) * step
        max_ball = min_ball + step
    elif togri_soni >= 30:
        step = (64.9 - 60.0) / (35 - 30)
        min_ball = 60.0 + (togri_soni - 30) * step
        max_ball = min_ball + step
    elif togri_soni >= 26:
        step = (59.9 - 55.0) / (29 - 26)
        min_ball = 55.0 + (togri_soni - 26) * step
        max_ball = min_ball + step
    elif togri_soni >= 21:
        step = (54.9 - 50.0) / (25 - 21)
        min_ball = 50.0 + (togri_soni - 21) * step
        max_ball = min_ball + step
    elif togri_soni >= 15:
        step = (49.9 - 46.0) / (20 - 15)
        min_ball = 46.0 + (togri_soni - 15) * step
        max_ball = min_ball + step
    else:
        base_min = (togri_soni / 15.0) * 45.9
        step = (45.9 / 15.0)
        min_ball = base_min
        max_ball = min_ball + step
        if max_ball > 45.9: max_ball = 45.9

    user_w_sum = sum(item_weights[i] for i in range(min_len) if user_answers_bin[i] == '1')
    sorted_w = sorted(item_weights[:min_len])
    min_w_sum = sum(sorted_w[:togri_soni])
    max_w_sum = sum(sorted_w[-togri_soni:])
    
    if max_w_sum == min_w_sum: ratio = 0.5 
    else: ratio = (user_w_sum - min_w_sum) / (max_w_sum - min_w_sum)
    ratio = max(0.0, min(1.0, ratio))
    
    yakuniy_ball = min_ball + ratio * (max_ball - min_ball)
    yakuniy_ball = round(yakuniy_ball, 1)
    
    if togri_soni < 15 or yakuniy_ball < 46.0: daraja = "—"
    elif 46.0 <= yakuniy_ball < 50.0: daraja = "C"
    elif 50.0 <= yakuniy_ball < 55.0: daraja = "C+"
    elif 55.0 <= yakuniy_ball < 60.0: daraja = "B"
    elif 60.0 <= yakuniy_ball < 65.0: daraja = "B+"
    elif 65.0 <= yakuniy_ball < 70.0: daraja = "A"
    else: daraja = "A+"
    return yakuniy_ball, daraja

def get_daraja(ball):
    if ball >= 70: return "A+"
    elif 65 <= ball < 70: return "A"
    elif 60 <= ball < 65: return "B+"
    elif 55 <= ball < 60: return "B"
    elif 50 <= ball < 55: return "C+"
    elif 46 <= ball < 50: return "C"
    else: return "—"

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
        safe_send(msg.chat.id, "⚠️ Siz bu testni allaqachon ishlagansiz!\nHar bir testga faqat *1 marta* javob yuborish mumkin.", reply_markup=main_menu(msg.chat.id))
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
        safe_send(msg.chat.id, "❌ Bu kod bo'yicha test topilmadi.", reply_markup=main_menu(msg.chat.id))
        return
    test_type, answers_raw, creator_id = test_info[0], test_info[1], test_info[2]
    if msg.chat.id != creator_id and msg.chat.id != SUPER_ADMIN:
        safe_send(msg.chat.id, "❌ Ushbu test natijalarini faqat uni yaratgan odam yuklab ololadi.", reply_markup=main_menu(msg.chat.id))
        return
    try:
        correct_list = json.loads(answers_raw)
        total_q = len(correct_list) if isinstance(correct_list, list) else len(answers_raw)
    except:
        total_q = len(answers_raw)

    rows = db_fetch("SELECT user_id, name, score, total, analysis_text, created_at FROM results WHERE code=? ORDER BY score DESC, created_at ASC", (code,))
    if not rows:
        safe_send(msg.chat.id, "❌ Bu test bo'yicha hech qanday natija topilmadi.", reply_markup=main_menu(msg.chat.id))
        return

    if test_type == "rush":
        try:
            output = io.StringIO()
            writer = csv.writer(output, delimiter=';')
            item_weights = recalculate_ms_item_weights(code, total_q)
            evaluated_students = []
            
            for r in rows:
                name, score, analysis_text = r[1], r[2], r[4]
                ans_bin = extract_bin_from_analysis(analysis_text)
                if not ans_bin or len(ans_bin) < total_q:
                    ans_bin = "1" * score + "0" * (total_q - score)
                ball, daraja = calculate_ms_final_score(ans_bin, item_weights)
                evaluated_students.append({"name": name, "score": score, "ball": ball, "daraja": daraja})
                
            evaluated_students.sort(key=lambda x: (x["ball"], x["score"]), reverse=True)
            writer.writerow(["O'rni", "Ism va Familiya", "Yakuniy MS Ball", "Sertifikat Darajasi", "To'g'ri javoblar"])
            lines = [f"📊 *{code}* - test natijalari (Dinamik MS reytingi):\n"]
            lines.append("_Natijalar bazadagi barcha javoblar asosida qayta tahlil qilindi va savollar vazniga ko'ra yangilandi._\n")

            for idx, st in enumerate(evaluated_students, 1):
                ball_val = st["ball"]
                safe_name = str(st["name"]).replace("_", "\\_").replace("*", "\\*")
                lines.append(f"*{idx}.* {safe_name} — {st['score']}/{total_q} ➪ *{ball_val} ball* ({st['daraja']})")
                writer.writerow([f"{idx}-o'rin", st["name"], ball_val, st["daraja"], f"{st['score']}/{total_q}"])

            result_text = "\n".join(lines)
            if len(result_text) > 4000:
                chunks = [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]
                for chunk in chunks: safe_send(msg.chat.id, chunk, parse_mode="Markdown")
            else:
                safe_send(msg.chat.id, result_text, parse_mode="Markdown")

            csv_text = output.getvalue()
            csv_bytes = '\ufeff'.encode('utf8') + csv_text.encode('utf8')
            bot.send_document(chat_id=msg.chat.id, document=(f"{code}_Dinamik_Natijalar.csv", csv_bytes), caption=f"📁 *{code}* - reytingi Excel faylda.", parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))
        except Exception as e:
            safe_send(msg.chat.id, f"❌ Xatolik yuz berdi:\n`{str(e)}`", reply_markup=main_menu(msg.chat.id))
    else:
        is_admin = (msg.chat.id == SUPER_ADMIN or msg.chat.id == creator_id)
        item_weights = []
        if is_admin:
            all_ans_bins = []
            for r in rows:
                ans_bin = extract_bin_from_analysis(r[4])
                if not ans_bin or len(ans_bin) < total_q: ans_bin = "1" * r[2] + "0" * (total_q - r[2])
                all_ans_bins.append(ans_bin)
            item_weights = recalculate_ms_item_weights_from_list(all_ans_bins, total_q)
            
        lines = [f"📊 *{code}* - test natijalari:\n"]
        for idx, r in enumerate(rows, 1):
            name, score, total = r[1], r[2], r[3]
            safe_name = str(name).replace("_", "\\_").replace("*", "\\*")
            if is_admin:
                ans_bin = extract_bin_from_analysis(r[4])
                if not ans_bin or len(ans_bin) < total_q: ans_bin = "1" * score + "0" * (total_q - score)
                ball, _ = calculate_ms_final_score(ans_bin, item_weights)
                lines.append(f"*{idx}.* {safe_name} — {score}/{total} ta to'g'ri *(MS: {ball} ball)*")
            else:
                lines.append(f"*{idx}.* {safe_name} — {score}/{total} ta to'g'ri")
                
        result_text = "\n".join(lines)
        if len(result_text) > 4000:
            chunks = [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]
            for chunk in chunks: safe_send(msg.chat.id, chunk, parse_mode="Markdown")
            safe_send(msg.chat.id, "✅ Barcha natijalar yuborildi", reply_markup=main_menu(msg.chat.id))
        else:
            safe_send(msg.chat.id, result_text, parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

@bot.message_handler(content_types=["web_app_data"])
def handle_web_app(msg):
    raw_data = msg.web_app_data.data.strip()
    state = get_state(msg.chat.id)

    if state.get("action") == "admin_save":
        test_type = state.get("test_type", "pdf")
        test_code = state.get("code", "Noma'lum")
        try:
            data = json.loads(raw_data)
            if isinstance(data, dict) and "code" in data: test_code = str(data["code"]).strip().upper()
        except: pass
        if not test_code:
            safe_send(msg.chat.id, "❌ Xatolik: Test kodi kiritilmadi!", reply_markup=main_menu(msg.chat.id))
            clear_state(msg.chat.id)
            return
        answers_list = extract_answers_list(raw_data)
        answers_json_str = json.dumps(answers_list)
        db_exec("INSERT OR REPLACE INTO tests (code, creator_id, answers, deadline, type, link) VALUES (?,?,?,?,?,?)",
                (test_code, msg.chat.id, answers_json_str, state.get("deadline", "0"), test_type, ""))
        clear_state(msg.chat.id)
        safe_send(msg.chat.id, f"✅ Test bazaga muvaffaqiyatli saqlandi!\n🔢 Kod: `{test_code}`", parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))
        return

    if state.get("action") == "student_solve":
        user_name = state.get("name", "Noma'lum")
        code = state.get("code", "Noma'lum")
        test_type = state.get("type", "pdf")
        correct_answers_raw = state.get("correct", "")
        try:
            correct_answers = json.loads(correct_answers_raw)
            if not isinstance(correct_answers, list): correct_answers = list(str(correct_answers_raw).lower())
        except:
            correct_answers = list(str(correct_answers_raw).lower())

        total_q = len(correct_answers)
        user_answers = extract_answers_list(raw_data)
        while len(user_answers) < total_q: user_answers.append("")

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
                analysis_text += f"{i+1}.❌  "
            if (i + 1) % 5 == 0: analysis_text += "\n"

        db_exec("INSERT INTO results (user_id, name, code, score, total, analysis_text) VALUES (?,?,?,?,?,?)",
                (msg.chat.id, user_name, code, score, total_q, analysis_text))

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
        safe_user_name = str(user_name).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
        safe_code = str(code).replace("_", "\\_").replace("*", "\\*")
        result_msg = (
            f"📊 *Test yakunlandi!*\n\n"
            f"👤 *O'quvchi:* {safe_user_name}\n"
            f"🔢 *Test kodi:* {safe_code}\n"
            f"🎯 *To'g'ri javoblar:* {score} / {total_q} ta\n"
            f"📈 *To'plangan ball:* {final_ms_ball_text}\n"
            f"📜 *Sertifikat darajasi:* {sertifikat_daraja_text}\n\n"
        )
        if test_type == "rush": result_msg += "⚠️ _MS (Rasch) tizimidagi yakuniy ballaringiz barcha o'quvchilar testni ishlab bo'lgach, o'qituvchi tomonidan natijalar e'lon qilinganda ma'lum bo'ladi._\n\n"
        result_msg += f"📝 *Batafsil tahlil:*\n{analysis_text}"
        safe_send(msg.chat.id, result_msg, parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))
        return
    safe_send(msg.chat.id, "✅ Ma'lumot qabul qilindi.", reply_markup=main_menu(msg.chat.id))

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update = telebot.types.Update.de_json(request.get_data(as_text=True))
    bot.process_new_updates([update])
    return "", 200

@app.route("/")
def index():
    return "Bot faol va server ishlamoqda!", 200

# --- Webhookni Gunicorn uchun global darajada o'rnatish ---
try:
    bot.remove_webhook()
    if RAILWAY_URL:
        # URL oxirida "/" qolib ketmasligi uchun rstrip ishlatamiz
        webhook_url = f"{RAILWAY_URL.rstrip('/')}/{TOKEN}"
        bot.set_webhook(url=webhook_url)
        log.info(f"✅ Webhook muvaffaqiyatli o'rnatildi: {webhook_url}")
    else:
        log.warning("⚠️ RAILWAY_URL topilmadi. Webhook o'rnatilmadi!")
except Exception as e:
    log.error(f"❌ Webhook o'rnatishda xatolik: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
