"""Background mail poller (threading — works with pure Bot API).

Every POLL_INTERVAL seconds, checks every registered gmail for new messages.
New mail → instantly sends the user:
  1. summary message with extracted OTP codes,
  2. the email as an .html document,
  3. the raw .eml document.

Polls BOTH the dotted and plain forms.
"""
import logging
import os
import tempfile
import threading
import time

from bot.keyboards import mail_actions
from services.emailnator import EmailnatorClient, EmailnatorError
from services.extractor import build_eml, esc, extract_codes, safe_id, strip_tags

log = logging.getLogger("mailer")


class Mailer:
    def __init__(self, api, database, interval: int = 10):
        self.api = api
        self.db = database
        self.interval = max(3, interval)
        self.emailnator = EmailnatorClient()
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------------ #
    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mailer")
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------ #
    def _loop(self):
        while not self._stop.is_set():
            started = time.time()
            try:
                for mail in self.db.all_mails():
                    self._poll_mail(mail)
            except Exception as e:
                log.warning("poller round failed: %s", e)
            elapsed = time.time() - started
            self._stop.wait(max(1.0, self.interval - elapsed))

    # ------------------------------------------------------------------ #
    def _poll_mail(self, mail: dict):
        mail_id = mail["id"]
        forms = {mail["address"]}
        if mail.get("plain_form") and mail["plain_form"] != mail["address"]:
            forms.add(mail["plain_form"])

        messages = {}
        for form in forms:
            try:
                for m in self.emailnator.messages(form):
                    if m.get("messageID"):
                        messages.setdefault(m["messageID"], m)
            except EmailnatorError as e:
                log.debug("list failed for %s: %s", form, e)

        for message_id, msg in messages.items():
            if self.db.is_delivered(mail_id, message_id):
                continue
            try:
                self._deliver(mail, msg)
                self.db.mark_delivered(mail_id, message_id)
            except Exception as e:
                log.warning("deliver failed for %s: %s", message_id, e)

    # ------------------------------------------------------------------ #
    def _deliver(self, mail: dict, msg: dict):
        user_id = mail["user_id"]
        address = mail["address"]
        message_id = msg["messageID"]
        sender = msg.get("from", "Unknown")
        subject = msg.get("subject", "(no subject)")
        recv_time = msg.get("time", "")

        try:
            body_html = self.emailnator.message_body(address, message_id)
        except EmailnatorError as e:
            log.info("body fetch failed for %s: %s", message_id, e)
            body_html = ""
        if not body_html or len(body_html) < 10:
            body_html = (f"<html><body><p>From: {sender}</p>"
                         f"<p>Subject: {subject}</p></body></html>")

        plain = strip_tags(body_html)
        codes = extract_codes(plain)
        code_line = ""
        if codes:
            code_line = "\n\n🔑 <b>OTP CODES:</b> <code>" + "  ".join(codes[:4]) + "</code>"

        self.api.send_message(
            user_id,
            f"📬 <b>New mail</b>\n"
            f"📧 To: <code>{esc(address)}</code>\n"
            f"👤 From: {esc(sender)}\n"
            f"✉️ Subject: {esc(subject)}\n"
            f"🕐 {esc(recv_time)}{code_line}",
            parse_mode="HTML", reply_markup=mail_actions(mail["id"]))

        sid = safe_id(message_id)
        with tempfile.TemporaryDirectory() as td:
            html_path = os.path.join(td, f"{sid}.html")
            eml_path = os.path.join(td, f"{sid}_raw.eml")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(body_html)
            with open(eml_path, "w", encoding="utf-8") as f:
                f.write(build_eml(sender, address, subject, body_html))
            self.api.send_document(user_id, html_path,
                                   caption=f"🌐 <b>HTML file</b> — {esc(subject[:60])}",
                                   parse_mode="HTML")
            self.api.send_document(user_id, eml_path,
                                   caption=f"📄 <b>Raw .eml</b> — {esc(subject[:60])}",
                                   parse_mode="HTML")
