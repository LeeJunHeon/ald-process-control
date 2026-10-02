"""
connection.py — WebSocket 연결 관리 + 로컬/원격 구분 + push_*.

★ 보안 경계: 원격(다른 PC) 접속은 '보기 전용'이다.
  host 를 0.0.0.0 으로 바꿔 원격에서 화면을 볼 수 있게 하더라도, 조작 명령은
  루프백(127.0.0.1 / ::1)에서 온 연결만 받는다. 판정은 반드시 서버가 한다 —
  화면 쪽 잠금은 개발자 도구로 풀 수 있으므로 신뢰하지 않는다.

★ 원격 접속 한도 — 원격이 장비와 이 PC 화면에 영향을 주지 못하게:
  받는 쪽: 원격 연결은 메시지 속도 한도(초당 RATE_PER_S · 순간 RATE_BURST)를 넘으면 JSON 을 풀기 전에
           버리고, RATE_KICK_S 넘게 계속 넘치면 1008 로 닫는다. 원격 연결 수는 REMOTE_MAX(넘으면 1013).
           로컬(이 PC 화면)에는 속도 한도가 없다 — 중단·알람 명령이 버려지면 안 된다.
  주는 쪽: 연결마다 보내기 대기열과 보내기 태스크를 둔다. push_* · send_to 는 넣기만 하고 네트워크를
           기다리지 않는다. live 는 연결마다 가장 최근 것 하나만. 대기열이 QUEUE_MAX 를 넘거나
           send 하나가 SEND_TIMEOUT_S 안에 끝나지 않으면 그 연결을 닫는다(화면은 다시 연결한다) —
           읽지 않는 화면 하나가 다른 화면의 live 를 멈추지 못하게.
"""

import json
import time
import asyncio
import ipaddress
from collections import deque
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


# ---- 원격 접속 한도 (README '원격 접속 한도') ----
RATE_PER_S = 5.0            # 원격 연결 메시지 속도(초당)
RATE_BURST = 10             # 순간 허용 개수
RATE_KICK_S = 10.0          # 이만큼 계속 넘치면 1008 로 닫는다
REMOTE_MAX = 4              # 원격 연결 수
QUEUE_MAX = 200             # 연결 하나의 보내기 대기열(live 제외)
SEND_TIMEOUT_S = 5.0        # send 하나가 이 안에 끝나지 않으면 그 연결을 닫는다


# IP 별로 묶는 로그 — 같은 일은 초당 한 줄, 묶여 빠진 건수는 다음 줄에 '같은 일 N건 더'
_grouped = {}


def grouped_log(key, level: str, msg: str):
    """[마지막으로 쓴 시각, 묶여 빠진 건수, 마지막 글, 수준, 남은 건수 쓰기 예약]"""
    now = time.monotonic()
    ent = _grouped.get(key)
    if ent is not None and now - ent[0] < 1.0:
        ent[1] += 1
        ent[2], ent[3] = msg, level
        if ent[4] is None:
            # ★ 마지막 묶음 뒤 다음 일이 없으면 'N건 더'가 영영 안 남는다 — 1 s 뒤 남은 건수를 한 줄로
            try:
                ent[4] = asyncio.get_running_loop().call_later(max(0.0, ent[0] + 1.0 - now),
                                                               _grouped_flush, key)
            except RuntimeError:
                pass                            # 루프 밖(시험) — 다음 일이 올 때 붙인다
        return
    if ent is not None and ent[4] is not None:
        ent[4].cancel()
    extra = f" — 같은 일 {ent[1]}건 더" if ent is not None and ent[1] else ""
    logger.write(level, msg + extra)
    _grouped[key] = [now, 0, msg, level, None]
    if len(_grouped) > 2000:                    # 오래된 항목 정리(몇 달 켜 두는 장비)
        for k in [k for k, v in _grouped.items() if now - v[0] > 60]:
            _grouped.pop(k, None)


def _grouped_flush(key):
    ent = _grouped.get(key)
    if ent is None:
        return
    ent[4] = None
    if ent[1]:
        # 마지막 글을 한 줄로 보이고, 'N건 더'는 그 줄을 뺀 수
        more = ent[1] - 1
        logger.write(ent[3], ent[2] + (f" — 같은 일 {more}건 더" if more else ""))
        ent[0], ent[1] = time.monotonic(), 0


