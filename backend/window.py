"""
window.py — pywebview 창: 좌/우 반쪽 배치, 챔버별 단일 실행, 포트 대체, 종료 확인.

server.py의 진입점에서만 쓴다. ★ server.py를 import하지 않는다(순환 import 방지) —
FastAPI 앱과 호스트/포트는 인자로 받는다.

exe 납품 대응:
  - WebView2 런타임이 없으면 콘솔이 없어 사용자에게 아무것도 안 보인다 → 메시지 박스로 안내
  - 포트가 이미 쓰이면 uvicorn이 스레드에서 죽고 빈 창이 된다 → 대체 포트 자동 탐색
  - 두 챔버를 한 화면에 나란히 띄운다 → 작업 영역의 정확히 절반씩
"""

import os
import sys
import time
import socket
import ctypes
import threading
import contextlib
import traceback

import logger
from paths import CHAMBER_DIR, check_writable

WINDOW = None         # main()에서 생성한 pywebview 창 객체를 보관
TITLE = "ALD Process Control"
_allow_close = False  # 창 닫기 허용 플래그(종료 확인 통과 후 True)
_MUTEX_HANDLE = None  # 핸들이 GC로 닫히면 뮤텍스가 풀린다 — 프로세스 수명 동안 전역 보관
_MUTEX_NAME = ""
_SERVER_ERROR = ""    # 서버 스레드가 죽은 사유. console=False 인 exe에서는 유일한 단서다.


def _msgbox(msg: str, title: str = ""):
    """Windows 메시지 박스(추가 의존성 없음). 다른 OS면 print로 폴백."""
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, title or TITLE, 0x10)
    except Exception:  # noqa: BLE001
        print(f"[{title or TITLE}] {msg}")


# ===================== 단일 실행 (챔버별) =====================
def set_chamber(chamber_id: str, chamber_name: str):
    """뮤텍스 이름과 창 제목에 챔버를 반영한다.
    ★ 뮤텍스 이름에 챔버 id를 넣어야 '같은 챔버의 이중 실행'만 막고 다른 챔버는 허용된다."""
    global _MUTEX_NAME, TITLE
    _MUTEX_NAME = f"VANAM_ALD_SingleInstance_{chamber_id or 'default'}"
    TITLE = f"{chamber_name or 'ALD'} · ALD Process Control"


def acquire_single_instance() -> bool:
    """같은 챔버가 이미 떠 있으면 False. 비Windows(개발 환경)는 항상 True.
    ★ 방지 장치 자체가 이유가 되어 실행을 막으면 안 되므로 예외 시 True."""
    if sys.platform != "win32":
        return True
    global _MUTEX_HANDLE
    # 재진입 안전: 창 경로로 실행하면 run()과 lifespan이 둘 다 부른다.
    if _MUTEX_HANDLE is not None:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        h = kernel32.CreateMutexW(None, False, _MUTEX_NAME or "VANAM_ALD_SingleInstance")
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            # 실패한 핸들을 남기면 재진입 가드가 재시도에서 "내가 잡은 것"으로 오판한다.
            with contextlib.suppress(Exception):
                kernel32.CloseHandle(h)
            _MUTEX_HANDLE = None
            return False
        _MUTEX_HANDLE = h
        return True
    except Exception:  # noqa: BLE001
        return True


# ===================== 포트 =====================
def find_free_port(host: str, start: int, tries: int = 10):
    """start부터 tries개까지 비어 있는 포트를 찾는다. 전부 막혔으면 None."""
    for p in range(start, start + tries):
        with socket.socket() as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return None


def _wait_server_ready(host: str, port: int, timeout_s: float = 20.0) -> bool:
    """서버 소켓이 바인딩될 때까지 기다린다.

    uvicorn은 lifespan(설정·진단)을 끝낸 뒤에야 소켓을 연다. 창이 그보다 먼저 URL을
    요청하면 WebView2가 연결 거부 화면을 띄우고 재시도하지 않는다."""
    deadline = time.monotonic() + timeout_s
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection((connect_host, port), 0.3):
                return True
        time.sleep(0.1)
    return False


# ===================== 화면 절반 배치 =====================
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


SPI_GETWORKAREA = 0x0030


def _enable_dpi_awareness():
    """DPI 배율 125 %·150 % 에서 작업 영역을 '물리 픽셀'로 정확히 받기 위해 필요하다.
    이걸 켜지 않으면 Windows가 값을 96 DPI 기준으로 되돌려 줘(가상화) 절반이 어긋난다."""
    with contextlib.suppress(Exception):
        # PER_MONITOR_AWARE_V2. 실패하면 구형 API로 내려간다.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    with contextlib.suppress(Exception):
        ctypes.windll.user32.SetProcessDPIAware()


