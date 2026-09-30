"""v0.4.4 — 통신·명령 처리·보안.

WebSocket 출처·Host 확인 · 메시지 크기 · 명령 이름·로그 정리 / 시작 흐름 백그라운드·스냅샷 /
Modbus 요청·예외 경로 / 응답 하나 유실 때 하트비트 공백 · 공정 중 PRM 쓰기 금지 · PRM 자동 복구 /
PRM 값 검증.
"""
import os
import json
import time
import types
import asyncio
import threading

import pytest

from powderald import addresses as A
from powderald import device as DEV
from powderald import commands as C
from powderald import logger
from powderald import paths
from powderald import recipe as R
from powderald import storage
from powderald.convert import Converters
from powderald.process import ProcessRunner, IDLE, BASE_WAIT
from powderald.state import state

from conftest import wait_until, free_port

pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient          # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

WS = "ws://127.0.0.1:8201/ws"


def _app():
    from powderald.server import create_app
    return create_app("", single_instance=False)


def _client(app):
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8201")


# ===================== 1. WebSocket · HTTP 접근 =====================
@pytest.mark.parametrize("origin,host,ok", [
    (None, "127.0.0.1:8201", True),                         # 브라우저가 아닌 도구
    ("http://127.0.0.1:8201", "127.0.0.1:8201", True),      # 화면(같은 출처)
    ("http://evil.example", "127.0.0.1:8201", False),
    ("http://127.0.0.1:9999", "127.0.0.1:8201", False),     # 다른 포트
    ("null", "127.0.0.1:8201", False),
])
def test_origin_rule(origin, host, ok):
    from powderald.connection import origin_ok
    assert origin_ok(origin, host) is ok


@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1:8201", True), ("localhost:8201", True), ("[::1]:8201", True), ("192.168.10.5", True),
    ("evil.example:8201", False), ("rebind.test", False), ("", False), (None, False),
    ("127.0.0.1:80x", False),
])
def test_host_rule(host, ok):
    from powderald.connection import host_ok
    assert host_ok(host) is ok


