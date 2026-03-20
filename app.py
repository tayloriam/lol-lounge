from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_FILE = ROOT / "storage.json"
LOCK = threading.Lock()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
PORT = int(os.environ.get("PORT", "8000"))


def make_slots(prefix: str, count: int) -> list[dict]:
    return [{"id": f"{prefix}-{index}", "label": f"{index}번", "occupant": None} for index in range(1, count + 1)]


def make_role_slots(prefix: str) -> list[dict]:
    roles = [
        ("top", "탑"),
        ("jungle", "정글"),
        ("mid", "미드"),
        ("adc", "원딜"),
        ("support", "서폿"),
    ]
    return [{"id": f"{prefix}-{role_id}", "label": label, "occupant": None} for role_id, label in roles]


def make_double_up_slots(prefix: str) -> list[dict]:
    slots: list[dict] = []
    for team in range(1, 5):
        for seat in range(1, 3):
            slots.append(
                {
                    "id": f"{prefix}-team-{team}-seat-{seat}",
                    "label": f"{team}팀 {seat}인",
                    "occupant": None,
                }
            )
    return slots


def build_initial_state() -> dict:
    queues = [
        {"id": "aram-normal-1", "name": "칼바람 일반 1", "slots": make_slots("aram-normal-1", 5)},
        {"id": "aram-normal-2", "name": "칼바람 일반 2", "slots": make_slots("aram-normal-2", 5)},
        {"id": "aram-augment-1", "name": "칼바람 증강 1", "slots": make_slots("aram-augment-1", 5)},
        {"id": "aram-augment-2", "name": "칼바람 증강 2", "slots": make_slots("aram-augment-2", 5)},
        {"id": "rift-normal-1", "name": "협곡 일반 1", "slots": make_role_slots("rift-normal-1")},
        {"id": "rift-normal-2", "name": "협곡 일반 2", "slots": make_role_slots("rift-normal-2")},
        {"id": "rift-flex", "name": "협곡 자랭", "slots": make_role_slots("rift-flex")},
        {"id": "tft-normal", "name": "롤체 일반", "slots": make_slots("tft-normal", 8)},
        {"id": "tft-double-up", "name": "롤체 더블업", "slots": make_double_up_slots("tft-double-up")},
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
            return json.load(file)
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
        for slot in queue["slots"]:
            if slot["occupant"] == nickname:
                return queue, slot
    return None, None


def get_queue(state: dict, queue_id: str) -> dict:
    for queue in state["queues"]:
        if queue["id"] == queue_id:
            return queue
    raise ValueError("존재하지 않는 파티입니다.")


def get_slot(queue: dict, slot_id: str) -> dict:
    for slot in queue["slots"]:
        if slot["id"] == slot_id:
            return slot
    raise ValueError("존재하지 않는 자리입니다.")


def append_event(state: dict, message: str) -> None:
    state["events"] = ([{"message": message, "time": datetime.now().strftime("%H:%M:%S")}] + state["events"])[:20]


def send_discord_notification(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return

    body = json.dumps({"content": message}).encode("utf-8")
    request = Request(
        DISCORD_WEBHOOK_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "lol-lounge-webhook/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=5):
            return
    except URLError:
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
        message = f"{nickname}님이 {queue['name']} - {slot['label']}에 참석했습니다."
        append_event(state, message)
        save_state(state)

    send_discord_notification(message)
    return state


def leave_queue(payload: dict) -> dict:
    nickname = normalize_nickname(payload.get("nickname", ""))

    with LOCK:
        state = load_state()
        queue, slot = find_membership(state, nickname)
        if not slot:
            raise ValueError("참석 중인 파티가 없습니다.")

        slot["occupant"] = None
        message = f"{nickname}님이 {queue['name']} - {slot['label']}에서 파티 제외되었습니다."
        append_event(state, message)
        save_state(state)

    send_discord_notification(message)
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
        if self.path not in {"/api/join", "/api/leave"}:
            self.respond_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8") or "{}")

        try:
            if self.path == "/api/join":
                state = join_queue(payload)
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
