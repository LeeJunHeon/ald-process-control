"""내장 시뮬레이터 — 펌핑·벤트·전체 닫기·거절 규칙·알람 래치/확인/리셋.

물리값은 대략이어도 되지만 상태 코드·결과 코드·비트 의미는 정확해야 한다.
★ PLC 는 PC 영역(D01000~D01124)을 쓰지 않는다 — 스스로 지우는 것은 내부 사본
  (D04012~13·D04050·D04120~·히터 전원 묶음)뿐이다. 시험도 그 기준으로 본다.
"""
import asyncio

import pytest

from peald import addresses as A
from peald import device as DEV
from conftest import wait_until

PC_AREA = (A.D_PC_HB, A.D_PRM_O3_MAX + 1)


def pc_area(s):
    """PC 영역 사본 — 하트비트·명령 번호·코드처럼 PC 가 계속 쓰는 워드는 뺀다."""
    skip = {A.D_PC_HB, A.D_CMD_NO, A.D_CMD_CODE}
    return {a: s.reg[a] for a in range(*PC_AREA) if a not in skip}


async def at_vacuum(lk, s):
    """대기압 입력이 꺼질 만큼 압력을 낮춘다(대기압이면 PLC 가 밸브 반영을 지운다)."""
    s.base_pressure = 1.0
    assert await wait_until(lambda: not A.bit(lk.status[A.D_INPUT0], A.IN0_ATM), 3)


async def manual(lk, valve=0, aux=0):
    """PC 가 하듯 요청 영역을 쓰고 명령 12 를 보낸다."""
    lo, hi = A.split_dword(valve)
    r, _ = await lk.send_command(A.CMD_MANUAL_APPLY,
                                 {A.D_MANUAL_VALVE: [lo, hi, 0, 0], A.D_MANUAL_AUX: aux})
    return r


async def test_pumping_opens_ive_and_lowers_pressure(link):
    lk, s, _cfg = link
    start_p = s.pressure
    r, _ = await lk.send_command(A.CMD_PUMP_START)
    assert r == A.RESULT_OK
    # 펌프 운전 피드백은 1 s 뒤, IV-E 리미트는 그 뒤 1 s
    assert await wait_until(lambda: A.bit(s.reg[A.D_INPUT0], A.IN0_PUMP_RUN), 4)
    assert await wait_until(lambda: A.bit(s.reg[A.D_INPUT0], A.IN0_IVE_OPEN), 4)
    assert await wait_until(lambda: s.pressure < start_p / 10, 8)
    assert A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE)
    assert A.bit(s.reg[A.D_AUX_OUT], A.AUX_PUMP)


async def test_vacuum_interlock_when_base_reached(link):
    lk, s, _cfg = link
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(s.reg[A.D_INTERLOCK], A.ILK_VACUUM), 15), \
        f"베이스 압력에 도달하지 못했다 (현재 {s.pressure:g} Torr)"


async def test_pump_stop_closes_ive(link):
    lk, s, _cfg = link
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE), 4)
    r, _ = await lk.send_command(A.CMD_PUMP_STOP)
    assert r == A.RESULT_OK
    assert await wait_until(lambda: not A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE), 3)
    assert await wait_until(lambda: not A.bit(s.reg[A.D_AUX_OUT], A.AUX_PUMP), 3)


async def test_vent_reaches_atmosphere(link):
    lk, s, _cfg = link
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: s.pressure < 1.0, 15)
    r, _ = await lk.send_command(A.CMD_VENT)
    assert r == A.RESULT_OK
    assert await wait_until(lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_VV), 5), "VV 가 열리지 않았다"
    assert await wait_until(lambda: s.pressure > 700, 15)
    # 대기압에 닿으면 VV 를 닫는다
    assert await wait_until(lambda: not A.bit(s.reg[A.D_AUX_OUT], A.AUX_VV), 5)
    assert await wait_until(lambda: A.bit(s.reg[A.D_INPUT0], A.IN0_ATM), 3)


