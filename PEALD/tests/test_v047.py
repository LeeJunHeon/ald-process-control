"""v0.4.7 — 원격 접속 한도 · 느린 화면 분리.

★ 하트비트는 PLC 쪽에서 잰다 — 별도 프로세스 시뮬레이터가 받은 하트비트(D01000) 쓰기 시각
  (내장 시뮬레이터는 서버와 같은 루프에서 돌아 루프가 막히면 같이 막힌다).
★ 원격 판정은 시험에서만 바꿔 끼운다 — 주소에 ?remote=1 을 붙인 연결을 원격으로 본다
  (connection.is_local 교체). 실제 판정 규칙은 그대로다.
"""
import os
import sys
import json
import time
import socket
import asyncio
import threading
import subprocess

import pytest

from peald import addresses as A
from peald import connection as CN
from peald import device as DEV
from peald import loops
from peald import recipe as R
from peald import paths
from peald.state import state

from conftest import free_port

websockets = pytest.importorskip("websockets")
uvicorn = pytest.importorskip("uvicorn")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SIM_CODE = r'''
import os, sys, time, asyncio
from peald import config as C, simulator as S, addresses as A
port, flag = int(sys.argv[1]), sys.argv[2]
hb = []
async def main():
    cfg, _p, _s = C.load("")
    sim = S.PlcSim(cfg, 5)
    orig = sim.write
    def spy(addr, values):
        if addr == A.D_PC_HB:
            hb.append(time.monotonic())
        return orig(addr, values)
    sim.write = spy
    srv = S.SimServer(sim, "127.0.0.1", port)
    assert await srv.start()
    print("READY", flush=True)
    while True:
        if os.path.exists(flag):
            os.remove(flag)
            hb.clear()
            sim.reg[A.D_ALARM0] = 0
            sim.reg[A.D_ALARM_NEW] = 0
            print("RESET", flush=True)
        gaps = [b - a for a, b in zip(hb, hb[1:])]
        if hb:
            gaps.append(time.monotonic() - hb[-1])
        print("STAT %d %d %d" % (int(max(gaps) * 1000) if gaps else 0,
                                 (sim.reg[A.D_ALARM0] >> A.ALM0_PC_LINK) & 1, len(hb)), flush=True)
        await asyncio.sleep(0.25)
asyncio.run(main())
'''


class PlcProc:
    """별도 프로세스 시뮬레이터 + 줄 읽기."""

    def __init__(self, tmp):
        self.port = free_port()
        self.flag = str(tmp / "reset.flag")
        self.lines = []
        self.p = subprocess.Popen([sys.executable, "-c", SIM_CODE, str(self.port), self.flag], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read, daemon=True).start()
        end = time.monotonic() + 15
        while "READY" not in self.lines and time.monotonic() < end:
            time.sleep(0.05)
        assert "READY" in self.lines, self.lines[-5:]

    def _read(self):
        for ln in self.p.stdout:
            self.lines.append(ln.strip())

    def reset(self):
        open(self.flag, "w").close()
        end = time.monotonic() + 3
        while "RESET" not in self.lines and time.monotonic() < end:
            time.sleep(0.05)
        self.lines.clear()

    def stats(self):
        """RESET 뒤 (최대 간격 ms, PC 통신 끊김 있었는가)."""
        st = [x.split() for x in self.lines if x.startswith("STAT")]
        return (max([int(x[1]) for x in st] or [0]), any(x[2] == "1" for x in st))

    def stop(self):
        self.p.kill()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """별도 프로세스 PLC + 실제 서버(uvicorn). ?remote=1 연결은 원격."""
    plc = PlcProc(tmp_path)
    from peald.server import create_app, uvicorn_config
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": False, "host": "127.0.0.1", "port": plc.port})
    cpath = tmp_path / "config.json"
    cpath.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    orig = CN.is_local
    monkeypatch.setattr(CN, "is_local",
                        lambda ws: ws.query_params.get("remote") != "1" and orig(ws))
    app = create_app(str(cpath), single_instance=False)
    port = free_port()
    server = uvicorn.Server(uvicorn_config(app, "127.0.0.1", port))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    end = time.monotonic() + 15
    while not (server.started and state.link and state.link.connected and state.link.write_ok):
        assert time.monotonic() < end, "서버·PLC 연결이 서지 않았다"
        time.sleep(0.05)
    try:
        yield type("Rig", (), {"port": port, "plc": plc})
    finally:
        server.should_exit = True
        th.join(10)
        plc.stop()
        state.link = None
        state.runner = None
        state.sim = None


