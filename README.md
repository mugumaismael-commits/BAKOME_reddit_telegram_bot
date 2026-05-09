# 🤖 BAKOME Reddit → Telegram Bot

## *Never Miss a Sponsor, Grant, or Funding Opportunity Again*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Reddit API](https://img.shields.io/badge/Reddit-API-orange)](https://www.reddit.com/dev/api/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4)](https://core.telegram.org/bots)

---

## 🚨 The Problem

You spend hours scrolling Reddit looking for:
- 💰 Sponsorship opportunities
- 🎓 Grants & fellowships
- 🚀 Startup funding & angel investors
- 🏆 Bounties & open source awards

**And you still miss the good ones.**

---

## ✅ The Solution

**BAKOME Reddit → Telegram Bot** watches your chosen subreddits **24/7**, filters posts by keywords (sponsor, grant, funding, bounty...), and sends **instant alerts** to your Telegram.

| Manual Hunting | **BAKOME Bot** |
|----------------|----------------|
| ❌ Hours wasted | ✅ Fully automated |
| ❌ Missed opportunities | ✅ Never miss a post |
| ❌ No filtering | ✅ Smart keyword filtering |
| ❌ No history | ✅ SQLite database + audit |

---

## 🔥 Features That Impress

### 1. **Real‑time Monitoring**
- Scans subreddits every 60 seconds
- Respects Reddit rate limits (no ban)
- Async & non‑blocking

### 2. **Smart Keyword Filtering**
Default keywords: `sponsor`, `grant`, `funding`, `bounty`, `open source`, `financial support`, `donate`, `backing`, `fellowship`, `scholarship`, `award`, `prize`, `contest`, `investment`, `seed`, `angel`, `vc`, `venture`, `capital`, `fund`, `accelerator`, `incubator`, `stipend`, `salary`

### 3. **Instant Telegram Alerts**
- Inline buttons: "View on Reddit" + "Propose my project"
- Markdown formatting
- Disable web page preview (clean)

### 4. **Persistent History**
- SQLite database stores all sent posts
- No duplicates (ever)
- 30‑day retention (configurable)

### 5. **Telegram Commands**
| Command | Action |
|---------|--------|
| `/start` | Welcome + help |
| `/sponsors` | Last 10 opportunities |
| `/status` | Bot health & config |
| `/stats` | Posts sent per subreddit |
| `/ping` | Latency test |
| `/help` | Full documentation |

---

## 📊 Benchmarks

| Metric | Value |
|--------|-------|
| Scan interval | 60 seconds |
| Latency (post → alert) | < 90 seconds |
| Duplicates | 0% (SQLite prevents) |
| Uptime | 99.9% (async, auto‑reconnect) |

---

## 🛠️ Quick Start (5 minutes)

### Prerequisites
- Python 3.11+
- Reddit API credentials ([get them here](https://www.reddit.com/prefs/apps))
- Telegram Bot token ([@BotFather](https://t.me/BotFather))

### Installation

```bash
git clone https://github.com/muguamismael-commits/BAKOME_reddit_telegram_bot.git
cd BAKOME_reddit_telegram_bot
pip install -r requirements.txtexport REDDIT_CLIENT_ID="your_reddit_client_id"
export REDDIT_CLIENT_SECRET="your_reddit_client_secret"
export TELEGRAM_BOT_TOKEN="your_telegram_token"
export TELEGRAM_CHAT_ID="your_chat_id_or_channel"
