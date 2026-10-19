"""v0.7.0 — PC 끊김 정책(공정 계속 · 끝난 뒤 안전 정지) PC 쪽 · 데이터 로그 이어 쓰기 · 꺼진 동안 끝난 공정 판정 ·
알람 원인/확인 안내 · 실행 정보 · 누설률 점검 · 정비 카운터 · 공정 중 압력/히터 경고 · PRM 표 칸.

★ 시뮬레이터는 가짜 시계(conftest.FakeSim)로 래더 스캔 단위로 돌린다. 누설률 점검 · 데이터 로그 · 경고는 시계를 바꿔
  끼워 실제로 기다리지 않는다. 화면 함수는 node 로 부른다(node 가 없으면 건너뜀).
"""
import os
import csv
import json
import time
import types
import asyncio

import pytest

from peald import addresses as A
from peald import commands as C
from peald import datalog as DL
from peald import device as DEV
from peald import leakcheck as LC
from peald import logview
from peald import loops
from peald import maintenance as M
from peald import paths
from peald import process as P
from peald import recipe as R
from peald import version
from peald.convert import Converters
from peald.datalog import DataLog
from peald.process import ProcessRunner
from peald.state import state

from conftest import FakeSim
from test_v048 import alm0, pump_down, running
from test_v0411 import _node, JS
from test_v054 import _fn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bit = A.bit


def _src(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _s(seq, st=A.STATE_IDLE, blk=1, cyc=1, gp=1, a0=0, a1=0):
    s = [0] * A.STATUS_COUNT
    s[A.D_SEQ_STATE], s[A.D_STATE], s[A.D_SEQ_BLOCK], s[A.D_SEQ_GROUP_PASS] = seq, st, blk, gp
    s[A.D_SEQ_BLOCK_PASS] = cyc
    s[A.D_ALARM0], s[A.D_ALARM1] = a0, a1
    return s


def _recipe(name="v070", blocks=2, repeat=3, groups=None, steps=None):
    rec = R.empty_recipe(name)
    rec["blocks"] = []
    for i in range(blocks):
        b = R.empty_block(f"b{i + 1}")
        b["repeat"] = repeat
        b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
        b["steps"] = [dict(x) for x in (steps or [
            {"name": "s1", "time_ms": 200, "valves": [], "pause_ok": True},
            {"name": "s2", "time_ms": 200, "valves": [], "pause_ok": True}])]
        if DEV.HAS_RF:
            b["rf_w"] = 0.0
            for st in b["steps"]:
                st["rf"] = False
        rec["blocks"].append(b)
    rec["groups"] = groups or []
    return rec


class FakeLink:
    """끝 판정 · 경고 · 누설률 점검이 보는 만큼만 흉내 낸 링크(소켓 없음)."""

    def __init__(self, status=None, words=None, sim=None):
        self.connected = True
        self.plc_hb_stalled = False
        self.id_state = "ok"
        self.device_id = DEV.DEVICE_ID
        self._status = status or [0] * A.STATUS_COUNT
        self.words = words
        self.sim = sim
        self.cmd = {}
        self.sent = []
        self._cmd_no = 1
        self.applied_aux = 0
        self.applied_valve = 0
        self.prm_written = {}
        self.prm_readback = {}
        # state.live() 가 읽는 것(데이터 로그 한 줄이 live 를 쓴다)
        self.plc_hb_ok = True
        self.rtt_ms = 1
        self.addr_text = "127.0.0.1:0"
        self.simulate = False
        self.prm_mismatch = []
        self.require_id = False
        self.hb_gap_ms = self.hb_gap_max_ms = 0
        self.config_error = ""
        self.display = [0] * A.DISPLAY_COUNT

    def _param_words(self):
        return {}

    @property
    def status(self):
        return list(self.sim.reg[:A.STATUS_COUNT]) if self.sim is not None else self._status

    @status.setter
    def status(self, v):
        self._status = v

    def id_block_text(self):
        return ""

    def _wdt_s(self):
        return 3.0

    def cmd_reg(self, addr):
        return self.cmd.get(addr)

    async def read_recipe_area(self):
        return list(self.words) if self.words else None

    async def send_command(self, code, args=None):
        self.sent.append(code)
        if self.sim is not None:
            r = self.fs.cmd(code)
            return r, "처리됨" if r == A.RESULT_OK else f"결과 {r}"
        return A.RESULT_OK, "처리됨"


@pytest.fixture
def clean_state(cfg):
    state.install_config(cfg, [], "example")
    state.alarms.clear_all()
    old = (state.link, state.runner, state.datalog)
    yield cfg
    if state.datalog is not None and state.datalog.fp:
        state.datalog.close()
    state.link, state.runner, state.datalog = old


# ===================== 0. 버전 =====================
def test_version():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 7, 0)


# ===================== P1 · P2. 래더 규칙(시뮬레이터) =====================
def _hb(fs, n, dt=0.02):
    """PC 하트비트를 쓰며 n 스캔."""
    s = fs.sim
    for _ in range(n):
        s.write(A.D_PC_HB, [(s.reg[A.D_PC_HB] + 1) & 0xFFFF])
        fs.step(1, dt)


def _rig(cfg, repeat=10, step_ms=200):
    fs = FakeSim(cfg, o3=DEV.HAS_O3)
    s = fs.sim
    pump_down(fs)
    s.write(A.D_PRM_PC_WDT_MS, [300])
    _hb(fs, 10)
    assert s.pc_link_ok
    running(fs, cfg, repeat=repeat, step_ms=step_ms)
    _hb(fs, 2)
    assert s.running
    return fs, s


def test_p_mask_and_crit_marks_agree():
    assert DEV.CRITICAL_MASK0 == 0xD23F
    b6 = next(a for a in DEV.ALARMS0 if a["bit"] == A.ALM0_PC_LINK)
    assert b6["name"] == "PC Link Lost" and b6["crit"] is False
    for a in DEV.ALARMS0:
        assert bool((DEV.CRITICAL_MASK0 >> a["bit"]) & 1) == a["crit"], a
    ilk = next(i for i in DEV.INTERLOCKS if i["bit"] == A.ILK_SAFE_STOP_REQ)
    assert ilk["why"] == "비상정지 또는 중대 알람 또는 PC 끊김 트립 — 공정 중이면 끝난 뒤"