class Local:
    """이 PC 화면 흉내 — live 를 세고, 명령 답 시간을 잰다."""

    def __init__(self, port):
        self.port = port
        self.live = []
        self.msgs = []

    async def start(self):
        self.ws = await websockets.connect(f"ws://127.0.0.1:{self.port}/ws", max_size=None)
        self.task = asyncio.create_task(self._rd())

    async def _rd(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                now = time.monotonic()
                if m.get("type") == "live":
                    self.live.append(now)
                self.msgs.append((now, m))
        except Exception:  # noqa: BLE001
            pass

    def per_second(self, t0, t1):
        out, t = [], t0
        while t + 1 <= t1:
            out.append(sum(1 for x in self.live if t <= x < t + 1))
            t += 1
        return out

    async def ask(self, cmd, kind, timeout=3.0, **kw):
        """명령을 보내고 kind 답(또는 notice)이 올 때까지 걸린 시간."""
        n = len(self.msgs)
        t0 = time.monotonic()
        await self.ws.send(json.dumps({"cmd": cmd, **kw}))
        while time.monotonic() - t0 < timeout:
            if any(m.get("type") == kind for _t, m in self.msgs[n:]):
                return time.monotonic() - t0
            await asyncio.sleep(0.01)
        return float("inf")

    async def close(self):
        await self.ws.close()
        self.task.cancel()


def max_recipe(cmd="recipe_validate", fill=None):
    """가장 큰 올바른 레시피 — 스텝 100 · 블록 10 · 그룹 5 · 모든 글자 최대 · 한글."""
    blocks = []
    for bi in range(R.BLOCK_MAX):
        steps = []
        for si in range(R.STEP_MAX // R.BLOCK_MAX):
            # 열 수 있는 밸브 모두 — 전구체와 반응물은 함께 열 수 없어 반응물 쪽은 뺀다
            vs = [v for v in DEV.RECIPE_VALVES if v not in DEV.REACTANT_TAGS]
            st = {"name": "가" * R.LABEL_MAX, "time_ms": R.STEP_MS_MAX, "valves": vs, "pause_ok": True}
            if DEV.HAS_RF:
                st["rf"] = True
            steps.append(st)
        b = {"name": "나" * R.LABEL_MAX, "repeat": R.BLOCK_REPEAT_MAX, "mfc_sccm": [0.0] * DEV.MFC_COUNT,
             "steps": steps}
        if DEV.HAS_PCV:
            b["pcv_pct"] = 100.0
        if DEV.HAS_RF:
            b["rf_w"] = 0.0
        if DEV.HAS_O3:
            b["o3"] = 0.0
        blocks.append(b)
    name = "다" * R.NAME_MAX
    rec = {"format": DEV.RECIPE_FORMAT, "name": name, "memo": fill or "라" * R.MEMO_MAX,
           "created": "2026-10-01 00:00:00", "modified": "2026-10-01 00:00:00", "blocks": blocks,
           "groups": [{"from_block": 2 * i + 1, "to_block": 2 * i + 2, "repeat": R.GROUP_REPEAT_MAX}
                      for i in range(R.GROUP_MAX)]}
    d = {"cmd": cmd, "recipe": rec}
    if cmd == "recipe_save":
        d["name"] = name
    return d


def size_of(d) -> int:
    return len(json.dumps(d, ensure_ascii=False).encode("utf-8"))


def allowed_max_msg():
    """크기 상한 바로 아래까지 채운 recipe_validate(검증은 '메모가 너무 깁니다' 오류로 끝나도 처리 비용은 같다)."""
    from peald.server import WS_MAX_SIZE
    d = max_recipe()
    room = WS_MAX_SIZE - size_of(d) - 512
    d["recipe"]["memo"] = "라" * (R.MEMO_MAX + room // 3)
    assert size_of(d) < WS_MAX_SIZE
    return json.dumps(d, ensure_ascii=False)


# ===================== 크기 =====================
def test_ws_max_size_is_about_twice_the_largest_valid_recipe():
    from peald.server import WS_MAX_SIZE
    d = max_recipe("recipe_save")
    assert R.ok(R.validate({"params": {}, "mfc": [{"full_scale_sccm": 1000}] * DEV.MFC_COUNT}, d["recipe"]))
    big = size_of(d)
    print(f"가장 큰 올바른 recipe_save 명령 {big} 바이트({big / 1024:.1f} KiB) · 상한 {WS_MAX_SIZE // 1024} KiB")
    assert 1.6 * big <= WS_MAX_SIZE <= 2.6 * big, (big, WS_MAX_SIZE)


@pytest.mark.parametrize("key,limit", [("name", R.NAME_MAX), ("memo", R.MEMO_MAX)])
def test_text_length_limits(key, limit):
    d = max_recipe()["recipe"]
    d[key] = "가" * (limit + 1)
    errs = R.validate({"params": {}, "mfc": []}, d)["errors"]
    assert any("너무 깁니다" in e["msg"] for e in errs), errs


def test_block_and_step_name_limits():
    d = max_recipe()["recipe"]
    d["blocks"][0]["name"] = "가" * (R.LABEL_MAX + 1)
    d["blocks"][1]["steps"][0]["name"] = "가" * (R.LABEL_MAX + 1)
    errs = R.validate({"params": {}, "mfc": []}, d)["errors"]
    assert sum("너무 깁니다" in e["msg"] for e in errs) == 2, errs


async def test_largest_valid_recipe_passes_and_oversize_closes(rig):
    from peald.server import WS_MAX_SIZE
    loc = Local(rig.port)
    await loc.start()
    await asyncio.sleep(0.5)
    d = max_recipe("recipe_save")
    t = await loc.ask("recipe_save", "recipe_saved", name=d["name"], recipe=d["recipe"])
    got = [m for _t, m in loc.msgs if m.get("type") == "recipe_saved"]
    assert t < 3 and got[-1]["ok"], got
    await loc.close()
    async with websockets.connect(f"ws://127.0.0.1:{rig.port}/ws", max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"cmd": "recipe_validate", "recipe": {"memo": "a" * (WS_MAX_SIZE + 10)}}))
        with pytest.raises(websockets.ConnectionClosed) as ei:
            for _ in range(50):
                await asyncio.wait_for(ws.recv(), 2)
        assert ei.value.rcvd is None or ei.value.rcvd.code in (1009, 1006)


# ===================== 원격이 쏟아붓는 검증 요청 =====================
async def _flood(port, msg, until):
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?remote=1", max_size=None,
                                  compression=None) as ws:
        async def drain():
            try:
                async for _ in ws:
                    pass
            except Exception:  # noqa: BLE001
                pass
        dt = asyncio.create_task(drain())
        n = 0
        try:
            while time.monotonic() < until:
                await ws.send(msg)
                n += 1
                await asyncio.sleep(0)
        except websockets.ConnectionClosed:
            pass
        dt.cancel()
        return n


