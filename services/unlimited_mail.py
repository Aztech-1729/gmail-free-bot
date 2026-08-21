"""♾️ UNLIMITED MAIL — pro mail arsenal for GMAILS FREE (no rate limits).

Reverse-engineered & measured (Aug 2026). All providers keyless.

Cascade (generate):
  1. EmailnatorGmail — REAL @gmail.com via Playwright WAF bypass
     (BotWafGuard passed by headless Chromium; generate runs inside the
     WAF-cleared page. ~3.5s/address. Budget ~250-300/IP/15min → proxy rotation)
  2. SMailPro — gmail-style domains via JWT-signer proxy (api.sonjj.com),
     ~1.5-2.7s/address, ~12 creates/IP → proxies
  3. tempmail.lol v2 / Guerrilla / mail.tm / mail.gw — keyless temp domains,
     fastest creates (0.2-0.7s), sender domains sometimes blocked
OTP: Proton verification-code endpoint (keyless), per-IP throttle →
     proxy rotation (measured: fresh proxies return 200 after direct 429).
"""
import json
import logging
import threading
import time
import urllib.parse

import requests

from services.proxy_pool import ProxyPool

try:
    from curl_cffi import requests as cffi_requests
    HAVE_CFFI = True
except Exception:  # pragma: no cover
    cffi_requests = None
    HAVE_CFFI = False

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

    Engine battle (measured): chromium WAF PASS + 543ms generate, 12/12 rapid;
    lightpanda fast-load but flaky on the obfuscated challenge; firefox works too.
    One persistent browser + lock; re-passes WAF automatically on expiry.
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
            log.warning("playwright missing — emailnator disabled "
                        "(pip install playwright && playwright install chromium)")
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
            log.warning("browser launch failed: %s", e)
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
        # rotate context (fresh proxy = fresh IP budget)
        if self._ctx:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = self._page = None
        try:
            self._new_context()
            if not self._waf_ready():
                if self._proxy and self.pool:
                    self.pool.punish(self._proxy)
                return self._ensure_page_retry()
            return True
        except Exception as e:
            log.warning("context setup failed: %s", e)
            return False

    def _ensure_page_retry(self):
        for _ in range(3):
            try:
                self._new_context()
                if self._waf_ready():
                    return True
            except Exception:
                pass
        return False

    # ---------------------------------------------------------- public API
    def generate(self) -> str:
        """Mint a real @gmail.com address. Raises RuntimeError on failure."""
        with self._lock:
            if not self._ensure_page():
                raise RuntimeError('emailnator browser unavailable (playwright missing?)')
            self._gens += 1
            try:
                out = self._page.evaluate(GEN_JS)
            except Exception:
                # page died — force re-pass next time
                self._ctx = self._page = None
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
            if not e or '@' not in str(e):
                raise RuntimeError('emailnator empty address')
            if self._proxy and self.pool:
                self.pool.credit(self._proxy)
            return str(e)

    def messages(self, email: str) -> list:
        """List messages (same shape as old client: [{messageID, from, subject, time}])."""
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
                "'https://www.emailnator.com/message-list', {email: email, messageID: mid});"
                " return JSON.stringify(r.data); } catch(e) { return 'ERR ' + "
                "(e.response ? e.response.status : e.message); } }", email, message_id)
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