def test_p_trip_in_process_runs_to_end_then_safe_stop(cfg):
    fs, s = _rig(cfg)
    fs.step(25)                                     # 0.5 s 하트비트 없음 → 트립
    assert s.pc_trip and alm0(s, A.ALM0_PC_LINK)
    assert s.reg[A.D_STATE] == A.STATE_RUN and not s.safe_stop, "공정 중 트립이 공정을 세웠다"
    assert not bit(s.reg[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ)
    for _ in range(2000):
        fs.step()
        if not s.running:
            break
    assert s.seq_state == 6 and s.reg[A.D_SEQ_BLOCK] == 2, "끝까지 돌지 않았다"
    fs.step()
    assert s.reg[A.D_STATE] == A.STATE_SAFE_STOP and bit(s.reg[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ)
    end = list(s.reg[:A.STATUS_COUNT])
    assert P.judge_end(end, {"words": None, "block_count": 1}, None) == \
        "정상 종료 (끝난 뒤 안전 정지: PC Link Lost (트립 남음))"


def test_p_trip_while_paused_keeps_pause_then_resume_ends_normal(cfg):
    fs, s = _rig(cfg)
    s.write(A.D_CMD_CODE, [A.CMD_PAUSE])
    s.write(A.D_CMD_NO, [(s.reg[A.D_CMD_NO] + 1) & 0xFFFF])
    for _ in range(200):
        _hb(fs, 1)
        if s.seq_state == 7:
            break
    assert s.seq_state == 7
    fs.step(50)                                     # 1 s — 트립
    assert s.pc_trip and s.reg[A.D_STATE] == A.STATE_PAUSE and not s.safe_stop
    _hb(fs, 3)                                      # 하트비트 회복 → PC_LINK_OK
    s.write(A.D_CMD_CODE, [A.CMD_ALARM_RESET])
    s.write(A.D_CMD_NO, [(s.reg[A.D_CMD_NO] + 1) & 0xFFFF])
    _hb(fs, 2)
    assert not s.pc_trip and not alm0(s, A.ALM0_PC_LINK)
    s.write(A.D_CMD_CODE, [A.CMD_RESUME])
    s.write(A.D_CMD_NO, [(s.reg[A.D_CMD_NO] + 1) & 0xFFFF])
    _hb(fs, 1)
    assert s.reg[A.D_ACK_RESULT] == A.RESULT_OK
    for _ in range(3000):
        _hb(fs, 1)
        if not s.running:
            break
    _hb(fs, 2)
    assert s.seq_state == 6 and s.reg[A.D_STATE] == A.STATE_IDLE and not s.safe_stop


def test_p_trip_then_recover_and_reset_no_safe_stop_after_end(cfg):
    fs, s = _rig(cfg)
    fs.step(25)
    assert s.pc_trip and s.running
    _hb(fs, 3)
    s.write(A.D_CMD_CODE, [A.CMD_ALARM_RESET])
    s.write(A.D_CMD_NO, [(s.reg[A.D_CMD_NO] + 1) & 0xFFFF])
    _hb(fs, 2)
    assert not s.pc_trip
    for _ in range(3000):
        _hb(fs, 1)
        if not s.running:
            break
    _hb(fs, 3)
    assert s.seq_state == 6 and s.reg[A.D_STATE] == A.STATE_IDLE


def test_p_trip_while_idle_safe_stops_at_once(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.write(A.D_PRM_PC_WDT_MS, [300])
    _hb(fs, 10)
    fs.step(25)
    assert s.pc_trip and s.safe_stop and s.reg[A.D_STATE] == A.STATE_SAFE_STOP


def test_p_critical_alarm_still_stops_process_without_pc(cfg):
    fs, s = _rig(cfg)
    s.set_fault("emo", True)
    fs.step(3)
    assert not s.running and s.seq_state == 8 and s.reg[A.D_STATE] == A.STATE_SAFE_STOP
    s.set_fault("emo", False)


def test_p_sim_texts_and_leak_fault():
    from peald import simulator as S
    src = _src(DEV.KEY, "simulator.py")
    assert "0xD27F" not in src and "(PC 통신 트립 AND NOT SEQ_RUN)" in src
    keys = {f["key"]: f["name"] for f in S.FAULTS}
    assert "공정 중이면 끝까지" in keys["pc_hb_stop"] and "chamber_leak" in keys
    assert S.LEAK_FAULT_X == 100.0


# ===================== P3 · P6. 끝 문구 =====================
def test_p3_crit_text_trip_left_and_result_level(clean_state):
    a0 = 1 << A.ALM0_PC_LINK
    assert P.crit_text_of(_s(6, a0=a0)) == "PC Link Lost (트립 남음)"
    assert P.crit_text_of(_s(6)) == "중대 알람 이름 없음"
    tbl = {"block_count": 2}
    assert P.judge_end(_s(6, blk=3, a0=a0), tbl, None) == "정상 종료 (끝난 뒤 안전 정지: PC Link Lost (트립 남음))"
    assert P.judge_end(_s(6, st=A.STATE_SAFE_STOP, blk=3, a0=a0), tbl, None).startswith("정상 종료 (끝난 뒤 안전 정지: PC")
    assert P.judge_end(_s(6, blk=3), tbl, None) == "정상 종료"
    # 사이클 후 정지 뒤 트립만 남음 — 중단이 아니라 사이클 후 정지 + 끝난 뒤 안전 정지
    out = P.judge_end(_s(6, st=A.STATE_SAFE_STOP, blk=1, cyc=1, a0=a0), None, _recipe(), crit_text=None)
    assert out.startswith("사이클 후 정지 (블록 1") and out.endswith("끝난 뒤 안전 정지: PC Link Lost (트립 남음)"), out
    # 중대 알람이 있으면 지금처럼 중단(안전 정지)
    emo = (1 << A.ALM0_EMO) | a0
    assert P.judge_end(_s(8, st=A.STATE_SAFE_STOP, blk=1, a0=emo), tbl, None).startswith("중단 (안전 정지 — EMO")
    # 러너의 원인 글(알람 목록에 중대 없음 + b6)
    r = ProcessRunner(state)
    assert r._crit_text(_s(6, a0=a0)) == "PC Link Lost (트립 남음)"
    for res, lv in (("정상 종료 · PC 끊긴 동안 끝남", "ok"), ("정상 종료" + P.OFF_TAIL, "ok"),
                    ("정상 종료 (끝난 뒤 안전 정지: PC Link Lost (트립 남음)) · PC 꺼진 동안 끝남(…)", "warn"),
                    ("중단 (PLC 중단) · PC 끊긴 동안 끝남", "warn")):
        assert logview.result_level(res) == lv and P.result_level(res) == lv, res


def test_p3_valve_ok_detail_trip(clean_state):
    r = ProcessRunner(state)
    s = _s(1, st=A.STATE_SAFE_STOP, a0=1 << A.ALM0_PC_LINK)
    s[A.D_INTERLOCK] = (1 << A.ILK_SAFE_STOP_REQ)
    s[A.D_INPUT0] = (1 << A.IN0_EMO) | (1 << A.IN0_IVE_OPEN)
    assert "PC 끊김 트립 — Alarm Reset" in r._valve_ok_detail(s)
    s[A.D_ALARM0] |= 1 << A.ALM0_EMO
    assert "안전 정지 요구 해제" in r._valve_ok_detail(s)


def _active_runner(cfg, rec=None):
    rec = rec or _recipe()
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    r = ProcessRunner(state)
    r.run = {"name": "끊김", "recipe": rec, "table": tbl}
    r.active_run = True
    r._was_running = True
    r.started_at = time.time() - 10
    r._run_started_mono = time.monotonic() - 10
    return r, tbl


def test_p3_ended_while_link_lost(clean_state):
    cfg = clean_state
    r, _tbl = _active_runner(cfg)
    link = FakeLink(_s(4, st=A.STATE_RUN))
    state.link = link
    logs = []
    r.tick(lambda lv, m: logs.append((lv, m)))
    link.connected = False
    r.tick(lambda lv, m: logs.append((lv, m)))
    assert r._lost_at is not None
    link.connected = True
    link.status = _s(6, blk=3)                      # 끊긴 동안 끝났다
    r.tick(lambda lv, m: logs.append((lv, m)))
    assert r.last_result == "정상 종료 · PC 끊긴 동안 끝남", r.last_result
    line = [m for lv, m in logs if m.startswith("공정 ")][-1]
    assert "(다시 연결된 때까지)" in line, line
    assert [lv for lv, m in logs if m.startswith("공정 ")][-1] == "ok"
    assert not r.active_run


def test_p7_toast_when_back_in_process_and_no_auto_reset(clean_state):
    cfg = clean_state
    r, _tbl = _active_runner(cfg)
    link = FakeLink(_s(4, st=A.STATE_RUN))
    state.link = link
    r.tick(lambda *a: None)
    link.connected = False
    r.tick(lambda *a: None)
    r._lost_mono -= 4.0                             # 4 s 끊김(와치독 3 s 이상)
    link.connected = True
    link.status = _s(4, st=A.STATE_RUN, a0=1 << A.ALM0_PC_LINK)
    r.tick(lambda *a: None)
    assert r.pending_toasts and r.pending_toasts[0][1].startswith("PC 연결이 돌아왔습니다 — 끊긴 4 s 동안 공정은 계속")
    assert r.active_run and r.last_result == ""
    # PC 가 스스로 알람 리셋을 보내지 않는다
    for f in ("process.py", "plclink.py", "loops.py", "server.py", "state.py"):
        assert "CMD_ALARM_RESET" not in _src(DEV.KEY, f), f


def test_p7_pc_notices_by_state(clean_state):
    link = FakeLink(_s(4, st=A.STATE_RUN, a0=1 << A.ALM0_PC_LINK))
    state.link = link
    n = state.pc_notices(True)
    assert ["warn", P_TRIP_RUN, "plc"] in n                  # v0.7.1(U2): 'plc' — PLC 값으로 만든 알림 표시
    link.status = _s(6, st=A.STATE_SAFE_STOP, a0=1 << A.ALM0_PC_LINK)
    assert ["err", "PC 끊김 트립이 남아 PLC 가 안전 정지 상태입니다 — [Alarm Reset] 으로 풀립니다.", "plc"] in \
        state.pc_notices(True)
    link.status = _s(1)
    assert not [x for x in state.pc_notices(True) if "PC 끊김" in x[1] or "PC 연결" in x[1]]
    main = _src("frontend", "js", "views", "main.js")
    assert "t.plc.pc_trip" in main and "core.chip('Trip', 'warn', 'PC Trip — " in main      # v0.7.1(U4): 짧은 글
    assert "(t.pc_notices || [])" in _src("frontend", "js", "views", "alarm.js")


P_TRIP_RUN = ("PC 연결이 끊겼던 기록(PC Link Lost)이 남아 있습니다 — 공정은 계속됩니다. 그대로 두면 공정이 끝날 때 PLC 가 "
              "안전 정지합니다(밸브 · 수동 요청 끔, 펌프 · 히터 유지). [Alarm Reset] 으로 풀립니다.")


def _closed_log(cfg, rec, tbl, result=None):
    """끝 판정 없이 닫힌 데이터 로그(프로그램 종료) 하나를 만든다(링크 없이 — 줄은 'PLC Lost'). 이름."""
    state.runner = types.SimpleNamespace(run_info={}, active_run=False, progress=lambda: {})
    link, state.link = state.link, None
    try:
        dl = DataLog(state)
        dl.start(rec["name"], rec, tbl, 1000)
        dl._next = 0
        dl.tick(1)
        if result:
            dl.note_end(result)
        dl.close()
    finally:
        state.link = link
    return dl.name


async def test_p6_offline_end_judged_once(clean_state):
    cfg = clean_state
    rec = _recipe("꺼짐")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    name = _closed_log(cfg, rec, tbl)
    meta0 = json.load(open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), encoding="utf-8"))
    assert meta0["result"].startswith("기록 중단(") and not DL.judged(meta0)
    link = FakeLink(_s(6, st=A.STATE_SAFE_STOP, blk=3, a0=1 << A.ALM0_PC_LINK), words=tbl["words"])
    state.link = link
    r = ProcessRunner(state)
    state.runner = r
    logs = []

    async def plog(m, lv="info"):
        logs.append((lv, m))
    res = await r.check_offline_end(plog)
    assert res == "정상 종료 (끝난 뒤 안전 정지: PC Link Lost (트립 남음))" + P.OFF_TAIL, res
    meta = json.load(open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), encoding="utf-8"))
    assert meta["result"] == res and meta["ended"] == "모름" and meta["log_closed"] == meta0["ended"]
    assert meta["judged_at"] and r.offline_checked
    assert logview.meta_of(name)["result"] == res
    assert any("PC 가 꺼진 동안 끝난 공정" in m for _lv, m in logs)
    # 한 실행에 한 번 · 한 파일에 한 번
    assert await ProcessRunner(state).check_offline_end(plog) == ""


