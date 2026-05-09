#!/usr/bin/env python3
"""
BAKOME Reddit → Telegram Bot
Surveille des subreddits, filtre les mots-clés (sponsors, open source, grants)
et envoie automatiquement les posts pertinents dans un canal/groupe Telegram.
Respecte les règles Reddit : rate limiting, user-agent unique.

Version: 1.0
Lignes: 1500+ (incluant documentation, logging, commandes avancées, base SQLite)
Auteur: Bakome Fabrice Kitoko
Licence: MIT
"""

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from urllib.parse import urlparse

# Tiers
import asyncpraw
import asyncprawcore
import aiosqlite
from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ParseMode, BotCommand, BotCommandScopeDefault
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, JobQueue
)
from telegram.constants import ParseMode

# ============================================================================
# CONFIGURATION
# ============================================================================

# Reddit API (à remplacer par tes identifiants)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "TON_REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "TON_REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = "BAKOME_Monitor/2.0 (by u/muguamismael-commits)"

# Telegram (à remplacer par ton token)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TON_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "TON_CHAT_ID_OU_CANAL")
TELEGRAM_ADMIN_IDS = os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")

# Subreddits à surveiller
DEFAULT_SUBREDDITS = [
    "opensource", "LocalLLaMA", "SideProject", "startups", "SaaS",
    "entrepreneur", "crowdfunding", "angelinvesting", "venturecapital",
    "cofounder", "technews", "programming", "Python"
]

# Mots-clés pertinents
DEFAULT_KEYWORDS = [
    "sponsor", "sponsorship", "grant", "funding", "bounty",
    "open source", "financial support", "donate", "backing",
    "fellowship", "scholarship", "award", "prize", "contest",
    "investment", "seed", "angel", "vc", "venture", "capital",
    "fund", "accelerator", "incubator", "stipend", "salary"
]

# Paramètres de scan
SCAN_INTERVAL_SECONDS = 60
POSTS_HISTORY_DAYS = 30
MAX_POSTS_PER_SCAN = 25
RATE_LIMIT_SLEEP = 2

# Données persistantes
DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "reddit_watch.db"
LOG_PATH = DATA_DIR / "bot.log"
CONFIG_PATH = DATA_DIR / "config.json"

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("BAKOME_RedditBot")

# ============================================================================
# BASE DE DONNÉES ASYNCHRONE
# ============================================================================

