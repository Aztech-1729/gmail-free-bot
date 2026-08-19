"""GMAILS FREE — Telegram bot entrypoint (pure Bot API, long polling).

Run:  python3 main.py
Self-test (no Telegram needed):  python3 main.py --selftest
"""
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s")
log = logging.getLogger("main")


def _selftest():
    """Verify the Emailnator core (mint → poll → extract → package)."""
    from services.emailnator import EmailnatorClient
    from services.extractor import build_eml, extract_codes, strip_tags

    log.info("SELFTEST: minting a gmail…")
    client = EmailnatorClient()
    address = client.generate()
    log.info("SELFTEST: minted %s", address)

    msgs = client.messages(address)
    log.info("SELFTEST: %d message(s) in inbox", len(msgs))

    for m in msgs:
        try:
            body = client.message_body(address, m["messageID"])
        except Exception as e:
            log.info("SELFTEST: skipped message (unfetchable): %s", e)
            continue
        codes = extract_codes(strip_tags(body))
        eml = build_eml(m["from"], address, m["subject"], body)
        log.info("SELFTEST ✅ html %d chars | codes=%s | eml %d bytes | from=%s",
                 len(body), codes, len(eml), m["from"])
        break
    log.info("SELFTEST complete.")


def main():
    from bot.api import BotAPI
    from bot.handlers import Handler
    from config import (BOT_TOKEN, POLL_INTERVAL, is_configured,
                        missing_credentials)
    from services.emailnator import EmailnatorClient
    from services.mailer import Mailer
    from storage.db import db

    if not is_configured():
        print("\n❌ Telegram credentials missing. Fill .env with:")
        for m in missing_credentials():
            print(f"   • {m}")
        print("\nSetup guide in README.md\n")
        sys.exit(1)

    api = BotAPI(BOT_TOKEN)
    me = api.get_me()
    if not me.get("ok"):
        print("❌ Invalid bot token — check BOT_TOKEN in .env")
        sys.exit(1)
    bot_name = me["result"].get("username") or me["result"].get("first_name")
    log.info("Bot online: @%s", bot_name)

    emailnator = EmailnatorClient()
    handler = Handler(api, emailnator)
    mailer = Mailer(api, db, POLL_INTERVAL)
    mailer.start()
    log.info("Mail poller started (every %ss) | storage: %s", POLL_INTERVAL, db.backend)

    offset = 0
    # Bootstrap: skip any updates that piled up while the bot was offline,
    # so we don't reply to stale messages after a restart.
    try:
        boot = api.get_updates(0, timeout=0)
        if boot.get("ok"):
            for u in boot["result"]:
                offset = max(offset, u["update_id"] + 1)
            if boot["result"]:
                log.info("Skipped %d stale update(s)", len(boot["result"]))
    except Exception:
        pass

    while True:
        try:
            updates = api.get_updates(offset, timeout=30)
            if updates.get("ok"):
                for u in updates["result"]:
                    offset = max(offset, u["update_id"] + 1)
                    handler.handle_update(u)
            elif updates.get("error_code") == 409:
                # Another instance (e.g. the VPS bot) owns long polling —
                # exit quietly instead of fighting over getUpdates.
                log.warning("409 conflict — another bot instance is polling. Exiting.")
                sys.exit(0)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.warning("polling error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
