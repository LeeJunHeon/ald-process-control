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
    """'0x5045' · '5045h' · '1000' · 'D01000' 을 정수로."""
    t = str(text).strip().upper()
    if t.startswith("D") and t[1:].isdigit():
        return int(t[1:])
    if t.endswith("H"):
        return int(t[:-1], 16)
    return int(t, 0)
