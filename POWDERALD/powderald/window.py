"""
window.py — pywebview 창: 좌/우 반쪽 배치, 단일 실행, 포트 대체, 종료 확인, 아이콘.

server.py 의 진입점에서만 쓴다. ★ server.py 를 import 하지 않는다(순환 방지) —
FastAPI 앱과 호스트/포트는 인자로 받는다.

★ 뮤텍스·AppUserModelID·창 제목은 device.py 에서 온다. 두 프로그램이 서로를
  같은 프로그램으로 보면(뮤텍스가 같으면) 한쪽이 아예 뜨지 않는다.
"""

import os
import sys
import time
import socket
import ctypes
import threading
import contextlib
import traceback

from . import logger
from . import paths
from . import device as DEV
from .config import host_valid

WINDOW = None
TITLE = DEV.TITLE
_allow_close = False
_MUTEX_HANDLE = None
_SERVER_ERROR = ""


def _msgbox(msg: str):
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, DEV.NAME, 0x10)
    except Exception:  # noqa: BLE001
        print(f"[{DEV.NAME}] {msg}")


# ===================== 단일 실행 =====================
def acquire_single_instance() -> bool:
    """같은 장비의 프로그램이 이미 떠 있으면 False. 비Windows는 항상 True.
    ★ 방지 장치 자체가 이유가 되어 실행을 막으면 안 되므로 예외 시 True."""
    if sys.platform != "win32":
        return True
    global _MUTEX_HANDLE
    if _MUTEX_HANDLE is not None:      # 재진입 안전(창 경로와 lifespan 이 둘 다 부른다)
        return True
    try:
        k32 = ctypes.windll.kernel32
        h = k32.CreateMutexW(None, False, DEV.MUTEX_NAME)
        if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            with contextlib.suppress(Exception):
                k32.CloseHandle(h)
            _MUTEX_HANDLE = None
            return False
        _MUTEX_HANDLE = h
        return True
    except Exception:  # noqa: BLE001
        return True


def set_app_user_model_id():
    """작업표시줄에서 두 프로그램이 따로 묶이게 한다.
    이것을 설정하지 않으면 같은 python.exe 로 묶여 아이콘이 하나로 합쳐진다."""
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(DEV.APP_USER_MODEL_ID)


# ===================== 포트 =====================
PORT_WAIT_S = 5.0       # 설정 포트가 쓰이고 있으면 이만큼 다시 시도한 뒤 이유를 알리고 멈춘다


def bind_sockets(host: str, port: int) -> list:
    """★ v0.4.12: 서버가 들을 소켓을 먼저 묶는다 — 확인이 곧 실제 bind 다(확인과 서버 bind 사이에 다른 프로그램이
    끼거나, IPv4 로만 확인해 '::' · '::1' 을 늘 '사용 중'으로 보던 것 · localhost 를 127.0.0.1 로만 보던 것).
    asyncio.create_server(uvicorn)와 같게: getaddrinfo(AF_UNSPEC · AI_PASSIVE)의 주소마다 하나씩, IPv6 는 V6ONLY,
    POSIX 만 SO_REUSEADDR(TIME_WAIT 만 남은 포트도 열린다 — Windows 는 옵션 없이도 열린다). 실패하면 연 것을 닫고
    OSError(이름을 못 풀면 socket.gaierror). 돌려준 소켓은 uvicorn Server.run(sockets=…) 에 넘긴다."""
    if not host_valid(host):
        raise OSError(f"server.host 형식이 올바르지 않습니다: {host!r} (빈 값 · localhost · IP 주소)")
    infos = socket.getaddrinfo(host or None, port, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_PASSIVE)
    socks, seen = [], set()
    try:
        for af, st, proto, _cn, sa in infos:
            if (af, sa) in seen:
                continue
            seen.add((af, sa))
            s = socket.socket(af, st, proto)
            socks.append(s)
            if os.name == "posix":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if af == getattr(socket, "AF_INET6", None) and hasattr(socket, "IPPROTO_IPV6"):
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            s.bind(sa)
    except BaseException:
        close_sockets(socks)
        raise
    if not socks:
        raise OSError(f"{host!r} 의 주소를 찾지 못했습니다")
    return socks


