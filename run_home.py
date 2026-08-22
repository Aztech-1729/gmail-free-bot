#!/usr/bin/env python3
"""
🏠 RUN AT HOME — full-auto IG account creator for your PC.

What it does automatically (zero input from you):
  1. Generates identity (username/password/name)
  2. Android signup chain → IG emails a 6-digit code to your gmail
  3. Reads the code from Gmail via IMAP (app password) — no pasting
  4. Submits create → saves @username/password to ig_accounts.json
  5. On spam-flag: waits, then retries with the +2/+3 alias (fresh email string)
  6. On "email used": advances alias automatically

Why home matters: residential IP (never flagged as datacenter) + this exact
engine = the best shot at IG's create step. IG may still require device trust
on some accounts — if a create returns feedback_required after 3 attempts,
the script tells you and moves on (pace: 2-4/day max, cooldown built in).

Setup (once):
  pip install curl_cffi
  (already configured: GMAIL + APP_PASS below)

Run:
  python3 run_home.py          # single account
  python3 run_home.py 5        # try up to 5 accounts (paced)
"""
import base64
import imaplib
import json
import os
import random
import re
import string
import sys
import time
import uuid

import requests as rq
from curl_cffi import requests as cffi_requests

GMAIL = "instagramacc1e9dh@gmail.com"
APP_PASS = "aycrgslbetqhiaoh"

W = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ANDROID_UA = ("Instagram 444.0.0.0.77 Android (36/14; 420dpi; 1080x2288; "
              "samsung; SM-G973F; beyond1; exynos9820; en_US; 399999999)")
APP_ID = "567067343352427"
BLOKS = "b7737193b91c3a2f4050bdfc9d9ae0f578a93b4181fd43efe549daacba5c7db9"
I = "https://i.instagram.com"
FIRSTS = ["Alexander", "Michael", "Jessica", "Daniel", "Sarah", "Christopher",
          "Emily", "Matthew", "Olivia", "Andrew"]
LASTS = ["Johnson", "Williams", "Brown", "Miller", "Davis", "Wilson",
         "Anderson", "Taylor", "Thomas", "White"]
VERSIONS = [("444.0.0.0.77", "399999999"), ("443.0.0.45.82", "395999999"),
            ("443.0.0.48", "389999999"), ("442.0.0.46.79", "389999900")]

RESULTS_FILE = "ig_accounts.json"


def read_code(timeout=150):
    """IMAP: auto-read the newest 6-digit IG code (no human)."""
    t0 = time.time()
    seen = set()
    while time.time() - t0 < timeout:
        try:
            M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            M.login(GMAIL, APP_PASS)
            M.select("inbox")
            typ, data = M.search(None, '(FROM "instagram")')
            for uid in reversed(data[0].split()):
                if uid in seen:
                    continue
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


def build_session(ver, code):
    ua = ANDROID_UA.replace("444.0.0.0.77", ver).replace("399999999", code)
    s = cffi_requests.Session(impersonate="chrome")
    s.headers.update({"User-Agent": W})
    s.get("https://www.instagram.com/", timeout=20)
    csrf = s.cookies.get("csrftoken")
    mid = s.cookies.get("mid")
    phone_id = str(uuid.uuid4())
    device_id = f"android-{uuid.uuid4().hex[:16]}"
    guid = str(uuid.uuid4())
    s.headers.update({
        "User-Agent": ua, "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en_US, en-US", "X-Ig-App-Locale": "en_US",
        "X-Ig-Mapped-Locale": "en_US", "X-Ig-Device-Locale": "en_US",
        "X-Ig-App-Id": APP_ID, "X-Ig-Capabilities": "3brTv10=",
        "X-Ig-Connection-Type": "WIFI", "X-Fb-Client-Ip": "True",
        "X-Fb-Server-Cluster": "True", "X-Fb-Connection-Type": "WIFI",
        "X-Fb-Http-Engine": "MNS/TCP", "X-Tigon-Is-Retry": "False",
        "X-Ig-Device-Id": device_id, "X-Ig-Android-Id": device_id,
        "X-Ig-Family-Device-Id": phone_id,
        "X-Pigeon-Session-Id": str(uuid.uuid4()),
        "X-Pigeon-Rawclienttime": str(int(time.time() * 1000)),
        "X-Ig-Bandwidth-Speed-Kbps": "-1.000",
        "X-Ig-Bandwidth-Totalbytes-B": "0", "X-Ig-Bandwidth-Totaltime-Ms": "0",
        "X-Ig-Timezone-Offset": "19800", "X-Ig-Device-Languages": "en-US",
        "Priority": "u=3", "X-Bloks-Version-Id": BLOKS,
        "x-csrftoken": csrf,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.instagram.com",
        "Referer": "https://www.instagram.com/"})
    if mid:
        s.headers["x-mid"] = mid
    return s, csrf, phone_id, device_id, guid