async def test_all_close_clears_internal_copies_but_keeps_pump(link):
    """전체 닫기는 PLC 내부 사본(D04012·D04050)만 지운다 — PC 요청 영역은 그대로다."""
    lk, s, _cfg = link
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_PUMP), 4)
    await at_vacuum(lk, s)
    want = DEV.MANUAL_VALVE_MASK & 0x0003
    assert await manual(lk, want, DEV.AUX_CMD_MASK) == A.RESULT_OK
    assert await wait_until(lambda: s.man_valve == want, 1)
    before = pc_area(s)

    r, _ = await lk.send_command(A.CMD_ALL_CLOSE)
    assert r == A.RESULT_OK
    await asyncio.sleep(0.2)
    assert s.man_valve == 0 and s.man_aux == 0
    assert s.reg[A.D_APPLIED_VALVE] == 0 and s.reg[A.D_APPLIED_AUX] == 0
    assert s.reg[A.D_VALVE_OUT] == 0, "밸브 출력이 남았다"
    assert pc_area(s) == before, "PLC 가 PC 영역을 바꿨다"
    assert await wait_until(lambda: not A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE), 2)
    assert A.bit(s.reg[A.D_AUX_OUT], A.AUX_PUMP), "펌프는 그대로 둬야 한다"


# ===================== 거절 규칙 =====================
async def test_pump_start_blocked_by_alarms(link):
    lk, s, _cfg = link
    for key, bit in (("emo", A.ALM0_EMO), ("pump_alm", A.ALM0_PUMP),
                     ("air", A.ALM0_AIR), ("cw", A.ALM0_CW)):
        s.set_fault(key, True)
        assert await wait_until(lambda b=bit: (s.reg[A.D_ALARM0] >> b) & 1, 3)
        r, _ = await lk.send_command(A.CMD_PUMP_START)
        assert r == A.RESULT_INTERLOCK, key
        s.set_fault(key, False)
        await lk.send_command(A.CMD_ALARM_RESET)
        await asyncio.sleep(0.2)


async def test_manual_apply_blocked_during_safe_stop(link):
    lk, s, _cfg = link
    s.set_fault("emo", True)
    assert await wait_until(lambda: s.safe_stop, 3)
    r, _ = await lk.send_command(A.CMD_MANUAL_APPLY)
    assert r == A.RESULT_INTERLOCK


async def test_safe_stop_clears_internal_copies(link):
    """안전 정지 요구가 되면 수동 밸브·보조 반영(내부 사본)을 지운다 — PC 영역은 그대로.
    원인을 없애도 스스로 다시 켜지지 않는다."""
    lk, s, _cfg = link
    await at_vacuum(lk, s)
    assert await manual(lk, 0x0003, DEV.AUX_CMD_MASK) == A.RESULT_OK
    assert await wait_until(lambda: s.man_valve == 0x0003, 1)
    before = pc_area(s)
    s.set_fault("emo", True)
    assert await wait_until(lambda: s.man_valve == 0 and s.man_aux == 0, 3)
    assert await wait_until(lambda: s.reg[A.D_APPLIED_VALVE] == 0, 1)
    assert s.reg[A.D_VALVE_OUT] == 0
    s.set_fault("emo", False)
    await lk.send_command(A.CMD_ALARM_RESET)
    await asyncio.sleep(0.3)
    assert s.man_valve == 0 and s.reg[A.D_VALVE_OUT] == 0
    assert pc_area(s) == before, "PLC 가 PC 영역을 바꿨다"


async def test_atmosphere_clears_valve_copy(link):
    """챔버 대기압 입력이면 PLC 가 밸브 반영을 매 스캔 지운다(명령 12 결과는 0)."""
    lk, s, _cfg = link
    assert await wait_until(lambda: A.bit(s.reg[A.D_INPUT0], A.IN0_ATM), 1)
    assert await manual(lk, 0x0001) == A.RESULT_OK
    await asyncio.sleep(0.1)
    assert s.man_valve == 0 and s.reg[A.D_VALVE_OUT] == 0
    assert s.reg[A.D_MANUAL_VALVE] == 0x0001, "PC 요청 영역은 PC 가 쓴 그대로여야 한다"


async def test_internal_path_cannot_write_pc_area(link):
    """시뮬레이터 내부 경로로 PC 영역을 쓰면 예외 — Modbus 쓰기 경로만 된다."""
    from peald.simulator import PcAreaWrite
    _lk, s, _cfg = link
    for addr in (A.D_MANUAL_VALVE, A.D_MANUAL_AUX, A.D_HEATER_POWER, A.D_MFC_SV,
                 A.D_PCV_SV, A.D_O3_SV, A.D_PRM_PC_WDT_MS, A.D_PRM_O3_MAX):
        with pytest.raises(PcAreaWrite):
            s.reg[addr] = 1
    s.write(A.D_MANUAL_AUX, [0])        # Modbus 경로는 된다


