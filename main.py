"""
Health Assistant Bot — Railway версия (только Telegram)
"""

import os
import json
import sqlite3
from datetime import datetime
import anthropic
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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

SYSTEM_PROMPT = """Ты персональный AI health-коуч. Анализируй данные Apple Health и давай конкретные мотивирующие рекомендации. Стиль: дружелюбный, без воды, с цифрами, короткие абзацы, умеренно эмодзи."""

def analyze_with_claude(today_data: dict, question: str = None) -> str:
    history = get_history(7)
    history_text = "\n".join([
        f"  {r['date']}: {r.get('steps','?')} шагов, сон {r.get('sleep_hours','?')}ч, вес {r.get('weight_kg','?')}кг"
        for r in history
    ]) or "Нет данных"

    if question:
        prompt = f"Вопрос: {question}\n\nДанные сегодня: {json.dumps(today_data or {}, ensure_ascii=False)}\n\nИстория:\n{history_text}\n\nЦели: {json.dumps(USER_GOALS, ensure_ascii=False)}"
    else:
        prompt = f"""Ежедневный отчёт:
- Вес: {today_data.get('weight_kg', '—')} кг
- Шаги: {today_data.get('steps', '—')}
- Калории: {today_data.get('calories_burned', '—')} ккал
- Сон: {today_data.get('sleep_hours', '—')} ч
- Пульс: {today_data.get('heart_rate_avg', '—')} уд/мин
- Тренировки: {', '.join(today_data.get('workouts', [])) or 'нет'}

История:\n{history_text}

Цели: {json.dumps(USER_GOALS, ensure_ascii=False)}

Дай: 📊 Итог дня / ✅ Что хорошо / ⚠️ Что улучшить / 💡 Рекомендации / 🎯 Прогресс"""

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я твой AI health-коуч.\n\n"
        "Команды:\n"
        "/today — анализ сегодня\n"
        "/week — отчёт за неделю\n"
        "/goals — прогресс к целям\n\n"
        "Или напиши любой вопрос!"
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
    history_text = "\n".join([f"{r['date']}: {r.get('steps','?')} шагов, сон {r.get('sleep_hours','?')}ч" for r in history])
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

def main():
    init_db()
    print("✅ База данных инициализирована")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Telegram бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
