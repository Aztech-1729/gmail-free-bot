#!/usr/bin/env python3
"""IG signup runner inside E2B sandbox — FRESH IP attack."""
import base64, json, random, re, secrets, string, time, uuid
import requests as rq
try:
    from curl_cffi import requests as cffi_requests
except Exception:
    cffi_requests = None

W = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
APP_ID = "567067343352427"
BLOKS = "b7737193b91c3a2f4050bdfc9d9ae0f578a93b4181fd43efe549daacba5c7db9"
VERSIONS = [("443.0.0.48", "389999999"), ("442.0.0.46.79", "389999900"),
            ("426.0.0.37.68", "383207247"), ("410.1.0.63.71", "381607172")]
I = "https://i.instagram.com"
NAMES = ["Alex", "Jordan", "Riley", "Casey", "Morgan", "Quinn", "Avery", "Blake"]

def inbox_create():
    try:
        r = rq.get("https://api.mail.tm/domains", headers={"User-Agent": W}, timeout=12)
        dom = r.json()["hydra:member"][0]["domain"]
        addr = f"igx{int(time.time())}@{dom}"
        pw = "IgPass" + secrets.token_hex(6)
        r2 = rq.post("https://api.mail.tm/accounts",
                     headers={"Content-Type": "application/json", "User-Agent": W},
                     json={"address": addr, "password": pw}, timeout=12)
        if r2.status_code in (200, 201):
            tok = rq.post("https://api.mail.tm/token",
                          headers={"Content-Type": "application/json", "User-Agent": W},
                          json={"address": addr, "password": pw}, timeout=12).json().get("token")
            if tok:
                return addr, tok, "mailtm"
    except Exception:
        pass
    try:
        j = rq.post("https://api.tempmail.lol/v2/inbox/create",
                    headers={"User-Agent": W}, timeout=12).json()
        if j.get("address"):
            return j["address"], j["token"], "lol"
    except Exception:
        pass
    return None, None, None

def poll_code(addr, tok, prov, timeout=100):
    t0 = time.time()
    seen = set()
    while time.time() - t0 < timeout:
        try:
            if prov == "mailtm":
                msgs = rq.get("https://api.mail.tm/messages",
                              headers={"Authorization": f"Bearer {tok}", "User-Agent": W},
                              timeout=12).json().get("hydra:member", [])
                for m in msgs:
                    j = rq.get(f"https://api.mail.tm/messages/{m['id']}",
                               headers={"Authorization": f"Bearer {tok}", "User-Agent": W},
                               timeout=12).json()
                    text = (j.get("text") or "") + " " + " ".join(j.get("html") or [])
                    codes = re.findall(r"\b\d{6}\b", text)
                    if codes and m.get("id") not in seen:
                        seen.add(m["id"])
                        return codes[0]
            else:
                for m in rq.get(f"https://api.tempmail.lol/v2/inbox?token={tok}",
                                headers={"User-Agent": W}, timeout=12).json().get("emails", []):
                    codes = re.findall(r"\b\d{6}\b", m.get("body") or "")
                    if codes and m.get("id") not in seen:
                        seen.add(m["id"])
                        return codes[0]
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

def signed(d):
    return "signed_body=SIGNATURE." + rq.utils.quote(json.dumps(d))

def main():
    addr, tok, prov = inbox_create()
    if not addr:
        print(json.dumps({"ok": False, "error": "inbox failed"}))
        return
    print(json.dumps({"step": "inbox", "addr": addr}))
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
        time.sleep(3)
        s.post(f"{I}/api/v1/accounts/contact_point_prefill/",
               data={"phone_id": phone_id, "_csrftoken": csrf, "device_id": device_id,
                     "_uid": "0", "guid": guid, "usage": "prefill"}, timeout=20)
        time.sleep(2)
        r = s.post(f"{I}/api/v1/accounts/send_verify_email/",
                   data={"phone_id": phone_id, "_csrftoken": csrf, "email": addr,
                         "device_id": device_id, "guid": guid, "waterfall_id": waterfall},
                   timeout=20)
        print(json.dumps({"step": "send_email", "ver": ver, "status": r.status_code}))
        if r.status_code != 200:
            continue
        vcode = poll_code(addr, tok, prov)
        print(json.dumps({"step": "code", "code": vcode}))
        if not vcode:
            print(json.dumps({"ok": False, "error": "no code"}))
            return
        username = f"ultra_{random.randint(1000,9999)}_{random.randint(10,99)}"
        password = "Ig" + ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + "!1"
        name = random.choice(NAMES)
        ts = str(int(time.time()))
        sn = base64.encodebytes(f"{addr}|{ts}|".encode() + secrets.token_bytes(24)).decode().strip()
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
            "email": addr, "force_sign_up_code": str(vcode),
            "sn_nonce": sn, "qs_stamp": "",
            "country_codes": '[{"country_code":"1","source":["default"]},{"country_code":"1","source":["uig_via_phone_id"]}]',
            "google_tokens": "[]"}
        time.sleep(4)
        r2 = s.post(f"{I}/api/v1/accounts/create/", data=signed(data), timeout=30)
        body = r2.text[:300]
        print(json.dumps({"step": "create", "ver": ver, "status": r2.status_code, "body": body}))
        if r2.status_code == 200:
            ck = dict(s.cookies)
            out = {"ok": True, "email": addr, "username": username,
                   "password": password, "sessionid": bool(ck.get("sessionid")),
                   "cookies": ck}
            print(json.dumps(out))
            return
        if "needs_upgrade" in body:
            time.sleep(3)
            continue
        print(json.dumps({"ok": False, "error": body[:150]}))
        return
    print(json.dumps({"ok": False, "error": "all versions needs_upgrade"}))

if __name__ == "__main__":
    main()
