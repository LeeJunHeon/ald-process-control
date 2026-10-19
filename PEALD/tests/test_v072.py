"""v0.7.2 — 정비 카운터 실제 경로 · 끝 결과 색 · 끝 판정 한 스캔 틈 · 이어받기 직후 오프라인 판정 · 압력 상한 중단 시험 ·
Leak Check 단추 조건 · 작은 것.

★ 정비 카운터는 Maintenance().count_run 을 직접 부르지 않고 실제 경로(러너 tick → _finish · check_offline_end)로 본다.
  v0.7.0 · v0.7.1 시험(test_v070 · test_v071)의 도구를 그대로 쓴다.
"""
import os
import json
import time
import types
import asyncio

import pytest

from peald import addresses as A
from peald import device as DEV
from peald import leakcheck as LC
from peald import maintenance as M
from peald import paths
from peald import process as P
from peald import recipe as R
from peald import version
from peald.convert import Converters
from peald.process import ProcessRunner
from peald.state import state

from test_v0411 import _node, JS
from test_v054 import _fn
from test_v070 import (FakeLink, _s, _recipe, clean_state, leak_rig, _active_runner, _closed_log,  # noqa: F401
                       _src)
from test_v071 import ForcedLink, _forced, _run_with  # noqa: F401

B6 = 1 << A.ALM0_PC_LINK
B13 = 1 << A.ALM0_RECIPE
TRIP = "PC Link Lost (트립 남음)"
PRE = DEV.PRECURSOR_TAGS[0]
REA = DEV.REACTANT_TAGS[0]


def test_version():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 7, 2)


# ===================== 도구 =====================
def _vrec(name="정비경로", blocks=2, repeat=3, groups=None):
    """블록마다 PRE · REA 를 여는 레시피(정비 카운터가 셀 것이 있게)."""
    return _recipe(name, blocks=blocks, repeat=repeat, groups=groups,
                   steps=[{"name": "p", "time_ms": 100, "valves": [PRE], "pause_ok": False},
                          {"name": "r", "time_ms": 100, "valves": [REA], "pause_ok": False}])


def _opens():
    return {t: v["opens"] for t, v in M.maint.data["valves"].items()}


def _delta(before):
    now = _opens()
    return {t: now[t] - before.get(t, 0) for t in now if now[t] - before.get(t, 0)}


def _usage(cfg, rec, upto=None):
    return {t: n for t, (n, _ms) in R.valve_usage(cfg, rec, upto).items() if n}


class _Clock:
    """process 모듈 시계(b13 1 s 대기 · 압력 상한 타이머)."""

    def __init__(self, monkeypatch):
        self.t = 1000.0
        monkeypatch.setattr(P, "time", types.SimpleNamespace(monotonic=lambda: self.t, time=time.time,
                                                             strftime=time.strftime))


def _run_to_end(r, link, run_states, end, clk=None, logs=None):
    """공정 중 상태들을 tick 으로 보인 뒤 끝 스냅샷 — b13 대기면 시계를 1.1 s 밀고 한 번 더."""
    logs = [] if logs is None else logs
    log = lambda lv, m: logs.append((lv, m))        # noqa: E731
    for s in run_states:
        link.status = s
        r.tick(log)
    link.status = end
    r.tick(log)
    if r._b13_wait is not None and clk is not None:
        clk.t += 1.1
        r.tick(log)
    return logs


def _pos(seq, blk, step, cyc=1, gp=1, st=A.STATE_RUN):
    s = _s(seq, st=st, blk=blk, cyc=cyc, gp=gp)
    s[A.D_SEQ_STEP] = step
    return s


# ===================== 1. 정비 카운터 — 실제 경로 =====================
def test_1_module_function_passes_all_args(clean_state):
    cfg = clean_state
    M.maint.load()
    rec = _vrec()
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    # 모듈 함수가 words · last_seq · result 를 그대로 넘긴다(v0.7.1 은 TypeError)
    msg = M.count_run(cfg, "k1", None, _s(6, blk=3), None, True, words=tbl["words"], last_seq=4, result="정상 종료")
    assert msg.startswith("정비 카운터: 밸브 열림"), msg


