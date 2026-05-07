"""
Health Assistant Bot — Railway версия
Telegram бот работает как основной процесс, Flask запускается в отдельном потоке.
"""

import os
import json
import sqlite3
import threading
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
import anthropic
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SERVER_PORT = int(os.environ.get("PORT", 5050))

USER_GOALS = {
    "target_weight_kg": 76,
    "daily_steps": 10000,
    "workouts_per_week": 4,
    "sleep_hours": 8,
    "target_muscle_mass_kg": 40,
}

DB_PATH = "/tmp/health_data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY,
            weight_kg REAL,
            steps INTEGER,
            calories_burned INTEGER,
            active_minutes INTEGER,
            sleep_hours REAL,
            heart_rate_avg INTEGER,
            workouts TEXT,
            raw_json TEXT,
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

def get_history(days: int = 7):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT ?", (days,)
    )
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    conn.close()
    return rows

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
        f"  {r['date']}: {r.get('steps','?')} шагов, сон {r.get('sleep_hours','?')}ч, вес {r.get('weight_kg','?')}кг"
        for r in history
    ]) or "Нет данных за предыдущие дни"

    if question:
        prompt = f"""Вопрос пользователя: {question}

Текущие данные:
{json.dumps(today_data or {}, ensure_ascii=False, indent=2)}

История за неделю:
{history_text}

Цели:
{json.dumps(USER_GOALS, ensure_ascii=False, indent=2)}

Ответь на вопрос используя данные выше."""
    else:
        prompt = f"""Проанализируй данные за сегодня и дай ежедневный отчёт.

Данные:
- Вес: {today_data.get('weight_kg', '—')} кг
- Шаги: {today_data.get('steps', '—')}
- Калории: {today_data.get('calories_burned', '—')} ккал
- Активных минут: {today_data.get('active_minutes', '—')}
- Сон: {today_data.get('sleep_hours', '—')} часов
- Пульс: {today_data.get('heart_rate_avg', '—')} уд/мин
- Тренировки: {', '.join(today_data.get('workouts', [])) or 'нет'}

История:
{history_text}

Цели:
{json.dumps(USER_GOALS, ensure_ascii=False, indent=2)}

Структура:
📊 Итог дня
✅ Что хорошо
⚠️ Что улучшить
💡 Рекомендации на завтра
🎯 Прогресс к целям"""

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text

# Flask в отдельном потоке
app_flask = Flask(__name__)

@app_flask.route("/health", methods=["POST"])
def receive_health_data():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data"}), 400
        data["date"] = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        save_daily_data(data)
        analysis = analyze_with_claude(data)
        # Отправляем в Telegram асинхронно
        asyncio.run(send_telegram_async(f"📱 *Отчёт {data['date']}*\n\n{analysis}"))
        return jsonify({"status": "ok", "analysis": analysis})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app_flask.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "running", "time": datetime.now().isoformat()})

def run_flask():
    app_flask.run(host="0.0.0.0", port=SERVER_PORT, debug=False, use_reloader=False)

async def send_telegram_async(text: str):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="Markdown")

# Telegram handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Привет! Я твой AI health-коуч.\n\n"
        f"Команды:\n"
        f"/today — анализ сегодня\n"
        f"/week — отчёт за неделю\n"
        f"/goals — прогресс к целям\n\n"
        f"Или напиши любой вопрос по здоровью!",
    )

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_history(1)
    if not history:
        await update.message.reply_text("Нет данных. Синхронизируй iPhone через Shortcuts!")
        return
    await update.message.reply_text("⏳ Анализирую...")
    analysis = analyze_with_claude(history[0])
    await update.message.reply_text(f"📊 *Сегодня*\n\n{analysis}", parse_mode="Markdown")

async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Готовлю отчёт...")
    history = get_history(7)
    if not history:
        await update.message.reply_text("Нет данных за неделю.")
        return
    history_text = "\n".join([f"  {r['date']}: {r.get('steps','?')} шагов, сон {r.get('sleep_hours','?')}ч" for r in history])
    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Еженедельный отчёт:\n{history_text}\nЦели: {json.dumps(USER_GOALS, ensure_ascii=False)}"}],
    )
    await update.message.reply_text(f"📅 *Неделя*\n\n{response.content[0].text}", parse_mode="Markdown")

async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🎯 *Твои цели*\n\n"
        f"⚖️ Вес: цель {USER_GOALS['target_weight_kg']} кг\n"
        f"👟 Шаги: {USER_GOALS['daily_steps']:,}/день\n"
        f"🏋️ Тренировок: {USER_GOALS['workouts_per_week']}/неделю\n"
        f"😴 Сон: {USER_GOALS['sleep_hours']}ч",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤔 Думаю...")
    history = get_history(1)
    today = history[0] if history else {}
    answer = analyze_with_claude(today, question=update.message.text)
    await update.message.reply_text(answer)

async def main():
    init_db()
    print("✅ База данных инициализирована")

    # Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"🌐 Flask запущен на порту {SERVER_PORT}")

    # Telegram как основной процесс
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Telegram бот запущен")
    await app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
