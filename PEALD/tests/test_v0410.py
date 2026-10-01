"""v0.4.10 — 블랙홀 끊김 재연결 · 큰 CSV 읽기 · PLC 멈춤 기록 · 끝 판정 · 시뮬레이터 래더 맞춤.

★ A1 · A2 의 하트비트는 PLC 쪽에서 잰다 — 별도 프로세스 시뮬레이터(test_v047 의 PlcProc)가 받은
  PC 하트비트(D01000) 쓰기 간격.
★ C 는 가짜 시계로 tick(= 래더 한 스캔) 단위로 돌린다. 스캔 순서: 물리 → 입력 → P25 → P30 → P35 →
  P40 → P45 → P50 → P60 → P70. 알람 워드는 내부 래치이고 D00005 · D00006 · D00007 은 P35 에서만 바뀐다.
"""
import os
import csv
import json
import math
import time
import types
import socket
import asyncio
import threading
import urllib.request

import pytest

from peald import addresses as A
from peald import commands as C
from peald import connection as CN
from peald import device as DEV
from peald import logger
from peald import logview
from peald import loops
from peald import paths
from peald import plclink
from peald import recipe as R
from peald import storage
from peald.convert import Converters
from peald.datalog import DataLog
from peald.process import ProcessRunner
from peald.simulator import AO_PCV, AO_O3, ao_mfc
from peald.state import state

from conftest import FakeSim, free_port, wait_until
from test_v044 import wired                                                # noqa: F401  (픽스처)
from test_v047 import PlcProc, rig                                         # noqa: F401  (픽스처)
from test_v048 import alm0, alm1, aux, pump_down, running, table
from test_v049 import _big_csv, _aget

websockets = pytest.importorskip("websockets")
uvicorn = pytest.importorskip("uvicorn")
bit = A.bit


# ===================== A1 블랙홀 끊김 =====================
class Blackhole:
    """서버 ↔ PLC 중계. hole(s) 동안 기존 연결의 바이트를 버리고(닫지 않음), 새 연결은 답 없이 매달린다
    (클라이언트의 연결 함수를 바꿔 끼운다 — Windows 에서도 SYN 무응답을 같게)."""

    def __init__(self, target):
        self.target = target
        self.port = free_port()
        self.until = 0.0
        self.stop = False
        self.lsock = socket.socket()
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind(("127.0.0.1", self.port))
        self.lsock.listen(8)
        self.lsock.settimeout(0.05)
        threading.Thread(target=self._accept, daemon=True).start()

    def holed(self):
        return time.monotonic() < self.until

    def _accept(self):
        while not self.stop:
            try:
                c, _ = self.lsock.accept()
            except (socket.timeout, OSError):
                continue
            u = socket.create_connection(("127.0.0.1", self.target))
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    def _pipe(self, a, b):
        try:
            while True:
                d = a.recv(4096)
                if not d:
                    break
                if self.holed():
                    continue                       # 바이트가 사라진다 — 연결은 그대로
                b.sendall(d)
        except OSError:
            pass
        for x in (a, b):
            try:
                x.close()
            except OSError:
                pass

    def patch(self, client):
        """끊김 동안 connect 가 답 없이 매달린다 — 시간 초과(연결 함수의 timeout)까지."""
        orig = client.connect
        hole = self

        async def connect(timeout=None):
            if hole.holed():
                await asyncio.sleep(timeout or client.timeout)
                raise asyncio.TimeoutError()
            return await orig(timeout) if timeout else await orig()
        client.connect = connect

    def close(self):
        self.stop = True
        try:
            self.lsock.close()
        except OSError:
            pass


@pytest.fixture
def hole_rig(tmp_path):
    plc = PlcProc(tmp_path)
    hole = Blackhole(plc.port)
    from peald.server import create_app, uvicorn_config
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": False, "host": "127.0.0.1", "port": hole.port})
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
    hole.patch(state.link.client)
    try:
        yield types.SimpleNamespace(port=port, plc=plc, hole=hole)
    finally:
        server.should_exit = True
        th.join(10)
        hole.close()
        plc.stop()
        state.link = None
        state.runner = None
        state.sim = None


@pytest.mark.parametrize("cut_s", [1.2, 2.0])
async def test_blackhole_cut_reconnects_before_watchdog(hole_rig, cut_s):
    r = hole_rig
    await asyncio.sleep(1.0)
    r.plc.reset()
    await asyncio.sleep(1.0)                              # 끊기 전 하트비트를 받아 둔다
    t0 = time.monotonic()
    r.hole.until = t0 + cut_s
    assert await wait_until(lambda: not state.link.connected, cut_s + 2)
    assert await wait_until(lambda: state.link.connected and state.link.write_ok, 8)
    back = time.monotonic() - t0
    await asyncio.sleep(1.0)
    gap, trip = r.plc.stats()
    print(f"\n블랙홀 {cut_s} s → 다시 붙기까지 {back:.2f} s · PLC 쪽 하트비트 최대 공백 {gap} ms")
    assert gap >= cut_s * 1000 - 300, gap                 # 끊김을 걸친 공백을 쟀다
    assert not trip and gap < 2800, gap