def close_sockets(socks):
    for s in socks or ():
        with contextlib.suppress(OSError):
            s.close()


def port_probe(host: str, port: int) -> str:
    """포트를 서버가 열 수 있는가 — 빈 문자열이면 된다, 아니면 이유. bind_sockets 로 실제로 묶어 보고 닫는다
    (서버와 같은 주소 · 같은 소켓 옵션). 자체 점검의 빈 포트 고르기와 시험용 — 설정 포트는 bind_port 로 묶은 채 쓴다."""
    try:
        close_sockets(bind_sockets(host, port))
        return ""
    except OSError as e:
        return f"{type(e).__name__}: {e}"


def bind_port(host: str, port: int, total_s: float = None, step_s: float = 0.5):
    """설정 포트를 잠시(PORT_WAIT_S) 다시 시도하며 묶는다 — (소켓 목록, '') 또는 ([], 마지막 이유).
    ★ 다른 포트로 조용히 옮기지 않는다 — 열린 화면 · 원격 화면은 설정 포트를 계속 두드린다.
    ★ 형식이 틀린 host · 풀 수 없는 이름은 기다려도 안 바뀐다 — 바로 돌려준다."""
    total_s = PORT_WAIT_S if total_s is None else total_s
    end = time.monotonic() + total_s
    while True:
        try:
            return bind_sockets(host, port), ""
        except socket.gaierror as e:
            return [], f"{type(e).__name__}: {e}"
        except OSError as e:
            why = f"{type(e).__name__}: {e}"
            if not host_valid(host) or time.monotonic() >= end:
                return [], why
        time.sleep(step_s)


def wait_port(host: str, port: int, total_s: float = None, step_s: float = 0.5) -> str:
    """bind_port 와 같게 기다리되 묶은 소켓은 닫는다(빈 문자열이면 열 수 있다). 묶은 채 서버에 넘기려면 bind_port."""
    socks, why = bind_port(host, port, total_s, step_s)
    close_sockets(socks)
    return why


def view_host(host: str) -> str:
    """이 PC 화면이 붙을 주소 — 모든 주소(빈 값 · 0.0.0.0 · ::)면 루프백, IPv6 는 URL 에 [ ]."""
    if host in ("", "0.0.0.0"):
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def url_host(host: str) -> str:
    h = view_host(host)
    return f"[{h}]" if ":" in h else h


def port_busy_text(host: str, port: int, why: str) -> str:
    if not host_valid(host) or "gaierror" in why:
        return (f"설정 server.host {host!r} 로 서버를 열 수 없습니다 — 빈 값(모든 주소) · localhost · IP 주소만 "
                f"씁니다. 설정을 고치세요. ({why})")
    return (f"설정 포트 {host}:{port} 를 열 수 없습니다 — 다른 프로그램(또는 이미 떠 있는 이 프로그램)이 쓰고 "
            f"있습니다. {PORT_WAIT_S:g} s 다시 시도했습니다. 쓰는 프로그램을 끄거나 설정 server.port 를 바꾸세요. ({why})")


def find_free_port(host: str, start: int, tries: int = 10):
    """비어 있는 포트(자체 점검처럼 내부용 포트를 고를 때만 — 설정 포트에는 wait_port)."""
    for p in range(start, start + tries):
        if not port_probe(host, p):
            return p
    return None


def _wait_server_ready(host: str, port: int, timeout_s: float = 20.0) -> bool:
    """서버 소켓이 열릴 때까지 기다린다. 창이 먼저 뜨면 WebView2 가 연결 거부 화면을
    띄우고 재시도하지 않는다."""
    deadline = time.monotonic() + timeout_s
    h = view_host(host)
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection((h, port), 0.3):
                return True
        time.sleep(0.1)
    return False


# ===================== 화면 절반 배치 =====================
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


SPI_GETWORKAREA = 0x0030


def enable_dpi_awareness():
    """DPI 125 %·150 % 에서 작업 영역을 '물리 픽셀'로 정확히 받기 위해 필요하다.
    켜지 않으면 Windows 가 값을 96 DPI 기준으로 되돌려 줘(가상화) 절반이 어긋난다."""
    with contextlib.suppress(Exception):
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
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
            return None
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:  # noqa: BLE001
        return None


