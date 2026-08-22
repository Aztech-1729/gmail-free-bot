#!/usr/bin/env python3
"""IG account farm orchestrator — rotates E2B sandboxes (fresh IPs) until
create passes. Runs ONE attempt per sandbox, saves wins to ig_accounts.json."""
import json
import time

from e2b import Sandbox

KEY = 'e2b_a8c512d19f092dc3fd1f623d6c50ee2f2fc50ced'
GMAIL = "instagramacc1e9dh@gmail.com"
APP_PASS = "aycrgslbetqhiaoh"
USER, _, DOM = GMAIL.partition("@")

# single-attempt script (no loop inside — orchestrator handles rotation)
RUNNER = r'''
import base64, imaplib, json, os, random, re, string, sys, time, uuid
import requests as rq
from curl_cffi import requests as cffi_requests

GMAIL, APP_PASS, IG_EMAIL = sys.argv[1], sys.argv[2], sys.argv[3]
W = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
APP_ID = "567067343352427"
BLOKS = "b7737193b91c3a2f4050bdfc9d9ae0f578a93b4181fd43efe549daacba5c7db9"
I = "https://i.instagram.com"
NAMES = ["Alex", "Jordan", "Riley", "Casey", "Morgan", "Quinn", "Avery", "Blake"]
VERSIONS = [("444.0.0.0.77", "399999999"), ("443.0.0.45.82", "395999999"),
            ("443.0.0.48", "389999999"), ("442.0.0.46.79", "389999900")]

def signed(d):
    return "signed_body=SIGNATURE." + rq.utils.quote(json.dumps(d))

def read_code(timeout=100):
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

def build_session(ver, code):
    ua = (f"Instagram {ver} Android (36/14; 420dpi; 1080x2288; "
          f"samsung; SM-G973F; beyond1; exynos9820; en_US; {code})")
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
        "X-Pigeon-Rawclienttime": str(int(time.time()*1000)),
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

def one(ig_email):
    for ver, code in VERSIONS:
        s, csrf, phone_id, device_id, guid = build_session(ver, code)
        waterfall, adid = str(uuid.uuid4()), str(uuid.uuid4())
        try:
            s.get(f"{I}/api/v1/accounts/read_msisdn_header/?device_id={device_id}", timeout=15)
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
                     "_uid": "0", "guid": guid, "usage": "prefill"}, timeout=20)
        time.sleep(2)
        r = s.post(f"{I}/api/v1/accounts/send_verify_email/",
                   data={"phone_id": phone_id, "_csrftoken": csrf, "email": ig_email,
                         "device_id": device_id, "guid": guid, "waterfall_id": waterfall},
                   timeout=20)
        if r.status_code != 200:
            print(json.dumps({"s": "email_fail", "status": r.status_code}), flush=True)
            continue
        vcode = read_code()
        if not vcode:
            print(json.dumps({"s": "code_timeout"}), flush=True)
            continue
        print(json.dumps({"s": "code_ok"}), flush=True)
        username = f"ultra_{random.randint(1000,9999)}_{random.randint(10,99)}"
        password = "Ig" + ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + "!1"
        name = random.choice(NAMES)
        ts = str(int(time.time()))
        sn = base64.encodebytes(f"{ig_email}|{ts}|".encode() + os.urandom(24)).decode().strip()
        data = {
            "jazoest": str(random.randint(22300, 22399)),
            "tos_version": "row", "suggestedUsername": "", "sn_result": "",
            "do_not_auto_login_if_credentials_match": "false",
            "phone_id": phone_id,
            "enc_password": f"#PWD_INSTAGRAM:0:{int(time.time())}:{password}",
            "username": username, "first_name": name,
            "adid": adid, "guid": guid,
            "day": "15", "month": "3", "year": "1995",
            "device_id": device_id, "_uuid": guid,
            "waterfall_id": waterfall, "one_tap_opt_in": "true",
            "email": ig_email, "force_sign_up_code": str(vcode),
            "sn_nonce": sn, "qs_stamp": "",
            "country_codes": '[{"country_code":"1","source":["default"]},{"country_code":"1","source":["uig_via_phone_id"]}]',
            "google_tokens": "[]"}
        time.sleep(2)
        r2 = s.post(f"{I}/api/v1/accounts/create/", data=signed(data), timeout=30)
        body = r2.text
        print(json.dumps({"s": "create", "status": r2.status_code, "body": body[:260]}), flush=True)
        if r2.status_code == 200 and '"account_created": true' in body:
            ck = dict(s.cookies)
            print(json.dumps({"WIN": True, "email": ig_email, "username": username,
                              "password": password, "sessionid": bool(ck.get("sessionid"))}), flush=True)
            return True
        if "email_sharing_limit" in body or "email_is_taken" in body:
            print(json.dumps({"s": "email_burned"}), flush=True)
            return False
        if "needs_upgrade" in body:
            time.sleep(2)
            continue
        return False
    return False

if __name__ == "__main__":
    one(sys.argv[3])
'''

def run_sandbox(ig_email, timeout_s=240):
    sb = Sandbox.create(template='base', api_key=KEY, timeout=600)
    try:
        sb.files.write('/home/user/run.py', RUNNER)
        sb.commands.run("pip install -q curl_cffi requests 2>&1 | tail -1", timeout=180)
        r = sb.commands.run(
            f"python3 -u /home/user/run.py {GMAIL} {APP_PASS} {ig_email}",
            timeout=timeout_s)
        out = r.stdout.strip()
        print(out)
        return out, sb.sandbox_id
    finally:
        try:
            sb.kill()
        except Exception:
            pass

def main():
    wins = []
    # strategy: base email first, then +aliases. On IP spam-flag -> retry same
    # email with a NEW sandbox. On email_burned -> advance alias.
    alias = 0
    emails_tried = {}
    for attempt in range(10):
        ig_email = GMAIL if alias == 0 else f"{USER}+{alias}@{DOM}"
        print(f"\n=== attempt {attempt+1}: {ig_email} ===")
        out, sid = run_sandbox(ig_email)
        if '"WIN": true' in out:
            try:
                win = json.loads([l for l in out.splitlines() if '"WIN": true' in l][-1])
                wins.append(win)
                json.dump(wins, open('/home/user/ig_accounts.json', 'w'), indent=1)
                print(f"🎉 ACCOUNT {len(wins)}: @{win['username']}")
            except Exception:
                pass
            alias += 1
        elif 'email_burned' in out:
            alias += 1
        elif 'feedback_required' in out or 'spam' in out:
            print("IP flagged — next sandbox (fresh IP) will retry")
        elif 'code_timeout' in out or 'email_fail' in out:
            print("chain hiccup — retrying")
        time.sleep(5)
    print(f"\nDONE: {len(wins)} accounts")
    for w in wins:
        print(f"  @{w['username']} | {w['password']} | sessionid={w['sessionid']}")

if __name__ == '__main__':
    main()
