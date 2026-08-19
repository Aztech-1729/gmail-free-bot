"""Deep scan test suite — run:  python3 tests/deepscan.py

Covers: live Emailnator core, every handler flow (all buttons + callbacks),
plain-form display, HTML safety, extractor, keyboards & pagination,
Mongo-backed storage, baseline (old-pool-mail filtering), the genmore
fast-generate button, and real multipart document sending.
"""
import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAIL = []


class MockAPI:
    """Records calls; simulates Telegram responses."""
    def __init__(self):
        self.calls = []
        self._mid = 1000

    def _next_id(self):
        self._mid += 1
        return {"ok": True, "result": {"message_id": self._mid}}

    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.calls.append(("send_message", text, parse_mode, reply_markup))
        return self._next_id()

    def edit_message_text(self, chat_id, message_id, text, parse_mode=None, reply_markup=None):
        self.calls.append(("edit", text, parse_mode, reply_markup))
        return self._next_id()

    def send_document(self, chat_id, file_path, caption=None, parse_mode=None):
        self.calls.append(("send_document", chat_id, file_path, caption, parse_mode))
        return self._next_id()

    def answer_callback(self, cb_id, text=None, alert=False):
        self.calls.append(("answer_callback", cb_id, text, alert))
        return {"ok": True}


def check(name, fn):
    try:
        fn()
        print(f"  ✅ {name}")
    except Exception as e:
        FAIL.append(name)
        print(f"  ❌ {name}: {type(e).__name__} {e}")
        traceback.print_exc(limit=2)


# ---------------- 1. live Emailnator core ----------------
def emailnator_core():
    from services.emailnator import EmailnatorClient
    c = EmailnatorClient()
    addr = c.generate()
    assert "@gmail.com" in addr and "+" not in addr, addr
    msgs = c.messages(addr)
    assert isinstance(msgs, list)
    print(f"      minted {addr} ({len(msgs)} msgs)")