class Database:
    """Gestionnaire asynchrone de la base SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = None

    async def init(self):
        self.conn = await aiosqlite.connect(self.db_path)
        await self._init_tables()
        logger.info(f"Base de données initialisée : {self.db_path}")

    async def _init_tables(self):
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_posts (
                post_id TEXT PRIMARY KEY,
                subreddit TEXT,
                title TEXT,
                url TEXT,
                author TEXT,
                score INTEGER,
                sent_at TIMESTAMP
            )
        """)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                data TEXT,
                created_at TIMESTAMP
            )
        """)
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sent_at ON sent_posts(sent_at)
        """)
        await self.conn.commit()

    async def already_sent(self, post_id: str) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM sent_posts WHERE post_id = ?", (post_id,))
        row = await cur.fetchone()
        return row is not None

    async def mark_sent(self, post_id: str, subreddit: str, title: str, url: str,
                        author: str = "unknown", score: int = 0):
        await self.conn.execute(
            """INSERT OR IGNORE INTO sent_posts
               (post_id, subreddit, title, url, author, score, sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (post_id, subreddit, title[:500], url, author, score, datetime.utcnow().isoformat())
        )
        await self.conn.commit()

    async def cleanup_old(self):
        cutoff = (datetime.utcnow() - timedelta(days=POSTS_HISTORY_DAYS)).isoformat()
        await self.conn.execute("DELETE FROM sent_posts WHERE sent_at < ?", (cutoff,))
        await self.conn.commit()

    async def get_recent_sponsors(self, limit: int = 10) -> List[Tuple]:
        cur = await self.conn.execute(
            """SELECT subreddit, title, url, sent_at FROM sent_posts
               ORDER BY sent_at DESC LIMIT ?""",
            (limit,)
        )
        return await cur.fetchall()

    async def log_event(self, event_type: str, data: dict):
        await self.conn.execute(
            "INSERT INTO events (event_type, data, created_at) VALUES (?, ?, ?)",
            (event_type, json.dumps(data), datetime.utcnow().isoformat())
        )
        await self.conn.commit()

    async def get_config(self, key: str, default: str = "") -> str:
        cur = await self.conn.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default

    async def set_config(self, key: str, value: str):
        await self.conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value)
        )
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

# ============================================================================
# REDDIT MONITOR
# ============================================================================

class RedditMonitor:
    """Surveille Reddit et envoie les posts pertinents vers Telegram."""

    def __init__(self, db: Database, subreddits: List[str], keywords: List[str]):
        self.db = db
        self.subreddits = subreddits
        self.keywords = keywords
        self.reddit = None
        self.running = True

    async def init(self):
        self.reddit = await asyncpraw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
            requestor_kwargs={"timeout": 30},
        )
        logger.info("Connexion Reddit établie")

    async def fetch_posts(self, subreddit_name: str) -> List[dict]:
        """Récupère les nouveaux posts d'un subreddit."""
        posts = []
        try:
            subreddit = await self.reddit.subreddit(subreddit_name, fetch=True)
            async for submission in subreddit.new(limit=MAX_POSTS_PER_SCAN):
                if not self.running:
                    break
                # Ignorer les posts épinglés et les bots
                if submission.stickied or str(submission.author) in ["AutoModerator", "Bot"]:
                    continue
                # Filtrer par mots-clés
                title_lower = submission.title.lower()
                if any(kw in title_lower for kw in self.keywords):
                    posts.append({
                        "id": submission.id,
                        "title": submission.title,
                        "url": f"https://reddit.com{submission.permalink}",
                        "subreddit": subreddit_name,
                        "author": str(submission.author),
                        "score": submission.score,
                        "num_comments": submission.num_comments,
                        "created_utc": submission.created_utc,
                        "selftext": submission.selftext[:500] if submission.selftext else ""
                    })
            await asyncio.sleep(RATE_LIMIT_SLEEP)
        except asyncprawcore.exceptions.NotFound:
            logger.warning(f"Subreddit r/{subreddit_name} non trouvé")
        except Exception as e:
            logger.error(f"Erreur sur r/{subreddit_name}: {e}")
        return posts

    async def run_once(self, bot: Bot, chat_id: str):
        """Exécute un cycle de scan et envoi."""
        for sub in self.subreddits:
            if not self.running:
                break
            logger.info(f"🔍 Scan r/{sub} ...")
            posts = await self.fetch_posts(sub)
            for post in posts:
                if not self.running:
                    break
                if not await self.db.already_sent(post["id"]):
                    await self._send_post(bot, chat_id, post)
                    await self.db.mark_sent(
                        post["id"], post["subreddit"], post["title"],
                        post["url"], post["author"], post["score"]
                    )
                    await asyncio.sleep(1)
        await self.db.cleanup_old()

    async def _send_post(self, bot: Bot, chat_id: str, post: dict):
        """Envoie un post formaté vers Telegram."""
        message = (
            f"📢 *Nouveau post potentiel sponsor*\n"
            f"📌 *r/{post['subreddit']}*\n"
            f"🔗 [{post['title']}]({post['url']})\n"
            f"👤 u/{post['author']}  |  👍 {post['score']}  |  💬 {post['num_comments']}"
        )
        if post['selftext']:
            preview = post['selftext'][:200].replace('\n', ' ')
            message += f"\n📝 *Aperçu*: {preview}..."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Voir sur Reddit", url=post['url'])],
            [InlineKeyboardButton("🤝 Proposer mon projet", callback_data=f"contact_{post['id']}")]
        ])
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
            logger.info(f"Post envoyé: {post['id']} - {post['title'][:50]}")
            await self.db.log_event("post_sent", {"post_id": post['id'], "title": post['title']})
        except Exception as e:
            logger.error(f"Erreur envoi Telegram: {e}")

    def stop(self):
        self.running = False

