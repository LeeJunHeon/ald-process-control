"""v0.7.1 — 트립 남은 끝 판정 · 이어받기 같은 파일 조건 · 오프라인 판정 기회 · 누설률 점검 격리/명령 11 응답/게이지 고정 ·
정비 파일 보호 · 그룹 회차 · 히터 이탈 전원 채널 · 알람 창 확인 문구 · 멈춤 중 PC 쪽 값 · PRM 표 · 작은 것.

★ v0.7.0 시험(test_v070)의 도구(가짜 링크 · 가짜 시계 누설률 점검 틀)를 그대로 쓴다.
"""
import os
import re
import json
import time
import types
import asyncio

import pytest

from powderald import addresses as A
from powderald import commands as C
from powderald import config as CF
from powderald import datalog as DL
from powderald import device as DEV
from powderald import leakcheck as LC
from powderald import logview
from powderald import maintenance as M
from powderald import paths
from powderald import process as P
from powderald import recipe as R
from powderald import version
from powderald.convert import Converters
from powderald.datalog import DataLog
from powderald.process import ProcessRunner
from powderald.state import state

from test_v0411 import _node
from test_v054 import _fn
from test_v070 import (FakeLink, _s, _recipe, clean_state, leak_rig, _active_runner, _closed_log,  # noqa: F401
                       ROOT, _src)

B6 = 1 << A.ALM0_PC_LINK
TRIP = "PC Link Lost (트립 남음)"


def test_version():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 7, 1)


# ===================== P1. 트립만 남은 안전 정지가 끝의 진짜 원인을 가리지 않는다 =====================
@pytest.mark.parametrize("trip", [True, False])
def test_p1_trip_only_keeps_real_cause(trip, clean_state):
    cfg = clean_state
    rec = _recipe("트립끝", blocks=3)
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    st = A.STATE_SAFE_STOP if trip else A.STATE_IDLE
    a0 = B6 if trip else 0
    tail = f" · 끝난 뒤 안전 정지: {TRIP}" if trip else ""
    # 운전자 중단 · 압력 상한 중단 · 결과 모름
    assert P.judge_end(_s(8, st=st, blk=2, a0=a0), tbl, rec, abort_sent=True) == "중단 (운전자 중단)" + tail
    assert P.judge_end(_s(8, st=st, blk=2, a0=a0), tbl, rec, abort_sent=True,
                       abort_why="압력 상한 — PC 가 즉시 중단") == "중단 (압력 상한 — PC 가 즉시 중단)" + tail
    assert P.judge_end(_s(8, st=st, blk=2, a0=a0), tbl, rec, abort_unknown=True) == \
        "중단 (운전자 중단 — 결과 확인 안 됨)" + tail
    # b13(레시피 표 오류)
    w = tbl["words"]
    b2 = A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_STRIDE - A.RCP_SUM_BASE
    w[b2 + A.RCP_BLOCK_LAST] = 0
    out = P.judge_end(_s(8, st=st, blk=2, a0=a0 | (1 << A.ALM0_RECIPE)), tbl, rec, b13_rose=True)
    assert out == "중단 (레시피 표 오류 — 블록 2 적재 거절: 끝 스텝 < 첫 스텝)" + tail, out
    # 정상 종료는 지금처럼(꼬리 없이 괄호 안)
    assert P.judge_end(_s(6, st=st, blk=4, a0=a0), tbl, rec) == \
        (f"정상 종료 (끝난 뒤 안전 정지: {TRIP})" if trip else "정상 종료")
    for res in (P.judge_end(_s(8, st=st, blk=2, a0=a0), tbl, rec, abort_sent=True),):
        assert logview.result_level(res) == "warn" and P.result_level(res + P.OFF_TAIL) == "warn"


