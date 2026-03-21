from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    import discord
except ImportError:  # pragma: no cover - Render installs this from requirements.txt
    discord = None


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_FILE = ROOT / "storage.json"
LOCK = threading.Lock()
NOTIFIER_LOCK = threading.Lock()
NOTIFICATION_WORKER_LOCK = threading.Lock()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
PORT = int(os.environ.get("PORT", "8000"))
DISCORD_NOTIFIER = None
NOTIFICATION_QUEUE: queue.Queue[dict[str, object]] = queue.Queue()
NOTIFICATION_WORKER = None


def make_slots(prefix: str, count: int) -> list[dict]:
    return [{"id": f"{prefix}-{index}", "label": f"{index}번", "occupant": None, "lastCall": False} for index in range(1, count + 1)]


def make_wait_slots(prefix: str, count: int = 2) -> list[dict]:
    return [{"id": f"{prefix}-wait-{index}", "label": f"대기 {index}", "occupant": None, "lastCall": False} for index in range(1, count + 1)]


def make_role_slots(prefix: str) -> list[dict]:
    roles = [
        ("top", "탑"),
        ("jungle", "정글"),
        ("mid", "미드"),
        ("adc", "원딜"),
        ("support", "서폿"),
    ]
    return [{"id": f"{prefix}-{role_id}", "label": label, "occupant": None, "lastCall": False} for role_id, label in roles]


def make_double_up_slots(prefix: str) -> list[dict]:
    slots: list[dict] = []
    for team in range(1, 5):
        for seat in range(1, 3):
            slots.append(
                {
                    "id": f"{prefix}-team-{team}-seat-{seat}",
                    "label": f"{team}팀 {seat}인",
                    "occupant": None,
                    "lastCall": False,
                }
            )
    return slots


def make_queue(queue_id: str, name: str, slots: list[dict]) -> dict:
    return {
        "id": queue_id,
        "name": name,
        "slots": slots,
        "waitlist": make_wait_slots(queue_id),
    }


def build_initial_state() -> dict:
    queues = [
        make_queue("aram-normal-1", "칼바람 일반 1", make_slots("aram-normal-1", 5)),
        make_queue("aram-normal-2", "칼바람 일반 2", make_slots("aram-normal-2", 5)),
        make_queue("aram-augment-1", "칼바람 증강 1", make_slots("aram-augment-1", 5)),
        make_queue("aram-augment-2", "칼바람 증강 2", make_slots("aram-augment-2", 5)),
        make_queue("rift-normal-1", "협곡 일반 1", make_role_slots("rift-normal-1")),
        make_queue("rift-normal-2", "협곡 일반 2", make_role_slots("rift-normal-2")),
        make_queue("rift-flex", "협곡 자랭", make_role_slots("rift-flex")),
        make_queue("tft-normal", "롤체 일반", make_slots("tft-normal", 8)),
        make_queue("tft-double-up", "롤체 더블업", make_double_up_slots("tft-double-up")),
    ]
    return {
        "queues": queues,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "events": [],
    }


def normalize_nickname(raw: str) -> str:
    nickname = " ".join((raw or "").strip().split())
    if not nickname:
        raise ValueError("닉네임을 입력해주세요.")
    if len(nickname) > 20:
        raise ValueError("닉네임은 20자 이하로 입력해주세요.")
    return nickname


def load_state() -> dict:
    if not DATA_FILE.exists():
        state = build_initial_state()
        save_state(state)
        return state

    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            return normalize_state(json.load(file))
    except json.JSONDecodeError:
        state = build_initial_state()
        save_state(state)
        return state


def save_state(state: dict) -> None:
    state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def find_membership(state: dict, nickname: str) -> tuple[dict | None, dict | None]:
    for queue in state["queues"]:
        for slot in iter_queue_slots(queue):
            if slot["occupant"] == nickname:
                return queue, slot
    return None, None


def iter_queue_slots(queue: dict) -> list[dict]:
    return [*queue.get("slots", []), *queue.get("waitlist", [])]


