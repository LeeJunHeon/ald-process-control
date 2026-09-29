"""내장 시뮬레이터 — 펌핑·벤트·전체 닫기·거절 규칙·알람 래치/확인/리셋.

물리값은 대략이어도 되지만 상태 코드·결과 코드·비트 의미는 정확해야 한다.
"""
import asyncio

import pytest

from peald import addresses as A
from peald import device as DEV
from conftest import wait_until


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


async def test_all_close_clears_requests_but_keeps_pump(link):
    lk, s, _cfg = link
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_PUMP), 4)
    s.reg[A.D_MANUAL_VALVE] = DEV.MANUAL_VALVE_MASK & 0x0007
    s.reg[A.D_MANUAL_AUX] = DEV.AUX_CMD_MASK

    r, _ = await lk.send_command(A.CMD_ALL_CLOSE)
    assert r == A.RESULT_OK
    await asyncio.sleep(0.2)
    assert s.reg[A.D_MANUAL_VALVE] == 0
    assert s.reg[A.D_MANUAL_AUX] == 0
    assert not A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE)
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


async def test_safe_stop_clears_valve_requests(link):
    """안전 정지 요구가 되면 공정 밸브·수동 보조 요청을 지운다(자동으로 다시 켜지지 않음)."""
    lk, s, _cfg = link
    s.reg[A.D_MANUAL_VALVE] = 0x0003
    s.reg[A.D_MANUAL_AUX] = DEV.AUX_CMD_MASK
    s.set_fault("emo", True)
    assert await wait_until(lambda: s.reg[A.D_MANUAL_VALVE] == 0, 3)
    assert s.reg[A.D_MANUAL_AUX] == 0
    # 원인을 없애도 스스로 다시 켜지지 않는다
    s.set_fault("emo", False)
    await lk.send_command(A.CMD_ALARM_RESET)
    await asyncio.sleep(0.3)
    assert s.reg[A.D_MANUAL_VALVE] == 0


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

    s.reg[A.D_MANUAL_VALVE] = pre | rea
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
    s.reg[A.D_RCP_STEP_COUNT] = 1
    s.reg[A.D_RCP_BLOCK_COUNT] = 1
    s.reg[A.D_RCP_GROUP_COUNT] = 0
    total = 0
    for a in range(A.RCP_SUM_BASE, A.RCP_SUM_END + 1):
        if a != A.D_RCP_SUM:
            total = (total + s.reg[a]) & 0xFFFF
    s.reg[A.D_RCP_SUM] = total
    assert await wait_until(lambda: s.reg[A.D_RECIPE_OK] == 1, 4), "합계가 맞는데 통과하지 않았다"
    assert s.reg[A.D_RECIPE_SUM_PLC] == total

    # 합계를 흔들면 불합격 + 경고 알람
    s.reg[A.D_RCP_SUM] = (total + 1) & 0xFFFF
    assert await wait_until(lambda: s.reg[A.D_RECIPE_OK] == 0, 4)
    assert (s.reg[A.D_ALARM0] >> A.ALM0_RECIPE) & 1


async def test_recipe_range_limits(link):
    lk, s, _cfg = link
    s.reg[A.D_RCP_STEP_COUNT] = A.RCP_STEP_MAX + 1      # 범위 밖
    s.reg[A.D_RCP_BLOCK_COUNT] = 1
    total = 0
    for a in range(A.RCP_SUM_BASE, A.RCP_SUM_END + 1):
        if a != A.D_RCP_SUM:
            total = (total + s.reg[a]) & 0xFFFF
    s.reg[A.D_RCP_SUM] = total
    assert await wait_until(lambda: s.reg[A.D_RECIPE_OK] == 0, 4)


# ===================== 환산 일치 =====================
async def test_cvg_raw_matches_configured_conversion(link):
    """시뮬레이터가 만든 원시값을 화면 환산으로 되돌리면 같은 압력이 나와야 한다."""
    lk, s, _cfg = link
    await asyncio.sleep(0.3)
    raw = s.reg[A.D_CVG_RAW]
    shown = lk.conv.cvg.to_torr(raw)
    assert shown == pytest.approx(s.pressure, rel=0.02)