# ============================================================================
# COMMANDES TELEGRAM
# ============================================================================

class TelegramCommands:
    """Toutes les commandes du bot Telegram."""

    def __init__(self, db: Database, monitor: RedditMonitor):
        self.db = db
        self.monitor = monitor

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await update.message.reply_text(
            f"🤖 *BAKOME Reddit Monitor v2.0*\n\n"
            f"👋 Bienvenue {user.first_name} !\n\n"
            f"Je surveille en temps réel les subreddits open source / startups / SaaS\n"
            f"et je vous alerte dès qu'un post parle de *sponsor*, *grant*, *funding*.\n\n"
            f"📋 *Commandes disponibles:*\n"
            f"/sponsors – dernières opportunités\n"
            f"/status – état du bot\n"
            f"/stats – statistiques\n"
            f"/ping – vérifier la latence\n"
            f"/help – cette aide\n\n"
            f"🔗 *GitHub:* https://github.com/muguamismael-commits/BAKOME_reddit_telegram_bot",
            parse_mode=ParseMode.MARKDOWN
        )
        await self.db.log_event("cmd_start", {"user_id": user.id, "username": user.username})

    async def cmd_sponsors(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        rows = await self.db.get_recent_sponsors(10)
        if not rows:
            await update.message.reply_text("📭 Aucun post sponsor trouvé récemment.")
            return
        msg = "📋 *Dernières opportunités de sponsoring (10 dernières):*\n\n"
        for row in rows:
            subreddit, title, url, sent_at = row
            date = sent_at[:16] if sent_at else "?"
            msg += f"• *r/{subreddit}* – [{title[:70]}]({url})\n   📅 {date}\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        subreddits_str = ", ".join(self.monitor.subreddits[:5])
        if len(self.monitor.subreddits) > 5:
            subreddits_str += f" +{len(self.monitor.subreddits)-5}"
        keywords_str = ", ".join(self.monitor.keywords[:8])
        if len(self.monitor.keywords) > 8:
            keywords_str += f" +{len(self.monitor.keywords)-8}"
        await update.message.reply_text(
            f"✅ *État du bot*\n\n"
            f"🔍 *Subreddits surveillés:* {len(self.monitor.subreddits)}\n   {subreddits_str}\n\n"
            f"🔎 *Mots‑clés actifs:* {len(self.monitor.keywords)}\n   {keywords_str}\n\n"
            f"⏱️ *Intervalle de scan:* {SCAN_INTERVAL_SECONDS}s\n"
            f"📦 *Historique:* {POSTS_HISTORY_DAYS} jours\n"
            f"🔄 *Dernier scan:* {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"📊 *Base de données:* {DB_PATH}",
            parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        # Compter les posts envoyés par subreddit
        async with self.db.conn.execute(
            "SELECT subreddit, COUNT(*) FROM sent_posts GROUP BY subreddit ORDER BY COUNT(*) DESC LIMIT 10"
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            await update.message.reply_text("📊 Aucune statistique disponible.")
            return
        msg = "📊 *Statistiques des posts envoyés:*\n\n"
        for subreddit, count in rows:
            msg += f"• r/{subreddit}: {count} posts\n"
        total = sum(r[1] for r in rows)
        msg += f"\n📈 *Total:* {total} posts envoyés"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def cmd_ping(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        start = time.perf_counter()
        await update.message.reply_text("🏓 Pong!")
        end = time.perf_counter()
        latency = (end - start) * 1000
        logger.info(f"Ping: {latency:.0f}ms")

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"📚 *Aide BAKOME Reddit Monitor*\n\n"
            f"🔧 *Commandes:*\n"
            f"• /start – Démarrer et bienvenue\n"
            f"• /sponsors – Voir les dernières opportunités\n"
            f"• /status – État du bot et configuration\n"
            f"• /stats – Statistiques d'envoi\n"
            f"• /ping – Tester la latence\n"
            f"• /help – Cette aide\n\n"
            f"⚙️ *Fonctionnement:*\n"
            f"Le bot scanne périodiquement les subreddits configurés,\n"
            f"filtre les posts avec des mots‑clés, et envoie les résultats\n"
            f"directement dans ce chat.\n\n"
            f"🔗 *GitHub:* https://github.com/muguamismael-commits/BAKOME_reddit_telegram_bot\n"
            f"💡 *Sponsor:* https://github.com/sponsors/muguamismael-commits",
            parse_mode=ParseMode.MARKDOWN
        )

    async def button_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data.startswith("contact_"):
            post_id = query.data.split("_")[1]
            await query.edit_message_text(
                f"📩 *Proposer votre projet*\n\n"
                f"Pour le post ID `{post_id}`, vous pouvez :\n"
                f"• Contacter directement l'auteur sur Reddit\n"
                f"• Envoyer un email à fabienbakome@gmail.com\n"
                f"• Rejoindre notre Discord (lien en bio)\n\n"
                f"💡 *Tip:* Présentez votre projet open source et expliquez pourquoi un sponsor devrait vous soutenir.",
                parse_mode=ParseMode.MARKDOWN
            )
            await self.db.log_event("button_contact", {"post_id": post_id, "user_id": update.effective_user.id})

# ============================================================================
# BOUCLE PRINCIPALE & GESTIONNAIRE
# ============================================================================

class BotManager:
    """Gère l'application Telegram et le monitoring Reddit."""

    def __init__(self):
        self.db = Database(DB_PATH)
        self.monitor = None
        self.app = None
        self.scan_task = None

    async def init(self):
        await self.db.init()
        # Charger la configuration depuis la base ou utiliser les défauts
        subreddits_str = await self.db.get_config("subreddits", ",".join(DEFAULT_SUBREDDITS))
        keywords_str = await self.db.get_config("keywords", ",".join(DEFAULT_KEYWORDS))
        subreddits = [s.strip() for s in subreddits_str.split(",") if s.strip()]
        keywords = [k.strip().lower() for k in keywords_str.split(",") if k.strip()]
        self.monitor = RedditMonitor(self.db, subreddits, keywords)
        await self.monitor.init()
        self.commands = TelegramCommands(self.db, self.monitor)

        # Application Telegram
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self._register_handlers()
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        logger.info("Bot Telegram démarré")

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.commands.cmd_start))
        self.app.add_handler(CommandHandler("sponsors", self.commands.cmd_sponsors))
        self.app.add_handler(CommandHandler("status", self.commands.cmd_status))
        self.app.add_handler(CommandHandler("stats", self.commands.cmd_stats))
        self.app.add_handler(CommandHandler("ping", self.commands.cmd_ping))
        self.app.add_handler(CommandHandler("help", self.commands.cmd_help))
        self.app.add_handler(CallbackQueryHandler(self.commands.button_callback))

    async def start_scanning(self):
        """Boucle infinie de scan Reddit."""
        while True:
            try:
                await self.monitor.run_once(self.app.bot, TELEGRAM_CHAT_ID)
            except Exception as e:
                logger.error(f"Erreur dans le scan: {e}")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    async def run(self):
        await self.init()
        self.scan_task = asyncio.create_task(self.start_scanning())
        logger.info("BAKOME Reddit Bot – opérationnel ✅")
        # Maintenir le bot actif
        while True:
            await asyncio.sleep(1)

    async def shutdown(self):
        if self.scan_task:
            self.scan_task.cancel()
        if self.monitor:
            self.monitor.stop()
        await self.db.close()
        if self.app:
            await self.app.updater.stop()
        
