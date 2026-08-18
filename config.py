"""Configuration loader — reads .env with sane defaults."""
import os
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
TMP_DIR = DATA_DIR / "tmp"

for d in (DATA_DIR, TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _load_env() -> dict:
    env = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _load_env()

BOT_TOKEN = os.environ.get("BOT_TOKEN") or ENV.get("BOT_TOKEN", "")


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


POLL_INTERVAL = _as_int(os.environ.get("POLL_INTERVAL") or ENV.get("POLL_INTERVAL", "10"), 10)

MONGO_URI = os.environ.get("MONGO_URI") or ENV.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB") or ENV.get("MONGO_DB", "gmailotp")

# Per-user generate cooldown (seconds) — prevents accidental hammering, still unlimited overall.
GENERATE_COOLDOWN = 1.5


def is_configured() -> bool:
    """True when the bot token is present."""
    return bool(BOT_TOKEN) and "PASTE_" not in BOT_TOKEN.upper()


def missing_credentials() -> list:
    """List of missing credential names (for friendly startup errors)."""
    if is_configured():
        return []
    return ["BOT_TOKEN (from @BotFather)"]
