import os
import sqlite3
from datetime import datetime, timedelta
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
import threading

# --- CONFIG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWITTER_BEARER = os.getenv("TWITTER_BEARER")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))

ALWAYS_QUALIFIED = "Tonynoel"
ALWAYS_QUALIFIED_TWITTER = "TonyNoel01"

DB_FILE = "db.sqlite3"
SESSION_ACTIVE = False

# --- DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS scores (
                    user TEXT, date TEXT, points INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS dm_drops (
                    user TEXT, date TEXT, link TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS group_drops (
                    user TEXT, date TEXT, session TEXT, link TEXT, engaged INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS twitter_handles (
                    telegram_user TEXT UNIQUE, twitter_handle TEXT)""")
    conn.commit()
    conn.close()

# --- DATABASE HELPERS ---
def add_points(user, points):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT points FROM scores WHERE user=? AND date=?", (user, today))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE scores SET points=? WHERE user=? AND date=?", (row[0]+points, user, today))
    else:
        cur.execute("INSERT INTO scores(user,date,points) VALUES (?,?,?)", (user,today,points))
    conn.commit()
    conn.close()

def add_dm_drop(user, link):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO dm_drops(user,date,link) VALUES (?,?,?)", (user,today,link))
    conn.commit()
    conn.close()

def add_group_drop(user, link, session):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO group_drops(user,date,session,link) VALUES (?,?,?,?)", (user,today,session,link))
    conn.commit()
    conn.close()

def has_posted_group(user, session):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT * FROM group_drops WHERE user=? AND date=? AND session=?", (user,today,session))
    row = cur.fetchone()
    conn.close()
    return bool(row)

def did_engage_previous(user):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""SELECT engaged FROM group_drops WHERE user=? AND date < ? ORDER BY date DESC LIMIT 1""", (user,today))
    row = cur.fetchone()
    conn.close()
    if not row: return True
    return bool(row[0])

def set_twitter_handle(user, handle):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO twitter_handles(telegram_user,twitter_handle) VALUES (?,?)", (user,handle))
    conn.commit()
    conn.close()

def get_twitter_handle(user):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT twitter_handle FROM twitter_handles WHERE telegram_user=?", (user,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

# --- TWITTER ENGAGEMENT (placeholder) ---
def check_twitter_engagement(link, twitter_handle):
    # TODO: implement real Twitter/X API call
    return 5  # placeholder points

# --- TELEGRAM BOT COMMANDS ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🚀 Engagement Bot Ready\n\n"
        "Bot DM Commands:\n"
        "/settwitter <handle> - register your Twitter/X handle\n"
        "/drop <link> - submit a link for points\n"
        "/mydrops - check eligibility for this session\n\n"
        "Group Commands (admin only):\n"
        "/startround - start group session\n"
        "/endround - end group session\n"
        "/sessionreport - view tracked posts & engagement"
    )

def set_twitter(update: Update, context: CallbackContext):
    user = update.message.from_user.username
    if not context.args:
        update.message.reply_text("❌ Usage: /settwitter <handle>")
        return
    handle = context.args[0].replace("@","")
    set_twitter_handle(user, handle)
    update.message.reply_text(f"✅ Twitter handle set: @{handle}")

def mydrops(update: Update, context: CallbackContext):
    user = update.message.from_user.username
    if user.lower() == ALWAYS_QUALIFIED.lower():
        update.message.reply_text("🎯 You can post in this session (always-qualified).")
        return
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT points FROM scores WHERE user=? AND date=?", (user, yesterday))
    row = cur.fetchone()
    pts = row[0] if row else 0
    if pts > 0:
        update.message.reply_text(f"🎯 You can post in this session. Points from yesterday: {pts}")
    else:
        update.message.reply_text("❌ You cannot post this session, no points from yesterday.")

def drop(update: Update, context: CallbackContext):
    user = update.message.from_user.username
    if not context.args:
        update.message.reply_text("❌ Provide a tweet/X link.")
        return
    link = " ".join(context.args)
    add_dm_drop(user, link)
    handle = get_twitter_handle(user)
    points = check_twitter_engagement(link, handle if handle else ALWAYS_QUALIFIED_TWITTER)
    add_points(user, points)
    update.message.reply_text(f"✅ Link recorded and points awarded: {points}")

# --- GROUP SESSION ---
def start_round(update: Update, context: CallbackContext):
    global SESSION_ACTIVE
    SESSION_ACTIVE = True
    update.message.reply_text("⚡ Group session started! One link per member in 'Post Links' section.")

def end_round(update: Update, context: CallbackContext):
    global SESSION_ACTIVE
    SESSION_ACTIVE = False
    update.message.reply_text("📌 Group session ended!")

def handle_group_messages(update: Update, context: CallbackContext):
    user = update.message.from_user.username
    text = update.message.text.strip()
    # Monitor General for spam
    forbidden_keywords = ["spamlink.com","malware","click here"]
    if any(word in text.lower() for word in forbidden_keywords):
        try:
            update.message.delete()
            context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠ @{user}, message removed (spam).")
        except: pass
    # Track Post Links section
    if SESSION_ACTIVE and update.message.chat.id == GROUP_CHAT_ID:
        session_name = "post_links"
        if has_posted_group(user, session_name):
            context.bot.send_message(chat_id=update.effective_chat.id,
                                     text=f"❌ @{user}, you have already posted a link this session.")
            return
        if user.lower() != ALWAYS_QUALIFIED.lower() and not did_engage_previous(user):
            context.bot.send_message(chat_id=update.effective_chat.id,
                                     text=f"❌ @{user}, engage previous session first before posting.")
            return
        add_group_drop(user, text, session_name)
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f"✅ @{user}, link recorded for this session (no points).")

# --- SESSION REPORT ---
def session_report(update: Update, context: CallbackContext):
    user = update.message.from_user.username
    if user.lower() != ALWAYS_QUALIFIED.lower():
        update.message.reply_text("❌ Only admins can use this command.")
        return
    session_name = "post_links"
    date_filter = context.args[0] if context.args else datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT user, link, engaged FROM group_drops WHERE date=? AND session=?", (date_filter, session_name))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        update.message.reply_text(f"📭 No posts tracked for {date_filter}.")
        return
    msg = f"📊 Session Report ({session_name} - {date_filter}):\n"
    for i, (u, link, engaged) in enumerate(rows, start=1):
        status = "✅ Engaged" if engaged else "❌ Not engaged"
        msg += f"{i}. @{u} → {link} | {status}\n"
    update.message.reply_text(msg)

# --- AUTO START GROUP SESSION ---
def auto_start_group(bot):
    global SESSION_ACTIVE
    SESSION_ACTIVE = True
    bot.send_message(chat_id=GROUP_CHAT_ID, text="⚡ Automatic group session started!")

# --- FLASK PING (Railway Keep-Alive) ---
app = Flask(__name__)
@app.route("/")
def home():
    return "Engagement Bot is running ✅"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

flask_thread = threading.Thread(target=run_flask)
flask_thread.start()

# --- TELEGRAM BOT SETUP ---
def main():
    init_db()
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("settwitter", set_twitter))
    dp.add_handler(CommandHandler("mydrops", mydrops))
    dp.add_handler(CommandHandler("drop", drop))
    dp.add_handler(CommandHandler("startround", start_round))
    dp.add_handler(CommandHandler("endround", end_round))
    dp.add_handler(CommandHandler("sessionreport", session_report))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_group_messages))

    # Scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: auto_start_group(updater.bot), 'cron', hour=12, minute=0)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
