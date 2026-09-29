"""
run.py — PEALD 제어 프로그램 진입점.

실행:
    python run.py                        (exe 옆 / 이 폴더의 config.json)
    python run.py --config 경로
    python run.py --headless             (창 없이 서버만 — 개발·검증용)

★ 이 프로그램은 POWDERALD 와 완전히 독립이다. 서로 import 하지 않고, 포트·뮤텍스·
  데이터 폴더도 전부 따로 쓴다. 한쪽이 멈춰도 다른 쪽은 돌아야 한다.
"""

import os
import sys

# 창 전용(console=False) exe: 콘솔이 없어 sys.stdout/sys.stderr 가 None 이다.
# 라이브러리가 .isatty()/.write() 를 직접 부르면 그 자리에서 죽는다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 한국어 진단 메시지를 콘솔에 찍다가 프로그램이 죽는 것을 막는다.
# Windows 기본 콘솔 코덱(cp949)은 '—' 같은 글자를 인코딩하지 못해 UnicodeEncodeError 로
# 그 자리에서 죽는다 — 로그 한 줄 때문에 장비 화면이 안 뜨는 일은 없어야 한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse  # noqa: E402

from peald import commands, device as DEV, window  # noqa: E402
from peald.server import create_app  # noqa: E402
from peald.state import state  # noqa: E402
from peald.version import APP_NAME  # noqa: E402


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--config", default="", help="설정 파일 경로 (없으면 exe 옆 config.json)")
    ap.add_argument("--headless", action="store_true", help="창 없이 서버만 띄운다(검증용)")
    args, _unknown = ap.parse_known_args(argv)
    return args


def main():
    args = parse_args()
    app = create_app(args.config)
    commands.set_shutdown_handler(window.request_shutdown)

    srv = state.cfg.get("server") or {}
    host = srv.get("host") or "127.0.0.1"
    port = int(srv.get("port") or DEV.DEFAULT_PORT)
    side = (state.cfg.get("window") or {}).get("side") or DEV.DEFAULT_SIDE

    if args.headless:
        import uvicorn
        free = window.find_free_port(host, port) or port
        print(f"[info] {DEV.NAME} headless — http://{host}:{free}")
        uvicorn.run(app, host=host, port=free, log_level="warning", log_config=None)
        return
    window.run(app, host, port, side)


if __name__ == "__main__":
    main()