def half_rect(side: str):
    """좌/우 반쪽의 물리 픽셀 사각형 (x, y, w, h).
    ★ 폭은 정수로 자르고 오른쪽이 나머지를 가져간다 — 홀수 폭에서 1 px 틈이나 겹침이
      생기지 않게."""
    wa = work_area()
    if not wa:
        return None
    x, y, w, h = wa
    half = w // 2
    if (side or "left").lower() == "right":
        return x + half, y, w - half, h
    return x, y, half, h


def _place_window(rect):
    """창이 뜬 뒤 Win32 로 직접 위치·크기를 잡는다.
    ★ pywebview 의 width/height 가 논리 픽셀인지 물리 픽셀인지는 백엔드·버전마다 다르다.
      HWND 에 직접 물리 픽셀을 주면 그 차이를 아예 없앨 수 있다."""
    if sys.platform != "win32" or not rect:
        return False
    x, y, w, h = rect
    user32 = ctypes.windll.user32
    SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
    for _ in range(40):
        try:
            hwnd = user32.FindWindowW(None, TITLE)
            if hwnd:
                user32.ShowWindow(hwnd, 1)
                user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
                _set_window_icon(hwnd)
                return True
        except Exception:  # noqa: BLE001
            return False
        time.sleep(0.1)
    return False


