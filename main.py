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
TOKEN       = os.environ.get("BOT_TOKEN", "8505975357:AAEtUiLlhjg7joD-iJN2JPqj0fKmKyIYpw0")
SUPER_ADMIN = int(os.environ.get("ADMIN_ID", "5541008041"))
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://eshoonqulov-math-testbot.netlify.app/")
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
        name     TEXT NOT NULL,
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

@bot.message_handler(commands=["info"])
def cmd_info(msg):
    safe_send(msg.chat.id,
        "ℹ️ *Math Test Bot*\n\n"
        "Bu bot orqali:\n"
        "• 📝 Test ishlashingiz\n"
        "• 📊 Natijalaringizni ko'rishingiz mumkin\n\n"
        "_Created by Eshonqulov Akobir_",
        parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

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

# ─────────────────────────────────────────
#  RASCH MATEMATIK HISOB-KITOB
# ─────────────────────────────────────────
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
        if abs(diff) < 0.01:
            break
        if info_sum > 0:
            theta -= diff / info_sum
            
    return theta

# ─────────────────────────────────────────
#  TEST ISHLASH BO'LIMI
# ─────────────────────────────────────────
@bot.message_handler(commands=["test"])
@bot.message_handler(func=lambda m: m.text in ["📝 Test ishlash", "📈 Rush model Test"])
def cmd_student(msg):
    user = db_fetch("SELECT name FROM users WHERE user_id=?", (msg.chat.id,), one=True)
    if not user:
        return cmd_start(msg)
    set_state(msg.chat.id, {"action": "student_solve", "name": user[0]})
    m = safe_send(msg.chat.id, "🔢 Test kodini kiriting:", reply_markup=back_kb())
    if m:
        bot.register_next_step_handler(m, _student_code_entered)

def _student_code_entered(msg):
    if is_back(msg.text):
        return go_home(msg)
    code = msg.text.strip().upper()

    count = db_fetch(
        "SELECT COUNT(*) FROM results WHERE user_id=? AND code=?",
        (msg.chat.id, code), one=True
    )
    if count and count[0] >= 2:
        safe_send(msg.chat.id,
                  "⚠️ Siz bu testni allaqachon *2 marta* ishlagansiz!\n"
                  "Boshqa test kodini kiriting.",
                  reply_markup=main_menu(msg.chat.id))
        return

    row = db_fetch(
        "SELECT answers, deadline, type, link FROM tests WHERE code=?",
        (code,), one=True
    )
    if not row:
        m = safe_send(msg.chat.id,
                      "❌ Bunday kod topilmadi. Qaytadan kiriting:",
                      reply_markup=back_kb())
        if m:
            bot.register_next_step_handler(m, _student_code_entered)
        return

    answers, deadline, test_type, html_link = row
    deadline = deadline or "0"

    if deadline != "0":
        try:
            if get_uz_now() > datetime.strptime(deadline, "%Y-%m-%d %H:%M"):
                safe_send(msg.chat.id,
                          f"⛔️ Test yopilgan!\n📅 Muddat: {deadline} gacha edi.",
                          reply_markup=main_menu(msg.chat.id))
                return
        except ValueError:
            pass

    update_state(msg.chat.id,
                 code=code,
                 correct=answers,
                 type=test_type,
                 html_link=html_link)

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # TELEGRAM KESHINI MAJBURIY YANGILASH UCHUN KESH-BASTER (&v=3) QO'SHILDI
    if test_type == "html":
        final_link = f"{html_link}&v=3" if "?" in html_link else f"{html_link}?v=3"
        kb.add(types.KeyboardButton("📱 Testni boshlash", web_app=types.WebAppInfo(url=final_link)))
    elif test_type == "rush":
        kb.add(types.KeyboardButton("📱 Rush Testni boshlash", web_app=types.WebAppInfo(url=f"{RUSH_WEB_APP_URL}?count={len(answers)}&v=3")))
    else:
        kb.add(types.KeyboardButton("📱 Javoblarni belgilash", web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?count={len(answers)}&v=3")))
        
    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
    
    test_info_msg = f"✅ *Test topildi!*\n🔢 Kod: `{code}`\n"
    if test_type == "rush":
        test_info_msg += "⚡️ *Bu test Rasch model (BMBA tizimi) orqali baholanadi!*\n⚠️ *Chegara:* Kamida 15 ta to'g'ri javob topilishi shart.\n\n"
    test_info_msg += "Boshlash uchun tugmani bosing 👇"

    safe_send(msg.chat.id, test_info_msg, parse_mode="Markdown", reply_markup=kb)

def _generate_and_send_pdf(chat_id, user_name, score, total, details):
    filename = f"Natija_{chat_id}_{int(get_uz_now().timestamp())}.pdf"
    try:
        c = canvas.Canvas(filename, pagesize=letter)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, 750, "Akobir ustoz - Rasch Modeli Test Natijalari")
        c.setLineWidth(1)
        c.line(50, 735, 550, 735)
        
        c.setFont("Helvetica", 12)
        c.drawString(50, 700, f"O'quvchi: {user_name}")
        c.drawString(50, 680, f"Umumiy natija: {score} / {total}")
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, 640, "Tahlil va Reyting:")
        
        c.setFont("Helvetica", 10)
        y = 620
        for line in details:
            c.drawString(50, y, str(line))
            y -= 20
            if y < 50:
                c.showPage()
                c.setFont("Helvetica", 10)
                y = 750
                
        c.save()
        
        with open(filename, "rb") as f:
            bot.send_document(
                chat_id, 
                f, 
                caption=f"📈 *{user_name}*, maxsus test natijangiz tayyor!\nNatija: {score}/{total}", 
                parse_mode="Markdown"
            )
            
        if os.path.exists(filename):
            os.remove(filename)
            
    except Exception as e:
        log.error("PDF yaratishda xato: %s", e)
        safe_send(chat_id, "❌ Natijani faylga yuklashda xatolik yuz berdi.")

# ─────────────────────────────────────────
#  WEB APP NATIJALARNI QABUL QILISH
# ─────────────────────────────────────────
@bot.message_handler(content_types=["web_app_data"])
def handle_web_app(msg):
    try:
        raw_data = msg.web_app_data.data.strip()
        
        try:
            data = json.loads(raw_data)
            if data.get("type") == "rush_test":
                _generate_and_send_pdf(msg.chat.id, msg.from_user.full_name, data.get("score", 0), data.get("total", 0), data.get("details", []))
                return
        except ValueError:
            pass 

        state  = get_state(msg.chat.id)
        action = state.get("action")

        if action == "admin_save":
            test_type = state.get("test_type", "pdf")
            db_exec(
                "INSERT OR REPLACE INTO tests (code, answers, deadline, type, link) "
                "VALUES (?,?,?,?,?)",
                (state["code"], raw_data.lower(), state.get("deadline", "0"), test_type, "")
            )
            clear_state(msg.chat.id)
            safe_send(msg.chat.id, f"✅ {test_type.upper()} testi muvaffaqiyatli saqlandi!",
                      reply_markup=main_menu(msg.chat.id))
            return

        if action == "student_solve":
            test_type = state.get("type", "pdf")
            name      = state.get("name", "Noma'lum")
            code      = state.get("code", "?")

            row = db_fetch("SELECT deadline FROM tests WHERE code=?", (code,), one=True)
            if row and row[0] != "0":
                try:
                    if get_uz_now() > datetime.strptime(row[0], "%Y-%m-%d %H:%M"):
                        safe_send(msg.chat.id,
                                  f"⛔️ Javoblar qabul qilinmadi!\n📅 Sababi: ishlash muddati ({row[0]}) tugagan.",
                                  reply_markup=main_menu(msg.chat.id))
                        clear_state(msg.chat.id)
                        return
                except ValueError:
                    pass

            if test_type in ["pdf", "rush"]:
                correct  = state.get("correct", "").lower()
                received = raw_data.lower()
                total    = len(correct)
                
                bin_str = ""
                score = 0
                analysis = []
                for i, (s, c) in enumerate(zip(received, correct), 1):
                    if s == c:
                        bin_str += "1"
                        score += 1
                        analysis.append(f"{i}✅")
                    else:
                        bin_str += "0"
                        analysis.append(f"{i}❌({c.upper()})")
                        
                grid = "\n".join(" ".join(analysis[i:i+5]) for i in range(0, len(analysis), 5))
                bar = progress_bar(score, total)

                if test_type == "rush":
                    db_exec("INSERT INTO rasch_answers (test_code, answers_bin) VALUES (?, ?)", (code, bin_str))
                    
                    b_items = get_rasch_item_difficulties(code, total)
                    theta = calculate_rasch_theta(score, b_items)
                    
                    t_score = 50 + (15 * theta)
                    t_score = max(0, min(100, round(t_score, 2)))

                    if score < 15:
                        grade = "Natija yo'q (O'tmadi) ❌"
                    else:
                        if t_score >= 70:
                            grade = "A+ Daraja (Eng oliy) 🥇"
                        elif t_score >= 65:
                            grade = "A Daraja (Oliy) 🥇"
                        elif t_score >= 60:
                            grade = "B+ Daraja (Yuqori) 🥈"
                        elif t_score >= 55:
                            grade = "B Daraja (Yaxshi) 🥈"
                        elif t_score >= 50:
                            grade = "C+ Daraja (O'rta) 🥉"
                        elif t_score >= 46:
                            grade = "C Daraja (Qoniqarli) 🥉"
                        else:
                            grade = "Natija yo'q (O'tmadi) ❌"

                    result_text = (
                        f"👤 *{name}*\n"
                        f"🔢 Kod: `{code}` (Rasch Baholash)\n"
                        f"📊 To'g'ri javoblar: *{score}/{total}*\n"
                        f"📈 Umumiy to'plagan bali: *{t_score}*\n"
                        f"🎓 Sertifikat darajasi: *{grade}*\n"
                        f"{bar}\n\n"
                        f"📋 *Tahlil:*\n{grid}"
                    )
                    
                    pdf_details = [
                        f"Test kodi: {code} (Rasch Model)",
                        f"To'g'ri javoblar: {score} ta",
                        f"Umumiy to'plagan bali: {t_score}",
                        f"Sertifikat darajasi: {grade}",
                        " ",
                        "Javoblar tahlili:"
                    ] + [ " ".join(analysis[i:i+5]) for i in range(0, len(analysis), 5) ]
                    
                    _generate_and_send_pdf(msg.chat.id, name, score, total, pdf_details)

                else:
                    result_text = (
                        f"👤 *{name}*\n"
                        f"🔢 Kod: `{code}`\n"
                        f"📊 Natija: *{score}/{total}*\n"
                        f"{bar}\n\n"
                        f"📋 *Tahlil:*\n{grid}"
                    )

            elif test_type == "html":
                parts = raw_data.split("|")
                if len(parts) >= 2:
                    try:
                        score = int(parts[0])
                        total = int(parts[1])
                    except ValueError:
                        score, total = 0, 0
                    grid = parts[2] if len(parts) > 2 else "✅ Test yakunlandi"
                else:
                    score, total, grid = 0, 0, "⚠️ Natija formati noto'g'ri"
                bar = progress_bar(score, total)
                result_text = (
                    f"👤 *{name}*\n"
                    f"🔢 Kod: `{code}`\n"
                    f"📊 Natija: *{score}/{total}*\n"
                    f"{bar}\n\n"
                    f"📋 *Tahlil:*\n{grid}"
                )
            else:
                return

            db_exec(
                "INSERT INTO results (user_id, name, code, score, total, analysis_text) "
                "VALUES (?,?,?,?,?,?)",
                (msg.chat.id, name, code, score, total, result_text)
            )
            clear_state(msg.chat.id)

            safe_send(msg.chat.id, result_text,
                      parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

            safe_send(SUPER_ADMIN, f"🔔 *Yangi natija!*\n\n{result_text}",
                      parse_mode="Markdown")

    except Exception as e:
        log.exception("web_app_data xatolik: %s", e)
        safe_send(msg.chat.id, "⚠️ Xatolik yuz berdi. Boshidan urinib ko'ring.",
                  reply_markup=main_menu(msg.chat.id))

# ─────────────────────────────────────────
#  ADMIN: PDF TEST QO'SHISH
# ─────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "➕ Yangi test qo'shish")
def admin_add_pdf(msg):
    if not is_admin(msg.chat.id):
        return
    m = safe_send(msg.chat.id,
                  "Kod va savol sonini kiriting\n_(Misol: 701 30)_",
                  parse_mode="Markdown", reply_markup=back_kb())
    if m:
        bot.register_next_step_handler(m, _admin_base_code, "pdf")

# ─────────────────────────────────────────
#  ADMIN: RUSH TEST QO'SHISH
# ─────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "➕ Rush test qo'shish")
def admin_add_rush(msg):
    if not is_admin(msg.chat.id):
        return
    m = safe_send(msg.chat.id,
                  "Rasch modeli asosida baholanuvchi kod va savol sonini kiriting\n_(Misol: 801 40)_",
                  parse_mode="Markdown", reply_markup=back_kb())
    if m:
        bot.register_next_step_handler(m, _admin_base_code, "rush")

def _admin_base_code(msg, test_type):
    if is_back(msg.text):
        return go_home(msg)
    try:
        parts = msg.text.strip().split()
        code  = parts[0].upper()
        count = int(parts[1])
        set_state(msg.chat.id, {"action": "admin_save_deadline", "code": code, "count": count, "test_type": test_type})
        m = safe_send(msg.chat.id,
                      "📅 Yopilish vaqtini kiriting\n_(Misol: 2026-12-31 18:00)_ yoki *0* (cheksiz)",
                      parse_mode="Markdown", reply_markup=back_kb())
        if m:
            bot.register_next_step_handler(m, _admin_base_deadline)
    except (IndexError, ValueError):
        m = safe_send(msg.chat.id, "❌ Noto'g'ri format!\n_(Misol: 701 30)_",
                      parse_mode="Markdown")
        if m:
            bot.register_next_step_handler(m, _admin_base_code, test_type)

def _admin_base_deadline(msg):
    if is_back(msg.text):
        return go_home(msg)
    deadline = msg.text.strip()
    if deadline != "0":
        try:
            datetime.strptime(deadline, "%Y-%m-%d %H:%M")
        except ValueError:
            m = safe_send(msg.chat.id, "❌ Noto'g'ri format! (YYYY-MM-DD HH:MM) yoki 0:")
            if m:
                bot.register_next_step_handler(m, _admin_base_deadline)
            return

    update_state(msg.chat.id, deadline=deadline, action="admin_save")
    state = get_state(msg.chat.id)
    kb    = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # BU YERGA HAM KESH FILTRI QO'SHILDI
    kb.add(types.KeyboardButton(
        "🛠 Javoblarni kiritish",
        web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?count={state['count']}&v=3")
    ))
    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))
    
    t_name = "Rush (Rasch)" if state.get("test_type") == "rush" else "PDF"
    safe_send(msg.chat.id,
              f"✅ *Kod:* `{state['code']}` ({t_name})\n📅 *Muddat:* {deadline}\n\n"
              "Tugmani bosib to'g'ri javoblarni kiriting 👇",
              parse_mode="Markdown", reply_markup=kb)

