"""
server.py — 진입점.

- FastAPI 앱 + 라우트(/ /css /js /health /api/trend) + WebSocket(/ws) + lifespan.
- 백그라운드 주기 태스크는 loops.py, pywebview 창은 window.py 가 담당한다.

실행:
    python backend/server.py --config config/chamber1.example.json
    python backend/server.py                  (exe 옆 / 프로젝트 루트의 config.json)

상태·명령·값공급·연결·파일·경로는 각 모듈로 분리한다:
  state.py · commands.py · providers/ · connection.py · storage.py · paths.py
통신 약속(메시지/스키마)은 INTERFACE.md 참고.
"""

import os
import sys

# 창 전용(console=False) exe: 콘솔이 없어 sys.stdout/sys.stderr 가 None 이다.
# 라이브러리가 .isatty()/.write() 를 직접 부르면 그 자리에서 죽는다 —
# uvicorn 로깅 설정이 sys.stdout.isatty() 를 불러 서버가 즉사하는 사고가 있었다.
# 다른 import 보다 먼저 devnull 로 갈아끼운다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 한국어 진단 메시지를 콘솔에 찍다가 프로그램이 죽는 것을 막는다.
# Windows 기본 콘솔 코덱(cp949)은 '—' 같은 글자를 인코딩하지 못해 UnicodeEncodeError 로
# 그 자리에서 죽는다 — 로그 한 줄 때문에 장비 화면이 안 뜨는 일은 없어야 한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — 재설정 불가한 스트림이면 그대로 둔다
        pass

# backend/ 안의 모듈을 최상위 이름으로 import 한다(가스 센서 프로그램과 같은 방식).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import time
import argparse
import contextlib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import paths
import logger
import config as config_mod
import version
import loops
import window
import storage
import commands
import recipe_model
from paths import FRONTEND_DIR, INDEX_PATH, DEFAULT_CONFIG_PATH, check_writable
from state import state
from connection import manager
from commands import handle_command
from trend_buffer import trend
from providers.demo import DemoProvider

# 정적 자산 캐시 무효화에 쓰는 파일 목록(아래 _asset_version).
_ASSET_FILES = ["css/tokens.css", "css/style.css", "js/fmt.js", "js/app.js", "js/core.js",
                "js/views/main.js", "js/views/schematic.js", "js/views/recipe.js",
                "js/views/trend.js", "js/views/alarm.js", "js/views/setup.js"]


# ===================== 설정 로드 =====================
def resolve_config_path(arg: str = "") -> str:
    """--config 가 있으면 그 경로, 없으면 exe 옆(개발 중에는 프로젝트 루트)의 config.json."""
    return os.path.abspath(arg) if arg else DEFAULT_CONFIG_PATH


def seed_sample_recipe(cfg: dict):
    """데모 모드이고 레시피 폴더가 비어 있으면 샘플을 하나 넣어 준다.
    ★ 빈 목록으로 뜨면 화면의 절반을 검증할 수 없다. 실장비 모드에서는 넣지 않는다 —
      현장 레시피 폴더에 우리가 만든 파일이 섞이면 안 된다."""
    if not (cfg.get("demo") or {}).get("enabled"):
        return None
    if storage.list_recipes():
        return storage.list_recipes()[0]
    recipe = recipe_model.sample_recipe(cfg)
    if not recipe:
        return None
    if storage.save_recipe(recipe["name"], recipe):
        logger.write("info", f"샘플 레시피를 만들었습니다: {recipe['name']}")
        return recipe["name"]
    return None