def test_fast_window_is_whole_watchdog_and_short_connect(cfg):
    lk = plclink.PlcLink(cfg, Converters(cfg))
    lk.prm_readback = {A.D_PRM_PC_WDT_MS: 3000}
    lk.last_hb_write_at = time.monotonic() - 2.9
    assert lk._in_fast_window()
    lk.last_hb_write_at = time.monotonic() - 3.1
    assert not lk._in_fast_window()
    assert (plclink.FAST_CONNECT_S, plclink.FAST_RETRY_S, plclink.HEARTBEAT_S) == (0.3, 0.25, 0.25)
    from peald import config as CF
    assert CF.DEFAULTS["plc"]["heartbeat_ms"] == 250
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        assert json.load(f)["plc"]["heartbeat_ms"] == 250


async def test_timeout_retries_immediately_refusal_waits(cfg, monkeypatch):
    """창 안: 시간 초과로 실패하면 바로 다시(0.3 s 시간 초과), 바로 거절되면 0.25 s 쉬고 다시."""
    lk = plclink.PlcLink(cfg, Converters(cfg))
    lk.last_hb_write_at = time.monotonic()
    tries = []

    async def connect(timeout=None):
        tries.append((time.monotonic(), timeout))
        if len(tries) <= 2:
            raise asyncio.TimeoutError()
        if len(tries) <= 4:
            raise ConnectionRefusedError("거절")
        lk._stop = True
        raise ConnectionRefusedError("끝")
    lk.client.connect = connect
    await asyncio.wait_for(lk._run(), 5)
    gaps = [b[0] - a[0] for a, b in zip(tries, tries[1:])]
    assert all(t == plclink.FAST_CONNECT_S for _t, t in tries)
    assert gaps[0] < 0.05 and gaps[1] < 0.05                # 시간 초과 뒤 — 바로
    assert 0.2 < gaps[2] < 0.4 and 0.2 < gaps[3] < 0.4      # 거절 뒤 — 0.25 s


# ===================== A2 큰 CSV 읽기 =====================
def _ref_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    return (rows[0], rows[1:]) if rows else ([], [])


def _ref_chart_rows(path, max_points=logview.MAX_POINTS):
    """v0.4.9 의 그래프 묶음(줄 전체를 모으던 방식) — 결과 비교용."""
    head, rows = _ref_rows(path)
    idx = {h: i for i, h in enumerate(head)}
    ti = idx.get("경과 s")
    cols = [i for i, h in enumerate(head) if logview._group_of(h)]
    pts = []
    for r in rows:
        try:
            t = float(r[ti])
        except (TypeError, ValueError, IndexError):
            continue
        vals = []
        for i in cols:
            try:
                vals.append(float(r[i]) if i < len(r) and r[i] != "" else None)
            except ValueError:
                vals.append(None)
        pts.append((t, vals))
    out = []
    if pts:
        t0, t1 = pts[0][0], pts[-1][0]
        width = max((t1 - t0) / max(1, max_points), 1e-9)
        buckets = {}
        for t, vals in pts:
            b = min(int((t - t0) / width), max_points - 1) if t1 > t0 else 0
            buckets.setdefault(b, []).append((t, vals))
        for b in sorted(buckets):
            grp = buckets[b]
            row = [sum(x[0] for x in grp) / len(grp)]
            for k in range(len(cols)):
                vs = [x[1][k] for x in grp if x[1][k] is not None]
                row.append([min(vs), max(vs), sum(vs) / len(vs)] if vs else None)
            out.append(row)
    return out