def work_area():
    """작업 표시줄을 제외한 작업 영역(물리 픽셀). 실패하면 None."""
    if sys.platform != "win32":
        return None
    try:
        r = _RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0,
                                                          ctypes.byref(r), 0):
            return None
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:  # noqa: BLE001
        return None


def half_rect(side: str):
    """좌/우 반쪽의 물리 픽셀 사각형 (x, y, w, h). 실패하면 None.
    ★ 폭은 나누기 전에 정수로 자르고 오른쪽이 나머지를 가져간다 — 홀수 폭에서
      1 px 틈이나 겹침이 생기지 않게."""
    wa = work_area()
    if not wa:
        return None
    x, y, w, h = wa
    half = w // 2
    if (side or "left").lower() == "right":
        return x + half, y, w - half, h
    return x, y, half, h


def _place_window(rect):
    """창이 뜬 뒤 Win32로 직접 위치·크기를 잡는다.

    ★ pywebview 의 width/height 가 논리 픽셀인지 물리 픽셀인지는 백엔드·버전마다 다르다.
      HWND 에 직접 물리 픽셀을 주면 그 차이를 아예 없앨 수 있다(125 %·150 % 에서도 정확히 절반).
    """
    if sys.platform != "win32" or not rect:
        return False
    x, y, w, h = rect
    user32 = ctypes.windll.user32
    SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
    for _ in range(40):          # 창이 만들어질 때까지 최대 4초 기다린다
        try:
            hwnd = user32.FindWindowW(None, TITLE)
            if hwnd:
                user32.ShowWindow(hwnd, 1)        # SW_SHOWNORMAL — 최대화 상태면 해제
                user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
                return True
        except Exception:  # noqa: BLE001
            return False
        time.sleep(0.1)
    return False


# ===================== 종료 =====================
def request_shutdown():
    """프로그램 종료 / 창 X 종료확인 통과 → 확실히 프로세스를 종료한다."""
    global _allow_close
    _allow_close = True

    def _force_exit():
        time.sleep(0.3)   # 정리 flush 여유 후 강제 종료(데드락과 무관하게 무조건 종료)
        os._exit(0)

    threading.Thread(target=_force_exit, daemon=True).start()
    if WINDOW is not None:
        try:
            WINDOW.destroy()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 창 종료 실패: {e}")


class _JsBridge:
    """pywebview js_api — 화면 JS가 서버를 거치지 않고 프로세스를 종료시키는 직통 경로.
    ws 가 끊기면 'exit' 명령이 오프라인 차단에 걸려 종료가 불가능해진다."""

    def force_close(self):
        request_shutdown()
        return True


def _on_closing():
    """창 우상단 X → 앱 내부 종료확인 모달로 되묻는다(확인 전엔 닫기 취소).
    ★ WebView2 데드락 방지: evaluate_js 를 closing 핸들러에서 '동기' 호출하면 GUI 스레드가
      재진입 데드락에 빠져 멈춘다. 반드시 별도 스레드에서 호출하고 즉시 반환해야 한다."""
    if _allow_close:
        return True

    # 모달을 띄웠다는 '확증'이 있을 때만 창을 유지한다. 오류 페이지처럼 앱 JS가 없는
    # 화면에서는 X가 영원히 무시돼 작업 관리자로만 끌 수 있게 된다.
    asked = []

    def _ask():
        try:
            ok = WINDOW.evaluate_js(
                "(function(){ if(window.requestExitConfirm){window.requestExitConfirm();"
                " return true;} return false; })()")
            if ok:
                asked.append(True)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 종료확인 모달 호출 실패: {e}")

    def _confirm_or_close():
        t = threading.Thread(target=_ask, daemon=True)
        t.start()
        t.join(5.0)
        if not asked:
            logger.write("warn", "창 닫기: 앱 화면 무응답 → 강제 종료")
            request_shutdown()

    threading.Thread(target=_confirm_or_close, daemon=True).start()
    return False