def test_p1_critical_alarm_still_wins_and_b13_wait(clean_state):
    cfg = clean_state
    rec = _recipe("중대")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    emo = (1 << A.ALM0_EMO) | B6
    assert P.judge_end(_s(8, st=A.STATE_SAFE_STOP, blk=1, a0=emo), tbl, rec, abort_sent=True).startswith(
        "중단 (안전 정지 — EMO")
    assert P.trip_only(_s(8, st=A.STATE_SAFE_STOP, a0=B6)) and not P.trip_only(_s(8, st=A.STATE_SAFE_STOP, a0=emo))
    r = ProcessRunner(state)
    r._b13_pre = False
    assert r._needs_b13_wait(_s(8, st=A.STATE_SAFE_STOP, a0=B6)), "트립만 남은 안전 정지에서 b13 대기를 건너뛰었다"
    assert not r._needs_b13_wait(_s(8, st=A.STATE_SAFE_STOP, a0=emo))
    # 표 없이 이어받은 갈래(사이클 후 정지 · 표 모름)도 트립만이면 안전 정지로 보지 않는다
    out = P.judge_end(_s(6, st=A.STATE_SAFE_STOP, blk=1, a0=B6), None, None, stop_reserved=True)
    assert out == f"사이클 후 정지 (블록 1 · 사이클 1 · 표 모름) · 끝난 뒤 안전 정지: {TRIP}", out


async def test_p1_offline_judgement_uses_same_rule(clean_state):
    cfg = clean_state
    rec = _recipe("꺼짐중단")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    name = _closed_log(cfg, rec, tbl)
    w = list(tbl["words"])
    b2 = A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_STRIDE - A.RCP_SUM_BASE
    state.link = FakeLink(_s(8, st=A.STATE_SAFE_STOP, blk=2, a0=B6 | (1 << A.ALM0_RECIPE)), words=w)
    w[b2 + A.RCP_BLOCK_LAST] = 0           # 표 머리(번호 · 합계)는 같고 블록 2 가 걸린다

    async def plog(*a):
        return None
    res = await ProcessRunner(state).check_offline_end(plog)
    assert res.startswith("중단 (레시피 표 오류 — 블록 2 적재 거절") and f"끝난 뒤 안전 정지: {TRIP}" in res, res
    assert json.load(open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), encoding="utf-8"))["result"] == res


# ===================== P2. 이어받기 같은 파일 조건 =====================
def test_p2_resume_window_and_flag(clean_state):
    cfg = clean_state
    t0 = time.mktime(time.strptime("2026-01-01 00:00:00", "%Y-%m-%d %H:%M:%S"))
    meta = {"started": "2026-01-01 00:00:00", "estimated_ms": 4 * 3600 * 1000}
    assert DL.resume_window_ok(meta, t0 + 4 * 3600 + 2 * 3600 - 1)          # 예상 4 h + 50 %(2 h)
    assert not DL.resume_window_ok(meta, t0 + 4 * 3600 + 2 * 3600 + 1)
    short = {"started": "2026-01-01 00:00:00", "estimated_ms": 60_000}
    assert DL.resume_window_ok(short, t0 + 60 + 3599) and not DL.resume_window_ok(short, t0 + 60 + 3601)  # 최소 1 시간
    assert not DL.resume_window_ok({"started": ""}, t0)
    rec = _recipe("조건")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    name = _closed_log(cfg, rec, tbl)
    state.link = None
    time.sleep(1.05)                                             # 이름(초 단위)이 겹치지 않게
    dl = DataLog(state)                                          # 이 실행에서 시작 명령을 보냈다 → 새 파일
    dl.start("조건", rec, {"number": tbl["number"], "words": tbl["words"]}, 1000, adopted=True, may_resume=False)
    assert dl.name != name and json.load(open(dl.meta_path, encoding="utf-8")).get("adopted") is True
    dl.close()
    src = _src(DEV.KEY, "process.py")
    assert "self.start_sent = True" in src and "self.may_resume = self.adopt_count == 1 and not self.start_sent" in src
    assert 'may_resume=bool(getattr(runner, "may_resume", False))' in _src(DEV.KEY, "loops.py")


def test_p2_old_meta_outside_window_gets_new_file(clean_state):
    cfg = clean_state
    rec = _recipe("오래됨")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    name = _closed_log(cfg, rec, tbl)
    mp = os.path.join(paths.DATALOG_DIR, name + ".recipe.json")
    meta = json.load(open(mp, encoding="utf-8"))
    meta["started"] = "2020-01-01 00:00:00"                     # 앞 공정 A — 오래 전 시작, 판정 없이 남음
    json.dump(meta, open(mp, "w", encoding="utf-8"))
    time.sleep(1.05)
    dl = DataLog(state)
    dl.start("오래됨", rec, {"number": tbl["number"], "words": tbl["words"]}, 1000, adopted=True, may_resume=True)
    assert dl.name != name and json.load(open(dl.meta_path, encoding="utf-8"))["adopted"] is True
    dl.close()


