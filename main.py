import os
import json
import logging
import sqlite3
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
DB_PATH = "testlar_bazasi.db"
PORT = int(os.environ.get("PORT", 5000))

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- Baza sozlari ---
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS tests (code TEXT PRIMARY KEY, answers TEXT)")
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
    bot.send_message(msg.chat.id, "Xush kelibsiz! Ism-familiyangizni kiriting:")
    bot.register_next_step_handler(msg, register)

def register(msg):
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO users (user_id, name) VALUES (?,?)", (msg.chat.id, msg.text))
        conn.commit()
    
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📝 Test ishlash", "📈 Rush model Test", "📊 Natijalarim")
    if is_admin(msg.chat.id):
        kb.add("➕ Yangi test qo'shish", "➕ Rush test qo'shish")
        kb.add("📊 Natijalarni olish", "👥 Adminlar boshqaruvi")
    bot.send_message(msg.chat.id, "Bosh menyu:", reply_markup=kb)

# --- Test Web App qabul qilish ---
@bot.message_handler(content_types=["web_app_data"])
def handle_web_app(msg):
    data = json.loads(msg.web_app_data.data)
    
    # Agar ma'lumotni admin test javobi sifatida yuborsa (buni WebApp'dagi payload orqali ajratasiz)
    if data.get("is_admin_save"): 
        code = data.get("code", "00")
        answers = "".join(data["answers"])
        with db_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO tests (code, answers) VALUES (?,?)", (code, answers))
            conn.commit()
        bot.send_message(msg.chat.id, f"✅ {code}-kodli test bazaga saqlandi!")
        
    # O'quvchi yechayotgan bo'lsa
    else:
        student_answers = data.get("answers", [])
        test_code = data.get("code", "00")
        
        with db_conn() as conn:
            test_row = conn.execute("SELECT answers FROM tests WHERE code=?", (test_code,)).fetchone()
            user_row = conn.execute("SELECT name FROM users WHERE user_id=?", (msg.chat.id,)).fetchone()
            
        if not test_row:
            bot.send_message(msg.chat.id, "❌ Test bazada topilmadi.")
            return
            
        correct_answers = list(test_row[0])
        total_questions = len(correct_answers) # Muammo yechimi: 537 emas, aniq javoblar uzunligi
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

        # O'quvchiga yo'naltiruvchi tugmalarni chiqarish
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("⬅️ Oldingi bo'lim", callback_data="prev_section"),
            types.InlineKeyboardButton("Keyingi bo'lim ➡️", callback_data="next_section")
        )
        kb.add(types.InlineKeyboardButton("ℹ️ Yo'riqnoma", callback_data="help_instruction"))
        
        result_msg = f"📊 Test yakunlandi!\n\n👤 O'quvchi: {user_name}\n🔢 Test kodi: {test_code}"
        bot.send_message(msg.chat.id, result_msg, reply_markup=kb)

# --- Admin uchun CSV natijalar ---
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
        
    # Excel mukammal o'qishi uchun maxsus oqim va BOM yozish
    output = io.StringIO()
    output.write('\ufeff') 
    
    # Vergul o'rniga nuqtali-vergul (;) ustunlar to'g'ri taqsimlanishi uchun muhim
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
        caption=f"📊 {code} - test bo'yicha o'quvchilarning natijalari va batafsil tahlili."
    )

# --- Server ishga tushishi ---
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    bot.process_new_updates([telebot.types.Update.de_json(request.get_data(as_text=True))])
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
