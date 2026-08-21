"""Update handlers — fully button-driven Bot API flows."""
import logging
import threading
import time

from bot import keyboards as kb
from config import GENERATE_COOLDOWN, REQUIRED_CHANNEL, REQUIRED_CHANNEL_URL
from services.emailnator import EmailnatorClient, EmailnatorError
from services.extractor import esc, extract_codes, parse_headers, render_mail, strip_tags
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

        # The pool occasionally hands out an address we already store
        # (unique index) — regenerate up to 10 times with backoff.
        mail_id = None
        address = None
        for attempt in range(10):
            try:
                address = self.emailnator.generate()
                mail_id = db.add_mail(user_id, address)
                break
            except EmailnatorError as e:
                log.warning("gen attempt %d: emailnator error: %s", attempt + 1, e)
            except Exception as e:
                log.warning("gen attempt %d: db/other error: %s", attempt + 1, e)
            if attempt < 9:
                # Exponential backoff with jitter for Cloudflare rate limits
                delay = min(2 ** attempt + 0.5, 8)  # 1.5s, 2.5s, 4.5s, 8.5s...
                time.sleep(delay)
        if not mail_id:
            # Non-fatal: try to inform user, but don't crash
            try:
                self.api.send_message(chat_id, "⚠️ Generation failed.\nTry again in a moment.")
            except Exception:
                pass
            return
        plain = address.split("@")[0].replace(".", "") + "@" + address.split("@")[1]

        # Baseline snapshot: this pooled inbox may contain OLD mail from a
        # previous pool tenant. Mark everything that already exists as
        # baseline so ONLY new mail (arriving after this moment) is delivered.
        try:
            old_ids = set()
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
        forms = {mail["address"]}
        if mail.get("plain_form") and mail["plain_form"] != mail["address"]:
            forms.add(mail["plain_form"])
        msgs = {}
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
                f"📭 <b>No new mail</b>\n{esc(mail['address'])}\n\n"
                f"Nothing new since your last check.",
                parse_mode="HTML")
            return
        self.api.send_message(
            chat_id, f"📬 <b>{esc(mail['address'])}</b> — {len(msgs)} new message(s):",
            parse_mode="HTML")
        for m in msgs[:8]:
            codes = []
            body_html = ""
            try:
                body_html = self.emailnator.message_body(
                    mail["address"], m["messageID"])
                codes = extract_codes(strip_tags(body_html))
            except Exception:
                pass
            headers = parse_headers(body_html) if body_html else {}
            sender = headers.get("from") or m.get("from", "?")
            subject = headers.get("subject") or m.get("subject", "(no subject)")
            recv_time = headers.get("time") or m.get("time", "")
            text = render_mail(mail["address"], sender, subject, recv_time,
                               strip_tags(body_html), codes)
            self.api.send_message(chat_id, text, parse_mode="HTML")
            # mark as seen so it won't be repeated on the next Check Inbox
            db.mark_delivered(mail_id, m.get("messageID", ""))
