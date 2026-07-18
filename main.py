import os
import json
import logging
import sqlite3
import threading
import csv
import io
from datetime import datetime
import telebot
from telebot import types
from flask import Flask, request

# --- Konfiguratsiya ---
TOKEN = os.environ.get("BOT_TOKEN", "8505975357:AAEtUiLlhjg7joD-iJN2JPqj0fKmKyIYpw0")
SUPER_ADMIN = int(os.environ.get("ADMIN_ID", "5541008041"))
WEB_APP_URL = "https://eshoonqulov-math-testbot.netlify.app/"
RUSH_WEB_APP_URL = "https://lucent-medovik-bd159a.netlify.app/"
DB_PATH = "testlar_bazasi.db"
PORT = int(os.environ.get("PORT", 5000))

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- State Management (Foydalanuvchi holatini saqlash) ---
_states_lock = threading.Lock()
_user_states = {}

def get_state(chat_id):
    with _states_lock: return _user_states.get(chat_id, {})

def set_state(chat_id, data):
    with _states_lock: _user_states[chat_id] = data

def clear_state(chat_id):
    with _states_lock: _user_states.pop(chat_id, None)

# --- Baza sozlari ---
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS tests (code TEXT PRIMARY KEY, answers TEXT, type TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, code TEXT, score INTEGER, total INTEGER, analysis_text TEXT, created_at TEXT DEFAULT (datetime('now','+5 hours')))")
        conn.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()

init_db()

# --- Yordamchi funksiyalar ---
def is_admin(chat_id):
    if int(chat_id) == SUPER_ADMIN: return True
    with db_conn() as conn:
        return conn.execute("SELECT 1 FROM admins WHERE user_id=?", (chat_id,)).fetchone() is not None

# --- Asosiy Menyular ---
@bot.message_handler(commands=["start"])
def start(msg):
    bot.send_message(msg.chat.id, "Xush kelibsiz! Ism-familiyangizni kiriting:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, register)

def register(msg):
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO users (user_id, name) VALUES (?,?)", (msg.chat.id, msg.text))
        conn.commit()
    bot.send_message(msg.chat.id, "Saqlandi! Bosh menyu:", reply_markup=main_menu(msg.chat.id))

def main_menu(chat_id):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📝 Test ishlash", "📈 Rush model Test", "📊 Natijalarim")
    if is_admin(chat_id):
        kb.add("➕ Yangi test qo'shish", "➕ Rush test qo'shish")
        kb.add("📊 Natijalarni olish", "👥 Adminlar boshqaruvi")
    return kb

# --- Admin: Yangi test qo'shish ---
@bot.message_handler(func=lambda m: m.text in ["➕ Yangi test qo'shish", "➕ Rush test qo'shish"])
def admin_add_test(msg):
    test_type = "rush" if "Rush" in msg.text else "standart"
    m = bot.send_message(msg.chat.id, "Kod va savollar sonini kiriting (Masalan: 00 55):")
    bot.register_next_step_handler(m, process_code, test_type)

def process_code(msg, test_type):
    try:
        parts = msg.text.strip().split()
        code, count = parts[0].upper(), int(parts[1])
        
        # Holatni saqlab qo'yamiz
        set_state(msg.chat.id, {"action": "admin_adding", "code": code, "type": test_type})
        
        target_url = RUSH_WEB_APP_URL if test_type == "rush" else WEB_APP_URL
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🛠 Javoblarni kiritish", web_app=types.WebAppInfo(url=f"{target_url}?count={count}&code={code}")))
        bot.send_message(msg.chat.id, f"Kod: {code}. Savollar: {count}. Tugmani bosing:", reply_markup=kb)
    except Exception as e:
        bot.send_message(msg.chat.id, "Xato format! Yana urinib ko'ring (Masalan: 00 55).")

# --- O'quvchi: Test ishlash ---
@bot.message_handler(func=lambda m: m.text in ["📝 Test ishlash", "📈 Rush model Test"])
def student_start(msg):
    test_type = "rush" if "Rush" in msg.text else "standart"
    m = bot.send_message(msg.chat.id, "Test kodini kiriting:")
    bot.register_next_step_handler(m, student_input, test_type)

def student_input(msg, test_type):
    code = msg.text.strip().upper()
    with db_conn() as conn:
        row = conn.execute("SELECT answers FROM tests WHERE code=?", (code,)).fetchone()
    
    if not row:
        bot.send_message(msg.chat.id, "❌ Test bazada topilmadi.")
        return
        
    count = len(row[0]) # Muammoning tub yechimi: uzunlik faqat bazadan olinadi!
    set_state(msg.chat.id, {"action": "student_solving", "code": code, "type": test_type})
    
    target_url = RUSH_WEB_APP_URL if test_type == "rush" else WEB_APP_URL
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📱 Testni boshlash", web_app=types.WebAppInfo(url=f"{target_url}?count={count}&code={code}")))
    bot.send_message(msg.chat.id, f"Test kodi: {code}. Boshlash uchun bosing:", reply_markup=kb)

# --- Web App Data (Javoblarni ushlash) ---
@bot.message_handler(content_types=["web_app_data"])
def handle_web_app(msg):
    data = json.loads(msg.web_app_data.data)
    state = get_state(msg.chat.id)
    
    if not state:
        bot.send_message(msg.chat.id, "Kutilmagan xatolik. Iltimos, menyudan qaytadan tanlang.")
        return
        
    # 1. Admin test qo'shayotgan bo'lsa
    if state.get("action") == "admin_adding" or data.get("is_admin_save"):
        code = state.get("code") or data.get("code", "00")
        answers = "".join(data["answers"])
        test_type = state.get("type", "standart")
        
        with db_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO tests (code, answers, type) VALUES (?,?,?)", (code, answers, test_type))
            conn.commit()
        bot.send_message(msg.chat.id, f"✅ {code}-kodli test bazaga muvaffaqiyatli saqlandi!", reply_markup=main_menu(msg.chat.id))
        clear_state(msg.chat.id)
        
    # 2. O'quvchi test yechayotgan bo'lsa
    elif state.get("action") == "student_solving":
        test_code = state.get("code")
        student_answers = data.get("answers", [])
        
        with db_conn() as conn:
            test_row = conn.execute("SELECT answers FROM tests WHERE code=?", (test_code,)).fetchone()
            user_row = conn.execute("SELECT name FROM users WHERE user_id=?", (msg.chat.id,)).fetchone()
            
        if not test_row:
            bot.send_message(msg.chat.id, "❌ Test javoblari topilmadi.")
            return
            
        correct_answers = list(test_row[0])
        total_questions = len(correct_answers)
        user_name = user_row[0] if user_row else "Noma'lum o'quvchi"
        
        score = 0
        analysis_list = []
        for i in range(min(len(student_answers), total_questions)):
            if student_answers[i].upper() == correct_answers[i].upper():
                score += 1
                analysis_list.append(f"{i+1}:✅")
            else:
                analysis_list.append(f"{i+1}:❌")
                
        analysis_text = ", ".join(analysis_list)
        
        # Natijani bazaga saqlash
        with db_conn() as conn:
            conn.execute("INSERT INTO results (user_id, name, code, score, total, analysis_text) VALUES (?,?,?,?,?,?)",
                         (msg.chat.id, user_name, test_code, score, total_questions, analysis_text))
            conn.commit()

        # O'quvchiga faqat yo'naltiruvchi tugmalar chiqadi (avtomatik baholashsiz)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("⬅️ Oldingi bo'lim", callback_data="prev_section"),
            types.InlineKeyboardButton("Keyingi bo'lim ➡️", callback_data="next_section")
        )
        kb.add(types.InlineKeyboardButton("ℹ️ Yo'riqnoma", callback_data="help_instruction"))
        
        result_msg = f"📊 Test yakunlandi!\n\n👤 O'quvchi: {user_name}\n🔢 Test kodi: {test_code}"
        bot.send_message(msg.chat.id, result_msg, reply_markup=kb)
        clear_state(msg.chat.id)

