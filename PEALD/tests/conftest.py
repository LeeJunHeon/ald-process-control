"""pytest 공통 설정.

★ 테스트가 진짜 data/ 폴더에 쓰면 개발자 환경이 오염된다 — DATA_ROOT 를 임시 폴더로 돌린다.
★ 이 폴더에서만 실행한다(cd PEALD && python -m pytest). 루트에서 두 프로그램을 한꺼번에
  돌리지 않는다 — 패키지 이름은 다르지만 tests/conftest.py 가 겹쳐 헷갈린다.
"""
import os
import sys
import socket
import asyncio

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from peald import paths  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data(tmp_path):
    paths.set_data_root(str(tmp_path))
    paths.ensure_dirs()
    yield


@pytest.fixture
def cfg():
    from peald import config as C
    c, _problems, _src = C.load("")
    return c


def free_port() -> int:
    """비어 있는 포트 하나. 테스트끼리 포트가 겹치지 않게 매번 새로 받는다."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def sim(cfg):
    """내장 시뮬레이터 + Modbus 서버. 배속을 올려 대기 시간을 줄인다."""
    from peald.simulator import PlcSim, SimServer
    port = free_port()
    cfg["plc"]["sim_port"] = port
    cfg["plc"]["sim_speed"] = 50
    s = PlcSim(cfg, 50)
    srv = SimServer(s, "127.0.0.1", port)
    await srv.start()
    try:
        yield s, port, cfg
    finally:
        await srv.stop()


@pytest.fixture
async def link(sim):
    """시뮬레이터에 붙은 PLC 링크."""
    from peald.convert import Converters
    from peald.plclink import PlcLink
    s, port, cfg = sim
    lk = PlcLink(cfg, Converters(cfg))
    lk.start()
    for _ in range(100):
        if lk.connected:
            break
        await asyncio.sleep(0.05)
    assert lk.connected, "시뮬레이터에 연결하지 못했습니다"
    try:
        yield lk, s, cfg
    finally:
        await lk.stop()


async def wait_until(fn, timeout=5.0, step=0.05):
    """조건이 참이 될 때까지 기다린다. 시간 안에 안 되면 False."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if fn():
            return True
        await asyncio.sleep(step)
    return False
