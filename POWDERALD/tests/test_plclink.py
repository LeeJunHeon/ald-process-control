"""PLC 링크 — 명령 핸드셰이크 · 하트비트 · PRM 쓰기/되읽기.

★ 핸드셰이크가 어긋나면 "보냈는데 안 됐다" 또는 "두 번 실행됐다"가 된다.
  번호 되감기·시간 초과·재연결 후 번호 이어 가기를 전부 고정한다.
"""
import asyncio

import pytest

from powderald import addresses as A
from powderald import device as DEV
from conftest import wait_until


async def new_link(sim, events=None):
    """이벤트를 모으는 링크를 새로 붙인다."""
    from powderald.convert import Converters
    from powderald.plclink import PlcLink
    s, _port, cfg = sim
    lk = PlcLink(cfg, Converters(cfg),
                 on_event=(lambda lvl, msg: events.append((lvl, msg))) if events is not None else None)
    lk.start()
    assert await wait_until(lambda: lk.connected, 5)
    return lk


async def test_status_is_polled(link):
    lk, s, _cfg = link
    assert await wait_until(lambda: lk.status[A.D_PLC_HB] != 0, 3)
    assert lk.plc_hb_ok


async def test_pc_heartbeat_is_written(link):
    """★ 하트비트는 화면·명령과 무관하게 계속 돌아야 한다.
    멈추면 PLC 가 PC 끊김으로 보고 멀쩡한 공정을 세운다."""
    lk, s, _cfg = link
    first = s.reg[A.D_PC_HB]
    assert await wait_until(lambda: s.reg[A.D_PC_HB] != first, 3)


async def test_params_written_and_readback_matches(link):
    lk, s, cfg = link
    assert lk.prm_mismatch == [], lk.prm_mismatch
    # 베이스 압력은 역함수로 원시값이 되어 들어간다
    want = lk.conv.cvg.to_raw(cfg["params"]["base_press_torr"])
    assert s.reg[A.D_PRM_BASE_PRESS] == want
    assert s.reg[A.D_PRM_PC_WDT_MS] == cfg["params"]["pc_wdt_ms"]
    assert s.reg[A.D_PRM_VALVE_MIN_MS] == cfg["params"]["valve_min_ms"]


async def test_heater_limit_none_writes_zero(link):
    """max_c 가 null 인 채널은 PLC 한계에 0 — PLC 는 그 채널의 소프트 과온 감시만 끈다
    (전원은 막지 않는다 → PC 가 설정·전원 켜기를 거절한다)."""
    lk, s, cfg = link
    for i, h in enumerate(cfg["heaters"]):
        want = 0 if h.get("max_c") is None else int(round(h["max_c"] * 10))
        assert s.reg[A.D_PRM_HEATER_MAX + i] == want, f"CH{h['ch']}"


@pytest.mark.parametrize("code,want", [
    (A.CMD_ALARM_ACK, A.RESULT_OK),
    (A.CMD_PUMP_START, A.RESULT_OK),
    (A.CMD_PUMP_STOP, A.RESULT_OK),
    (A.CMD_ALL_CLOSE, A.RESULT_OK),
    (99, A.RESULT_UNKNOWN),
])
async def test_command_results(link, code, want):
    lk, _s, _cfg = link
    result, _text = await lk.send_command(code)
    assert result == want


async def test_process_start_rejected_without_permit(link):
    """공정 시작 허가(인터락 b3 — 레시피 표 통과 포함)가 없으면 결과 1."""
    lk, _s, _cfg = link
    result, _ = await lk.send_command(A.CMD_PROCESS_START)
    assert result == A.RESULT_INTERLOCK


async def test_pause_rejected_when_not_running(link):
    lk, _s, _cfg = link
    result, _ = await lk.send_command(A.CMD_PAUSE)
    assert result == A.RESULT_STATE


async def test_command_number_increments_and_wraps(link):
    """65535 다음은 0. 되감기에서 멈추면 그 뒤 모든 명령이 무시된다."""
    lk, s, _cfg = link
    lk._cmd_no = 65535
    r1, _ = await lk.send_command(A.CMD_ALARM_ACK)
    assert r1 == A.RESULT_OK and s.reg[A.D_ACK_NO] == 65535
    assert lk._cmd_no == 0
    r2, _ = await lk.send_command(A.CMD_ALARM_ACK)
    assert r2 == A.RESULT_OK and s.reg[A.D_ACK_NO] == 0


async def test_timeout_does_not_resend(link, monkeypatch):
    """★ 응답이 없다고 같은 명령을 다시 보내면 PLC 가 두 번 실행할 수 있다.
    한 번만 쓰고 포기해야 한다."""
    lk, s, _cfg = link
    writes = []
    orig = lk.client.write_single

    async def spy(addr, value):
        writes.append((addr, value))
        if addr == A.D_CMD_NO:
            # 번호를 쓴 척만 하고 PLC 에 전달하지 않는다 → 응답이 영원히 오지 않는다
            return
        return await orig(addr, value)

    monkeypatch.setattr(lk.client, "write_single", spy)
    monkeypatch.setattr("powderald.plclink.ACK_TIMEOUT_S", 0.3)
    result, text = await lk.send_command(A.CMD_ALARM_ACK)
    assert result is None and "응답 없음" in text
    assert len([w for w in writes if w[0] == A.D_CMD_NO]) == 1, "명령 번호를 두 번 썼다"


