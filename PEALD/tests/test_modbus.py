"""Modbus 클라이언트 ↔ 내장 시뮬레이터.

읽기·나눠 쓰기·예외 응답·시간 초과·재연결을 실제 소켓으로 확인한다.
★ 시뮬레이터용 지름길을 쓰지 않는다 — 실장비와 같은 통신 코드를 타야
  여기서 통과한 것이 현장에서도 통과한다.
"""
import asyncio

import pytest

from peald import addresses as A
from peald.modbus import ModbusClient, ModbusError, ModbusTimeout
from conftest import free_port, wait_until


async def client_for(port, **kw):
    c = ModbusClient("127.0.0.1", port, 1, kw.pop("timeout_ms", 1000))
    await c.connect()
    return c


async def test_read_status_block(sim):
    _s, port, _cfg = sim
    c = await client_for(port)
    regs = await c.read_holding(A.STATUS_BASE, A.STATUS_COUNT)
    assert len(regs) == 81
    assert all(0 <= v <= 0xFFFF for v in regs)
    await c.close()


async def test_read_more_than_125_is_split(sim):
    """125 워드 제한을 넘는 읽기는 나눠서 처리해야 한다."""
    _s, port, _cfg = sim
    c = await client_for(port)
    regs = await c.read_holding(A.RCP_SUM_BASE, A.RCP_AREA_COUNT)   # 1120 워드
    assert len(regs) == A.RCP_AREA_COUNT
    await c.close()


async def test_write_multiple_splits_and_reads_back(sim):
    """레시피 표 1120 워드를 나눠 쓰고 되읽어 같은지 본다."""
    s, port, _cfg = sim
    c = await client_for(port)
    values = [(i * 7 + 3) & 0xFFFF for i in range(A.RCP_AREA_COUNT)]
    await c.write_multiple(A.RCP_SUM_BASE, values)
    back = await c.read_holding(A.RCP_SUM_BASE, A.RCP_AREA_COUNT)
    assert back == values
    await c.close()


async def test_write_single_and_readback(sim):
    _s, port, _cfg = sim
    c = await client_for(port)
    await c.write_single(A.D_PC_HB, 0x1234)
    assert (await c.read_holding(A.D_PC_HB, 1))[0] == 0x1234
    await c.close()


async def test_exception_response_on_bad_address(sim):
    """범위 밖 주소는 PLC 예외 응답으로 돌아온다(연결은 살아 있다)."""
    _s, port, _cfg = sim
    c = await client_for(port)
    with pytest.raises(ModbusError):
        await c.read_holding(60000, 10)
    # 예외 응답 뒤에도 정상 요청이 된다
    assert len(await c.read_holding(0, 4)) == 4
    await c.close()


async def test_bad_count_is_rejected(sim):
    _s, port, _cfg = sim
    c = await client_for(port)
    with pytest.raises(ModbusError):
        await c._request(b"\x03\x00\x00\x00\xFF")   # 255 워드 요청
    await c.close()


async def test_timeout_drops_socket():
    """★ 시간 초과가 나면 소켓을 버려야 한다. 늦게 도착한 응답이 다음 요청의 응답으로
    읽히면 엉뚱한 주소의 값을 그 주소의 값으로 믿게 된다.

    받기만 하고 절대 답하지 않는 서버를 세워 확실하게 시간 초과를 만든다."""
    async def swallow(reader, writer):
        try:
            await reader.read(-1)           # 응답을 보내지 않는다
        except Exception:                   # noqa: BLE001
            pass

    port = free_port()
    srv = await asyncio.start_server(swallow, "127.0.0.1", port)
    try:
        c = ModbusClient("127.0.0.1", port, 1, 300)
        await c.connect()
        with pytest.raises(ModbusTimeout):
            await c.read_holding(0, 81)
        assert not c.connected              # 소켓을 버렸다
        await c.close()
    finally:
        srv.close()
        await asyncio.wait_for(srv.wait_closed(), 2)


async def test_connect_failure_when_nothing_listens():
    c = ModbusClient("127.0.0.1", free_port(), 1, 300)
    with pytest.raises(Exception):
        await c.connect()
    assert not c.connected


async def test_reconnect_after_server_restart(cfg):
    """서버가 사라졌다 돌아오면 링크가 스스로 다시 붙어야 한다."""
    from peald.convert import Converters
    from peald.plclink import PlcLink
    from peald.simulator import PlcSim, SimServer

    port = free_port()
    cfg["plc"]["sim_port"] = port
    s = PlcSim(cfg, 50)
    srv = SimServer(s, "127.0.0.1", port)
    await srv.start()

    lk = PlcLink(cfg, Converters(cfg))
    lk.start()
    assert await wait_until(lambda: lk.connected, 5)

    await srv.stop()
    assert await wait_until(lambda: not lk.connected, 5), "끊김을 알아채지 못했다"

    srv2 = SimServer(s, "127.0.0.1", port)
    await srv2.start()
    assert await wait_until(lambda: lk.connected, 10), "재연결하지 못했다"
    await lk.stop()
    await srv2.stop()
