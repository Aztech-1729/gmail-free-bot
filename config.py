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


POLL_INTERVAL = _as_int(os.environ.get("POLL_INTERVAL") or ENV.get("POLL_INTERVAL", "5"), 5)

MONGO_URI = os.environ.get("MONGO_URI") or ENV.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB") or ENV.get("MONGO_DB", "gmailotp")

ADMIN_ID = _as_int(os.environ.get("ADMIN_ID") or ENV.get("ADMIN_ID", "6670166083"), 6670166083)

# Channel users must join before using the bot (bot must be admin there)
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL") or ENV.get("REQUIRED_CHANNEL", "")
REQUIRED_CHANNEL_URL = (os.environ.get("REQUIRED_CHANNEL_URL")
                        or ENV.get("REQUIRED_CHANNEL_URL")
                        or "https://t.me/aztechshub")

# Per-user generate cooldown (seconds) — near-instant, still unlimited overall.
GENERATE_COOLDOWN = 0.5


def is_configured() -> bool:
    """True when the bot token is present."""
    return bool(BOT_TOKEN) and "PASTE_" not in BOT_TOKEN.upper()


def missing_credentials() -> list:
    """List of missing credential names (for friendly startup errors)."""
    if is_configured():
        return []
    return ["BOT_TOKEN (from @BotFather)"]