def _close(a, b):
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _write(name, text):
    p = os.path.join(paths.DATALOG_DIR, name + ".csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
    return p


@pytest.mark.parametrize("kind", ["big", "quoted", "no_trailing_newline", "header_only", "quoted_last"])
def test_chart_table_meta_same_as_full_read(kind):
    logview.chart_cache.clear()
    if kind == "big":
        name = _big_csv("20260101_000000_큰", 20000)
        path = os.path.join(paths.DATALOG_DIR, name + ".csv")
    else:
        head = "시각,경과 s,장비 상태,블록,스텝,스텝 이름,CVG Torr,MFC1 현재 sccm\r\n"
        quoted_name = '"줄\r\n바꿈"'                     # 따옴표 안 줄바꿈
        body = "".join(f"2026-01-01 00:00:{i % 60:02d},{i * 0.5:.1f},공정 중,1,{1 + i // 5},"
                       + (quoted_name if kind.startswith("quoted") and i % 7 == 3 else "s")
                       + f",{0.01 + i * 0.001:.5f},{'' if i % 4 == 0 else 100 + i}\r\n" for i in range(60))
        if kind == "quoted_last":
            body += "2026-01-01 00:01:00,30.0,대기,1,9,\"끝\r\n줄\",0.5,7\r\n"
        text = head + ("" if kind == "header_only" else body)
        if kind == "no_trailing_newline":
            text = text.rstrip("\r\n")
        name = "20260101_000000_" + kind
        path = _write(name, text)
    head, rows = _ref_rows(path)
    res = logview.chart(name)
    ref = _ref_chart_rows(path)
    assert len(res["rows"]) == len(ref)
    for r, q in zip(res["rows"], ref):
        assert _close(r[0], q[0])
        for c, d in zip(r[1:], q[1:]):
            assert (c is None) == (d is None) and (c is None or all(_close(u, v) for u, v in zip(c, d)))
    for off in (0, 5, 200, len(rows) - 3, 10 ** 6):
        t = logview.table(name, off)
        assert t["head"] == head and t["total"] == len(rows) and t["rows"] == rows[off:off + logview.PAGE]
    g = logview._guess_meta(path)
    assert g["rows"] == len(rows)
    if rows:
        idx = {h: i for i, h in enumerate(head)}
        assert g["ended"] == rows[-1][idx["시각"]]
    # JSON — 유효 숫자 5 자리 · 결과는 같다(자릿수 제외)
    out = json.loads(logview.chart_json(name))
    for r, q in zip(out["rows"], ref):
        for c, d in zip(r[1:], q[1:]):
            assert (c is None) == (d is None) and (c is None or all(abs(u - v) <= 1e-4 * max(1, abs(v))
                                                                     for u, v in zip(c, d)))


async def test_heavy_queries_keep_plc_heartbeat_under_700ms(rig):
    """v0.4.9 시험 조건(큰 CSV 여러 개, 원격 · 로컬 동시) — PLC 쪽 하트비트 최대 공백 < 700 ms."""
    logview.chart_cache.clear()
    names = [_big_csv(f"2026010{i}_000000_큰파일{i}", 120000) for i in range(1, 6)]
    size = sum(os.path.getsize(os.path.join(paths.DATALOG_DIR, n + ".csv")) for n in names)
    rig.plc.reset()
    await asyncio.sleep(0.6)
    t0 = time.monotonic()
    jobs = []
    for k in range(8):                                    # 원격 8개(5개 번갈아)
        jobs.append(_aget(rig.port, "/api/datalog/chart?name=" + urllib.request.quote(names[k % 5])
                          + f"&remote=1&k={k}", 180))
    for k in range(9):                                    # 로컬 9개(그래프 · 표 · 목록)
        nm = urllib.request.quote(names[k % 5])
        url = [f"/api/datalog/chart?name={nm}&k={k}", f"/api/datalog/rows?name={nm}&offset=119800&k={k}",
               f"/api/datalog/list?k={k}"][k % 3]
        jobs.append(_aget(rig.port, url, 180))
    res = await asyncio.gather(*jobs)
    took = time.monotonic() - t0
    await asyncio.sleep(0.6)
    gap, trip = rig.plc.stats()
    codes = sorted(r[0] for r in res)
    print(f"\n큰 CSV {size / 1e6:.0f} MB — 원격 8 · 로컬 9 요청 {took:.1f} s · 응답 {codes} · "
          f"PLC 쪽 하트비트 최대 공백 {gap} ms")
    assert set(codes) <= {200, 429} and codes.count(200) >= 9
    assert not trip and gap < 700, gap


# ===================== A3 PLC 멈춤 동안 기록 =====================
def _stalled_link(cfg):
    lk = plclink.PlcLink(cfg, Converters(cfg))
    lk.connected = True
    lk.status = [0] * A.STATUS_COUNT
    lk._connected_at = lk._plc_hb_at = time.monotonic() - 10
    assert lk.plc_hb_stalled
    return lk


def test_stall_not_recorded_in_trend_and_datalog_row(cfg):
    from peald.trend_buffer import trend
    from peald.trendlog import trendlog
    state.install_config(cfg, [], "example")
    state.link = _stalled_link(cfg)
    runner = types.SimpleNamespace(active_run=True, active_name="멈춤", active_recipe=R.empty_recipe("멈춤"),
                                   active_table={"number": 1}, last_result="", progress=lambda: {},
                                   tick=lambda *_a: None)
    state.runner = runner
    state.datalog = DataLog(state)
    try:
        n_fast, n_pend = len(trend.fast.items()) if hasattr(trend.fast, "items") else None, len(trendlog.pending)
        trendlog._next = 0
        for _ in range(3):
            loops.sample_once()
            state.datalog._next = 0
        assert len(trendlog.pending) == n_pend, "멈춤 동안 트렌드 기록에 넣었다"
        if n_fast is not None:
            assert len(trend.fast.items()) == n_fast
        with open(state.datalog.path, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))[1:]
        assert rows and all(r[2] == "PLC 멈춤" and all(c == "" for c in r[3:]) for r in rows), rows[:2]
        # 다시 하트비트가 돌면 트렌드에 들어간다
        state.link._plc_hb_at = time.monotonic()
        trendlog._next = 0
        loops.sample_once()
        assert len(trendlog.pending) == n_pend + 1
    finally:
        state.datalog.close()
        state.link = state.runner = state.datalog = None


# ===================== A4 끝 판정 =====================
def _runner_with(cfg, blocks=2, groups=None, repeat=3):
    state.install_config(cfg, [], "example")
    state.alarms.clear_all()
    rec = R.empty_recipe("끝")
    rec["blocks"] = []
    for i in range(blocks):
        b = R.empty_block(f"b{i + 1}")
        b["repeat"] = repeat if i == blocks - 1 else 1
        b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
        b["steps"] = [{"name": "s1", "time_ms": 200, "valves": [], "pause_ok": True},
                      {"name": "s2", "time_ms": 200, "valves": [], "pause_ok": True}]
        if DEV.HAS_RF:
            b["rf_w"] = 0.0
            for st in b["steps"]:
                st["rf"] = False
        rec["blocks"].append(b)
    rec["groups"] = groups or []
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    r = ProcessRunner(state)
    r.run = {"name": "끝", "recipe": rec, "table": tbl}
    return r, tbl