def test_1_finish_normal_end_counts(clean_state, monkeypatch):
    cfg = clean_state
    M.maint.load()
    b0 = _opens()
    rec = _vrec()
    r, _tbl = _active_runner(cfg, rec)
    state.datalog = None
    link = FakeLink(_pos(4, 1, 1))
    state.link = link
    logs = _run_to_end(r, link, [_pos(4, 1, 1), _pos(4, 2, 4, cyc=3)], _s(6, blk=3))
    assert r.last_result == "정상 종료", r.last_result
    assert _delta(b0) == _usage(cfg, rec) and _delta(b0)[PRE] == 6, (_delta(b0), logs)
    n = sum(_usage(cfg, rec).values())
    assert any(m.startswith(f"정비 카운터: 밸브 열림 {n} 회 더함(레시피 전체)") for _lv, m in logs), logs
    assert not any("세기 실패" in m for _lv, m in logs)


def test_1_finish_operator_abort_counts_to_position(clean_state, monkeypatch):
    cfg = clean_state
    clk = _Clock(monkeypatch)
    M.maint.load()
    b0 = _opens()
    rec = _vrec()
    r, _tbl = _active_runner(cfg, rec)
    state.datalog = None
    link = FakeLink(_pos(4, 1, 1))
    state.link = link
    r._abort_sent = True                                     # 명령 5 결과 0
    end = _s(8, blk=1, cyc=2)
    end[A.D_SEQ_STEP] = 2
    _run_to_end(r, link, [_pos(4, 1, 1), _pos(4, 1, 2, cyc=2)], end, clk)
    assert r.last_result == "중단 (운전자 중단)", r.last_result
    want = _usage(cfg, rec, {"block": 1, "step": 2, "cycle": 2, "group_pass": 1})
    assert _delta(b0) == want and want[PRE] == 2 and want[REA] == 2, _delta(b0)


def test_1_finish_plc_restart_with_group_pass(clean_state, monkeypatch):
    cfg = clean_state
    M.maint.load()
    b0 = _opens()
    rec = _vrec("그룹재시작", blocks=1, repeat=1, groups=[{"from_block": 1, "to_block": 1, "repeat": 10}])
    r, _tbl = _active_runner(cfg, rec)
    state.datalog = None
    link = FakeLink(_pos(4, 1, 1))
    state.link = link
    _run_to_end(r, link, [_pos(4, 1, 1), _pos(4, 1, 2, gp=6)], _s(0, blk=0, gp=0))
    assert r.last_result.startswith("중단 (PLC 재시작"), r.last_result
    assert _delta(b0) == {PRE: 6, REA: 6}, _delta(b0)


async def test_1_adopted_plc_number_counts_from_words(clean_state):
    cfg = clean_state
    M.maint.load()
    b0 = _opens()
    rec = _vrec("번호만")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    link = FakeLink(_pos(4, 1, 1), words=tbl["words"])
    state.link = link
    state.datalog = None
    r = ProcessRunner(state)
    state.runner = r

    async def plog(*a):
        return None
    assert await r.adopt_running(plog)
    assert r.recipe_name == f"(PLC 번호 {tbl['number']})" and r.active_recipe is None
    _run_to_end(r, link, [_pos(4, 1, 2)], _s(6, blk=3))
    assert r.last_result == "정상 종료" and _delta(b0) == _usage(cfg, rec), (r.last_result, _delta(b0))


async def test_1_offline_end_counts(clean_state):
    cfg = clean_state
    M.maint.load()
    b0 = _opens()
    rec = _vrec("꺼진동안")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    name = _closed_log(cfg, rec, tbl)
    state.link = FakeLink(_s(6, blk=3), words=tbl["words"])
    logs = []

    async def plog(m, lv="info"):
        logs.append(m)
    res = await ProcessRunner(state).check_offline_end(plog)
    assert res.startswith("정상 종료") and _delta(b0) == _usage(cfg, rec), (res, _delta(b0), logs)
    assert name in M.maint.data["counted"] and any(m.startswith("정비 카운터: 밸브 열림") for m in logs)