async def test_p6_offline_end_skips_other_table_and_plc_restart(clean_state):
    cfg = clean_state
    rec = _recipe("다름")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    name = _closed_log(cfg, rec, tbl)
    other = R.to_plc_words(cfg, Converters(cfg), _recipe("다름", repeat=5))
    plog = (lambda *a: asyncio.sleep(0))
    state.link = FakeLink(_s(6, blk=3), words=other["words"])
    assert await ProcessRunner(state).check_offline_end(plog) == ""
    state.link = FakeLink(_s(0, blk=0), words=tbl["words"])
    assert await ProcessRunner(state).check_offline_end(plog) == ""
    state.link = FakeLink(_s(4, st=A.STATE_RUN), words=tbl["words"])        # 공정 중이면 이어받기가 맡는다
    assert await ProcessRunner(state).check_offline_end(plog) == ""
    meta = json.load(open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), encoding="utf-8"))
    assert meta["result"].startswith("기록 중단(") and "judged_at" not in meta
    # 끝 판정이 이미 있으면 하지 않는다
    time.sleep(1.05)                                  # 이름(초 단위)이 겹치지 않게
    name2 = _closed_log(cfg, rec, tbl, "정상 종료")
    state.link = FakeLink(_s(6, blk=3), words=tbl["words"])
    assert await ProcessRunner(state).check_offline_end(plog) == ""
    assert json.load(open(os.path.join(paths.DATALOG_DIR, name2 + ".recipe.json"), encoding="utf-8"))["result"] == "정상 종료"