@pytest.mark.parametrize("remotes", [1, 3])
async def test_remote_flood_keeps_heartbeat_and_local_screen(rig, remotes):
    """원격 1개 · 3개가 가장 큰 허용 크기의 recipe_validate 를 쉬지 않고 — PLC 쪽 하트비트 간격 < 1000 ms,
    PC 통신 끊김 없음, 로컬 live 초당 4개 이상, 로컬 명령 답 1 s 안, 멈춘 뒤 루프 지연 경고 없음."""
    loc = Local(rig.port)
    await loc.start()
    await asyncio.sleep(1.0)
    rig.plc.reset()
    msg = allowed_max_msg()
    t0 = time.monotonic()
    until = t0 + 6
    flood = asyncio.gather(*[_flood(rig.port, msg, until) for _ in range(remotes)])
    answers = []
    while time.monotonic() < until:
        await asyncio.sleep(0.8)
        answers.append(await loc.ask("recipe_load", "notice", name="없는이름"))
    sent = await flood
    t1 = time.monotonic()
    await asyncio.sleep(2.5)
    gap, pclink = rig.plc.stats()
    per = loc.per_second(t0, t1 + 2)
    lag_after = [ms for t, ms, _w in loops.lag["hist"] if t > t1 + 0.5 and ms > 500]
    await loc.close()
    assert sum(sent) > 50, sent
    assert gap < 1000, f"PLC 쪽 하트비트 최대 간격 {gap} ms"
    assert not pclink, "PC 통신 끊김"
    assert min(per) >= 4, per
    assert max(answers) < 1.0, answers
    assert not lag_after, lag_after


