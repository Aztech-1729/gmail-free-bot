"""Background mail poller — parallel & instant.

Polls ALL registered addresses concurrently (worker pool + per-thread
Emailnator sessions), both address forms each. New mail → instantly sends
the user: summary with OTP codes, .html file (no raw .eml).

Performance improvements:
- Cached message lists (2s TTL) via EmailnatorClient
- Circuit breaker on 5xx
- Parallel body fetches with semaphore (32 concurrent)
- Increased worker pools (16 mailboxes, 4 forms)
"""
import concurrent.futures
import logging
import threading
import time
from io import BytesIO

from services.emailnator import EmailnatorClient, EmailnatorError
from services.extractor import (esc, extract_codes, parse_headers, render_mail,
                                safe_id, strip_tags)

log = logging.getLogger("mailer")

MAIL_WORKERS = 16    # parallel mailboxes per round (was 8)
FORM_WORKERS = 4     # dotted + plain in parallel (was 2)

# Body fetch semaphore is now in EmailnatorClient (32 concurrent)


class Mailer:
    def __init__(self, api, database, interval: int = 5):
        self.api = api
        self.db = database
        self.interval = max(3, interval)
        # Cache TTL 2s, 32 concurrent body fetches
        self.emailnator = EmailnatorClient(cache_ttl=2.0, max_concurrent_bodies=32)
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
        # skip if deleted while this round was in flight (prevents re-sending
        # every old message as a burst right after the user deletes the mail)
        if not self.db.get_mail(mail_id):
            return
        provider = mail.get("provider") or "emailnator"
        forms = {mail["address"]}
        if mail.get("plain_form") and mail["plain_form"] != mail["address"]:
            forms.add(mail["plain_form"])

        messages = {}

        if provider != "emailnator":
            # ♾️ arsenal providers — unified reader (SMailPro / mail.tm / gw /
            # tempmail.lol / guerrilla). Bodies come embedded in the message.
            try:
                from services.unlimited_mail import get_mailer
                for m in get_mailer().read_messages(mail["address"], provider):
                    if m.get("messageID"):
                        messages.setdefault(m["messageID"], m)
            except Exception as e:
                log.debug("arsenal list failed for %s (%s): %s",
                          mail["address"], provider, e)
        else:
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

        provider = mail.get("provider") or "emailnator"
        if provider != "emailnator":
            # arsenal providers embed the body in the message payload
            body_html = msg.get("body") or ""
        else:
            try:
                # Emailnator indexes bodies under the exact minted (dotted) form;
                # message_body() tries dotted first, plain as fallback.
                body_html = self.emailnator.message_body(dotted, message_id)
            except EmailnatorError as e:
                log.info("body fetch failed for %s: %s", message_id, e)
                body_html = ""

        # Prefer the precise headers from the raw body when available
        headers = parse_headers(body_html) if body_html else {}
        sender = headers.get("from") or sender
        subject = headers.get("subject") or subject
        recv_time = headers.get("time") or recv_time

        plain = strip_tags(body_html)
        codes = extract_codes(plain)

        # 1) The FULL mail as a chat message: headers block + subject + body.
        text = render_mail(show_addr, sender, subject, recv_time, plain, codes)
        res_text = self.api.send_message(user_id, text, parse_mode="HTML")
        if not res_text.get("ok"):
            raise RuntimeError(f"text send failed: {res_text}")
        text_message_id = res_text["result"]["message_id"]

        # 2) Attach the .html as a REPLY to that message (drop the raw .eml).
        sid = safe_id(message_id)
        html_bytes = (body_html or f"<html><body><p>From: {esc(sender)}</p>"
                        f"<p>Subject: {esc(subject)}</p></body></html>").encode("utf-8")
        res_html = self.api.send_document(
            user_id, BytesIO(html_bytes),
            filename=f"{sid}.html",
            caption=f"🌐 <b>HTML file</b> — {esc(subject[:60])}",
            parse_mode="HTML", reply_to_message_id=text_message_id)
        if not res_html.get("ok"):
            # text already delivered — log, don't raise, so the mail is
            # marked delivered and never re-sent as a duplicate
            log.warning("html attach failed for %s: %s", message_id, res_html)