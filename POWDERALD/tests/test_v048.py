"""v0.4.8 — 시뮬레이터를 래더에 맞춤. 가짜 시계로 tick(= 래더 한 스캔) 단위로 돌려 래더 기준 결과를 본다.

스캔 순서: 물리 → 입력 이미지 → P25 → P30 → P35 → P40 → P45 → P50 → P60 → P70.
"""
import pytest

from powderald import addresses as A
from powderald import device as DEV
from powderald import recipe as R
from powderald.convert import Converters
from powderald.simulator import AO_PCV, AO_RF, ao_mfc

from conftest import FakeSim

bit = A.bit


def alm0(s, b):
    return bit(s.reg[A.D_ALARM0], b)


def alm1(s, b):
    return bit(s.reg[A.D_ALARM1], b)


def aux(s, b):
    return bit(s.reg[A.D_AUX_OUT], b)


def pump_down(fs, base_torr=1.0):
    """베이스 압력 설정 · 펌핑 시작 → 공정 밸브 허가(IV-E 열림)까지."""
    s = fs.sim
    s.write(A.D_PRM_BASE_PRESS, [s.conv.cvg.to_raw(base_torr)])
    s.base_pressure = s.pressure = 0.5
    assert s._execute(A.CMD_PUMP_START) == A.RESULT_OK
    for _ in range(200):
        fs.step()
        if bit(s.reg[A.D_INTERLOCK], A.ILK_VALVE_OK):
            return
    raise AssertionError("공정 밸브 허가가 서지 않았다")


def table(cfg, blocks=1, repeat=50, step_ms=500, pcv=None):
    r = R.empty_recipe("래더")
    r["blocks"] = []
    for i in range(blocks):
        b = R.empty_block(f"b{i + 1}")
        b["repeat"] = repeat
        b["steps"] = [{"name": "s", "time_ms": step_ms, "valves": [], "pause_ok": True,
                       **({"rf": False} if DEV.HAS_RF else {})}]
        if DEV.HAS_PCV and pcv is not None:
            b["pcv_pct"] = pcv
        r["blocks"].append(b)
    return R.to_plc_words(cfg, Converters(cfg), r)


def running(fs, cfg, stable_s=0, **kw):
    """블록 준비를 바로 넘기는 PRM 으로 공정을 시작한다(Powder 는 O3 허가 먼저)."""
    s = fs.sim
    s.write(A.D_PRM_MFC_STABLE, [stable_s])
    s.write(A.D_PRM_MFC_TOL, [0])
    s.write(A.RCP_SUM_BASE, table(cfg, **kw)["words"])
    assert s._process_start() == A.RESULT_OK
    fs.step()
    return s


# ===================== 0 · 스캔 순서 =====================
def test_tick_runs_programs_in_ladder_order(cfg, monkeypatch):
    fs = FakeSim(cfg)
    order = []
    for name in ("_physics", "_inputs", "_p25", "_interlocks", "_alarms", "_sequencer", "_p45",
                 "_auto_clear", "_p60", "_p70"):
        orig = getattr(fs.sim, name)
        monkeypatch.setattr(fs.sim, name, (lambda o, n: lambda *a, **k: (order.append(n), o(*a, **k))[1])(orig, name))
    fs.step()
    assert order == ["_physics", "_inputs", "_p25", "_interlocks", "_alarms", "_sequencer", "_p45",
                     "_auto_clear", "_p60", "_p70"]


# ===================== 1 · RF 반사 =====================
@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
@pytest.mark.parametrize("lim,ms", [(100, 300), (0, 0)])
def test_rf_reflect_uses_delay_timer_and_zero_limit(cfg, lim, ms):
    """DO_RF_ON AND AI_RF_REF > PRM_RF_REF_MAX 가 PRM_RF_REF_MS 이어지면 래치 — 한계 > 0 조건 없음."""
    fs = FakeSim(cfg)
    s = fs.sim
    s.write(A.D_PRM_RF_MAX, [16000])
    s.write(A.D_PRM_RF_MAX_PRESS, [s.conv.cvg.to_raw(10.0)])
    s.write(A.D_PRM_RF_REF_MAX, [lim])
    s.write(A.D_PRM_RF_REF_MS, [ms])
    pump_down(fs, base_torr=1e-3)
    s.base_pressure = 0.5
    s.set_fault("rf_ref", True)                 # 반사 = 순방향 30 %
    s.man_aux = 1 << A.AUX_RF
    s.ao[AO_RF] = 1000
    t_on = None
    for _ in range(100):
        fs.step(1, 0.01)
        if aux(s, A.AUX_RF) and t_on is None:
            t_on = fs.t[0]
        if alm1(s, A.ALM1_RF_REF):
            break
    assert t_on is not None, "RF 가 켜지지 않았다"
    took = fs.t[0] - t_on
    assert alm1(s, A.ALM1_RF_REF)
    assert ms / 1000.0 <= took <= ms / 1000.0 + 0.05, took


