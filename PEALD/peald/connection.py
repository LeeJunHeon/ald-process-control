"""
connection.py — WebSocket 연결 관리 + 로컬/원격 구분 + push_*.

★ 보안 경계: 원격(다른 PC) 접속은 '보기 전용'이다.
  host 를 0.0.0.0 으로 바꿔 원격에서 화면을 볼 수 있게 하더라도, 조작 명령은
  루프백(127.0.0.1 / ::1)에서 온 연결만 받는다. 판정은 반드시 서버가 한다 —
  화면 쪽 잠금은 개발자 도구로 풀 수 있으므로 신뢰하지 않는다.
"""

import json
import time
import ipaddress
from urllib.parse import urlsplit

from fastapi import WebSocket

from . import logger
from .state import state


def is_local(ws) -> bool:
    """이 연결이 이 PC에서 온 것인지. 판정 불가면 원격으로 본다(안전한 쪽)."""
    client = getattr(ws, "client", None)
    host = getattr(client, "host", None) if client else None
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _split_host(host: str):
    """'127.0.0.1:8201' · '[::1]:8201' · 'localhost' → (호스트, 포트 문자열)."""
    host = (host or "").strip()
    if host.startswith("["):
        end = host.find("]")
        return (host[1:end], host[end + 2:]) if end > 0 else ("", "")
    if host.count(":") == 1:
        h, _, p = host.partition(":")
        return h, p
    return host, ""


def host_ok(host_header) -> bool:
    """요청의 Host 가 IP 주소(v4/v6) 또는 localhost 인지.
    ★ 도메인 이름이면 거절한다 — DNS 재바인딩으로 외부 페이지가 이 서버에 닿는 길을 막는다."""
    h, p = _split_host(host_header)
    if not h:
        return False
    if p and not p.isdigit():
        return False
    if h.lower() == "localhost":
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def origin_ok(origin, host_header) -> bool:
    """Origin 이 있으면(브라우저는 항상 보낸다) 그 host:port 가 요청의 Host 와 같아야 한다.
    ★ 운전 PC 브라우저에 열린 아무 웹 페이지가 ws://127.0.0.1:포트/ws 로 명령을 보내지 못하게 한다."""
    if origin is None:
        return True                 # 브라우저가 아닌 도구 — 루프백 여부 규칙을 그대로 쓴다
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.netloc:
        return False
    return u.netloc.lower() == (host_header or "").strip().lower()


class ConnectionManager:
    def __init__(self):
        self.active = {}          # WebSocket -> {"local": bool}
        self._log_at = {}         # WebSocket -> 마지막 거절 로그 시각(초당 한 번만)

    async def connect(self, ws: WebSocket):
        await ws.accept()
        local = is_local(ws)
        self.active[ws] = {"local": local}
        await self._send(ws, state.snapshot(access_local=local))
        if local:
            from .admin import admin
            await self._send(ws, admin.status(ws))
        if not local:
            host = getattr(getattr(ws, "client", None), "host", "?")
            logger.write("info", f"원격 접속(보기 전용): {host}")
            await self._send(ws, {"type": "notice", "level": "info",
                                  "msg": "원격 접속입니다 — 보기 전용으로 동작합니다"})

    def disconnect(self, ws):
        self.active.pop(ws, None)
        self._log_at.pop(ws, None)

    def log_limited(self, ws, level: str, msg: str):
        """같은 연결에서 초당 한 번만 로그를 남긴다(명령을 쏟아부어 로그를 부풀리지 못하게)."""
        now = time.monotonic()
        if now - self._log_at.get(ws, 0.0) < 1.0:
            return
        self._log_at[ws] = now
        logger.write(level, msg)

    def is_local_ws(self, ws) -> bool:
        return bool((self.active.get(ws) or {}).get("local"))

    async def _send(self, ws, obj: dict):
        try:
            await ws.send_text(json.dumps(obj, ensure_ascii=False, default=_json_default))
        except Exception:  # noqa: BLE001
            self.active.pop(ws, None)

    async def send_to(self, ws, obj: dict):
        await self._send(ws, obj)

    async def broadcast(self, obj: dict):
        if not self.active:
            return
        text = json.dumps(obj, ensure_ascii=False, default=_json_default)
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.active.pop(ws, None)


def _json_default(o):
    """float('inf') 같은 값이 섞여도 직렬화가 통째로 실패하지 않게 한다."""
    return None


manager = ConnectionManager()


async def push_state():
    """전체 스냅샷. access(조작 권한)는 연결마다 다르므로 개별 전송한다."""
    for ws, meta in list(manager.active.items()):
        await manager.send_to(ws, state.snapshot(access_local=meta.get("local", False)))


async def push_live():
    await manager.broadcast(state.live())


async def push_log(msg: str, level: str = "info"):
    """화면 로그 + 파일 로그 + 서버 보관(접속 시 재생)."""
    logger.write(level, msg)
    state.add_log(level, msg)
    await manager.broadcast({"type": "log", "level": level, "msg": msg,
                             "ts": state.logs[-1]["ts"]})


async def push_notice(msg: str, level: str = "info", ws=None):
    """토스트. 명령을 보낸 사람에게만 알려야 할 때는 ws 를 지정한다."""
    obj = {"type": "notice", "level": level, "msg": msg}
    if ws is not None:
        await manager.send_to(ws, obj)
    else:
        await manager.broadcast(obj)