async def test_rf_request_dropped_when_power_zero(link):
    """PEALD: 명령 12 에서 RF 설정이 0 이면 보조 반영(D04050)을 버린다."""
    if not DEV.HAS_RF:
        pytest.skip("RF 가 있는 장비만")
    lk, s, _cfg = link
    r, _ = await lk.send_command(A.CMD_MANUAL_APPLY,
                                 {A.D_MANUAL_AUX: 1 << A.AUX_RF, A.D_RF_SV: 0})
    assert r == A.RESULT_OK
    await asyncio.sleep(0.1)
    assert s.man_aux == 0 and s.reg[A.D_APPLIED_AUX] == 0


async def test_precursor_and_reactant_together_is_blocked(link):
    """★ ALD 의 전제가 무너지는 조합 — 둘 다 막고 알람을 건다.

    인터락 b6(동시 요청)은 '지금 그런 요청이 있다'는 순간 표시라 래치되지 않는다.
    동시 개방 알람은 중대라서 안전 정지가 걸리고, 안전 정지가 수동 요청을 지우면
    b6 은 곧 0 으로 돌아간다 — 그래서 알람이 뜨기까지 지켜보며 잡는다.
    """
    lk, s, _cfg = link
    pre = 1 << DEV.PRECURSOR_VALVE_BITS[0]
    rea = 1 << DEV.REACTANT_VALVE_BITS[0]
    saw_both_req = []
    opened = []

    def watch():
        if (s.reg[A.D_INTERLOCK] >> A.ILK_BOTH_REQ) & 1:
            saw_both_req.append(True)
        if s.reg[A.D_VALVE_OUT] & (pre | rea):
            opened.append(s.reg[A.D_VALVE_OUT])
        return (s.reg[A.D_ALARM0] >> A.ALM0_BOTH_OPEN) & 1

    await at_vacuum(lk, s)
    await manual(lk, pre | rea)
    assert await wait_until(watch, 3, step=0.01), "동시 개방 알람이 걸리지 않았다"
    assert not opened, f"밸브가 열렸다: {opened}"
    assert saw_both_req, "동시 요청 인터락(b6)이 한 번도 서지 않았다"
    assert s.safe_stop, "중대 알람인데 안전 정지가 걸리지 않았다"


# ===================== 알람 =====================
async def test_alarm_latch_ack_reset(link):
    lk, s, _cfg = link
    s.set_fault("leak", True)
    assert await wait_until(lambda: (s.reg[A.D_ALARM0] >> A.ALM0_LEAK) & 1, 3)
    assert s.reg[A.D_ALARM_NEW] == 1, "새 알람 표시(부저)가 서지 않았다"

    # 확인(6)은 부저만 끈다 — 알람은 남는다
    r, _ = await lk.send_command(A.CMD_ALARM_ACK)
    assert r == A.RESULT_OK
    await asyncio.sleep(0.2)
    assert s.reg[A.D_ALARM_NEW] == 0
    assert (s.reg[A.D_ALARM0] >> A.ALM0_LEAK) & 1

    # 원인이 남아 있으면 리셋해도 다시 걸린다
    await lk.send_command(A.CMD_ALARM_RESET)
    await asyncio.sleep(0.3)
    assert (s.reg[A.D_ALARM0] >> A.ALM0_LEAK) & 1

    s.set_fault("leak", False)
    await lk.send_command(A.CMD_ALARM_RESET)
    assert await wait_until(lambda: not ((s.reg[A.D_ALARM0] >> A.ALM0_LEAK) & 1), 3)


async def test_critical_alarm_sets_safe_stop_state(link):
    lk, s, _cfg = link
    s.set_fault("leak", True)     # 중대 알람
    assert await wait_until(lambda: s.reg[A.D_STATE] == A.STATE_SAFE_STOP, 3)
    assert (s.reg[A.D_INTERLOCK] >> A.ILK_SAFE_STOP_REQ) & 1


