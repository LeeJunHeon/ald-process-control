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


def _log_sync(level, msg):
    """동기 자리에서 남기는 로그(종료 감지 등). 화면에는 event_loop 가 흘려 보낸다."""
    _pending_events.append((level, msg))


async def sample_loop():
    period = 1.0 / SAMPLE_HZ
    while True:
        try:
            state.refresh()
            if state.runner:
                state.runner.tick(_log_sync)
            _datalog_tick()
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


def _datalog_tick():
    """공정이 시작되면 데이터 로그를 열고, 끝나면 조금 더 남기고 닫는다."""
    dl, runner = state.datalog, state.runner
    if not (dl and runner):
        return
    prog = runner.progress()
    dl.follow(bool(prog.get("running")),
              lambda: dl.start(runner.recipe_name, runner.recipe, runner.table,
                               prog.get("total_ms") or 0))
    dl.tick((state.cfg.get("log") or {}).get("datalog_interval_s", 1))


async def plc_recipe_loop():
    """지금 PLC 에 올라가 있는 레시피 요약을 주기적으로 되읽는다.

    ★ 화면이 '편집 중인 것'과 '장비가 들고 있는 것'을 나란히 보여 주려면 이 값이
      필요하다. 공정 중에는 읽지 않는다 — 통신을 공정 감시에 쓴다.
    """
    from .commands import refresh_plc_recipe
    while True:
        try:
            link = state.link
            if link and link.connected and not (state.runner and state.runner.progress().get("running")):
                await refresh_plc_recipe()
        except Exception as e:  # noqa: BLE001
            logger.write("warn", f"PLC 레시피 되읽기 실패(계속 진행): {e}")
        await asyncio.sleep(5.0)


def start_all() -> list:
    return [asyncio.create_task(sample_loop()),
            asyncio.create_task(live_loop()),
            asyncio.create_task(event_loop()),
            asyncio.create_task(plc_recipe_loop())]


async def stop_all(tasks: list):
    for t in tasks or []:
        t.cancel()
    for t in tasks or []:
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