# ===================== P4 · P5. 데이터 로그 =====================
class _Clock:
    def __init__(self):
        self.t = 1000.0

    def ns(self):
        return types.SimpleNamespace(monotonic=lambda: self.t, time=time.time, strftime=time.strftime,
                                     mktime=time.mktime, strptime=time.strptime)


def test_p4_no_close_in_process_and_thin_rows(clean_state, monkeypatch):
    cfg = clean_state
    clk = _Clock()
    monkeypatch.setattr(DL, "time", clk.ns())
    link = FakeLink(_s(4, st=A.STATE_RUN))
    state.link = link
    rec = _recipe("끊김31분")
    state.runner = types.SimpleNamespace(active_run=True, active_name="끊김31분", active_recipe=rec,
                                         active_table={"number": 1}, last_result="", progress=lambda: {},
                                         run_info={})
    state.datalog = DataLog(state)
    loops._datalog_tick()
    first = state.datalog.path
    link.connected = False
    for _ in range(int(DL.DOWN_CLOSE_S) + 300):     # 35 분
        clk.t += 1.0
        state.datalog.tick(1)
    assert state.datalog.fp is not None and state.datalog.path == first, "공정 구간인데 닫혔다"
    link.connected = True
    clk.t += 1.0
    state.datalog.tick(1)
    with open(first, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))[1:]
    lost = [r for r in rows if r[2] == "PLC Lost"]
    late = [r for r in lost if float(r[1]) > DL.DOWN_CLOSE_S + 2]
    assert 4 <= len(late) <= 7, len(late)            # 30 분 뒤 5 분 동안 60 s 에 한 줄
    assert rows[-1][2] != "PLC Lost"                 # 다시 붙으면 같은 파일에 이어 쓴다
    # 공정 구간이 아니면 지금처럼 30 분에 닫는다
    state.runner.active_run = False
    link.connected = False
    for _ in range(int(DL.DOWN_CLOSE_S) + 5):
        clk.t += 1.0
        if state.datalog.fp is None:
            break
        state.datalog.tick(1)
    assert state.datalog.fp is None


def test_p5_resume_same_file_or_new_when_header_differs(clean_state):
    cfg = clean_state
    rec = _recipe("이어")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    name = _closed_log(cfg, rec, tbl)                 # 프로그램 종료로 닫힘(끝 판정 없음)
    state.link = FakeLink(_s(4, st=A.STATE_RUN))
    dl = DataLog(state)
    dl.start("이어", rec, {"number": tbl["number"], "words": tbl["words"]}, 1000, adopted=True)
    assert dl.name == name
    dl._next = 0
    dl.tick(1)
    meta = json.load(open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), encoding="utf-8"))
    assert "ended" not in meta and "result" not in meta and meta["resumed"][0]["at"]
    assert logview.meta_of(name)["result"] == "기록 중"
    dl.note_end("정상 종료")
    dl.close()
    with open(os.path.join(paths.DATALOG_DIR, name + ".csv"), encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert sum(1 for r in rows if r[2] == DL.RESTART_STATE) == 1
    assert all(c == "" for r in rows if r[2] == DL.RESTART_STATE for c in r[3:])
    meta = json.load(open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), encoding="utf-8"))
    assert meta["result"] == "정상 종료" and meta["resumed"] and meta["rows"] == len(rows) - 1
    # 머리줄이 다르면 새 파일(메타 adopted)
    time.sleep(1.05)                                  # 이름(초 단위)이 겹치지 않게
    name2 = _closed_log(cfg, rec, tbl)
    time.sleep(1.05)                                  # 이름(초 단위)이 겹치지 않게
    cfg2 = json.loads(json.dumps(cfg))
    for h in cfg2.get("heaters") or []:
        h["enabled"] = True
        h["max_c"] = h.get("max_c") or 200
    state.install_config(cfg2, [], "example")
    dl2 = DataLog(state)
    dl2.start("이어", rec, {"number": tbl["number"], "words": tbl["words"]}, 1000, adopted=True)
    assert dl2.name != name2
    assert json.load(open(dl2.meta_path, encoding="utf-8"))["adopted"] is True
    dl2.close()
    state.install_config(cfg, [], "example")


# ===================== A1. 알람 원인 · 확인 =====================
def test_a1_alarm_cause_check_everywhere(clean_state):
    for d in DEV.ALARMS0 + DEV.ALARMS1:
        assert d.get("cause", "").strip() and d.get("check", "").strip(), d
    defs = state.structure()["alarm_defs"]
    assert len(defs) == len(DEV.ALARMS0) + len(DEV.ALARMS1)
    assert defs["A0-06"]["cause"].startswith("PC 하트비트가 PC Heartbeat Timeout 넘게 멈춤")
    assert defs["A0-00"]["check"].startswith("버튼을 돌려 풀고 원인을 없앤 뒤 Alarm Reset")
    alarm = _src("frontend", "js", "views", "alarm.js")
    assert 'tabindex="0" role="button" aria-expanded="' in alarm and "almhelp" in alarm and "title=\"' + core.esc(tip)" in alarm
    core = _src("frontend", "js", "core.js")
    assert "'<div class=\"am-check\">확인: '" in core
    # v0.7.1(U1): 첫 문장으로 자르지 않는다(' — ' 뒤의 안전 문구가 빠졌다) — 전부 보인다
    assert "var ck = (defs[a.code] || {}).check || '';" in core and "firstSentence" not in core