async def test_warning_alarm_does_not_stop(link):
    """경고(예: 대기압 도달 시간 초과)는 표시·기록만 한다."""
    lk, s, _cfg = link
    s._latch0(A.ALM0_VENT_TIMEOUT)
    await asyncio.sleep(0.2)
    assert not s.safe_stop
    assert s.reg[A.D_STATE] != A.STATE_SAFE_STOP


# ===================== 레시피 표 검사 =====================
async def test_recipe_table_checksum(link):
    lk, s, _cfg = link
    # 유효한 최소 표: 스텝 1, 블록 1, 그룹 0 + 합계
    s.write(A.D_RCP_STEP_COUNT, [1, 1, 0])
    total = 0
    for a in range(A.RCP_SUM_BASE, A.RCP_SUM_END + 1):
        if a != A.D_RCP_SUM:
            total = (total + s.reg[a]) & 0xFFFF
    s.write(A.D_RCP_SUM, [total])
    assert await wait_until(lambda: s.reg[A.D_RECIPE_OK] == 1, 4), "합계가 맞는데 통과하지 않았다"
    assert s.reg[A.D_RECIPE_SUM_PLC] == total

    # 합계를 흔들면 불합격 — ★ 주기 검사는 알람을 래치하지 않는다(올리는 도중 헛알람)
    s.write(A.D_RCP_SUM, [(total + 1) & 0xFFFF])
    assert await wait_until(lambda: s.reg[A.D_RECIPE_OK] == 0, 4)
    assert not (s.reg[A.D_ALARM0] >> A.ALM0_RECIPE) & 1


async def test_recipe_range_limits(link):
    lk, s, _cfg = link
    s.write(A.D_RCP_STEP_COUNT, [A.RCP_STEP_MAX + 1, 1])      # 스텝 수 범위 밖
    total = 0
    for a in range(A.RCP_SUM_BASE, A.RCP_SUM_END + 1):
        if a != A.D_RCP_SUM:
            total = (total + s.reg[a]) & 0xFFFF
    s.write(A.D_RCP_SUM, [total])
    assert await wait_until(lambda: s.reg[A.D_RECIPE_OK] == 0, 4)


# ===================== 환산 일치 =====================
async def test_cvg_raw_matches_configured_conversion(link):
    """시뮬레이터가 만든 원시값을 화면 환산으로 되돌리면 같은 압력이 나와야 한다."""
    lk, s, _cfg = link
    await asyncio.sleep(0.3)
    raw = s.reg[A.D_CVG_RAW]
    shown = lk.conv.cvg.to_torr(raw)
    assert shown == pytest.approx(s.pressure, rel=0.02)


# ===================== 펌핑 · 벤트 (래더 확정 동작) =====================
async def test_vent_while_pumping_closes_ive_then_opens_vv(link):
    """펌핑 중(IV-E 열림)에도 벤트를 받는다 — 배기 요청을 내려 IV-E 가 닫힌 뒤 VV 를 연다."""
    lk, s, _cfg = link
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(s.reg[A.D_INPUT0], A.IN0_IVE_OPEN), 5)
    assert not A.bit(s.reg[A.D_INTERLOCK], A.ILK_VENT_OK)
    r, _ = await lk.send_command(A.CMD_VENT)
    assert r == A.RESULT_OK
    assert await wait_until(lambda: not A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE), 1)
    assert await wait_until(lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_VV), 4), "VV 가 열리지 않았다"


async def test_emo_closes_ive(link):
    """비상정지로 배기 요청이 지워지면 IV-E 출력도 바로 닫힌다."""
    lk, s, _cfg = link
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE), 5)
    s.set_fault("emo", True)
    assert await wait_until(lambda: not A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE), 1)
    assert not s.exh_req and not s.pump_req


async def test_pump_start_after_emo_release_before_reset(link):
    """PLC 는 비상정지 '입력'을 본다 — 풀고 알람 리셋 전에도 펌핑 시작 결과 0."""
    lk, s, _cfg = link
    s.set_fault("emo", True)
    assert await wait_until(lambda: (s.reg[A.D_ALARM0] >> A.ALM0_EMO) & 1, 3)
    r, _ = await lk.send_command(A.CMD_PUMP_START)
    assert r == A.RESULT_INTERLOCK
    s.set_fault("emo", False)
    await asyncio.sleep(0.1)
    assert (s.reg[A.D_ALARM0] >> A.ALM0_EMO) & 1, "알람은 아직 래치돼 있어야 한다"
    r, _ = await lk.send_command(A.CMD_PUMP_START)
    assert r == A.RESULT_OK