# ===================== P3 · P4 · P5 · P6 =====================
async def test_p3_offline_chance_kept_while_plc_runs(clean_state):
    cfg = clean_state
    rec = _recipe("기회")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    _closed_log(cfg, rec, tbl)
    link = FakeLink(_s(4, st=A.STATE_RUN), words=tbl["words"])
    state.link = link
    r = ProcessRunner(state)

    async def plog(*a):
        return None
    assert await r.check_offline_end(plog) == "" and r.offline_checked is False    # 공정 중 — 기회를 쓰지 않는다
    link.status = _s(6, blk=3)
    assert (await r.check_offline_end(plog)).startswith("정상 종료") and r.offline_checked
    src = _src(DEV.KEY, "process.py")
    assert "self.offline_checked = False    # v0.7.1(P3)" in src


def test_p4_end_seen_before_loss_is_not_lost_end(clean_state):
    cfg = clean_state
    r, _tbl = _active_runner(cfg)
    link = FakeLink(_s(4, st=A.STATE_RUN))
    state.link = link
    r.tick(lambda *a: None)
    r._b13_wait = (time.monotonic() + 100, (_s(8, blk=1), time.time(), lambda *a: None))   # 끝을 이미 봤다
    link.connected = False
    r.tick(lambda *a: None)
    link.connected = True
    link.status = _s(8, blk=1)
    r._link_back(link.status, False)
    assert r._ended_while_lost is False


def test_p5_crit_text_from_snapshot(clean_state):
    r = ProcessRunner(state)
    state.alarms.clear_all()
    assert r._crit_text(_s(8, st=A.STATE_SAFE_STOP, a0=(1 << A.ALM0_EMO) | B6)) == "EMO"
    assert r._crit_text(_s(8, st=A.STATE_SAFE_STOP, a0=B6)) == TRIP


def test_p6_trip_notice_names_real_cause(clean_state):
    state.link = FakeLink(_s(1, st=A.STATE_SAFE_STOP, a0=B6 | (1 << A.ALM0_EMO)))
    n = [x for x in state.pc_notices(True) if "PC 끊김" in x[1]]
    assert n and "원인은 EMO" in n[0][1] and "[Alarm Reset] 으로 풀립니다" not in n[0][1], n
    state.link = FakeLink(_s(1, st=A.STATE_SAFE_STOP, a0=B6))
    assert any(x[1].endswith("[Alarm Reset] 으로 풀립니다.") for x in state.pc_notices(True))


# ===================== L. 누설률 점검 =====================
class ForcedLink(FakeLink):
    """상태 영역 일부를 덮어쓰는 링크(격리 깨짐 · 게이지 범위 밖 · 명령 11 결과 모름)."""
    force = None            # {주소: (OR 할 비트, 값)} — 값이 있으면 그 값
    none_for = ()           # 이 명령은 결과 모름(None)으로 돌려준다
    run_none = True         # 결과 모름일 때 PLC 가 실제로 받았나

    @property
    def status(self):
        s = list(self.sim.reg[:A.STATUS_COUNT])
        for addr, (orbits, val) in (self.force or {}).items():
            s[addr] = val if val is not None else (s[addr] | orbits)
        return s

    async def send_command(self, code, args=None):
        self.sent.append(code)
        if code in self.none_for:
            if self.run_none:
                self.fs.cmd(code)
            return None, "응답 없음 — PLC 가 받았을 수 있습니다"
        r = self.fs.cmd(code)
        return r, "처리됨" if r == A.RESULT_OK else f"결과 {r}"


def _forced(rig):
    rig.link.__class__ = ForcedLink
    rig.link.force = {}
    return rig.link


async def _run_with(rig, phase=None, action=None):
    lk = LC.LeakCheck()
    ok, msg = lk.start(state, rig.plog, rig.plog)
    assert ok, msg
    while lk.running:
        if action and lk.phase == phase:
            action()
            action = None
        await asyncio.sleep(0)
    return lk


