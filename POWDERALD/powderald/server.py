"""
server.py — FastAPI 앱 + 라우트 + WebSocket + lifespan.

  GET  /              화면
  GET  /css/* /js/*   정적 자산
  GET  /health        {ok, device, version}
  GET  /api/trend     트렌드 이력
  WS   /ws            상태·값·로그·알림 / 명령

진입점은 run.py 다. 이 모듈은 앱을 만들기만 한다(테스트가 create_app 을 직접 부른다).
"""

import os
import json
import time
import asyncio
import logging
import contextlib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import paths
from . import logger
from . import loops
from . import window
from . import version
from . import commands
from . import config as config_mod
from . import device as DEV
from .datalog import DataLog, cleanup as datalog_cleanup
from .plclink import PlcLink
from .process import ProcessRunner
from .simulator import PlcSim, SimServer
from .state import state
from .connection import manager, host_ok, origin_ok
from .commands import handle_command
from .trend_buffer import trend
from . import trendlog as trendlog_mod
from . import logview

log = logging.getLogger(__name__)

# WebSocket 메시지 크기 상한 — 가장 큰 레시피 저장 명령의 몇 배. 넘으면 uvicorn 이 연결을 닫는다.
WS_MAX_SIZE = 256 * 1024


def uvicorn_config(app, host: str, port: int):
    """창·headless·자체 점검이 같은 서버 설정을 쓴다.
    log_config=None: uvicorn 자체 로깅 dictConfig 를 타지 않는다(창 전용 exe 에서 죽는다)."""
    import uvicorn
    return uvicorn.Config(app, host=host, port=port, log_level="warning", log_config=None,
                          ws_max_size=WS_MAX_SIZE)

_ASSET_FILES = ["css/tokens.css", "css/style.css", "js/fmt.js", "js/core.js", "js/app.js",
                "js/views/main.js", "js/views/schematic.js", "js/views/trend.js",
                "js/views/alarm.js", "js/views/setup.js", "js/views/recipe.js",
                "js/views/manual.js", "js/views/datalog.js", "js/chart.js"]


