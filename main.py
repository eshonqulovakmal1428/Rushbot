# --- Get Results & Export ---
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

    test_type, answers_raw, creator_id = test_info
    
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

    # Fayl yaratish va jo'natish xavfsiz blokda (xatolik bo'lsa chatga yozadi)
    try:
        output = io.StringIO()
        output.write('\ufeff') # UTF-8 ni qo'llab quvvatlash uchun (Excelda yozuvlar buzilmasligi uchun)
        writer = csv.writer(output, delimiter=';')
        
        # Faqat 4 ta ustun
        writer.writerow(["Ism va Familiya", "To'g'ri javob soni", "Olgan bali", "Daraja"])

        if test_type == "rush":
            b_items = get_rasch_item_difficulties(code, total_q)
            for r in rows:
                user_id, name, score, total, analysis_text, created_at = r
                theta = calculate_rasch_theta(score, b_items)
                raw_ball = theta_to_ball(theta)
                
                # Yangi chegaralangan MS baholash
                ball, daraja = get_ms_grade_and_ball(score, raw_ball)
                writer.writerow([name, score, ball, daraja])
        else:
            for r in rows:
                user_id, name, score, total, analysis_text, created_at = r
                ball = round((score / total) * 100, 1) if total else 0.0
                daraja = get_daraja(ball)
                writer.writerow([name, score, ball, daraja])

        # Matnni bytelarga xavfsiz o'girish
        file_data = output.getvalue().encode('utf-8-sig')
        mem_file = io.BytesIO(file_data)
        mem_file.name = f"{code}_natijalar.csv"
        mem_file.seek(0) # IMPORTANT: faylni boshidan o'qish uchun kursor 0 ga qaytarilishi shart!

        # Faylni foydalanuvchiga yuborish
        bot.send_document(
            chat_id=msg.chat.id,
            document=mem_file,
            visible_file_name=f"{code}_natijalar.csv",
            caption=f"📊 *{code}* - test bo'yicha o'quvchilarning natijalari.",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
    except Exception as e:
        log.error(f"Fayl yaratish xatosi: {e}")
        safe_send(msg.chat.id, "❌ Faylni yaratish yoki yuborishda kutilmagan xatolik yuz berdi. Iltimos qaytadan urinib ko'ring.", reply_markup=main_menu())
