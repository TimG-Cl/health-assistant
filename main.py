"""
Health Assistant Bot
Автоматически получает данные из Apple Health через iPhone Shortcuts,
анализирует с помощью Claude AI и отправляет отчёты в Telegram.

Установка:
  pip install anthropic flask python-telegram-bot requests

Запуск:
  python main.py
"""

import os
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import anthropic
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ============================================================
# НАСТРОЙКИ — замени на свои ключи
# ============================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SERVER_PORT        = 5050

# Твои личные цели
USER_GOALS = {
    "target_weight_kg":     76,
    "daily_steps":          10000,
    "workouts_per_week":    4,
    "sleep_hours":          8,
    "target_muscle_mass_kg": 40,
}

# ============================================================
# База данных
# ============================================================
DB_PATH = "health_data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date              TEXT PRIMARY KEY,
            weight_kg         REAL,
            steps             INTEGER,
            calories_burned   INTEGER,
            active_minutes    INTEGER,
            sleep_hours       REAL,
            heart_rate_avg    INTEGER,
            workouts          TEXT,
            raw_json          TEXT,
            created_at        TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            role       TEXT,
            content    TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_daily_data(data: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO daily_metrics
        (date, weight_kg, steps, calories_burned, active_minutes,
         sleep_hours, heart_rate_avg, workouts, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("date", datetime.now().strftime("%Y-%m-%d")),
        data.get("weight_kg"),
        data.get("steps"),
        data.get("calories_burned"),
        data.get("active_minutes"),
        data.get("sleep_hours"),
        data.get("heart_rate_avg"),
        json.dumps(data.get("workouts", []), ensure_ascii=False),
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()

def get_history(days: int = 7) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT ?", (days,)
    )
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    conn.close()
    return rows

# ============================================================
# Claude AI — анализ здоровья
# ============================================================
import os
os.environ['HTTP_PROXY'] = 'http://127.0.0.1:12334'
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:12334'
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Ты персональный AI health-коуч. Твоя задача — анализировать данные Apple Health пользователя и давать конкретные, мотивирующие рекомендации.

Стиль общения:
- Дружелюбный, поддерживающий, без воды
- Конкретные цифры и сроки
- Короткие абзацы, легко читать на телефоне
- Используй эмодзи умеренно

Цели пользователя: похудение, набор мышечной массы, улучшение выносливости, нормализация сна.
"""

def analyze_with_claude(today_data: dict, question: str = None) -> str:
    history = get_history(7)
    
    history_text = "\n".join([
        f"  {r['date']}: {r.get('steps','?')} шагов, сон {r.get('sleep_hours','?')}ч, вес {r.get('weight_kg','?')}кг, калории {r.get('calories_burned','?')}"
        for r in history
    ]) or "Нет данных за предыдущие дни"

    if question:
        prompt = f"""Вопрос пользователя: {question}

Текущие данные за сегодня:
{json.dumps(today_data or {}, ensure_ascii=False, indent=2)}

История за неделю:
{history_text}

Цели пользователя:
{json.dumps(USER_GOALS, ensure_ascii=False, indent=2)}

Ответь на вопрос, используя данные выше."""
    else:
        prompt = f"""Проанализируй данные пользователя за сегодня и дай ежедневный отчёт.

Данные за сегодня:
- Вес: {today_data.get('weight_kg', '—')} кг
- Шаги: {today_data.get('steps', '—')}
- Калории сожжено: {today_data.get('calories_burned', '—')} ккал
- Активных минут: {today_data.get('active_minutes', '—')}
- Сон: {today_data.get('sleep_hours', '—')} часов
- Пульс в покое: {today_data.get('heart_rate_avg', '—')} уд/мин
- Тренировки: {', '.join(today_data.get('workouts', [])) or 'нет'}

История за неделю:
{history_text}

Цели:
{json.dumps(USER_GOALS, ensure_ascii=False, indent=2)}

Структура ответа:
📊 Итог дня (2–3 предложения)
✅ Что хорошо сегодня
⚠️ Что улучшить
💡 Конкретные рекомендации на завтра
🎯 Прогресс к целям (краткая таблица в процентах)"""

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text

def get_weekly_report() -> str:
    history = get_history(7)
    if not history:
        return "Нет данных за прошлую неделю. Начни синхронизацию через iPhone Shortcuts!"

    history_text = "\n".join([
        f"  {r['date']}: {r.get('steps','?')} шагов, сон {r.get('sleep_hours','?')}ч, вес {r.get('weight_kg','?')}кг"
        for r in history
    ])

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"""Создай еженедельный отчёт по здоровью.

История за неделю:
{history_text}

Цели:
{json.dumps(USER_GOALS, ensure_ascii=False, indent=2)}

Структура:
🏆 Главные достижения недели
📈 Динамика показателей
⚡ Топ-3 рекомендации на следующую неделю
🎯 Прогресс к долгосрочным целям"""}],
    )
    return response.content[0].text

# ============================================================
# Flask-сервер — принимает данные от iPhone Shortcuts
# ============================================================
app_flask = Flask(__name__)

@app_flask.route("/health", methods=["POST"])
def receive_health_data():
    """Endpoint куда iPhone Shortcuts отправляет данные каждый вечер"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data"}), 400

        data["date"] = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        save_daily_data(data)

        # Анализируем с Claude
        analysis = analyze_with_claude(data)

        # Отправляем в Telegram
        send_telegram_message(f"📱 *Ежедневный отчёт {data['date']}*\n\n{analysis}")

        return jsonify({"status": "ok", "analysis": analysis})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app_flask.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "running", "time": datetime.now().isoformat()})

