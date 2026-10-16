"""HTTP-сервер для Render Web Service + самопинг, чтобы бесплатный сервис не засыпал."""
import asyncio
import os

import aiohttp
from aiohttp import web


async def _health(request):
    return web.json_response({"status": "ok", "service": "UNU Monitor Telegram"})


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"[WEB] HTTP-сервер слушает порт {port}")
    return runner


async def keep_alive_task():
    """Раз в 10 минут обращаемся к собственному публичному адресу (Free-инстанс засыпает после 15 мин без запросов)."""
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not url:
        print("[WEB] RENDER_EXTERNAL_URL не задан (запуск не на Render) — самопинг отключён")
        return
    await asyncio.sleep(60)
    while True:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.get(url + "/health") as r:
                    print(f"[WEB] self-ping {r.status}")
        except Exception as e:
            print(f"[WEB] self-ping error: {e}")
        await asyncio.sleep(600)
