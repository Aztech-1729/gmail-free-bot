#!/usr/bin/env python3
"""Async GMAILS FREE Bot — webhook + async poller."""
import asyncio
import logging
import os
import sys

import aiohttp

from config import (BOT_TOKEN, POLL_INTERVAL, MONGO_URI, MONGO_DB,
                    is_configured, missing_credentials)
from services.emailnator_async import AsyncEmailnatorClient
from services.mailer_async import AsyncMailer
from services.async_db import AsyncMongoStore
from app_async import AsyncBotAPI, AsyncBotHandler, create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s")
log = logging.getLogger("main")


async def main():
    if not is_configured():
        print("\n❌ Telegram credentials missing. Fill .env with:")
        for m in missing_credentials():
            print(f"   • {m}")
        print("\nSetup guide in README.md\n")
        sys.exit(1)

    # Initialize async services
    log.info("Initializing async services...")
    aiohttp_session = aiohttp.ClientSession()

    # Database
    if MONGO_URI:
        try:
            db = AsyncMongoStore(MONGO_URI, MONGO_DB)
            await db.init_indexes()
            log.info("Storage backend: MongoDB Atlas (%s)", MONGO_DB)
        except Exception as e:
            log.warning("MongoDB unavailable (%s) — cannot run async mode without MongoDB", e)
            sys.exit(1)
    else:
        log.warning("MONGO_URI not set — cannot run async mode")
        sys.exit(1)

    # Emailnator client with connection pooling + caching + circuit breaker
    emailnator = AsyncEmailnatorClient(aiohttp_session, cache_ttl=2.0)

    # Bot API
    bot_api = AsyncBotAPI(BOT_TOKEN)
    me = await bot_api._call("getMe", {})
    if not me.get("ok"):
        print("❌ Invalid bot token — check BOT_TOKEN in .env")
        await aiohttp_session.close()
        sys.exit(1)
    bot_name = me["result"].get("username") or me["result"].get("first_name")
    log.info("Bot online: @%s", bot_name)

    # Mailer
    mailer = AsyncMailer(
        api=None,  # Will be set in handler
        database=db,
        emailnator=emailnator,
        interval=POLL_INTERVAL,
        max_concurrent_mailboxes=16,
        max_concurrent_bodies=32,
    )

    # Bot handler
    bot_handler = AsyncBotHandler(bot_api, emailnator, db, mailer)
    mailer.api = bot_api  # Set api reference for mailer

    # Create web app
    app = create_app()
    app["bot_handler"] = bot_handler

    # Start mailer
    await mailer.start()
    log.info("Async mail poller started (every %ss) | storage: %s", POLL_INTERVAL, db.backend)

    # Start web server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    log.info("Webhook server listening on 0.0.0.0:8080")

    # Set webhook
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        await bot_api._call("setWebhook", {"url": f"{webhook_url}/webhook"})
        log.info("Webhook set to %s/webhook", webhook_url)
    else:
        log.warning("WEBHOOK_URL not set — webhook not configured")

    # Run until interrupted
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    finally:
        log.info("Shutting down...")
        await mailer.stop()
        await runner.cleanup()
        await bot_api.close()
        await aiohttp_session.close()


if __name__ == "__main__":
    asyncio.run(main())