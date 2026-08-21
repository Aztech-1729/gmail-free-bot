"""🔒 GMAIL-ONLY unlimited mail — real @gmail.com addresses, nothing else.

Emailnator @gmail via Playwright WAF bypass (BotWafGuard). Proxy-rotated budget:
Emailnator ≈ 250-300 generates/IP/15-min window × proxy pool = no effective limit.

REMOVED (user request — other domains are useless for OTP anyway):
  SMailPro / tempmail.lol / Guerrilla / mail.tm / mail.gw

Also ships ProtonOTP — keyless 6-digit-code sender (proxy-rotated).

Setup for the @gmail path:
  pip install playwright
  playwright install chromium --with-deps
"""
import json
import logging
import threading
import time

import requests

from services.proxy_pool import ProxyPool

log = logging.getLogger("unlimitedmail")

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
PROTON_CODE = 'https://account-api.proton.me/api/core/v4/users/code'
GEN_JS = ("async () => { try { const r = await axios.post("
          "'https://www.emailnator.com/generate-email', {email: ['dotGmail']});"
          " return JSON.stringify(r.data); } catch(e) { return 'ERR ' + "
          "(e.response ? e.response.status + ' ' + e.response.statusText : e.message); } }")


# ================================================================ Emailnator
class EmailnatorGmail:
    """REAL @gmail.com — BotWafGuard bypassed with headless Chromium.

    Engine battle (measured Aug 2026): chromium WAF PASS + ~543ms generate,
    12/12 rapid, zero limits; lightpanda fast-load but flaky on the obfuscated
    challenge; firefox works too.
    One persistent browser + lock; re-passes WAF automatically.
    Optional proxy rotation per window budget (pool)."""

    name = 'emailnator'

    def __init__(self, pool: ProxyPool = None, gens_per_proxy: int = 40,
                 engine: str = 'chromium'):
        self.pool = pool
        self.gens_per_proxy = gens_per_proxy
        self.engine = engine
        self._lock = threading.Lock()
        self._browser = None
        self._pw = None
        self._ctx = None
        self._page = None
        self._gens = 0
        self._proxy = None

    # ---------------------------------------------------------- internals
    def _launch(self):
        if self._browser is not None:
            return True
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            log.warning("playwright missing — @gmail generation disabled. "
                        "Fix: pip install playwright && "
                        "playwright install chromium --with-deps")
            return False
        try:
            self._pw = sync_playwright().start()
            if self.engine == 'firefox':
                self._browser = self._pw.firefox.launch(headless=True)
            else:
                self._browser = self._pw.chromium.launch(
                    headless=True,
                    args=['--no-sandbox',
                          '--disable-blink-features=AutomationControlled',
                          '--disable-dev-shm-usage', '--disable-gpu'])
            return True
        except Exception as e:
            log.warning("browser launch failed: %s — chromium binaries missing? "
                        "playwright install chromium --with-deps", e)
            return False

    def _new_context(self):
        self._gens = 0
        self._proxy = self.pool.get() if self.pool else None
        kw = {'user_agent': UA}
        if self._proxy:
            kw['proxy'] = {'server': f'http://{self._proxy}'}
        self._ctx = self._browser.new_context(**kw)
        self._page = self._ctx.new_page()

    def _waf_ready(self) -> bool:
        for _ in range(5):
            try:
                self._page.goto('https://www.emailnator.com/', timeout=30000,
                                wait_until='domcontentloaded')
            except Exception:
                pass
            time.sleep(3)
            try:
                if self._page.locator('button[name="goBtn"]').count():
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def _ensure_page(self) -> bool:
        """Guarantee a WAF-cleared page; rotate proxy when budget burned."""
        if not self._launch():
            return False
        if self._page is not None and self._gens < self.gens_per_proxy:
            return True
        if self._ctx:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = self._page = None
        for _ in range(4):
            try:
                self._new_context()
                if self._waf_ready():
                    return True
            except Exception:
                pass
            if self._proxy and self.pool:
                self.pool.punish(self._proxy)
        return False

    # ---------------------------------------------------------- public API
    def generate(self) -> str:
        """Mint a real @gmail.com address. Raises RuntimeError on failure."""
        with self._lock:
            if not self._ensure_page():
                raise RuntimeError('emailnator browser unavailable — install: '
                                   'playwright install chromium --with-deps')
            self._gens += 1
            try:
                out = self._page.evaluate(GEN_JS)
            except Exception:
                self._ctx = self._page = None  # page died → re-pass next time
                raise RuntimeError('emailnator page died')
            if out.startswith('ERR'):
                if '419' in out or '429' in out:
                    if self._proxy and self.pool:
                        self.pool.punish(self._proxy)
                    self._ctx = self._page = None  # rotate next call
                raise RuntimeError(f'emailnator blocked: {out[:60]}')
            try:
                j = json.loads(out)
                e = j.get('email')
                e = e[0] if isinstance(e, list) else e
            except Exception:
                raise RuntimeError('emailnator bad payload')
            if not e or '@' not in str(e) or 'gmail.com' not in str(e):
                # HARD GUARD: only real gmail/googlemail addresses leave here
                raise RuntimeError(f'emailnator non-gmail address: {e}')
            if self._proxy and self.pool:
                self.pool.credit(self._proxy)
            return str(e)

    def messages(self, email: str) -> list:
        """List messages [{messageID, from, subject, time}]."""
        with self._lock:
            if not self._ensure_page():
                raise RuntimeError('emailnator browser unavailable')
            out = self._page.evaluate(
                "async (email) => { try { const r = await axios.post("
                "'https://www.emailnator.com/message-list', {email: email});"
                " return JSON.stringify(r.data); } catch(e) { return 'ERR ' + "
                "(e.response ? e.response.status : e.message); } }", email)
            if out.startswith('ERR'):
                raise RuntimeError(f'emailnator read blocked: {out[:60]}')
            try:
                j = json.loads(out)
                return j.get('messageData', []) or []
            except Exception:
                return []

    def message_body(self, email: str, message_id: str) -> str:
        """Full message HTML via message-list {email, messageID}."""
        with self._lock:
            if not self._ensure_page():
                raise RuntimeError('emailnator browser unavailable')
            out = self._page.evaluate(
                "async (email, mid) => { try { const r = await axios.post("
                "'https://www.emailnator.com/message-list', "
                "{email: email, messageID: mid});"
                " return JSON.stringify(r.data); } catch(e) { return 'ERR ' + "
                "(e.response ? e.response.status : e.message); } }",
                email, message_id)
            if out.startswith('ERR'):
                raise RuntimeError(f'emailnator body blocked: {out[:60]}')
            return out

    def close(self):
        with self._lock:
            for obj in (self._ctx, self._browser):
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:
                    pass
            self._ctx = self._page = self._browser = self._pw = None