def _s(seq, st=A.STATE_IDLE, blk=1, cyc=1, gp=1, a0=0, a1=0, i1=0):
    s = [0] * A.STATUS_COUNT
    s[A.D_SEQ_STATE], s[A.D_STATE], s[A.D_SEQ_BLOCK], s[A.D_SEQ_GROUP_PASS] = seq, st, blk, gp
    s[A.D_SEQ_BLOCK_PASS] = cyc
    s[A.D_ALARM0], s[A.D_ALARM1], s[A.D_INPUT1] = a0, a1, i1
    return s


def test_end_recipe_table_error_block_and_group(cfg):
    r, tbl = _runner_with(cfg, blocks=3, groups=[{"from_block": 1, "to_block": 1, "repeat": 1},
                                                 {"from_block": 2, "to_block": 3, "repeat": 2}])
    w = tbl["words"]
    b2 = A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_STRIDE - A.RCP_SUM_BASE
    w[b2 + A.RCP_BLOCK_LAST] = 0                          # 블록 2 끝 스텝 < 첫 스텝
    out = r.end_result(_s(8, blk=2, a0=1 << A.ALM0_RECIPE))
    assert out == "중단 (레시피 표 오류 — 블록 2 적재 거절: 끝 스텝 < 첫 스텝)", out
    r2, tbl2 = _runner_with(cfg, blocks=3, groups=[{"from_block": 1, "to_block": 1, "repeat": 1},
                                                   {"from_block": 2, "to_block": 3, "repeat": 2}])
    g2 = A.D_RCP_GROUP_BASE + A.RCP_GROUP_STRIDE - A.RCP_SUM_BASE
    tbl2["words"][g2 + 2] = 40000                          # 그룹 2 반복 40000 = 음수
    out = r2.end_result(_s(8, blk=2, a0=1 << A.ALM0_RECIPE))
    assert out.startswith("중단 (레시피 표 오류 — 그룹 2 진입 거절: 반복 < 1") and "블록 2" in out, out
    # b13 이 운전자 중단보다 앞선다
    r.abort_begin()
    r.abort_result(True)
    assert r.end_result(_s(8, blk=2, a0=1 << A.ALM0_RECIPE)).startswith("중단 (레시피 표 오류")
    assert r.end_result(_s(8, blk=2)) == "중단 (운전자 중단)"


def test_end_waits_for_b13_one_second(cfg, monkeypatch):
    from peald import process as P
    monkeypatch.setattr(P, "B13_WAIT_S", 0.15)
    r, _t = _runner_with(cfg)
    s = _s(4, st=A.STATE_RUN, blk=1)
    link = types.SimpleNamespace(connected=True, status=s, cmd_reg=lambda a: None, plc_hb_stalled=False)
    state.link = link
    logs = []
    try:
        r.tick(lambda lv, m: logs.append(m))
        link.status = _s(8, blk=2)                        # 시퀀서 8 — b13 은 한 스캔 늦다
        r.tick(lambda lv, m: logs.append(m))
        assert r.last_result == "" and not logs
        link.status = _s(8, blk=2, a0=1 << A.ALM0_RECIPE)
        r.tick(lambda lv, m: logs.append(m))
        assert r.last_result.startswith("중단 (레시피 표 오류 — 블록 2"), r.last_result
        # b13 이 1 s 안에 안 서면 그때 값으로 'PLC 중단'
        r2, _t = _runner_with(cfg)
        link.status = _s(4, st=A.STATE_RUN)
        r2.tick(lambda *a: None)
        link.status = _s(8, blk=1)
        r2.tick(lambda *a: None)
        time.sleep(0.2)
        r2.tick(lambda *a: None)
        assert r2.last_result == "중단 (PLC 중단)"
    finally:
        state.link = None


def test_end_plc_restart_complete_then_safe_stop_and_overlap(cfg):
    r, _t = _runner_with(cfg, blocks=2, repeat=7)
    assert r.end_result(_s(0, blk=0, cyc=0)) == "중단 (PLC 재시작 — 시퀀서 · 출력 초기화)"
    r._stall_in_run = True
    assert r.end_result(_s(4, blk=2)) == "중단 (PLC 재시작 — 시퀀서 · 출력 초기화)"
    r._stall_in_run = False
    state.alarms.update(1 << A.ALM0_PC_LINK, 0)
    out = r.end_result(_s(6, st=A.STATE_SAFE_STOP, blk=3))
    assert out.startswith("정상 종료 (끝난 뒤 안전 정지: ") and "PC" in out, out
    assert r.end_result(_s(6, st=A.STATE_SAFE_STOP, blk=2)).startswith("중단 (안전 정지 — ")
    state.alarms.clear_all()
    assert r.end_result(_s(6, blk=2, cyc=7)) == "정상 종료 (사이클 후 정지와 겹침)"
    assert r.end_result(_s(6, blk=2, cyc=4)) == "사이클 후 정지 (블록 2 · 사이클 4/7)"
    assert r.end_result(_s(6, blk=1, cyc=1)).startswith("사이클 후 정지 (블록 1")
    # 마지막 블록이 그룹 안이면 마지막 그룹 회차여야 겹침
    rg, _t = _runner_with(cfg, blocks=2, repeat=7, groups=[{"from_block": 1, "to_block": 2, "repeat": 3}])
    assert rg.end_result(_s(6, blk=2, cyc=7, gp=2)) == "사이클 후 정지 (블록 2 · 사이클 7/7)"
    assert rg.end_result(_s(6, blk=2, cyc=7, gp=3)) == "정상 종료 (사이클 후 정지와 겹침)"