def test_1_count_failure_shows_in_notices(clean_state, monkeypatch):
    cfg = clean_state
    M.maint.load()
    rec = _vrec()
    r, _tbl = _active_runner(cfg, rec)
    state.datalog = None
    link = FakeLink(_pos(4, 1, 1))
    state.link = link

    def boom(*a, **k):
        raise TypeError("시험 실패")
    monkeypatch.setattr(M.maint, "count_run", boom)
    logs = _run_to_end(r, link, [_pos(4, 1, 1)], _s(6, blk=3))
    assert r.last_result == "정상 종료"
    fail = [m for lv, m in logs if lv == "warn" and "정비 카운터 세기 실패" in m]
    assert fail and "TypeError" in fail[0], logs
    n = [x for x in state.pc_notices(True) if "정비 카운터 세기 실패" in x[1]]
    assert len(n) == 1 and n[0][0] == "warn", n
    monkeypatch.undo()
    M.maint.count_run(cfg, "다음", rec, _s(6, blk=3), None, True)
    assert not [x for x in state.pc_notices(True) if "정비 카운터 세기 실패" in x[1]]     # 다음에 세면 지운다


# ===================== 2. 끝 결과 색(화면) =====================
def test_2_result_level_strips_tails():
    out = _node("const T = require(%s); console.log(JSON.stringify(%s.map(T.resultLevel)));" % (
        json.dumps(os.path.join(JS, "terms.js")),
        json.dumps(["중단 (운전자 중단) · 끝난 뒤 안전 정지: PC Link Lost (트립 남음)",
                    "중단 (압력 상한 — PC 가 즉시 중단) · 끝난 뒤 안전 정지: PC Link Lost (트립 남음)",
                    "정상 종료 (끝난 뒤 안전 정지: PC Link Lost (트립 남음))",
                    "정상 종료 · PC 끊긴 동안 끝남",
                    "정상 종료 · PC 꺼진 동안 끝남(다시 켠 뒤 PLC 끝 상태로 판정)",
                    "중단 (운전자 중단) · 끝난 뒤 안전 정지: PC Link Lost (트립 남음) · PC 꺼진 동안 끝남(…)",
                    "중단 (안전 정지 — EMO)",
                    "중단 (레시피 표 오류 — 블록 2 적재 거절) · 끝난 뒤 안전 정지: PC Link Lost (트립 남음)"],
                   ensure_ascii=False)))
    assert out == ["warn", "warn", "warn", "", "", "warn", "stop", "stop"], out


# ===================== 3. 끝 판정 한 스캔 틈 =====================
def test_3_seq8_before_safe_stop_gets_tail(clean_state, monkeypatch):
    cfg = clean_state
    rec = _recipe("틈", blocks=3)
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    tail = f" · 끝난 뒤 안전 정지: {TRIP}"
    assert P.judge_end(_s(8, blk=2, a0=B6), tbl, rec, abort_sent=True) == "중단 (운전자 중단)" + tail
    assert P.judge_end(_s(8, blk=2, a0=B6), tbl, rec, abort_unknown=True) == "중단 (운전자 중단 — 결과 확인 안 됨)" + tail
    assert P.judge_end(_s(8, blk=2, a0=B6), tbl, rec) == "중단 (PLC 중단)" + tail
    # 다음 스캔(안전 정지)과 같은 글
    assert P.judge_end(_s(8, st=A.STATE_SAFE_STOP, blk=2, a0=B6), tbl, rec, abort_sent=True) == \
        "중단 (운전자 중단)" + tail
    # 트립이 없거나 중대 알람이 있으면 붙이지 않는다
    assert P.judge_end(_s(8, blk=2), tbl, rec, abort_sent=True) == "중단 (운전자 중단)"
    assert P.judge_end(_s(8, blk=2, a0=B6 | (1 << A.ALM0_EMO)), tbl, rec, abort_sent=True) == "중단 (운전자 중단)"
    # 러너 경로 — 라이브 알람 목록과 무관하게 원인은 트립
    clk = _Clock(monkeypatch)
    r, _tbl = _active_runner(cfg, rec)
    state.datalog = None
    link = FakeLink(_pos(4, 2, 3))
    state.link = link
    r._abort_sent = True
    _run_to_end(r, link, [_pos(4, 2, 3, st=A.STATE_RUN)], _s(8, blk=2, a0=B6), clk)
    assert r.last_result == "중단 (운전자 중단)" + tail, r.last_result