# ─────────────────────────────────────────
#  ADMIN: HTML TEST QO'SHISH
# ─────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "➕ HTML test qo'shish")
def admin_add_html(msg):
    if not is_admin(msg.chat.id):
        return
    m = safe_send(msg.chat.id,
                  "Kod va havolani kiriting\n_(Misol: 901 https://example.com)_\n\n"
                  "⚠️ Havola Netlify yoki boshqa saytdan olingan to'liq URL bo'lishi kerak",
                  parse_mode="Markdown", reply_markup=back_kb())
    if m:
        bot.register_next_step_handler(m, _admin_html_link)

def _admin_html_link(msg):
    if is_back(msg.text):
        return go_home(msg)
    try:
        parts = msg.text.strip().split(maxsplit=1)
        code  = parts[0].upper()
        link  = parts[1].strip()
        if not link.startswith("http"):
            raise ValueError("URL noto'g'ri")
        set_state(msg.chat.id, {"code": code, "link": link})
        m = safe_send(msg.chat.id,
                      "📅 Yopilish vaqti _(YYYY-MM-DD HH:MM)_ yoki *0*:",
                      parse_mode="Markdown", reply_markup=back_kb())
        if m:
            bot.register_next_step_handler(m, _admin_html_save)
    except (IndexError, ValueError):
        m = safe_send(msg.chat.id,
                      "❌ Noto'g'ri format!\n_(Misol: 901 https://example.netlify.app)_",
                      parse_mode="Markdown")
        if m:
            bot.register_next_step_handler(m, _admin_html_link)