def test_ws_other_origin_rejected_same_origin_allowed():
    with _client(_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(WS, headers={"origin": "http://evil.example"}) as ws:
                ws.receive_json()
        with c.websocket_connect(WS, headers={"origin": "http://127.0.0.1:8201"}) as ws:
            assert ws.receive_json()["type"] == "state"
        with c.websocket_connect(WS) as ws:                      # Origin 없음(도구) — 루프백
            m = ws.receive_json()
            assert m["type"] == "state" and m["access"]["local"] is True


def test_domain_host_rejected_http_and_ws():
    with _client(_app()) as c:
        assert c.get("/health", headers={"host": "rebind.example:8201"}).status_code == 403
        assert c.get("/api/datalog/list", headers={"host": "rebind.example"}).status_code == 403
        assert c.get("/health").status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("ws://rebind.example:8201/ws") as ws:
                ws.receive_json()


async def test_large_ws_message_closes_connection():
    """uvicorn ws_max_size(256 KiB)를 넘는 메시지는 연결을 닫는다(실제 uvicorn 으로)."""
    import uvicorn
    import websockets
    from powderald.server import uvicorn_config, WS_MAX_SIZE
    app = _app()
    port = free_port()
    server = uvicorn.Server(uvicorn_config(app, "127.0.0.1", port))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws", max_size=None) as ws:
            await ws.recv()
            await ws.send(json.dumps({"cmd": "recipe_validate", "recipe": {"x": "a" * (300 * 1024)}}))
            with pytest.raises(websockets.ConnectionClosed) as ei:
                for _ in range(50):
                    await asyncio.wait_for(ws.recv(), 2)
            assert ei.value.rcvd is None or ei.value.rcvd.code in (1009, 1011, 1006)
        assert WS_MAX_SIZE == 256 * 1024
    finally:
        server.should_exit = True
        th.join(5)


class FakeWS:
    def __init__(self, host="127.0.0.1"):
        self.client = types.SimpleNamespace(host=host)
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    def notices(self):
        return [m["msg"] for m in self.sent if m.get("type") == "notice"]


def _log_lines():
    d = logger.current_dir()                 # 로거가 실제로 쓰는 폴더
    lines = []
    for f in os.listdir(d) if os.path.isdir(d) else []:
        if f.endswith(".log"):
            lines += open(os.path.join(d, f), encoding="utf-8").read().splitlines()
    return lines


async def test_newline_command_name_is_one_log_line():
    """개행을 넣은 명령 이름으로 가짜 '처리됨' 줄을 만들 수 없다 — 한 줄·40자."""
    from powderald.connection import manager
    fake = "x\n2026-01-01 00:00:00 [OK] 명령 공정 시작 (번호 1) → 처리됨 · 보낸 곳 local"
    for host, local in (("127.0.0.1", True), ("192.168.10.55", False)):
        ws = FakeWS(host)
        manager.active[ws] = {"local": local}
        before = len(_log_lines())
        await C.handle_command({"cmd": fake}, ws)
        await C.handle_command({"cmd": fake}, ws)           # 1 초 안 두 번째는 로그 없음
        manager.disconnect(ws)
        new = _log_lines()[before:]
        assert len(new) == 1, new
        assert "[OK] 명령 공정 시작" not in new[0].split("] ", 1)[1][:10]
        assert not any(ln.startswith("2026-01-01 00:00:00") for ln in _log_lines())


async def test_huge_command_name_stays_small():
    """16 MB 명령 이름이 로그·작업 이름·화면 방송을 부풀리지 않는다."""
    from powderald import loops
    from powderald.connection import manager
    ws = FakeWS("192.168.10.55")
    manager.active[ws] = {"local": False}
    loops.note_work("시작 전")
    await C.handle_command({"cmd": "A" * (16 * 1024 * 1024)}, ws)
    manager.disconnect(ws)
    assert loops._work[0] == "시작 전", "원격·모르는 명령은 작업 이름을 바꾸지 않는다"
    assert max(len(x) for x in _log_lines()) < 3000
    loops.note_work("B" * 5000)
    assert len(loops.lag_status()["max_work"]) <= 41 and len(loops._work[0]) <= 41


def test_logger_strips_control_chars():
    logger.write("warn", "a\r\nb\x00c" + "z" * 5000)
    last = _log_lines()[-1]
    assert "a  b c" in last and len(last) < logger.MAX_LINE + 100


def test_ws_handler_exception_keeps_connection(monkeypatch):
    async def boom(d, ws):
        raise RuntimeError("시험용")
    monkeypatch.setitem(C._HANDLERS, "recipe_validate", boom)
    with _client(_app()) as c:
        with c.websocket_connect(WS) as ws:
            ws.receive_json()
            ws.send_json({"cmd": "recipe_validate", "recipe": {}})
            for _ in range(20):
                m = ws.receive_json()
                if m.get("type") == "notice":
                    break
            assert "처리하지 못했습니다" in m["msg"]
            ws.send_json({"cmd": "plc_recipe_read"})         # 연결이 살아 있다
            ws.receive_json()


# ===================== 공통 준비 =====================
@pytest.fixture
def wired(link):
    lk, sim, cfg = link
    state.startup_notices = []
    state.link = lk
    state.install_config(cfg, [], "example")
    state.sim = sim
    state.runner = ProcessRunner(state)
    state.datalog = None
    state.recipe_check = {}
    state.manual_unlock_until = 0.0
    try:
        yield lk, sim, cfg
    finally:
        from powderald.connection import manager
        manager.active.clear()
        state.link = None
        state.runner = None
        state.sim = None


def _recipe(name, repeat=2):
    r = R.empty_recipe(name)
    b = R.empty_block("b1")
    b["repeat"] = repeat
    b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    b["steps"] = [{"name": "s", "time_ms": 200, "valves": [], "pause_ok": True}]
    if DEV.HAS_RF:
        b["steps"][0]["rf"] = False
        b["rf_w"] = 0.0
    r["blocks"] = [b]
    return r


async def _pump(lk, sim):
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(lk.status[A.D_INTERLOCK], A.ILK_VALVE_OK), 20)
    if DEV.HAS_O3:
        await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
        assert await wait_until(lambda: A.bit(lk.status[A.D_INTERLOCK], A.ILK_O3_OK), 15)