# ================================================================ OTP
class ProtonOTP:
    """Keyless OTP sender — Proton verification-code email (6-digit code).
    Per-IP throttle after ~10 sends → proxy rotation fixes it (measured)."""

    def send(self, address: str, pool: ProxyPool = None) -> tuple:
        """Returns (http_status, body_text)."""
        attempts = [None]
        if pool is not None:
            attempts += [pool.get() for _ in range(10)]
        for p in attempts:
            try:
                kw = {'timeout': 20}
                if p:
                    kw['proxies'] = {'http': f'http://{p}', 'https': f'http://{p}'}
                r = requests.post(PROTON_CODE, headers={
                    'Content-Type': 'application/json',
                    'x-pm-appversion': 'Other',
                    'User-Agent': UA},
                    json={'Type': 'email', 'Destination': {'Address': address}},
                    **kw)
                if r.status_code == 200:
                    if p and pool:
                        pool.credit(p)
                    return 200, r.text
                if r.status_code == 429 and p and pool:
                    pool.punish(p)
            except Exception:
                continue
        return 0, 'all proxies failed'


# ================================================================ mailer
class UnlimitedMailer:
    """GMAIL-ONLY generate/read. No other domains ever leave this class."""

    def __init__(self, pool: ProxyPool = None, use_emailnator: bool = True):
        self.pool = pool
        self.emailnator = EmailnatorGmail(pool) if use_emailnator else None

    def generate(self, prefer_gmail: bool = True) -> dict:
        """{'address': '@gmail.com', 'provider': 'emailnator'}.
        Raises RuntimeError with the fix instructions when Chromium is missing."""
        if self.emailnator is None:
            raise RuntimeError('gmail generation disabled — install playwright: '
                               'pip install playwright && '
                               'playwright install chromium --with-deps')
        return {'address': self.emailnator.generate(),
                'provider': 'emailnator', 'meta': {}}

    def read_messages(self, address: str, provider: str = 'emailnator') -> list:
        if provider != 'emailnator' or self.emailnator is None:
            return []  # legacy non-gmail entries are retired
        try:
            return self.emailnator.messages(address)
        except Exception:
            return []

    def read_body(self, address: str, provider: str, message_id: str) -> str:
        if provider == 'emailnator' and self.emailnator is not None:
            return self.emailnator.message_body(address, message_id)
        raise RuntimeError(f'body read unsupported for {provider}')


# module-level singleton (built lazily by the bot)
_mailer = None
_mailer_lock = threading.Lock()


def get_mailer(use_emailnator: bool = True) -> UnlimitedMailer:
    global _mailer
    with _mailer_lock:
        if _mailer is None:
            _mailer = UnlimitedMailer(ProxyPool(), use_emailnator=use_emailnator)
        return _mailer
