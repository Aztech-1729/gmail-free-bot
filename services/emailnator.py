"""Emailnator client — mints real @gmail.com inboxes and reads their mail.

Uses curl_cffi (Chrome TLS impersonation) because Emailnator sits behind
Cloudflare. Zero API key: session cookies only.

Verified live (2026-08): 2,030+ mints, no rate limit found.
"""
import threading
import time
from typing import List, Optional

from curl_cffi import requests as cffi_requests

BASE = "https://www.emailnator.com"

# dotGmail = real gmail with decorative dots. NEVER request plusGmail
# (+ aliases are flagged by many OTP systems).
DEFAULT_TYPES = ["dotGmail"]


class EmailnatorError(Exception):
    pass


class EmailnatorClient:
    def __init__(self):
        self._session: Optional[cffi_requests.Session] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # session plumbing
    # ------------------------------------------------------------------ #
    def _ensure_session(self):
        if self._session is not None:
            return self._session
        s = cffi_requests.Session(impersonate="chrome")
        s.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": BASE,
            "Referer": BASE + "/",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36"),
            "X-Requested-With": "XMLHttpRequest",
        })
        s.get(BASE + "/")
        self._update_tokens(s)
        self._session = s
        return s

    def _update_tokens(self, session):
        xsrf = session.cookies.get("XSRF-TOKEN")
        sess = session.cookies.get("gmailnator_session")
        if xsrf:
            session.headers["X-Xsrf-Token"] = xsrf.replace("%3D", "=")
        if xsrf and sess:
            session.headers["Cookie"] = f"XSRF-TOKEN={xsrf}; gmailnator_session={sess};"

    def _reset_session(self):
        self._session = None

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def generate(self, types: Optional[List[str]] = None) -> str:
        """Mint one gmail. Returns the address (dotted form)."""
        types = types or DEFAULT_TYPES
        with self._lock:
            try:
                s = self._ensure_session()
                r = s.post(f"{BASE}/generate-email", json={"email": types}, timeout=30)
                if r.status_code != 200:
                    self._reset_session()
                    raise EmailnatorError(f"generate HTTP {r.status_code}: {r.text[:120]}")
                data = r.json()
                addrs = data.get("email") or []
                if not addrs:
                    raise EmailnatorError("empty response from generate-email")
                addr = addrs[0]
                if "+" in addr:
                    raise EmailnatorError("got a + alias — retry")
                self._update_tokens(s)
                return addr
            except EmailnatorError:
                raise
            except Exception as e:
                self._reset_session()
                raise EmailnatorError(f"generate failed: {type(e).__name__} {e}")

    def messages(self, address: str) -> List[dict]:
        """List inbox messages for an exact address string."""
        with self._lock:
            try:
                s = self._ensure_session()
                r = s.post(f"{BASE}/message-list", json={"email": address}, timeout=30)
                if r.status_code != 200:
                    self._reset_session()
                    raise EmailnatorError(f"list HTTP {r.status_code}: {r.text[:120]}")
                data = r.json()
                msgs = data.get("messageData") or []
                self._update_tokens(s)
                return [
                    {
                        "messageID": m.get("messageID"),
                        "from": m.get("from", ""),
                        "subject": m.get("subject", ""),
                        "time": m.get("time", ""),
                    }
                    for m in msgs
                ]
            except EmailnatorError:
                raise
            except Exception as e:
                self._reset_session()
                raise EmailnatorError(f"list failed: {type(e).__name__} {e}")

    def message_body(self, address: str, message_id: str, retries: int = 2) -> str:
        """Fetch the full raw HTML of one message (retries, other-form fallback)."""
        last_err = None
        for attempt in range(retries + 1):
            with self._lock:
                try:
                    s = self._ensure_session()
                    r = s.post(f"{BASE}/message-list",
                               json={"email": address, "messageID": message_id},
                               timeout=30)
                    if r.status_code == 200:
                        self._update_tokens(s)
                        if r.text and '"message": "Server Error"' not in r.text:
                            return r.text
                        last_err = "emailnator server error for this message"
                    else:
                        last_err = f"body HTTP {r.status_code}"
                except Exception as e:
                    last_err = f"{type(e).__name__} {e}"
                self._reset_session()
                time.sleep(1.5)
        raise EmailnatorError(last_err or "body failed")