def _local_ws():
    from powderald.connection import manager
    ws = FakeWS()
    manager.active[ws] = {"local": True}
    return ws


# ===================== 2. 시작 흐름 백그라운드 =====================
async def test_cancel_wait_handled_quickly_and_edits_blocked(wired):
    """베이스 압력 대기 중에도 같은 통로의 명령이 바로 처리된다 — 대기 취소 0.5 s 안.
    대기 중 레시피 선택·올리기·설정 저장은 거절한다."""
    from powderald.admin import admin
    lk, sim, cfg = wired
    await _pump(lk, sim)
    lk.prm_autofix = False
    sim.base_pressure = 500.0
    sim.write(A.D_PRM_BASE_PRESS, [1])                 # 베이스 압력에 닿지 않게
    assert storage.save("대기시험", _recipe("대기시험"))
    assert storage.save("다른것", _recipe("다른것"))
    assert state.runner.select("대기시험")[0]
    ws = _local_ws()
    t0 = time.monotonic()
    await C.handle_command({"cmd": "process_start"}, ws)
    assert time.monotonic() - t0 < 1.0, "process_start 가 흐름이 끝날 때까지 붙잡았다"
    assert await wait_until(lambda: state.runner.phase == BASE_WAIT, 10)

    for cmd in ({"cmd": "recipe_select", "name": "다른것"},
                {"cmd": "recipe_upload", "name": "다른것"},
                {"cmd": "recipe_save", "name": "대기시험", "recipe": _recipe("대기시험", 3)}):
        n = len(ws.notices())
        await C.handle_command(cmd, ws)
        assert any("시작 절차가 진행 중" in m for m in ws.notices()[n:]), cmd["cmd"]
    assert state.runner.recipe_name == "대기시험"
    admin.sessions[ws] = {"token": "tok", "last": time.monotonic()}
    n = len(ws.notices())
    await C.handle_command({"cmd": "config_save", "token": "tok",
                            "edits": {"params.valve_min_ms": 150}}, ws)
    assert any("시작 절차가 진행 중" in m for m in ws.notices()[n:])
    assert not os.path.exists(paths.DEFAULT_CONFIG_PATH)

    t0 = time.monotonic()
    await C.handle_command({"cmd": "process_cancel_wait"}, ws)
    assert await wait_until(lambda: state.runner.phase == IDLE and not state.runner.busy, 0.5)
    print(f"\n대기 취소 처리 {int((time.monotonic() - t0) * 1000)} ms")
    assert any("취소" in m for m in ws.notices())
    assert sim.reg[A.D_STATE] != A.STATE_RUN


async def test_records_use_start_snapshot(wired):
    """시작한 뒤 다른 레시피를 골라도 진행 표시·끝 기록은 시작 때 스냅샷."""
    from powderald import loops
    from powderald.datalog import DataLog
    lk, sim, cfg = wired
    await _pump(lk, sim)
    assert storage.save("스냅A", _recipe("스냅A", 3))
    assert storage.save("스냅B", _recipe("스냅B", 1))
    assert state.runner.select("스냅A")[0]
    state.datalog = DataLog(state)
    ok, msg = await state.runner.start(_log, _notice)
    assert ok, msg
    assert await wait_until(lambda: lk.status[A.D_STATE] in (A.STATE_READY, A.STATE_RUN), 5)
    assert state.runner.select("스냅B")[0]            # 공정 중에 다른 레시피를 고른다
    seen = []
    for _ in range(400):
        state.refresh()
        prog = state.runner.progress()
        if prog.get("running"):
            assert prog["recipe"] == "스냅A" and prog["number"] == R.recipe_number(_recipe("스냅A", 3))
        state.runner.tick(lambda lvl, m: seen.append(m))
        loops._datalog_tick()
        if seen:
            break
        await asyncio.sleep(0.05)
    assert seen and "스냅A" in seen[0], seen
    state.datalog.close()
    meta = json.load(open(os.path.join(paths.DATALOG_DIR, state.datalog.name + ".recipe.json"),
                          encoding="utf-8"))
    assert meta["recipe"]["name"] == "스냅A"