# ===================== FastAPI =====================
def create_app(config_path: str = "", single_instance: bool = True) -> FastAPI:
    """설정을 읽어 앱을 만든다. 테스트도 이 함수로 헤드리스 기동한다.

    single_instance=False 는 검증 하네스 전용이다. 이중 실행 방지는 뮤텍스를 쓰는데,
    실제 프로그램이 떠 있는 개발 PC 에서 테스트를 돌리면 테스트가 그 뮤텍스에 걸려
    프로세스째 종료된다(설계대로 동작한 결과다). 운전 경로에서는 항상 켠다.
    """
    cfg, problems = config_mod.load(resolve_config_path(config_path))
    state.cfg = cfg
    state.config_path = resolve_config_path(config_path)

    chamber_id = (cfg.get("chamber") or {}).get("id") or "chamber"
    paths.init_chamber(chamber_id)
    window.set_chamber(chamber_id, (cfg.get("chamber") or {}).get("name") or "ALD")
    logger.configure(cfg.get("log") or {}, chamber_id)

    for lv, msg in problems:
        logger.write(lv, f"설정 확인 필요 — {msg}")
        state.startup_notices.append({"level": lv, "msg": f"설정 확인 필요 — {msg}"})

    state.provider = DemoProvider(cfg)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 버전은 진단이 아니라 정보 — 로그 맨 앞에 남긴다(로그만 받아도 버전을 알 수 있게).
        logger.write("info", f"{version.APP_NAME} v{version.APP_VERSION} "
                             f"({version.BUILD_DATE}) — 챔버 {chamber_id}")
        # 이중 실행 방지는 창 경로(window.run)뿐 아니라 서버 기동 공통 경로에도 둔다.
        # `uvicorn server:app` 로 띄우면 main()을 거치지 않아 검사 없이 떠 버린다.
        if single_instance and not window.acquire_single_instance():
            msg = f"챔버 {chamber_id} 프로그램이 이미 실행 중입니다 — 기동을 중단합니다"
            print(f"[error] {msg}", flush=True)   # os._exit 는 버퍼를 비우지 않는다
            logger.write("err", msg)
            os._exit(1)

        # 기동 진단: 문제가 있어도 중단하지 않는다(진단 우선).
        # ★ 여기서 push_log 를 부르면 안 된다 — 접속한 클라이언트가 0개라 사라진다.
        #   state.startup_notices 에 쌓아 두면 접속 시 connection.py 가 전달한다.
        def notice(msg, level="warn"):
            logger.write(level, msg)
            state.startup_notices.append({"level": level, "msg": msg})

        for lv, msg in logger.drain_early():
            state.startup_notices.append({"level": lv, "msg": msg})
        if paths.DATA_DIR_ERROR:
            notice(paths.DATA_DIR_ERROR)
        ok_w, why = check_writable()
        if not ok_w:
            notice(f"데이터 폴더에 쓸 수 없습니다: {paths.CHAMBER_DIR} ({why}) — "
                   f"레시피·로그가 저장되지 않습니다.")

        name = seed_sample_recipe(cfg)
        await state.provider.start()
        if name:
            state.provider.prime(storage.load_recipe(name))
        for lv, msg in state.provider.drain_logs():
            state.add_log(lv, msg)
            logger.write(lv, msg)

        tasks = loops.start_all()
        try:
            yield
        finally:
            await loops.stop_all(tasks)
            with contextlib.suppress(Exception):
                await state.provider.stop()

    app = FastAPI(lifespan=lifespan)
    _register_routes(app)
    return app


def _asset_version() -> str:
    """정적 자산에 붙일 "앱버전-최신mtime". 파일을 고치면 URL이 바뀌어 WebView2가
    옛 자산을 재사용하지 못한다("코드는 최신인데 화면은 과거" 사고 차단)."""
    mtimes = []
    for rel in _ASSET_FILES:
        with contextlib.suppress(OSError):
            mtimes.append(os.path.getmtime(os.path.join(FRONTEND_DIR, rel)))
    return f"{version.APP_VERSION}-{int(max(mtimes)) if mtimes else 0}"


def _register_routes(app: FastAPI):
    @app.get("/")
    async def root():
        # index 는 매번 읽어 치환한다(파일이 작고, 캐시하면 버전 갱신이 막힌다).
        try:
            with open(INDEX_PATH, encoding="utf-8") as fh:
                html = fh.read().replace("__ASSET_V__", _asset_version())
        except OSError:
            return FileResponse(INDEX_PATH)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health():
        return JSONResponse({"ok": True, "chamber": paths.CHAMBER_ID,
                             "version": version.APP_VERSION})

    @app.get("/api/trend")
    async def api_trend(sec: int = 120):
        # 허용 범위 밖의 값은 잘라낸다 — sec=999999 로 전체 버퍼를 매번 직렬화하면
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

    # 정적 파일. 폴더가 없으면(빌드 시 frontend 누락) import 단계에서 죽지 않도록 검사 후 마운트.
    for url, sub in (("/css", "css"), ("/js", "js")):
        d = os.path.join(FRONTEND_DIR, sub)
        if os.path.isdir(d):
            app.mount(url, StaticFiles(directory=d), name=sub)
        else:
            # ★ 화면이 아예 안 뜨는 치명적 상황이라 print 도 남긴다(UI 로그를 볼 수 없다).
            miss = f"정적 폴더 없음: {d} — 빌드 시 frontend 가 누락됐을 수 있습니다"
            print(f"[error] {miss}")
            logger.early("err", miss)


# ===================== 실행 =====================
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=version.APP_NAME)
    ap.add_argument("--config", default="", help="설정 파일 경로 (없으면 exe 옆 config.json)")
    ap.add_argument("--headless", action="store_true", help="창 없이 서버만 띄운다(개발·검증용)")
    args, _unknown = ap.parse_known_args(argv)
    return args


def main():
    args = parse_args()
    app = create_app(args.config)
    commands.set_shutdown_handler(window.request_shutdown)
    srv = state.cfg.get("server") or {}
    host = srv.get("host") or "127.0.0.1"
    port = int(srv.get("port") or 8001)
    side = ((state.cfg.get("ui") or {}).get("window") or {}).get("side") or "left"

    if args.headless:
        import uvicorn
        free = window.find_free_port(host, port) or port
        print(f"[info] headless — http://{host}:{free}")
        uvicorn.run(app, host=host, port=free, log_level="warning", log_config=None)
        return
    window.run(app, host, port, side)


if __name__ == "__main__":
    main()