def test_end_abort_result_unknown(cfg):
    r, _t = _runner_with(cfg)
    r.abort_begin()
    r.abort_result(False, unknown=True)
    assert r.end_result(_s(8, blk=1)) == "중단 (운전자 중단 — 결과 확인 안 됨)"
    r2, _t = _runner_with(cfg)
    r2.abort_begin()
    r2.abort_result(False)                                # 거절 — 적지 않는다
    assert r2.end_result(_s(8, blk=1)) == "중단 (PLC 중단)"


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_end_powder_b3_follows(cfg):
    r, _t = _runner_with(cfg)
    names = {f"A1-{d['bit']:02d}": d["name"] for d in DEV.ALARMS1}
    emo = next(d["name"] for d in DEV.ALARMS0 if d["bit"] == A.ALM0_EMO)
    state.alarms.update(1 << A.ALM0_EMO, 1 << A.ALM1_O3_GEN)
    try:
        out = r.end_result(_s(8, st=A.STATE_SAFE_STOP, blk=1))
        assert out == f"중단 (안전 정지 — {emo} · 뒤따름: {names[f'A1-{A.ALM1_O3_GEN:02d}']})", out
        # O3 발생기 알람 입력이 켜져 있으면 원인이다
        out = r.end_result(_s(8, st=A.STATE_SAFE_STOP, blk=1, i1=1 << A.IN1_O3_ALM))
        assert "뒤따름" not in out
    finally:
        state.alarms.clear_all()


# ===================== A5 =====================
async def test_grouped_log_flushes_remaining_count(monkeypatch):
    lines = []
    monkeypatch.setattr(logger, "write", lambda lv, m: lines.append(m))
    CN._grouped.clear()
    for _ in range(4):
        CN.grouped_log(("x", "10.0.0.5"), "warn", "거절")
    assert lines == ["거절"]
    await asyncio.sleep(1.2)
    assert lines == ["거절", "거절 — 같은 일 3건 더"]
    await asyncio.sleep(1.2)
    assert len(lines) == 2


async def test_plc_recipe_change_pushes_state(monkeypatch):
    pushed = []
    vals = iter([{"number": 1}, {"number": 1}, {"number": 2}])

    async def refresh():
        state.plc_recipe = next(vals)

    async def push():
        pushed.append(dict(state.plc_recipe))
    monkeypatch.setattr(C, "refresh_plc_recipe", refresh)
    monkeypatch.setattr(CN, "push_state", push)
    old = state.link, state.runner
    state.link = types.SimpleNamespace(connected=True)
    state.runner = None
    state.plc_recipe = {}
    try:
        for _ in range(3):
            await loops.plc_recipe_once()
        assert pushed == [{"number": 1}, {"number": 2}]
    finally:
        state.link, state.runner = old


def test_readme_watchdog_address():
    root = os.path.dirname(paths.EXAMPLE_CONFIG)
    text = open(os.path.join(os.path.dirname(root), "README.md"), encoding="utf-8").read()
    # 재연결 절의 와치독 주소 — D01100(PRM_PC_WDT_MS). D01102 는 베이스 도달 제한
    assert "(`D01102` 되읽은 값" not in text and "(`D01100` 되읽은 값" in text


# ===================== B1 · B2 화면 (headless) =====================
playwright = None


async def _page(port):
    pw_mod = pytest.importorskip("playwright.async_api")
    pw = await pw_mod.async_playwright().start()
    b = await pw.chromium.launch(headless=True)
    pg = await b.new_page(viewport={"width": 960, "height": 1040})
    await pg.goto(f"http://127.0.0.1:{port}/")
    await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
    return pw, b, pg


async def test_b1_alarm_tab_unknown_when_plc_down(rig):
    pw, b, pg = await _page(rig.port)
    try:
        await pg.evaluate("() => core.setTab('alarm')")
        await asyncio.sleep(0.5)
        c0 = await pg.evaluate("() => core.bind('alarmCount').textContent")
        assert c0.startswith("중대 "), c0                  # 처음 열 때 '—' 가 아니다
        rig.plc.stop()
        await pg.wait_for_function("() => core.bind('alarmCount').textContent === '알 수 없음'", timeout=15000)
        txt = await pg.evaluate("() => core.bind('alarmTbl').textContent")
        assert "알 수 없습니다" in txt and "없습니다" in txt and "현재 알람이 없습니다" not in txt
    finally:
        await b.close()
        await pw.stop()


