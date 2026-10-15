"""test/_pkg.py — 시험 도구(fake_plc · plc_ctl · e2e_process)가 이 프로그램 패키지를 찾는 곳.

★ PEALD · POWDERALD 의 test/ 폴더는 같은 파일이다. 패키지 이름(peald / powderald)은 이 폴더 옆에 있는
  것으로 정한다 — 두 프로그램은 서로 import 하지 않는다.
★ 시험 도구는 exe 에 들어가지 않는다(build.spec 은 run.py 에서 import 하는 것만 묶는다).
"""
import os
import sys
import importlib

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TEST_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PKG = next((n for n in ("peald", "powderald") if os.path.isdir(os.path.join(ROOT, n))), None)
if PKG is None:
    raise SystemExit("프로그램 패키지(peald / powderald)를 찾지 못했습니다 — test/ 는 프로그램 폴더 안에 있어야 합니다")


def mod(name: str):
    """이 프로그램 패키지의 모듈 — mod('addresses') 등."""
    return importlib.import_module(PKG + "." + name)


def utf8_console():
    """Windows 기본 콘솔(cp949)에서 '—' 같은 글자로 죽지 않게."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def parse_int(text) -> int:
    """'0x5045' · '5045h' · '1000' · 'D01000' · '01004' 을 정수로.
    ★ v0.6.1(C10): 앞자리 0 은 10 진(주소표 표기 '01004') — int('01004', 0) 은 ValueError 다."""
    t = str(text).strip().upper()
    if t.startswith("D") and t[1:].isdigit():
        return int(t[1:])
    if t.endswith("H"):
        return int(t[:-1], 16)
    if t.isdigit():
        return int(t, 10)
    return int(t, 0)


def parse_id(text) -> int:
    """장비 ID — 늘 16 진(화면 · 래더 표기 h5045 와 같게). '5045' · '0x5045' · 'h5045' · '5045h' → 0x5045."""
    t = str(text).strip().upper()
    if t.startswith("0X"):
        t = t[2:]
    elif t.startswith("H"):
        t = t[1:]
    elif t.endswith("H"):
        t = t[:-1]
    return int(t, 16) & 0xFFFF


def config_path(path: str = "") -> str:
    """시험 도구가 읽을 설정 파일 — --config 가 없으면 프로그램과 같은 기본 경로(config.json), 없으면 예시 설정.
    ★ v0.6.1(C5): 전에는 늘 예시 설정을 읽어 아날로그 환산이 사용자 config.json 과 다를 수 있었다."""
    paths = mod("paths")
    if path:
        return os.path.abspath(path)
    return paths.DEFAULT_CONFIG_PATH if os.path.isfile(paths.DEFAULT_CONFIG_PATH) else paths.EXAMPLE_CONFIG


def load_config(path: str = ""):
    """(cfg, 읽은 파일, 설명). 설정만 읽고 data/ 에는 쓰지 않는다 — 데이터 자리는 없는 폴더로(폴더를 만들지 않음)."""
    import tempfile
    paths = mod("paths")
    target = config_path(path)
    example = os.path.abspath(paths.EXAMPLE_CONFIG)
    paths.set_data_root(os.path.join(tempfile.gettempdir(), "ald_test_tools_no_data"))
    cfg, _problems, source = mod("config").load(target)
    used = cfg.get("_path") or target
    what = "예시 설정" if (source != "file" or os.path.abspath(used) == example) else "설정 파일"
    return cfg, used, what


def ask(prompt: str):
    """입력 한 줄. EOF(Ctrl+Z · Ctrl+D · 닫힌 입력)면 None — traceback 없이 '쓰지 않음'으로 끝나게(C10)."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def exit_on_stdin_eof(on_eof):
    """★ v0.6.1(C9): 시험 틀(부모)이 강제로 꺼지면 자식의 stdin 파이프가 닫힌다(EOF) — 그때 on_eof() 를 부른다.
    부모가 stdin=PIPE 로 띄운 자식에서만 쓴다(콘솔에서 사람이 띄우면 stdin 이 콘솔이라 쓰지 않는다)."""
    import threading

    def watch():
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except Exception:  # noqa: BLE001
            pass
        on_eof()
    threading.Thread(target=watch, name="stdin-eof", daemon=True).start()


def alive(proc) -> bool:
    """자식 프로세스가 살아 있나 — Popen.poll() 로만 본다.
    ★ Windows 에서 os.kill(pid, 0) 은 '살아 있나'가 아니라 프로세스를 끝낸다(TerminateProcess) — 쓰지 않는다."""
    return proc is not None and proc.poll() is None