def is_wait_slot(slot: dict) -> bool:
    return "-wait-" in slot["id"]


def normalize_event_entry(event: dict | str) -> dict:
    if isinstance(event, str):
        message = event
        return {
            "time": datetime.now().strftime("%H:%M:%S"),
            "title": "업데이트",
            "tone": "info",
            "lines": [message],
            "message": message,
        }

    time = event.get("time") or datetime.now().strftime("%H:%M:%S")
    title = event.get("title") or "업데이트"
    tone = event.get("tone") or "info"
    lines = event.get("lines")
    message = event.get("message") or ""

    if not isinstance(lines, list) or not lines:
        lines = [message] if message else []

    if not message:
        message = " ".join(lines)

    return {
        "time": time,
        "title": title,
        "tone": tone,
        "lines": lines,
        "message": message,
    }


def parse_retry_after(details: str) -> float | None:
    if not details:
        return None

    try:
        payload = json.loads(details)
    except json.JSONDecodeError:
        return None

    retry_after = payload.get("retry_after")
    if isinstance(retry_after, (int, float)):
        return max(float(retry_after), 1.0)
    return None


def normalize_state(state: dict) -> dict:
    for queue in state.get("queues", []):
        for slot in queue.get("slots", []):
            slot.setdefault("lastCall", False)
        queue.setdefault("waitlist", make_wait_slots(queue["id"]))
        for slot in queue.get("waitlist", []):
            slot.setdefault("lastCall", False)
    state["events"] = [normalize_event_entry(event) for event in state.get("events", [])]
    return state


def get_queue(state: dict, queue_id: str) -> dict:
    for queue in state["queues"]:
        if queue["id"] == queue_id:
            return queue
    raise ValueError("존재하지 않는 파티입니다.")


def get_slot(queue: dict, slot_id: str) -> dict:
    for slot in iter_queue_slots(queue):
        if slot["id"] == slot_id:
            return slot
    raise ValueError("존재하지 않는 자리입니다.")


def append_event(state: dict, message: str) -> None:
    state["events"] = ([normalize_event_entry({"message": message})] + state["events"])[:20]


def append_structured_event(state: dict, event: dict) -> None:
    state["events"] = ([normalize_event_entry(event)] + state["events"])[:20]


def format_prefixed_message(slot: dict, message: str) -> str:
    prefix = "[막판] " if slot.get("lastCall") else ""
    return f"{prefix}{message}"


def format_queue_name(queue: dict) -> str:
    queue_id = queue["id"]
    queue_name = queue["name"]

    if queue_id.startswith("rift-normal"):
        return queue_name.replace("협곡 일반", "⚔️ 협곡 일반")
    if queue_id == "rift-flex":
        return f"⚔️ {queue_name}"
    if queue_id.startswith("aram-normal"):
        return queue_name.replace("칼바람 일반", "❄️ 칼바람 일반")
    if queue_id.startswith("aram-augment"):
        return queue_name.replace("칼바람 증강", "❄️ 칼바람 증강")
    if queue_id in {"tft-normal", "tft-double-up"}:
        return f"🧩 {queue_name}"
    return queue_name


def build_discord_message(title: str, icon: str, nickname: str, queue: dict, slot: dict, status_text: str) -> str:
    lines = [
        f"{icon} **{title}**",
        f"> 닉네임: **{nickname}**",
        f"> 파티: **{format_queue_name(queue)}**",
        f"> 자리: **{slot['label']}**",
        f"> 상태: {status_text}",
        f"> 시간: `{datetime.now().strftime('%H:%M:%S')}`",
    ]
    return "\n".join(lines)


def build_actor_discord_message(title: str, icon: str, actor_nickname: str, target_nickname: str, queue: dict, slot: dict, status_text: str) -> str:
    lines = [
        f"{icon} **{title}**",
        f"> 대상: **{target_nickname}**",
        f"> 처리자: **{actor_nickname}**",
        f"> 파티: **{format_queue_name(queue)}**",
        f"> 자리: **{slot['label']}**",
        f"> 상태: {status_text}",
        f"> 시간: `{datetime.now().strftime('%H:%M:%S')}`",
    ]
    return "\n".join(lines)