async def test_command_number_continues_after_reconnect(sim):
    """재연결하면 PLC 에 남아 있는 번호 다음부터 이어 간다.
    0부터 다시 시작하면 PLC 가 '이미 처리한 번호'로 보고 무시한다."""
    from powderald.convert import Converters
    from powderald.plclink import PlcLink
    s, port, cfg = sim
    lk = PlcLink(cfg, Converters(cfg))
    lk.start()
    assert await wait_until(lambda: lk.connected, 5)
    await lk.send_command(A.CMD_ALARM_ACK)
    await lk.send_command(A.CMD_ALARM_ACK)
    last = s.reg[A.D_CMD_NO]
    await lk.stop()

    lk2 = PlcLink(cfg, Converters(cfg))
    lk2.start()
    assert await wait_until(lambda: lk2.connected, 5)
    assert lk2._cmd_no == (last + 1) & 0xFFFF
    await lk2.stop()


async def test_plc_hb_stall_is_detected(link, monkeypatch):
    """통신은 되는데 PLC 가 스캔을 안 돌리는 경우(STOP)를 잡는다."""
    lk, s, _cfg = link
    assert lk.plc_hb_ok
    monkeypatch.setattr("powderald.plclink.PLC_HB_STALL_S", 0.3)
    # 시뮬레이터의 하트비트를 멈춘다
    monkeypatch.setattr(s, "_plc_heartbeat", lambda now: None)
    assert await wait_until(lambda: not lk.plc_hb_ok, 3)


async def test_pc_link_alarm_and_reset(link, monkeypatch):
    """PC 하트비트가 멈추면 PC 끊김 알람 + 안전 정지 요구.
    다시 보내고 리셋하면 풀린다."""
    lk, s, _cfg = link
    s.write(A.D_PRM_PC_WDT_MS, [300])
    s.set_fault("pc_hb_stop", True)
    assert await wait_until(lambda: (s.reg[A.D_ALARM0] >> A.ALM0_PC_LINK) & 1, 4)
    assert await wait_until(lambda: (s.reg[A.D_INTERLOCK] >> A.ILK_SAFE_STOP_REQ) & 1, 3)
    assert s.reg[A.D_STATE] == A.STATE_SAFE_STOP

    # 원인이 남아 있으면 리셋해도 다시 걸린다
    await lk.send_command(A.CMD_ALARM_RESET)
    await asyncio.sleep(0.3)
    assert (s.reg[A.D_ALARM0] >> A.ALM0_PC_LINK) & 1

    s.set_fault("pc_hb_stop", False)
    assert await wait_until(lambda: s.pc_link_ok, 4)
    r, _ = await lk.send_command(A.CMD_ALARM_RESET)
    assert r == A.RESULT_OK
    assert await wait_until(lambda: not ((s.reg[A.D_ALARM0] >> A.ALM0_PC_LINK) & 1), 3)
    assert await wait_until(lambda: s.reg[A.D_STATE] == A.STATE_IDLE, 3)


# ===================== 수동 요청 기준 = PLC 반영 영역 =====================
async def test_reconnect_sync_uses_applied_area(sim):
    """재연결 뒤 '지금 수동 요청'은 PC 영역(D01004)이 아니라 반영 영역(D04012)이다.
    PC 영역에 남은 옛 요청은 다음 명령 12 에서 되살아나지 않는다."""
    s, _port, _cfg = sim
    s.base_pressure = 1.0
    assert await wait_until(lambda: not A.bit(s.reg[A.D_INPUT0], A.IN0_ATM), 2)
    s.write(A.D_MANUAL_VALVE, [0x0002])         # 옛 요청 — PLC 가 이미 지운 것
    s.man_valve = 0x0001                         # PLC 가 지금 반영하고 있는 것
    assert await wait_until(lambda: s.reg[A.D_APPLIED_VALVE] == 0x0001, 1)
    lk = await new_link(sim)
    try:
        assert lk.applied_valve == 0x0001
        res = await lk.manual_apply(lambda cur: ({**cur, "valve": cur["valve"] | 0x0004}, ""))
        assert res["result"] == A.RESULT_OK
        assert s.reg[A.D_MANUAL_VALVE] == 0x0005, f"{s.reg[A.D_MANUAL_VALVE]:#06x}"
        assert s.man_valve == 0x0005
    finally:
        await lk.stop()


async def test_manual_apply_reports_lost_bits_at_atmosphere(link):
    """대기압 입력이면 결과 0 뒤 반영 영역을 다시 읽어 '반영 안 됨' 비트를 돌려준다."""
    lk, s, _cfg = link
    assert await wait_until(lambda: A.bit(lk.status[A.D_INPUT0], A.IN0_ATM), 2)
    res = await lk.manual_apply(lambda cur: ({**cur, "valve": cur["valve"] | 0x0002}, ""))
    assert res["result"] == A.RESULT_OK
    assert res["lost_valve"] == 0x0002


