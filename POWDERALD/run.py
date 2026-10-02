"""
run.py — Powder ALD 제어 프로그램 진입점.

실행:
    python run.py                        (exe 옆 / 이 폴더의 config.json)
    python run.py --config 경로
    python run.py --headless             (창 없이 서버만 — 개발·검증용)
    python run.py --selftest             (설정·번들 자원·서버 기동을 점검하고 종료 코드 0/1)

★ 이 프로그램은 PEALD 와 완전히 독립이다. 서로 import 하지 않고, 포트·뮤텍스·
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

from powderald import commands, device as DEV, logger, window  # noqa: E402
from powderald.server import create_app  # noqa: E402
from powderald.state import state  # noqa: E402
from powderald.version import APP_NAME  # noqa: E402


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--config", default="", help="설정 파일 경로 (없으면 exe 옆 config.json)")
    ap.add_argument("--headless", action="store_true", help="창 없이 서버만 띄운다(검증용)")
    ap.add_argument("--selftest", action="store_true",
                    help="설정 읽기·번들 자원·빈 포트로 서버 기동(/health)을 점검하고 종료한다")
    args, _unknown = ap.parse_known_args(argv)
    return args


def selftest(config_path: str = "") -> int:
    """자체 점검 — 결과 한 줄을 data/logs 에 남기고 0(통과)/1(실패)을 돌려준다.
    ★ 시뮬레이터·PLC 링크는 띄우지 않는다(점검이 실장비에 쓰는 일이 없게).
    ★ 이미 떠 있는 프로그램과 부딪히지 않도록 뮤텍스를 잡지 않고 빈 포트를 쓴다."""
    import json
    import threading
    import time
    import urllib.request
    from powderald import config as config_mod, logger, paths, version

    problems = []
    try:
        cfg, cprob, source = config_mod.load(config_path)
        errs = [m for lv, m in cprob if lv == "err"]
        if errs:
            problems.append(f"설정 오류 {len(errs)}건 ({errs[0]})")
    except Exception as e:  # noqa: BLE001
        problems.append(f"설정 읽기 실패: {e}")
        source = "?"
    need = [paths.INDEX_PATH, os.path.join(paths.FRONTEND_DIR, "css", "style.css"),
            os.path.join(paths.FRONTEND_DIR, "js", "core.js"), paths.EXAMPLE_CONFIG,
            paths.asset(DEV.ICON)]
    miss = [os.path.relpath(p, paths.BUNDLE_ROOT) for p in need if not os.path.isfile(p)]
    if miss:
        problems.append("번들 자원 없음: " + ", ".join(miss))

    health = ""
    try:
        import uvicorn
        app = create_app(config_path, single_instance=False, start_io=False)
        host = "127.0.0.1"
        port = window.find_free_port(host, DEV.DEFAULT_PORT + 50) or (DEV.DEFAULT_PORT + 50)
        from powderald.server import uvicorn_config
        server = uvicorn.Server(uvicorn_config(app, host, port))
        th = threading.Thread(target=server.run, daemon=True)
        th.start()
        body = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1) as r:
                    body = json.loads(r.read().decode("utf-8"))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        server.should_exit = True
        th.join(5)
        if not body or not body.get("ok") or body.get("device") != DEV.KEY:
            problems.append(f"/health 응답 이상: {body!r}")
        else:
            health = f"/health 정상(포트 {port})"
    except Exception as e:  # noqa: BLE001
        problems.append(f"서버 기동 실패: {type(e).__name__}: {e}")

    ok = not problems
    line = (f"자체 점검 {'통과' if ok else '실패'} — v{version.APP_VERSION} · 설정 {source} · "
            + (health if ok else " / ".join(problems)))
    logger.write("ok" if ok else "err", line)
    print(line, flush=True)
    return 0 if ok else 1


def main():
    args = parse_args()
    if args.selftest:
        sys.exit(selftest(args.config))
    app = create_app(args.config)
    commands.set_shutdown_handler(window.request_shutdown)

    srv = state.cfg.get("server") or {}
    host = srv.get("host") or "127.0.0.1"
    port = int(srv.get("port") or DEV.DEFAULT_PORT)
    side = (state.cfg.get("window") or {}).get("side") or DEV.DEFAULT_SIDE

    if args.headless:
        import uvicorn
        why = window.wait_port(host, port)
        if why:
            # ★ 창 모드와 같게 — 다른 포트로 조용히 옮기지 않고 이유를 알리고 멈춘다
            text = window.port_busy_text(host, port, why)
            print(f"[error] {text}", flush=True)
            logger.write("err", text)
            sys.exit(2)
        print(f"[info] {DEV.NAME} headless — http://{host}:{port}")
        from powderald.server import uvicorn_config
        uvicorn.Server(uvicorn_config(app, host, port)).run()
        return
    window.run(app, host, port, side)


if __name__ == "__main__":
    main()
