#!/usr/bin/env python3
"""IG signup through AUTHENTICATED proxies (user-provided residential).
Usage: python3 ig_proxy_signup.py  (edit PROXIES + GMAIL + APP_PASS at top)
Chain proven live: email code auto-read via IMAP. Wall: shared-proxy IPs get
429/feedback_required from IG's 2026 anti-bot stack."""
import base64, imaplib, json, os, random, re, string, time, uuid
import requests as rq
from curl_cffi import requests as cffi_requests

GMAIL = "instagramacc1e9dh@gmail.com"
APP_PASS = "aycrgslbetqhiaoh"
PROXIES = [
    ("31.56.127.193", "7684"), ("45.38.107.97", "6014"),
    ("198.105.121.200", "6462"), ("64.137.96.74", "6641"),
    ("198.23.243.226", "6361"), ("38.154.185.97", "6370"),
    ("191.96.254.138", "6185"),
]
PUSER, PPASS = "gljgdadq", "9ekf76dbe0rm"

W = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
ANDROID_UA = "Instagram 444.0.0.0.77 Android (36/14; 420dpi; 1080x2288; samsung; SM-G973F; beyond1; exynos9820; en_US; 399999999)"
APP_ID = "567067343352427"
BLOKS = "b7737193b91c3a2f4050bdfc9d9ae0f578a93b4181fd43efe549daacba5c7db9"
I = "https://i.instagram.com"
FIRSTS = ["Alexander", "Michael", "Jessica", "Daniel", "Sarah", "Christopher", "Emily", "Matthew", "Olivia", "Andrew"]
LASTS = ["Johnson", "Williams", "Brown", "Miller", "Davis", "Wilson", "Anderson", "Taylor", "Thomas", "White"]

def read_code(timeout=150):
    t0 = time.time()
    seen = set()
    while time.time() - t0 < timeout:
        try:
            M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            M.login(GMAIL, APP_PASS)
            M.select("inbox")
            typ, data = M.search(None, '(FROM "instagram")')
            for uid in reversed(data[0].split()):
                if uid in seen: continue
                seen.add(uid)
                typ, d = M.fetch(uid, "(BODY.PEEK[])")
                raw = d[0][1].decode(errors="replace")
                codes = re.findall(r"\b\d{6}\b", raw)
                if codes:
                    M.logout()
                    return codes[0]
            M.logout()
        except Exception:
            pass
        time.sleep(5)
    return None

def signed(d):
    return "signed_body=SIGNATURE." + rq.utils.quote(json.dumps(d))

def try_create(ip, port):
    PROXY = f"http://{PUSER}:{PPASS}@{ip}:{port}"
    print(f"=== {ip} ===", flush=True)
    s = cffi_requests.Session(impersonate="chrome", proxies={"http": PROXY, "https": PROXY}, timeout=25)
    s.headers.update({"User-Agent": W})
    s.get("https://www.instagram.com/", timeout=20)
    time.sleep(2)
    csrf = s.cookies.get("csrftoken")
    mid = s.cookies.get("mid")
    phone_id = str(uuid.uuid4())
    device_id = f"android-{uuid.uuid4().hex[:16]}"
    guid = str(uuid.uuid4())
    waterfall = str(uuid.uuid4())
    adid = str(uuid.uuid4())
    jazoest = "2" + str(sum(ord(c) for c in phone_id))
    s.headers.update({
        "User-Agent": ANDROID_UA, "x-ig-app-id": APP_ID, "x-ig-capabilities": "3brTv10=",
        "x-ig-connection-type": "WIFI", "X-Ig-Device-Id": device_id,
        "X-Ig-Android-Id": device_id, "X-Ig-Family-Device-Id": phone_id,
        "x-csrftoken": csrf, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept-Language": "en_US, en-US", "X-Bloks-Version-Id": BLOKS,
        "Origin": "https://www.instagram.com", "Referer": "https://www.instagram.com/"})
    if mid: s.headers["x-mid"] = mid
    s.post(f"{I}/api/v1/accounts/contact_point_prefill/",
           data={"phone_id": phone_id, "_csrftoken": csrf, "device_id": device_id,
                 "_uid": "0", "guid": guid, "usage": "prefill"}, timeout=25)
    time.sleep(4)
    r = s.post(f"{I}/api/v1/accounts/send_verify_email/",
               data={"phone_id": phone_id, "_csrftoken": csrf, "email": GMAIL,
                     "device_id": device_id, "guid": guid, "waterfall_id": waterfall}, timeout=25)
    if r.status_code != 200:
        return f"send_email {r.status_code}"
    vcode = read_code()
    print(f"code: {vcode}", flush=True)
    if not vcode:
        return "no code"
    time.sleep(3)
    username = f"ultra_{random.randint(1000,9999)}_{random.randint(10,99)}"
    password = "Ig" + ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + "!1"
    name = f"{random.choice(FIRSTS)} {random.choice(LASTS)}"
    ts = str(int(time.time()))
    sn = base64.encodebytes(f"{GMAIL}|{ts}|".encode() + os.urandom(24)).decode().strip()
    data = {
        "jazoest": jazoest, "tos_version": "row", "suggestedUsername": "", "sn_result": "",
        "do_not_auto_login_if_credentials_match": "false", "phone_id": phone_id,
        "enc_password": f"#PWD_INSTAGRAM:0:{int(time.time())}:{password}",
        "username": username, "first_name": name, "adid": adid, "guid": guid,
        "day": "15", "month": "3", "year": "1995", "device_id": device_id, "_uuid": guid,
        "waterfall_id": waterfall, "one_tap_opt_in": "true",
        "email": GMAIL, "force_sign_up_code": str(vcode),
        "sn_nonce": sn, "qs_stamp": "",
        "country_codes": '[{"country_code":"1","source":["default"]},{"country_code":"1","source":["uig_via_phone_id"]}]',
        "google_tokens": "[]"}
    r2 = s.post(f"{I}/api/v1/accounts/create/", data=signed(data), timeout=30)
    body = r2.text
    print(f"create: {r2.status_code} {body[:160]}", flush=True)
    if r2.status_code == 200 and '"account_created": true' in body:
        ck = dict(s.cookies)
        json.dump({"email": GMAIL, "username": username, "password": password,
                   "cookies": ck}, open("ig_account.json", "w"), indent=1)
        print(f"🎉 WIN via {ip}: @{username}", flush=True)
        return "WIN"
    return body[:120]

if __name__ == "__main__":
    for ip, port in PROXIES:
        res = try_create(ip, port)
        print(f">>> {ip}: {res}\n", flush=True)
        if res == "WIN":
            break
        time.sleep(8)