# ===================== 읽지 않는 원격 =====================
def _nonreader(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.connect(("127.0.0.1", port))
    s.sendall((f"GET /ws?remote=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(1)
    return s


async def test_nonreading_remotes_do_not_stall_local(rig, monkeypatch):
    monkeypatch.setattr(CN, "SEND_TIMEOUT_S", 1.0)
    loc = Local(rig.port)
    await loc.start()
    await asyncio.sleep(1.0)
    socks = [_nonreader(rig.port) for _ in range(3)]
    try:
        await asyncio.sleep(0.5)
        assert CN.manager.remote_count() == 3
        # 읽지 않는 연결의 버퍼를 빨리 채운다 — 상태 스냅샷을 여러 번
        t0 = time.monotonic()
        waits = []
        for _ in range(8):
            waits.append(await loc.ask("plc_recipe_read", "state"))
            await asyncio.sleep(0.5)
        t1 = time.monotonic()
        per = loc.per_second(t0, t1)
        end = time.monotonic() + 15
        while CN.manager.remote_count() and time.monotonic() < end:
            await asyncio.sleep(0.2)
        assert min(per) >= 4, per
        assert max(waits) < 1.0, waits
        assert CN.manager.remote_count() == 0, "읽지 않는 연결이 닫히지 않았다"
        log = open(os.path.join(paths.LOGS_DIR, os.listdir(paths.LOGS_DIR)[0]), encoding="utf-8").read()
        assert "화면 응답 없음 — 연결을 끊었습니다" in log
    finally:
        for s in socks:
            s.close()
        await loc.close()


# ===================== 속도 · 연결 수 · 검증 합치기 =====================
async def test_rate_limit_drops_then_kicks(rig, monkeypatch):
    monkeypatch.setattr(CN, "RATE_KICK_S", 1.5)
    async with websockets.connect(f"ws://127.0.0.1:{rig.port}/ws?remote=1", max_size=None) as ws:
        await asyncio.sleep(0.3)
        got = []

        async def rd():
            try:
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("type") == "notice" and "열 수 없습니다" in m.get("msg", ""):
                        got.append(m)
            except websockets.ConnectionClosed as e:
                got.append(("closed", e.rcvd.code if e.rcvd else None))
        rt = asyncio.create_task(rd())
        for _ in range(40):                            # 순간 40개 — 순간 한도(10) 뒤는 버린다
            await ws.send(json.dumps({"cmd": "recipe_load", "name": "없는이름"}))
        await asyncio.sleep(0.8)
        answered = sum(1 for x in got if isinstance(x, dict))
        assert CN.RATE_BURST <= answered <= CN.RATE_BURST + 6, answered
        try:                                           # 계속 넘치면 1008 로 닫힌다
            end = time.monotonic() + 4
            while time.monotonic() < end:
                await ws.send(json.dumps({"cmd": "ping"}))
                await asyncio.sleep(0.01)
        except websockets.ConnectionClosed:
            pass
        await asyncio.wait_for(rt, 5)
        assert ("closed", 1008) in got, got[-3:]


async def test_remote_connection_count_limit(rig):
    conns = []
    try:
        for _ in range(CN.REMOTE_MAX):
            ws = await websockets.connect(f"ws://127.0.0.1:{rig.port}/ws?remote=1", max_size=None)
            await ws.recv()
            conns.append(ws)
        extra = await websockets.connect(f"ws://127.0.0.1:{rig.port}/ws?remote=1", max_size=None)
        with pytest.raises(websockets.ConnectionClosed) as ei:
            for _ in range(10):
                await asyncio.wait_for(extra.recv(), 2)
        assert ei.value.rcvd and ei.value.rcvd.code == 1013
        # 로컬은 원격 수와 상관없이 들어온다
        loc = Local(rig.port)
        await loc.start()
        assert await loc.ask("recipe_load", "notice", name="없는이름") < 1.0
        await loc.close()
    finally:
        for ws in conns:
            await ws.close()


async def test_validate_requests_are_coalesced(rig, monkeypatch):
    from peald import commands as C
    slow = C._check_payload

    def slower(cfg, recipe):
        time.sleep(0.3)
        return slow(cfg, recipe)
    monkeypatch.setattr(C, "_check_payload", slower)
    loc = Local(rig.port)
    await loc.start()
    await asyncio.sleep(0.5)
    names = [f"합치기{i}" for i in range(10)]
    for nm in names:
        d = max_recipe()
        d["recipe"]["name"] = nm
        await loc.ws.send(json.dumps(d, ensure_ascii=False))
    await asyncio.sleep(2.0)
    checks = [m for _t, m in loc.msgs if m.get("type") == "recipe_check"]
    await loc.close()
    assert 1 <= len(checks) <= 2, len(checks)          # 처음 것(돌던 것) + 가장 최근 것
    last = max_recipe()["recipe"]
    last["name"] = names[-1]
    assert checks[-1]["summary"]["number"] == R.recipe_number(last)


# ===================== D-1 알람 번호 =====================
def test_alarm_popup_seq_counts_each_new_alarm():
    import types
    s = [0] * A.STATUS_COUNT
    old = state.link
    state.link = types.SimpleNamespace(connected=True, status=s)
    try:
        state.alarms.clear_all()
        state._last_new_alarm = 0
        n0 = state.alarm_popup_seq
        for _ in range(2):                             # 비상정지 → 리셋 → 다시 비상정지
            s[A.D_ALARM0] = 1 << A.ALM0_EMO
            s[A.D_ALARM_NEW] = 1
            state.refresh()
            state.refresh()                            # 같은 알람이 이어지는 동안은 한 번만
            s[A.D_ALARM0] = 0
            s[A.D_ALARM_NEW] = 0
            state.refresh()
        assert state.alarm_popup_seq == n0 + 2
    finally:
        state.link = old