# ===================== A2. 실행 정보 =====================
def test_a2_clean_run_info():
    out = P.clean_run_info({"operator": "  김\t작업\n자 " + "x" * 50, "sample": "S\x00\x1b[31m" + "y" * 100,
                            "memo": ["리스트"], "other": "버림"})
    assert out["operator"] == ("김 작업 자 " + "x" * 50)[:40]
    assert "\x00" not in out["sample"] and "\x1b" not in out["sample"] and len(out["sample"]) == 80
    assert out["memo"] == "" and set(out) == {"operator", "sample", "memo"}
    assert P.run_info_text({"operator": "A", "sample": ""}) == " · 작업자 A"
    assert P.run_info_text({}) == ""


async def test_a2_begin_keeps_info_and_meta_list(clean_state):
    cfg = clean_state
    r = ProcessRunner(state)
    state.runner = r
    state.link = None
    r.recipe = _recipe("정보")
    r.recipe_name = "정보"

    async def nop(*a, **k):
        return None
    ok, _msg = r.begin(nop, nop, info={"operator": "홍길동", "sample": "Si", "memo": "메모\n둘째"})
    assert ok and r.run_info == {"operator": "홍길동", "sample": "Si", "memo": "메모 둘째"}
    await r.stop_task()
    state.link = FakeLink(_s(4, st=A.STATE_RUN))
    state.link.cmd[A.D_HEATER_SV] = 2000
    for h in cfg.get("heaters") or []:
        h["enabled"] = h["ch"] == 1
    dl = DataLog(state)
    dl.start("정보", r.recipe, R.to_plc_words(cfg, Converters(cfg), r.recipe), 1000)
    meta = json.load(open(dl.meta_path, encoding="utf-8"))
    dl.close()
    assert meta["operator"] == "홍길동" and meta["sample"] == "Si" and meta["memo"] == "메모 둘째"
    assert meta["app_version"] == version.APP_VERSION and meta["device"]["key"] == DEV.KEY
    assert meta["device"]["id"] == DEV.DEVICE_ID and list(meta["heater_sv"].values()) == [200.0]
    assert isinstance(meta["prm"], list)
    item = next(i for i in logview.list_logs() if i["name"] == dl.name)
    assert item["operator"] == "홍길동" and item["sample"] == "Si" and item["memo"] == "메모 둘째"
    src = _src(DEV.KEY, "process.py")
    assert "{run_info_text(self.run_info)}" in src                    # 이벤트 로그 시작 줄


def test_a2_enter_does_not_start():
    main = _src("frontend", "js", "views", "main.js")
    out = _node(_fn(main, "runInfoKey") + r'''
      const ev = (key, inside) => ({ key, prevented: false, stopped: false,
        target: { closest: sel => (inside && sel === '[data-runinfo]') ? {} : null },
        preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } });
      const a = ev('Enter', true), b = ev('Enter', false), c = ev('a', true);
      console.log(JSON.stringify([runInfoKey(a), a.prevented, a.stopped, runInfoKey(b), b.prevented, runInfoKey(c)]));''')
    assert out == [True, True, True, False, False, False]
    assert "w.app.send('process_start', runInfo())" in main and "maxlength=\"' + f[2]" in main
    assert "['operator', 'Operator', 40], ['sample', 'Sample', 80], ['memo', 'Memo', 200]" in main
    cmd = _src(DEV.KEY, "commands.py")
    assert 'info = {k: d.get(k) for k in ("operator", "sample", "memo")}' in cmd and "state.runner.set_run_info(info)" in cmd


# ===================== A3. 누설률 점검 =====================
class _LeakRig:
    """가짜 시계로 도는 시뮬레이터 + 그 위의 링크 — 누설률 점검의 sleep 이 시계를 밀고 스캔을 돈다."""

    def __init__(self, cfg, monkeypatch):
        self.fs = FakeSim(cfg)
        s = self.fs.sim
        pump_down(self.fs)
        s.write(A.D_PRM_BASE_PRESS, [s.conv.cvg.to_raw(5.0)])
        for _ in range(300):
            self.fs.step(1, 0.05)
        self.link = FakeLink(sim=s)
        self.link.fs = self.fs
        state.link = self.link
        clk = self

        real_sleep = asyncio.sleep

        async def fake_sleep(dt):
            n = max(1, int(round(dt / 0.05)))
            for _ in range(n):
                clk.fs.t[0] += 0.05
                clk.fs.sim.tick()
            await real_sleep(0)
        monkeypatch.setattr(LC, "time", types.SimpleNamespace(monotonic=lambda: clk.fs.t[0], strftime=time.strftime))
        # 누설률 점검 모듈의 asyncio 만 바꿔 끼운다(전역 asyncio.sleep 은 그대로)
        monkeypatch.setattr(LC, "asyncio", types.SimpleNamespace(
            sleep=fake_sleep, create_task=asyncio.create_task, wait_for=asyncio.wait_for, shield=asyncio.shield,
            TimeoutError=asyncio.TimeoutError, CancelledError=asyncio.CancelledError))
        self.logs = []

    async def plog(self, m, lv="info"):
        self.logs.append((lv, m))

    async def run(self, cancel=False):
        lk = LC.LeakCheck()
        ok, msg = lk.start(state, self.plog, self.plog)
        assert ok, msg
        if cancel:
            lk.cancel("운전자 취소")                 # [Cancel] — 명령 11 뒤 첫 확인에서 멈추고 명령 8 로 되돌린다
        await asyncio.wait_for(lk.task, 30)
        return lk


@pytest.fixture
def leak_rig(clean_state, monkeypatch):
    cfg = clean_state
    cfg["leakcheck"] = {"settle_s": 3, "measure_s": 30, "limit_mtorr_min": 10, "max_torr": 1.0}
    state.runner = ProcessRunner(state)
    return _LeakRig(cfg, monkeypatch)