def _host_of(ws) -> str:
    return logger.clean(getattr(getattr(ws, "client", None), "host", "?"), 60)


class ConnectionManager:
    def __init__(self):
        # WebSocket -> 연결 정보. "local" 은 판정 결과, "q" 가 있으면 보내기 대기열을 쓰는 실제 연결이다
        # (시험에서 {"local": ...} 만 넣은 연결은 대기열 없이 바로 보낸다).
        self.active = {}
        self._log_at = {}         # WebSocket -> 마지막 거절 로그 시각(초당 한 번만)
        self._full_log_at = 0.0   # 원격 연결 수 초과 로그(초당 한 번)

    async def connect(self, ws: WebSocket) -> bool:
        """받아들이면 True. 원격 연결 수가 넘치면 1013 으로 닫고 False."""
        local = is_local(ws)
        if not local and self.remote_count() >= REMOTE_MAX:
            await ws.accept()
            grouped_log(("remote-full", _host_of(ws)), "warn",
                        f"원격 연결 수 한도({REMOTE_MAX}) — 연결을 받지 않았습니다 ({_host_of(ws)})")
            try:
                await ws.close(code=1013)
            except Exception:  # noqa: BLE001
                pass
            return False
        await ws.accept()
        meta = {"local": local, "host": _host_of(ws), "q": deque(), "live": None,
                "wake": asyncio.Event(), "closed": False,
                "tokens": float(RATE_BURST), "tok_at": time.monotonic(),
                "over_since": None, "last_drop": 0.0, "dropped": 0}
        self.active[ws] = meta
        meta["task"] = asyncio.create_task(self._pump(ws, meta))
        # ★ 접속 직후 보내는 것도 같은 대기열로 — 순서가 바뀌지 않게
        recipes = await _recipes_async()
        self._enqueue(ws, meta, _dumps(state.snapshot(access_local=local, recipes=recipes)))
        if local:
            from .admin import admin
            self._enqueue(ws, meta, _dumps(admin.status(ws)))
        else:
            grouped_log(("remote-in", meta["host"]), "info", f"원격 접속(보기 전용): {meta['host']}")
            self._enqueue(ws, meta, _dumps({"type": "notice", "level": "info",
                                            "msg": "원격 접속입니다 — 보기 전용으로 동작합니다"}))
        return True

    def remote_count(self) -> int:
        return sum(1 for m in self.active.values() if not m.get("local") and not m.get("closed"))

    def disconnect(self, ws):
        meta = self.active.pop(ws, None)
        self._log_at.pop(ws, None)
        if meta and "q" in meta:
            meta["closed"] = True
            t = meta.get("task")
            if t and t is not asyncio.current_task():
                t.cancel()

    # ---------- 받는 쪽: 속도 한도 ----------
    def admit(self, ws) -> str:
        """원격 메시지 하나를 받아도 되는가 — 'ok' · 'drop'(버림) · 'kick'(1008 로 닫을 때).
        ★ JSON 을 풀기 전에 부른다. 로컬은 언제나 'ok'."""
        meta = self.active.get(ws)
        if meta is None or meta.get("local") or "q" not in meta:
            return "ok"
        now = time.monotonic()
        meta["tokens"] = min(float(RATE_BURST), meta["tokens"] + (now - meta["tok_at"]) * RATE_PER_S)
        meta["tok_at"] = now
        if meta["tokens"] >= 1.0:
            meta["tokens"] -= 1.0
            return "ok"
        meta["dropped"] += 1
        # 버림이 1 초 넘게 끊기면 '계속 넘침'을 새로 센다
        if meta["over_since"] is None or now - meta["last_drop"] > 1.0:
            meta["over_since"] = now
        meta["last_drop"] = now
        if now - meta["over_since"] > RATE_KICK_S:
            logger.write("warn", f"원격 메시지 속도 한도(초당 {RATE_PER_S:g}개)를 {RATE_KICK_S:g} s 넘게 넘어 "
                                 f"연결을 끊었습니다 ({meta['host']} · 버린 메시지 {meta['dropped']}개)")
            return "kick"
        return "drop"

    # ---------- 주는 쪽: 연결마다 대기열 ----------
    def _enqueue(self, ws, meta, text: str):
        if meta.get("closed"):
            return
        if len(meta["q"]) >= QUEUE_MAX:
            logger.write("warn", f"화면 보내기 대기열이 넘쳐 연결을 끊었습니다 ({self._who(meta)})")
            self._drop(ws, meta, 1008)
            return
        meta["q"].append(text)
        meta["wake"].set()

    def _set_live(self, ws, meta, text: str):
        if meta.get("closed"):
            return
        meta["live"] = text                 # ★ 밀리면 옛 live 는 버린다(가장 최근 것 하나만)
        meta["wake"].set()

    @staticmethod
    def _who(meta) -> str:
        return "이 PC 화면" if meta.get("local") else meta.get("host", "?")

    async def _pump(self, ws, meta):
        """이 연결 하나만의 보내기 태스크 — 느린 화면은 자기만 늦는다."""
        try:
            while not meta["closed"]:
                await meta["wake"].wait()
                meta["wake"].clear()
                while not meta["closed"]:
                    if meta["q"]:
                        text = meta["q"].popleft()
                    elif meta["live"] is not None:
                        text, meta["live"] = meta["live"], None
                    else:
                        break
                    try:
                        await asyncio.wait_for(ws.send_text(text), SEND_TIMEOUT_S)
                    except asyncio.TimeoutError:
                        logger.write("warn", f"화면 응답 없음 — 연결을 끊었습니다 ({self._who(meta)})")
                        self._drop(ws, meta, 1008)
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        self._drop(ws, meta, None)
                        return
        except asyncio.CancelledError:
            pass

    def _drop(self, ws, meta, code):
        """연결을 목록에서 빼고 닫는다(닫기는 기다리지 않는다 — 막힌 소켓에서 오래 걸릴 수 있다)."""
        meta["closed"] = True
        self.active.pop(ws, None)
        self._log_at.pop(ws, None)
        t = meta.get("task")
        if t and t is not asyncio.current_task():
            t.cancel()
        if code is not None:
            asyncio.create_task(_close_quietly(ws, code))

    def log_limited(self, ws, level: str, msg: str):
        """같은 연결에서 초당 한 번만 로그를 남긴다(명령을 쏟아부어 로그를 부풀리지 못하게)."""
        now = time.monotonic()
        if now - self._log_at.get(ws, 0.0) < 1.0:
            return
        self._log_at[ws] = now
        logger.write(level, msg)

    def is_local_ws(self, ws) -> bool:
        return bool((self.active.get(ws) or {}).get("local"))

    async def _send_now(self, ws, text: str):
        """대기열이 없는 연결(시험용으로 넣은 것)에만 — 바로 보낸다."""
        try:
            await asyncio.wait_for(ws.send_text(text), SEND_TIMEOUT_S)
        except Exception:  # noqa: BLE001
            self.active.pop(ws, None)

    async def send_to(self, ws, obj: dict):
        meta = self.active.get(ws)
        if meta is not None and "q" in meta:
            self._enqueue(ws, meta, _dumps(obj))
            return
        await self._send_now(ws, _dumps(obj))

    async def broadcast(self, obj: dict, live: bool = False):
        if not self.active:
            return
        text = _dumps(obj)
        for ws, meta in list(self.active.items()):
            if "q" in meta:
                (self._set_live if live else self._enqueue)(ws, meta, text)
            else:
                await self._send_now(ws, text)

    async def close_all(self):
        for ws, meta in list(self.active.items()):
            if "q" in meta:
                self._drop(ws, meta, None)


async def _close_quietly(ws, code):
    try:
        await ws.close(code=code)
    except Exception:  # noqa: BLE001
        pass


def _json_default(o):
    """float('inf') 같은 값이 섞여도 직렬화가 통째로 실패하지 않게 한다."""
    return None


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


async def _recipes_async():
    """레시피 목록(파일 읽기)은 이벤트 루프 밖에서."""
    from . import storage
    return await asyncio.to_thread(storage.list_recipes)


manager = ConnectionManager()


async def push_state():
    """전체 스냅샷. access(조작 권한)는 로컬/원격 두 가지만 만든다(연결마다 다시 만들지 않는다)."""
    if not manager.active:
        return
    recipes = await _recipes_async()
    snap = {}
    for ws, meta in list(manager.active.items()):
        loc = bool(meta.get("local", False))
        if loc not in snap:
            snap[loc] = state.snapshot(access_local=loc, recipes=recipes)
        await manager.send_to(ws, snap[loc])


async def push_live():
    await manager.broadcast(state.live(), live=True)


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
