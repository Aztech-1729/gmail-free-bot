"""Update handlers — fully button-driven Bot API flows."""
import json
import logging
import os
import re
import tempfile
import threading
import time

from bot import keyboards as kb
from config import GENERATE_COOLDOWN, REQUIRED_CHANNEL, REQUIRED_CHANNEL_URL
from services.emailnator import EmailnatorClient, EmailnatorError
from services.extractor import esc, extract_codes, parse_headers, render_mail, strip_tags
from services.proxy_pool import ProxyPool
from services.unlimited_mail import ProtonOTP, get_mailer
from services.x_signup import XSignupError, initiate_signup, verify_otp_and_create
from storage.db import db

log = logging.getLogger("handlers")

WELCOME = (
    "👋 <b>Welcome to GMAILS FREE!</b>\n\n"
    "I generate <b>real @gmail.com</b> addresses for receiving OTP codes — "
    "free, unlimited, instant.\n\n"
    "Any mail that arrives is sent to you <b>immediately</b> as:\n"
    "• a summary with OTP codes extracted 🔑\n"
    "• an <b>HTML file</b> of the email\n"
    "• the <b>raw .eml</b> file\n\n"
    "Use the buttons below ⬇️"
)

HELP = (
    "❓ <b>How to use</b>\n\n"
    "➕ <b>Generate Gmail</b> — mint a fresh @gmail.com address.\n"
    "📥 <b>Check Inbox</b> — read mail for any address manually.\n"
    "🗑 <b>Delete Mail</b> — remove an address (mail stops being polled).\n"
    "🔐 <b>Create X Acc</b> — send <code>/createx jak.sen.d.a.n.m.ar.k@gmail.com</code> → I signup on X, poll OTP, DM you OTP, reply OTP → I save session file.\n"
    "📊 <b>Stats</b> — your totals.\n\n"
    "1️⃣ Press <b>Generate</b>, copy the address.\n"
    "2️⃣ Use it on any site / app that sends an OTP.\n"
    "3️⃣ The code arrives here in seconds — with HTML + raw files.\n\n"
    "⚠️ These are pooled, passwordless inboxes — perfect for one-time "
    "OTPs, not for permanent account recovery."
)

JOIN_MSG = (
    "🔒 <b>Join our channel to use the bot</b>\n\n"
    "You must be a member of the channel below before using GMAILS FREE:\n"
    f"👉 {REQUIRED_CHANNEL_URL}\n\n"
    "Press <b>Join Channel</b>, then <b>✅ Verify</b>."
)

_cooldown: dict = {}
_lock = threading.Lock()
_mass_jobs: dict = {}       # user_id -> {'done': int, 'total': int, 'running': bool}
_otp_pool: ProxyPool = None
_otp_pool_lock = threading.Lock()
_x_pending: dict = {}       # user_id -> XSignupSession (awaiting OTP)
_x_pending_lock = threading.Lock()


