from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_FILE = ROOT / "storage.json"
LOCK = threading.Lock()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
PORT = int(os.environ.get("PORT", "8000"))


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


def normalize_state(state: dict) -> dict:
    for queue in state.get("queues", []):
        for slot in queue.get("slots", []):
            slot.setdefault("lastCall", False)
        queue.setdefault("waitlist", make_wait_slots(queue["id"]))
        for slot in queue.get("waitlist", []):
            slot.setdefault("lastCall", False)
    state.setdefault("events", [])
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
    state["events"] = ([{"message": message, "time": datetime.now().strftime("%H:%M:%S")}] + state["events"])[:20]


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


def send_discord_notification(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("[discord] webhook url not configured", file=sys.stderr, flush=True)
        return

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
            return
    except HTTPError as error:
        details = ""
        try:
            details = error.read().decode("utf-8", errors="replace")
        except Exception:
            details = "<no-body>"
        print(
            f"[discord] http error status={error.code} message={message} details={details}",
            file=sys.stderr,
            flush=True,
        )
        return
    except URLError:
        print(f"[discord] network error message={message}", file=sys.stderr, flush=True)
        return


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
        else:
            message = format_prefixed_message(slot, f"{nickname}님이 {queue['name']} - {slot['label']}에 참석했습니다.")
            discord_message = build_discord_message("파티 참석", "✅", nickname, queue, slot, "참석 완료")
        append_event(state, message)
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
        else:
            status_text = "막판 상태에서 파티 제외" if slot.get("lastCall") else "파티 제외"
            message = format_prefixed_message(slot, f"{nickname}님이 {queue['name']} - {slot['label']}에서 파티 제외되었습니다.")
            discord_message = build_discord_message("파티 제외", "↩️", nickname, queue, slot, status_text)
        slot["occupant"] = None
        slot["lastCall"] = False
        append_event(state, message)
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
        else:
            message = f"[막판 해제] {nickname}님이 {queue['name']} - {slot['label']}에서 막판을 해제했습니다."
            discord_message = build_discord_message("막판 해제", "🧊", nickname, queue, slot, "막판 상태를 해제했어요")
        append_event(state, message)
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
            else:
                status_text = "막판 상태에서 파티 제외" if slot.get("lastCall") else "파티 제외"
                message = format_prefixed_message(slot, f"{target_nickname}님이 {queue['name']} - {slot['label']}에서 파티 제외되었습니다.")
                discord_message = build_discord_message("파티 제외", "↩️", target_nickname, queue, slot, status_text)
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

        slot["occupant"] = None
        slot["lastCall"] = False
        append_event(state, message)
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

    server = ThreadingHTTPServer(("0.0.0.0", PORT), PartyHandler)
    print(f"Server running at http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