# ===================== 4. 이어받기 직후 오프라인 판정 =====================
async def test_4_adopt_sets_offline_checked(clean_state):
    cfg = clean_state
    rec = _recipe("직후")
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    name = _closed_log(cfg, rec, tbl)
    link = FakeLink(_pos(4, 1, 1), words=tbl["words"])
    state.link = link
    r = ProcessRunner(state)
    state.runner = r

    async def plog(*a):
        return None
    assert r.offline_checked is False
    assert await r.adopt_running(plog) and r.offline_checked is True
    # 이어받은 공정이 끝난 뒤 이어받기 루프(server._adopt_loop 와 같은 조건)가 돌아도 열린 메타를 '꺼진 동안 끝남' 으로
    # 판정하지 않는다
    link.status = _s(6, blk=3)
    if not r.offline_checked:
        await r.check_offline_end(plog)
    meta = json.load(open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), encoding="utf-8"))
    assert "PC 꺼진 동안" not in str(meta.get("result"))
    src = _src(DEV.KEY, "server.py")
    assert 'not getattr(r, "offline_checked", True)' in src


# ===================== 5. 압력 상한 즉시 중단 — 실제 경로 =====================
async def test_5_press_abort_real_path(clean_state, monkeypatch):
    cfg = clean_state
    clk = _Clock(monkeypatch)
    cfg["process"].update({"press_high_torr": 10, "press_high_s": 0, "press_high_abort": True})
    rec = _recipe("압력")
    r, _tbl = _active_runner(cfg, rec)
    state.datalog = None
    hi = _pos(4, 1, 1)
    hi[A.D_CVG_RAW] = state.conv.cvg.to_raw(50.0)
    link = FakeLink(hi)
    state.link = link
    logs = []
    r.tick(lambda lv, m: logs.append((lv, m)))              # 공정 중 압력 상한 → PC 가 즉시 중단을 보낸다
    assert r._press_task is not None
    await r._press_task
    assert link.sent == [A.CMD_ABORT] and r._abort_sent and r._abort_why == "압력 상한 — PC 가 즉시 중단"
    end = _s(8, blk=1)
    end[A.D_CVG_RAW] = hi[A.D_CVG_RAW]
    _run_to_end(r, link, [], end, clk, logs)
    assert r.last_result == "중단 (압력 상한 — PC 가 즉시 중단)", (r.last_result, logs)
    assert any(m.startswith("공정 중단 (압력 상한 — PC 가 즉시 중단)") for _lv, m in logs), logs
    assert r._abort_why == ""                                # 끝에서 지운다(다음 공정에 넘어가지 않게)


# ===================== 6. Leak Check 단추 조건 =====================
def test_6_leak_button_uses_refuse_reasons(clean_state):
    st = _s(1)
    st[A.D_INTERLOCK] = (1 << A.ILK_PUMP) | (1 << A.ILK_VACUUM)
    st[A.D_INPUT0] = 1 << A.IN0_IVE_OPEN
    state.link = FakeLink(st)
    state.runner = ProcessRunner(state)
    state.alarms.clear_all()
    lk = LC.LeakCheck()
    assert lk.blocked(state) == "" and lk.refuse_reasons(state) == []
    st2 = list(st)
    st2[A.D_INPUT0] = 0
    st2[A.D_INTERLOCK] = 1 << A.ILK_PUMP
    state.link.status = st2
    why = lk.blocked(state)
    assert "IV-E 열림 입력 꺼짐" in why and "Vacuum OK 아님" in why, why
    assert why == " · ".join(lk.refuse_reasons(state))
    assert lk.status(state.cfg, state)["blocked"] == why
    setup = _src("frontend", "js", "views", "setup.js")
    assert "var why = core.lockReason() || (lc.running ? '누설률 점검 중' : (lc.blocked || ''));" in setup
    assert "disabled title=\"' + core.esc(core.lockReason()) + '\"') + '>Leak Check</button>'" in setup