# ============================================================
# Telegram бот
# ============================================================
bot_instance = Bot(token=TELEGRAM_BOT_TOKEN)

def send_telegram_message(text: str):
    """Отправить сообщение в Telegram"""
    try:
        bot_instance.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"Telegram error: {e}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👋 Привет! Я твой AI health-коуч.\n\n"
        f"Твой Chat ID: `{chat_id}`\n"
        f"Скопируй его в TELEGRAM_CHAT_ID в настройках бота.\n\n"
        f"Команды:\n"
        f"/today — анализ сегодняшних данных\n"
        f"/week — еженедельный отчёт\n"
        f"/goals — прогресс к целям\n\n"
        f"Или просто напиши любой вопрос по здоровью!",
        parse_mode="Markdown",
    )

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_history(1)
    if not history:
        await update.message.reply_text("Нет данных за сегодня. Синхронизируй iPhone через Shortcuts!")
        return
    await update.message.reply_text("⏳ Анализирую данные...")
    analysis = analyze_with_claude(history[0])
    await update.message.reply_text(f"📊 *Анализ за сегодня*\n\n{analysis}", parse_mode="Markdown")

async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Готовлю еженедельный отчёт...")
    report = get_weekly_report()
    await update.message.reply_text(f"📅 *Отчёт за неделю*\n\n{report}", parse_mode="Markdown")

async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_history(1)
    today = history[0] if history else {}
    weight_now = today.get("weight_kg", "—")
    target_weight = USER_GOALS["target_weight_kg"]
    
    msg = (
        f"🎯 *Твои цели и прогресс*\n\n"
        f"⚖️ Вес: {weight_now} кг → цель {target_weight} кг\n"
        f"👟 Шаги в день: цель {USER_GOALS['daily_steps']:,}\n"
        f"🏋️ Тренировок в неделю: цель {USER_GOALS['workouts_per_week']}\n"
        f"😴 Сон: цель {USER_GOALS['sleep_hours']}ч\n\n"
        f"Напиши /today чтобы узнать текущий прогресс!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка свободных вопросов к AI-коучу"""
    user_text = update.message.text
    await update.message.reply_text("🤔 Думаю...")
    
    history = get_history(1)
    today = history[0] if history else {}
    answer = analyze_with_claude(today, question=user_text)
    await update.message.reply_text(answer)

def run_telegram_bot():
    app_bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", cmd_start))
    app_bot.add_handler(CommandHandler("today", cmd_today))
    app_bot.add_handler(CommandHandler("week", cmd_week))
    app_bot.add_handler(CommandHandler("goals", cmd_goals))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Telegram бот запущен")
    app_bot.run_polling()

# ============================================================
# Точка входа
# ============================================================
if __name__ == "__main__":
    init_db()
    print("✅ База данных инициализирована")

    # Запускаем Telegram-бота в отдельном потоке
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()

    # Запускаем Flask-сервер
    print(f"🌐 Сервер запущен на http://localhost:{SERVER_PORT}")
    print(f"📡 Endpoint для Shortcuts: http://ТВОЙ_IP:{SERVER_PORT}/health")
    app_flask.run(host="0.0.0.0", port=SERVER_PORT, debug=False)
    