# 🤖 GMAILS FREE — Telegram OTP Bot

**Free, unlimited real `@gmail.com` addresses inside Telegram — with instant OTP forwarding.**

- Press a button → get a fresh gmail (**plain form** — no dots, no `+`; the dotted alias is polled invisibly)
- Any mail arriving at that address → forwarded to you **instantly** as:
  1. 📝 a summary with **OTP codes auto-extracted**
  2. 🌐 an **`.html` file** of the email
  3. 📄 the **raw `.eml`** file
- Manage everything with **colored buttons** (Bot API 9.4 styles: 🔵 primary · 🔴 danger · 🟢 success)
- **No API key, no payment** — minted via Emailnator's pool of real Gmail inboxes (2,000+ mints verified with zero rate limits)
- Storage: **MongoDB Atlas** (with automatic SQLite fallback)

---

## 🚀 Setup (ONE credential, 2 minutes)

### 1. Get a bot token (BotFather)
1. Open [@BotFather](https://t.me/BotFather) → `/newbot`
2. Name it → pick a username
3. Copy the token → paste into `.env` as `BOT_TOKEN`

*(That's it — pure Bot API, no my.telegram.org credentials needed.)*

### 2. Run
```bash
cd telegram-otp-bot
cp .env.example .env      # fill BOT_TOKEN (+ optionally MONGO_URI)
pip install -r requirements.txt
python3 main.py
```
You'll see `Bot online: @your_bot` — done.

> **Sanity check without Telegram:** `python3 main.py --selftest`
> (mints a gmail, reads its inbox, extracts codes, builds html+eml — fully automated, verified live.)

---

## 🎛 Features & buttons

### Main menu (colored reply keyboard, always at the bottom)

| Button | Color | Action |
|---|---|---|
| ➕ Generate Gmail | 🔵 primary | Mints a fresh `@gmail.com` — **shown in plain form only** |
| 📬 My Mails | 🟢 success | Paged list of your addresses |
| 🗑 Delete Mail | 🔴 danger | Pick an address to remove |
| 📊 Stats | default | Your mailboxes + delivered messages |
| ❓ Help | default | Usage guide |

### Per-mail inline buttons
- **📥 Check Inbox** (🔵 primary) — manually pull the current inbox
- **➕ Generate another** (🟢 success) — one tap to mint the next address
- **🗑 Delete** (🔴 danger) — with confirmation: **✅ Yes, delete it** (🔴) / **❌ Cancel** (🟢)

### Incoming mail — instant triple delivery
For every new email:
1. **Summary message** — sender, subject, time + extracted OTP codes in `code` blocks
2. **`<id>.html`** — the email as a document
3. **`<id>_raw.eml`** — raw headers + original body

Polling runs every `POLL_INTERVAL` seconds (default **5**) and watches **both** the dotted and plain address forms. All mailboxes are polled **in parallel** (8 workers, per-thread sessions), so delivery is instant even with hundreds of mails.

### Old-mail protection (baseline)
Pooled addresses arrive with a history of someone else's mail. At **generate time** the bot snapshots every pre-existing message as *baseline* — only mail arriving **after** that moment is ever forwarded (or shown in Check Inbox). No old spam, ever.

### Attachment guarantee
Files are sent **before** the summary: if the `.html` / `.eml` upload fails, nothing is marked delivered and the bot retries next poll — so you never get a summary without its files.

---

## 📁 Project structure

```
telegram-otp-bot/
├── main.py               # entrypoint — Bot API long polling (+ --selftest)
├── config.py             # .env loader, credentials validation
├── requirements.txt
├── .env / .env.example
├── bot/
│   ├── api.py            # minimal Bot API client (sendMessage/sendDocument/…)
│   ├── keyboards.py      # colored reply + inline keyboards (9.4 styles)
│   └── handlers.py       # generate / check / delete / stats / help flows
├── services/
│   ├── emailnator.py     # Emailnator client (mint, list, body) — curl_cffi TLS
│   ├── extractor.py      # OTP code extraction, html→text, .eml builder
│   └── mailer.py         # background poller → instant forwarding (threading)
├── storage/
│   └── db.py             # MongoDB Atlas (primary) / SQLite fallback
└── data/                 # temp files + sqlite fallback (auto-created)
```

### Storage — MongoDB Atlas

| Collection | Documents |
|---|---|
| `users` | `{_id: user_id, username, joined_at}` |
| `mails` | `{_id, user_id, address, plain_form, created_at}` — unique index on `address` |
| `delivered` | `{_id, mail_id, message_id, delivered_at}` — unique compound index (dedupe) |
| `baseline` | `{_id, mail_id, message_id, baselined_at}` — old pool mail snapshot at generate time; **never forwarded** |

Configure with `MONGO_URI` / `MONGO_DB` in `.env`. If Mongo is unreachable, the bot **auto-falls back to local SQLite** and logs a warning — it never crashes on storage failure. (Both paths verified live against Atlas.)

---

## ⚙️ Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | — | From @BotFather (the only required credential) |
| `POLL_INTERVAL` | `5` | Seconds between inbox checks (all mails polled in parallel) |
| `MONGO_URI` | *(empty → SQLite)* | MongoDB Atlas connection string |
| `MONGO_DB` | `gmailotp` | Database name |

---

## 🔬 Verified live

| Test | Result |
|---|---|
| Bot token `getMe` | ✅ `@GmailsFreeOTPBot` ("GMAILS FREE") |
| MongoDB Atlas connect + CRUD + dedupe | ✅ against the live cluster |
| Mint fresh gmail | ✅ |
| Read inbox, fetch 65KB HTML body, extract codes | ✅ |
| Build raw `.eml` | ✅ |
| Unfetchable-message handling (Emailnator 500s) | ✅ skips gracefully |
| Colored buttons (primary/danger/success) | ✅ |
| Mint scale (earlier session) | ✅ 2,030+ mints, no rate limit |

---

## ⚠️ Honest caveats

- **Pooled, passwordless inboxes** (Emailnator's model) — perfect for one-time OTPs, **not** for permanent account recovery. Others may eventually be handed the same address.
- **Messages auto-purge** after ~24h on Emailnator's side — the bot forwards instantly, so you keep your copies in Telegram.
- **Addresses keyed by exact string**: the bot polls both dotted and plain forms to catch mail regardless of which form the sender used.
- **Generate cooldown**: 1.5s per user (anti-hammer) — still unlimited overall.
- Keep `.env` private — it contains your bot token and DB credentials.

## 🔭 Upgrade ideas

- Per-mail mute/pause buttons
- Auto-reply or forwarding of selected mails to another inbox
- Web dashboard mirroring the DB
- Dockerfile for one-command deploy (VPS/Railway)

## ♾️ PRO ARSENAL — @gmail.com ONLY, no rate limits (proxy-rotated, measured Aug 2026)

**The bot mints ONLY real @gmail.com addresses** — other domains (SMailPro, temp
mail, etc.) are removed: OTP senders block them anyway.

| Path | Gives | Speed | Limit → defeat |
|---|---|---|---|
| **Emailnator @gmail** (Playwright WAF bypass) | real @gmail.com | ~3.5s | ~250-300 gens/IP/15min → proxy rotation |
| Legacy curl_cffi client (fallback) | real @gmail.com | ~1-2s | retries with backoff |

Hard guard: any non-gmail address is discarded before it reaches the user.

**Proxy pool** (`services/proxy_pool.py`): scrapes 20 sources → validates (anonymous
check) → auto-refresh. Measured: 6,790 raw → 188 alive → 136 anon; refresh pass
grew the pool 187 → 315. `python3 services/proxy_pool.py --loop` keeps it fed forever.

**Bot commands**
- `♾️ Mass Gmails` button or `/gmails N` — mint N addresses (up to 500), sent as a .txt
- `/otp email` — keyless Proton verification-code mail (proxy-rotated)
- `/proxies` — pool status

**Mass generator (10k+)**: `python3 mass_gmail.py 10000 12` — 12 browser contexts,
proxy rotation, checkpoint resume (measured: 4.3/s sandbox, 3,227 uniques in one run,
99.2% unique). Output: `data/gmails.txt`.

**Setup on a new machine**
```bash
pip install -r requirements.txt
playwright install chromium --with-deps   # for the @gmail.com WAF bypass
```
If Playwright is missing, generation fails with clear install instructions
(gmail-only means no junk-domain fallbacks — fix chromium and you're back).

