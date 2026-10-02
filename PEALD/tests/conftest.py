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


class FakeSim:
    """가짜 시계로 tick 단위로 돌리는 시뮬레이터(소켓 없이). step(n, dt) 로 n 스캔."""

    def __init__(self, cfg, speed=1, o3=False):
        from peald.simulator import PlcSim
        self.t = [1000.0]
        self.sim = PlcSim(cfg, speed, clock=lambda: self.t[0])
        if o3:
            self.o3_ready()

    def step(self, n=1, dt=0.02):
        for _ in range(n):
            self.t[0] += dt
            self.sim.tick()
        return self.sim

    def cmd(self, code, dt=0.02):
        """PC 처럼 명령 영역(D01002 코드 → D01001 번호)에 쓰고 한 스캔 돈 뒤 결과(D00003).
        래더 순서 그대로 — P25 는 요청만, 평가는 그 명령의 프로그램(P40 · P45 · P50)이 같은 스캔에 한다."""
        from peald import addresses as A
        s = self.sim
        s.write(A.D_CMD_CODE, [code])
        s.write(A.D_CMD_NO, [(s.reg[A.D_CMD_NO] + 1) & 0xFFFF])
        self.step(1, dt)
        return s.reg[A.D_ACK_RESULT]

    def o3_ready(self):
        """Powder: O3 라인을 래더대로 켠다(바이패스 펌프 → IV-B → 5 s 뒤 O3 허가 → 발생기).
        ★ 래더는 공정 중 O3 허가가 빠지면 알람1 b3 로 중단한다 — 시퀀서를 직접 돌리는 시험의 전제."""
        from peald import addresses as A
        from peald import device as DEV
        if not DEV.HAS_O3:
            return
        s = self.sim
        if s.reg[A.D_PRM_O3_MAX] == 0:
            s.write(A.D_PRM_O3_MAX, [16000])
        s.man_aux |= (1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB) | (1 << A.AUX_O3_GEN)
        for _ in range(400):
            self.step(1, 0.05)
            if A.bit(s.reg[A.D_INTERLOCK], A.ILK_O3_OK) and s.o3_gen_on:
                return
        raise AssertionError("O3 허가가 서지 않았다")


async def o3_line_live(sim, timeout=10.0):
    """실시간으로 도는 시뮬레이터에 O3 라인을 켜고 O3 허가(5 s 뒤)를 기다린다 — Powder 에서
    시퀀서를 직접 시작하는 시험의 전제(래더는 공정 중 O3 허가가 없으면 알람1 b3 로 중단한다).
    O3 가 없는 장비에서는 아무것도 안 한다."""
    from peald import addresses as A
    from peald import device as DEV
    if not DEV.HAS_O3:
        return
    if sim.reg[A.D_PRM_O3_MAX] == 0:
        sim.write(A.D_PRM_O3_MAX, [16000])
    sim.man_aux |= (1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB) | (1 << A.AUX_O3_GEN)
    assert await wait_until(lambda: A.bit(sim.reg[A.D_INTERLOCK], A.ILK_O3_OK), timeout), "O3 허가가 서지 않았다"
