"""v0.4.9 — 무거운 조회 한도 · 로그 묶음 · 압축 끔 · CSV 수식 · 시작 대기 취소 · RF 확인 ·
데이터 로그 연속 · 빠른 재연결 · PLC 멈춤 · 알람 창 · 레시피 이름 바꾸기.

★ A1 · B4 의 하트비트는 PLC 쪽에서 잰다 — 별도 프로세스 시뮬레이터(test_v047 의 PlcProc)가 받은
  PC 하트비트(D01000) 쓰기 간격.
"""
import os
import csv
import json
import time
import types
import socket
import asyncio
import threading
import urllib.error
import urllib.request

import pytest

from powderald import addresses as A
from powderald import commands as C
from powderald import config as CF
from powderald import connection as CN
from powderald import device as DEV
from powderald import heavy
from powderald import logger
from powderald import logview
from powderald import paths
from powderald import plclink
from powderald import recipe as R
from powderald import storage
from powderald.datalog import DataLog
from powderald.process import ProcessRunner, IDLE, BASE_WAIT
from powderald.state import state

from conftest import FakeSim, wait_until, free_port
from test_v044 import FakeWS, wired, _pump, _recipe, _local_ws          # noqa: F401  (픽스처)
from test_v047 import PlcProc, rig, Local                                 # noqa: F401  (픽스처)

websockets = pytest.importorskip("websockets")
uvicorn = pytest.importorskip("uvicorn")


