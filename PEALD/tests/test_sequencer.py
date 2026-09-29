"""시뮬레이터 공정 실행 — 블록·사이클·그룹 순서, 일시정지·정지·중단, 알람 중단.

시퀀서는 PLC 래더의 확정 동작을 흉내 낸다. 여기서 어긋나면 화면의 진행 표시와
남은 시간이 현장에서 전부 틀린다.

★ 시퀀서는 레지스터만 쥐고 돌기 때문에 소켓 없이 직접 돌릴 수 있다 —
  짧은 스텝으로 빠르게 검증한다(통신 경로는 test_modbus·test_plclink 가 덮는다).
"""
import time

import pytest

from peald import addresses as A
from peald import device as DEV
from peald import recipe as R
from peald.convert import Converters
from peald.simulator import PlcSim


# ===================== 도우미 =====================
def mkstep(name="s", ms=50, valves=(), pause_ok=False, rf=False):
    s = {"name": name, "time_ms": ms, "valves": list(valves), "pause_ok": pause_ok}
    if DEV.HAS_RF:
        s["rf"] = rf
    return s


def mkblock(name="b", repeat=1, steps=None, mfc=None):
    b = R.empty_block(name)
    b["repeat"] = repeat
    b["steps"] = steps or [mkstep()]
    b["mfc_sccm"] = list(mfc) if mfc else [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    return b


def loaded(cfg, blocks, groups=None, stable_s=0, tol_raw=0, vmin=0):
    """레시피를 올린 시뮬레이터. 대기 시간을 0 으로 두어 빠르게 돈다."""
    conv = Converters(cfg)
    r = R.empty_recipe("t")
    r["blocks"] = blocks
    r["groups"] = groups or []
    tbl = R.to_plc_words(cfg, conv, r)

    sim = PlcSim(cfg, 1)
    sim.write(A.RCP_SUM_BASE, tbl["words"])
    sim.reg[A.D_PRM_MFC_STABLE] = stable_s
    sim.reg[A.D_PRM_MFC_TOL] = tol_raw
    sim.reg[A.D_PRM_MFC_TIMEOUT] = 60
    sim.reg[A.D_PRM_VALVE_MIN_MS] = vmin
    if DEV.HAS_RF:
        sim.reg[A.D_PRM_RF_MAX] = 16000
    if DEV.HAS_O3:
        sim.reg[A.D_PRM_O3_MAX] = 16000
    return sim, r


def run(sim, ms=4000, step_ms=10, watch=None):
    """시퀀서만 가짜 시계로 돌린다. watch(sim) 가 있으면 매 tick 부른다."""
    t = time.monotonic()
    for _ in range(int(ms / step_ms)):
        t += step_ms / 1000.0
        sim._sequencer(step_ms / 1000.0, t)
        sim._publish_seq()
        sim._state()
        if watch:
            watch(sim)
        if not sim.running:
            return True
    return False


def trace(sim, ms=4000, step_ms=10):
    """(블록, 사이클, 스텝, 그룹회차) 가 바뀔 때마다 기록."""
    seen = []

    def w(s):
        key = (s.blk, s.cycle, s.step_no, s.group_pass)
        if s.running and (not seen or seen[-1] != key):
            seen.append(key)

    done = run(sim, ms, step_ms, w)
    return seen, done


# ===================== 순서 =====================
def test_block_and_cycle_order(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 3, [mkstep("a1", 50), mkstep("a2", 50)])])
    sim._process_start()
    seen, done = trace(sim)
    assert done and sim.end_reason == "정상 종료"
    assert seen == [(1, 1, 1), (1, 1, 2), (1, 2, 1), (1, 2, 2), (1, 3, 1), (1, 3, 2)] \
        or [k[:3] for k in seen] == [(1, 1, 1), (1, 1, 2), (1, 2, 1), (1, 2, 2), (1, 3, 1), (1, 3, 2)]


def test_step_numbers_are_table_wide(cfg):
    """D00022 는 표 전체 기준 스텝 번호다(블록 안 번호가 아니다)."""
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a1", 50), mkstep("a2", 50)]),
                           mkblock("B", 1, [mkstep("b1", 50)])])
    sim._process_start()
    seen, done = trace(sim)
    assert done
    assert [k[2] for k in seen] == [1, 2, 3]
    assert [k[0] for k in seen] == [1, 1, 2]


