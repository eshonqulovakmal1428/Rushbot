# Railway — Environment Variables (Variables bo'limiga qo'shing)

BOT_TOKEN    = 8505975357:AAEtUiLlhjg7joD-iJN2JPqj0fKmKyIYpw0
ADMIN_ID     = 5541008041
WEB_APP_URL  = https://eshoonqulov-math-testbot.netlify.app/
RAILWAY_URL  = https://SIZNING-LOYIHA-NOMINGIZ.up.railway.app   # ← deploy bo'lgandan keyin to'ldiring
DB_PATH      = testlar_bazasi.db
PORT         = 5000   # Railway o'zi avtomatik beradi, o'zgartirish shart emas


# ──────────────────────────────────────────────
#  DEPLOY QADAMLARI
# ──────────────────────────────────────────────

# 1. GitHub repoga quyidagi fayllarni push qiling:
#      main.py
#      requirements.txt
#      Procfile

# 2. railway.app → New Project → Deploy from GitHub repo

# 3. Deploy tugagach Settings → Domains → Generate Domain bosing
#    Hosil bo'lgan URL (masalan: https://web-production-xxxx.up.railway.app)
#    ni RAILWAY_URL ga yozing

# 4. Variables bo'limiga yuqoridagi o'zgaruvchilarni kiriting

# 5. Redeploy qiling — bot ishga tushadi ✅

# ──────────────────────────────────────────────
#  MUHIM ESLATMA
# ──────────────────────────────────────────────
# SQLite fayl Railway-da har restart'da o'chib ketadi!
# Doimiy saqlash uchun Railway Volume yoki tashqi DB ishlating.
# Bepul variant: Railway → Add Volume → /app papkasiga mount qiling
# va DB_PATH = /app/testlar_bazasi.db deb o'zgartiring.