# ================================================================ SMailPro
class SMailPro:
    """gmail-style temp domains via api.sonjj.com JWT-signer proxy (keyless).
    Cracked flow: GET smailpro.com/app/payload?url=... → JWT → GET sonjj?...payload=JWT"""

    name = 'smailpro'

    def __init__(self, pool: ProxyPool = None):
        self.pool = pool

    def _sess(self, p):
        if not HAVE_CFFI:
            raise RuntimeError('curl_cffi missing')
        kw = {'impersonate': 'chrome', 'timeout': 20}
        if p:
            kw['proxies'] = {'http': f'http://{p}', 'https': f'http://{p}'}
        s = cffi_requests.Session(**kw)
        s.get('https://smailpro.com/')
        return s

    def _signed(self, s, path, email=None):
        params = {'url': f'https://api.sonjj.com/v1/temp_email{path}'}
        if email:
            params['email'] = email
        q = urllib.parse.urlencode(params)
        payload = s.get(f'https://smailpro.com/app/payload?{q}',
                        headers={'Referer': 'https://smailpro.com/'}).text
        return payload if payload and not payload.startswith('{') else None

    def create(self):
        p = self.pool.get() if self.pool else None
        for _ in range(3):
            try:
                s = self._sess(p)
                payload = self._signed(s, '/create')
                if not payload:
                    if p and self.pool:
                        self.pool.punish(p)
                    p = self.pool.get() if self.pool else None
                    continue
                r = s.get('https://api.sonjj.com/v1/temp_email/create?payload='
                          + urllib.parse.quote(payload, safe=''),
                          headers={'Accept': 'application/json',
                                   'Referer': 'https://smailpro.com/'})
                if r.status_code == 200 and r.json().get('email'):
                    if p and self.pool:
                        self.pool.credit(p)
                    return {'address': r.json()['email'], 'provider': self.name,
                            'session': s}
                if r.status_code in (401, 429) and p and self.pool:
                    self.pool.punish(p)
                    p = self.pool.get() if self.pool else None
            except Exception:
                p = self.pool.get() if self.pool else None
        return None

    def read(self, inbox):
        s = inbox.get('session')
        if s is None:
            return None
        payload = self._signed(s, '/inbox', email=inbox['address'])
        if not payload:
            return None
        r = s.get('https://api.sonjj.com/v1/temp_email/inbox?payload='
                  + urllib.parse.quote(payload, safe=''),
                  headers={'Accept': 'application/json',
                           'Referer': 'https://smailpro.com/'})
        if r.status_code != 200:
            return None
        return [{'messageID': m.get('mid'), 'from': (m.get('textFrom') or '').strip(),
                 'subject': m.get('textSubject') or '', 'time': '',
                 'body': m.get('text') or ''}
                for m in r.json().get('messages', [])]


# ================================================================ keyless
class MailTM:
    name = 'mailtm'
    BASE = 'https://api.mail.tm'

    def create(self):
        try:
            r = requests.get(f'{self.BASE}/domains', headers={'User-Agent': UA},
                             timeout=12)
            dom = r.json()['hydra:member'][0]['domain']
            addr = f'u{int(time.time())}{threading.get_ident() % 1000}@{dom}'
            r2 = requests.post(f'{self.BASE}/accounts',
                               headers={'Content-Type': 'application/json',
                                        'User-Agent': UA},
                               json={'address': addr, 'password': 'UltraPass123!'},
                               timeout=12)
            if r2.status_code != 201:
                return None
            r3 = requests.post(f'{self.BASE}/token',
                               headers={'Content-Type': 'application/json',
                                        'User-Agent': UA},
                               json={'address': addr, 'password': 'UltraPass123!'},
                               timeout=12)
            return {'address': addr, 'provider': self.name, 'token': r3.json()['token']}
        except Exception:
            return None

    def read(self, inbox):
        try:
            r = requests.get(f'{self.BASE}/messages',
                             headers={'Authorization': f'Bearer {inbox["token"]}',
                                      'User-Agent': UA}, timeout=12)
            return [{'messageID': m.get('id'), 'from': m.get('from', {}).get('address', ''),
                     'subject': m.get('subject'), 'time': m.get('createdAt', ''),
                     'body': m.get('intro', '')}
                    for m in r.json().get('hydra:member', [])]
        except Exception:
            return None


class MailGW(MailTM):
    name = 'mailgw'
    BASE = 'https://api.mail.gw'