def test_group_repeat_order(cfg):
    sim, _r = loaded(cfg,
                     [mkblock("A", 2, [mkstep("a", 50)]), mkblock("B", 1, [mkstep("b", 50)])],
                     groups=[{"from_block": 1, "to_block": 2, "repeat": 2}])
    sim._process_start()
    seen, done = trace(sim)
    assert done and sim.end_reason == "정상 종료"
    assert seen == [(1, 1, 1, 1), (1, 2, 1, 1), (2, 1, 2, 1),
                    (1, 1, 1, 2), (1, 2, 1, 2), (2, 1, 2, 2)]


def test_blocks_outside_group_run_once(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)]),
                           mkblock("B", 1, [mkstep("b", 50)]),
                           mkblock("C", 1, [mkstep("c", 50)])],
                     groups=[{"from_block": 1, "to_block": 2, "repeat": 2}])
    sim._process_start()
    seen, done = trace(sim)
    assert done
    assert [k[0] for k in seen] == [1, 2, 1, 2, 3]


def test_registers_during_run(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 2, [mkstep("a", 200)])])
    sim._process_start()
    run(sim, ms=50, step_ms=10)
    assert sim.reg[A.D_SEQ_BLOCK] == 1
    assert sim.reg[A.D_SEQ_STEP] == 1
    assert A.dword(sim.reg[A.D_SEQ_BLOCK_PASS], sim.reg[A.D_SEQ_BLOCK_PASS + 1]) == 1
    ms = A.dword(sim.reg[A.D_SEQ_STEP_MS], sim.reg[A.D_SEQ_STEP_MS + 1])
    assert 0 < ms <= 200
    assert sim.reg[A.D_STATE] == A.STATE_RUN
    assert sim.reg[A.D_SEQ_STATE] == 4


def test_step_elapsed_is_zero_outside_step(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])], stable_s=2, tol_raw=0)
    sim._process_start()
    run(sim, ms=100, step_ms=10)
    assert sim.reg[A.D_SEQ_STATE] == 3                  # 블록 준비 중
    assert sim.reg[A.D_STATE] == A.STATE_READY
    assert A.dword(sim.reg[A.D_SEQ_STEP_MS], sim.reg[A.D_SEQ_STEP_MS + 1]) == 0


# ===================== 최소 열림 =====================
def test_min_open_extends_step(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50, ["PV-1"])])], vmin=200)
    sim._process_start()
    run(sim, ms=30, step_ms=10)
    assert sim.step_dur == 200, "새로 열리는 밸브가 있으면 최소 열림으로 늘어난다"


def test_min_open_not_applied_when_already_open(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 2, [mkstep("a", 50, ["PV-1"]),
                                            mkstep("b", 50, ["PV-1"])])], vmin=200)
    sim._process_start()
    run(sim, ms=30, step_ms=10)
    assert sim.step_dur == 200           # 첫 스텝: 직전이 전부 닫힘
    run(sim, ms=220, step_ms=10)
    assert sim.step_no == 2 and sim.step_dur == 50, "이미 열려 있으면 늘리지 않는다"


# ===================== 일시정지 · 정지 · 중단 =====================
def test_pause_only_at_allowed_step(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50), mkstep("b", 50, pause_ok=True),
                                            mkstep("c", 50)])])
    sim._process_start()
    # ★ 풀스케일이 미정인 장비는 블록 MFC 가 원시값 0 이라 비교가 무의미하다 —
    #   알아볼 수 있는 값을 직접 넣고 그대로 남는지 본다.
    sim.reg[A.D_MFC_SV] = 1234
    sim.pause_req = True
    run(sim, ms=60, step_ms=10)          # 스텝 1 끝 — 허용이 아니라 계속
    assert sim.seq_state == 4 and sim.step_no == 2
    run(sim, ms=60, step_ms=10)          # 스텝 2 끝 — 허용이라 멈춘다
    assert sim.seq_state == 7
    assert sim.reg[A.D_STATE] == A.STATE_PAUSE
    assert sim.seq_valves == 0, "일시정지하면 밸브를 닫는다"
    assert sim.reg[A.D_MFC_SV] == 1234, "일시정지해도 MFC 설정은 유지한다"