async def test_a3_pass_fail_bigleak_cancel(leak_rig, clean_state):
    rig = leak_rig
    lk = await rig.run()
    assert lk.last["Result"] == "PASS", lk.last
    assert 0.5 < float(lk.last["mTorr/min"]) < 2.0                 # 2e-5 Torr/s = 1.2 mTorr/min(실제 시간)
    assert rig.link.sent[0] == A.CMD_ALL_CLOSE and rig.link.sent[-1] == A.CMD_PUMP_START, rig.link.sent
    assert rig.fs.sim.ive_out, "끝난 뒤 IV-E 를 다시 열지 않았다"
    rig.fs.sim.set_fault("chamber_leak", True)
    lk = await rig.run()
    assert lk.last["Result"] == "FAIL" and 100 < float(lk.last["mTorr/min"]) < 140, lk.last
    clean_state["leakcheck"]["max_torr"] = 0.001                    # 큰 누설 — 그 자리에서 멈춤
    lk = await rig.run()
    assert lk.last["Result"] == "FAIL" and "큰 누설" in lk.last["Reason"] and float(lk.last["Measured s"]) < 2
    rig.fs.sim.set_fault("chamber_leak", False)
    clean_state["leakcheck"]["max_torr"] = 1.0
    rig.link.sent.clear()
    lk = await rig.run(cancel=True)
    assert lk.last["Result"] == "CANCEL" and rig.link.sent[-1] == A.CMD_PUMP_START and rig.fs.sim.ive_out
    with open(LC.csv_path(), "rb") as f:
        raw = f.read()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\xef\xbb\xbf") == 1
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    assert rows[0] == LC.HEAD and [r[7] for r in rows[1:]] == ["PASS", "FAIL", "FAIL", "CANCEL"]
    assert [r["Result"] for r in LC.LeakCheck().recent_rows()] == ["CANCEL", "FAIL", "FAIL", "PASS"]
    assert any("누설률 점검 PASS" in m for _lv, m in rig.logs)


def test_a3_refuse_reasons(clean_state):
    cfg = clean_state
    st = _s(1)
    st[A.D_INTERLOCK] = (1 << A.ILK_PUMP) | (1 << A.ILK_VACUUM)
    st[A.D_INPUT0] = 1 << A.IN0_IVE_OPEN
    state.link = FakeLink(st)
    state.runner = ProcessRunner(state)
    lk = LC.LeakCheck()
    assert lk.refuse_reasons(state) == []
    st2 = list(st)
    st2[A.D_STATE] = A.STATE_RUN
    state.link.status = st2
    assert "공정 중입니다" in lk.refuse_reasons(state)
    m1 = next((m for m in cfg.get("mfc") or [] if m.get("full_scale_sccm")), None)
    if m1:
        st3 = list(st)
        st3[A.D_MFC_PV + m1["no"] - 1] = state.conv.mfc[m1["no"]].to_raw(0.05 * float(m1["full_scale_sccm"]))
        state.link.status = st3
        assert any(w.startswith("MFC 유량이 풀스케일 1 % 이상") for w in lk.refuse_reasons(state))
    st4 = list(st)
    if DEV.HAS_O3:
        st4[A.D_AUX_OUT] = 1 << A.AUX_O3_GEN
        state.link.status = st4
        assert any("O3 라인이 켜져 있습니다" in w for w in lk.refuse_reasons(state))
    else:
        st4[A.D_AUX_OUT] = 1 << A.AUX_RF
        state.link.status = st4
        assert "RF 출력이 켜져 있습니다" in lk.refuse_reasons(state)
    st5 = list(st)
    st5[A.D_INPUT0] = 0
    state.link.status = st5
    assert any(w.startswith("IV-E 열림 입력 꺼짐") for w in lk.refuse_reasons(state))
    assert abs(LC.slope_mtorr_min([(0, 1.0), (60, 1.001)]) - 1.0) < 1e-9


async def test_a3_blocks_start_manual_and_operator_cancels(clean_state, monkeypatch):
    from peald import connection as CN
    notes = []

    async def note(msg, level="info", ws=None):
        notes.append(msg)
    monkeypatch.setattr(C, "push_notice", note)
    leak = C._leak()
    fake = types.SimpleNamespace(running=True, cancel_args=None)

    async def caw(why, restore=False, timeout=5.0):
        fake.cancel_args = (why, restore)
        fake.running = False
        return True
    fake.cancel_and_wait = caw
    monkeypatch.setattr("peald.leakcheck.leak", fake)
    state.runner = ProcessRunner(state)
    await C._cmd_process_start({}, None)
    assert any("누설률 점검 중에는 공정을 시작할 수 없습니다" in n for n in notes)
    state.link = None
    assert C._manual_allowed() == (False, "누설률 점검 중에는 수동 밸브를 조작할 수 없습니다")
    await C.handle_command({"cmd": "vent"})
    assert fake.cancel_args and fake.cancel_args[0].startswith("운전자") and fake.cancel_args[1] is False
    assert leak is not None and CN is not None


# ===================== A4. 정비 카운터 =====================
def _unroll(cfg, rec, upto=None):
    vmin = R.prm_value(cfg, "valve_min_ms")
    blocks = rec["blocks"]
    order, n = [], 1
    while n <= len(blocks):
        g, _ = R._group_of(rec, n)
        if g and int(g["from_block"]) == n:
            a, b = int(g["from_block"]), int(g["to_block"])
            for gp in range(1, int(g.get("repeat") or 1) + 1):
                order += [(j, gp) for j in range(a, b + 1)]
            n = b + 1
        else:
            order.append((n, 1))
            n += 1
    acc = {}
    for j, gp in order:
        base = sum(len(b["steps"]) for b in blocks[:j - 1])
        prev = frozenset()
        for c in range(1, int(blocks[j - 1]["repeat"]) + 1):
            for i, st in enumerate(blocks[j - 1]["steps"]):
                cur = frozenset(st["valves"])
                new = cur - prev
                t = R.effective_step_ms(st["time_ms"], bool(new), vmin)
                for v in cur:
                    acc.setdefault(v, [0, 0])[1] += t
                for v in new:
                    acc[v][0] += 1
                prev = cur
                if upto and (j, gp, c, base + i + 1) == (upto["block"], upto.get("group_pass", 1), upto["cycle"],
                                                          upto["step"]):
                    return acc
    return acc