async def test_l1_isolation_break_aborts(leak_rig):
    link = _forced(leak_rig)
    lk = await _run_with(leak_rig, "measure", lambda: link.force.update({A.D_AUX_OUT: (1 << A.AUX_VV, None)}))
    assert lk.last["Result"] == "ABORT" and "VV(벤트) 출력" in lk.last["Reason"], lk.last
    link.force = {}
    lk = await _run_with(leak_rig, "settle", lambda: link.force.update({A.D_INPUT0: (0, link.sim.reg[A.D_INPUT0]
                                                                                   & ~(1 << A.IN0_IVE_CLOSE))}))
    assert lk.last["Result"] == "ABORT" and "IV-E 닫힘 입력" in lk.last["Reason"], lk.last
    link.force = {}


async def test_l2_all_close_result_unknown(leak_rig):
    link = _forced(leak_rig)
    link.none_for = (A.CMD_ALL_CLOSE,)
    link.run_none = True                       # 받았다 — 닫힘 입력이 켜지면 그대로 잰다
    lk = await _run_with(leak_rig)
    assert lk.last["Result"] == "PASS", lk.last
    assert any("결과는 몰랐지만" in m for _lv, m in leak_rig.logs)
    link.run_none = False                      # 못 받았다 — 닫히지 않음 → ABORT · 명령 8 로 되돌리기 · 알림
    link.sent.clear()
    lk = await _run_with(leak_rig)
    assert lk.last["Result"] == "ABORT" and "결과 모름" in lk.last["Reason"], lk.last
    assert link.sent[-1] == A.CMD_PUMP_START
    assert any("모두 닫기 결과를 몰라 멈춥니다" in m for _lv, m in leak_rig.logs)


async def test_l3_gauge_fixed_and_range(leak_rig):
    link = _forced(leak_rig)
    raw_max = state.conv.raw_max
    lk = await _run_with(leak_rig, "measure", lambda: link.force.update({A.D_CVG_RAW: (0, raw_max)}))
    assert lk.last["Result"] == "FAIL" and "범위 위로" in lk.last["Reason"] and lk.last["Gauge"] == "CVG", lk.last
    link.force = {}
    lk = await _run_with(leak_rig, "measure", lambda: link.force.update({A.D_CVG_RAW: (0, 0)}))
    assert lk.last["Result"] == "ABORT" and "범위 밖(아래)" in lk.last["Reason"], lk.last
    link.force = {}
    src = _src(DEV.KEY, "leakcheck.py")
    assert "g = pick_gauge(state, link.status)" in src and "read_gauge(state, link.status, g)" in src


async def test_l4_cancel_before_close_no_closed_text(clean_state, monkeypatch):
    lk = LC.LeakCheck()

    async def boom(*a, **k):
        raise asyncio.CancelledError()
    monkeypatch.setattr(lk, "_steps", boom)
    lk.task = None
    with pytest.raises(asyncio.CancelledError):
        await lk._run(state, None, None)
    assert lk.last["Result"] == "ABORT" and "닫힌 채" not in lk.last["Reason"] and "프로그램 종료" in lk.last["Reason"]


async def test_l6_operator_command_refused_after_cancel(clean_state, monkeypatch):
    notes = []

    async def note(msg, level="info", ws=None):
        notes.append(msg)

    async def refused(code, ws, *a, **k):
        return A.RESULT_INTERLOCK
    fake = types.SimpleNamespace(running=True)

    async def caw(why, restore=False, timeout=3.0):
        fake.running = False
        return True
    fake.cancel_and_wait = caw
    monkeypatch.setattr("powderald.leakcheck.leak", fake)
    monkeypatch.setattr(C, "push_notice", note)
    monkeypatch.setattr(C, "_send_plc", refused)
    state.runner = ProcessRunner(state)
    await C.handle_command({"cmd": "vent"})
    assert any("[Pumping Start] 로 IV-E 를 다시 여세요" in n for n in notes), notes


def test_l7_start_checks_and_button(clean_state, monkeypatch):
    st = _s(1)
    st[A.D_INTERLOCK] = (1 << A.ILK_PUMP) | (1 << A.ILK_VACUUM) | (1 << A.ILK_VALVE_OK)
    state.link = FakeLink(st)
    r = ProcessRunner(state)
    state.runner = r
    task = types.SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(LC.leak, "task", task)
    ok, blocking, checks = r.can_start()
    assert any(c["key"] == "leakcheck" and c["label"] == "Leak Check 진행 중" for c in blocking)
    monkeypatch.setattr(LC.leak, "task", None)
    assert not any(c["key"] == "leakcheck" for c in r.start_checks())
    lk = LC.LeakCheck()
    assert lk.blocked(state) == ""
    state.link.status = _s(4, st=A.STATE_RUN)
    assert lk.blocked(state) == "공정 중입니다"
    state.link.connected = False
    assert lk.blocked(state) == "PLC 에 연결되어 있지 않습니다"
    assert lk.status(state.cfg, state)["blocked"]
    setup = _src("frontend", "js", "views", "setup.js")
    assert "(lc.blocked || '')" in setup and "st.title = why" in setup