def _admin_html_save(msg):
    if is_back(msg.text):
        return go_home(msg)
    state    = get_state(msg.chat.id)
    deadline = msg.text.strip()
    if deadline != "0":
        try:
            datetime.strptime(deadline, "%Y-%m-%d %H:%M")
        except ValueError:
            m = safe_send(msg.chat.id, "❌ Noto'g'ri format! (YYYY-MM-DD HH:MM) yoki 0:")
            if m:
                bot.register_next_step_handler(m, _admin_html_save)
            return
    db_exec(
        "INSERT OR REPLACE INTO tests (code, answers, deadline, type, link) VALUES (?,?,?,?,?)",
        (state["code"], "", deadline, "html", state["link"])
    )
    clear_state(msg.chat.id)
    safe_send(msg.chat.id,
              f"✅ HTML test saqlandi!\n🔢 Kod: `{state['code']}`\n🔗 Link: {state['link']}",
              parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

# ─────────────────────────────────────────
#  ADMIN: NATIJALARNI KO'RISH (JADVAL BILAN)
# ─────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "📊 Natijalarni olish")
def admin_get_results(msg):
    if not is_admin(msg.chat.id):
        return
    m = safe_send(msg.chat.id, "🔢 Test kodini kiriting:", reply_markup=back_kb())
    if m:
        bot.register_next_step_handler(m, _admin_show_results)

def _admin_show_results(msg):
    if is_back(msg.text):
        return go_home(msg)
    code = msg.text.strip().upper()
    
    test_row = db_fetch("SELECT type, answers FROM tests WHERE code=?", (code,), one=True)
    if not test_row:
        safe_send(msg.chat.id, f"❌ `{code}` kodi bo'yicha test topilmadi.",
                  reply_markup=main_menu(msg.chat.id))
        return
        
    test_type, answers = test_row
    rows = db_fetch(
        "SELECT name, score, total FROM results WHERE code=? ORDER BY score DESC",
        (code,)
    )
    if not rows:
        safe_send(msg.chat.id, f"❌ `{code}` kodi bo'yicha hali natija yo'q.",
                  reply_markup=main_menu(msg.chat.id))
        return

    if test_type == "rush":
        total_q = len(answers)
        b_items = get_rasch_item_difficulties(code, total_q)
        
        lines = [f"📊 *{code}* (Rush model) natijalari — jami: {len(rows)} ta\n"]
        lines.append("`Ism Familiya       | To'g'ri | Ball  | Daraja`")
        lines.append("`---------------------------------------------`")
        for i, (name, score, total) in enumerate(rows, 1):
            theta = calculate_rasch_theta(score, b_items)
            t_score = max(0, min(100, round(50 + (15 * theta), 2)))
            
            if score < 15:
                grade = "O'tmadi"
            else:
                if t_score >= 70:
                    grade = "A+"
                elif t_score >= 65:
                    grade = "A"
                elif t_score >= 60:
                    grade = "B+"
                elif t_score >= 55:
                    grade = "B"
                elif t_score >= 50:
                    grade = "C+"
                elif t_score >= 46:
                    grade = "C"
                else:
                    grade = "O'tmadi"
                    
            padded_name = (name[:17] + "..") if len(name) > 17 else name.ljust(19)
            lines.append(f"`{i}. {padded_name} | {str(score).rjust(2)}/{total_q}   | {t_score:<5} | {grade}`")
    else:
        lines = [f"📊 *{code}* natijalari — jami: {len(rows)} ta\n"]
        for i, (name, score, total) in enumerate(rows, 1):
            bar = progress_bar(score, total)
            lines.append(f"{i}. *{name}* — `{score}/{total}`\n{bar}\n")

    full = "\n".join(lines)
    for chunk in [full[i:i+4000] for i in range(0, len(full), 4000)]:
        safe_send(msg.chat.id, chunk, parse_mode="Markdown",
                  reply_markup=main_menu(msg.chat.id))

# ─────────────────────────────────────────
#  SUPER ADMIN: ADMINLAR BOSHQARUVI
# ─────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "👥 Adminlar boshqaruvi")
def admin_management(msg):
    if not is_super_admin(msg.chat.id):
        return
    admins = db_fetch("SELECT user_id, name, added_at FROM admins ORDER BY added_at DESC")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("➕ Admin qo'shish"),
        types.KeyboardButton("❌ Admin o'chirish"),
    )
    kb.add(types.KeyboardButton("🔙 Ortga qaytish"))

    if not admins:
        text = "👥 *Adminlar ro'yxati*\n\nHozircha qo'shimcha admin yo'q."
    else:
        lines = ["👥 *Adminlar ro'yxati:*\n"]
        for i, (uid, name, added_at) in enumerate(admins, 1):
            lines.append(f"{i}. *{name}*\n   ID: `{uid}`\n   📅 {added_at}\n")
        text = "\n".join(lines)

    safe_send(msg.chat.id, text, parse_mode="Markdown", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "➕ Admin qo'shish")
