"""♾️ PROXY POOL — the unlimited layer for Emailnator/Cloudflare-walled APIs.

Reverse-engineered & measured (Aug 2026):
  Emailnator budget ≈ 250-300 generates per IP per 15-min window.
  Fresh proxy IP = fresh budget → rotating pool = no effective rate limit.
  Free proxies die constantly → this module re-scrapes + re-validates + merges,
  keeping the pool fat forever (refresh every 10 min via --loop or the bot).

Measured: 6,790 raw → 188 alive → 136 anonymous (first pass);
          refresh pass: 9,461 raw → 252 alive → pool 187 → 315.

Usage:
  python3 services/proxy_pool.py            # one refresh pass
  python3 services/proxy_pool.py --loop     # refresh every 10 min forever

In-code:
  pool = ProxyPool()          # loads data/proxies_alive.txt, lazy warmup
  p = pool.get()              # round-robin least-throttled proxy
  pool.punish(p)              # 429/419 → cooldown 15 min
  pool.credit(p)              # success → count against per-proxy budget
"""
import concurrent.futures
import logging
import os
import sys
import threading
import time

import requests

log = logging.getLogger("proxypool")

SOURCES = [
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
    'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt',
    'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt',
    'https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt',
    'https://raw.githubusercontent.com/ALIILAPRO/Proxy/main/http.txt',
    'https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/http/http.txt',
    'https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/http.txt',
    'https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt',
    'https://raw.githubusercontent.com/mertguvencli/http-proxy-list/main/proxy-list/data.txt',
    'https://raw.githubusercontent.com/ProxyScraper/ProxyScraper/main/proxies.txt',
    'https://raw.githubusercontent.com/caliphdev/Proxy-List/master/http.txt',
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt',
    'https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt',
    'https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt',
    'https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt',
    'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt',
    'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt',
    'https://raw.githubusercontent.com/Volodichev/proxy-list/main/http.txt',
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
RAW_PATH = os.path.join(DATA_DIR, "proxies_raw.txt")
ALIVE_PATH = os.path.join(DATA_DIR, "proxies_alive.txt")
os.makedirs(DATA_DIR, exist_ok=True)


# ---------------------------------------------------------------- scraping
def scrape() -> list:
    got = set()
    try:
        with open(RAW_PATH) as f:
            for line in f:
                line = line.strip()
                if ':' in line:
                    got.add(line)
    except Exception:
        pass
    for u in SOURCES:
        try:
            r = requests.get(u, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            for line in r.text.splitlines():
                line = line.strip()
                if ':' in line and len(line) < 40 and not line.startswith('#'):
                    got.add(line)
        except Exception:
            pass
    return sorted(got)


def _validate(proxy):
    try:
        r = requests.get('https://api.ipify.org?format=json',
                         proxies={'http': f'http://{proxy}', 'https': f'http://{proxy}'},
                         timeout=6, headers={'User-Agent': 'Mozilla/5.0'})
        anon = r.json().get('ip') == proxy.split(':')[0]
        return (proxy, r.elapsed.total_seconds(), anon)
    except Exception:
        return None


def refresh(min_workers=120) -> int:
    """Scrape all sources, validate, merge into data/proxies_alive.txt.
    Returns pool size."""
    proxies = scrape()
    log.info("scraped %d raw proxies", len(proxies))
    alive = []
    with concurrent.futures.ThreadPoolExecutor(min_workers) as ex:
        for res in ex.map(_validate, proxies):
            if res:
                alive.append(res)
    anon = [a for a in alive if a[2]]
    log.info("alive: %d | anonymous: %d", len(alive), len(anon))
    pool = {p: (t, a) for p, t, a in anon}
    try:
        with open(ALIVE_PATH) as f:
            for line in f:
                line = line.strip()
                if ':' in line and line not in pool:
                    pool[line] = (9.9, False)  # keep old pool as fallback
    except Exception:
        pass
    ranked = sorted(pool.items(), key=lambda kv: kv[1][0])
    with open(ALIVE_PATH, 'w') as f:
        f.write('\n'.join(p for p, _ in ranked))
    try:
        with open(RAW_PATH, 'w') as f:
            f.write('\n'.join(proxies))
    except Exception:
        pass
    log.info("pool now: %d proxies (fresh anonymous first)", len(pool))
    return len(pool)


# ---------------------------------------------------------------- pool
class ProxyPool:
    """Round-robin proxy pool with per-proxy budgets + cooldowns.

    Tuned to Emailnator: ~250 gens/IP/15min → budget 40 uses, cooldown 900s."""

    def __init__(self, budget: int = 40, cooldown: int = 900,
                 min_size: int = 20):
        self.budget = budget
        self.cooldown = cooldown
        self.min_size = min_size
        self._lock = threading.Lock()
        self._idx = 0
        self._uses = {}
        self._cool = {}
        self._all = []
        self._warm = False
        self._load()

    def _load(self):
        try:
            with open(ALIVE_PATH) as f:
                self._all = [l.strip() for l in f if ':' in l.strip()]
        except Exception:
            self._all = []

    def _ensure_warm(self):
        if self._warm:
            return
        with self._lock:
            if self._warm:
                return
            self._warm = True
        if len(self._all) < self.min_size:
            log.info("proxy pool small (%d) — background refresh…", len(self._all))
            t = threading.Thread(target=self._refresh_bg, daemon=True)
            t.start()

    def _refresh_bg(self):
        try:
            refresh()
            self._load()
        except Exception as e:
            log.warning("proxy refresh failed: %s", e)

    def size(self) -> int:
        return len(self._all)

    def get(self):
        """Next usable proxy or None. Rotates; skips cooling/throttled ones."""
        self._ensure_warm()
        with self._lock:
            n = len(self._all)
            if not n:
                return None
            now = time.time()
            for _ in range(n):
                self._idx = (self._idx + 1) % n
                p = self._all[self._idx]
                if now < self._cool.get(p, 0):
                    continue
                if self._uses.get(p, 0) >= self.budget:
                    self._cool[p] = now + self.cooldown
                    continue
                return p
            return None

    def credit(self, p):
        with self._lock:
            self._uses[p] = self._uses.get(p, 0) + 1

    def punish(self, p):
        with self._lock:
            self._cool[p] = time.time() + self.cooldown

    def http(self, p):
        return {'http': f'http://{p}', 'https': f'http://{p}'}


# ---------------------------------------------------------------- CLI
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    loop = '--loop' in sys.argv
    while True:
        t0 = time.time()
        try:
            refresh()
        except Exception as e:
            log.warning("refresh error: %s", e)
        if not loop:
            break
        log.info("sleeping 600s…")
        time.sleep(600)
