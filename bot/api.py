"""Minimal Bot API client (long polling) — only a bot token is needed."""
import json
import logging

import requests

log = logging.getLogger("botapi")
API = "https://api.telegram.org/bot{token}/{method}"


class BotAPI:
    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ---------------------------------------------------------------- #
    def _call(self, method: str, params: dict, files=None, timeout=60):
        url = API.format(token=self.token, method=method)
        if files:
            # multipart — drop the JSON content-type; requests builds the
            # correct multipart header when files= is present.
            r = self.session.post(url, data=params, files=files, timeout=timeout,
                                  headers={"Content-Type": None})
        else:
            r = self.session.post(url, data=json.dumps(params), timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            log.warning("Bot API %s returned non-JSON: %s %s", method, r.status_code, r.text[:200])
            return {"ok": False, "description": f"non-JSON response {r.status_code}"}
        if not data.get("ok"):
            log.warning("Bot API %s failed: %s", method, data)
        return data

    def get_me(self):
        return self._call("getMe", {})

    def get_updates(self, offset: int, timeout: int = 30):
        # read timeout must exceed the long-poll hold (timeout) with margin —
        # flaky networks otherwise trigger spurious read timeouts.
        return self._call("getUpdates", {"offset": offset, "timeout": timeout,
                                         "allowed_updates": ["message", "callback_query"]},
                          timeout=timeout + 40)

    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        p = {"chat_id": chat_id, "text": text}
        if parse_mode:
            p["parse_mode"] = parse_mode
        if reply_markup is not None:
            p["reply_markup"] = reply_markup
        return self._call("sendMessage", p)

    def edit_message_text(self, chat_id, message_id, text, parse_mode=None, reply_markup=None):
        p = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            p["parse_mode"] = parse_mode
        if reply_markup is not None:
            p["reply_markup"] = reply_markup
        return self._call("editMessageText", p)

    def send_document(self, chat_id, file_path, caption=None, parse_mode=None,
                      reply_to_message_id=None):
        """Send a file as a document. Uses an explicit (filename, bytes) tuple
        so Telegram always receives the right name/extension."""
        import os as _os
        p = {"chat_id": chat_id}
        if caption:
            p["caption"] = caption
        if parse_mode:
            p["parse_mode"] = parse_mode
        if reply_to_message_id:
            p["reply_to_message_id"] = reply_to_message_id
        filename = _os.path.basename(file_path)
        with open(file_path, "rb") as f:
            content = f.read()
        return self._call("sendDocument", p, files={"document": (filename, content)})

    def answer_callback(self, callback_query_id, text=None, alert=False):
        p = {"callback_query_id": callback_query_id}
        if text:
            p["text"] = text
        p["show_alert"] = alert
        return self._call("answerCallbackQuery", p)

    def get_chat_member(self, chat_id, user_id):
        return self._call("getChatMember", {"chat_id": chat_id, "user_id": user_id})