async def test_start_refused_when_plc_table_changed_before_command(wired):
    """명령 1 직전에 PLC 표(합계·통과·번호)가 올린 것과 다르면 보내지 않는다."""
    lk, sim, cfg = wired
    await _pump(lk, sim)
    lk.prm_autofix = False
    assert storage.save("표바뀜", _recipe("표바뀜"))
    assert state.runner.select("표바뀜")[0]
    sim.write(A.D_PRM_BASE_PRESS, [1])
    task = asyncio.create_task(state.runner.start(_log, _notice))
    assert await wait_until(lambda: state.runner.phase == BASE_WAIT, 10)
    sim.write(A.D_RCP_NO, [(sim.reg[A.D_RCP_NO] + 1) & 0xFFFF])     # 다른 곳에서 표를 건드림
    await asyncio.sleep(1.3)                                        # PLC 가 표를 다시 검사
    sim.write(A.D_PRM_BASE_PRESS, [16000])                          # 이제 베이스 압력 도달
    ok, msg = await asyncio.wait_for(task, 20)
    assert not ok and "다릅니다" in msg
    assert sim.reg[A.D_STATE] != A.STATE_RUN


async def test_runner_returns_to_idle_after_unexpected_error(wired, monkeypatch):
    lk, sim, cfg = wired
    await _pump(lk, sim)
    assert storage.save("예외", _recipe("예외"))
    assert state.runner.select("예외")[0]

    async def broken(words, checksum):
        raise AttributeError("'NoneType' object has no attribute 'write'")
    monkeypatch.setattr(lk, "upload_recipe", broken)
    ok, msg = await state.runner.start(_log, _notice)
    assert not ok and "AttributeError" in msg
    assert state.runner.phase == IDLE and state.runner.message == ""
    monkeypatch.undo()
    ok, msg = await state.runner.start(_log, _notice)
    assert ok, f"다음 시작이 막혔다: {msg}"
    await lk.send_command(A.CMD_ABORT)


# ===================== 3. Modbus 요청·예외 경로 =====================
class DropProxy:
    """Modbus TCP 프록시 — 응답마다 delay 를 주고, drop_next 면 응답 하나를 버린다."""

    def __init__(self, target_port: int, delay: float = 0.02):
        self.target = target_port
        self.delay = delay
        self.drop_next = False
        self.dropped = 0
        self.server = None
        self.port = 0

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()

    async def _client(self, cr, cw):
        try:
            ur, uw = await asyncio.open_connection("127.0.0.1", self.target)
        except OSError:
            cw.close()
            return

        async def up():
            try:
                while True:
                    data = await cr.read(4096)
                    if not data:
                        break
                    uw.write(data)
                    await uw.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                uw.close()

        async def down():
            try:
                while True:
                    head = await ur.readexactly(7)
                    body = await ur.readexactly(int.from_bytes(head[4:6], "big") - 1)
                    if self.drop_next:
                        self.drop_next = False
                        self.dropped += 1
                        continue
                    await asyncio.sleep(self.delay)
                    cw.write(head + body)
                    await cw.drain()
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                pass
            finally:
                cw.close()

        await asyncio.gather(up(), down())


async def test_queued_request_after_timeout_is_modbus_error(sim):
    """앞 요청이 시간 초과로 소켓을 버리면, 잠금을 기다리던 요청은 AttributeError 가 아니라
    ModbusError 로 끝난다."""
    from powderald.modbus import ModbusClient, ModbusError, ModbusTimeout
    s, port, _cfg = sim
    px = DropProxy(port, 0.0)
    await px.start()
    cl = ModbusClient("127.0.0.1", px.port, 1, 300)
    await cl.connect()
    try:
        px.drop_next = True
        r1, r2 = await asyncio.gather(cl.read_holding(0, 1), cl.read_holding(0, 1),
                                      return_exceptions=True)
        assert isinstance(r1, ModbusTimeout), r1
        assert isinstance(r2, ModbusError) and not isinstance(r2, AttributeError), repr(r2)
    finally:
        await cl.close()
        await px.stop()


