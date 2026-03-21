from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord


BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"]) if os.environ.get("DISCORD_CHANNEL_ID") else 0
RELAY_SECRET = os.environ.get("DISCORD_RELAY_SECRET", "").strip()
PORT = int(os.environ.get("PORT", "8011"))

READY = threading.Event()
CLIENT_LOOP: asyncio.AbstractEventLoop | None = None
CLIENT: discord.Client | None = None


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


async def send_channel_message(content: str) -> None:
    if CLIENT is None:
        raise RuntimeError("discord client is not initialized")

    await CLIENT.wait_until_ready()
    channel = CLIENT.get_channel(CHANNEL_ID)
    if channel is None:
        channel = await CLIENT.fetch_channel(CHANNEL_ID)
    await channel.send(content)


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "DiscordRelay/1.0"

    def do_GET(self) -> None:
        if self.path != "/healthz":
            json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        json_response(
            self,
            {
                "ok": True,
                "ready": READY.is_set(),
                "channelId": str(CHANNEL_ID),
            },
        )

    def do_POST(self) -> None:
        if self.path != "/notify":
            json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        if RELAY_SECRET and self.headers.get("X-Relay-Secret", "") != RELAY_SECRET:
            json_response(self, {"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return

        if CLIENT_LOOP is None or CLIENT is None or not READY.is_set():
            json_response(self, {"error": "relay not ready"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            json_response(self, {"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return

        content = str(payload.get("content", "")).strip()
        if not content:
            json_response(self, {"error": "content is required"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            future = asyncio.run_coroutine_threadsafe(send_channel_message(content), CLIENT_LOOP)
            future.result(timeout=15)
        except Exception as error:
            print(f"[relay] send failed error={error} content={content}", file=sys.stderr, flush=True)
            json_response(self, {"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return

        print(f"[relay] delivered content={content}", flush=True)
        json_response(self, {"ok": True})

    def log_message(self, fmt: str, *args) -> None:
        return


def run_discord_client() -> None:
    global CLIENT_LOOP
    global CLIENT

    loop = asyncio.new_event_loop()
    CLIENT_LOOP = loop
    asyncio.set_event_loop(loop)

    intents = discord.Intents.none()
    client = discord.Client(intents=intents)
    CLIENT = client

    @client.event
    async def on_ready() -> None:
        user = getattr(client, "user", None)
        print(f"[relay] connected as {user}", flush=True)
        READY.set()

    @client.event
    async def on_disconnect() -> None:
        READY.clear()
        print("[relay] disconnected", file=sys.stderr, flush=True)

    async def runner() -> None:
        try:
            await client.start(BOT_TOKEN)
        except Exception as error:
            READY.clear()
            print(f"[relay] failed to start: {error}", file=sys.stderr, flush=True)

    loop.run_until_complete(runner())


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN 환경변수를 먼저 설정해주세요.")
    if not CHANNEL_ID:
        raise SystemExit("DISCORD_CHANNEL_ID 환경변수를 먼저 설정해주세요.")

    bot_thread = threading.Thread(target=run_discord_client, name="discord-relay-bot", daemon=True)
    bot_thread.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), RelayHandler)
    print(f"[relay] listening on http://0.0.0.0:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
