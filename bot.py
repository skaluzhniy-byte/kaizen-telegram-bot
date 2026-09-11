"""
Kaizen 1% — Telegram-бот для трьох атомарних звичок (Тригер / Двигун / Замок).

Старт кривої: 02.09.2026, ціль на день i (0-based) = 2 * 1.01**i хвилин.
Дані зберігаються локально в SQLite (kaizen.db), окремо по chat_id —
бот можна ділити з кількома людьми, кожен веде свою серію.

Запуск: python bot.py   (потребує змінну середовища KAIZEN_BOT_TOKEN)
"""

import os
import sqlite3
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kaizen_bot")

TZ = ZoneInfo("Europe/Kyiv")
START_DATE = date(2026, 9, 2)   # день 1
TOTAL_DAYS = 365
START_MIN = 2.0
GROWTH = 1.01

DB_PATH = os.path.join(os.path.dirname(__file__), "kaizen.db")

REMINDER_TIMES = {
    "trigger": time(9, 0, tzinfo=TZ),
    "engine": time(12, 0, tzinfo=TZ),
    "lock": time(21, 0, tzinfo=TZ),
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS entries (
            chat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            trigger_done INTEGER DEFAULT 0,
            engine TEXT DEFAULT 'none',
            lock_value TEXT DEFAULT '',
            PRIMARY KEY (chat_id, day)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pending_lock (
            chat_id INTEGER PRIMARY KEY
        )"""
    )
    return conn


def register_chat(chat_id: int):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()


def all_chat_ids():
    conn = db()
    rows = conn.execute("SELECT chat_id FROM chats").fetchall()
    conn.close()
    return [r[0] for r in rows]


def upsert_entry(chat_id: int, day: date, **fields):
    conn = db()
    day_str = day.isoformat()
    cur = conn.execute(
        "SELECT trigger_done, engine, lock_value FROM entries WHERE chat_id=? AND day=?",
        (chat_id, day_str),
    )
    row = cur.fetchone()
    trigger_done, engine, lock_value = row if row else (0, "none", "")
    trigger_done = fields.get("trigger_done", trigger_done)
    engine = fields.get("engine", engine)
    lock_value = fields.get("lock_value", lock_value)
    conn.execute(
        """INSERT INTO entries (chat_id, day, trigger_done, engine, lock_value)
           VALUES (?,?,?,?,?)
           ON CONFLICT(chat_id, day) DO UPDATE SET
             trigger_done=excluded.trigger_done,
             engine=excluded.engine,
             lock_value=excluded.lock_value""",
        (chat_id, day_str, trigger_done, engine, lock_value),
    )
    conn.commit()
    conn.close()


def get_entry(chat_id: int, day: date):
    conn = db()
    cur = conn.execute(
        "SELECT trigger_done, engine, lock_value FROM entries WHERE chat_id=? AND day=?",
        (chat_id, day.isoformat()),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"trigger_done": 0, "engine": "none", "lock_value": ""}
    return {"trigger_done": row[0], "engine": row[1], "lock_value": row[2]}


def get_all_entries(chat_id: int):
    conn = db()
    rows = conn.execute(
        "SELECT day, trigger_done, engine, lock_value FROM entries WHERE chat_id=? ORDER BY day",
        (chat_id,),
    ).fetchall()
    conn.close()
    return {r[0]: {"trigger_done": r[1], "engine": r[2], "lock_value": r[3]} for r in rows}


def set_pending_lock(chat_id: int, waiting: bool):
    conn = db()
    if waiting:
        conn.execute("INSERT OR IGNORE INTO pending_lock (chat_id) VALUES (?)", (chat_id,))
    else:
        conn.execute("DELETE FROM pending_lock WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()


def is_pending_lock(chat_id: int) -> bool:
    conn = db()
    row = conn.execute("SELECT 1 FROM pending_lock WHERE chat_id=?", (chat_id,)).fetchone()
    conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# Curve math
# ---------------------------------------------------------------------------

def day_index(d: date) -> int:
    return (d - START_DATE).days


def target_minutes(idx: int) -> float:
    idx = max(0, min(idx, TOTAL_DAYS - 1))
    return START_MIN * (GROWTH ** idx)


def compute_stats(chat_id: int):
    today = datetime.now(TZ).date()
    today_idx = day_index(today)
    entries = get_all_entries(chat_id)

    statuses = []
    for i in range(0, min(today_idx, TOTAL_DAYS - 1) + 1):
        if i < 0:
            continue
        d = (START_DATE + timedelta(days=i)).isoformat()
        e = entries.get(d)
        if e is None:
            st = "missed" if i < today_idx else "pending"
        else:
            st = "done" if e["engine"] == "target" else ("floor" if e["engine"] == "floor" else "missed")
            if e["engine"] == "none" and i == today_idx:
                st = "pending"
        statuses.append(st)

    current = 0
    for st in reversed(statuses):
        if st == "pending":
            continue
        if st in ("done", "floor"):
            current += 1
        else:
            break

    best = run = 0
    for st in statuses:
        if st in ("done", "floor"):
            run += 1
            best = max(best, run)
        elif st != "pending":
            run = 0

    finalized = [s for s in statuses if s != "pending"]
    done_count = sum(1 for s in finalized if s in ("done", "floor"))
    pct = round(100 * done_count / len(finalized)) if finalized else None

    return {
        "today_idx": today_idx,
        "current_streak": current,
        "best_streak": best,
        "pct": pct,
        "finish_date": START_DATE + timedelta(days=TOTAL_DAYS - 1),
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    register_chat(chat_id)
    today = datetime.now(TZ).date()
    idx = day_index(today)
    text = (
        "Kaizen 1% підключено.\n\n"
        f"Старт кривої: {START_DATE.strftime('%d.%m.%Y')}. "
        f"Ціль дня 365: {target_minutes(TOTAL_DAYS-1):.1f} хв (≈37×).\n\n"
        "Три щоденні нагадування:\n"
        "09:00 — Тригер (точка важеля)\n"
        "12:00 — Двигун (захищений фокус-блок)\n"
        "21:00 — Замок (одне число)\n\n"
        "Команди: /today — показати всі три пункти зараз, /status — серія і статистика."
    )
    if idx < 0:
        text += f"\n\nДо старту лишилось {-idx} дн."
    await update.message.reply_text(text)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = compute_stats(chat_id)
    if s["today_idx"] < 0:
        await update.message.reply_text(f"Крива ще не почалась. Старт — {START_DATE.strftime('%d.%m.%Y')}.")
        return
    today_target = target_minutes(s["today_idx"])
    text = (
        f"День {s['today_idx']+1} з {TOTAL_DAYS}\n"
        f"Ціль сьогодні: {today_target:.1f} хв\n\n"
        f"Поточна серія: {s['current_streak']} дн.\n"
        f"Найкраща серія: {s['best_streak']} дн.\n"
        f"% днів виконано: {s['pct']}%\n" if s['pct'] is not None else ""
    )
    text += f"Фініш кривої: {s['finish_date'].strftime('%d.%m.%Y')}"
    await update.message.reply_text(text)


def trigger_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Записав точку важеля", callback_data="trigger:done")]])


def engine_keyboard(today_target: float):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎯 Ціль ({today_target:.1f} хв)", callback_data="engine:target")],
        [InlineKeyboardButton("🛟 Підлога (2 хв)", callback_data="engine:floor")],
        [InlineKeyboardButton("❌ Пропущено", callback_data="engine:none")],
    ])


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    today = datetime.now(TZ).date()
    idx = day_index(today)
    if idx < 0:
        await update.message.reply_text(f"Крива ще не почалась. Старт — {START_DATE.strftime('%d.%m.%Y')}.")
        return
    target = target_minutes(idx)
    await update.message.reply_text(
        "Тригер — яка одна дія сьогодні найбільше зрушить дохід? Запиши подумки/на папері, потім тисни кнопку.",
        reply_markup=trigger_keyboard(),
    )
    await update.message.reply_text(
        f"Двигун — сьогоднішня ціль {target:.1f} хв захищеного фокусу.",
        reply_markup=engine_keyboard(target),
    )
    await update.message.reply_text("Замок — напиши одне число, яке рухалося сьогодні (виручка/ліди/угоди).")
    set_pending_lock(chat_id, True)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    today = datetime.now(TZ).date()
    await query.answer()
    data = query.data
    if data.startswith("trigger:"):
        upsert_entry(chat_id, today, trigger_done=1)
        await query.edit_message_text("Тригер зафіксовано ✅")
    elif data.startswith("engine:"):
        choice = data.split(":", 1)[1]
        upsert_entry(chat_id, today, engine=choice)
        label = {"target": "Ціль виконано 🎯", "floor": "Підлога зарахована 🛟", "none": "Позначено як пропущено"}[choice]
        await query.edit_message_text(label)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_pending_lock(chat_id):
        return  # ignore free text outside of the lock-capture window
    today = datetime.now(TZ).date()
    upsert_entry(chat_id, today, lock_value=update.message.text.strip())
    set_pending_lock(chat_id, False)
    await update.message.reply_text("Замок збережено 🔒")


# ---------------------------------------------------------------------------
# Scheduled reminders (JobQueue)
# ---------------------------------------------------------------------------

async def remind_trigger(context: ContextTypes.DEFAULT_TYPE):
    for chat_id in all_chat_ids():
        await context.bot.send_message(
            chat_id,
            "Тригер — яка одна дія сьогодні найбільше зрушить дохід?",
            reply_markup=trigger_keyboard(),
        )


async def remind_engine(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()
    idx = day_index(today)
    if idx < 0:
        return
    target = target_minutes(idx)
    for chat_id in all_chat_ids():
        await context.bot.send_message(
            chat_id,
            f"Двигун — сьогоднішня ціль {target:.1f} хв захищеного фокусу.",
            reply_markup=engine_keyboard(target),
        )


async def remind_lock(context: ContextTypes.DEFAULT_TYPE):
    for chat_id in all_chat_ids():
        set_pending_lock(chat_id, True)
        await context.bot.send_message(
            chat_id, "Замок — напиши одне число, яке рухалося сьогодні (виручка/ліди/угоди)."
        )


def main():
    token = os.environ.get("KAIZEN_BOT_TOKEN")
    if not token:
        raise SystemExit("Встанови змінну середовища KAIZEN_BOT_TOKEN (токен від @BotFather).")

    db()  # ensure tables exist

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    jq = app.job_queue
    jq.run_daily(remind_trigger, time=REMINDER_TIMES["trigger"], name="remind_trigger")
    jq.run_daily(remind_engine, time=REMINDER_TIMES["engine"], name="remind_engine")
    jq.run_daily(remind_lock, time=REMINDER_TIMES["lock"], name="remind_lock")

    log.info("Kaizen bot started (polling).")
    app.run_polling()


if __name__ == "__main__":
    main()