def create_app(config_path: str = "", single_instance: bool = True,
               start_io: bool = True) -> FastAPI:
    """설정을 읽어 앱을 만든다.

    start_io=False 는 자체 점검(--selftest) 전용이다 — 시뮬레이터·PLC 링크·주기 태스크를
    띄우지 않는다(점검이 실장비에 PRM 을 쓰는 일이 없게).

    single_instance=False 는 검증 하네스 전용이다. 이중 실행 방지는 뮤텍스를 쓰는데,
    실제 프로그램이 떠 있는 개발 PC 에서 테스트를 돌리면 테스트가 그 뮤텍스에 걸려
    프로세스째 종료된다(설계대로 동작한 결과다). 운전 경로에서는 항상 켠다.
    """
    paths.ensure_dirs()
    cfg, problems, source = config_mod.load(config_path)
    state.startup_notices = []
    state.install_config(cfg, problems, source)
    for lv, msg in problems:
        logger.write(lv, f"설정 확인 필요 — {msg}")
    trendlog_mod.cleanup((cfg.get("log") or {}).get("trend_keep_days", 90))
    low = trendlog_mod.disk_warning()
    if low:
        logger.write("warn", low)
        state.startup_notices.append({"level": "warn", "msg": low, "kind": "config"})

    plc = cfg.get("plc") or {}
    simulate = bool(plc.get("simulate"))
    sim = PlcSim(cfg, float(plc.get("sim_speed") or 5)) if simulate else None
    state.sim = sim
    sim_server = SimServer(sim, "127.0.0.1", int(plc.get("sim_port") or DEV.DEFAULT_SIM_PORT)) \
        if simulate else None

    link = PlcLink(cfg, state.conv, on_event=loops.on_link_event)
    state.link = link
    state.runner = ProcessRunner(state)
    state.datalog = DataLog(state)
    state.recipe_check = {}
    datalog_cleanup((cfg.get("log") or {}).get("datalog_keep_days", 180))

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.write("info", f"{version.APP_NAME} v{version.APP_VERSION} "
                             f"({version.BUILD_DATE}) 기동")
        # 이중 실행 방지는 창 경로뿐 아니라 서버 기동 공통 경로에도 둔다.
        if single_instance and not window.acquire_single_instance():
            msg = f"{DEV.NAME} 프로그램이 이미 실행 중입니다 — 기동을 중단합니다"
            print(f"[error] {msg}", flush=True)     # os._exit 는 버퍼를 비우지 않는다
            logger.write("err", msg)
            os._exit(1)

        if paths.DATA_DIR_ERROR:
            state.startup_notices.append({"level": "warn", "msg": paths.DATA_DIR_ERROR,
                                          "kind": "config"})
        ok_w, why = paths.check_writable()
        if not ok_w:
            state.startup_notices.append(
                {"level": "warn", "kind": "config",
                 "msg": f"데이터 폴더에 쓸 수 없습니다: {paths.DATA_DIR} ({why})"})
        for lv, msg in logger.drain_early():
            state.startup_notices.append({"level": lv, "msg": msg, "kind": "config"})

        if not start_io:
            yield
            return
        if sim_server:
            if await sim_server.start():
                logger.write("info", f"내장 시뮬레이터 시작 (배속 {sim.speed:g})")
            else:
                # 포트가 이미 쓰이면(같은 프로그램이 또 떴거나 다른 것이 쓰는 중)
                # 시뮬레이터 없이 뜬다 — PLC 링크는 연결 실패로 보이고 화면이 이유를 알린다.
                state.startup_notices.append(
                    {"level": "err", "msg": sim_server.error, "kind": "config"})
                logger.write("err", sim_server.error)
        link.start()
        tasks = loops.start_all()
        # PC 를 다시 켰을 때 PLC 가 이미 공정 중이면 레시피를 되찾아 이어 간다.
        tasks.append(asyncio.create_task(_adopt_later()))
        try:
            yield
        finally:
            if state.runner:
                with contextlib.suppress(Exception):
                    await state.runner.stop_task()
            if state.datalog:
                state.datalog.close()
            with contextlib.suppress(Exception):
                trendlog_mod.trendlog.flush()
                trendlog_mod.trendlog.close()
            await loops.stop_all(tasks)
            with contextlib.suppress(Exception):
                await link.stop()
            if sim_server:
                with contextlib.suppress(Exception):
                    await sim_server.stop()

    app = FastAPI(lifespan=lifespan)
    _routes(app)
    return app


async def _adopt_later():
    """링크가 붙을 때까지 기다렸다가 진행 중인 공정을 이어받는다."""
    from .connection import push_log
    for _ in range(100):
        await asyncio.sleep(0.2)
        if state.link and state.link.connected:
            with contextlib.suppress(Exception):
                await state.runner.adopt_running(push_log)
            return


def _asset_version() -> str:
    """정적 자산에 붙일 "앱버전-최신mtime". 파일을 고치면 URL 이 바뀌어 WebView2 가
    옛 자산을 재사용하지 못한다("코드는 최신인데 화면은 과거" 사고 차단)."""
    mtimes = []
    for rel in _ASSET_FILES:
        with contextlib.suppress(OSError):
            mtimes.append(os.path.getmtime(os.path.join(paths.FRONTEND_DIR, rel)))
    return f"{version.APP_VERSION}-{int(max(mtimes)) if mtimes else 0}"