# ===================== 2 · 리드 =====================
def test_lid_open_during_block_prep_is_critical(cfg):
    fs = FakeSim(cfg, o3=True)
    s = running(fs, cfg, stable_s=600)              # 블록 준비(MFC 안정 대기)에 머문다
    assert s.seq_state == 3 and s.reg[A.D_STATE] == A.STATE_READY
    s.set_fault("lid", True)
    fs.step(3)
    assert alm0(s, A.ALM0_LID)
    assert not s.running and s.reg[A.D_SEQ_STATE] == 8 and s.reg[A.D_STATE] == A.STATE_SAFE_STOP


# ===================== 3 · Powder O3 허가 알람 =====================
@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
@pytest.mark.parametrize("how", ["bp_run_lost", "o3_max_zero", "emo"])
def test_o3_permit_alarm_in_process(cfg, how):
    """SEQ_RUN AND NOT ILK_O3_OK → 알람1 b3. 공정 중 안전 정지(어떤 중대 알람이든)에도 함께 선다."""
    fs = FakeSim(cfg, o3=True)
    s = running(fs, cfg)
    assert s.running and not alm1(s, A.ALM1_O3_GEN)
    if how == "bp_run_lost":
        s.man_aux &= ~(1 << A.AUX_BYPASS_PUMP)       # 바이패스 펌프 운전 입력이 끊긴다
    elif how == "o3_max_zero":
        s.write(A.D_PRM_O3_MAX, [0])
    else:
        s.set_fault("emo", True)
    fs.step(4)
    assert alm1(s, A.ALM1_O3_GEN), f"b3 가 서지 않았다 ({how})"
    assert not s.running and s.reg[A.D_SEQ_STATE] == 8 and s.reg[A.D_STATE] == A.STATE_SAFE_STOP
    if how == "emo":
        assert alm0(s, A.ALM0_EMO)