async def test_periodic_compare_logs_plc_clear(sim):
    """명령이 없었는데 반영 비트가 줄면 이름과 사유를 남긴다."""
    s, _port, _cfg = sim
    s.base_pressure = 1.0
    events = []
    lk = await new_link(sim, events)
    try:
        assert await wait_until(lambda: not A.bit(lk.status[A.D_INPUT0], A.IN0_ATM), 3)
        res = await lk.manual_apply(lambda cur: ({**cur, "valve": 0x0002}, ""))
        assert res["result"] == A.RESULT_OK and not res["lost_valve"]
        await asyncio.sleep(0.7)                   # 주기 읽기 한 번 이상
        s.set_fault("emo", True)
        assert await wait_until(lambda: any("수동 요청을 지웠습니다" in m for _l, m in events), 3), events
        msg = next(m for _l, m in events if "수동 요청을 지웠습니다" in m)
        assert "PV-2" in msg and "안전 정지 요구" in msg
    finally:
        await lk.stop()


async def test_mfc_apply_keeps_other_channels(link):
    """명령 14 는 D04121~ 현재 값에 이번 변경만 얹어 MFC 개수만큼 전부 쓴다."""
    lk, s, _cfg = link
    for no in range(1, DEV.MFC_COUNT + 1):
        s.ao[no] = 100 * no
    assert await wait_until(lambda: s.reg[A.DISPLAY_BASE + DEV.MFC_COUNT] == 100 * DEV.MFC_COUNT, 1)
    r, _ = await lk.mfc_apply({1: 999})
    assert r == A.RESULT_OK
    assert s.ao[1] == 999
    for no in range(2, DEV.MFC_COUNT + 1):
        assert s.ao[no] == 100 * no, f"MFC{no} 가 바뀌었다"


# ===================== 히터 =====================
async def test_heater_trip_zeroes_pc_request(sim):
    """과온 알람이 새로 켜지면 PC 가 D01010 = 0 을 쓰고 로그를 남긴다."""
    s, _port, _cfg = sim
    events = []
    lk = await new_link(sim, events)
    try:
        res = await lk.heater_apply(lambda p, sv: (0x0001, sv, ""))
        assert res["result"] == A.RESULT_OK and s.heater_power == 0x0001
        assert lk.cmd_reg(A.D_HEATER_POWER) == 0x0001, "명령 13 성공 직후 쓴 값으로 갱신"
        s.set_fault("ot", True)
        assert await wait_until(lambda: s.reg[A.D_HEATER_POWER] == 0, 3)
        assert lk.cmd_reg(A.D_HEATER_POWER) == 0
        assert any("과온 차단" in m for _l, m in events)
    finally:
        await lk.stop()


async def test_connect_with_over_temp_latched_zeroes_request(sim):
    s, _port, _cfg = sim
    s.write(A.D_HEATER_POWER, [0x0003])
    s.set_fault("ot", True)
    assert await wait_until(lambda: (s.reg[A.D_ALARM0] >> A.ALM0_OT) & 1, 2)
    lk = await new_link(sim)
    try:
        assert s.reg[A.D_HEATER_POWER] == 0
        assert lk.cmd_reg(A.D_HEATER_POWER) == 0
    finally:
        await lk.stop()


async def test_heater_apply_reverted_when_rejected(link, monkeypatch):
    """명령 13 이 거절되면 방금 쓴 D01010·D01012~ 를 이전 값으로 되돌려 쓴다."""
    lk, s, _cfg = link
    s.write(A.D_HEATER_POWER, [0x0001])
    s.write(A.D_HEATER_SV, [500])
    monkeypatch.setattr(s, "_execute", lambda code: A.RESULT_STATE)
    res = await lk.heater_apply(lambda p, sv: (0x0003, [800] + sv[1:], ""))
    assert res["result"] == A.RESULT_STATE and res["reverted"]
    assert s.reg[A.D_HEATER_POWER] == 0x0001
    assert s.reg[A.D_HEATER_SV] == 500


async def test_cmd_area_is_reread(link):
    """명령 영역은 1 s 마다 되읽는다 — 다른 경로로 바뀐 값도 화면에 따라온다."""
    lk, s, _cfg = link
    s.write(A.D_HEATER_SV + 1, [800])
    assert await wait_until(lambda: lk.cmd_reg(A.D_HEATER_SV + 1) == 800, 2.5)


def test_periods_follow_config(cfg):
    from powderald.convert import Converters
    from powderald.plclink import PlcLink
    cfg["plc"]["poll_ms"] = 250
    cfg["plc"]["heartbeat_ms"] = 400
    lk = PlcLink(cfg, Converters(cfg))
    assert lk.poll_s == pytest.approx(0.25) and lk.heartbeat_s == pytest.approx(0.4)