async def test_error_after_command_number_says_maybe_received(link, monkeypatch):
    from powderald.modbus import ModbusTimeout
    lk, s, _cfg = link
    orig = lk.client.read_holding

    async def rh(addr, count):
        if addr == A.D_ACK_NO:
            raise ModbusTimeout("응답 시간 초과")
        return await orig(addr, count)
    monkeypatch.setattr(lk.client, "read_holding", rh)
    r, text = await lk.send_command(A.CMD_ALARM_ACK)
    assert r is None and "받았을 수 있습니다" in text


# ===================== 4. 응답 하나 유실 · PRM =====================
async def test_one_lost_response_heartbeat_gap_and_no_prm_writes_in_process(sim):
    """PLC 앞 프록시(응답 지연 20 ms)가 응답 하나를 버려도 PLC 쪽에서 본 하트비트 최대 간격이
    timeout_ms + heartbeat_ms + 300 ms 안이고 공정이 계속된다. 공정 중 재연결 때 PRM 쓰기 0건,
    공정이 끝나면 PRM 불일치를 스스로 맞춘다."""
    from powderald.plclink import PlcLink
    s, port, cfg = sim
    px = DropProxy(port, 0.02)
    await px.start()
    cfg["plc"].update({"simulate": False, "host": "127.0.0.1", "port": px.port})
    events = []
    lk = PlcLink(cfg, Converters(cfg), on_event=lambda lv, m: events.append((lv, m)))
    lk.start()
    try:
        assert await wait_until(lambda: lk.connected and lk.write_ok, 5)
        await asyncio.sleep(0.8)
        # 공정을 돌린다(PLC 쪽) — 몇 분짜리
        tbl = R.to_plc_words(cfg, Converters(cfg), _recipe("유실", 5000))
        s.write(A.RCP_SUM_BASE, tbl["words"])
        assert s._process_start() == A.RESULT_OK
        assert await wait_until(lambda: lk.status[A.D_STATE] in (A.STATE_READY, A.STATE_RUN), 3)
        # PLC 쪽 PRM 하나를 설정과 다르게 — 공정 중 재연결 때 쓰면 안 된다
        want_vmin = cfg["params"]["valve_min_ms"]
        s.write(A.D_PRM_VALVE_MIN_MS, [want_vmin + 50])

        hb, prm_writes = [], []
        orig = s.write

        def spy(addr, values):
            if addr == A.D_PC_HB:
                hb.append(time.monotonic())
            elif A.D_PRM_PC_WDT_MS <= addr <= A.D_PRM_O3_MAX:
                prm_writes.append(addr)
            return orig(addr, values)
        s.write = spy
        await asyncio.sleep(1.2)
        px.drop_next = True
        assert await wait_until(lambda: px.dropped == 1, 3)
        await asyncio.sleep(4.0)
        gaps = [b - a for a, b in zip(hb, hb[1:])]
        limit = (cfg["plc"]["timeout_ms"] + cfg["plc"]["heartbeat_ms"] + 300) / 1000
        print(f"\n응답 하나 유실 - PLC 쪽 하트비트 최대 간격 {max(gaps) * 1000:.0f} ms (한계 {limit * 1000:.0f} ms)")
        assert max(gaps) <= limit, f"{max(gaps):.2f} s"
        assert s.running, s.end_reason
        assert not (s.reg[A.D_ALARM0] >> A.ALM0_PC_LINK) & 1
        assert any("통신 끊김" in m for _l, m in events), "재연결이 일어나지 않았다"
        assert prm_writes == [], f"공정 중 재연결 때 PRM 을 썼다: {prm_writes}"
        assert any("공정 중이라 PLC 파라미터를 쓰지 않았습니다" in m for _l, m in events)
        assert s.reg[A.D_PRM_VALVE_MIN_MS] == want_vmin + 50

        # 공정이 끝나 대기가 되면 스스로 맞춘다
        s._process_end("시험 종료", aborted=True)
        assert await wait_until(lambda: s.reg[A.D_PRM_VALVE_MIN_MS] == want_vmin, 4), \
            "대기 중 PRM 불일치를 맞추지 않았다"
        # 공정 중 미뤄 둔 쓰기는 그렇게 알린다('PLC 가 다시 시작됐을 수 있다'가 아니라)
        assert await wait_until(
            lambda: any("공정 중 미뤄 둔 PLC 파라미터를 썼습니다" in m for _l, m in events), 2), events
        assert not any("다시 시작됐거나" in m for _l, m in events)
    finally:
        await lk.stop()
        await px.stop()