def _set_window_icon(hwnd):
    """창·작업표시줄 아이콘. 실패해도 프로그램 동작에는 영향이 없다."""
    ico = paths.asset(DEV.ICON)
    if not os.path.isfile(ico):
        return
    with contextlib.suppress(Exception):
        u = ctypes.windll.user32
        IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
        big = u.LoadImageW(None, ico, IMAGE_ICON, 32, 32, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        small = u.LoadImageW(None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        WM_SETICON = 0x0080
        if big:
            u.SendMessageW(hwnd, WM_SETICON, 1, big)
        if small:
            u.SendMessageW(hwnd, WM_SETICON, 0, small)


# ===================== 종료 =====================
def request_shutdown():
    global _allow_close
    _allow_close = True

    def _force_exit():
        time.sleep(0.3)   # 정리 flush 여유 후 강제 종료
        os._exit(0)

    threading.Thread(target=_force_exit, daemon=True).start()
    if WINDOW is not None:
        try:
            WINDOW.destroy()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 창 종료 실패: {e}")


class _JsBridge:
    """화면 JS 가 서버를 거치지 않고 프로세스를 종료시키는 직통 경로.
    서버가 죽으면 'exit' 명령이 오프라인 차단에 걸려 종료가 불가능해진다."""

    def force_close(self):
        request_shutdown()
        return True


def _on_closing():
    """창 X → 앱 내부 종료확인 모달로 되묻는다.
    ★ WebView2 데드락 방지: evaluate_js 를 closing 핸들러에서 동기 호출하면 GUI 스레드가
      재진입 데드락에 빠진다. 반드시 별도 스레드에서 호출하고 즉시 반환한다."""
    if _allow_close:
        return True
    asked = []

    def _ask():
        try:
            if WINDOW.evaluate_js("(function(){if(window.requestExitConfirm)"
                                  "{window.requestExitConfirm();return true;}return false;})()"):
                asked.append(True)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 종료확인 모달 호출 실패: {e}")

    def _confirm_or_close():
        t = threading.Thread(target=_ask, daemon=True)
        t.start()
        t.join(5.0)
        if not asked:
            # 앱 JS 없음·무응답 — 닫히지 않는 창은 허용하지 않는다.
            logger.write("warn", "창 닫기: 앱 화면 무응답 → 강제 종료")
            request_shutdown()

    threading.Thread(target=_confirm_or_close, daemon=True).start()
    return False


# ===================== 실행 =====================
def run(app, host: str, port: int, side: str = None):
    import uvicorn

    set_app_user_model_id()
    got = acquire_single_instance()
    deadline = time.monotonic() + 3.0
    while not got and time.monotonic() < deadline:
        # 종료 직후 재실행은 직전 인스턴스의 정리가 끝나기 전이라 뮤텍스가 아직 잡혀 있다.
        time.sleep(0.25)
        got = acquire_single_instance()
    if not got:
        _msgbox(f"{DEV.NAME} 프로그램이 이미 실행 중입니다.\n작업 표시줄에서 기존 창을 확인하세요.")
        return

    ok_w, _why = paths.check_writable()
    if not ok_w:
        # ★ 중단하지 않는다 — 읽기 전용이어도 장비 상태를 보는 것은 가능해야 한다.
        _msgbox("데이터 폴더에 쓸 수 없습니다.\n"
                f"{paths.DATA_DIR}\n\n로그와 알람 이력이 저장되지 않습니다.")

    # ★ v0.4.12: 포트를 먼저 묶는다(확인이 곧 실제 bind). 못 묶으면 서버 · PLC 링크 · 시뮬레이터(lifespan)를
    #   띄우기 전에 이유를 알리고 멈춘다 — uvicorn 이 스스로 bind 하면 lifespan(PLC 링크)이 먼저 돌고, bind 실패는
    #   SystemExit 라 아래 except 에도 안 잡혔다
    socks, why = bind_port(host, port)
    if not socks:
        text = port_busy_text(host, port, why)
        print(f"[error] {text}")
        logger.write("err", text)
        _msgbox(text)
        return

    def run_server():
        global _SERVER_ERROR
        try:
            # log_config=None: uvicorn 자체 로깅 dictConfig 를 타지 않는다
            # (창 전용 exe 에서 sys.stdout.isatty() 로 죽는다).
            from .server import uvicorn_config
            uvicorn.Server(uvicorn_config(app, host, port)).run(sockets=socks)
        except BaseException as e:  # noqa: BLE001 — uvicorn 은 시작 실패를 SystemExit 로 낸다
            _SERVER_ERROR = f"{type(e).__name__}: {e}"
            print(f"[error] 내부 서버가 중단되었습니다: {traceback.format_exc()}")
            logger.write("err", f"내부 서버 중단: {_SERVER_ERROR}")
        finally:
            close_sockets(socks)

    threading.Thread(target=run_server, daemon=True).start()
    url = f"http://{url_host(host)}:{port}"

    try:
        import webview
    except Exception as e:  # noqa: BLE001
        print(f"[info] pywebview 를 불러올 수 없습니다 ({e}). 브라우저에서 {url} 를 여세요.")
        _msgbox("화면을 표시할 수 없습니다.\nMicrosoft Edge WebView2 런타임이 없을 수 있습니다.\n"
                f"설치 후 다시 실행하거나, 브라우저에서 {url} 로 접속하세요.")
        return

    if not _wait_server_ready(host, port):
        reason = ("내부 서버가 시작되지 못했습니다.\n\n" + _SERVER_ERROR) if _SERVER_ERROR \
            else "내부 서버가 시간 안에 시작되지 않았습니다."
        logger.write("err", reason.replace("\n", " "))
        _msgbox(reason + "\n\n로그 폴더를 확인하세요.\n" + logger.current_dir())
        return

    enable_dpi_awareness()
    rect = half_rect(side or DEV.DEFAULT_SIDE)
    if rect:
        x, y, w, h = rect
    else:
        try:
            scr = webview.screens[0]
            w, h = scr.width // 2, scr.height
            x = 0 if (side or DEV.DEFAULT_SIDE).lower() == "left" else w
            y = 0
        except Exception:  # noqa: BLE001
            x, y, w, h = 0, 0, 960, 1000
        logger.early("warn", "작업 영역을 읽지 못해 화면 크기 기준으로 배치합니다")

    global WINDOW
    WINDOW = webview.create_window(TITLE, url, x=x, y=y, width=w, height=h,
                                   js_api=_JsBridge())
    try:
        WINDOW.events.closing += _on_closing
    except Exception as e:  # noqa: BLE001
        print(f"[warn] closing 이벤트 연결 실패: {e}")

    threading.Thread(target=_place_window, args=(rect or (x, y, w, h),), daemon=True).start()
    try:
        webview.start()
    except Exception as e:  # noqa: BLE001
        print(f"[error] 창을 띄우지 못했습니다: {e}")
        _msgbox("화면을 표시할 수 없습니다.\nMicrosoft Edge WebView2 런타임이 없을 수 있습니다.\n"
                f"설치 후 다시 실행하거나, 브라우저에서 {url} 로 접속하세요.")