async def test_b2_rename_without_reply_unlocks_after_5s(rig, monkeypatch):
    async def silent(d, ws):
        return None
    monkeypatch.setitem(C._HANDLERS, "recipe_rename", silent)
    assert storage.save("답없음", R.empty_recipe("답없음")) or True
    pw, b, pg = await _page(rig.port)
    try:
        await pg.evaluate("() => { core.setTab('recipe'); app.send('recipe_load', {name: '답없음'}); }")
        await pg.wait_for_function("() => document.querySelector('[data-rcf=\"name\"]').value === '답없음'",
                                   timeout=5000)
        await pg.click("[data-rcbtn='rename']")
        await pg.wait_for_function("() => !!document.getElementById('rcAskName')", timeout=3000)
        await pg.fill("#rcAskName", "새이름")
        await asyncio.sleep(0.6)
        await pg.click("#confirmModal [data-cf='ok']")
        await asyncio.sleep(0.5)
        locked = await pg.evaluate("() => ['rcSave', 'rcRename', 'rcDelete'].map(k => core.bind(k).disabled)")
        assert locked == [True, True, True], locked
        await pg.wait_for_function("() => core.bind('rcSaveMsg').textContent.startsWith('답 없음')", timeout=7000)
        free = await pg.evaluate("() => ['rcSave', 'rcRename', 'rcDelete'].map(k => core.bind(k).disabled)")
        name = await pg.evaluate("() => document.querySelector('[data-rcf=\"name\"]').value")
        assert free == [False, False, False] and name == "답없음", (free, name)
    finally:
        await b.close()
        await pw.stop()


# ===================== C 시뮬레이터 =====================
def _emo(fs, on):
    fs.sim.set_fault("emo", on)


