# 🤖 IG FULL-AUTO — run on YOUR PC (residential IP = passes IG)

The engine is 100% automatic: IG code sent to your gmail → IMAP auto-reads it
(app password works, verified) → account created → credentials saved.
IG only blocks DATACENTER IPs (E2B/proxies/VPS) — your home internet passes.

## One-time setup (2 min)
1. Install Python 3.10+ if missing
2. `pip install curl_cffi requests e2b` (e2b only needed for the farm)
3. Edit the top of `ig_fullauto.py` if your gmail/app-password changes

## Run
```bash
python3 ig_fullauto.py        # single-shot, base email then +1..+8 aliases
```
Watch it print: `code_sent → code_read → create → WIN @username`
Wins saved to `ig_accounts.json` (email, username, password, cookies).

## Rate limits (honest)
- ~2-4 accounts per day per IP before IG's cooldown — pace it
- plus-aliases: instagramacc1e9dh+1@gmail.com … all deliver to the SAME inbox,
  one IMAP read covers all. ~15-20 accounts possible per gmail.
