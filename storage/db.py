"""Storage layer — MongoDB Atlas (primary) with SQLite fallback.

Same interface regardless of backend, so handlers/mailer are unchanged.

Collections (db = MONGO_DB):
  users     — {_id: user_id, username, joined_at}
  mails     — {_id: ObjectId, user_id, address, plain_form, created_at}
  delivered — {_id: ObjectId, mail_id, message_id, delivered_at}
"""
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR, MONGO_DB, MONGO_URI

log = logging.getLogger("storage")

DB_PATH = DATA_DIR / "otp_bot.db"  # only used when Mongo is unavailable


class MongoStore:
    def __init__(self, uri: str, db_name: str):
        from bson import ObjectId  # noqa: F401  (keep import for typing clarity)
        from pymongo import MongoClient

        self._client = MongoClient(uri, serverSelectionTimeoutMS=12000)
        self._client.admin.command("ping")  # fail fast if unreachable
        self._db = self._client[db_name]
        self._users = self._db["users"]
        self._mails = self._db["mails"]
        self._delivered = self._db["delivered"]
        # unique indexes (idempotent; _id is already unique implicitly)
        self._mails.create_index("address", unique=True)
        self._delivered.create_index([("mail_id", 1), ("message_id", 1)], unique=True)
        self.backend = "mongodb"

    def add_user(self, user_id, username=""):
        self._users.update_one(
            {"_id": user_id},
            {"$set": {"username": username or ""}, "$setOnInsert": {"joined_at": self._now()}},
            upsert=True)

    def add_mail(self, user_id, address):
        plain = address.split("@")[0].replace(".", "") + "@" + address.split("@")[1]
        doc = {"user_id": user_id, "address": address, "plain_form": plain,
               "created_at": self._now()}
        res = self._mails.insert_one(doc)
        return str(res.inserted_id)

    def list_mails(self, user_id):
        return [self._shape(m) for m in self._mails.find({"user_id": user_id}).sort("_id", -1)]

    def get_mail(self, mail_id):
        from bson import ObjectId
        try:
            m = self._mails.find_one({"_id": ObjectId(mail_id)})
        except Exception:
            return None
        return self._shape(m) if m else None

    def delete_mail(self, mail_id):
        from bson import ObjectId
        try:
            oid = ObjectId(mail_id)
        except Exception:
            return False
        res = self._mails.delete_one({"_id": oid})
        if res.deleted_count:
            self._delivered.delete_many({"mail_id": mail_id})
        return res.deleted_count > 0

    def count_mails(self, user_id):
        return self._mails.count_documents({"user_id": user_id})

    def all_mails(self):
        return [self._shape(m) for m in self._mails.find()]

    def is_delivered(self, mail_id, message_id):
        return self._delivered.count_documents(
            {"mail_id": mail_id, "message_id": message_id}, limit=1) > 0

    def mark_delivered(self, mail_id, message_id):
        try:
            self._delivered.insert_one(
                {"mail_id": mail_id, "message_id": message_id,
                 "delivered_at": self._now()})
        except Exception:
            pass  # duplicate — already delivered

    def delivered_count(self, user_id):
        # delivered.mail_id stores the string id (same as mails._id stringified)
        mail_ids = [str(m["_id"]) for m in self._mails.find({"user_id": user_id}, {"_id": 1})]
        if not mail_ids:
            return 0
        return self._delivered.count_documents({"mail_id": {"$in": mail_ids}})

    def user_count(self):
        return self._users.count_documents({})

    def mail_count_total(self):
        return self._mails.count_documents({})

    @staticmethod
    def _shape(m):
        m = dict(m)
        m["id"] = str(m.pop("_id"))
        return m

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)


class SqliteStore:
    """Fallback for local dev without Mongo."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, joined_at TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS mails (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, address TEXT NOT NULL UNIQUE, plain_form TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS delivered (mail_id INTEGER NOT NULL, message_id TEXT NOT NULL, delivered_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (mail_id, message_id));
    CREATE INDEX IF NOT EXISTS idx_mails_user ON mails(user_id);
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()
        self.backend = "sqlite"

    def add_user(self, user_id, username=""):
        with self._lock:
            self.conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username or ""))
            self.conn.commit()

    def add_mail(self, user_id, address):
        plain = address.split("@")[0].replace(".", "") + "@" + address.split("@")[1]
        with self._lock:
            cur = self.conn.execute("INSERT INTO mails (user_id, address, plain_form) VALUES (?, ?, ?)", (user_id, address, plain))
            self.conn.commit()
            return cur.lastrowid

    def list_mails(self, user_id):
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM mails WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()]

    def get_mail(self, mail_id):
        with self._lock:
            r = self.conn.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()
            return dict(r) if r else None

    def delete_mail(self, mail_id):
        with self._lock:
            cur = self.conn.execute("DELETE FROM mails WHERE id = ?", (mail_id,))
            self.conn.execute("DELETE FROM delivered WHERE mail_id = ?", (mail_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def count_mails(self, user_id):
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM mails WHERE user_id = ?", (user_id,)).fetchone()[0]

    def all_mails(self):
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM mails").fetchall()]

    def is_delivered(self, mail_id, message_id):
        with self._lock:
            return self.conn.execute("SELECT 1 FROM delivered WHERE mail_id = ? AND message_id = ?", (mail_id, message_id)).fetchone() is not None

    def mark_delivered(self, mail_id, message_id):
        with self._lock:
            self.conn.execute("INSERT OR IGNORE INTO delivered (mail_id, message_id) VALUES (?, ?)", (mail_id, message_id))
            self.conn.commit()

    def delivered_count(self, user_id):
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM delivered d JOIN mails m ON m.id = d.mail_id WHERE m.user_id = ?", (user_id,)).fetchone()[0]

    def user_count(self):
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def mail_count_total(self):
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM mails").fetchone()[0]


def _build_store():
    if MONGO_URI:
        try:
            store = MongoStore(MONGO_URI, MONGO_DB)
            log.info("Storage backend: MongoDB Atlas (%s)", MONGO_DB)
            return store
        except Exception as e:
            log.warning("MongoDB unavailable (%s) — falling back to SQLite", e)
    return SqliteStore()


db = _build_store()
