"""Keyboard builders — plain Bot API dicts with 9.4 colored styles.

Styles: "primary" (blue) · "danger" (red) · "success" (green).
"""


def _btn(text, style=None):
    b = {"text": text}
    if style:
        b["style"] = style
    return b


def main_menu():
    """Colored reply keyboard (persistent at the bottom)."""
    return {
        "keyboard": [
            [_btn("➕ Generate Gmail", "primary")],
            [_btn("📬 My Mails", "success"), _btn("🗑 Delete Mail", "danger")],
            [_btn("♾️ Mass Gmails", "success"), _btn("🗑 Delete All", "danger")],
            [_btn("🔐 Create X Acc", "primary")],
            [_btn("📊 Stats"), _btn("❓ Help")],
        ],
        "resize_keyboard": True,
    }


def mail_actions(mail_id):
    return {"inline_keyboard": [
        [{"text": "📥 Check Inbox", "callback_data": f"check:{mail_id}", "style": "primary"},
         {"text": "🗑 Delete", "callback_data": f"del:{mail_id}", "style": "danger"}],
        [{"text": "➕ Generate another", "callback_data": "genmore", "style": "success"}],
    ]}


def mail_list_keyboard(mails, page=0, page_size=6):
    total = len(mails)
    start = page * page_size
    chunk = mails[start:start + page_size]
    rows = []
    for m in chunk:
        label = m["address"]
        rows.append([
            {"text": f"📥 {label}", "callback_data": f"check:{m['id']}", "style": "primary"},
            {"text": "🗑", "callback_data": f"del:{m['id']}", "style": "danger"},
        ])
    nav = []
    if start > 0:
        nav.append({"text": "⬅️ Prev", "callback_data": f"page:{page - 1}"})
    if start + page_size < total:
        nav.append({"text": "Next ➡️", "callback_data": f"page:{page + 1}"})
    if nav:
        rows.append(nav)
    return {"inline_keyboard": rows}


def join_channel_menu(channel_url):
    """Inline buttons shown when the user must join the channel first."""
    return {"inline_keyboard": [
        [{"text": "🔗 Join Channel", "url": channel_url, "style": "primary"}],
        [{"text": "✅ Verify", "callback_data": "verify", "style": "success"}],
    ]}


def confirm_delete_all(count):
    """Inline confirm for wiping every mail of a user."""
    return {"inline_keyboard": [
        [{"text": f"⚠️ Yes, delete ALL {count}", "callback_data": "delall:yes",
          "style": "danger"}],
        [{"text": "❌ Cancel", "callback_data": "delall:no", "style": "success"}],
    ]}


def confirm_delete(mail_id):
    return {"inline_keyboard": [
        [{"text": "✅ Yes, delete it", "callback_data": f"delok:{mail_id}", "style": "danger"}],
        [{"text": "❌ Cancel", "callback_data": "delno", "style": "success"}],
    ]}