def add_admin_start(msg):
    if not is_super_admin(msg.chat.id):
        return
    m = safe_send(msg.chat.id,
                  "👤 Yangi admin *Telegram ID* sini kiriting:\n\n"
                  "_(Foydalanuvchi @userinfobot ga /start yuborsа ID ni oladi)_",
                  parse_mode="Markdown", reply_markup=back_kb())
    if m:
        set_state(msg.chat.id, {"action": "add_admin_id"})
        bot.register_next_step_handler(m, _add_admin_id)

def _add_admin_id(msg):
    if is_back(msg.text):
        return go_home(msg)
    try:
        new_id = int(msg.text.strip())
    except ValueError:
        m = safe_send(msg.chat.id, "❌ ID faqat raqamdan iborat bo'lishi kerak. Qaytadan:")
        if m:
            bot.register_next_step_handler(m, _add_admin_id)
        return
    if new_id == SUPER_ADMIN:
        safe_send(msg.chat.id, "⚠️ Bu asosiy admin ID si!",
                  reply_markup=main_menu(msg.chat.id))
        return
    existing = db_fetch("SELECT user_id FROM admins WHERE user_id=?", (new_id,), one=True)
    if existing:
        safe_send(msg.chat.id, "⚠️ Bu foydalanuvchi allaqachon admin!",
                  reply_markup=main_menu(msg.chat.id))
        return
    update_state(msg.chat.id, new_admin_id=new_id)
    m = safe_send(msg.chat.id,
                  f"ID: `{new_id}`\n\n✏️ Bu adminning ismini kiriting:",
                  parse_mode="Markdown", reply_markup=back_kb())
    if m:
        bot.register_next_step_handler(m, _add_admin_name)

