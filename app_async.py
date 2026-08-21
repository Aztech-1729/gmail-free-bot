"""Async GMAILS FREE Bot — webhook + async poller."""
import asyncio
import logging
import os
import sys

from aiohttp import web

from config import (BOT_TOKEN, POLL_INTERVAL, REQUIRED_CHANNEL,
                    REQUIRED_CHANNEL_URL, is_configured, missing_credentials)
from services.emailnator_async import AsyncEmailnatorClient
from services.extractor import esc, render_mail
from bot.keyboards import main_menu, join_channel_menu

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s")
log = logging.getLogger("main")


# ---- Async bot handler (simplified for webhook) ----
class AsyncBotHandler:
    def __init__(self, api, emailnator, db, mailer):
        self.api = api
        self.emailnator = emailnator
        self.db = db
        self.mailer = mailer

    async def handle_webhook(self, request):
        """Handle Telegram webhook updates."""
        try:
            update = await request.json()
            if "message" in update:
                await self._handle_message(update["message"])
            elif "callback_query" in update:
                await self._handle_callback(update["callback_query"])
        except Exception as e:
            log.exception("webhook handling failed: %s", e)
        return web.Response(text="OK")

    async def _handle_message(self, msg: dict):
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        username = msg["from"].get("username", "")
        text = msg.get("text", "") or ""

        await self.db.add_user(user_id, username)

        # Channel check
        if REQUIRED_CHANNEL and not await self._is_member(user_id):
            await self.api.send_message(chat_id, JOIN_MSG, parse_mode="HTML",
                                        reply_markup=join_channel_menu(REQUIRED_CHANNEL_URL))
            return

        if text == "/start":
            await self.api.send_message(chat_id, WELCOME, parse_mode="HTML", reply_markup=main_menu())
        elif text == "➕ Generate Gmail":
            await self._generate(chat_id, user_id)
        elif text == "📬 My Mails":
            await self._show_mail_list(chat_id, user_id, page=0)
        elif text == "🗑 Delete Mail":
            await self._show_mail_list(chat_id, user_id, page=0)
        elif text == "📊 Stats":
            await self._stats(chat_id, user_id)
        elif text == "❓ Help":
            await self.api.send_message(chat_id, HELP, parse_mode="HTML", reply_markup=main_menu())
        else:
            await self.api.send_message(chat_id, "Use the buttons below ⬇️", reply_markup=main_menu())

    async def _is_member(self, user_id: int) -> bool:
        if not REQUIRED_CHANNEL:
            return True
        try:
            r = await self.api.get_chat_member(REQUIRED_CHANNEL, user_id)
            if r.get("ok"):
                return r["result"].get("status", "") in ("member", "administrator", "creator")
        except Exception:
            pass
        return False

    async def _generate(self, chat_id: int, user_id: int, skip_cooldown: bool = False):
        from time import time
        import time as time_mod

        # Simple cooldown (per-user)
        now = time()
        if not skip_cooldown and now - getattr(self, "_cooldown", {}).get(user_id, 0) < 1.5:
            await self.api.send_message(chat_id, "⏳ One moment…", reply_markup=main_menu())
            return
        if not hasattr(self, "_cooldown"):
            self._cooldown = {}
        self._cooldown[user_id] = now

        status_id = None
        try:
            status = await self.api.send_message(chat_id, "⏳ Generating your gmail…")
            status_id = status["result"]["message_id"] if status.get("ok") else None
        except Exception:
            pass

        mail_id = None
        address = None
        for attempt in range(10):
            try:
                address = await self.emailnator.generate()
                mail_id = await self.db.add_mail(user_id, address)
                break
            except Exception as e:
                if attempt < 9:
                    await asyncio.sleep(0.3 * (attempt + 1))
        if not mail_id:
            try:
                await self.api.send_message(chat_id, "⚠️ Generation failed.\nTry again in a moment.")
            except Exception:
                pass
            return

        plain = address.split("@")[0].replace(".", "") + "@" + address.split("@")[1]

        # Baseline snapshot
        try:
            old_ids = set()
            for form in {address, plain}:
                try:
                    for m in await self.emailnator.messages(form):
                        if m.get("messageID") and m.get("messageID") != "ADSVPN":
                            old_ids.add(m["messageID"])
                except Exception:
                    pass
            if old_ids:
                await self.db.mark_baseline(mail_id, list(old_ids))
        except Exception as e:
            log.warning("baseline snapshot failed for %s: %s", mail_id, e)

        text = (f"✅ <b>Your gmail is ready!</b>\n\n"
                f"📧 <b>{address}</b>\n\n"
                f"Use it anywhere an OTP is needed — I'll forward every mail "
                f"here instantly with HTML + raw files.")
        kb_markup = self.api.mail_actions(mail_id)
        try:
            if status_id:
                await self.api.edit_message_text(chat_id, status_id, text, parse_mode="HTML", reply_markup=kb_markup)
            else:
                await self.api.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb_markup)
        except Exception as e:
            log.warning("failed to send success msg: %s", e)

    async def _show_mail_list(self, chat_id: int, user_id: int, page: int = 0):
        mails = await self.db.list_mails(user_id)
        if not mails:
            await self.api.send_message(
                chat_id,
                "You don't have any mails yet.\nPress ➕ Generate Gmail to create one!",
                reply_markup=main_menu())
            return
        body = "\n".join(f"{i+1}. <code>{esc(m['address'])}</code>" for i, m in enumerate(mails))
        await self.api.send_message(
            chat_id,
            f"📬 <b>Your mails</b>\n\n{body}\n\n[📥 Check] [🗑 Delete] per row",
            parse_mode="HTML", reply_markup=kb.mail_list_keyboard(mails, page))

    async def _stats(self, chat_id: int, user_id: int):
        n_mails = await self.db.count_mails(user_id)
        n_delivered = await self.db.delivered_count(user_id)
        await self.api.send_message(
            chat_id,
            f"📊 <b>Your stats</b>\n\n"
            f"📧 Mailboxes: <b>{n_mails}</b>\n"
            f"📬 Messages delivered: <b>{n_delivered}</b>",
            parse_mode="HTML", reply_markup=main_menu())

    async def _handle_callback(self, cb: dict):
        cb_id = cb["id"]
        data = cb.get("data", "")
        user_id = cb["from"]["id"]
        chat_id = cb["message"]["chat"]["id"]
        message_id = cb["message"]["message_id"]

        if data == "verify":
            if await self._is_member(user_id):
                await self.api.answer_callback(cb_id, "✅ Verified! Enjoy the bot.")
                await self.api.edit_message_text(chat_id, message_id, "✅ Verified!", parse_mode="HTML")
                await self.api.send_message(chat_id, WELCOME, parse_mode="HTML", reply_markup=main_menu())
            else:
                await self.api.answer_callback(cb_id, "❌ Not a member yet", alert=True)
            return

        if REQUIRED_CHANNEL and not await self._is_member(user_id):
            await self.api.answer_callback(cb_id, "🔒 Join channel first", alert=True)
            await self.api.send_message(chat_id, JOIN_MSG, parse_mode="HTML",
                                        reply_markup=join_channel_menu(REQUIRED_CHANNEL_URL))
            return

        if data.startswith("check:"):
            mail_id = data.split(":", 1)[1]
            await self._check_inbox(chat_id, user_id, cb_id, mail_id)
        elif data.startswith("del:"):
            mail_id = data.split(":", 1)[1]
            mail = await self.db.get_mail(mail_id)
            if not mail or mail["user_id"] != user_id:
                await self.api.answer_callback(cb_id, "Not found", alert=True)
                return
            await self.api.edit_message_text(
                chat_id, message_id,
                f"🗑 Delete <code>{esc(mail['address'])}</code>?",
                parse_mode="HTML", reply_markup=kb.confirm_delete(mail_id))
            await self.api.answer_callback(cb_id)
        elif data.startswith("delok:"):
            mail_id = data.split(":", 1)[1]
            mail = await self.db.get_mail(mail_id)
            if not mail or mail["user_id"] != user_id:
                await self.api.answer_callback(cb_id, "Not found", alert=True)
                return
            await self.db.delete_mail(mail_id)
            await self.api.edit_message_text(
                chat_id, message_id, f"✅ Deleted <code>{esc(mail['address'])}</code>.",
                parse_mode="HTML")
            await self.api.answer_callback(cb_id, "Deleted")
        elif data == "genmore":
            await self.api.answer_callback(cb_id, "Generating…")
            await self._generate(chat_id, user_id, skip_cooldown=True)
        elif data == "delno":
            await self.api.edit_message_text(chat_id, message_id, "👍 Kept.")
            await self.api.answer_callback(cb_id)
        elif data.startswith("page:"):
            await self.api.answer_callback(cb_id)
            page = int(data.split(":", 1)[1])
            await self._show_mail_list(chat_id, user_id, page)
        else:
            await self.api.answer_callback(cb_id)

    async def _check_inbox(self, chat_id: int, user_id: int, cb_id: str, mail_id: str):
        mail = await self.db.get_mail(mail_id)
        if not mail or mail["user_id"] != user_id:
            await self.api.answer_callback(cb_id, "Not found", alert=True)
            return
        await self.api.answer_callback(cb_id, "Checking…")
        forms = {mail["address"]}
        if mail.get("plain_form") and mail["plain_form"] != mail["address"]:
            forms.add(mail["plain_form"])
        msgs = {}
        try:
            for form in forms:
                for m in await self.emailnator.messages(form):
                    if m.get("messageID"):
                        msgs.setdefault(m["messageID"], m)
        except Exception as e:
            await self.api.send_message(chat_id, f"⚠️ Inbox check failed: {esc(e)}")
            return

        msgs = list(msgs.values())
        msgs = [m for m in msgs
                if not await self.db.is_baseline(mail_id, m.get("messageID", ""))
                and not await self.db.is_delivered(mail_id, m.get("messageID", ""))]
        if not msgs:
            await self.api.send_message(
                chat_id,
                f"📭 <b>No new mail</b>\n{esc(mail['address'])}\n\nNothing new since last check.",
                parse_mode="HTML")
            return

        await self.api.send_message(
            chat_id, f"📬 <b>{esc(mail['address'])}</b> — {len(msgs)} new message(s):",
            parse_mode="HTML")
        for m in msgs[:8]:
            body_html = ""
            try:
                body_html = await self.emailnator.message_body(mail["address"], m["messageID"])
            except Exception:
                pass
            headers = parse_headers(body_html) if body_html else {}
            sender = headers.get("from") or m.get("from", "?")
            subject = headers.get("subject") or m.get("subject", "(no subject)")
            recv_time = headers.get("time") or m.get("time", "")
            from services.extractor import render_mail, strip_tags, extract_codes
            text = render_mail(mail["address"], sender, subject, recv_time,
                               strip_tags(body_html), extract_codes(strip_tags(body_html)))
            await self.api.send_message(chat_id, text, parse_mode="HTML")
            await self.db.mark_delivered(mail_id, m.get("messageID", ""))


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