# ===================== 7. 작은 것 =====================
def test_7a_b13_end_does_not_count_that_block(clean_state):
    cfg = clean_state
    M.maint.load()
    b0 = _opens()
    rec = _vrec("표오류", blocks=2, repeat=2)
    r, tbl = _active_runner(cfg, rec)
    w = tbl["words"]
    b2 = A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_STRIDE - A.RCP_SUM_BASE
    w[b2 + A.RCP_BLOCK_REPEAT_LO] = 0                       # 블록 2 반복 0 — 적재 거절
    state.datalog = None
    link = FakeLink(_pos(4, 1, 1))
    state.link = link
    end = _s(8, blk=2, a0=B13)                               # 래더: 블록 2 첫 스텝 · 사이클 1 을 쓴 뒤 거절
    end[A.D_SEQ_STEP] = 3
    _run_to_end(r, link, [_pos(4, 1, 1), _pos(4, 1, 2, cyc=2)], end)
    assert r.last_result.startswith("중단 (레시피 표 오류 — 블록 2 적재 거절"), r.last_result
    one = dict(rec, blocks=rec["blocks"][:1])
    assert _delta(b0) == _usage(cfg, one) and _delta(b0)[PRE] == 2, _delta(b0)


async def test_7b_closed_from_before_command_11(clean_state):
    class Cut(FakeLink):
        async def send_command(self, code, args=None):
            self.sent.append(code)
            raise asyncio.CancelledError()                   # 보내는 중 프로그램 종료
    state.link = Cut(_s(1))
    lk = LC.LeakCheck()

    async def plog(*a, **k):
        return None
    with pytest.raises(asyncio.CancelledError):
        await lk._run(state, plog, plog)
    assert "IV-E 는 닫힌 채" in lk.last["Reason"], lk.last


async def test_7b_no_closed_text_after_reopen(leak_rig):
    lk = LC.LeakCheck()
    ok, msg = lk.start(state, leak_rig.plog, leak_rig.plog)
    assert ok, msg
    while lk.running and not (lk.phase == "restore" and leak_rig.link.sent[-1:] == [A.CMD_PUMP_START]):
        await asyncio.sleep(0)
    lk.task.cancel()                                         # 명령 8 처리 뒤 열림을 기다리는 중 종료
    with pytest.raises(asyncio.CancelledError):
        await lk.task
    assert "프로그램 종료" in lk.last["Reason"] and "닫힌 채" not in lk.last["Reason"], lk.last


async def test_7b_restore_exception_still_writes_csv(clean_state, monkeypatch):
    lk = LC.LeakCheck()
    notes = []

    async def plog(m, lv="info"):
        notes.append(m)

    async def steps(state_, p, rec, *a):
        lk._closed = True
        rec.update(result="ABORT", why="시험")

    async def bad_restore(*a):
        raise RuntimeError("되돌리기 고장")
    monkeypatch.setattr(lk, "_steps", steps)
    monkeypatch.setattr(lk, "_restore", bad_restore)
    await lk._run(state, plog, plog)
    assert "되돌리기 오류 — RuntimeError" in lk.last["Reason"], lk.last
    with open(LC.csv_path(), encoding="utf-8-sig") as f:
        assert "되돌리기 오류" in f.read()
    assert any(LC.REOPEN_TEXT in n for n in notes)


async def test_7c_gauge_range_needs_three_in_a_row(leak_rig, monkeypatch):
    real = LC.read_gauge
    n = [0]
    pattern = {}

    def fake(state_, s, g):
        n[0] += 1
        return (None, pattern[n[0]]) if n[0] in pattern else real(state_, s, g)
    monkeypatch.setattr(LC, "read_gauge", fake)
    pattern.update({3: "high", 4: "high", 6: "low"})         # 두 번 이어짐 · 한 번 — 멈추지 않는다
    lk = await _run_with(leak_rig)
    assert lk.last["Result"] == "PASS", lk.last
    n[0] = 0
    pattern.clear()
    pattern.update({3: "high", 4: "high", 5: "high"})
    lk = await _run_with(leak_rig)
    assert lk.last["Result"] == "FAIL" and "범위 위로" in lk.last["Reason"], lk.last
    n[0] = 0
    pattern.clear()
    pattern.update({3: "low", 4: "low", 5: "low"})
    lk = await _run_with(leak_rig)
    assert lk.last["Result"] == "ABORT" and "범위 밖(아래)" in lk.last["Reason"], lk.last