def test_a4_valve_usage_equals_unrolled(cfg):
    cfg = json.loads(json.dumps(cfg))
    cfg.setdefault("params", {})["valve_min_ms"] = 50
    pre, rea = DEV.PRECURSOR_TAGS[0], DEV.REACTANT_TAGS[0]
    steps = [{"name": "p", "time_ms": 30, "valves": [pre], "pause_ok": False},
             {"name": "u", "time_ms": 40, "valves": [], "pause_ok": True},
             {"name": "r", "time_ms": 30, "valves": [rea], "pause_ok": False},
             {"name": "rr", "time_ms": 60, "valves": [rea], "pause_ok": False}]
    rec = _recipe("셈", blocks=3, repeat=4, steps=steps, groups=[{"from_block": 2, "to_block": 3, "repeat": 3}])
    rec["blocks"][0]["steps"] = rec["blocks"][0]["steps"][:3]
    rec["blocks"][0]["repeat"] = 2
    full = R.valve_usage(cfg, rec)
    assert full == _unroll(cfg, rec) and full[pre][0] > 0 and full[rea][1] > 0
    for upto in ({"block": 1, "step": 2, "cycle": 2}, {"block": 1, "step": 1, "cycle": 1},
                 {"block": 2, "step": 5, "cycle": 3, "group_pass": 2},
                 {"block": 3, "step": 10, "cycle": 4, "group_pass": 3},
                 {"block": 3, "step": 8, "cycle": 1, "group_pass": 1}):
        assert R.valve_usage(cfg, rec, upto) == _unroll(cfg, rec, upto), upto
    # 큰 반복도 펼치지 않고 곱으로(1,000,000)
    big = _recipe("큰", blocks=1, repeat=1_000_000, steps=steps)
    t0 = time.monotonic()
    u = R.valve_usage(cfg, big)
    assert time.monotonic() - t0 < 0.5 and u[pre][0] == 1_000_000


def test_a4_count_once_manual_and_hours(clean_state, monkeypatch):
    cfg = clean_state
    mt = M.Maintenance()
    mt.load()
    rec = _recipe("정비", steps=[{"name": "p", "time_ms": 100, "valves": [DEV.RECIPE_VALVES[0]], "pause_ok": False},
                                 {"name": "u", "time_ms": 100, "valves": [], "pause_ok": True}])
    msg = mt.count_run(cfg, "20260101_000000_정비", rec, _s(6, blk=3), None, True)
    tag = DEV.RECIPE_VALVES[0]
    assert "밸브 열림 6 회" in msg and mt.data["valves"][tag]["opens"] == 6     # 블록 2 × 반복 3
    assert mt.count_run(cfg, "20260101_000000_정비", rec, _s(6, blk=3), None, True) == ""
    assert mt.data["valves"][tag]["opens"] == 6, "두 번 셌다"
    assert "레시피(표) 없이" in mt.count_run(cfg, "x", None, _s(6, blk=3), None, True)
    # 중간에 멈춤 — 끝 스냅샷 위치(시퀀서 8 · 블록 1 · 스텝 1 · 사이클 2)까지
    mid = _s(8, blk=1, cyc=2)
    mid[A.D_SEQ_STEP] = 1
    mt.count_run(cfg, "y", rec, mid, None, False)
    assert mt.data["valves"][tag]["opens"] == 6 + 2
    mt.manual_open(tag)
    assert mt.data["valves"][tag]["opens"] == 9
    saved = json.load(open(M.path(), encoding="utf-8"))
    assert saved["valves"][tag]["opens"] == 8 and "y" in saved["counted"]      # 수동 1 회는 다음 저장 때
    # 켜진 시간 — 링크가 붙어 있는 동안만
    clk = [100.0]
    monkeypatch.setattr(M, "time", types.SimpleNamespace(monotonic=lambda: clk[0], strftime=time.strftime))
    st = _s(1)
    st[A.D_AUX_OUT] = 1 << DEV.MAINT_HOURS[0]["aux"]
    link = FakeLink(st)
    mt.tick(link)
    clk[0] += 0.5
    mt.tick(link)
    link.connected = False
    clk[0] += 0.5
    mt.tick(link)
    assert abs(mt.data["hours"][DEV.MAINT_HOURS[0]["key"]]["s"] - 0.5) < 1e-9
    cfg.setdefault("maintenance", {})["valve_opens_warn"] = 5
    assert any(n[1].startswith(f"정비 한계 — {tag} opens 9") for n in mt.notices(cfg))
    src = _src(DEV.KEY, "commands.py")
    assert "maint.manual_open(tag)" in src and 'was.get("open") is False' in src


async def test_a4_reset_needs_admin_pin(clean_state, monkeypatch):
    from peald import admin as AD
    M.maint.load()
    tag = DEV.RECIPE_VALVES[0]
    M.maint.data["valves"][tag]["opens"] = 7
    notes, logs = [], []

    async def note(msg, level="info", ws=None):
        notes.append(msg)

    async def plog(msg, level="info"):
        logs.append(msg)
    monkeypatch.setattr(C, "push_notice", note)
    monkeypatch.setattr(C, "push_log", plog)
    await C._cmd_maint_reset({"key": f"valve:{tag}"}, None)
    assert M.maint.data["valves"][tag]["opens"] == 7 and any("관리자 잠금" in n for n in notes)
    monkeypatch.setattr(AD.admin, "is_admin", lambda ws, token=None: True)
    await C._cmd_maint_reset({"key": f"valve:{tag}"}, None)
    assert M.maint.data["valves"][tag]["opens"] == 0 and M.maint.data["valves"][tag]["reset_at"]
    assert any(m.startswith(f"정비 카운터 리셋 — 이전 값: {tag} 열림 7 회") for m in logs)


