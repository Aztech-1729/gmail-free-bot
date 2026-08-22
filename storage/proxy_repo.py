"""Proxy storage — MongoDB proxies collection.

Each doc: {proxy: "http://user:pass@ip:port", added_at, last_checked, alive}
"""
import time
import random
from datetime import datetime, timezone

from storage.db import db as sync_db

COLL = "proxies"

def _coll():
    # lazy get collection from sync_db's mongo client (if mongodb, else None)
    if sync_db.backend != "mongodb":
        return None
    return sync_db._db[COLL]

def add_proxies(proxy_lines: list) -> int:
    """Add proxies from lines like ip:port:user:pass -> http://user:pass@ip:port . Returns added count."""
    coll = _coll()
    if coll is None:
        return 0
    added = 0
    for line in proxy_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # already http://user:pass@ip:port ?
        if line.startswith("http://") or line.startswith("https://"):
            proxy = line
        elif line.count(":") == 3:
            ip, port, user, pwd = line.split(":", 3)
            proxy = f"http://{user}:{pwd}@{ip}:{port}"
        elif line.count(":") == 1:
            # ip:port without auth (free)
            proxy = f"http://{line}"
        else:
            continue
        try:
            coll.update_one(
                {"proxy": proxy},
                {"$setOnInsert": {"proxy": proxy, "added_at": datetime.now(timezone.utc)}, "$set": {"updated_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
            added += 1
        except Exception:
            pass
    return added

def get_random_proxy() -> str | None:
    coll = _coll()
    if coll is None:
        return None
    try:
        # pick random alive or any if none marked alive yet
        # try alive first
        pipeline = [{"$match": {"alive": True}}, {"$sample": {"size": 1}}]
        doc = list(coll.aggregate(pipeline))
        if doc:
            return doc[0]["proxy"]
        # fallback: any random
        doc = list(coll.aggregate([{"$sample": {"size": 1}}]))
        if doc:
            return doc[0]["proxy"]
    except Exception:
        pass
    return None

def count_proxies() -> int:
    coll = _coll()
    if coll is None:
        return 0
    try:
        return coll.count_documents({})
    except Exception:
        return 0

def list_proxies(limit: int = 100):
    coll = _coll()
    if coll is None:
        return []
    try:
        return list(coll.find({}, {"proxy": 1, "alive": 1, "last_checked": 1}).limit(limit))
    except Exception:
        return []

def set_alive(proxy: str, alive: bool):
    coll = _coll()
    if coll is None:
        return
    try:
        coll.update_one({"proxy": proxy}, {"$set": {"alive": alive, "last_checked": datetime.now(timezone.utc)}})
    except Exception:
        pass