def test_vent_permit_not_during_prep(cfg):
    """벤트 허가는 공정 준비(블록 준비) 중에도 서지 않는다."""
    from peald.simulator import PlcSim
    s = PlcSim(cfg, 1)
    assert s._vent_ok()
    s.running, s.seq_state = True, 3
    assert not s._vent_ok()


# ===================== 명령 결과 (래더 P40) =====================
def _one_block_words(cfg):
    from peald import recipe as R
    from peald.convert import Converters
    r = R.empty_recipe("t")
    b = R.empty_block("b")
    b["steps"] = [{"name": "s", "time_ms": 50, "valves": [], "pause_ok": False}]
    if DEV.HAS_RF:
        b["steps"][0]["rf"] = False
    r["blocks"] = [b]
    return list(R.to_plc_words(cfg, Converters(cfg), r)["words"])


def test_start_results(cfg):
    """공정 시작 — 허가 없음 1 / 허가 있어도 그 순간 표가 틀리면 3 + b13 / 동작 중 2."""
    from peald.simulator import PlcSim
    s = PlcSim(cfg, 1)
    assert s._execute(A.CMD_PROCESS_START) == A.RESULT_INTERLOCK

    words = _one_block_words(cfg)
    s.write(A.RCP_SUM_BASE, words)
    s.reg[A.D_INTERLOCK] = 1 << A.ILK_START_OK      # 1 s 전 검사로 허가가 선 상태
    s.write(A.D_RCP_STEP_BASE, [s.reg[A.D_RCP_STEP_BASE] ^ 1])  # 그 뒤 표가 흠집 남
    assert s._execute(A.CMD_PROCESS_START) == A.RESULT_RECIPE
    assert (s.reg[A.D_ALARM0] >> A.ALM0_RECIPE) & 1
    assert not s.running

    s.write(A.RCP_SUM_BASE, words)
    assert s._execute(A.CMD_PROCESS_START) == A.RESULT_OK
    assert s.running
    assert s._execute(A.CMD_PROCESS_START) == A.RESULT_STATE


def test_start_rejects_bad_first_group(cfg):
    """첫 그룹 적재 오류(끝 > 블록 수)는 결과 3 + b13 — 공정이 시작되지 않는다."""
    from peald.simulator import PlcSim
    from peald import recipe as R
    s = PlcSim(cfg, 1)
    words = _one_block_words(cfg)
    words[A.D_RCP_GROUP_COUNT - A.RCP_SUM_BASE] = 1
    g = A.D_RCP_GROUP_BASE - A.RCP_SUM_BASE
    words[g:g + 3] = [1, 5, 1]                          # 끝 블록 5 > 블록 수 1
    words[A.D_RCP_SUM - A.RCP_SUM_BASE] = R.checksum_of(words)
    s.write(A.RCP_SUM_BASE, words)
    s.reg[A.D_INTERLOCK] = 1 << A.ILK_START_OK
    assert s._execute(A.CMD_PROCESS_START) == A.RESULT_RECIPE
    assert (s.reg[A.D_ALARM0] >> A.ALM0_RECIPE) & 1 and not s.running


async def test_recipe_check_does_not_latch_while_uploading(link):
    """PC 가 올리는 도중(본문만 쓰인 상태)에 주기 검사가 돌아도 b13 이 래치되지 않는다."""
    lk, s, _cfg = link
    s.write(A.D_RCP_STEP_BASE, [100, 0, 0, 0, 50, 0, 0, 0])
    s.write(A.D_RCP_STEP_COUNT, [1, 1, 0, 0x1234])        # 합계가 안 맞는 헤더
    await asyncio.sleep(1.3)
    assert s.reg[A.D_RECIPE_OK] == 0
    assert not (s.reg[A.D_ALARM0] >> A.ALM0_RECIPE) & 1


def test_abort_when_stopped_is_noop(cfg):
    from peald.simulator import PlcSim
    s = PlcSim(cfg, 1)
    assert s._execute(A.CMD_ABORT) == A.RESULT_OK
    assert not s.running