# ===================== 도우미 =====================
def _big_csv(name: str, rows: int) -> str:
    """데이터 로그 모양의 CSV(그래프가 읽는 열) — 이름은 목록 규칙(YYYYMMDD_HHMMSS_이름)."""
    path = os.path.join(paths.DATALOG_DIR, name + ".csv")
    head = ["시각", "경과 s", "장비 상태", "시퀀서 상태", "블록", "스텝", "스텝 이름", "CVG Torr",
            "MFC1 PV", "MFC1 SV", "CH1 PV ℃", "CH1 SV ℃", "CH2 PV ℃", "CH2 SV ℃"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for i in range(rows):
            w.writerow(["2026-01-01 00:00:00", f"{i * 0.5:.1f}", "공정 중", "스텝", 1 + (i // 500) % 3,
                        1 + (i // 50) % 4, "s", f"{0.01 + i % 7 * 0.001:.5f}", f"{100 + i % 5:.1f}", "100.0",
                        f"{200 + i % 3:.1f}", "200.0", f"{150 + i % 4:.1f}", "150.0"])
    return name


def _get(port, path, timeout=60):
    """(상태 코드, 본문 바이트, 걸린 s)"""
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status, r.read(), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.monotonic() - t0


async def _aget(port, path, timeout=60):
    return await asyncio.to_thread(_get, port, path, timeout)


# ===================== A1 무거운 조회 문 =====================
async def test_gate_total_two_remote_one_rejects_at_once():
    g = heavy.Gate()
    cur, peak, lock = [0], [0], threading.Lock()

    def work():
        with lock:
            cur[0] += 1
            peak[0] = max(peak[0], cur[0])
        time.sleep(0.3)
        with lock:
            cur[0] -= 1
        return 1

    assert (heavy.TOTAL, heavy.REMOTE) == (2, 1)
    first = asyncio.create_task(g.run(work, remote=True))
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    with pytest.raises(heavy.Busy):
        await g.run(work, remote=True)                  # 원격 칸이 차 있다 — 기다리지 않는다
    assert time.monotonic() - t0 < 0.05
    # 로컬은 줄을 선다(거절 없음) — 전체 동시 2 개
    res = await asyncio.gather(first, *[g.run(work) for _ in range(4)])
    assert res == [1] * 5
    assert peak[0] == 2
    assert g.remote_busy == 0


def test_result_cache_lru_and_hits():
    c = heavy.ResultCache(2)
    c.put("a", b"1")
    c.put("b", b"2")
    assert c.get("a") == b"1"
    c.put("c", b"3")                                    # b 가 가장 오래 안 쓰였다
    assert c.get("b") is None and c.get("c") == b"3" and c.hits == 2


async def test_http_remote_429_local_queues_and_chart_cache(rig, monkeypatch):
    """원격 그래프 두 개 동시 → 하나는 바로 429. 그동안 로컬은 거절 없이 받는다.
    같은 파일 두 번째는 캐시(다시 풀지 않는다)."""
    name = _big_csv("20260101_000000_캐시시험", 3000)
    logview.chart_cache.clear()
    calls = []
    orig = logview.chart

    def slow(nm, *a, **k):
        calls.append(nm)
        time.sleep(1.0)
        return orig(nm, *a, **k)
    monkeypatch.setattr(logview, "chart", slow)
    q = "/api/datalog/chart?name=" + urllib.request.quote(name)
    r1 = asyncio.create_task(_aget(rig.port, q + "&remote=1"))
    await asyncio.sleep(0.3)
    code2, body2, dt2 = await _aget(rig.port, q + "&remote=1")
    assert code2 == 429 and dt2 < 0.5, (code2, dt2)
    assert heavy.BUSY_TEXT in json.loads(body2)["error"]
    code_l, _b, _dt = await _aget(rig.port, "/api/datalog/list")          # 로컬 — 줄 서서 받는다
    assert code_l == 200
    code1, body1, _ = await r1
    assert code1 == 200 and json.loads(body1)["name"] == name
    hits0 = logview.chart_cache.hits
    code3, body3, dt3 = await _aget(rig.port, q + "&remote=1")
    assert code3 == 200 and body3 == body1 and dt3 < 0.5, dt3
    assert logview.chart_cache.hits == hits0 + 1 and calls == [name]
    # 원격 트렌드도 같은 원격 칸을 지난다
    monkeypatch.setattr(heavy.gate, "_remote", heavy.REMOTE)
    try:
        assert (await _aget(rig.port, "/api/trend?sec=60&remote=1"))[0] == 429
        assert (await _aget(rig.port, "/api/trend?sec=60"))[0] == 200            # 로컬은 문 밖
        assert (await _aget(rig.port, "/api/datalog/rows?name=" + urllib.request.quote(name)
                            + "&remote=1"))[0] == 429
        now = time.time()
        assert (await _aget(rig.port, f"/api/trend/history?t0={now - 60:.0f}&t1={now:.0f}&remote=1"))[0] == 429
    finally:
        monkeypatch.setattr(heavy.gate, "_remote", 0)


async def test_heavy_queries_keep_plc_heartbeat(rig):
    """큰 CSV 여러 개를 원격 · 로컬이 한꺼번에 조회해도 PLC 쪽 하트비트 공백이 와치독에 닿지 않는다."""
    logview.chart_cache.clear()
    names = [_big_csv(f"2026010{i}_000000_큰파일{i}", 120000) for i in range(1, 4)]
    size = sum(os.path.getsize(os.path.join(paths.DATALOG_DIR, n + ".csv")) for n in names)
    rig.plc.reset()
    await asyncio.sleep(0.6)
    t0 = time.monotonic()
    jobs = []
    for k in range(9):
        nm = names[k % 3]
        remote = "&remote=1" if k % 3 != 2 else ""
        jobs.append(_aget(rig.port, "/api/datalog/chart?name=" + urllib.request.quote(nm) + remote, 120))
    res = await asyncio.gather(*jobs)
    took = time.monotonic() - t0
    await asyncio.sleep(0.6)
    gap, trip = rig.plc.stats()
    codes = sorted(r[0] for r in res)
    print(f"\n큰 CSV {size / 1e6:.1f} MB × 9 요청 {took:.1f} s — 응답 {codes} · PLC 쪽 하트비트 최대 공백 {gap} ms")
    assert set(codes) <= {200, 429} and 200 in codes
    assert not trip and gap < 700, gap                    # v0.4.10 A2 기준


# ===================== A2 로그 묶음 =====================
def test_grouped_log_one_line_per_second_then_count(monkeypatch):
    lines = []
    monkeypatch.setattr(logger, "write", lambda lv, msg: lines.append(msg))
    CN._grouped.clear()
    for _ in range(5):
        CN.grouped_log(("ws-reject", "10.0.0.9"), "warn", "거절 시험")
    assert lines == ["거절 시험"]
    CN.grouped_log(("ws-reject", "10.0.0.8"), "warn", "다른 IP")       # IP 마다 따로
    assert lines[-1] == "다른 IP"
    CN._grouped[("ws-reject", "10.0.0.9")][0] -= 1.5                    # 1 s 지난 것으로
    CN.grouped_log(("ws-reject", "10.0.0.9"), "warn", "거절 시험")
    assert lines[-1] == "거절 시험 — 같은 일 4건 더"
    assert len(lines) == 3


def test_ws_reject_flood_logs_one_line():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from powderald.server import create_app
    lines = []
    orig = logger.write
    CN._grouped.clear()
    try:
        logger.write = lambda lv, msg: (lines.append(msg), orig(lv, msg))
        with TestClient(create_app("", single_instance=False), client=("127.0.0.1", 50000),
                        base_url="http://127.0.0.1:8201") as c:
            for _ in range(20):
                with pytest.raises(WebSocketDisconnect):
                    with c.websocket_connect("ws://127.0.0.1:8201/ws",
                                             headers={"origin": "http://evil.example"}) as ws:
                        ws.receive_json()
    finally:
        logger.write = orig
    assert sum("WebSocket 연결 거절" in x for x in lines) == 1, lines


def test_daily_housekeeping_runs_all_cleanups(monkeypatch):
    from powderald import loops
    from powderald import datalog as DL
    from powderald import trendlog as T
    called = []
    monkeypatch.setattr(logger, "_cleanup", lambda: called.append("log"))
    monkeypatch.setattr(DL, "cleanup", lambda k: called.append(("datalog", k)))
    monkeypatch.setattr(T, "cleanup", lambda k: called.append(("trend", k)))
    monkeypatch.setattr(T, "cleanup_exports", lambda k: called.append(("export", k)))
    loops.housekeeping()
    assert called[0] == "log" and {c[0] for c in called[1:]} == {"datalog", "trend", "export"}
    assert loops.HOUSEKEEP_S == 86400.0


# ===================== A3 압축 끔 =====================
async def test_ws_compression_off(rig):
    from powderald.server import uvicorn_config
    assert uvicorn_config(None, "127.0.0.1", 1).ws_per_message_deflate is False
    async with websockets.connect(f"ws://127.0.0.1:{rig.port}/ws", compression="deflate", max_size=None) as ws:
        await ws.recv()
        ext = ws.response.headers.get("Sec-WebSocket-Extensions")
    assert not ext, ext                                  # 서버가 permessage-deflate 를 받지 않았다


# ===================== A4 CSV 수식 =====================
@pytest.mark.parametrize("v,out", [
    ("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("-x", "'-x"), ("@SUM(A1)", "'@SUM(A1)"),
    ("\tq", "'\tq"), ("\rq", "'\rq"), ("-1.5", "-1.5"), ("+3", "+3"), ("abc", "abc"), ("", ""), (7, 7),
])
def test_csv_cell(v, out):
    assert logger.csv_cell(v) == out


def test_datalog_rows_guarded(cfg, monkeypatch):
    """데이터 로그 줄(스텝 이름 등 레시피에서 온 글자)도 CSV 수식 보호를 지난다."""
    state.install_config(cfg, [], "example")
    state.link = None
    state.runner = None
    dl = DataLog(state)
    dl.start("수식", R.empty_recipe("수식"), {"number": 1}, 0)
    monkeypatch.setattr(dl, "_row", lambda now: ["2026-01-01 00:00:00", "0.0", "=HYPERLINK(\"x\")", "-1.5", "@a"])
    dl.tick(0.2)
    dl.close()
    with open(dl.path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[1][2:] == ["'=HYPERLINK(\"x\")", "-1.5", "'@a"]


def test_trend_export_guarded(monkeypatch):
    from powderald import trendlog as T
    tl = T.trendlog
    n = len(T.COL_NAMES)
    monkeypatch.setattr(tl, "raw_rows", lambda t0, t1: iter([[time.time(), "=evil()", -1.5] + [None] * (n - 2)]))
    name = tl.export_csv(time.time() - 10, time.time())
    with open(os.path.join(T.export_dir(), name), encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "'=evil()" and rows[1][2] == "-1.5"


# ===================== B1 시작 대기 취소 =====================
@pytest.mark.parametrize("cmd", ["vent", "pump_stop", "all_close"])
async def test_base_wait_cancelled_by_vent_pumpstop_allclose(wired, cmd):
    lk, sim, cfg = wired
    await _pump(lk, sim)
    lk.prm_autofix = False
    sim.base_pressure = 500.0
    sim.write(A.D_PRM_BASE_PRESS, [1])                 # 베이스 압력에 닿지 않게
    assert storage.save("대기취소", _recipe("대기취소"))
    assert state.runner.select("대기취소")[0]
    ws = _local_ws()
    await C.handle_command({"cmd": "process_start"}, ws)
    assert await wait_until(lambda: state.runner.phase == BASE_WAIT, 10)
    await C.handle_command({"cmd": cmd}, ws)
    assert await wait_until(lambda: state.runner.phase == IDLE and not state.runner.busy, 1.0)
    # 대기를 다시 살려도(펌핑 시작) 공정이 저절로 시작되지 않는다
    await lk.send_command(A.CMD_PUMP_START)
    await asyncio.sleep(1.0)
    assert sim.reg[A.D_STATE] not in (A.STATE_READY, A.STATE_RUN)
    assert any("취소" in m and A.CMD_NAMES[{"vent": A.CMD_VENT, "pump_stop": A.CMD_PUMP_STOP,
                                            "all_close": A.CMD_ALL_CLOSE}[cmd]] in m for m in ws.notices())


async def test_base_wait_broken_when_pump_stops_elsewhere(wired):
    """판넬 · 다른 경로로 펌핑이 멈춰도(이 PC 의 명령이 아님) 대기를 그만둔다."""
    lk, sim, cfg = wired
    await _pump(lk, sim)
    lk.prm_autofix = False
    sim.base_pressure = 500.0
    sim.write(A.D_PRM_BASE_PRESS, [1])
    assert storage.save("대기깨짐", _recipe("대기깨짐"))
    assert state.runner.select("대기깨짐")[0]
    ws = _local_ws()
    await C.handle_command({"cmd": "process_start"}, ws)
    assert await wait_until(lambda: state.runner.phase == BASE_WAIT, 10)
    await lk.send_command(A.CMD_VENT)                   # 처리기를 거치지 않는다
    assert await wait_until(lambda: state.runner.phase == IDLE and not state.runner.busy, 5)
    assert any(("꺼졌" in m or "대기압" in m or "대기가 아닙니다" in m) for m in ws.notices()), ws.notices()


# ===================== B2 RF · O3 풀스케일 · RF 시작 조건 · RF 경고 =====================
@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 없는 장비")
def test_rf_full_scale_required(cfg):
    rec = _recipe("rf")
    rec["blocks"][0]["rf_w"] = 50.0
    rec["blocks"][0]["steps"][0]["rf"] = True
    ok = R.validate(cfg, rec)
    assert not any("rf.max_w" in e["msg"] for e in ok["errors"])
    cfg["rf"]["max_w"] = 0
    bad = R.validate(cfg, rec)
    assert any("rf.max_w" in e["msg"] for e in bad["errors"])


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 없는 장비")
def test_o3_full_scale_required(cfg):
    rec = _recipe("o3")
    rec["blocks"][0]["o3"] = 10.0
    ok = R.validate(cfg, rec)
    assert not any("o3.full" in e["msg"] for e in ok["errors"])
    cfg["o3"]["full"] = 0
    bad = R.validate(cfg, rec)
    assert any("o3.full" in e["msg"] for e in bad["errors"])


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 없는 장비")
async def test_rf_start_check(wired):
    lk, sim, cfg = wired
    await _pump(lk, sim)
    rec = _recipe("rf시작")
    rec["blocks"][0]["rf_w"] = 50.0
    rec["blocks"][0]["steps"][0]["rf"] = True
    assert storage.save("rf시작", rec)
    assert state.runner.select("rf시작")[0]
    rf = lambda: [c for c in state.runner.start_checks() if c["key"] == "rf"]   # noqa: E731
    assert rf() and rf()[0]["ok"], rf()
    sim.set_fault("rf_notready", True)
    assert await wait_until(lambda: not rf()[0]["ok"], 3)
    assert "RF 준비" in rf()[0]["detail"]
    sim.set_fault("rf_notready", False)
    # RF 를 쓰지 않는 레시피는 RF 조건을 보지 않는다
    assert storage.save("rf없음", _recipe("rf없음"))
    assert state.runner.select("rf없음")[0]
    assert not rf()


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 없는 장비")
def test_rf_never_on_in_rf_step_warns_once(cfg):
    state.install_config(cfg, [], "example")
    r = ProcessRunner(state)
    rec = _recipe("rf경고")
    b = rec["blocks"][0]
    b["rf_w"] = 50.0
    b["steps"] = [{"name": "rf", "time_ms": 1000, "valves": [], "pause_ok": True, "rf": True},
                  {"name": "쉼", "time_ms": 200, "valves": [], "pause_ok": True, "rf": False}]
    r.run = {"recipe": rec}
    s = [0] * A.STATUS_COUNT
    s[A.D_SEQ_BLOCK], s[A.D_SEQ_STATE] = 1, 4
    logs = []
    log = lambda lv, m: logs.append(m)                                            # noqa: E731
    for cyc in range(3):
        s[A.D_SEQ_BLOCK_PASS] = cyc
        s[A.D_SEQ_STEP] = 1
        r._watch_rf(s, log)
        s[A.D_SEQ_STEP] = 2
        r._watch_rf(s, log)
    assert len(logs) == 1 and "RF 가 한 번도 켜지지 않았습니다" in logs[0] and "블록 1" in logs[0]
    # RF 출력이 켜진 스텝은 경고 없음
    r2 = ProcessRunner(state)
    r2.run = {"recipe": rec}
    logs.clear()
    s[A.D_SEQ_STEP] = 1
    s[A.D_AUX_OUT] = 1 << A.AUX_RF
    r2._watch_rf(s, log)
    s[A.D_SEQ_STEP], s[A.D_AUX_OUT] = 2, 0
    r2._watch_rf(s, log)
    r2._watch_rf(None, log)
    assert not logs


# ===================== B3 데이터 로그 연속 =====================
def test_datalog_one_file_across_disconnect_and_closes_after_limit(cfg, monkeypatch):
    from powderald import loops
    from powderald import datalog as DL
    state.install_config(cfg, [], "example")
    from powderald.convert import Converters
    link = plclink.PlcLink(cfg, Converters(cfg))          # 시작하지 않은 링크 — 연결 여부만 바꿔 끼운다
    link.connected = True
    link.status = [0] * A.STATUS_COUNT
    state.link = link
    runner = types.SimpleNamespace(active_run=True, active_name="연속", active_recipe=_recipe("연속"),
                                   active_table={"number": 1}, last_result="", progress=lambda: {})
    state.runner = runner
    state.datalog = DataLog(state)
    try:
        loops._datalog_tick()
        first = state.datalog.path
        link.connected = False                          # 짧은 PLC 끊김 — 공정 구간은 이어진다
        for _ in range(3):
            state.datalog._next = 0
            loops._datalog_tick()
        link.connected = True
        state.datalog._next = 0
        loops._datalog_tick()
        assert state.datalog.path == first
        with open(first, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        down = [r for r in rows[1:] if r[2] == "PLC 끊김"]
        assert len(down) == 3
        assert all(c == "" for r in down for c in r[3:])          # 0x0000 · 0 을 지어내지 않는다
        assert len([n for n in os.listdir(paths.DATALOG_DIR) if n.endswith(".csv")]) == 1
        # 끊김이 한도를 넘으면 닫는다
        monkeypatch.setattr(DL, "DOWN_CLOSE_S", 0.05)
        link.connected = False
        state.datalog._next = 0
        loops._datalog_tick()
        time.sleep(0.1)
        state.datalog._next = 0
        loops._datalog_tick()
        assert state.datalog.fp is None and state.datalog.end_result == "기록 중단(PLC 끊김)"
    finally:
        if state.datalog:
            state.datalog.close()
        state.link = None
        state.runner = None
        state.datalog = None


async def test_runner_active_run_spans_command_to_end(wired):
    lk, sim, cfg = wired
    await _pump(lk, sim)
    assert storage.save("구간", _recipe("구간", 2))
    assert state.runner.select("구간")[0]
    assert not state.runner.active_run
    ws = _local_ws()
    await C.handle_command({"cmd": "process_start"}, ws)
    assert await wait_until(lambda: state.runner.active_run, 15)
    for _ in range(600):
        state.runner.tick(lambda *a: None)
        if not state.runner.active_run:
            break
        await asyncio.sleep(0.05)
    assert not state.runner.active_run and state.runner.last_result


# ===================== B4 빠른 재연결 =====================
class Relay:
    """서버 ↔ PLC 사이 TCP 중계 — cut(s) 동안 연결을 끊고 새 연결을 받지 않는다."""

    def __init__(self, target):
        self.target = target
        self.port = free_port()
        self.conns = []
        self.down_until = 0.0
        self.stop = False
        self.lsock = None
        self._listen()
        threading.Thread(target=self._accept, daemon=True).start()

    def _listen(self):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(8)
        s.settimeout(0.05)
        self.lsock = s

    def _accept(self):
        while not self.stop:
            if time.monotonic() < self.down_until:
                if self.lsock:
                    self.lsock.close()
                    self.lsock = None
                time.sleep(0.01)
                continue
            if self.lsock is None:
                self._listen()
            try:
                c, _ = self.lsock.accept()
            except (socket.timeout, OSError):
                continue
            u = socket.create_connection(("127.0.0.1", self.target))
            self.conns += [c, u]
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    @staticmethod
    def _pipe(a, b):
        try:
            while True:
                d = a.recv(4096)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        for x in (a, b):
            try:
                x.close()
            except OSError:
                pass

    def cut(self, s):
        self.down_until = time.monotonic() + s
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        self.conns = []

    def close(self):
        self.stop = True
        self.cut(0)


@pytest.fixture
def relay_rig(tmp_path, monkeypatch):
    plc = PlcProc(tmp_path)
    relay = Relay(plc.port)
    from powderald.server import create_app, uvicorn_config
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": False, "host": "127.0.0.1", "port": relay.port})
    cpath = tmp_path / "config.json"
    cpath.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
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
        yield types.SimpleNamespace(port=port, plc=plc, relay=relay)
    finally:
        server.should_exit = True
        th.join(10)
        relay.close()
        plc.stop()
        state.link = None
        state.runner = None
        state.sim = None


async def test_short_cut_reconnects_before_watchdog(relay_rig):
    """1.2 s 순간 끊김 — 0.25 s 간격으로 다시 붙어 PLC 쪽 하트비트 공백이 와치독(3 s)에 닿지 않는다."""
    r = relay_rig
    await asyncio.sleep(1.0)
    r.plc.reset()
    await asyncio.sleep(1.0)                              # 끊기 전 하트비트를 받아 둔다(공백이 끊김을 걸치게)
    r.relay.cut(1.2)
    t0 = time.monotonic()
    assert await wait_until(lambda: not state.link.connected, 2)
    assert await wait_until(lambda: state.link.connected and state.link.write_ok, 5)
    back = time.monotonic() - t0
    await asyncio.sleep(1.0)
    gap, trip = r.plc.stats()
    print(f"\n1.2 s 끊김 → 다시 붙기까지 {back:.2f} s · PLC 쪽 하트비트 최대 공백 {gap} ms")
    assert gap >= 1200, gap                               # 끊김을 걸친 공백을 쟀다
    assert not trip and gap < 2000, gap


def test_fast_window_uses_whole_watchdog(cfg, monkeypatch):
    """v0.4.10 A1: 빠른 재연결 창 = 와치독 전체(v0.4.9 의 2/3 에서 넓힘 — PLC 는 그때까지 트립하지 않는다)."""
    from powderald.convert import Converters
    lk = plclink.PlcLink(cfg, Converters(cfg))
    assert not lk._in_fast_window()                       # 하트비트를 쓴 적이 없다
    lk.prm_readback = {A.D_PRM_PC_WDT_MS: 3000}
    lk.last_hb_write_at = time.monotonic() - 2.1
    assert lk._in_fast_window()
    lk.last_hb_write_at = time.monotonic() - 3.1
    assert not lk._in_fast_window()
    assert plclink.FAST_RETRY_S == 0.25


# ===================== B5 PLC 하트비트 멈춤 =====================
async def test_hb_stall_blocks_commands_and_shows_in_live(wired):
    lk, sim, cfg = wired
    assert await wait_until(lambda: lk.plc_hb_ok, 5)
    assert not lk.plc_hb_stalled and lk.blocked_reason() == ""
    sim.set_fault("plc_stop", True)                       # PLC 스캔이 멈춘다(STOP) — 하트비트 그대로
    try:
        assert await wait_until(lambda: lk.plc_hb_stalled, plclink.PLC_HB_STALL_S + 3)
        assert lk.connected
        assert "하트비트 멈춤" in lk.blocked_reason()
        live = state.live()
        assert live["plc"]["hb_stalled"] is True
        # v0.4.10: send_command 는 (결과, 글) — 튜플과 비교하면 늘 참이었다. 보내지 않았으니 결과 None
        result, text = await lk.send_command(A.CMD_ALARM_ACK)
        assert result is None and "하트비트 멈춤" in text, (result, text)
    finally:
        sim.set_fault("plc_stop", False)
    assert await wait_until(lambda: not lk.plc_hb_stalled and lk.blocked_reason() == "", 5)


# ===================== B6 · B7 알람 창 · 이력 =====================
def _fake_link(s, connected=True):
    return types.SimpleNamespace(connected=connected, status=s)


def test_new_code_while_d7_held_pops_again_and_first_refresh_rule():
    s = [0] * A.STATUS_COUNT
    old = state.link
    try:
        state.link = _fake_link(s, False)
        state.refresh()
        # 연결 직후 첫 갱신: D00007 = 0 이면 이미 서 있던 알람에 창을 띄우지 않는다
        state.link = _fake_link(s)
        s[A.D_ALARM0] = 1 << A.ALM0_EMO
        n0 = state.alarm_popup_seq
        state.refresh()
        assert state.alarm_popup_seq == n0
        # 확인 전(D00007 = 1 유지) 다른 알람이 새로 선다 → 다시 띄운다
        s[A.D_ALARM_NEW] = 1
        state.refresh()
        assert state.alarm_popup_seq == n0 + 1
        s[A.D_ALARM1] = 1 << 0
        state.refresh()
        assert state.alarm_popup_seq == n0 + 2
        state.refresh()                                   # 같은 알람이 이어지면 그대로
        assert state.alarm_popup_seq == n0 + 2
        # 끊겼다 다시 붙음 — 첫 갱신에 D00007 = 1 이면 한 번
        state.link = _fake_link(s, False)
        state.refresh()
        state.link = _fake_link(s)
        state.refresh()
        assert state.alarm_popup_seq == n0 + 3
    finally:
        state.link = old
        state.alarms.clear_all()


def test_disconnect_closes_open_history_rows(monkeypatch):
    events = []
    monkeypatch.setattr(logger, "alarm_event", lambda *a: events.append(a))
    s = [0] * A.STATUS_COUNT
    old = state.link
    try:
        state.alarms.active.clear()
        state.alarms.history.clear()
        state.link = _fake_link(s)
        state.refresh()
        s[A.D_ALARM0] = 1 << A.ALM0_EMO
        state.refresh()
        v0 = state.alarms.ver
        assert state.alarms.history[0]["cleared"] == ""
        state.link = _fake_link(s, False)
        state.refresh()
        h = state.alarms.history[0]
        assert h["cleared"].startswith("해제(연결 끊김) ")
        assert state.alarms.ver > v0 and not state.alarms.active
        assert events[-1][0] == "해제(연결 끊김)"
        state.link = _fake_link(s)
        state.refresh()                                   # 다시 붙으면 새 줄 하나(앞 줄은 닫혀 있다)
        assert [x["cleared"] == "" for x in state.alarms.history[:2]] == [True, False]
    finally:
        state.link = old
        state.alarms.clear_all()


async def test_alarm_history_command_readonly_and_version():
    from powderald.connection import manager
    ws = FakeWS("192.168.10.77")
    manager.active[ws] = {"local": False}
    try:
        await C.handle_command({"cmd": "alarm_history"}, ws)
        m = [x for x in ws.sent if x.get("type") == "alarm_history"]
        assert m and m[0]["ver"] == state.alarms.ver and isinstance(m[0]["items"], list)
        assert "alarm_hist_ver" in state.live()
    finally:
        manager.active.clear()


# ===================== B8 부호 있는 16비트 =====================
def test_valve_min_signed_limit_and_raw_max(cfg):
    rules = {k: (lo, hi) for k, lo, hi, _w in CF.PRM_INT_RULES}
    assert rules["valve_min_ms"] == (0, 32767)
    assert R.effective_step_ms(100, True, 40000) == 100            # 음수로 비교 — 늘리지 않는다(래더와 같게)
    assert R.effective_step_ms(100, True, 300) == 300
    cfg["analog"]["raw_max"] = 40000
    probs = CF.validate(cfg)
    assert any("raw_max" in m and "32767" in m for _lv, m in probs)
    cfg["params"]["valve_min_ms"] = 40000
    probs = CF.validate(cfg)
    assert any("valve_min_ms" in m for _lv, m in probs)


def test_simulator_compares_valve_min_signed(cfg):
    fs = FakeSim(cfg)
    fs.sim.write(A.D_PRM_VALVE_MIN_MS, [40000])
    assert fs.sim._prm(A.D_PRM_VALVE_MIN_MS) == 40000 - 65536


# ===================== B9 히터 안정 시간 · 통신 =====================
async def test_heater_ready_uses_stable_s_and_comm(wired):
    lk, sim, cfg = wired
    hr = cfg.setdefault("process", {}).setdefault("heater_ready", {})
    hr.update({"enabled": True, "band_c": 1000.0, "stable_s": 1.0})
    for h in cfg["heaters"]:
        h["enabled"] = h["ch"] == 1
    assert storage.save("히터", _recipe("히터"))
    assert state.runner.select("히터")[0]
    heat = lambda: [c for c in state.runner.start_checks() if c["key"] == "heater"][0]   # noqa: E731
    state.runner._hr_since = {}
    state.runner.tick(lambda *a: None)
    assert not heat()["ok"] and "안정" in heat()["detail"]
    await asyncio.sleep(1.1)
    state.runner.tick(lambda *a: None)
    assert heat()["ok"], heat()
    station = int(cfg["heaters"][0].get("station") or 1)
    lk.status[A.D_TC_COMM] &= ~(1 << (station - 1))                # 그 국번 통신 없음
    assert not heat()["ok"] and "통신 없음" in heat()["detail"]


# ===================== B10 O3 끄기 예약 =====================
@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 없는 장비")
async def test_o3_off_timer_cancelled_by_start_flow(wired):
    lk, sim, cfg = wired
    await _pump(lk, sim)
    lk.prm_autofix = False
    await C.handle_command({"cmd": "manual_o3", "action": "off"})
    assert state.o3_off_task is not None
    sim.base_pressure = 500.0
    sim.write(A.D_PRM_BASE_PRESS, [1])
    assert storage.save("o3예약", _recipe("o3예약"))
    assert state.runner.select("o3예약")[0]
    ws = _local_ws()
    await C.handle_command({"cmd": "process_start"}, ws)
    assert await wait_until(lambda: state.runner.phase == BASE_WAIT, 10)
    assert state.o3_off_task is None
    state.runner.cancel_wait()
    assert await wait_until(lambda: state.runner.phase == IDLE, 2)


# ===================== C1 이름 바꾸기 · 삭제 답 =====================
async def test_rename_and_delete_reply_to_sender(wired):
    ws = _local_ws()
    assert storage.save("가", _recipe("가"))
    assert storage.save("나", _recipe("나"))
    await C.handle_command({"cmd": "recipe_rename", "name": "가", "new_name": "나"}, ws)
    rep = [m for m in ws.sent if m.get("type") == "recipe_renamed"][-1]
    assert rep == {"type": "recipe_renamed", "ok": False, "old": "가", "new": "나",
                   "why": "같은 이름이 이미 있습니다: 나"}
    assert storage.load("나")["name"] == "나"                        # 덮어쓰지 않았다
    await C.handle_command({"cmd": "recipe_rename", "name": "가", "new_name": "다"}, ws)
    rep = [m for m in ws.sent if m.get("type") == "recipe_renamed"][-1]
    assert rep["ok"] and rep["new"] == "다" and storage.exists("다") and not storage.exists("가")
    await C.handle_command({"cmd": "recipe_delete", "name": "없는것"}, ws)
    rep = [m for m in ws.sent if m.get("type") == "recipe_deleted"][-1]
    assert rep["ok"] is False and rep["name"] == "없는것" and rep["why"]
    await C.handle_command({"cmd": "recipe_delete", "name": "다"}, ws)
    rep = [m for m in ws.sent if m.get("type") == "recipe_deleted"][-1]
    assert rep == {"type": "recipe_deleted", "ok": True, "name": "다", "why": ""}
    await C.handle_command({"cmd": "recipe_rename", "name": ["x"], "new_name": 5}, ws)   # 잘못된 형
    assert [m for m in ws.sent if m.get("type") == "recipe_renamed"][-1]["ok"] is False


# ===================== C6 검증 요청 번호 =====================
@pytest.mark.parametrize("req,out", [(7, 7), (0, 0), (-1, None), ("7", None), (True, None), (2 ** 31, None)])
async def test_validate_echoes_request_number(wired, req, out):
    ws = _local_ws()
    await C.handle_command({"cmd": "recipe_validate", "recipe": _recipe("번호"), "req": req}, ws)
    m = [x for x in ws.sent if x.get("type") == "recipe_check"][-1]
    assert m["req"] == out