def create_one(ig_email):
    for ver, code in VERSIONS:
        print(f"  version {ver}…", flush=True)
        s, csrf, phone_id, device_id, guid = build_session(ver, code)
        waterfall, adid = str(uuid.uuid4()), str(uuid.uuid4())
        jazoest = "2" + str(sum(ord(c) for c in phone_id))
        try:
            s.get(f"{I}/api/v1/accounts/read_msisdn_header/?device_id={device_id}",
                  timeout=20)
            launcher = {
                "_csrftoken": csrf, "_uid": "0", "_uuid": guid,
                "phone_id": phone_id, "device_id": device_id, "guid": guid,
                "device": {"manufacturer": "samsung", "device": "beyond1",
                           "model": "SM-G973F", "android_version": 36,
                           "android_release": "14.0.0", "dpi": 420,
                           "resolution": "1080x2288", "cpu": "exynos9820",
                           "font_scale": 1.0, "total_memory_bytes": 5817462784,
                           "memory_class_low": False, "cache_size_bytes": 268435456},
                "login_params": [], "experiments": {}, "one_tap_app_login": False,
                "offline_experiment_group": "caa_iteration_v3_perf_ig_4",
                "pk": "com.instagram.android"}
            s.post(f"{I}/api/v1/launcher/sync/", data=signed(launcher), timeout=20)
        except Exception:
            pass
        time.sleep(2)
        s.post(f"{I}/api/v1/accounts/contact_point_prefill/",
               data={"phone_id": phone_id, "_csrftoken": csrf, "device_id": device_id,
                     "_uid": "0", "guid": guid, "usage": "prefill"}, timeout=25)
        time.sleep(2)
        r = s.post(f"{I}/api/v1/accounts/send_verify_email/",
                   data={"phone_id": phone_id, "_csrftoken": csrf, "email": ig_email,
                         "device_id": device_id, "guid": guid, "waterfall_id": waterfall},
                   timeout=25)
        if r.status_code != 200:
            print(f"  email send: {r.status_code} — retrying next version", flush=True)
            time.sleep(4)
            continue
        print("  ✓ code email sent — reading inbox…", flush=True)
        vcode = read_code()
        if not vcode:
            print("  ✗ no code in inbox", flush=True)
            continue
        print(f"  ✓ code auto-read: {vcode}", flush=True)
        time.sleep(2)
        username = f"ultra_{random.randint(1000, 9999)}_{random.randint(10, 99)}"
        password = "Ig" + ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + "!1"
        name = f"{random.choice(FIRSTS)} {random.choice(LASTS)}"
        ts = str(int(time.time()))
        sn = base64.encodebytes(f"{ig_email}|{ts}|".encode() + os.urandom(24)).decode().strip()
        data = {
            "jazoest": jazoest, "tos_version": "row", "suggestedUsername": "",
            "sn_result": "", "do_not_auto_login_if_credentials_match": "false",
            "phone_id": phone_id,
            "enc_password": f"#PWD_INSTAGRAM:0:{int(time.time())}:{password}",
            "username": username, "first_name": name, "adid": adid, "guid": guid,
            "day": "15", "month": "3", "year": "1995",
            "device_id": device_id, "_uuid": guid,
            "waterfall_id": waterfall, "one_tap_opt_in": "true",
            "email": ig_email, "force_sign_up_code": str(vcode),
            "sn_nonce": sn, "qs_stamp": "",
            "country_codes": '[{"country_code":"1","source":["default"]},{"country_code":"1","source":["uig_via_phone_id"]}]',
            "google_tokens": "[]"}
        r2 = s.post(f"{I}/api/v1/accounts/create/", data=signed(data), timeout=30)
        body = r2.text
        print(f"  create: {r2.status_code} — {body[:120]}", flush=True)
        if r2.status_code == 200 and '"account_created": true' in body:
            ck = dict(s.cookies)
            out = {"email": ig_email, "username": username, "password": password,
                   "sessionid": bool(ck.get("sessionid")), "cookies": ck}
            try:
                wins = json.load(open(RESULTS_FILE))
            except Exception:
                wins = []
            wins.append(out)
            json.dump(wins, open(RESULTS_FILE, "w"), indent=1)
            print(f"  🎉 ACCOUNT CREATED: @{username} (saved to {RESULTS_FILE})", flush=True)
            return "WIN"
        if "email_sharing_limit" in body or "email_is_taken" in body:
            return "EMAIL_USED"
        if "feedback_required" in body or "spam" in body:
            return "SPAM"
        if "needs_upgrade" in body:
            time.sleep(3)
            continue
        return f"REJECTED: {body[:80]}"
    return "ALL_VERSIONS_REJECTED"


def main():
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    user, _, dom = GMAIL.partition("@")
    alias = 0
    wins = 0
    spam_streak = 0
    while wins < want and alias < 12:
        ig_email = GMAIL if alias == 0 else f"{user}+{alias}@{dom}"
        print(f"\n=== account {wins + 1}/{want} — email: {ig_email} ===", flush=True)
        res = create_one(ig_email)
        print(f">>> result: {res}", flush=True)
        if res == "WIN":
            wins += 1
            alias += 1
            spam_streak = 0
            time.sleep(60)  # pace between accounts
        elif res == "EMAIL_USED":
            alias += 1
        elif res == "SPAM":
            spam_streak += 1
            if spam_streak >= 3:
                print("\n⚠️ 3 spam flags in a row. IG is refusing creates from this "
                      "session (device-trust check). Best fix: wait a few hours and "
                      "run again — or use the phone-SMS path once to build trust.",
                      flush=True)
                break
            wait = 300 * spam_streak
            print(f"  cooling down {wait // 60} min…", flush=True)
            time.sleep(wait)
        else:
            alias += 1
            time.sleep(30)
    print(f"\n🏁 done: {wins} account(s) created — saved in {RESULTS_FILE}", flush=True)


if __name__ == "__main__":
    main()
