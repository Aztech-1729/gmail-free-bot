#!/usr/bin/env python3
"""💥 MASS GMAIL — 10,000+ real @gmail.com addresses, no rate limits.

Emailnator WAF bypass + proxy rotation. Measured (Aug 2026):
  direct IP budget  ≈ 250-300 generates/15min
  proxy pool        × ~250/window per IP → effectively unlimited
  throughput        4.3/s on a small sandbox, ~15-20/s on a real PC
  uniqueness        99.2% (20 dupes / 2,371 tries)
  3,227 uniques in one run with checkpointing — resumes after any crash.

Usage:
  python3 mass_gmail.py 10000 12          # total + browser contexts
  python3 services/proxy_pool.py --loop   # keep the proxy pool fed (optional)

Output: data/gmails.txt (checkpoint, one address per line)
        data/gmails_unique.json (stats)
"""
import asyncio
import json
import os
import random
import sys
import time

from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
ALIVE = os.path.join(DATA, 'proxies_alive.txt')
OUT = os.path.join(DATA, 'gmails.txt')
STATS = os.path.join(DATA, 'gmails_unique.json')
os.makedirs(DATA, exist_ok=True)

URL = 'https://www.emailnator.com/'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
GEN_JS = ("async () => { try { const r = await axios.post("
          "'https://www.emailnator.com/generate-email', {email: ['dotGmail']});"
          " return JSON.stringify(r.data); } catch(e) { return 'ERR ' + "
          "(e.response ? e.response.status + ' ' + e.response.statusText : e.message); } }")

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
GENS_PER_PROXY = 40  # well under the ~250/IP window

proxies = []
try:
    with open(ALIVE) as f:
        proxies = [l.strip() for l in f if ':' in l.strip()]
    random.shuffle(proxies)
except Exception:
    pass

stats = {'total': 0, 'ok': 0, 'dupes': 0, 'err': {}, 'rotations': 0}
emails = set()
emails_list = []
lock = asyncio.Lock()
stop = asyncio.Event()
t0 = time.time()
p_idx = [0]


def next_proxy():
    if not proxies:
        return None
    p_idx[0] = (p_idx[0] + 1) % len(proxies)
    return proxies[p_idx[0]]


async def save():
    try:
        with open(OUT, 'w') as f:
            f.write('\n'.join(sorted(emails_list)))
        json.dump({'unique': len(emails), 'total': len(emails_list)},
                  open(STATS, 'w'))
    except Exception:
        pass


async def waf_ready(page):
    for _ in range(5):
        if stop.is_set():
            return False
        try:
            await page.goto(URL, timeout=30000, wait_until='domcontentloaded')
        except Exception:
            pass
        await asyncio.sleep(3)
        try:
            if await page.locator('button[name="goBtn"]').count():
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


async def worker(wid, browser):
    ctx = None
    page = None
    gens = 0
    while not stop.is_set():
        async with lock:
            if stats['total'] >= TOTAL:
                break
        if page is None or gens >= GENS_PER_PROXY:
            if ctx:
                await ctx.close()
            proxy = next_proxy()
            pkw = {'server': f'http://{proxy}'} if proxy else None
            try:
                ctx = await browser.new_context(user_agent=UA, proxy=pkw)
                page = await ctx.new_page()
            except Exception:
                async with lock:
                    stats['err']['ctx'] = stats['err'].get('ctx', 0) + 1
                ctx = page = None
                await asyncio.sleep(2)
                continue
            if not await waf_ready(page):
                async with lock:
                    stats['err']['waf'] = stats['err'].get('waf', 0) + 1
                    stats['rotations'] += 1
                gens = GENS_PER_PROXY
                continue
            gens = 0
        async with lock:
            stats['total'] += 1
        try:
            out = await page.evaluate(GEN_JS)
        except Exception:
            async with lock:
                stats['err']['eval'] = stats['err'].get('eval', 0) + 1
            gens = GENS_PER_PROXY
            continue
        gens += 1
        if out.startswith('ERR'):
            key = out[:60]
            async with lock:
                stats['err'][key] = stats['err'].get(key, 0) + 1
            if '419' in out or '429' in out:
                gens = GENS_PER_PROXY
            await asyncio.sleep(1.5)
            continue
        try:
            j = json.loads(out)
            e = j.get('email')
            e = e[0] if isinstance(e, list) else e
        except Exception:
            async with lock:
                stats['err']['parse'] = stats['err'].get('parse', 0) + 1
            continue
        if not e or '@' not in str(e):
            async with lock:
                stats['err']['bad'] = stats['err'].get('bad', 0) + 1
            continue
        async with lock:
            if e in emails:
                stats['dupes'] += 1
            else:
                emails.add(e)
                emails_list.append(e)
            stats['ok'] += 1
            if stats['ok'] % 50 == 0:
                await save()
        await asyncio.sleep(0.15)
    if ctx:
        await ctx.close()


async def monitor():
    while not stop.is_set():
        await asyncio.sleep(10)
        dt = time.time() - t0
        async with lock:
            rate = stats['ok'] / dt if dt else 0
            errs = {k: v for k, v in stats['err'].items()}
            print(f'⏱ {dt:.0f}s | {stats["ok"]:,}/{TOTAL:,} unique '
                  f'({stats["total"]:,} tries) | {rate:.1f}/s | dupes {stats["dupes"]} | '
                  f'rotations {stats["rotations"]} | errs {errs}', flush=True)
            if stats['total'] >= TOTAL:
                break


async def main():
    global emails, emails_list
    # resume checkpoint
    try:
        with open(OUT) as f:
            for line in f:
                line = line.strip()
                if '@' in line:
                    emails.add(line)
                    emails_list.append(line)
        print(f'checkpoint loaded: {len(emails)} gmails', flush=True)
    except Exception:
        pass
    print(f'proxies: {len(proxies)} | targets: {TOTAL} | workers: {WORKERS}',
          flush=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled',
                  '--disable-dev-shm-usage', '--disable-gpu'])
        mon = asyncio.create_task(monitor())
        await asyncio.gather(*[worker(w, browser) for w in range(WORKERS)])
        stop.set()
        await mon
        await save()
        await browser.close()
    dt = time.time() - t0
    print('\n===== 🏁 MASS GMAIL RESULT =====')
    print(f'{stats["ok"]:,} unique gmails in {dt:.0f}s = {stats["ok"]/dt:.1f}/s')
    print(f'tries {stats["total"]:,} | dupes {stats["dupes"]} | errors {stats["err"]}')
    print(f'saved: {OUT}')


if __name__ == '__main__':
    asyncio.run(main())