# ---------------- 2. handler flows ----------------
def handler_flows():
    from bot.handlers import Handler
    from storage.db import db as database

    for m in database.list_mails(1):
        database.delete_mail(m["id"])

    api = MockAPI()
    h = Handler(api, None)

    h._handle_message({"chat": {"id": 1}, "from": {"id": 1, "username": "u1"}, "text": "/start"})
    assert any("Welcome" in c[1] for c in api.calls if c[0] == "send_message")

    class FakeEmailnator:
        _counter = 0

        def __init__(self):
            self.new_arrived = False

        def generate(self):
            FakeEmailnator._counter += 1
            return f"te.s.t.us.er{FakeEmailnator._counter}@gmail.com"

        def messages(self, a):
            # OLD pool mail always present; NEW mail only after arrive_new()
            msgs = [{"messageID": "oldpool1", "from": "Old Spam <spam@x.com>",
                     "subject": "Old pool mail", "time": "days ago"}]
            if self.new_arrived:
                msgs.append({"messageID": "newmail1", "from": "OTP Sender <otp@x.com>",
                             "subject": "Your code", "time": "Just Now"})
            return msgs

        def message_body(self, a, i):
            return "<html><body><p>Your code is <b>123456</b></p></body></html>"

    fake = FakeEmailnator()
    h2 = Handler(api, fake)

    # generate → baseline must capture oldpool1 (so it never gets delivered)
    h2._handle_message({"chat": {"id": 1}, "from": {"id": 1, "username": "u1"}, "text": "➕ Generate Gmail"})
    gen = [c for c in api.calls if c[0] == "edit" and "ready" in c[1]]
    assert gen, "no generate-edit"
    assert "testuser1@gmail.com" in gen[0][1], "plain form not shown: " + gen[0][1]
    assert "te.s.t" not in gen[0][1], "dotted form leaked into display"
    mail_id = database.list_mails(1)[0]["id"]
    assert database.is_baseline(mail_id, "oldpool1"), "baseline not captured at generate"

    # genmore → instant second generate (bypasses cooldown)
    h2._handle_callback({"id": "cbg", "data": "genmore",
                         "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 1000}})
    assert database.count_mails(1) >= 2, "genmore did not generate"

    # my mails → plain form in list
    h2._handle_message({"chat": {"id": 1}, "from": {"id": 1, "username": "u1"}, "text": "📬 My Mails"})
    list_msgs = [c for c in api.calls if c[0] == "send_message" and "Your mails" in c[1]]
    assert list_msgs and "testuser1@gmail.com" in list_msgs[0][1], "list not plain-form"

    # check inbox BEFORE new mail: baseline hides old pool mail → "empty"
    h2._handle_callback({"id": "cb0", "data": f"check:{mail_id}",
                         "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 999}})
    empties = [c for c in api.calls if c[0] == "send_message" and "Inbox empty" in c[1]]
    assert empties, "baseline mail leaked into check inbox"

    # check inbox AFTER new mail arrives → OTP code surfaced, old mail still hidden
    fake.new_arrived = True
    h2._handle_callback({"id": "cb1", "data": f"check:{mail_id}",
                         "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 1001}})
    bodies = [c[1] for c in api.calls if c[0] == "send_message"]
    assert any("123456" in b for b in bodies), "otp code missing"
    assert not any("Old pool mail" in b for b in bodies), "old pool mail shown"

    # delete confirm → cancel → confirm → ok
    h2._handle_callback({"id": "cb2", "data": f"del:{mail_id}",
                         "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 1002}})
    h2._handle_callback({"id": "cb3", "data": "delno",
                         "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 1002}})
    h2._handle_callback({"id": "cb4", "data": f"del:{mail_id}",
                         "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 1003}})
    h2._handle_callback({"id": "cb5", "data": f"delok:{mail_id}",
                         "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 1003}})
    assert database.get_mail(mail_id) is None, "delete failed"

    # attacker (user 1) can't delete another user's mail
    attacker_mail = database.add_mail(888, "victim.test@gmail.com")
    h2._handle_callback({"id": "cb6", "data": f"delok:{attacker_mail}",
                         "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 1003}})
    assert database.get_mail(attacker_mail) is not None, "attacker deleted victim mail!"
    database.delete_mail(attacker_mail)

    # stats + help
    h2._handle_message({"chat": {"id": 1}, "from": {"id": 1, "username": "u1"}, "text": "📊 Stats"})
    h2._handle_message({"chat": {"id": 1}, "from": {"id": 1, "username": "u1"}, "text": "❓ Help"})

    # cleanup
    for m in database.list_mails(1):
        database.delete_mail(m["id"])


# ---------------- 3. extractor ----------------
def extractor_tests():
    from services.extractor import build_eml, esc, extract_codes, safe_id, strip_tags
    assert extract_codes("Your code is 123456") == ["123456"]
    assert extract_codes("OTP: 98765 ok") == ["98765"]
    assert "123456" in extract_codes("code <b>123456</b> thanks")
    eml = build_eml("A <a@b.c>", "t@x.c", "S", "<html>hi</html>")
    assert "From: A" in eml and "Subject: S" in eml
    assert safe_id("abc/<>def") == "abc___def"
    t = strip_tags("<html><body><p>Hello<br>World</p></body></html>")
    assert "Hello" in t and "World" in t
    assert esc("a<b>&c") == "a&lt;b&gt;&amp;c"


# ---------------- 4. keyboards ----------------
def keyboard_tests():
    from bot import keyboards as kb
    mm = kb.main_menu()
    assert len(mm["keyboard"]) == 3
    assert mm["keyboard"][0][0]["style"] == "primary"
    assert mm["keyboard"][1][0]["style"] == "success"
    assert mm["keyboard"][1][1]["style"] == "danger"

    ma = kb.mail_actions("42")
    styles = [b.get("style") for row in ma["inline_keyboard"] for b in row]
    assert "primary" in styles and "danger" in styles and "success" in styles
    assert any(b["callback_data"] == "genmore" for row in ma["inline_keyboard"] for b in row)

    cd = kb.confirm_delete("42")
    assert cd["inline_keyboard"][0][0]["style"] == "danger"
    assert cd["inline_keyboard"][1][0]["style"] == "success"

    mails = [{"id": str(i), "address": f"m.i{i}@gmail.com", "plain_form": f"mi{i}@gmail.com"} for i in range(13)]
    lst = kb.mail_list_keyboard(mails, 0)
    assert any("mi0@gmail.com" in b["text"] for row in lst["inline_keyboard"] for b in row)
    assert any("Next" in b["text"] for row in lst["inline_keyboard"] for b in row)
    lst2 = kb.mail_list_keyboard(mails, 1)
    assert any("Prev" in b["text"] for row in lst2["inline_keyboard"] for b in row)
    for row in lst["inline_keyboard"]:
        for b in row:
            if "callback_data" in b:
                assert len(b["callback_data"].encode()) <= 64


# ---------------- 5. mailer: baseline skip + html/eml delivery ----------------
def mailer_test():
    from services.mailer import Mailer
    from storage.db import db as database

    class FakeApi:
        def __init__(self): self.calls = []
        def send_message(self, uid, text, parse_mode=None, reply_markup=None):
            self.calls.append(("msg", uid, text))
            return {"ok": True}
        def send_document(self, uid, path, caption=None, parse_mode=None):
            self.calls.append(("doc", uid, path, caption))
            return {"ok": True}

    class FakeEmailnator:
        def __init__(self):
            self.messages_map = {}
        def messages(self, a):
            return self.messages_map.get(a, [])
        def message_body(self, a, i):
            return "<html><body>code <b>654321</b></body></html>"

    uid = 555002
    mail_id = database.add_mail(uid, "zo.zo.t@gmail.com")
    fake = FakeEmailnator()

    api = FakeApi()
    m = Mailer(api, database, interval=5)
    m.emailnator = fake

    # baseline: one old pool message that must NEVER be delivered
    database.mark_baseline(mail_id, ["OLD1"])
    fake.messages_map["zo.zo.t@gmail.com"] = [
        {"messageID": "OLD1", "from": "Old <o@x.c>", "subject": "Old", "time": "old"},
        {"messageID": "NEW1", "from": "OTP <n@x.c>", "subject": "Code", "time": "Now"},
    ]

    m._poll_mail(database.get_mail(mail_id))

    texts = [c[2] for c in api.calls if c[0] == "msg"]
    assert any("654321" in t for t in texts), "mailer missed OTP"
    assert not any("Old" in t and "New mail" in t for t in texts), "baseline mail was delivered"
    assert any("zozot@gmail.com" in t for t in texts), "mailer not plain-form"

    docs = [c for c in api.calls if c[0] == "doc"]
    assert len(docs) == 2, f"expected 2 docs (html+eml), got {len(docs)}"
    assert docs[0][2].endswith(".html") and docs[1][2].endswith("_raw.eml"), "wrong doc files"
    assert database.is_delivered(mail_id, "NEW1") and not database.is_delivered(mail_id, "OLD1")

    # second poll → nothing new delivered (dedupe)
    api.calls.clear()
    m._poll_mail(database.get_mail(mail_id))
    assert not [c for c in api.calls if c[0] == "msg"], "duplicate delivery"

    database.delete_mail(mail_id)


# ---------------- 6. real multipart send_document ----------------
def multipart_test():
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            captured["body"] = body
            captured["content_type"] = self.headers.get("Content-Type", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "result": {"message_id": 1}}).encode())

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    from bot.api import BotAPI
    # point the client at our local server by monkeypatching the API base
    import bot.api as apimod
    orig = apimod.API
    apimod.API = f"http://127.0.0.1:{srv.server_port}/bot{{token}}/{{method}}"
    try:
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write("<html><body>hello</body></html>")
            fpath = f.name
        api2 = BotAPI("dummy:token")
        res = api2.send_document(123456, fpath, caption="test", parse_mode="HTML")
        assert res.get("ok"), res
        body = captured["body"]
        assert b"123456" in body, "chat_id missing from multipart body"
        assert b"filename=" in body, "file part missing"
        assert b".html" in body, "html filename missing"
        assert b"test" in body, "caption missing"
        assert "multipart/form-data" in captured["content_type"], "not multipart"
        os.unlink(fpath)
    finally:
        apimod.API = orig
        srv.shutdown()


print("=== DEEP SCAN (v3 — baseline + attachments) ===")
check("emailnator core (live)", emailnator_core)
check("handler flows (buttons + plain + genmore + baseline + security)", handler_flows)
check("extractor + esc + eml builder", extractor_tests)
check("keyboards + colored styles + pagination + callback limits", keyboard_tests)
check("mailer (baseline skip, html+eml, dedupe)", mailer_test)
check("send_document multipart (chat_id + html file + caption)", multipart_test)
print(f"\n=== RESULT: {6 - len(FAIL)}/6 passed | failures: {FAIL if FAIL else 'NONE'} ===")
