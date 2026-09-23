"""
loops.py — 백그라운드 주기 태스크.

  sample_loop    10 Hz  provider 샘플링 → state.snap 갱신 → 트렌드 기록 → provider 로그 배달
  telemetry_loop  5 Hz  화면에 telemetry 전송

주기를 둘로 나눈 이유: 압력 펄스는 0.1 s 라서 10 Hz로 읽어야 트렌드에 남는다. 반면 화면은
5 Hz면 사람 눈에 충분하고, 그 이상 보내면 WebSocket과 렌더링만 바빠진다.

★ 어떤 예외도 루프를 죽이지 못하게 한다. 루프가 멈추면 화면의 모든 값이 얼어붙고,
  운전자는 그것을 '장비가 멈췄다'로 오해한다.
"""

import time
import asyncio

import logger
from state import state
from trend_buffer import trend
from connection import push_telemetry, push_log

SAMPLE_HZ = 10
TELEMETRY_HZ = 5

_last_step_key = None      # 스텝이 바뀌는 순간을 잡아 펄스 표시 띠에 기록한다


async def sample_loop():
    period = 1.0 / SAMPLE_HZ
    while True:
        try:
            now = time.monotonic()
            p = state.provider
            if p is not None:
                p.tick(now)
                state.snap = p.read_snapshot()
                trend.record(now, state.snap, _pulse_line())
                for level, msg in p.drain_logs():
                    await push_log(msg, level)
        except Exception as e:  # noqa: BLE001
            logger.write("err", f"샘플링 루프 오류(계속 진행): {type(e).__name__}: {e}")
        await asyncio.sleep(period)


def _pulse_line() -> str:
    """스텝이 바뀐 순간에만 라인 id를 돌려준다(트렌드 하단 펄스 띠용).
    ★ 매 tick 기록하면 0.1 s 스텝이 띠를 가득 채워 구분이 안 된다."""
    global _last_step_key
    proc = (state.snap or {}).get("process") or {}
    if proc.get("mode") not in ("running", "paused"):
        _last_step_key = None
        return ""
    key = (proc.get("block"), proc.get("cycle"), proc.get("step"))
    if key == _last_step_key:
        return ""
    _last_step_key = key
    name = proc.get("step_name") or ""
    # 펄스 스텝(짧은 스텝)만 띠에 남긴다 — 퍼지까지 찍으면 띠가 의미를 잃는다.
    return name if float(proc.get("step_total_s") or 0) <= 1.0 else ""


async def telemetry_loop():
    period = 1.0 / TELEMETRY_HZ
    while True:
        try:
            await push_telemetry()
        except Exception as e:  # noqa: BLE001
            logger.write("err", f"telemetry 루프 오류(계속 진행): {type(e).__name__}: {e}")
        await asyncio.sleep(period)


def start_all() -> list:
    return [asyncio.create_task(sample_loop()), asyncio.create_task(telemetry_loop())]


async def stop_all(tasks: list):
    for t in tasks or []:
        t.cancel()
    for t in tasks or []:
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
