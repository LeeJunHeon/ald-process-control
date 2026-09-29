"""
loops.py — 백그라운드 주기 태스크.

  sample_loop  10 Hz  PLC 링크가 읽어 둔 값으로 알람 추적·트렌드 기록
  live_loop     5 Hz  화면에 값 전송

PLC 읽기 자체는 plclink.py 가 100 ms 주기로 따로 돈다. 여기서 읽지 않는 이유는
소켓을 한 태스크만 가져야 하기 때문이다(요청이 섞이면 엉뚱한 응답을 읽는다).

★ 어떤 예외도 루프를 죽이지 못하게 한다. 루프가 멈추면 화면의 모든 값이 얼어붙고,
  운전자는 그것을 '장비가 멈췄다'로 오해한다.
"""

import time
import asyncio

from . import logger
from .state import state
from .trend_buffer import trend
from .connection import push_live, push_log

SAMPLE_HZ = 10
LIVE_HZ = 5


async def sample_loop():
    period = 1.0 / SAMPLE_HZ
    while True:
        try:
            state.refresh()
            trend.record(time.monotonic(), state.live())
        except Exception as e:  # noqa: BLE001
            logger.write("err", f"샘플링 루프 오류(계속 진행): {type(e).__name__}: {e}")
        await asyncio.sleep(period)


async def live_loop():
    period = 1.0 / LIVE_HZ
    while True:
        try:
            await push_live()
        except Exception as e:  # noqa: BLE001
            logger.write("err", f"화면 전송 루프 오류(계속 진행): {type(e).__name__}: {e}")
        await asyncio.sleep(period)


_pending_events = []


def on_link_event(level: str, msg: str):
    """PLC 링크가 연결·끊김을 알릴 때 부른다(동기 콜백이라 큐에 넣고 루프가 비운다)."""
    _pending_events.append((level, msg))


async def event_loop():
    while True:
        try:
            while _pending_events:
                level, msg = _pending_events.pop(0)
                await push_log(msg, level)
        except Exception as e:  # noqa: BLE001
            logger.write("err", f"이벤트 루프 오류(계속 진행): {e}")
        await asyncio.sleep(0.2)


def start_all() -> list:
    return [asyncio.create_task(sample_loop()),
            asyncio.create_task(live_loop()),
            asyncio.create_task(event_loop())]


async def stop_all(tasks: list):
    for t in tasks or []:
        t.cancel()
    for t in tasks or []:
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