# ===================== M. 정비 카운터 =====================
def test_m1_group_pass_from_last_position(clean_state):
    cfg = clean_state
    rec = R.empty_recipe("그룹재시작")
    b = R.empty_block("b1")
    b["repeat"] = 1
    b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    pre, rea = DEV.PRECURSOR_TAGS[0], DEV.REACTANT_TAGS[0]
    b["steps"] = [{"name": "p", "time_ms": 100, "valves": [pre], "pause_ok": False},
                  {"name": "r", "time_ms": 100, "valves": [rea], "pause_ok": False}]
    if DEV.HAS_RF:
        b["rf_w"] = 0.0
        for x in b["steps"]:
            x["rf"] = False
    rec["blocks"] = [b]
    rec["groups"] = [{"from_block": 1, "to_block": 1, "repeat": 10}]
    mt = M.Maintenance()
    mt.load()
    # 6 회차 스텝 2 에서 PLC 재시작(끝 스냅샷 시퀀서 0 · 블록 0)
    mt.count_run(cfg, "재시작", rec, _s(0, blk=0, gp=0), (1, 2, 1, 6), False, last_seq=4)
    assert mt.data["valves"][pre]["opens"] == 6 and mt.data["valves"][rea]["opens"] == 6
    assert mt.data["valves"][pre]["open_ms"] == 1200 and mt.data["valves"][rea]["open_ms"] == 1200
    # 러너가 마지막 위치에 그룹 회차를 담는다 · where_text
    r, _tbl = _active_runner(cfg, rec)
    s = _s(4, st=A.STATE_RUN, blk=1, gp=6)
    s[A.D_SEQ_STEP] = 2
    state.link = FakeLink(s)
    r.tick(lambda *a: None)
    assert r._last_pos == (1, 2, 1, 6) and r._last_seq == 4 and r.where_text().endswith("그룹 회차 6")


def test_m2_unreadable_file_is_kept(clean_state, monkeypatch):
    p = M.path()
    with open(p, "w", encoding="utf-8") as f:
        f.write("{ 깨짐")
    mt = M.Maintenance()
    mt.load()
    bad = [n for n in os.listdir(paths.DATA_DIR) if n.startswith("maintenance.bad-")]
    assert len(bad) == 1 and not os.path.exists(p) and "정비 카운터 파일을 읽지 못했습니다" in mt.problem
    assert open(os.path.join(paths.DATA_DIR, bad[0]), encoding="utf-8").read() == "{ 깨짐"
    assert mt.notices({})[0] == ("err", mt.problem)
    mt.manual_open(DEV.RECIPE_VALVES[0])
    mt.save(force=True)
    assert json.load(open(p, encoding="utf-8"))["valves"][DEV.RECIPE_VALVES[0]]["opens"] == 1
    # 옮기지도 못하면(권한) 덮어쓰지 않는다
    with open(p, "w", encoding="utf-8") as f:
        f.write("[1, 2")

    def no_move(a, b):
        raise PermissionError("거부")
    monkeypatch.setattr(M.os, "replace", no_move)
    mt2 = M.Maintenance()
    mt2.load()
    mt2.manual_open(DEV.RECIPE_VALVES[0])
    mt2.save(force=True)
    assert open(p, encoding="utf-8").read() == "[1, 2" and "저장하지 않습니다" in mt2.problem
    assert 'state.add_log("err", maint.problem)' in _src(DEV.KEY, "server.py")