# ===================== A5. 공정 중 압력 상한 · 히터 이탈 =====================
def test_a5_press_high_and_heater_dev(clean_state, monkeypatch):
    cfg = clean_state
    clk = [0.0]
    monkeypatch.setattr(P, "time", types.SimpleNamespace(monotonic=lambda: clk[0], time=time.time,
                                                         strftime=time.strftime))
    cfg["process"].update({"press_high_torr": 10, "press_high_s": 5, "press_high_abort": False,
                           "heater_dev_c": 15, "heater_dev_s": 120})
    for h in cfg.get("heaters") or []:
        h["enabled"] = h["ch"] == 1
        h["station"] = 1
    r, _tbl = _active_runner(cfg)
    st = _s(4, st=A.STATE_RUN)
    st[A.D_CVG_RAW] = state.conv.cvg.to_raw(20.0)
    st[A.D_TC_COMM] = 1
    st[A.D_HEATER_PV] = 1000                                       # 100.0 ℃
    link = FakeLink(st)
    link.cmd[A.D_HEATER_SV] = 2000                                  # 설정 200.0 ℃
    link.cmd[A.D_HEATER_POWER] = 1                                  # v0.7.1(W1): 전원이 켜진 채널만 본다
    state.link = link
    logs = []

    def lg(lv, m):
        logs.append(m)
    for t in (0, 4.0):
        clk[0] = t
        r._watch_limits(link.status, lg)
    assert not logs
    clk[0] = 5.1
    r._watch_limits(link.status, lg)
    clk[0] = 6.0
    r._watch_limits(link.status, lg)
    press = [m for m in logs if m.startswith("공정 중 압력 상한")]
    assert len(press) == 1 and len([t for t in r.pending_toasts if "압력 상한" in t[1]]) == 1
    assert any("PC 감시(process.press_high_torr)" in n[1] for n in r.watch_notices)
    paused = list(st)
    paused[A.D_STATE] = A.STATE_PAUSE
    clk[0] = 7.0
    r._watch_limits(paused, lg)
    # v0.7.1(W2): 일시정지는 타이머를 멈추기만 한다 — '해제' 로 끝내지 않고, 재개 뒤 다시 경고 · 토스트하지 않는다
    assert "공정 중 압력 상한 해제" not in logs and [n for n in r.watch_notices if "일시정지 중" in n[1]]
    clk[0] = 8.0
    r._watch_limits(st, lg)
    assert len([m for m in logs if m.startswith("공정 중 압력 상한 —")]) == 1
    assert len([t for t in r.pending_toasts if "압력 상한" in t[1]]) == 1
    low = list(st)
    low[A.D_CVG_RAW] = state.conv.cvg.to_raw(0.1)
    r._watch_limits(low, lg)
    assert "공정 중 압력 상한 해제" in logs and not [n for n in r.watch_notices if "압력" in n[1]]
    clk[0] = 130.0
    r._watch_limits(st, lg)
    clk[0] = 131.0
    r._watch_limits(st, lg)
    dev = [m for m in logs if m.startswith("히터 이탈 — CH1")]
    assert len(dev) == 1, logs
    ok = list(st)
    ok[A.D_HEATER_PV] = 1990
    ok[A.D_CVG_RAW] = state.conv.cvg.to_raw(0.1)
    r._watch_limits(ok, lg)
    assert any(m.startswith("히터 이탈 해제 — CH1") for m in logs) and not r.watch_notices
    # 중단 옵션 — 한 번만 보내고 끝 결과 문구
    cfg["process"]["press_high_abort"] = True
    r2, _ = _active_runner(cfg)
    spawned = []
    monkeypatch.setattr(r2, "_spawn", lambda coro: (spawned.append(1), coro.close()))
    for t in (200.0, 206.0, 207.0):
        clk[0] = t
        r2._watch_limits(st, lg)
    # v0.7.1(W3): 이유는 즉시 중단을 실제로 보낼 때(_press_abort 안)만 세운다 — 띄우기만 한 지금은 비어 있다
    assert spawned == [1] and r2._abort_why == ""
    assert P.judge_end(_s(8, blk=1), None, _recipe(), abort_sent=True, abort_why="압력 상한 — PC 가 즉시 중단") == \
        "중단 (압력 상한 — PC 가 즉시 중단)"
    # 0 = 끔
    cfg["process"].update({"press_high_torr": 0, "heater_dev_c": 0})
    r3, _ = _active_runner(cfg)
    for t in (300.0, 500.0):
        clk[0] = t
        r3._watch_limits(st, lg)
    assert not r3.watch_notices and not r3.pending_toasts


def test_a5_settings_registered_and_validated(cfg):
    from peald import settings as S
    from peald import config as CF
    for k in ("process.press_high_torr", "process.press_high_s", "process.press_high_abort", "process.heater_dev_c",
              "process.heater_dev_s", "leakcheck.settle_s", "leakcheck.measure_s", "leakcheck.limit_mtorr_min",
              "leakcheck.max_torr", "maintenance.valve_opens_warn", "maintenance.pump_hours_warn"):
        assert k in S.FIELDS, k
    assert ("maintenance.rf_hours_warn" in S.FIELDS) == DEV.HAS_RF
    assert ("maintenance.o3_hours_warn" in S.FIELDS) == DEV.HAS_O3
    assert S.FIELDS["process.press_high_torr"]["label"].startswith("PC Watch")
    ex = json.load(open(os.path.join(ROOT, "config", "config.example.json"), encoding="utf-8"))
    assert ex["leakcheck"]["measure_s"] == 120 and ex["process"]["press_high_torr"] == 10
    assert ex["maintenance"]["pump_hours_warn"] == 0
    bad = json.loads(json.dumps(cfg))
    bad["leakcheck"]["measure_s"] = 1
    bad["process"]["press_high_s"] = -1
    bad["maintenance"]["pump_hours_warn"] = -3
    msgs = [m for lv, m in CF.validate(bad) if lv == "err"]
    assert any("leakcheck.measure_s" in m for m in msgs) and any("process.press_high_s" in m for m in msgs)
    assert any("maintenance.pump_hours_warn" in m for m in msgs)


# ===================== B1. PRM 표 Match 칸 =====================
def test_b1_prm_table_match_column_visible():
    css = _src("frontend", "css", "style.css")
    assert ".prmtbl td.l, .prmtbl th.l { white-space: normal; overflow-wrap: anywhere;" in css
    assert ".prmtbl td:last-child, .prmtbl th:last-child { white-space: nowrap;" in css
    assert ".tblx { max-width: 100%; overflow-x: auto; }" in css
    setup = _src("frontend", "js", "views", "setup.js")
    assert '<div class="tblx" style="margin-top:8px"><table class="tbl prmtbl">' in setup
    assert "'<tr' + (r.match === false ? ' class=\"hl\"' : '') + '><td class=\"l\">'" in setup
    # 마지막 칸(Match)이 가장 넓은 칩 글('✕ Mismatch')보다 좁아지지 않게 — 이름 칸만 줄바꿈된다
    assert "white-space: nowrap; width: 1%" in css


def test_p8_texts():
    cf = _src(DEV.KEY, "config.py")
    assert "PLC 가 PC 끊김 트립을 걸 수 있습니다" in cf and "PC 끊김으로 공정을 세울 수 있습니다" not in cf
    pl = _src(DEV.KEY, "plclink.py")
    assert "PC 가 스스로 리셋을 보내지 않는다" in pl and "PC 끊김으로 보고 안전 정지한다" not in pl