def test_pause_at_block_end_even_if_not_allowed(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50), mkstep("b", 50)])])
    sim._process_start()
    sim.pause_req = True
    run(sim, ms=120, step_ms=10)
    assert sim.seq_state == 7, "블록의 마지막 스텝에서는 허용이 아니어도 멈춘다"


def test_resume_continues(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 2, [mkstep("a", 50, pause_ok=True)])])
    sim._process_start()
    sim.pause_req = True
    run(sim, ms=60, step_ms=10)
    assert sim.seq_state == 7
    assert sim._execute(A.CMD_RESUME) == A.RESULT_OK
    done = run(sim, ms=500, step_ms=10)
    assert done and sim.end_reason == "정상 종료"


def test_pause_is_not_cancellable(cfg):
    """일시정지 예약에는 취소 명령이 없다 — 두 번째 요청은 거절한다."""
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 500)])])
    sim._process_start()
    run(sim, ms=20, step_ms=10)
    assert sim._execute(A.CMD_PAUSE) == A.RESULT_OK
    assert sim._execute(A.CMD_PAUSE) == A.RESULT_STATE


def test_stop_after_cycle(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 10, [mkstep("a", 50)])])
    sim._process_start()
    run(sim, ms=20, step_ms=10)
    assert sim._execute(A.CMD_STOP_AFTER_CYCLE) == A.RESULT_OK
    assert sim.reg[A.D_STATE] == A.STATE_STOPPING or sim.stop_req
    done = run(sim, ms=500, step_ms=10)
    assert done and sim.end_reason == "사이클 후 정지"
    assert sim.cycle == 0


def test_abort(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 100, [mkstep("a", 50)])])
    sim._process_start()
    run(sim, ms=20, step_ms=10)
    assert sim._execute(A.CMD_ABORT) == A.RESULT_OK
    assert not sim.running and sim.seq_state == 8
    assert "즉시 중단" in sim.end_reason


def test_end_clears_outputs(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50, ["PV-1"])])])
    sim._process_start()
    run(sim, ms=500, step_ms=10)
    assert sim.seq_valves == 0
    assert all(sim.reg[A.D_MFC_SV + i] == 0 for i in range(8))
    assert not sim.pause_req and not sim.stop_req


def test_safe_stop_aborts(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 100, [mkstep("a", 50)])])
    sim._process_start()
    run(sim, ms=20, step_ms=10)
    sim.safe_stop = True
    run(sim, ms=20, step_ms=10)
    assert not sim.running and "안전 정지" in sim.end_reason


def test_recipe_value_error_aborts(cfg):
    """작업본의 스텝 시간이 범위를 벗어나면 레시피 값 오류로 중단 + 알람0 b13."""
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])])
    sim._process_start()
    # 작업본의 스텝 1 시간을 0 으로 흠집낸다
    base = A.D_RCP_STEP_BASE + A.RCP_STEP_TIME_LO - A.RCP_SUM_BASE
    sim.work[base] = 0
    sim.work[base + 1] = 0
    sim.step_no = 1
    sim._load_step()
    assert not sim.running
    assert "레시피 값 오류" in sim.end_reason
    assert (sim.reg[A.D_ALARM0] >> A.ALM0_RECIPE) & 1


def test_bad_block_item_aborts(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])])
    sim._process_start()
    sim.work[A.D_RCP_BLOCK_BASE - A.RCP_SUM_BASE] = 0      # 첫 스텝 0 → 올바르지 않다
    sim._load_block(1)
    assert not sim.running and "레시피 값 오류" in sim.end_reason


# ===================== MFC 감시 =====================
def test_block_prep_waits_for_mfc(cfg):
    """허용 편차가 0 이 아니면 MFC1 이 안정될 때까지 첫 스텝으로 가지 않는다."""
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])], stable_s=1, tol_raw=100)
    sim.faults["mfc1_stuck"] = True
    sim.mfc_pv[0] = 0.0
    sim._process_start()
    run(sim, ms=500, step_ms=10)
    assert sim.seq_state == 3, "MFC 가 안 맞으면 블록 준비에 머문다"


def test_block_prep_timeout_alarms_and_aborts(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])], stable_s=1, tol_raw=100)
    sim.reg[A.D_PRM_MFC_TIMEOUT] = 1
    sim.faults["mfc1_stuck"] = True
    sim.mfc_pv[0] = 0.0
    sim._process_start()
    run(sim, ms=2000, step_ms=10)
    assert not sim.running
    assert (sim.reg[A.D_ALARM0] >> A.ALM0_MFC) & 1
    assert "MFC 안정 대기 시간 초과" in sim.end_reason