# ===================== 히터 (래더 확정 동작) =====================
async def test_heater_power_is_internal_copy(link):
    """명령 13 때만 D01010 을 전원 묶음으로 복사한다 — 그 뒤 D01010 을 바꿔도 출력은 그대로."""
    lk, s, _cfg = link
    r, _ = await lk.send_command(A.CMD_HEATER_APPLY, {A.D_HEATER_POWER: 0x0003})
    assert r == A.RESULT_OK
    assert s.heater_power == 0x0003 & DEV.HEATER_POWER_MASK
    s.write(A.D_HEATER_POWER, [0])
    await asyncio.sleep(0.1)
    assert s.heater_power == 0x0003 & DEV.HEATER_POWER_MASK


async def test_over_temp_latch_turns_heaters_off(link):
    """과온 알람 래치 중에는 매 스캔 전원 묶음 = 0 — PC 영역 D01010 은 PLC 가 건드리지 않는다."""
    lk, s, _cfg = link
    await lk.send_command(A.CMD_HEATER_APPLY, {A.D_HEATER_POWER: 0x0001})
    assert s.heater_power
    s.set_fault("ot", True)
    assert await wait_until(lambda: s.heater_power == 0, 1)
    s.set_fault("ot", False)
    await lk.send_command(A.CMD_ALARM_RESET)
    await asyncio.sleep(0.2)
    assert s.heater_power == 0, "리셋 뒤에도 스스로 다시 켜지지 않는다"


def test_soft_over_temp_only_for_limited_channels(cfg):
    """한계 0 인 채널은 소프트 과온 감시만 안 한다 — 한계가 있으면 넘을 때 b9."""
    from peald.simulator import PlcSim
    s = PlcSim(cfg, 1)
    s.write(A.D_PRM_HEATER_MAX, [0, 1000])              # CH1 한계 없음, CH2 100.0 ℃
    s.heater_pv[0] = 500.0
    assert not s._over_temp()
    s.heater_pv[1] = 120.0
    assert s._over_temp()


# ===================== PC 영역을 쓰지 않는다 =====================
async def test_scenarios_never_write_pc_area(link):
    """공정 준비·전체 닫기·안전 정지·대기압 시나리오 전후로 PC 영역이 그대로다."""
    lk, s, cfg = link
    await lk.send_command(A.CMD_PUMP_START)
    await at_vacuum(lk, s)
    await manual(lk, 0x0002, DEV.AUX_CMD_MASK)
    await lk.send_command(A.CMD_MFC_APPLY, {A.D_MFC_SV: [1000] * DEV.MFC_COUNT})
    await lk.send_command(A.CMD_HEATER_APPLY, {A.D_HEATER_POWER: 0x0001})
    before = pc_area(s)
    # 공정: 표를 올리고 시작 → 블록 적재 → 중단(공정 종료 정리)
    s.write(A.RCP_SUM_BASE, _one_block_words(cfg))
    assert await wait_until(lambda: s.reg[A.D_RECIPE_OK] == 1, 3)
    assert s._process_start() == A.RESULT_OK
    await asyncio.sleep(0.2)
    await lk.send_command(A.CMD_ABORT)
    await lk.send_command(A.CMD_ALL_CLOSE)
    s.set_fault("emo", True)
    await asyncio.sleep(0.2)
    s.set_fault("emo", False)
    await lk.send_command(A.CMD_ALARM_RESET)
    s.base_pressure = 760.0
    await asyncio.sleep(0.3)
    assert pc_area(s) == before, "PLC 가 PC 영역을 바꿨다"



# ===================== v0.4.1 출력 단계 (래더 P60) =====================
async def pumped_permit(lk, s):
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(s.reg[A.D_INTERLOCK], A.ILK_VALVE_OK), 20)


async def test_valve_output_needs_valve_permit(link):
    lk, s, _cfg = link
    await at_vacuum(lk, s)
    await manual(lk, 0x0004)
    await asyncio.sleep(0.2)
    assert s.man_valve == 0x0004 and s.reg[A.D_VALVE_OUT] == 0, "허가 없이 밸브가 나갔다"
    await pumped_permit(lk, s)
    assert await wait_until(lambda: s.reg[A.D_VALVE_OUT] == 0x0004, 2)
    s.set_fault("emo", True)                      # 펌프가 멈추고 허가가 빠진다
    assert await wait_until(lambda: s.reg[A.D_VALVE_OUT] == 0, 2)