@bot.callback_query_handler(func=lambda call: call.data in ["prev_section", "next_section", "help_instruction"])
def handle_directions(call):
    if call.data == "help_instruction":
        bot.answer_callback_query(call.id, "Yo'riqnoma: Bu bo'limlar orqali savollar bo'ylab harakatlanishingiz mumkin.", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "Bo'lim o'zgartirildi.")

# --- Admin: CSV formatida natijalarni olish ---
@bot.message_handler(func=lambda m: m.text == "📊 Natijalarni olish")
def admin_get_results(msg):
    if not is_admin(msg.chat.id): return
    m = bot.send_message(msg.chat.id, "Natijalarini olmoqchi bo'lgan test kodini kiriting:")
    bot.register_next_step_handler(m, process_csv_export)

def process_csv_export(msg):
    code = msg.text.strip().upper()
    with db_conn() as conn:
        rows = conn.execute("SELECT name, score, total, analysis_text, created_at FROM results WHERE code=? ORDER BY score DESC", (code,)).fetchall()
        
    if not rows:
        bot.send_message(msg.chat.id, "Bu test kodi bo'yicha natijalar yo'q.")
        return
        
    output = io.StringIO()
    output.write('\ufeff') # Excelda rus/o'zbek harflari o'qilishi uchun BOM yozish
    
    # Delimiter sifatida ";" ishlatiladi, shunda ustunlar joyi-joyida chiqadi
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["T/R", "O'quvchi Ismi", "Test Kodi", "To'g'ri Javoblar", "Jami Savollar", "Batafsil Tahlil (To'g'ri/Xato)", "Sana"])
    
    for i, row in enumerate(rows, 1):
        writer.writerow([i, row[0], code, row[1], row[2], row[3], row[4]])
        
    output.seek(0)
    file_data = io.BytesIO(output.getvalue().encode('utf-8'))
    file_data.name = f"{code}_natijalar.csv"
    
    bot.send_document(
        msg.chat.id, 
        types.InputFile(file_data), 
        caption=f"📊 {code} - test natijalari va batafsil tahlili."
    )

# --- Server qismi ---
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'Xato', 403

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
    