def test_mfc_deviation_during_run_aborts(cfg):
    """공정 중 MFC1 편차가 10 s 계속되면 중단한다."""
    sim, _r = loaded(cfg, [mkblock("A", 1000, [mkstep("a", 50)])], stable_s=0, tol_raw=100)
    sim._process_start()
    # 블록 준비를 통과하려면 먼저 MFC 가 맞아야 한다
    sim.reg[A.D_MFC_PV] = sim.reg[A.D_MFC_SV]
    run(sim, ms=100, step_ms=10)
    assert sim.running and sim.seq_state == 4, "블록 준비를 통과하지 못했다"
    sim.reg[A.D_MFC_PV] = 0                 # 설정과 크게 벌어진 상태를 만든다
    sim.reg[A.D_MFC_SV] = 8000
    run(sim, ms=11_000, step_ms=50)
    assert not sim.running
    assert (sim.reg[A.D_ALARM0] >> A.ALM0_MFC) & 1
    assert "MFC 편차" in sim.end_reason


def test_no_mfc_watch_when_tolerance_zero(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 40, [mkstep("a", 50)])], stable_s=0, tol_raw=0)
    sim._process_start()
    sim.reg[A.D_MFC_PV] = 0
    sim.reg[A.D_MFC_SV] = 8000
    done = run(sim, ms=4000, step_ms=10)
    assert done and sim.end_reason == "정상 종료", "감시를 끄면 편차가 있어도 돈다"


# ===================== 거절 규칙 =====================
def test_commands_rejected_when_not_running(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])])
    for code in (A.CMD_PAUSE, A.CMD_RESUME, A.CMD_STOP_AFTER_CYCLE, A.CMD_ABORT):
        assert sim._execute(code) == A.RESULT_STATE, code


def test_manual_commands_rejected_while_running(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 100, [mkstep("a", 50)])])
    sim._process_start()
    run(sim, ms=20, step_ms=10)
    sim._state()
    for code in (A.CMD_PUMP_STOP, A.CMD_VENT, A.CMD_ALL_CLOSE,
                 A.CMD_MANUAL_APPLY, A.CMD_HEATER_APPLY, A.CMD_MFC_APPLY):
        assert sim._execute(code) == A.RESULT_STATE, code


def test_start_clears_manual_valve_request(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])])
    sim.reg[A.D_MANUAL_VALVE] = 0x0003
    sim._process_start()
    assert sim.reg[A.D_MANUAL_VALVE] == 0


# ===================== 장비 전용 =====================
@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
def test_rf_flag_only_during_flagged_step(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50, ["PV-R", "PV-R3"], rf=True),
                                            mkstep("b", 50)])])
    sim._process_start()
    run(sim, ms=20, step_ms=10)
    assert sim.rf_step is True
    run(sim, ms=60, step_ms=10)
    assert sim.step_no == 2 and sim.rf_step is False


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
def test_block_rf_is_clipped_to_limit(cfg):
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])])
    sim.reg[A.D_PRM_RF_MAX] = 100
    sim._process_start()
    assert sim.block_rf_raw <= 100


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_block_o3_is_clipped_to_limit(cfg):
    b = mkblock("A", 1, [mkstep("a", 50)])
    b["o3"] = 999999
    sim, _r = loaded(cfg, [b])
    sim.reg[A.D_PRM_O3_MAX] = 100
    sim._process_start()
    assert sim.reg[A.D_O3_SV] <= 100


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_o3_line_is_kept_at_start_and_end(cfg):
    """Powder 는 공정 시작·종료 때 O3 라인(수동 보조 요청)을 유지한다."""
    sim, _r = loaded(cfg, [mkblock("A", 1, [mkstep("a", 50)])])
    sim.reg[A.D_MANUAL_AUX] = DEV.AUX_CMD_MASK
    sim._process_start()
    assert sim.reg[A.D_MANUAL_AUX] == DEV.AUX_CMD_MASK
    run(sim, ms=500, step_ms=10)
    assert sim.reg[A.D_MANUAL_AUX] == DEV.AUX_CMD_MASK