def build_event_entry(
    title: str,
    icon: str,
    queue: dict,
    slot: dict,
    status_text: str,
    *,
    tone: str = "info",
    nickname: str | None = None,
    actor_nickname: str | None = None,
    target_nickname: str | None = None,
) -> dict:
    lines: list[str] = []
    if nickname:
        lines.append(f"닉네임: {nickname}")
    if target_nickname:
        lines.append(f"대상: {target_nickname}")
    if actor_nickname:
        lines.append(f"처리자: {actor_nickname}")
    lines.extend(
        [
            f"파티: {format_queue_name(queue)}",
            f"자리: {slot['label']}",
            f"상태: {status_text}",
        ]
    )
    return {
        "title": f"{icon} {title}",
        "time": datetime.now().strftime("%H:%M:%S"),
        "tone": tone,
        "lines": lines,
        "message": " ".join(lines),
    }


class DiscordBotNotifier:
    def __init__(self, token: str, channel_id: str) -> None:
        self.token = token
        self.channel_id = int(channel_id)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.client = None
        self.ready = threading.Event()

    def start(self) -> None:
        if discord is None:
            print("[discord-bot] discord.py is not installed", file=sys.stderr, flush=True)
            return

        if self.thread and self.thread.is_alive():
            return

        self.thread = threading.Thread(target=self._run, name="discord-bot-notifier", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        intents = discord.Intents.none()
        self.client = discord.Client(intents=intents)

        @self.client.event
        async def on_ready() -> None:
            user = getattr(self.client, "user", None)
            print(f"[discord-bot] connected as {user}", flush=True)
            self.ready.set()

        @self.client.event
        async def on_disconnect() -> None:
            self.ready.clear()
            print("[discord-bot] disconnected", file=sys.stderr, flush=True)

        async def runner() -> None:
            try:
                await self.client.start(self.token)
            except Exception as error:
                self.ready.clear()
                print(f"[discord-bot] failed to start: {error}", file=sys.stderr, flush=True)

        self.loop.run_until_complete(runner())

    async def _send(self, message: str) -> None:
        if self.client is None:
            raise RuntimeError("client not initialized")

        await self.client.wait_until_ready()
        channel = self.client.get_channel(self.channel_id)
        if channel is None:
            channel = await self.client.fetch_channel(self.channel_id)
        await channel.send(message)

    def send(self, message: str) -> bool:
        if self.loop is None or self.client is None:
            return False
        if not self.ready.wait(timeout=1):
            return False

        try:
            future = asyncio.run_coroutine_threadsafe(self._send(message), self.loop)
            future.result(timeout=15)
            print(f"[discord-bot] delivered via gateway message={message}", flush=True)
            return True
        except Exception as error:
            print(f"[discord-bot] gateway send failed message={message} error={error}", file=sys.stderr, flush=True)
            return False


def get_discord_notifier() -> DiscordBotNotifier | None:
    global DISCORD_NOTIFIER

    if not (DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID):
        return None

    if discord is None:
        return None

    try:
        int(DISCORD_CHANNEL_ID)
    except ValueError:
        print("[discord-bot] DISCORD_CHANNEL_ID must be a numeric channel id", file=sys.stderr, flush=True)
        return None

    with NOTIFIER_LOCK:
        if DISCORD_NOTIFIER is None:
            DISCORD_NOTIFIER = DiscordBotNotifier(DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID)
            DISCORD_NOTIFIER.start()
        return DISCORD_NOTIFIER


def start_notification_worker() -> None:
    global NOTIFICATION_WORKER

    if not (DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID) and not DISCORD_WEBHOOK_URL:
        return

    with NOTIFICATION_WORKER_LOCK:
        if NOTIFICATION_WORKER and NOTIFICATION_WORKER.is_alive():
            return

        NOTIFICATION_WORKER = threading.Thread(
            target=run_notification_worker,
            name="discord-notification-worker",
            daemon=True,
        )
        NOTIFICATION_WORKER.start()


def run_notification_worker() -> None:
    while True:
        payload = NOTIFICATION_QUEUE.get()
        try:
            message = str(payload.get("message", ""))
            attempt = int(payload.get("attempt", 1))
            delivered, retry_after = deliver_discord_notification(message)
            if delivered:
                continue

            if retry_after and attempt < 5:
                print(
                    f"[discord] rate limited; retrying in {retry_after:.0f}s attempt={attempt + 1} message={message}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(retry_after)
                NOTIFICATION_QUEUE.put({"message": message, "attempt": attempt + 1})
                continue

            print(
                f"[discord] delivery failed permanently attempt={attempt} message={message}",
                file=sys.stderr,
                flush=True,
            )
        finally:
            NOTIFICATION_QUEUE.task_done()


def deliver_discord_notification(message: str) -> tuple[bool, float | None]:
    notifier = get_discord_notifier()
    if notifier and notifier.send(message):
        return True, None

    if DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID:
        return send_discord_bot_notification(message)

    if DISCORD_WEBHOOK_URL:
        return send_discord_webhook_notification(message)

    print("[discord] notification target not configured", file=sys.stderr, flush=True)
    return False, None


def send_discord_notification(message: str) -> None:
    if not (DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID) and not DISCORD_WEBHOOK_URL:
        print("[discord] notification target not configured", file=sys.stderr, flush=True)
        return

    start_notification_worker()
    NOTIFICATION_QUEUE.put({"message": message, "attempt": 1})


def send_discord_webhook_notification(message: str) -> tuple[bool, float | None]:
    body = json.dumps({"content": message}).encode("utf-8")
    request = Request(
        DISCORD_WEBHOOK_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            print(f"[discord] delivered status={response.status} message={message}", flush=True)
            return True, None
    except HTTPError as error:
        details = ""
        try:
            details = error.read().decode("utf-8", errors="replace")
        except Exception:
            details = "<no-body>"
        retry_after = parse_retry_after(details) if error.code == 429 else None
        print(
            f"[discord] http error status={error.code} message={message} details={details}",
            file=sys.stderr,
            flush=True,
        )
        return False, retry_after
    except URLError:
        print(f"[discord] network error message={message}", file=sys.stderr, flush=True)
        return False, None


def send_discord_bot_notification(message: str) -> tuple[bool, float | None]:
    body = json.dumps({"content": message}).encode("utf-8")
    request = Request(
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            print(f"[discord-bot] delivered status={response.status} message={message}", flush=True)
            return True, None
    except HTTPError as error:
        details = ""
        try:
            details = error.read().decode("utf-8", errors="replace")
        except Exception:
            details = "<no-body>"
        retry_after = parse_retry_after(details) if error.code == 429 else None
        hint = ""
        if error.code == 403:
            hint = (
                " hint=verify the bot is guild-installed and has View Channels / Send Messages "
                "permission for the configured channel"
            )
        print(
            f"[discord-bot] http error status={error.code} message={message} details={details}{hint}",
            file=sys.stderr,
            flush=True,
        )
        return False, retry_after
    except URLError:
        print(f"[discord-bot] network error message={message}", file=sys.stderr, flush=True)
        return False, None


def join_queue(payload: dict) -> dict:
    nickname = normalize_nickname(payload.get("nickname", ""))
    queue_id = payload.get("queueId", "")
    slot_id = payload.get("slotId", "")

    with LOCK:
        state = load_state()
        current_queue, current_slot = find_membership(state, nickname)
        if current_slot and (current_queue["id"] != queue_id or current_slot["id"] != slot_id):
            raise ValueError("이미 다른 파티에 참석 중입니다. 먼저 파티 제외를 눌러주세요.")

        queue = get_queue(state, queue_id)
        slot = get_slot(queue, slot_id)

        if slot["occupant"] and slot["occupant"] != nickname:
            raise ValueError("이미 다른 사람이 참석한 자리입니다.")

        slot["occupant"] = nickname
        slot["lastCall"] = False
        if is_wait_slot(slot):
            message = f"{nickname}님이 {queue['name']} - {slot['label']}에 등록되었습니다."
            discord_message = build_discord_message("대기 등록", "🕒", nickname, queue, slot, "대기열 등록 완료")
            event = build_event_entry("대기 등록", "🕒", queue, slot, "대기열 등록 완료", tone="wait", nickname=nickname)
        else:
            message = format_prefixed_message(slot, f"{nickname}님이 {queue['name']} - {slot['label']}에 참석했습니다.")
            discord_message = build_discord_message("파티 참석", "✅", nickname, queue, slot, "참석 완료")
            event = build_event_entry("파티 참석", "✅", queue, slot, "참석 완료", tone="success", nickname=nickname)
        append_structured_event(state, event)
        save_state(state)

    send_discord_notification(discord_message)
    return state


def leave_queue(payload: dict) -> dict:
    nickname = normalize_nickname(payload.get("nickname", ""))

    with LOCK:
        state = load_state()
        queue, slot = find_membership(state, nickname)
        if not slot:
            raise ValueError("참석 중인 파티가 없습니다.")

        if is_wait_slot(slot):
            status_text = "대기열에서 제거됨"
            message = f"{nickname}님이 {queue['name']} - {slot['label']}에서 대기 취소되었습니다."
            discord_message = build_discord_message("대기 취소", "↩️", nickname, queue, slot, status_text)
            event = build_event_entry("대기 취소", "↩️", queue, slot, status_text, tone="muted", nickname=nickname)
        else:
            status_text = "막판 상태에서 파티 제외" if slot.get("lastCall") else "파티 제외"
            message = format_prefixed_message(slot, f"{nickname}님이 {queue['name']} - {slot['label']}에서 파티 제외되었습니다.")
            discord_message = build_discord_message("파티 제외", "↩️", nickname, queue, slot, status_text)
            event = build_event_entry("파티 제외", "↩️", queue, slot, status_text, tone="danger", nickname=nickname)
        slot["occupant"] = None
        slot["lastCall"] = False
        append_structured_event(state, event)
        save_state(state)

    send_discord_notification(discord_message)
    return state


def update_last_call(payload: dict) -> dict:
    nickname = normalize_nickname(payload.get("nickname", ""))
    enabled = bool(payload.get("enabled"))

    with LOCK:
        state = load_state()
        queue, slot = find_membership(state, nickname)
        if not slot:
            raise ValueError("참석 중인 자리에서만 막판 설정이 가능합니다.")
        if is_wait_slot(slot):
            raise ValueError("대기열에서는 막판 설정을 사용할 수 없습니다.")

        slot["lastCall"] = enabled
        if enabled:
            message = f"[막판] {nickname}님이 {queue['name']} - {slot['label']}에서 막판입니다."
            discord_message = build_discord_message("막판 요청", "🔥", nickname, queue, slot, "막판 멤버를 찾고 있어요")
            event = build_event_entry("막판 요청", "🔥", queue, slot, "막판 멤버를 찾고 있어요", tone="warning", nickname=nickname)
        else:
            message = f"[막판 해제] {nickname}님이 {queue['name']} - {slot['label']}에서 막판을 해제했습니다."
            discord_message = build_discord_message("막판 해제", "🧊", nickname, queue, slot, "막판 상태를 해제했어요")
            event = build_event_entry("막판 해제", "🧊", queue, slot, "막판 상태를 해제했어요", tone="info", nickname=nickname)
        append_structured_event(state, event)
        save_state(state)

    send_discord_notification(discord_message)
    return state


def remove_queue_member(payload: dict) -> dict:
    actor_nickname = normalize_nickname(payload.get("nickname", ""))
    target_nickname = normalize_nickname(payload.get("targetNickname", ""))

    with LOCK:
        state = load_state()
        queue, slot = find_membership(state, target_nickname)
        if not slot:
            raise ValueError("해당 닉네임은 현재 파티나 대기열에 없습니다.")

        if actor_nickname == target_nickname:
            if is_wait_slot(slot):
                message = f"{target_nickname}님이 {queue['name']} - {slot['label']}에서 대기 취소되었습니다."
                discord_message = build_discord_message("대기 취소", "↩️", target_nickname, queue, slot, "대기열에서 제거됨")
                event = build_event_entry("대기 취소", "↩️", queue, slot, "대기열에서 제거됨", tone="muted", nickname=target_nickname)
            else:
                status_text = "막판 상태에서 파티 제외" if slot.get("lastCall") else "파티 제외"
                message = format_prefixed_message(slot, f"{target_nickname}님이 {queue['name']} - {slot['label']}에서 파티 제외되었습니다.")
                discord_message = build_discord_message("파티 제외", "↩️", target_nickname, queue, slot, status_text)
                event = build_event_entry("파티 제외", "↩️", queue, slot, status_text, tone="danger", nickname=target_nickname)
        else:
            if is_wait_slot(slot):
                message = f"{actor_nickname}님이 {target_nickname}님을 {queue['name']} - {slot['label']}에서 대기 취소시켰습니다."
                discord_message = build_actor_discord_message(
                    "대기 취소 처리",
                    "🧹",
                    actor_nickname,
                    target_nickname,
                    queue,
                    slot,
                    "다른 사용자가 대기열에서 제거했어요",
                )
                event = build_event_entry(
                    "대기 취소 처리",
                    "🧹",
                    queue,
                    slot,
                    "다른 사용자가 대기열에서 제거했어요",
                    tone="muted",
                    actor_nickname=actor_nickname,
                    target_nickname=target_nickname,
                )
            else:
                message = format_prefixed_message(slot, f"{actor_nickname}님이 {target_nickname}님을 {queue['name']} - {slot['label']}에서 파티 제외시켰습니다.")
                status_text = "다른 사용자가 파티에서 제외했어요"
                if slot.get("lastCall"):
                    status_text = "막판 상태에서 다른 사용자가 파티에서 제외했어요"
                discord_message = build_actor_discord_message(
                    "파티 제외 처리",
                    "🧹",
                    actor_nickname,
                    target_nickname,
                    queue,
                    slot,
                    status_text,
                )
                event = build_event_entry(
                    "파티 제외 처리",
                    "🧹",
                    queue,
                    slot,
                    status_text,
                    tone="danger",
                    actor_nickname=actor_nickname,
                    target_nickname=target_nickname,
                )

        slot["occupant"] = None
        slot["lastCall"] = False
        append_structured_event(state, event)
        save_state(state)

    send_discord_notification(discord_message)
    return state


class PartyHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        if path.startswith("/api/"):
            return super().translate_path(path)
        requested = path.split("?", 1)[0].split("#", 1)[0]
        relative = requested.lstrip("/") or "index.html"
        return str(STATIC_DIR / relative)

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self.respond_json(load_state())
            return

        if self.path == "/" or self.path.startswith("/?"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path not in {"/api/join", "/api/leave", "/api/last-call", "/api/remove"}:
            self.respond_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8") or "{}")

        try:
            if self.path == "/api/join":
                state = join_queue(payload)
            elif self.path == "/api/last-call":
                state = update_last_call(payload)
            elif self.path == "/api/remove":
                state = remove_queue_member(payload)
            else:
                state = leave_queue(payload)
        except ValueError as error:
            self.respond_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        self.respond_json(state)

    def log_message(self, fmt: str, *args) -> None:
        return

    def respond_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    STATIC_DIR.mkdir(exist_ok=True)
    if not DATA_FILE.exists():
        save_state(build_initial_state())

    get_discord_notifier()
    start_notification_worker()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), PartyHandler)
    print(f"Server running at http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