# ===================== 4 · 0 인 PRM =====================
def test_zero_pc_wdt_trips_right_after_first_heartbeat(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.write(A.D_PRM_PC_WDT_MS, [0])
    fs.step(10)
    assert not alm0(s, A.ALM0_PC_LINK), "하트비트를 보기 전에는 트립하지 않는다"
    s.write(A.D_PC_HB, [1])
    fs.step(1)
    fs.step(1)
    assert alm0(s, A.ALM0_PC_LINK)


def test_zero_vent_timeout_clears_request_before_vv(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.write(A.D_PRM_VENT_TIMEOUT, [0])
    s.base_pressure = s.pressure = 1.0
    fs.step(2)
    assert s._execute(A.CMD_VENT) == A.RESULT_OK
    seen_vv = False
    for _ in range(5):
        fs.step()
        seen_vv |= aux(s, A.AUX_VV)
    assert alm0(s, A.ALM0_VENT_TIMEOUT) and not s.vent_req and not seen_vv


def test_zero_pump_timeout_trips_when_ive_opens(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.write(A.D_PRM_PUMP_TIMEOUT, [0])
    s.write(A.D_PRM_BASE_PRESS, [s.conv.cvg.to_raw(1e-3)])
    assert s._execute(A.CMD_PUMP_START) == A.RESULT_OK
    ive_at = b7_at = None
    for _ in range(200):
        fs.step()
        if s.ive_out and ive_at is None:
            ive_at = fs.t[0]
        if alm0(s, A.ALM0_BASE_TIMEOUT):
            b7_at = fs.t[0]
            break
    assert ive_at is not None and b7_at is not None and b7_at - ive_at <= 0.041, (ive_at, b7_at)


def test_zero_mfc_timeout_aborts_every_block_prep(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    s.write(A.D_PRM_MFC_TIMEOUT, [0])
    s.write(A.D_PRM_MFC_STABLE, [0])
    s.write(A.D_PRM_MFC_TOL, [0])
    s.write(A.RCP_SUM_BASE, table(cfg)["words"])
    assert s._process_start() == A.RESULT_OK
    # v0.4.10 래더: 스캔 1 P40 T0024 → 스캔 2 P35 b12 → 스캔 3 P30 안전 정지 요구로 P40 중단(장비 상태 6)
    fs.step(1)
    assert not alm0(s, A.ALM0_MFC) and s.mfc_to_done
    fs.step(1)
    assert alm0(s, A.ALM0_MFC) and s.reg[A.D_SEQ_STATE] == 3
    fs.step(1)
    assert s.reg[A.D_SEQ_STATE] == 8 and s.reg[A.D_STATE] == A.STATE_SAFE_STOP


# ===================== 5 · 펌핑 시간 초과 =====================
def test_pump_timeout_counts_from_ive_open_and_comes_back_after_reset(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.write(A.D_PRM_PUMP_TIMEOUT, [2])
    s.write(A.D_PRM_BASE_PRESS, [s.conv.cvg.to_raw(1e-6)])       # 갈 수 없는 베이스
    t0 = fs.t[0]
    assert s._execute(A.CMD_PUMP_START) == A.RESULT_OK
    ive_at = b7_at = None
    for _ in range(400):
        fs.step()
        if s.ive_out and ive_at is None:
            ive_at = fs.t[0]
        if alm0(s, A.ALM0_BASE_TIMEOUT):
            b7_at = fs.t[0]
            break
    assert ive_at - t0 >= 0.99, "IV-E 는 펌프 운전 입력(1 s) 뒤에 열린다"
    assert 2.0 <= b7_at - ive_at <= 2.05, (ive_at - t0, b7_at - t0)
    # 원인이 사라진 알람 하나(가스 누출)를 함께 래치해 두고 리셋 — 같은 스캔에 누출은 풀리고 b7 은 다시 선다
    s.set_fault("leak", True)
    fs.step(1)
    s.set_fault("leak", False)
    fs.step(1)
    assert alm0(s, A.ALM0_LEAK)
    s.write(A.D_CMD_CODE, [A.CMD_ALARM_RESET])
    s.write(A.D_CMD_NO, [s.last_cmd_no + 1])
    fs.step(1)
    assert s.reg[A.D_ACK_RESULT] == A.RESULT_OK
    assert not alm0(s, A.ALM0_LEAK), "원인이 없는 알람은 풀려야 한다"
    assert alm0(s, A.ALM0_BASE_TIMEOUT), "베이스에 못 간 채 펌핑 중이면 리셋 스캔에 다시 선다"


def test_zero_base_pressure_never_reaches_vac_done(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.write(A.D_PRM_PUMP_TIMEOUT, [1])
    s.write(A.D_PRM_BASE_PRESS, [0])
    s._execute(A.CMD_PUMP_START)
    fs.step(200)
    assert not s.vac_done and alm0(s, A.ALM0_BASE_TIMEOUT)


# ===================== 6 · 램프 · 부저 =====================
def lamps(s):
    return (aux(s, A.AUX_LAMP_R), aux(s, A.AUX_LAMP_Y), aux(s, A.AUX_LAMP_G), aux(s, A.AUX_BUZZER))


def at_blink(fs, on):
    """다음 스캔이 깜빡임 켜짐(on) · 꺼짐 칸에 오게 시계를 맞춰 한 스캔."""
    t = fs.t[0]
    base = int(t) + 1
    fs.t[0] = base + (0.1 if on else 0.6) - 0.02
    fs.step(1)
    return lamps(fs.sim)


def test_lamp_table(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    fs.step(2)
    assert at_blink(fs, True) == (False, True, False, False), "대기 · 알람 없음 → 황"
    s.set_fault("leak", True)
    fs.step(2)
    s.set_fault("leak", False)
    assert at_blink(fs, True) == (True, False, False, True), "알람 · 확인 전 → 적 깜빡임(켜짐 칸) · 부저"
    assert at_blink(fs, False) == (False, False, False, True), "알람 · 확인 전 → 적 깜빡임(꺼짐 칸)"
    s._execute(A.CMD_ALARM_ACK)
    assert at_blink(fs, False) == (True, False, False, False), "확인 뒤 → 적 켜짐 · 부저 끔"
    s._execute(A.CMD_ALARM_RESET)
    fs.step(2)
    fs.o3_ready()                   # Powder: 중대 알람의 안전 정지가 O3 라인을 지웠다 — 다시 켠다
    running(fs, cfg)
    assert at_blink(fs, False) == (False, False, True, False), "공정 중 → 녹"
    s.pause_req = True
    for _ in range(100):
        fs.step()
        if s.seq_state == 7:
            break
    assert at_blink(fs, True) == (False, True, False, False), "일시정지 → 황 깜빡임(켜짐 칸)"
    assert at_blink(fs, False) == (False, False, False, False), "일시정지 → 황 깜빡임(꺼짐 칸) · 녹 꺼짐"


def test_dp_n2_follows_pump_output(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    fs.step()
    assert not aux(s, A.AUX_PUMP_N2)
    s._execute(A.CMD_PUMP_START)
    fs.step()
    assert aux(s, A.AUX_PUMP) and aux(s, A.AUX_PUMP_N2)
    s._execute(A.CMD_PUMP_STOP)
    fs.step()
    assert not aux(s, A.AUX_PUMP_N2)


# ===================== 7 · 벤트 끝 =====================
def test_vent_ends_on_atm_input_same_scan(cfg):
    fs = FakeSim(cfg, speed=20)
    s = fs.sim
    s.base_pressure = s.pressure = 1.0
    fs.step(3)
    s._execute(A.CMD_VENT)
    for _ in range(2000):
        fs.step()
        if bit(s.reg[A.D_INPUT0], A.IN0_ATM):
            break
    assert bit(s.reg[A.D_INPUT0], A.IN0_ATM)
    assert not s.vent_req and not aux(s, A.AUX_VV), "대기압 입력이 켜진 스캔에 VV 가 닫혀야 한다"
    assert not alm0(s, A.ALM0_VENT_TIMEOUT)


# ===================== 8 · 시작 결과 3 =====================
def test_start_result_3_when_block1_load_fails(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    pump_down(fs)
    tbl = table(cfg, blocks=2, pcv=40.0)
    w = list(tbl["words"])
    first = w[A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_FIRST - A.RCP_SUM_BASE]
    # 블록 1 반복을 0 으로(표 머리는 맞게 합계를 다시)
    w[A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_REPEAT_LO - A.RCP_SUM_BASE] = 0
    w[A.D_RCP_SUM - A.RCP_SUM_BASE] = 0
    w[A.D_RCP_SUM - A.RCP_SUM_BASE] = R.checksum_of(w)
    s.write(A.RCP_SUM_BASE, w)
    s.man_valve = 1 << DEV.PRECURSOR_VALVE_BITS[0]
    s.ao[ao_mfc(1)] = 1234
    if DEV.HAS_RF:
        s.man_aux = 1 << A.AUX_RF
        s.ao[AO_RF] = 500
    for i in range(60):                          # PC 하트비트 · 1 s 표 검사로 시작 허가
        s.write(A.D_PC_HB, [i + 1])
        fs.step(1, 0.05)
    assert bit(s.reg[A.D_INTERLOCK], A.ILK_START_OK), hex(s.reg[A.D_INTERLOCK])
    s.write(A.D_CMD_CODE, [A.CMD_PROCESS_START])
    s.write(A.D_CMD_NO, [s.last_cmd_no + 1])
    s.write(A.D_PC_HB, [99])
    fs.step(1)
    assert s.reg[A.D_ACK_RESULT] == A.RESULT_RECIPE and alm0(s, A.ALM0_RECIPE)
    assert s.reg[A.D_SEQ_STATE] == 8
    assert s.reg[A.D_SEQ_BLOCK] == 1 and s.reg[A.D_SEQ_STEP] == first
    assert s.reg[A.D_SEQ_GROUP_PASS] == 1 and A.dword(s.reg[A.D_SEQ_BLOCK_PASS], s.reg[A.D_SEQ_BLOCK_PASS + 1]) == 1
    assert s.man_valve == 0
    if DEV.HAS_RF:
        assert s.man_aux == 0 and s.ao[AO_RF] == 0
    assert s.ao[ao_mfc(1)] == 0
    if DEV.HAS_PCV:
        assert s.ao[AO_PCV] == Converters(cfg).pcv.to_raw(40.0)
    ok, total = s._table_check()
    assert s.reg[A.D_RECIPE_SUM_PLC] == total and s.reg[A.D_RECIPE_OK] == (1 if ok else 0)
    assert s.reg[A.D_STATE] not in (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)


# ===================== 9 · 공압 · N2 1 s =====================
@pytest.mark.parametrize("key,alarm,inp", [("air", A.ALM0_AIR, A.IN0_AIR), ("n2", A.ALM0_N2, A.IN0_N2)])
def test_air_n2_need_one_second(cfg, key, alarm, inp):
    fs = FakeSim(cfg)
    s = fs.sim
    fs.step(2)
    s.set_fault(key, True)                       # 0.9 s 떨림 — 무시
    fs.step(45)
    assert not bit(s.reg[A.D_INPUT0], inp), "입력 이미지는 바로 바뀐다"
    assert bit(s.reg[A.D_INTERLOCK], A.ILK_BASIC) and not alm0(s, alarm)
    s.set_fault(key, False)
    fs.step(2)
    s.set_fault(key, True)                       # 1 s 이어짐 — 빠진다
    fs.step(49)
    assert bit(s.reg[A.D_INTERLOCK], A.ILK_BASIC) and not alm0(s, alarm)
    fs.step(2)
    assert not bit(s.reg[A.D_INTERLOCK], A.ILK_BASIC) and alm0(s, alarm)


# ===================== 10 · PC 통신 트립 =====================
def test_pc_link_trip_rules(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.write(A.D_PRM_PC_WDT_MS, [500])
    fs.step(100)                                 # 2 s 동안 하트비트 없음 — 전원 직후라 트립 안 함
    assert not alm0(s, A.ALM0_PC_LINK) and not s.pc_link_ok
    for i in range(10):
        s.write(A.D_PC_HB, [i + 1])
        fs.step(5)
    assert s.pc_link_ok
    fs.step(30)                                  # 0.6 s 멈춤 → 트립
    assert alm0(s, A.ALM0_PC_LINK) and s.pc_trip and not s.pc_link_ok
    s._execute(A.CMD_ALARM_RESET)                # PC_LINK_OK 아님 — 트립은 남고 P35 가 다시 세운다
    fs.step(1)
    assert alm0(s, A.ALM0_PC_LINK)
    s.write(A.D_PC_HB, [77])
    fs.step(1)
    s._execute(A.CMD_ALARM_RESET)                # (v0.4.10: 리셋은 P35 첫 행 — 명령으로 요청)
    fs.step(1)
    assert not alm0(s, A.ALM0_PC_LINK) and not s.pc_trip


# ===================== 11 · IV-B =====================
@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_ivb_needs_bypass_pump_running(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.man_aux = 1 << A.AUX_IVB
    fs.step(3)
    assert not aux(s, A.AUX_IVB), "바이패스 펌프 없이 IV-B 가 열렸다"
    s.man_aux |= 1 << A.AUX_BYPASS_PUMP
    fs.step(3)
    assert aux(s, A.AUX_IVB)
    s.set_fault("bp_alm", True)
    fs.step(2)
    assert not aux(s, A.AUX_IVB)


# ===================== 12 · MFC 준비 시간 초과 =====================
@pytest.mark.parametrize("stable,timeout,aborts", [(5, 3, True), (1, 3, False), (3, 3, True)])
def test_mfc_prep_timeout_even_with_zero_tolerance(cfg, stable, timeout, aborts):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    s.write(A.D_PRM_MFC_TIMEOUT, [timeout])
    s.write(A.D_PRM_MFC_STABLE, [stable])
    s.write(A.D_PRM_MFC_TOL, [0])
    s.write(A.RCP_SUM_BASE, table(cfg)["words"])
    assert s._process_start() == A.RESULT_OK
    t0 = fs.t[0]
    for _ in range(400):
        fs.step()
        if s.seq_state != 3:
            break
    took = fs.t[0] - t0
    if aborts:
        # v0.4.10 래더: T0024 출력 뒤 두 스캔(P35 b12 → P30 안전 정지) 늦게 중단 — 한 스캔 0.02 s
        assert alm0(s, A.ALM0_MFC) and s.reg[A.D_SEQ_STATE] == 8
        assert s.reg[A.D_STATE] == A.STATE_SAFE_STOP
        assert timeout + 0.04 - 1e-6 <= took <= timeout + 0.05 + 0.04, took
    else:
        assert not alm0(s, A.ALM0_MFC) and s.seq_state == 4
        assert stable <= took <= stable + 0.05, took


# ===================== 13 · D00080 =====================
def test_scan_max_is_zero_like_ladder(cfg):
    fs = FakeSim(cfg)
    fs.step(5)
    assert fs.sim.reg[A.D_SCAN_MAX] == 0