class TempMailLol:
    name = 'lol'
    BASE = 'https://api.tempmail.lol/v2'

    def create(self):
        try:
            r = requests.post(f'{self.BASE}/inbox/create', headers={'User-Agent': UA},
                              timeout=12)
            j = r.json()
            return {'address': j['address'], 'provider': self.name, 'token': j['token']}
        except Exception:
            return None

    def read(self, inbox):
        try:
            r = requests.get(f'{self.BASE}/inbox?token={inbox["token"]}',
                             headers={'User-Agent': UA}, timeout=12)
            return [{'messageID': m.get('id'), 'from': m.get('from', {}).get('address', ''),
                     'subject': m.get('subject'), 'time': m.get('created_at', ''),
                     'body': m.get('body', '')}
                    for m in r.json().get('emails', [])]
        except Exception:
            return None


class Guerrilla:
    name = 'guerrilla'

    def create(self):
        try:
            r = requests.get('https://api.guerrillamail.com/ajax.php?f=get_email_address'
                             '&ip=127.0.0.1&agent=Mozilla_foo',
                             headers={'User-Agent': UA}, timeout=12)
            j = r.json()
            return {'address': j.get('email_addr'), 'provider': self.name,
                    'sid_token': j.get('sid_token')}
        except Exception:
            return None

    def read(self, inbox):
        try:
            r = requests.get('https://api.guerrillamail.com/ajax.php?f=fetch_email'
                             f'&seq=0&sid_token={inbox["sid_token"]}',
                             headers={'User-Agent': UA}, timeout=12)
            return [{'messageID': m.get('mail_id'), 'from': m.get('mail_from'),
                     'subject': m.get('mail_subject'), 'time': m.get('mail_timestamp', ''),
                     'body': m.get('mail_excerpt', '')}
                    for m in r.json().get('list', [])]
        except Exception:
            return None


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
                    json={'Type': 'email', 'Destination': {'Address': address}}, **kw)
                if r.status_code == 200:
                    if p and pool:
                        pool.credit(p)
                    return 200, r.text
                if r.status_code == 429 and p and pool:
                    pool.punish(p)
            except Exception:
                continue
        return 0, 'all proxies failed'


# ================================================================ cascade
class UnlimitedMailer:
    """One-stop generate/read across every provider, proxy-rotated."""

    def __init__(self, pool: ProxyPool = None, use_emailnator: bool = True):
        self.pool = pool
        self.emailnator = EmailnatorGmail(pool) if use_emailnator else None
        self.smailpro = SMailPro(pool) if HAVE_CFFI else None
        self.lol = TempMailLol()
        self.guerrilla = Guerrilla()
        self.mailtm = MailTM()
        self.mailgw = MailGW()

    def generate(self, prefer_gmail: bool = True) -> dict:
        """Best-effort address across providers.
        Returns {'address': str, 'provider': str, 'meta': dict}.
        Raises RuntimeError when everything fails."""
        errors = []
        if prefer_gmail and self.emailnator is not None:
            try:
                addr = self.emailnator.generate()
                return {'address': addr, 'provider': 'emailnator', 'meta': {}}
            except Exception as e:
                errors.append(f'emailnator: {e}')
        if self.smailpro is not None:
            for _ in range(3):
                inbox = self.smailpro.create()
                if inbox:
                    return {'address': inbox['address'], 'provider': 'smailpro',
                            'meta': {'session': inbox.get('session')}}
                time.sleep(1)
            errors.append('smailpro: all retries failed')
        for prov in (self.lol, self.guerrilla, self.mailgw, self.mailtm):
            inbox = prov.create()
            if inbox:
                return {'address': inbox['address'], 'provider': prov.name,
                        'meta': {'token': inbox.get('token'),
                                 'sid_token': inbox.get('sid_token')}}
            errors.append(f'{prov.name}: failed')
        raise RuntimeError('all providers failed: ' + '; '.join(errors[-3:]))

    def read_messages(self, address: str, provider: str) -> list:
        """Unified read. Returns [] on failure (mailer treats as empty)."""
        try:
            if provider == 'emailnator' and self.emailnator is not None:
                return self.emailnator.messages(address)
            if provider == 'smailpro' and self.smailpro is not None:
                return self.smailpro.read({'address': address, 'session': None})
        except Exception:
            pass
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
