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
