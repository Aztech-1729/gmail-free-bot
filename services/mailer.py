"""Background mail poller — parallel & instant.

Polls ALL registered addresses concurrently (worker pool + per-thread
Emailnator sessions), both address forms each. New mail → instantly sends
the user: summary with OTP codes, .html file, raw .eml file.
"""
import concurrent.futures
import logging
import os
import tempfile
import threading
import time

from bot.keyboards import mail_actions
from services.emailnator import EmailnatorClient, EmailnatorError
from services.extractor import build_eml, esc, extract_codes, safe_id, strip_tags

log = logging.getLogger("mailer")

MAIL_WORKERS = 8   # parallel mailboxes per round
FORM_WORKERS = 2   # dotted + plain in parallel


class Mailer:
    def __init__(self, api, database, interval: int = 5):
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
                mails = self.db.all_mails()
                if mails:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=MAIL_WORKERS) as ex:
                        list(ex.map(self._poll_mail, mails))
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

        def fetch_form(form):
            try:
                for m in self.emailnator.messages(form):
                    if m.get("messageID"):
                        messages.setdefault(m["messageID"], m)
            except EmailnatorError as e:
                log.debug("list failed for %s: %s", form, e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=FORM_WORKERS) as ex:
            list(ex.map(fetch_form, forms))

        for message_id, msg in messages.items():
            if self.db.is_delivered(mail_id, message_id):
                continue
            if self.db.is_baseline(mail_id, message_id):
                # old pool mail that existed before the user generated
                # this address — never forward it.
                continue
            try:
                self._deliver(mail, msg)
                self.db.mark_delivered(mail_id, message_id)
            except Exception as e:
                log.warning("deliver failed for %s: %s", message_id, e)

    # ------------------------------------------------------------------ #
    def _deliver(self, mail: dict, msg: dict):
        user_id = mail["user_id"]
        dotted = mail["address"]
        # display the EXACT minted (dotted) address — Emailnator indexes and
        # lists messages only under this form, so users must send to it
        show_addr = dotted

        message_id = msg["messageID"]
        sender = msg.get("from", "Unknown")
        subject = msg.get("subject", "(no subject)")
        recv_time = msg.get("time", "")

        try:
            # Emailnator indexes bodies under the exact minted (dotted) form;
            # message_body() tries dotted first, plain as fallback.
            body_html = self.emailnator.message_body(dotted, message_id)
        except EmailnatorError as e:
            log.info("body fetch failed for %s: %s", message_id, e)
            body_html = ""
        if not body_html or len(body_html) < 10:
            body_html = (f"<html><body><p>From: {sender}</p>"
                         f"<p>Subject: {subject}</p></body></html>")

        plain = strip_tags(body_html)
        codes = extract_codes(plain)

        # 1) Send the files FIRST — the summary is only sent after BOTH
        #    attachments succeeded, so a failure retries next poll without
        #    spamming summary-only messages.
        sid = safe_id(message_id)
        with tempfile.TemporaryDirectory() as td:
            html_path = os.path.join(td, f"{sid}.html")
            eml_path = os.path.join(td, f"{sid}_raw.eml")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(body_html)
            with open(eml_path, "w", encoding="utf-8") as f:
                f.write(build_eml(sender, dotted, subject, body_html))

            res_html = self.api.send_document(user_id, html_path,
                                              caption=f"🌐 <b>HTML file</b> — {esc(subject[:60])}",
                                              parse_mode="HTML")
            res_eml = self.api.send_document(user_id, eml_path,
                                             caption=f"📄 <b>Raw .eml</b> — {esc(subject[:60])}",
                                             parse_mode="HTML")
            if not res_html.get("ok") or not res_eml.get("ok"):
                raise RuntimeError(
                    f"document send failed: html={res_html} eml={res_eml}")

        # 2) Summary message (always arrives together with the files)
        code_line = ""
        if codes:
            code_line = "\n\n🔑 <b>OTP CODES:</b> <code>" + "  ".join(codes[:4]) + "</code>"

        self.api.send_message(
            user_id,
            f"📬 <b>New mail</b>\n"
            f"📧 To: <code>{esc(show_addr)}</code>\n"
            f"👤 From: {esc(sender)}\n"
            f"✉️ Subject: {esc(subject)}\n"
            f"🕐 {esc(recv_time)}{code_line}",
            parse_mode="HTML", reply_markup=mail_actions(mail["id"]))
