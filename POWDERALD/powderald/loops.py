"""
loops.py — 백그라운드 주기 태스크.

  sample_loop  10 Hz  PLC 링크가 읽어 둔 값으로 알람 추적·트렌드 기록(이력 1 Hz)
  live_loop     5 Hz  화면에 값 전송
  event_loop    5 Hz  링크 이벤트 로그 · 관리자 잠금 만료 알림

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
from .trendlog import trendlog
from .admin import admin
from .connection import manager, push_live, push_log

SAMPLE_HZ = 10
LIVE_HZ = 5
LAG_PERIOD_S = 0.100
LAG_WARN_S = 0.500
LAG_WINDOW_S = 600.0             # 설정 탭에 보여 주는 '최근' 최대 지연 창


# ===================== 이벤트 루프 지연 감시 =====================
# ★ 루프가 막히면 PC 하트비트가 멈춘다. 100 ms 주기 작업이 늦어진 시간을 재서
#   500 ms 를 넘으면 그때 하던 일과 함께 경고를 남긴다.
_work = ["", 0.0]                # (최근 시작한 일, 시작 시각)
lag = {"last_ms": 0, "max_ms": 0, "max_at": "", "max_work": "", "hist": []}


def note_work(name: str):
    """무거울 수 있는 일을 시작할 때 부른다(지연 경고에 이름을 붙이려고)."""
    _work[0], _work[1] = logger.clean(name, 40), time.monotonic()


def lag_status() -> dict:
    now = time.monotonic()
    recent = [(t, ms, w) for t, ms, w in lag["hist"] if now - t <= LAG_WINDOW_S]
    top = max(recent, key=lambda x: x[1]) if recent else (0, 0, "")
    return {"last_ms": lag["last_ms"], "recent_max_ms": top[1],
            "recent_max_work": logger.clean(top[2], 40),
            "max_ms": lag["max_ms"], "max_at": lag["max_at"],
            "max_work": logger.clean(lag["max_work"], 40)}


async def lag_loop():
    expect = time.monotonic() + LAG_PERIOD_S
    while True:
        await asyncio.sleep(max(0.0, expect - time.monotonic()))
        now = time.monotonic()
        late = max(0.0, now - expect)
        ms = int(late * 1000)
        lag["last_ms"] = ms
        work = _work[0] if now - _work[1] <= late + LAG_PERIOD_S + 1.0 else ""
        if ms >= 50:
            lag["hist"].append((now, ms, work))
            del lag["hist"][:-600]
        if ms > lag["max_ms"]:
            lag.update(max_ms=ms, max_at=time.strftime("%H:%M:%S"), max_work=work)
        if late > LAG_WARN_S:
            logger.write("warn", f"이벤트 루프 지연 {ms} ms" + (f" — {work}" if work else "")
                         + " (PC 하트비트가 그만큼 늦었습니다)")
        expect = now + LAG_PERIOD_S


def _log_sync(level, msg):
    """동기 자리에서 남기는 로그(종료 감지 등). 화면에는 event_loop 가 흘려 보낸다."""
    _pending_events.append((level, msg))


def sample_once():
    """샘플링 한 번 — 알람 · 끝 판정 · 데이터 로그 · 트렌드."""
    state.refresh()
    if state.runner:
        state.runner.tick(_log_sync)
    _datalog_tick()
    live = state.live()
    # ★ PLC 하트비트 멈춤(STOP 등) 동안 live 에 남은 값은 굳은 옛 값이다 — 트렌드 버퍼 ·
    #   트렌드 기록에 넣지 않는다(실제 값처럼 남지 않게). 끊김과 같다
    if not (live.get("plc") or {}).get("hb_stalled"):
        trend.record(time.monotonic(), live)
        trendlog.record(live)
    _admin_on_process_start(live)


async def sample_loop():
    period = 1.0 / SAMPLE_HZ
    while True:
        try:
            sample_once()
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


_was_running = False
_admin_lock_pending = []


def _admin_on_process_start(live: dict):
    """공정이 시작되면(멈춤 → 공정 중) 관리자 잠금을 다시 채운다."""
    global _was_running
    running = bool((live.get("process") or {}).get("running"))
    if running and not _was_running:
        _admin_lock_pending.extend(admin.lock_all("공정 시작"))
    _was_running = running


async def event_loop():
    while True:
        try:
            while _pending_events:
                level, msg = _pending_events.pop(0)
                await push_log(msg, level)
            # 잠긴 연결(공정 시작·시간 만료)에 상태를 알린다
            locked = _admin_lock_pending[:] + admin.expired()
            del _admin_lock_pending[:]
            for ws in locked:
                if ws in manager.active:
                    await manager.send_to(ws, admin.status(ws))
                    await manager.send_to(ws, {"type": "notice", "level": "info",
                                               "msg": "관리자 잠금 — 설정 편집이 다시 잠겼습니다"})
        except Exception as e:  # noqa: BLE001
            logger.write("err", f"이벤트 루프 오류(계속 진행): {e}")
        await asyncio.sleep(0.2)


def _total_ms(rec) -> int:
    from . import recipe as R
    try:
        return R.total_ms(state.cfg, rec) if rec else 0
    except Exception:  # noqa: BLE001
        return 0


def _datalog_tick():
    """공정이 시작되면 데이터 로그를 열고, 끝나면 조금 더 남기고 닫는다."""
    dl, runner = state.datalog, state.runner
    if not (dl and runner):
        return
    # ★ 공정 구간(명령 1 처리됨 · 이어받기 ~ 끝 판정)에 묶는다 — '공정 중' 읽기에 묶으면 짧은 PLC 끊김에
    #   progress() 가 비어 '공정 아님'이 되고, 다시 붙으면 새 파일이 열려 두 파일로 갈라진다
    dl.follow(bool(runner.active_run),
              lambda: dl.start(runner.active_name, runner.active_recipe, runner.active_table,
                               _total_ms(runner.active_recipe)),
              lambda: runner.last_result)
    dl.tick((state.cfg.get("log") or {}).get("datalog_interval_s", 1))


async def plc_recipe_loop():
    """지금 PLC 에 올라가 있는 레시피 요약을 주기적으로 되읽는다.

    ★ 화면이 '편집 중인 것'과 '장비가 들고 있는 것'을 나란히 보여 주려면 이 값이
      필요하다. 공정 중에는 읽지 않는다 — 통신을 공정 감시에 쓴다.
    """
    while True:
        try:
            await plc_recipe_once()
        except Exception as e:  # noqa: BLE001
            logger.write("warn", f"PLC 레시피 되읽기 실패(계속 진행): {e}")
        await asyncio.sleep(5.0)


async def plc_recipe_once():
    from .commands import refresh_plc_recipe
    from .connection import push_state
    link = state.link
    if link and link.connected and not (state.runner and state.runner.progress().get("running")):
        before = state.plc_recipe
        await refresh_plc_recipe()
        if state.plc_recipe != before:
            # ★ 레시피 탭의 '지금 PLC' 줄 — 바뀌었을 때만 상태를 보낸다(옛 값이 남지 않게)
            await push_state()


HOUSEKEEP_S = 86400.0             # 로그 · 데이터 로그 · 트렌드 · 내보내기 정리 주기(하루)


def housekeeping():
    """보존 기간이 지난 파일 정리 — 작업 스레드에서. 몇 달 켜 두는 장비라 시작 때만으로는 부족하다."""
    from .datalog import cleanup as datalog_cleanup
    from . import trendlog as T
    lg = state.cfg.get("log") or {}
    logger._cleanup()
    datalog_cleanup(lg.get("datalog_keep_days", 180))
    T.cleanup(lg.get("trend_keep_days", 90))
    T.cleanup_exports(lg.get("trend_keep_days", 90))


async def housekeeping_loop():
    while True:
        await asyncio.sleep(HOUSEKEEP_S)
        try:
            await asyncio.to_thread(housekeeping)
            logger.write("info", "하루 정리 — 보존 기간이 지난 로그 · 데이터 로그 · 트렌드 · 내보내기를 지웠습니다")
        except Exception as e:  # noqa: BLE001
            logger.write("warn", f"하루 정리 실패(계속 진행): {type(e).__name__}: {e}")


def start_all() -> list:
    return [asyncio.create_task(sample_loop()),
            asyncio.create_task(live_loop()),
            asyncio.create_task(event_loop()),
            asyncio.create_task(plc_recipe_loop()),
            asyncio.create_task(lag_loop()),
            asyncio.create_task(housekeeping_loop())]


async def stop_all(tasks: list):
    for t in tasks or []:
        t.cancel()
    for t in tasks or []:
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