def _routes(app: FastAPI):
    @app.middleware("http")
    async def host_guard(request, call_next):
        # ★ Host 가 IP·localhost 가 아니면 거절 — DNS 재바인딩으로 이력·데이터 로그에 닿지 못하게
        if not host_ok(request.headers.get("host")):
            return PlainTextResponse("Host 거절", status_code=403)
        return await call_next(request)

    @app.get("/")
    async def root():
        try:
            with open(paths.INDEX_PATH, encoding="utf-8") as fh:
                html = fh.read().replace("__ASSET_V__", _asset_version())
        except OSError:
            return FileResponse(paths.INDEX_PATH)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health():
        return JSONResponse({"ok": True, "device": DEV.KEY, "name": DEV.NAME,
                             "version": version.APP_VERSION})

    @app.get("/api/trend")
    async def api_trend(sec: int = 120):
        # 범위 밖의 값은 잘라낸다 — sec=999999 로 전체 버퍼를 매번 직렬화하면
        # 서버가 그 시간만큼 다른 일을 못 한다.
        sec = max(10, min(int(sec or 120), 3600))
        return JSONResponse(trend.series(sec, time.monotonic()))

    # ---- 이력 · 데이터 로그 (읽기 전용 — 원격 보기에서도 된다) ----
    @app.get("/api/trend/history")
    async def api_trend_history(t0: float, t1: float, cols: str = "", points: int = 2000):
        # ★ 조회는 작업 스레드에서 — 루프를 붙잡으면 PC 하트비트가 멈춰 공정이 선다
        cl = [c for c in cols.split(",") if c] or None
        points = max(10, min(int(points or 2000), 2000))
        loops.note_work("트렌드 이력 조회")
        keep = (state.cfg.get("log") or {}).get("trend_keep_days", 90)
        res = await trendlog_mod.trendlog.query_async(t0, t1, cl, points, keep)
        return JSONResponse(res, status_code=400 if res.get("error") else 200)

    @app.get("/api/datalog/list")
    async def api_datalog_list():
        loops.note_work("데이터 로그 목록")
        return JSONResponse({"items": await logview.list_async()})

    @app.get("/api/datalog/chart")
    async def api_datalog_chart(name: str):
        loops.note_work("데이터 로그 그래프")
        res = await logview.chart_async(name)
        if res is None:
            return JSONResponse({"error": "목록에 없는 파일입니다"}, status_code=404)
        return JSONResponse(res)

    @app.get("/api/datalog/rows")
    async def api_datalog_rows(name: str, offset: int = 0):
        loops.note_work("데이터 로그 표")
        res = await logview.table_async(name, offset)
        if res is None:
            return JSONResponse({"error": "목록에 없는 파일입니다"}, status_code=404)
        return JSONResponse(res)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        host = ws.headers.get("host")
        origin = ws.headers.get("origin")
        if not host_ok(host) or not origin_ok(origin, host):
            # ★ 다른 출처의 웹 페이지·도메인 Host 는 연결 자체를 받지 않는다
            logger.write("warn", "WebSocket 연결 거절 — 출처 "
                         + logger.clean(origin if origin is not None else "(없음)", 80)
                         + " · Host " + logger.clean(host or "(없음)", 60))
            await ws.close(code=1008)
            return
        await manager.connect(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(data, dict) and "cmd" in data:
                    try:
                        await handle_command(data, ws)
                    except WebSocketDisconnect:
                        raise
                    except Exception as e:  # noqa: BLE001
                        # ★ 명령 처리 중 예외가 나도 연결은 유지한다(화면이 말없이 끊기지 않게)
                        logger.write("err", f"명령 처리 오류({logger.clean(data.get('cmd'), 40)}): "
                                            f"{type(e).__name__}: {logger.clean(e, 300)}")
                        from .connection import push_notice
                        await push_notice(f"명령을 처리하지 못했습니다 — {type(e).__name__}", "err", ws)
        except WebSocketDisconnect:
            manager.disconnect(ws)
        except Exception:  # noqa: BLE001
            manager.disconnect(ws)
        finally:
            from .admin import admin
            admin.forget(ws)

    for url, sub in (("/css", "css"), ("/js", "js")):
        d = os.path.join(paths.FRONTEND_DIR, sub)
        if os.path.isdir(d):
            app.mount(url, StaticFiles(directory=d), name=sub)
        else:
            # ★ 화면이 아예 안 뜨는 치명적 상황이라 print 도 남긴다.
            miss = f"정적 폴더 없음: {d} — 빌드 시 frontend 가 누락됐을 수 있습니다"
            print(f"[error] {miss}")
            logger.early("err", miss)