async def test_both_request_zeroes_all_valves(link):
    """전구체+반응물 동시 요청이면 다른 밸브까지 모든 출력 0 + 알람0 b15."""
    lk, s, _cfg = link
    await pumped_permit(lk, s)
    other = next(v["bit"] for v in DEV.VALVES if v["bit"] not in
                 DEV.PRECURSOR_VALVE_BITS + DEV.REACTANT_VALVE_BITS and not v.get("auto"))
    pre, rea = 1 << DEV.PRECURSOR_VALVE_BITS[0], 1 << DEV.REACTANT_VALVE_BITS[0]
    seen = []
    await manual(lk, pre | rea | (1 << other))

    def watch():
        seen.append(s.reg[A.D_VALVE_OUT])
        return (s.reg[A.D_ALARM0] >> A.ALM0_BOTH_OPEN) & 1

    assert await wait_until(watch, 3, step=0.01)
    assert all(v == 0 for v in seen), f"동시 요청인데 밸브가 나갔다: {[hex(v) for v in seen if v]}"


async def test_pump_start_clears_vent_request(link):
    """펌핑 시작은 벤트 요청을 지운다 — VV 가 바로 닫히고 IV-E 가 열린다."""
    lk, s, _cfg = link
    await pumped_permit(lk, s)
    await lk.send_command(A.CMD_VENT)
    assert await wait_until(lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_VV), 4)
    r, _ = await lk.send_command(A.CMD_PUMP_START)
    assert r == A.RESULT_OK
    assert not s.vent_req
    assert await wait_until(lambda: not A.bit(s.reg[A.D_AUX_OUT], A.AUX_VV), 0.5), "VV 가 닫히지 않았다"
    assert await wait_until(lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE), 3), "IV-E 가 열리지 않았다"


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
async def test_pv_b_follows_generator_and_pv_r(link):
    """발생기 운전 중 PV-R 요청이 없으면 PV-B 열림(허가 없어도), PV-R 을 요청하면 PV-B 닫힘."""
    lk, s, _cfg = link
    await at_vacuum(lk, s)                      # 대기압이면 PLC 가 PV-R 요청을 바로 지운다
    s.write(A.D_PRM_O3_MAX, [16000])
    pvb = 1 << DEV.valve_bit("PV-B")
    pvr = 1 << DEV.valve_bit("PV-R")
    lo_aux = (1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB) | (1 << A.AUX_O3_GEN)
    await lk.send_command(A.CMD_MANUAL_APPLY, {A.D_MANUAL_VALVE: [0, 0, 0, 0], A.D_MANUAL_AUX: lo_aux,
                                               A.D_O3_SV: 1000})
    assert await wait_until(lambda: s.o3_gen_on, 10), "발생기가 켜지지 않았다"
    assert not A.bit(s.reg[A.D_INTERLOCK], A.ILK_VALVE_OK)
    assert await wait_until(lambda: s.reg[A.D_VALVE_OUT] & pvb, 1), "PV-B 가 열리지 않았다"
    await lk.send_command(A.CMD_MANUAL_APPLY, {A.D_MANUAL_VALVE: [pvr, 0, 0, 0], A.D_MANUAL_AUX: lo_aux,
                                               A.D_O3_SV: 1000})
    assert await wait_until(lambda: not (s.reg[A.D_VALVE_OUT] & pvb), 1), "PV-R 요청인데 PV-B 가 열려 있다"


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
async def test_rf_output_needs_rf_permit(link):
    lk, s, _cfg = link
    s.write(A.D_PRM_RF_MAX, [16000])
    await lk.send_command(A.CMD_MANUAL_APPLY, {A.D_MANUAL_AUX: 1 << A.AUX_RF, A.D_RF_SV: 1000})
    await asyncio.sleep(0.2)
    assert s.man_aux & (1 << A.AUX_RF)
    assert not A.bit(s.reg[A.D_INTERLOCK], A.ILK_RF_OK)
    assert not A.bit(s.reg[A.D_AUX_OUT], A.AUX_RF), "RF 허가 없이 RF 가 켜졌다"


async def test_simulator_publishes_device_id(link):
    _lk, s, _cfg = link
    assert await wait_until(lambda: s.reg[A.D_DEVICE_ID] == DEV.DEVICE_ID, 1)