def _add_admin_name(msg):
    if is_back(msg.text):
        return go_home(msg)
    state = get_state(msg.chat.id)
    new_id = state.get("new_admin_id")
    name   = msg.text.strip()
    if not name or len(name) > 100:
        m = safe_send(msg.chat.id, "❌ Ism noto'g'ri. Qaytadan kiriting:")
        if m:
            bot.register_next_step_handler(m, _add_admin_name)
        return
    db_exec("INSERT OR REPLACE INTO admins (user_id, name) VALUES (?,?)", (new_id, name))
    clear_state(msg.chat.id)
    try:
        bot.send_message(new_id,
                         "🎉 Siz botga *admin* sifatida qo'shildingiz!\n"
                         "Endi test qo'shish va natijalarni ko'rish imkoniyatingiz bor.\n\n"
                         "/start bosing.",
                         parse_mode="Markdown")
    except Exception:
        pass
    safe_send(msg.chat.id,
              f"✅ *{name}* (ID: `{new_id}`) admin qilindi!",
              parse_mode="Markdown", reply_markup=main_menu(msg.chat.id))

@bot.message_handler(func=lambda m: m.text == "❌ Admin o'chirish")
def remove_admin_start(msg):
    if not is_super_admin(msg.chat.id):
        return
    admins = db_fetch("SELECT user_id, name FROM admins ORDER BY added_at DESC")
    if not admins:
        safe_send(msg.chat.id, "❌ O'chirish uchun admin yo'q.",
                  reply_markup=main_menu(msg.chat.id))
        return
    kb = types.InlineKeyboardMarkup()
    for uid, name in admins:
        kb.add(types.InlineKeyboardButton(
            f"❌ {name} (ID: {uid})",
            callback_data=f"del_admin:{uid}"
        ))
    safe_send(msg.chat.id, "👇 O'chirmoqchi bo'lgan adminni tanlang:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("del_admin:"))
def remove_admin_confirm(call):
    if not is_super_admin(call.message.chat.id):
        return
    uid = int(call.data.split(":")[1])
    row = db_fetch("SELECT name FROM admins WHERE user_id=?", (uid,), one=True)
    if not row:
        bot.answer_callback_query(call.id, "Admin topilmadi!")
        return
    name = row[0]
    db_exec("DELETE FROM admins WHERE user_id=?", (uid,))
    try:
        bot.send_message(uid, "⚠️ Sizning admin huquqingiz bekor qilindi.")
    except Exception:
        pass
    bot.answer_callback_query(call.id, f"✅ {name} o'chirildi!")
    bot.edit_message_text(
        f"✅ *{name}* (ID: `{uid}`) admin ro'yxatidan o'chirildi.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown"
    )
    safe_send(call.message.chat.id, "🏠 Asosiy menyu:",
              reply_markup=main_menu(call.message.chat.id))

# ─────────────────────────────────────────
#  WEBHOOK
# ─────────────────────────────────────────
@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.content_type == "application/json":
        update = telebot.types.Update.de_json(request.get_data(as_text=True))
        bot.process_new_updates([update])
        return "", 200
    return "", 403

@app.route("/")
def health_check():
    return "✅ Bot ishlayapti!", 200

def setup_webhook():
    if not RAILWAY_URL:
        log.warning("⚠️ RAILWAY_URL o'rnatilmagan — webhook o'rnatilmadi")
        return
    webhook_url = f"{RAILWAY_URL.rstrip('/')}/{TOKEN}"
    try:
        bot.remove_webhook()
        bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "web_app_data", "callback_query"]
        )
        log.info("✅ Webhook o'rnatildi: %s", webhook_url)
    except Exception as e:
        log.error("❌ Webhook o'rnatishda xato: %s", e)

setup_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