def test_c1_reset_in_p35_relatches_and_clears_new(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    fs.step(2)
    _emo(fs, True)
    fs.step(1)
    assert alm0(s, A.ALM0_EMO) and s.reg[A.D_ALARM_NEW] == 1
    s._execute(A.CMD_ALARM_RESET)                         # 원인이 남은 채 리셋
    fs.step(1)
    assert alm0(s, A.ALM0_EMO) and s.reg[A.D_ALARM_NEW] == 0      # 다시 섰지만 새 알람 아님
    assert aux(s, A.AUX_LAMP_R) and not aux(s, A.AUX_BUZZER)      # 적색 켜짐 · 부저 끔
    fs.step(3)
    assert s.reg[A.D_ALARM_NEW] == 0
    s.set_fault("leak", True)                             # 다른 새 알람 — 이번 워드 & ~앞 워드
    fs.step(1)
    assert alm0(s, A.ALM0_LEAK) and s.reg[A.D_ALARM_NEW] == 1
    s._execute(A.CMD_ALARM_ACK)
    fs.step(1)
    assert s.reg[A.D_ALARM_NEW] == 0 and alm0(s, A.ALM0_LEAK)
    _emo(fs, False)
    s.set_fault("leak", False)
    s._execute(A.CMD_ALARM_RESET)
    fs.step(1)
    assert s.reg[A.D_ALARM0] == 0


def test_c2_safe_request_holds_with_emo_pressed_through_reset(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    fs.step(2)
    _emo(fs, True)
    fs.step(1)
    seen = []
    for _ in range(5):
        s._execute(A.CMD_ALARM_RESET)
        fs.step(1)
        seen.append((s.reg[A.D_STATE], bit(s.reg[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ)))
    assert all(x == (A.STATE_SAFE_STOP, True) for x in seen), seen
    _emo(fs, False)
    s._execute(A.CMD_ALARM_RESET)
    fs.step(1)                                            # 이 스캔 P30 은 리셋 전 워드를 본다 — 아직 6
    assert s.reg[A.D_STATE] == A.STATE_SAFE_STOP and s.reg[A.D_ALARM0] == 0
    fs.step(1)
    assert s.reg[A.D_STATE] == A.STATE_IDLE


def test_c2_pc_trip_alone_requests_safe_stop(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.pc_trip = True                                       # 알람 워드에 아직 없어도
    s._interlocks(fs.t[0])
    assert s.safe_stop


def test_c3_c4_both_request_ilk_same_scan_alarm_next_scan(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    # 대기압 — P50 이 수동 마스크를 지워 동시 요청이 없다(거짓 b15 없음)
    s.man_valve = (1 << DEV.PRECURSOR_VALVE_BITS[0]) | (1 << DEV.REACTANT_VALVE_BITS[0])
    fs.step(1)
    assert not bit(s.reg[A.D_INTERLOCK], A.ILK_BOTH_REQ)
    fs.step(1)
    assert not alm0(s, A.ALM0_BOTH_OPEN)
    pump_down(fs)
    s.man_valve = (1 << DEV.PRECURSOR_VALVE_BITS[0]) | (1 << DEV.REACTANT_VALVE_BITS[0])
    fs.step(1)
    assert bit(s.reg[A.D_INTERLOCK], A.ILK_BOTH_REQ) and not alm0(s, A.ALM0_BOTH_OPEN)
    assert s.alm0 >> A.ALM0_BOTH_OPEN & 1                 # 내부 래치는 섰다
    fs.step(1)
    assert alm0(s, A.ALM0_BOTH_OPEN)


def test_c5_mfc_timeout_ends_by_safe_stop_never_state1_seq8(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    s.write(A.D_PRM_MFC_TIMEOUT, [0])
    s.write(A.D_PRM_MFC_STABLE, [0])
    s.write(A.D_PRM_MFC_TOL, [0])
    s.write(A.RCP_SUM_BASE, table(cfg)["words"])
    assert s._process_start() == A.RESULT_OK
    seen = []
    for _ in range(6):
        fs.step(1)
        seen.append((s.reg[A.D_STATE], s.reg[A.D_SEQ_STATE]))
    assert (A.STATE_IDLE, 8) not in seen, seen
    assert seen[-1] == (A.STATE_SAFE_STOP, 8) and alm0(s, A.ALM0_MFC), seen
    if DEV.HAS_O3:
        assert alm1(s, A.ALM1_O3_GEN), "래더: 공정 중 안전 정지면 O3 허가 알람 b3 도 선다"


def _three_blocks(cfg, groups):
    rec = R.empty_recipe("적재")
    rec["blocks"] = []
    for i in range(3):
        b = R.empty_block(f"b{i + 1}")
        b["repeat"] = 1
        b["steps"] = [{"name": f"s{i + 1}{k}", "time_ms": 100, "valves": [], "pause_ok": True,
                       **({"rf": False} if DEV.HAS_RF else {})} for k in range(2)]
        if DEV.HAS_PCV:
            b["pcv_pct"] = 10.0 * (i + 1)
        if DEV.HAS_O3:
            b["o3"] = 5.0 * (i + 1)
        rec["blocks"].append(b)
    rec["groups"] = groups
    return R.to_plc_words(cfg, Converters(cfg), rec)


def _blk_word(words, n, off):
    return words[A.D_RCP_BLOCK_BASE + (n - 1) * A.RCP_BLOCK_STRIDE + off - A.RCP_SUM_BASE]


def _run_to_end(fs, n=400):
    for _ in range(n):
        fs.step(1)
        if not fs.sim.running:
            return
    raise AssertionError("끝나지 않았다")


def test_c6_group_entry_failure_loads_next_block_then_aborts(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    s.write(A.D_PRM_MFC_STABLE, [0])
    s.write(A.D_PRM_MFC_TOL, [0])
    tbl = _three_blocks(cfg, [{"from_block": 1, "to_block": 1, "repeat": 1},
                              {"from_block": 2, "to_block": 3, "repeat": 1}])
    s.write(A.RCP_SUM_BASE, tbl["words"])
    assert s._process_start() == A.RESULT_OK
    g2 = A.D_RCP_GROUP_BASE + A.RCP_GROUP_STRIDE - A.RCP_SUM_BASE
    s.work[g2 + 2] = 40000                                 # 그룹 2 반복 40000 = 음수(부호 있는 16비트)
    _run_to_end(fs)
    w = tbl["words"]
    assert s.reg[A.D_SEQ_BLOCK] == 2 and s.reg[A.D_SEQ_STEP] == _blk_word(w, 2, A.RCP_BLOCK_FIRST)
    assert s.reg[A.D_SEQ_GROUP_PASS] == 1 and s.reg[A.D_SEQ_BLOCK_PASS] == 1
    assert s.reg[A.D_SEQ_STATE] == 8 and s.reg[A.D_STATE] == A.STATE_IDLE
    if DEV.HAS_PCV:
        assert s.ao[AO_PCV] == _blk_word(w, 2, A.RCP_BLOCK_PCV)
    if DEV.HAS_O3:
        assert s.ao[AO_O3] == _blk_word(w, 2, A.RCP_BLOCK_O3)
    assert all(s.ao[ao_mfc(no)] == 0 for no in range(1, DEV.MFC_COUNT + 1))
    fs.step(1)
    assert alm0(s, A.ALM0_RECIPE)


def test_c6_block_load_failure_writes_position_and_ao_first(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    s.write(A.D_PRM_MFC_STABLE, [0])
    s.write(A.D_PRM_MFC_TOL, [0])
    tbl = _three_blocks(cfg, [])
    s.write(A.RCP_SUM_BASE, tbl["words"])
    assert s._process_start() == A.RESULT_OK
    b2 = A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_STRIDE - A.RCP_SUM_BASE
    s.work[b2 + A.RCP_BLOCK_LAST] = 0                      # 끝 스텝 < 첫 스텝
    _run_to_end(fs)
    w = tbl["words"]
    assert (s.reg[A.D_SEQ_BLOCK], s.reg[A.D_SEQ_STEP], s.reg[A.D_SEQ_BLOCK_PASS]) == \
        (2, _blk_word(w, 2, A.RCP_BLOCK_FIRST), 1)
    if DEV.HAS_PCV:
        assert s.ao[AO_PCV] == _blk_word(w, 2, A.RCP_BLOCK_PCV)
    if DEV.HAS_O3:
        assert s.ao[AO_O3] == _blk_word(w, 2, A.RCP_BLOCK_O3)
    assert s.reg[A.D_SEQ_STATE] == 8 and s.reg[A.D_STATE] == A.STATE_IDLE


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_c7_start_result3_loads_block1_o3(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    tbl = _three_blocks(cfg, [])
    w = list(tbl["words"])
    w[A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_FIRST - A.RCP_SUM_BASE] = 0     # 블록 1 적재 거절
    w[A.D_RCP_SUM - A.RCP_SUM_BASE] = R.checksum_of(w)
    s.write(A.RCP_SUM_BASE, w)
    s.ao[AO_O3] = 1234                                     # 수동 값
    s.write(A.D_PRM_O3_MAX, [16000])
    assert s._process_start() == A.RESULT_RECIPE
    assert s.ao[AO_O3] == _blk_word(w, 1, A.RCP_BLOCK_O3)
    lim = _blk_word(w, 1, A.RCP_BLOCK_O3) - 1              # 상한보다 크면 상한
    s.write(A.D_PRM_O3_MAX, [lim])
    assert s._process_start() == A.RESULT_RECIPE
    assert s.ao[AO_O3] == lim


def test_c8_ive_stuck_alarm_after_5s(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    fs.step(5)
    s.set_fault("ive_stuck", True)
    fs.step(int(4.8 / 0.02))
    assert not alm1(s, A.ALM1_IVE)
    fs.step(int(0.4 / 0.02))
    assert alm1(s, A.ALM1_IVE) and s.reg[A.D_STATE] == A.STATE_SAFE_STOP


def test_c8_pump_no_feedback_alarm_then_pump_stops(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.set_fault("pump_nofb", True)
    assert s._execute(A.CMD_PUMP_START) == A.RESULT_OK
    fs.step(int(9.8 / 0.02))
    assert s.pump_on and not alm0(s, A.ALM0_PUMP)
    fs.step(int(0.3 / 0.02))
    assert alm0(s, A.ALM0_PUMP) and not s.pump_req and not aux(s, A.AUX_PUMP)


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_c8_bypass_pump_no_feedback(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.set_fault("bp_nofb", True)
    s.man_aux = (1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB)
    fs.step(5)
    assert aux(s, A.AUX_BYPASS_PUMP) and not aux(s, A.AUX_IVB)      # 운전 입력 없음 — IV-B 바로 닫힘
    fs.step(int(10.2 / 0.02))
    assert alm1(s, A.ALM1_BYPASS_PUMP)


def test_c8_faults_on_panel():
    from peald.simulator import visible_faults
    keys = {f["key"] for f in visible_faults()}
    assert {"ive_stuck", "pump_nofb", "plc_stop"} <= keys
    assert ("bp_nofb" in keys) == bool(DEV.HAS_O3)


def test_c9_stop_then_run_first_scan(cfg):
    fs = FakeSim(cfg, o3=True)
    s = running(fs, cfg, repeat=500)
    fs.step(10)
    assert s.running
    s.write(A.D_PC_HB, [5])
    fs.step(2)
    assert s.pc_link_ok
    s.set_fault("plc_stop", True)
    fs.step(1)
    hb, ack = s.reg[A.D_PLC_HB], s.reg[A.D_ACK_NO]
    no = (s.reg[A.D_CMD_NO] + 1) & 0xFFFF
    s.write(A.D_CMD_CODE, [A.CMD_PUMP_START])
    s.write(A.D_CMD_NO, [no])                              # STOP 중에 쓴 명령
    s.write(A.D_PRM_PC_WDT_MS, [0])                        # 0 인 PRM — RUN 때 기본값
    fs.step(60)
    assert s.reg[A.D_PLC_HB] == hb and s.reg[A.D_ACK_NO] == ack, "STOP 중 상태 영역이 바뀌었다"
    assert s.valve_out == 0 and s.aux_out == 0 and not s.pump_on and s.heater_power == 0
    s.set_fault("plc_stop", False)
    fs.step(1)
    assert s.reg[A.D_ACK_NO] == no and s.reg[A.D_ACK_RESULT] == 0
    assert not s.pump_req, "STOP 중에 쓴 명령이 실행됐다"
    assert not s.running and s.reg[A.D_SEQ_STATE] == 0 and s.reg[A.D_SEQ_BLOCK] == 0
    assert s.reg[A.D_STATE] == A.STATE_IDLE
    assert s.reg[A.D_PRM_PC_WDT_MS] == A.PRM_PLC_DEFAULTS[A.D_PRM_PC_WDT_MS]
    assert not s.pc_link_ok
    s.write(A.D_PC_HB, [6])
    fs.step(1)
    assert s.pc_link_ok


async def test_c9_stall_then_restart_end_text(wired):
    """공정 중 PLC STOP → 하트비트 멈춤 → RUN — 끝 판정 'PLC 재시작'."""
    from test_v044 import _pump, _recipe, _local_ws
    lk, sim, cfg = wired
    await _pump(lk, sim)
    assert storage.save("재시작", _recipe("재시작", 500))
    assert state.runner.select("재시작")[0]
    await C.handle_command({"cmd": "process_start"}, _local_ws())
    assert await wait_until(lambda: lk.status[A.D_STATE] == A.STATE_RUN, 15)
    for _ in range(10):
        state.runner.tick(lambda *a: None)
        await asyncio.sleep(0.05)
    sim.set_fault("plc_stop", True)
    assert await wait_until(lambda: lk.plc_hb_stalled, plclink.PLC_HB_STALL_S + 3)
    state.runner.tick(lambda *a: None)
    sim.set_fault("plc_stop", False)
    assert await wait_until(lambda: lk.status[A.D_SEQ_STATE] == 0 and not lk.plc_hb_stalled, 5)
    for _ in range(20):
        state.runner.tick(lambda *a: None)
        if state.runner.last_result:
            break
        await asyncio.sleep(0.1)
    assert state.runner.last_result == "중단 (PLC 재시작 — 시퀀서 · 출력 초기화)"


# 쓰지 않는 이름 정리(가져온 것)
_ = (math, alm1)