async def test_idle_prm_drift_is_restored(link):
    """PLC 가 다시 시작돼 0 이던 PRM 이 기본값으로 바뀐 경우처럼, 대기 중 불일치는 1 s 되읽기가 맞춘다."""
    lk, s, cfg = link
    want = s.reg[A.D_PRM_VALVE_MIN_MS]
    s.write(A.D_PRM_VALVE_MIN_MS, [(want + 111) & 0xFFFF])
    assert await wait_until(lambda: s.reg[A.D_PRM_VALVE_MIN_MS] == want, 4)


# ===================== 5. PRM 값 검증 =====================
def _errs(cfg):
    from powderald import config as Cf
    return [m for lv, m in Cf.validate(cfg) if lv == "err"]


def test_example_config_passes_prm_rules(cfg):
    from powderald.config import prm_problems
    assert prm_problems(cfg) == []
    assert not any("params." in m for m in _errs(cfg))


@pytest.mark.parametrize("key,val,word", [
    ("pc_wdt_ms", 0, "pc_wdt_ms"), ("vent_timeout_s", 0, "vent_timeout_s"),
    ("pump_timeout_s", 0, "pump_timeout_s"), ("mfc_timeout_s", 0, "mfc_timeout_s"),
    ("mfc_stable_s", -1, "mfc_stable_s"), ("valve_min_ms", -5, "valve_min_ms"),
    ("pump_timeout_s", 7000, "pump_timeout_s"), ("pc_wdt_ms", 70000, "pc_wdt_ms"),
    ("valve_min_ms", 1.5, "정수"),
])
def test_bad_prm_rejected(cfg, key, val, word):
    cfg["params"][key] = val
    assert any(word in m for m in _errs(cfg)), _errs(cfg)


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
@pytest.mark.parametrize("key", ["rf_max_w", "rf_ref_max_w", "rf_p_max_torr"])
def test_rf_prm_required(cfg, key):
    cfg["params"][key] = None
    assert any(key in m for m in _errs(cfg))
    cfg["params"][key] = 0
    assert any(key in m for m in _errs(cfg))


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_o3_max_required(cfg):
    cfg["params"]["o3_max"] = 0
    assert any("o3_max" in m for m in _errs(cfg))


def test_worst_gap_rule(cfg):
    cfg["plc"]["timeout_ms"] = 2000
    cfg["plc"]["heartbeat_ms"] = 500
    cfg["params"]["pc_wdt_ms"] = 3000
    assert any("최악 하트비트 공백" in m for m in _errs(cfg))


def test_settings_save_rejects_bad_prm_and_overflow(cfg):
    from powderald import settings as S
    for edits, word in (({"params.pc_wdt_ms": 0}, "pc_wdt_ms"),
                        ({"params.mfc_stable_s": -1}, "mfc_stable_s"),
                        ({"params.valve_min_ms": "1e400"}, "숫자가 아닙니다")):
        res = S.prepare(cfg, edits)
        assert not res["ok"] and any(word in e for e in res["errors"]), res["errors"]
    key = "params.rf_ref_max_w" if DEV.HAS_RF else "params.o3_max"
    res = S.prepare(cfg, {key: ""})
    assert not res["ok"]


async def test_link_does_not_write_invalid_prm(sim):
    """설정의 PRM 이 규칙에 어긋나면 PLC 에 아무것도 쓰지 않는다(0 이 타이머로 쓰이지 않게)."""
    from powderald.plclink import PlcLink
    s, port, cfg = sim
    cfg["params"]["pc_wdt_ms"] = 0
    events = []
    lk = PlcLink(cfg, Converters(cfg), on_event=lambda lv, m: events.append(m))
    lk.start()
    try:
        assert await wait_until(lambda: lk.connected, 5)
        await asyncio.sleep(0.5)
        assert s.reg[A.D_PRM_PC_WDT_MS] != 0
        assert any("올바르지 않아 PLC 에 쓰지 않았습니다" in m for m in events)
    finally:
        await lk.stop()


async def _log(msg, level="info"):
    pass


async def _notice(msg, level="info", ws=None):
    pass
