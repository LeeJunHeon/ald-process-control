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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import paths
from . import logger
from . import loops
from . import window
from . import version
from . import commands
from . import config as config_mod
from . import device as DEV
from .convert import Converters
from .plclink import PlcLink
from .simulator import PlcSim, SimServer
from .state import state
from .connection import manager
from .commands import handle_command
from .trend_buffer import trend

log = logging.getLogger(__name__)

_ASSET_FILES = ["css/tokens.css", "css/style.css", "js/fmt.js", "js/core.js", "js/app.js",
                "js/views/main.js", "js/views/schematic.js", "js/views/trend.js",
                "js/views/alarm.js", "js/views/setup.js", "js/views/recipe.js"]


def create_app(config_path: str = "", single_instance: bool = True) -> FastAPI:
    """설정을 읽어 앱을 만든다.

    single_instance=False 는 검증 하네스 전용이다. 이중 실행 방지는 뮤텍스를 쓰는데,
    실제 프로그램이 떠 있는 개발 PC 에서 테스트를 돌리면 테스트가 그 뮤텍스에 걸려
    프로세스째 종료된다(설계대로 동작한 결과다). 운전 경로에서는 항상 켠다.
    """
    paths.ensure_dirs()
    cfg, problems, source = config_mod.load(config_path)
    state.cfg = cfg
    state.config_source = source
    state.conv = Converters(cfg)
    logger.configure(cfg.get("log") or {})

    state.startup_notices = []
    for lv, msg in problems:
        logger.write(lv, f"설정 확인 필요 — {msg}")
        state.startup_notices.append({"level": lv, "msg": msg, "kind": "config"})
    for name in state.conv.unconfirmed():
        state.startup_notices.append(
            {"level": "warn", "msg": f"환산 미확정: {name}", "kind": "unconfirmed"})

    plc = cfg.get("plc") or {}
    simulate = bool(plc.get("simulate"))
    sim = PlcSim(cfg, float(plc.get("sim_speed") or 5)) if simulate else None
    state.sim = sim
    sim_server = SimServer(sim, "127.0.0.1", int(plc.get("sim_port") or DEV.DEFAULT_SIM_PORT)) \
        if simulate else None

    link = PlcLink(cfg, state.conv, on_event=loops.on_link_event)
    state.link = link

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
        try:
            yield
        finally:
            await loops.stop_all(tasks)
            with contextlib.suppress(Exception):
                await link.stop()
            if sim_server:
                with contextlib.suppress(Exception):
                    await sim_server.stop()

    app = FastAPI(lifespan=lifespan)
    _routes(app)
    return app


def _asset_version() -> str:
    """정적 자산에 붙일 "앱버전-최신mtime". 파일을 고치면 URL 이 바뀌어 WebView2 가
    옛 자산을 재사용하지 못한다("코드는 최신인데 화면은 과거" 사고 차단)."""
    mtimes = []
    for rel in _ASSET_FILES:
        with contextlib.suppress(OSError):
            mtimes.append(os.path.getmtime(os.path.join(paths.FRONTEND_DIR, rel)))
    return f"{version.APP_VERSION}-{int(max(mtimes)) if mtimes else 0}"


def _routes(app: FastAPI):
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

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await manager.connect(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(data, dict) and "cmd" in data:
                    await handle_command(data, ws)
        except WebSocketDisconnect:
            manager.disconnect(ws)
        except Exception:  # noqa: BLE001
            manager.disconnect(ws)

    for url, sub in (("/css", "css"), ("/js", "js")):
        d = os.path.join(paths.FRONTEND_DIR, sub)
        if os.path.isdir(d):
            app.mount(url, StaticFiles(directory=d), name=sub)
        else:
            # ★ 화면이 아예 안 뜨는 치명적 상황이라 print 도 남긴다.
            miss = f"정적 폴더 없음: {d} — 빌드 시 frontend 가 누락됐을 수 있습니다"
            print(f"[error] {miss}")
            logger.early("err", miss)