def test_m3_prep_block_not_counted_and_words(clean_state):
    cfg = clean_state
    pre = DEV.PRECURSOR_TAGS[0]
    rec = _recipe("준비", blocks=2, repeat=2, steps=[{"name": "p", "time_ms": 300, "valves": [pre], "pause_ok": False},
                                                   {"name": "u", "time_ms": 300, "valves": [], "pause_ok": True}])
    mt = M.Maintenance()
    mt.load()
    s = _s(8, blk=2)
    s[A.D_SEQ_STEP] = 3
    mt.count_run(cfg, "a", rec, s, None, False, last_seq=3)             # 블록 2 준비 중 중단 — 블록 1 만
    assert mt.data["valves"][pre]["opens"] == 2
    mt.count_run(cfg, "b", rec, _s(8, blk=1), None, False, last_seq=None)   # 시작 때 표 오류 — 0
    assert mt.data["valves"][pre]["opens"] == 2
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    msg = mt.count_run(cfg, "c", None, _s(6, blk=3), None, True, words=tbl["words"])     # '(PLC 번호 N)' 이어받기
    assert "4 회" in msg and mt.data["valves"][pre]["opens"] == 6
    fr = R.recipe_from_words(tbl["words"])
    assert R.valve_usage(cfg, fr) == R.valve_usage(cfg, rec)


# ===================== W. 공정 중 경고 =====================
def test_w1_heater_dev_only_powered_channels(clean_state, monkeypatch):
    cfg = clean_state
    clk = [0.0]
    monkeypatch.setattr(P, "time", types.SimpleNamespace(monotonic=lambda: clk[0], time=time.time,
                                                         strftime=time.strftime))
    for h in cfg.get("heaters") or []:
        h["enabled"] = h["ch"] == 1
        h["station"] = 1
    r, _tbl = _active_runner(cfg)
    st = _s(4, st=A.STATE_RUN)
    st[A.D_TC_COMM] = 1
    st[A.D_HEATER_PV] = 250
    link = FakeLink(st)
    link.cmd[A.D_HEATER_SV] = 2000
    link.cmd[A.D_HEATER_POWER] = 0                          # 사용으로만 두고 전원은 꺼 둠
    state.link = link
    logs = []
    for t in (0.0, 500.0):
        clk[0] = t
        r._watch_limits(st, lambda lv, m: logs.append(m))
    assert not [m for m in logs if "히터 이탈" in m] and not r.pending_toasts


async def test_w3_abort_why_only_when_sent(clean_state):
    r = ProcessRunner(state)
    logs = []
    state.link = FakeLink(_s(1))                             # 공정이 이미 끝남 — 보내지 않는다
    await r._press_abort(lambda lv, m: logs.append(m))
    assert r._abort_why == "" and any("보내지 않았습니다" in m for m in logs)

    class Refuse(FakeLink):
        async def send_command(self, code, args=None):
            return A.RESULT_STATE, "결과 2(상태)"
    state.link = Refuse(_s(4, st=A.STATE_RUN))
    await r._press_abort(lambda lv, m: logs.append(m))
    assert r._abort_why == "" and any("처리되지 않았습니다" in m for m in logs)
    src = _src(DEV.KEY, "process.py")
    assert "self._press_task = self._spawn(" in src and src.count('self._abort_why = ""                # v0.7.1(W3)') == 2


def test_w4_config_messages(cfg):
    bad = json.loads(json.dumps(cfg))
    bad["process"].update({"press_high_s": -1, "heater_dev_c": -1})
    bad["leakcheck"]["measure_s"] = LC.MEASURE_MIN_S - 1
    msgs = [m for lv, m in CF.validate(bad) if lv == "err"]
    assert any("process.press_high_s" in m and "(0 = 넘는 즉시)" in m for m in msgs)
    assert any("process.heater_dev_c" in m and "(0 = 끔)" in m for m in msgs)
    assert any("leakcheck.measure_s" in m for m in msgs)
    assert LC.params({"leakcheck": {"measure_s": 1}})["measure_s"] == LC.MEASURE_MIN_S
    ok = json.loads(json.dumps(cfg))
    ok["leakcheck"]["measure_s"] = LC.MEASURE_MIN_S
    assert not [m for lv, m in CF.validate(ok) if "leakcheck.measure_s" in m]