def test_7c_cm_zero_picks_cvg(clean_state, monkeypatch):
    s = _s(1)
    s[A.D_CM_RAW] = 100
    cm = types.SimpleNamespace(installed=True, confirmed=True, to_torr=lambda raw: 0.0)
    monkeypatch.setattr(state.conv, "cm", cm)
    assert LC.pick_gauge(state, s) == "CVG"                  # CM 값 0 Torr — CVG
    cm.to_torr = lambda raw: 0.5
    assert LC.pick_gauge(state, s) == "CM"
    s[A.D_CM_RAW] = 0
    assert LC.pick_gauge(state, s) == "CVG"


def test_7d_trip_chip_tooltip_rule(clean_state):
    main = _src("frontend", "js", "views", "main.js")
    out = _node(_fn(main, "tripTip") + r'''
      console.log(JSON.stringify([tripTip({ pc_trip_only: true }, true), tripTip({ pc_trip_only: true }, false),
                                  tripTip({ pc_trip_only: false, pc_trip_cause: 'EMO' }, true)]));''')
    assert out[0].endswith("PLC 가 안전 정지 상태입니다. [Alarm Reset] 으로 풀립니다")
    assert out[1].endswith("공정이 끝나면 PLC 가 안전 정지합니다. [Alarm Reset] 으로 풀립니다")
    assert "원인은 EMO 입니다 — 원인을 없앤 뒤 [Alarm Reset]" in out[2] and "풀립니다" not in out[2], out
    assert "tripTip(t.plc, t.state.code === ST_SAFE_STOP)" in main
    state.link = FakeLink(_s(1, st=A.STATE_SAFE_STOP, a0=B6 | (1 << A.ALM0_EMO)))
    assert state._trip_cause(True) == {"pc_trip_only": False, "pc_trip_cause": "EMO"}
    state.link = FakeLink(_s(1, st=A.STATE_SAFE_STOP, a0=B6))
    assert state._trip_cause(True) == {"pc_trip_only": True, "pc_trip_cause": ""}


def test_7e_maintenance_read_retries_once(clean_state, monkeypatch):
    p = M.path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"valves": {PRE: {"opens": 7}}}, f)
    monkeypatch.setattr(M, "READ_RETRY_S", 0)
    real = open
    calls = [0]

    def locked_once(path, *a, **k):
        if path == p:
            calls[0] += 1
            if calls[0] == 1:
                raise PermissionError("잠김")
        return real(path, *a, **k)
    monkeypatch.setattr(M, "open", locked_once, raising=False)
    mt = M.Maintenance()
    mt.load()
    assert calls[0] == 2 and mt.data["valves"][PRE]["opens"] == 7 and not mt.problem and os.path.exists(p)

    def always(path, *a, **k):
        if path == p:
            calls[0] += 1
            raise PermissionError("잠김")
        return real(path, *a, **k)
    calls[0] = 0
    monkeypatch.setattr(M, "open", always, raising=False)
    mt2 = M.Maintenance()
    mt2.load()
    assert calls[0] == 2 and "PermissionError" in mt2.problem and not os.path.exists(p)
    assert [n for n in os.listdir(paths.DATA_DIR) if n.startswith("maintenance.bad-")]


# ===================== 8. e2e =====================
def test_8_e2e_checks_present():
    e2e = _src("test", "e2e_process.py")
    assert "이어받은 공정 끝 → 정비 카운터 = 셈 함수(recipe.valve_usage)" in e2e
    assert "want = R_usage(self.h.cfg, adopt_rec)" in e2e
    assert 'any(m.startswith("정비 카운터: 밸브 열림") for m in lines)' in e2e
    assert 'not any("정비 카운터 세기 실패" in m for m in lines)' in e2e
