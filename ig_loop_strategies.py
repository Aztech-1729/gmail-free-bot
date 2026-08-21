"""IG v5 — cycle 3 email strategies: dotted / plain-form / plus-alias gmails."""
import base64, json, random, re, secrets, string, time, urllib.parse, uuid
import requests as rq
from curl_cffi import requests as cffi_requests

W = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
APP_ID = "567067343352427"
BLOKS = "b7737193b91c3a2f4050bdfc9d9ae0f578a93b4181fd43efe549daacba5c7db9"
I = "https://i.instagram.com"
NAMES = ["Alex", "Jordan", "Riley", "Casey", "Morgan", "Quinn", "Avery", "Blake"]

def mint_gmail(mode):
    """mode: 'dot' | 'plain' | 'plus' → returns (session, headers, addr_for_ig, addr_for_read)"""
    s = cffi_requests.Session(impersonate="chrome")
    s.headers.update({"User-Agent": W})
    s.get("https://www.emailnator.com/", timeout=20)
    xsrf = urllib.parse.unquote(s.cookies.get("XSRF-TOKEN") or "")
    h = {"X-XSRF-TOKEN": xsrf, "X-Requested-With": "XMLHttpRequest",
         "Accept": "application/json", "Origin": "https://www.emailnator.com",
         "Referer": "https://www.emailnator.com/inbox/"}
    types = {"dot": ["dotGmail"], "plain": ["dotGmail"], "plus": ["plusGmail"]}[mode]
    r = s.post("https://www.emailnator.com/generate-email",
               json={"email": types}, headers=h, timeout=30)
    if r.status_code != 200:
        return None
    e = r.json().get("email")
    e = e[0] if isinstance(e, list) else e
    if not e or "@" not in str(e):
        return None
    e = str(e)
    read_addr = e
    ig_addr = e
    if mode == "plain":
        user, _, dom = e.partition("@")
        ig_addr = user.replace(".", "") + "@" + dom
    if "gmail.com" not in ig_addr and "googlemail" not in ig_addr:
        return None
    return s, h, ig_addr, read_addr

def poll_code(s, h, read_addr, timeout=100):
    t0 = time.time()
    seen = set()
    while time.time() - t0 < timeout:
        try:
            r = s.post("https://www.emailnator.com/message-list",
                       json={"email": read_addr}, headers=h, timeout=30)
            if r.status_code == 200:
                data = r.json()
                msgs = data.get("messageData") if isinstance(data, dict) else (data or [])
                for m in msgs:
                    mid = m.get("messageID")
                    if not mid or mid in seen or mid == "ADSVPN":
                        continue
                    seen.add(mid)
                    r2 = s.post("https://www.emailnator.com/message-list",
                                json={"email": read_addr, "messageID": mid}, headers=h, timeout=30)
                    if r2.status_code == 200:
                        codes = re.findall(r"\b\d{6}\b", r2.text)
                        if codes:
                            return codes[0]
        except Exception:
            pass
        time.sleep(4)
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

def signed(d):
    return "signed_body=SIGNATURE." + rq.utils.quote(json.dumps(d))

def attempt(n, mode):
    g = mint_gmail(mode)
    if not g:
        print(json.dumps({"n": n, "mode": mode, "error": "mint failed"}))
        return False
    s, h, ig_addr, read_addr = g
    print(json.dumps({"n": n, "mode": mode, "ig_email": ig_addr}))
    ver, code = ("443.0.0.48", "389999999")
    s2, csrf, phone_id, device_id, guid = build_session(ver, code)
    waterfall, adid = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        s2.get(f"{I}/api/v1/accounts/read_msisdn_header/?device_id={device_id}", timeout=15)
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
        s2.post(f"{I}/api/v1/launcher/sync/", data=signed(launcher), timeout=20)
    except Exception:
        pass
    time.sleep(3)
    s2.post(f"{I}/api/v1/accounts/contact_point_prefill/",
            data={"phone_id": phone_id, "_csrftoken": csrf, "device_id": device_id,
                  "_uid": "0", "guid": guid, "usage": "prefill"}, timeout=20)
    time.sleep(2)
    r = s2.post(f"{I}/api/v1/accounts/send_verify_email/",
                data={"phone_id": phone_id, "_csrftoken": csrf, "email": ig_addr,
                      "device_id": device_id, "guid": guid, "waterfall_id": waterfall},
                timeout=20)
    if r.status_code != 200:
        print(json.dumps({"n": n, "mode": mode, "error": f"send_email {r.status_code}: {r.text[:80]}"}))
        return False
    vcode = poll_code(s, h, read_addr)
    print(json.dumps({"n": n, "mode": mode, "code": vcode}))
    if not vcode:
        return False
    username = f"ultra_{random.randint(1000,9999)}_{random.randint(10,99)}"
    password = "Ig" + ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + "!1"
    name = random.choice(NAMES)
    ts = str(int(time.time()))
    sn = base64.encodebytes(f"{ig_addr}|{ts}|".encode() + secrets.token_bytes(24)).decode().strip()
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
        "email": ig_addr, "force_sign_up_code": str(vcode),
        "sn_nonce": sn, "qs_stamp": "",
        "country_codes": '[{"country_code":"1","source":["default"]},{"country_code":"1","source":["uig_via_phone_id"]}]',
        "google_tokens": "[]"}
    time.sleep(3)
    r2 = s2.post(f"{I}/api/v1/accounts/create/", data=signed(data), timeout=30)
    body = r2.text[:300]
    print(json.dumps({"n": n, "mode": mode, "create": r2.status_code, "body": body[:160]}))
    if r2.status_code == 200 and '"account_created": true' in body:
        ck = dict(s2.cookies)
        print(json.dumps({"WIN": True, "email": ig_addr, "username": username,
                          "password": password, "sessionid": bool(ck.get("sessionid")),
                          "cookies": ck}))
        return True
    if "needs_upgrade" in body:
        time.sleep(2)
    return False

def main():
    for n in range(1, 11):
        mode = ["plain", "plus", "dot"][(n - 1) % 3]
        try:
            if attempt(n, mode):
                return
        except Exception as e:
            print(json.dumps({"n": n, "mode": mode, "error": str(e)[:100]}))
        time.sleep(6)
    print(json.dumps({"WIN": False, "note": "10 attempts done"}))

if __name__ == "__main__":
    main()