# ===================== U. 화면 =====================
def test_u1_alarm_modal_shows_whole_check():
    core = _src("frontend", "js", "core.js")
    defs = {f"A0-{d['bit']:02d}": {"check": d["check"]} for d in DEV.ALARMS0}
    script = r'''
      var lastState = { live: { plc: { connected: true }, alarms: [{ code: 'A0-14', crit: true, since: '10:00:00', name: 'Gas Leak' }] },
                        structure: { alarm_defs: %s } };
      var tbl = {};
      const bind = () => tbl, html = (e, h) => { e.innerHTML = h; }, chip = t => t, T = k => k,
            esc = s => String(s), downWhy = () => '', canOperate = () => true,
            d = { querySelectorAll: () => [] };
      %s
      renderAlarmModal();
      console.log(JSON.stringify(tbl.innerHTML));''' % (json.dumps(defs), _fn(core, "renderAlarmModal"))
    out = _node(script)
    gas = next(d for d in DEV.ALARMS0 if d["bit"] == A.ALM0_LEAK)["check"]
    assert "원인을 찾기 전에 다시 시작하지 않는다" in gas and ("확인: " + gas) in out, out


def test_u2_stalled_keeps_pc_side_values():
    core = _src("frontend", "js", "core.js")
    out = _node(_fn(core, "blankStalled") + r'''
      const t = { plc: { connected: true, hb_stalled: true, pc_trip: true, prm: [] }, leakcheck: { recent: [1] },
                  maintenance: [{ key: 'valve:PV-1' }], pc_notices: [['warn', 'trip', 'plc'], ['warn', '정비 한계']] };
      const b = blankStalled(t);
      console.log(JSON.stringify([b.leakcheck, b.maintenance, b.pc_notices, b.plc.pc_trip]));''')
    assert out == [{"recent": [1]}, [{"key": "valve:PV-1"}], [["warn", "정비 한계"]], False]


def test_u3_prm_table_fits_with_mismatch():
    """설정 탭 오른쪽 칸(960 px 캔버스) 안에 Mismatch 줄이 있는 PRM 표가 들어가는지 — 글자 폭을 어림해 잰다."""
    css = _src("frontend", "css", "style.css")
    assert ".prmtbl td, .prmtbl th { padding-left: 4px; padding-right: 4px; }" in css
    assert ".panel-body > .tblx, .panel-body .tblx" not in css                  # 겹친 규칙 정리
    pad = 4 * 2
    # #app 960 − .view 좌우 16×2 − 두 칸 사이 6 − 세로 스크롤바 17 → 반, − 패널 테두리 2 − panel-body 8×2
    avail = (960 - 32 - 6 - 17) / 2 - 2 - 16
    sans, mono, small = 13 * 0.62, 13 * 0.6, 12 * 0.6
    chip = len("✕ Mismatch") * 13 * 0.66 + 6 * 2 + 2                         # 굵은 글 · 칩 여백 · 테두리
    name = 6 * 13                                                             # min-width 6em(줄바꿈)
    cols = [name, len("0.05") * mono, len("16000") * mono, max(len("16000") * mono, len("0.050 Torr") * small), chip]
    need = sum(cols) + pad * len(cols)
    assert need + 20 <= avail, (need, avail)


def test_u4_u5_u6_small():
    main = _src("frontend", "js", "views", "main.js")
    assert "core.chip('Trip', 'warn'" in main and "t.state.code === ST_SAFE_STOP" in main and "code === 6" not in main
    dl = _src("frontend", "js", "views", "datalog.js")
    assert 'dl-who" title="' in dl and 'dl-memo" title="' in dl
    css = _src("frontend", "css", "style.css")
    assert "-webkit-line-clamp: 2" in css and ".dl-item .b .dl-who {" in css and "text-overflow: ellipsis" in css
    html = _src("frontend", "index.html")
    assert 'placeholder="Recipe / Operator / Sample"' in html
    setup = _src("frontend", "js", "views", "setup.js")
    assert "core.esc(b.dataset.mtlabel || key)" in setup
    alarm = _src("frontend", "js", "views", "alarm.js")
    assert "delete openCode[c]" in alarm
    assert "maint_hours" not in state.structure()
    assert "_last_op = None" in _src(DEV.KEY, "commands.py")


def test_e_scenarios_present():
    e2e = _src("test", "e2e_process.py")
    assert "stopped = await op.wait(lambda: self.state_code() == A.STATE_SAFE_STOP, 5)" in e2e
    assert '"중단 (운전자 중단) · 끝난 뒤 안전 정지: PC Link Lost (트립 남음)"' in e2e
    assert "이어받은 공정이 트립을 남긴 채 끝남" in e2e
    assert re.search(r"long_recipe\(name, 40, 100\)", e2e)