class Handler:
    def __init__(self, api, emailnator: EmailnatorClient):
        self.api = api
        self.emailnator = emailnator

    # ------------------------------------------------------------------ #
    def handle_update(self, update: dict):
        """Dispatch one Bot API update."""
        try:
            if "message" in update:
                self._handle_message(update["message"])
            elif "callback_query" in update:
                self._handle_callback(update["callback_query"])
        except Exception as e:
            log.exception("update handling failed: %s", e)

    # ------------------------------------------------------------------ #
    def _is_member(self, user_id: int) -> bool:
        """True when the user has joined the required channel (or none set)."""
        if not REQUIRED_CHANNEL:
            return True
        try:
            r = self.api.get_chat_member(REQUIRED_CHANNEL, user_id)
            if r.get("ok"):
                return r["result"].get("status", "") in (
                    "member", "administrator", "creator")
        except Exception:
            pass
        return False

    def _require_member(self, chat_id: int, user_id: int) -> bool:
        """Send the join prompt when not a member; returns True if allowed."""
        if self._is_member(user_id):
            return True
        self.api.send_message(chat_id, JOIN_MSG, parse_mode="HTML",
                              reply_markup=kb.join_channel_menu(REQUIRED_CHANNEL_URL))
        return False

    # ------------------------------------------------------------------ #
    def _handle_message(self, msg: dict):
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        username = msg["from"].get("username", "")
        text = msg.get("text", "") or ""

        db.add_user(user_id, username)

        if not self._require_member(chat_id, user_id):
            return

        if text == "/start":
            self.api.send_message(chat_id, WELCOME, parse_mode="HTML",
                                   reply_markup=kb.main_menu())
        elif text.startswith("/gmails"):
            self._mass_generate(chat_id, user_id, text)
        elif text.startswith("/otp"):
            self._send_otp(chat_id, user_id, text)
        elif text.startswith("/proxies"):
            self._proxy_status(chat_id)
        elif text == "♾️ Mass Gmails":
            self._mass_generate(chat_id, user_id, "/gmails 10")
        elif text == "🔐 Create X Acc" or text.startswith("/createx"):
            # /createx jak.sen.d.a.n.m.ar.k@gmail.com
            parts = text.split()
            email = parts[1] if len(parts) > 1 else ""
            if email and "@" in email:
                self._x_create(chat_id, user_id, email.strip())
            else:
                self.api.send_message(chat_id,
                    "🔐 <b>Create X Account</b>\n\nSend your dotted gmail:\n<code>/createx jak.sen.d.a.n.m.ar.k@gmail.com</code>\n\n"
                    "I'll signup on X, X will email OTP to that gmail, I'll DM you the OTP → reply with the OTP to finish and get your session file.",
                    parse_mode="HTML")
        elif re.fullmatch(r"\d{4,8}", text.strip()):
            # 4-8 digit OTP reply for pending X signup
            with _x_pending_lock:
                pending = _x_pending.get(user_id)
            if pending:
                self._x_verify(chat_id, user_id, text.strip())
                return
            self.api.send_message(chat_id, "Use the buttons below ⬇️", reply_markup=kb.main_menu())
        elif "@gmail.com" in text.lower() and len(text.strip().split()) == 1:
            # bare dotted gmail sent — treat as /createx <gmail>
            self._x_create(chat_id, user_id, text.strip())
        elif text == "➕ Generate Gmail":
            self._generate(chat_id, user_id)
        elif text == "📬 My Mails":
            self._show_mail_list(chat_id, user_id, page=0)
        elif text == "🗑 Delete Mail":
            self._show_mail_list(chat_id, user_id, page=0)
        elif text == "📊 Stats":
            self._stats(chat_id, user_id)
        elif text == "❓ Help":
            self.api.send_message(chat_id, HELP, parse_mode="HTML",
                                   reply_markup=kb.main_menu())
        else:
            self.api.send_message(chat_id, "Use the buttons below ⬇️",
                                   reply_markup=kb.main_menu())

    # ------------------------------------------------------------------ #
    def _generate(self, chat_id, user_id, skip_cooldown=False):
        now = time.time()
        with _lock:
            if not skip_cooldown and now - _cooldown.get(user_id, 0) < GENERATE_COOLDOWN:
                self.api.send_message(chat_id, "⏳ One moment…", reply_markup=kb.main_menu())
                return
            _cooldown[user_id] = now

        # Fire-and-forget status message (non-fatal)
        status_id = None
        try:
            status = self.api.send_message(chat_id, "⏳ Generating your gmail…")
            status_id = status["result"]["message_id"] if status.get("ok") else None
        except Exception:
            pass

        # 🔒 GMAIL-ONLY generation — real @gmail.com, nothing else.
        #   Path 1: Playwright WAF bypass (proxy-rotated, no effective limit)
        #   Path 2: legacy curl_cffi client (also real @gmail.com)
        #   Both paths are gmail-only; anything else is discarded.
        mail_id = None
        address = None
        provider = "emailnator"
        mailer = None
        try:
            mailer = get_mailer()
            res = mailer.generate()
            address = res["address"]
        except Exception as e:
            log.warning("gmail (waf-bypass) generate failed: %s — legacy fallback", e)
        if not address:
            for attempt in range(10):
                try:
                    address = self.emailnator.generate()
                    break
                except EmailnatorError as e2:
                    log.warning("legacy gen attempt %d: %s", attempt + 1, e2)
                except Exception as e2:
                    log.warning("legacy gen attempt %d: %s", attempt + 1, e2)
                if attempt < 9:
                    time.sleep(min(2 ** attempt + 0.5, 8))
        if address and "gmail.com" not in str(address):
            log.warning("non-gmail address discarded: %s", address)
            address = None
        if address:
            for attempt in range(5):
                try:
                    mail_id = db.add_mail(user_id, address, provider=provider)
                    break
                except Exception as e:
                    log.warning("db add attempt %d: %s", attempt + 1, e)
                    time.sleep(1.5)
        if not mail_id:
            # Non-fatal: try to inform user, but don't crash
            try:
                self.api.send_message(
                    chat_id,
                    "⚠️ <b>Generation failed.</b>\n\n"
                    "🔧 Gmail-only mode needs Chromium on the server:\n"
                    "<code>pip install playwright</code>\n"
                    "<code>playwright install chromium --with-deps</code>\n\n"
                    "Then restart the bot.",
                    parse_mode="HTML")
            except Exception:
                pass
            return
        plain = address.split("@")[0].replace(".", "") + "@" + address.split("@")[1]

        # Baseline snapshot: this pooled inbox may contain OLD mail from a
        # previous pool tenant. Mark everything that already exists as
        # baseline so ONLY new mail (arriving after this moment) is delivered.
        # Routed by provider: emailnator vs arsenal readers.
        try:
            old_ids = set()
            if provider != "emailnator":
                try:
                    if mailer is None:
                        mailer = get_mailer()
                    for m in mailer.read_messages(address, provider):
                        if m.get("messageID"):
                            old_ids.add(m["messageID"])
                except Exception as e:
                    log.warning("arsenal baseline failed for %s: %s", address, e)
            else:
                for form in {address, plain}:
                    try:
                        for m in self.emailnator.messages(form):
                            if m.get("messageID"):
                                old_ids.add(m["messageID"])
                    except Exception:
                        pass
            if old_ids:
                db.mark_baseline(mail_id, old_ids)
        except Exception as e:
            log.warning("baseline snapshot failed for %s: %s", mail_id, e)

        # Final message — try edit, fallback to send; never crash
        text = (f"✅ <b>Your gmail is ready!</b>\n\n"
                f"📧 <b>{esc(address)}</b>\n\n"
                f"Use it anywhere an OTP is needed — I'll forward every mail "
                f"here instantly with HTML + raw files.")
        kb_markup = kb.mail_actions(mail_id)
        ok = False
        if status_id:
            try:
                self.api.edit_message_text(chat_id, status_id, text, parse_mode="HTML", reply_markup=kb_markup)
                ok = True
            except Exception:
                pass
        if not ok:
            try:
                self.api.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb_markup)
            except Exception as e:
                log.warning("failed to send success msg to %s: %s", user_id, e)

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # ♾️ PRO COMMANDS — mass generate · OTP sender · proxy status
    # ------------------------------------------------------------------ #
    def _mass_generate(self, chat_id, user_id, text):
        """`/gmails N` — mint N addresses via the no-rate-limit arsenal."""
        parts = text.split()
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        n = max(1, min(n, 500))
        with _lock:
            job = _mass_jobs.get(user_id)
            if job and job["running"]:
                self.api.send_message(
                    chat_id, f"⏳ Mass job already running: {job['done']}/{job['total']}")
                return
            _mass_jobs[user_id] = {"done": 0, "total": n, "running": True}
        self.api.send_message(
            chat_id,
            f"♾️ <b>Mass generation started</b>\n\n"
            f"• {n} × real @gmail.com\n"
            f"• Playwright WAF bypass + proxy rotation = no rate limits\n\n"
            f"⏳ Generating… roughly {max(5, n // 2)}s.",
            parse_mode="HTML")

        def run():
            import io
            import time as _t
            mailer = None
            try:
                mailer = get_mailer()
            except Exception:
                pass
            lines = []
            job = _mass_jobs.get(user_id, {})
            total = job.get("total", n)
            for i in range(total):
                address = None
                provider = "emailnator"
                if mailer is not None:
                    try:
                        res = mailer.generate()
                        address = res["address"]
                        provider = res["provider"]
                    except Exception:
                        pass
                if address is None:
                    try:
                        address = self.emailnator.generate()
                        provider = "emailnator"
                    except Exception:
                        address = None
                if address and "gmail.com" in str(address):
                    lines.append(address)
                    try:
                        db.add_mail(user_id, address, provider=provider)
                    except Exception:
                        pass
                with _lock:
                    _mass_jobs[user_id] = {"done": i + 1, "total": total, "running": True}
                _t.sleep(0.3)
            with _lock:
                _mass_jobs[user_id]["running"] = False
            buf = io.BytesIO(("\n".join(lines) + "\n").encode())
            self.api.send_document(
                chat_id, buf, filename=f"gmails_{len(lines)}.txt",
                caption=f"♾️ <b>Mass done:</b> {len(lines)}/{total} addresses\n\n"
                        "Send each to any site — mails land in <b>My Mails</b>.",
                parse_mode="HTML")

        threading.Thread(target=run, daemon=True).start()

    def _send_otp(self, chat_id, user_id, text):
        """`/otp email` — keyless Proton verification-code mail (proxy-rotated)."""
        global _otp_pool
        parts = text.split()
        if len(parts) < 2 or "@" not in parts[1]:
            self.api.send_message(chat_id, "Usage: /otp email@domain.com")
            return
        address = parts[1]
        with _otp_pool_lock:
            if _otp_pool is None:
                _otp_pool = ProxyPool()
        self.api.send_message(chat_id, f"📤 Sending OTP code mail to {address}…")
        status, body = ProtonOTP().send(address, _otp_pool)
        if status == 200:
            self.api.send_message(
                chat_id, f"✅ <b>OTP mail sent</b> to {address}\n\n"
                         "Proton verification code (6 digits) — check inbox/spam.",
                parse_mode="HTML")
        else:
            self.api.send_message(
                chat_id, f"⚠️ OTP send failed ({status})\n{body[:100]}")

    def _proxy_status(self, chat_id):
        pool = ProxyPool()
        self.api.send_message(
            chat_id,
            f"♾️ <b>Proxy pool</b>\n\n"
            f"• pool size: {pool.size()}\n"
            f"• budget: 40 uses/proxy, 15-min cooldown\n"
            f"• refresh: python3 services/proxy_pool.py --loop\n\n"
            f"Emailnator ≈ 250-300 gens/IP/window × pool = no effective limit.",
            parse_mode="HTML")

    def _x_create(self, chat_id, user_id, email: str):
        email = email.strip()
        if not re.fullmatch(r"[^@\s]+@gmail\.com", email, re.I):
            self.api.send_message(chat_id, "❌ Send a valid <code>@gmail.com</code> (dotted allowed), e.g. <code>jak.sen.d.a.n.m.ar.k@gmail.com</code>", parse_mode="HTML")
            return
        # ensure we track this gmail in My Mails so OTP poller catches it
        try:
            db.add_mail(user_id, email)
        except Exception:
            pass
        self.api.send_message(chat_id, f"🔐 <b>X signup started</b>\n\n📧 <code>{esc(email)}</code>\n👤 Handle: generating…\n\nTriggering X to send OTP — watch this chat for the code.", parse_mode="HTML")
        def run():
            try:
                sess = initiate_signup(email)
                with _x_pending_lock:
                    _x_pending[user_id] = sess
                self.api.send_message(chat_id,
                    f"✅ X init OK\n\n📧 <code>{esc(email)}</code>\n👤 <b>@{esc(sess.handle)}</b>\n🔑 <code>{esc(sess.password)}</code>\n\n"
                    f"X should email OTP to that gmail now. I'll also DM you the OTP when it lands. "
                    f"Reply with the <b>6-digit OTP</b> (e.g. <code>123456</code>) to finish and get your session file.",
                    parse_mode="HTML")
            except XSignupError as e:
                self.api.send_message(chat_id, f"❌ X init failed: {esc(str(e))}", parse_mode="HTML")
            except Exception as e:
                self.api.send_message(chat_id, f"❌ X init error: {esc(str(e)[:200])}", parse_mode="HTML")
        threading.Thread(target=run, daemon=True).start()

    def _x_verify(self, chat_id, user_id, otp: str):
        with _x_pending_lock:
            sess = _x_pending.get(user_id)
        if not sess:
            self.api.send_message(chat_id, "❌ No pending X signup. Send <code>/createx your@gmail.com</code> first.", parse_mode="HTML")
            return
        self.api.send_message(chat_id, f"⏳ Verifying OTP <code>{esc(otp)}</code> for <b>@{esc(sess.handle)}</b>…", parse_mode="HTML")
        def run():
            try:
                result = verify_otp_and_create(sess, otp)
                # save session file
                data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sessions")
                os.makedirs(data_dir, exist_ok=True)
                fname = f"{result['handle']}_{int(time.time())}.json"
                fpath = os.path.join(data_dir, fname)
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(result["cookies"], f, indent=2)
                # send file
                with open(fpath, "rb") as f:
                    self.api.send_document(chat_id, f, filename=fname, caption=(
                        f"✅ <b>X account created!</b>\n\n"
                        f"📧 {esc(result['email'])}\n"
                        f"👤 <b>@{esc(result['handle'])}</b> — {esc(result['name'])}\n"
                        f"🔑 <code>{esc(result['password'])}</code>\n\n"
                        f"Session file attached — same shape as <code>session1.json</code>. Drop it into your scraper as <code>session1.json</code> or keep as backup."
                    ), parse_mode="HTML")
                with _x_pending_lock:
                    _x_pending.pop(user_id, None)
            except XSignupError as e:
                self.api.send_message(chat_id, f"❌ OTP verify failed: {esc(str(e))}\n\nTry again — reply with correct OTP.", parse_mode="HTML")
            except Exception as e:
                self.api.send_message(chat_id, f"❌ Verify error: {esc(str(e)[:200])}", parse_mode="HTML")
        threading.Thread(target=run, daemon=True).start()

    def _show_mail_list(self, chat_id, user_id, page=0):
        mails = db.list_mails(user_id)
        if not mails:
            self.api.send_message(
                chat_id,
                "You don't have any mails yet.\nPress ➕ Generate Gmail to create one!",
                reply_markup=kb.main_menu())
            return
        body = "\n".join(f"{i+1}. <code>{esc(m['address'])}</code>" for i, m in enumerate(mails))
        self.api.send_message(
            chat_id,
            f"📬 <b>Your mails</b>\n\n{body}\n\n[📥 Check] [🗑 Delete] per row",
            parse_mode="HTML", reply_markup=kb.mail_list_keyboard(mails, page))

    def _stats(self, chat_id, user_id):
        n_mails = db.count_mails(user_id)
        n_delivered = db.delivered_count(user_id)
        self.api.send_message(
            chat_id,
            f"📊 <b>Your stats</b>\n\n"
            f"📧 Mailboxes: <b>{n_mails}</b>\n"
            f"📬 Messages delivered: <b>{n_delivered}</b>",
            parse_mode="HTML", reply_markup=kb.main_menu())

    # ------------------------------------------------------------------ #
    def _handle_callback(self, cb: dict):
        cb_id = cb["id"]
        data = cb.get("data", "")
        user_id = cb["from"]["id"]
        chat_id = cb["message"]["chat"]["id"]
        message_id = cb["message"]["message_id"]

        if data == "verify":
            self._verify(chat_id, user_id, cb_id, message_id)
            return
        if not self._is_member(user_id):
            self.api.answer_callback(cb_id, "🔒 Join the channel first", alert=True)
            self.api.send_message(
                chat_id, JOIN_MSG, parse_mode="HTML",
                reply_markup=kb.join_channel_menu(REQUIRED_CHANNEL_URL))
            return

        if data.startswith("check:"):
            self._check_inbox(chat_id, user_id, cb_id, data.split(":", 1)[1])
        elif data.startswith("del:"):
            mail_id = data.split(":", 1)[1]
            mail = db.get_mail(mail_id)
            if not mail or mail["user_id"] != user_id:
                self.api.answer_callback(cb_id, "Not found", alert=True)
                return
            self.api.edit_message_text(
                chat_id, message_id,
                f"🗑 Delete <code>{esc(mail['address'])}</code>?\n"
                f"All its delivered history will be removed too.",
                parse_mode="HTML", reply_markup=kb.confirm_delete(mail_id))
            self.api.answer_callback(cb_id)
        elif data.startswith("delok:"):
            mail_id = data.split(":", 1)[1]
            mail = db.get_mail(mail_id)
            if not mail or mail["user_id"] != user_id:
                self.api.answer_callback(cb_id, "Not found", alert=True)
                return
            db.delete_mail(mail_id)
            self.api.edit_message_text(
                chat_id, message_id, f"✅ Deleted <code>{esc(mail['address'])}</code>.",
                parse_mode="HTML")
            self.api.answer_callback(cb_id, "Deleted")
        elif data == "genmore":
            self.api.answer_callback(cb_id, "Generating…")
            self._generate(chat_id, user_id, skip_cooldown=True)
        elif data == "delno":
            self.api.edit_message_text(chat_id, message_id, "👍 Kept.")
            self.api.answer_callback(cb_id)
        elif data.startswith("page:"):
            self.api.answer_callback(cb_id)
            page = int(data.split(":", 1)[1])
            mails = db.list_mails(user_id)
            if mails:
                body = "\n".join(f"{i+1}. <code>{esc(m['address'])}</code>" for i, m in enumerate(mails))
                self.api.edit_message_text(
                    chat_id, message_id,
                    f"📬 <b>Your mails</b>\n\n{body}\n\n[📥 Check] [🗑 Delete] per row",
                    parse_mode="HTML", reply_markup=kb.mail_list_keyboard(mails, page))
        else:
            self.api.answer_callback(cb_id)

    # ------------------------------------------------------------------ #
    def _verify(self, chat_id, user_id, cb_id, message_id):
        """Verify button — re-checks membership and unlocks the bot."""
        if self._is_member(user_id):
            self.api.answer_callback(cb_id, "✅ Verified! Enjoy the bot.")
            self.api.edit_message_text(
                chat_id, message_id, "✅ <b>Verified!</b> Use the buttons below.",
                parse_mode="HTML")
            self.api.send_message(chat_id, WELCOME, parse_mode="HTML",
                                  reply_markup=kb.main_menu())
        else:
            self.api.answer_callback(cb_id, "❌ Not a member yet — join the channel first",
                                     alert=True)

    # ------------------------------------------------------------------ #
    def _check_inbox(self, chat_id, user_id, cb_id, mail_id):
        mail = db.get_mail(mail_id)
        if not mail or mail["user_id"] != user_id:
            self.api.answer_callback(cb_id, "Not found", alert=True)
            return
        self.api.answer_callback(cb_id, "Checking…")
        provider = mail.get("provider") or "emailnator"
        address = mail["address"]
        msgs = {}
        if provider != "emailnator":
            # ♾️ arsenal providers (SMailPro / mail.tm / gw / lol / guerrilla) —
            # read via the unified provider router, bodies come embedded
            try:
                from services.unlimited_mail import get_mailer
                for m in get_mailer().read_messages(address, provider):
                    if m.get("messageID"):
                        msgs.setdefault(m["messageID"], m)
            except Exception as e:
                log.warning("arsenal check failed for %s (%s): %s",
                            address, provider, e)
                self.api.send_message(
                    chat_id,
                    f"⚠️ Inbox check failed ({provider}): {esc(str(e))[:120]}")
                return
        else:
            forms = {address}
            if mail.get("plain_form") and mail["plain_form"] != address:
                forms.add(mail["plain_form"])
            try:
                for form in forms:
                    for m in self.emailnator.messages(form):
                        if m.get("messageID"):
                            msgs.setdefault(m["messageID"], m)
            except EmailnatorError as e:
                self.api.send_message(chat_id, f"⚠️ Inbox check failed: {esc(e)}")
                return
        msgs = list(msgs.values())
        # show ONLY new mail: skip pre-existing pool mail (baseline) and
        # anything already delivered — so Check Inbox never repeats old mails
        msgs = [m for m in msgs
                if not db.is_baseline(mail_id, m.get("messageID", ""))
                and not db.is_delivered(mail_id, m.get("messageID", ""))]
        if not msgs:
            self.api.send_message(
                chat_id,
                f"📭 <b>No new mail</b>\n{esc(address)}\n\n"
                f"Nothing new since your last check.",
                parse_mode="HTML")
            return
        self.api.send_message(
            chat_id, f"📬 <b>{esc(address)}</b> — {len(msgs)} new message(s):",
            parse_mode="HTML")
        for m in msgs[:8]:
            codes = []
            if provider != "emailnator":
                # arsenal message: body already embedded in the payload
                body_html = m.get("body") or ""
            else:
                body_html = ""
                try:
                    body_html = self.emailnator.message_body(
                        address, m["messageID"])
                except Exception:
                    pass
            plain = strip_tags(body_html) if body_html else ""
            codes = extract_codes(plain) if plain else []
            headers = parse_headers(body_html) if body_html else {}
            sender = headers.get("from") or m.get("from", "?")
            subject = headers.get("subject") or m.get("subject", "(no subject)")
            recv_time = headers.get("time") or m.get("time", "")
            text = render_mail(address, sender, subject, recv_time, plain, codes)
            self.api.send_message(chat_id, text, parse_mode="HTML")
            # mark as seen so it won't be repeated on the next Check Inbox
            db.mark_delivered(mail_id, m.get("messageID", ""))
