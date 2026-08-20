"""OTP extraction + raw email packaging."""
import html as html_mod
import re
from email.utils import formatdate


def esc(s) -> str:
    """Escape text for Telegram parse_mode=HTML (safe for user-generated data)."""
    return html_mod.escape(str(s or ""), quote=False)


def strip_tags(html_text: str) -> str:
    """HTML → readable plain text."""
    text = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|h[1-6]|tr|li)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract_codes(text: str, max_candidates: int = 6) -> list:
    """Pull likely OTP codes (5-6 digits preferred, 4-digit fallback)."""
    codes6 = re.findall(r"\b\d{6}\b", text)
    codes5 = re.findall(r"\b\d{5}\b", text)
    codes4 = re.findall(r"\b\d{4}\b", text)

    out, seen = [], set()
    for c in codes6 + codes5 + codes4:
        if c not in seen:
            seen.add(c)
            out.append(c)
        if len(out) >= max_candidates:
            break
    return out


def build_eml(from_: str, to: str, subject: str, body_html: str) -> str:
    """Reconstruct a raw .eml file (headers + original HTML body)."""
    lines = [
        f"From: {from_}",
        f"To: {to}",
        f"Subject: {subject}",
        f"Date: {formatdate(localtime=True)}",
        "MIME-Version: 1.0",
        "Content-Type: text/html; charset=utf-8",
        "",
        body_html,
    ]
    return "\r\n".join(lines)


def safe_id(message_id: str) -> str:
    """Sanitize a messageID for use in file names."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", message_id or "msg")[:40]


SEP = "➖➖➖➖➖➖➖➖➖"

MAX_TEXT = 3200  # Telegram message limit is 4096 chars — leave room for headers


def parse_headers(html_text: str) -> dict:
    """Pull From / Subject / Time out of Emailnator's `subject-header` block."""
    if not html_text:
        return {}
    m = re.search(r'<div id="subject-header">(.*?)</div>', html_text, re.S | re.I)
    if not m:
        return {}
    block = m.group(1)
    out = {}
    for key, label in (("from", "From"), ("subject", "Subject"), ("time", "Time")):
        mm = re.search(
            r"<b>\s*" + label + r"\s*:\s*</b>(.*?)(?:<br\s*/?>|<div|</div>|<hr|$)",
            block, re.S | re.I)
        if mm:
            out[key] = strip_tags(mm.group(1)).strip()
    return out


def render_mail(address: str, sender: str, subject: str, recv_time: str,
                plain_body: str, codes: list) -> str:
    """Full mail text for the chat: headers block, subject, then the body."""
    code_line = ""
    if codes:
        code_line = ("\n🔑 <b>OTP:</b> <code>" + "  ".join(esc(c) for c in codes[:4])
                     + "</code>")
    body = (plain_body or "(no body)").strip()
    truncated = False
    if len(body) > MAX_TEXT:
        body = body[:MAX_TEXT]
        truncated = True
    if truncated:
        body += "\n\n… (truncated — full content in the attached HTML file)"
    return (
        SEP + "\n"
        + f"<b>From:</b> {esc(sender)}\n"
        + f"<b>To:</b> <code>{esc(address)}</code>\n"
        + f"<b>Date:</b> {esc(recv_time)}{code_line}\n"
        + SEP + "\n"
        + f"<b>Subject:</b> {esc(subject)}\n"
        + SEP + "\n"
        + esc(body)
    )