# ---- Bot API wrapper for async ----
class AsyncBotAPI:
    def __init__(self, token: str):
        self.token = token
        self._session = None
        self.base = f"https://api.telegram.org/bot{token}"

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _call(self, method: str, params: dict = None, data=None, files=None):
        session = await self._get_session()
        url = f"{self.base}/{method}"
        try:
            if files:
                form = aiohttp.FormData()
                for k, v in params.items():
                    form.add_field(k, str(v))
                for k, (fname, fobj) in files.items():
                    form.add_field(k, fobj, filename=fname)
                async with session.post(url, data=form) as r:
                    return await r.json()
            else:
                async with session.post(url, json=params) as r:
                    return await r.json()
        except Exception as e:
            log.warning("Bot API %s failed: %s", method, e)
            return {"ok": False, "description": str(e)}

    async def send_message(self, chat_id: int, text: str, parse_mode: str = None, reply_markup=None):
        p = {"chat_id": chat_id, "text": text}
        if parse_mode:
            p["parse_mode"] = parse_mode
        if reply_markup:
            p["reply_markup"] = reply_markup
        return await self._call("sendMessage", p)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, parse_mode=None, reply_markup=None):
        p = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            p["parse_mode"] = parse_mode
        if reply_markup:
            p["reply_markup"] = reply_markup
        return await self._call("editMessageText", p)

    async def send_document(self, chat_id: int, file_obj, caption=None, parse_mode=None, reply_to_message_id=None, filename=None):
        p = {"chat_id": chat_id}
        if caption:
            p["caption"] = caption
        if parse_mode:
            p["parse_mode"] = parse_mode
        if reply_to_message_id:
            p["reply_to_message_id"] = reply_to_message_id

        session = await self._get_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption)
        if parse_mode:
            form.add_field("parse_mode", parse_mode)
        if reply_to_message_id:
            form.add_field("reply_to_message_id", str(reply_to_message_id))
        if isinstance(file_obj, bytes):
            form.add_field("document", file_obj, filename=filename or "file.html")
        else:
            file_obj.seek(0)
            form.add_field("document", file_obj.read(), filename=filename or "file.html")

        url = f"{self.base}/sendDocument"
        async with session.post(url, data=form) as r:
            return await r.json()

    async def answer_callback(self, callback_query_id: str, text=None, alert=False):
        p = {"callback_query_id": callback_query_id}
        if text:
            p["text"] = text
        p["show_alert"] = alert
        return await self._call("answerCallbackQuery", p)

    async def get_chat_member(self, chat_id: str, user_id: int):
        return await self._call("getChatMember", {"chat_id": chat_id, "user_id": user_id})

    def mail_actions(self, mail_id):
        from bot.keyboards import mail_actions
        return mail_actions(mail_id)


# ---- App entrypoint ----
async def on_startup(app):
    app["startup_complete"] = asyncio.Event()
    log.info("Starting async services...")
    # Services are already started in main()


async def on_shutdown(app):
    log.info("Shutting down...")
    # Graceful shutdown handled by main


def create_app():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Webhook route
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/health", health_check)

    return app


async def handle_webhook(request):
    # Injected handler from main
    return await request.app["bot_handler"].handle_webhook(request)


async def health_check(request):
    return web.json_response({"status": "ok"})


if __name__ == "__main__":
    print("Run via main.py with async services")