# ===================== 실행 =====================
def run(app, host: str, port: int, side: str = "left"):
    """서버를 별도 스레드로 띄우고 창을 연다. 창을 닫으면 반환된다."""
    import uvicorn

    # 같은 챔버의 이중 실행 차단. 인스턴스가 2개면 포트 회피로 둘 다 뜨고,
    # 다음 단계에서는 둘 다 같은 PLC에 붙어 서로 밸브 명령을 덮어쓴다.
    # 종료 직후 재실행은 직전 인스턴스의 정리가 끝나기 전이라 뮤텍스가 아직 잡혀 있다 →
    # 최대 3초 기다렸다가 판정한다.
    got = acquire_single_instance()
    deadline = time.monotonic() + 3.0
    while not got and time.monotonic() < deadline:
        time.sleep(0.25)
        got = acquire_single_instance()
    if not got:
        _msgbox("이 챔버의 프로그램이 이미 실행 중입니다.\n작업 표시줄에서 기존 창을 확인하세요.")
        return

    # 쓰기 불가면 설정·레시피가 저장되지 않는다. ★ 중단하지 않는다 — 읽기 전용이어도
    # 화면으로 장비 상태를 보는 것은 가능해야 한다.
    ok_w, _why = check_writable()
    if not ok_w:
        _msgbox("데이터 폴더에 쓸 수 없습니다.\n"
                f"{CHAMBER_DIR}\n\n"
                "레시피와 로그가 저장되지 않습니다.\n"
                "쓰기 가능한 경로로 옮겨서 실행하세요.")

    free = find_free_port(host, port)
    if free is None:
        _msgbox(f"사용 가능한 포트를 찾지 못했습니다 ({port}~{port + 9}).\n"
                "다른 프로그램을 종료한 뒤 다시 실행하세요.")
        return
    if free != port:
        # 콘솔 없는 exe에서는 print가 증발한다 → 파일 로그에도 남긴다.
        print(f"[info] 포트 {port} 사용 중 → {free} 사용")
        logger.early("info", f"포트 {port} 사용 중 → {free} 사용")
    port = free

    def run_server():
        global _SERVER_ERROR
        try:
            # log_config=None: uvicorn 자체 로깅 dictConfig 를 타지 않는다.
            # 창 전용 exe 에서 그 구성이 sys.stdout.isatty() 로 죽는다(server.py 가드와 짝).
            uvicorn.run(app, host=host, port=port, log_level="warning", log_config=None)
        except Exception as e:  # noqa: BLE001
            _SERVER_ERROR = f"{type(e).__name__}: {e}"
            print(f"[error] 내부 서버가 중단되었습니다: {traceback.format_exc()}")
            logger.early("err", f"내부 서버 중단: {_SERVER_ERROR}")
            logger.write("err", f"내부 서버 중단: {_SERVER_ERROR}")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    view_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{view_host}:{port}"

    try:
        import webview  # pywebview
    except Exception as e:  # noqa: BLE001
        print(f"[info] pywebview를 불러올 수 없습니다 ({e}).")
        print(f"[info] 브라우저에서 {url} 를 열어 사용하세요. (Ctrl+C 종료)")
        logger.early("warn", f"pywebview를 불러올 수 없습니다 — 브라우저에서 {url} 로 접속하세요")
        _msgbox("화면을 표시할 수 없습니다.\n"
                "Microsoft Edge WebView2 런타임이 설치되어 있지 않을 수 있습니다.\n"
                f"설치 후 다시 실행하거나, 브라우저에서 {url} 로 접속하세요.")
        with contextlib.suppress(KeyboardInterrupt):
            server_thread.join()
        return

    if not _wait_server_ready(view_host, port):
        reason = ("내부 서버가 시작되지 못했습니다.\n\n" + _SERVER_ERROR) if _SERVER_ERROR \
            else "내부 서버가 시간 안에 시작되지 않았습니다."
        print(f"[error] {reason}")
        logger.write("err", reason.replace("\n", " "))
        _msgbox(reason + "\n\n로그 폴더를 확인하세요.\n" + logger.current_dir())
        return

    _enable_dpi_awareness()
    rect = half_rect(side)
    if rect:
        x, y, w, h = rect
    else:
        # 작업 영역을 못 읽으면 pywebview 가 보고하는 화면 크기로 폴백한다.
        try:
            scr = webview.screens[0]
            w, h = scr.width // 2, scr.height
            x = 0 if (side or "left").lower() == "left" else w
            y = 0
        except Exception:  # noqa: BLE001
            x, y, w, h = 0, 0, 960, 1000
        logger.early("warn", "작업 영역을 읽지 못해 화면 크기 기준으로 배치합니다")

    global WINDOW
    WINDOW = webview.create_window(
        TITLE, url,
        x=x, y=y, width=w, height=h,
        js_api=_JsBridge(),        # 서버가 죽어도 화면에서 종료할 수 있는 직통 경로
    )
    try:
        WINDOW.events.closing += _on_closing
    except Exception as e:  # noqa: BLE001
        print(f"[warn] closing 이벤트 연결 실패: {e}")

    # 창이 뜬 뒤 Win32로 다시 한 번 정확히 맞춘다(논리/물리 픽셀 단위 차이 제거).
    threading.Thread(target=_place_window, args=(rect or (x, y, w, h),), daemon=True).start()

    try:
        webview.start()   # 창을 닫으면 여기서 반환 → 데몬 스레드와 함께 종료
    except Exception as e:  # noqa: BLE001
        print(f"[error] 창을 띄우지 못했습니다: {e}")
        _msgbox("화면을 표시할 수 없습니다.\n"
                "Microsoft Edge WebView2 런타임이 설치되어 있지 않을 수 있습니다.\n"
                f"설치 후 다시 실행하거나, 브라우저에서 {url} 로 접속하세요.")
