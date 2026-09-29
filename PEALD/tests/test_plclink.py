"""PLC 링크 — 명령 핸드셰이크 · 하트비트 · PRM 쓰기/되읽기.

★ 핸드셰이크가 어긋나면 "보냈는데 안 됐다" 또는 "두 번 실행됐다"가 된다.
  번호 되감기·시간 초과·재연결 후 번호 이어 가기를 전부 고정한다.
"""
import asyncio

import pytest

from peald import addresses as A
from conftest import wait_until


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
    """max_c 가 null 인 채널은 PLC 한계에 0 → PLC 가 그 채널을 막는다."""
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


async def test_process_start_rejected_without_recipe(link):
    """레시피 표가 통과하지 않았으면 결과 3(레시피 표 오류)."""
    lk, _s, _cfg = link
    result, _ = await lk.send_command(A.CMD_PROCESS_START)
    assert result == A.RESULT_RECIPE


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
    monkeypatch.setattr("peald.plclink.ACK_TIMEOUT_S", 0.3)
    result, text = await lk.send_command(A.CMD_ALARM_ACK)
    assert result is None and "응답 없음" in text
    assert len([w for w in writes if w[0] == A.D_CMD_NO]) == 1, "명령 번호를 두 번 썼다"


async def test_command_number_continues_after_reconnect(sim):
    """재연결하면 PLC 에 남아 있는 번호 다음부터 이어 간다.
    0부터 다시 시작하면 PLC 가 '이미 처리한 번호'로 보고 무시한다."""
    from peald.convert import Converters
    from peald.plclink import PlcLink
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
    monkeypatch.setattr("peald.plclink.PLC_HB_STALL_S", 0.3)
    # 시뮬레이터의 하트비트를 멈춘다
    monkeypatch.setattr(s, "_plc_heartbeat", lambda now: None)
    assert await wait_until(lambda: not lk.plc_hb_ok, 3)


async def test_pc_link_alarm_and_reset(link, monkeypatch):
    """PC 하트비트가 멈추면 PC 끊김 알람 + 안전 정지 요구.
    다시 보내고 리셋하면 풀린다."""
    lk, s, _cfg = link
    s.reg[A.D_PRM_PC_WDT_MS] = 300
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
