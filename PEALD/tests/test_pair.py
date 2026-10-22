"""두 프로그램 짝 맞춤 — PEALD · POWDERALD 는 서로 import 하지 않고 따로 돌지만, 같은 방식으로 만든다.

한쪽에만 있는 파일, 한쪽만 고친 공통 파일, 한쪽에만 있거나 예시 설정보다 오래된 config.json 을 찾는다.
★ 이 파일은 두 폴더가 바이트까지 같다 — 패키지를 import 하지 않고 폴더 이름으로 짝을 찾는다.
★ 짝 폴더가 옆에 없으면(현장 PC 처럼 한 프로그램만 둔 곳) 건너뛴다.
★ 실행하며 생기는 것(data/ · 로그 · 캐시 · 빌드 산출물)은 보지 않는다 — .gitignore 와 같은 기준.
★ config.json 은 추적하지 않지만 이 PC 에서 두 프로그램을 돌리려면 두 폴더 모두 있어야 한다 — 한쪽에만 두면
  그쪽 프로그램만 그 설정으로 뜨고, 다른 쪽은 예시 설정(내장 시뮬레이터)으로 떠서 시험이 어긋난다.
"""
import os
import re
import json

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ME = os.path.basename(ROOT)
PKGS = {"PEALD": "peald", "POWDERALD": "powderald"}
PAIR = {"PEALD": "POWDERALD", "POWDERALD": "PEALD"}.get(ME, "")
OTHER = os.path.join(os.path.dirname(ROOT), PAIR) if PAIR else ""

# 실행하며 생기는 것 · 편집기 · OS 가 만드는 것 — 비교하지 않는다(.gitignore 와 같은 기준)
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", "venv", ".git", ".vscode", ".idea",
             "data", "logs", "datalog", "build", "dist", "screenshot", "screenshots"}
SKIP_FILES = {"Thumbs.db", "desktop.ini", ".DS_Store"}
SKIP_EXT = (".pyc", ".tmp", ".log", ".png")
# 장비마다 내용이 다른 파일 — 있기는 두 폴더 모두 있어야 한다('pkg' = 패키지 이름 자리)
DEVICE_FILES = {"README.md", "config.json", "config/config.example.json", "pkg/device.py",
                "frontend/js/views/pid_model.js", "assets/pkg.ico"}
# 예시 설정에서 장비 전용 항목(PEALD: RF · PCV / Powder: O3) — 이 말이 이름 마디에 든 키
DEVICE_KEY = re.compile(r"(^|[._])(rf|pcv|o3)([._]|$)")

pytestmark = pytest.mark.skipif(not (OTHER and os.path.isdir(os.path.join(OTHER, PKGS.get(PAIR, "?")))),
                                reason="짝 프로그램 폴더가 옆에 없다")


def _files(root: str, name: str) -> dict:
    """{패키지 이름을 'pkg' 로 바꾼 상대 경로: 실제 경로}."""
    pkg = PKGS[name]
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for f in fns:
            if f in SKIP_FILES or f.endswith(SKIP_EXT):
                continue
            rel = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
            out[rel.replace(pkg, "pkg")] = os.path.join(dp, f)
    return out


def _norm(data: bytes):
    """이름만 다른 줄을 같게 본다 — 패키지(peald · powderald) · 폴더(PEALD · POWDERALD) · 표시 이름(Powder ALD).
    줄바꿈은 LF 로(저장소는 LF — .gitattributes)."""
    try:
        t = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    for a in ("POWDERALD", "PEALD", "Powder ALD"):
        t = t.replace(a, "PKG")
    for a in ("powderald", "peald"):
        t = t.replace(a, "pkg")
    return t.replace("\r\n", "\n")


def _keys(d: dict, pre: str = "") -> set:
    """설정의 키 경로(dict 만 따라간다 — 목록은 통째로 바뀌므로 보지 않는다). '_' 로 시작하는 설명 키는 뺀다."""
    out = set()
    for k, v in d.items():
        if k.startswith("_"):
            continue
        out.add(pre + k)
        if isinstance(v, dict):
            out |= _keys(v, pre + k + ".")
    return out


def _load(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def test_same_files_in_both_programs():
    """한쪽에만 있는 파일이 없다(추적 안 하는 config.json 도)."""
    a, b = _files(ROOT, ME), _files(OTHER, PAIR)
    only_me, only_other = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    assert not only_me and not only_other, (f"{ME} 에만: {only_me} · {PAIR} 에만: {only_other} — "
                                            "두 프로그램은 같은 파일을 가진다(한쪽에 만들면 다른 쪽에도)")


def test_common_files_same_except_names():
    """장비 전용 파일 말고는 이름(패키지 · 폴더 · 표시 이름)만 빼고 같다 — 한쪽만 고친 것이 없다."""
    a, b = _files(ROOT, ME), _files(OTHER, PAIR)
    diff = []
    for k in sorted((set(a) & set(b)) - DEVICE_FILES):
        with open(a[k], "rb") as fa, open(b[k], "rb") as fb:
            if _norm(fa.read()) != _norm(fb.read()):
                diff.append(k)
    assert not diff, f"이름만 빼고 같아야 하는데 다르다: {diff} — 한쪽만 고쳤다(두 폴더 같이 고친다)"


def test_example_configs_have_same_items():
    """두 예시 설정은 장비 전용 항목(RF · PCV · O3) 말고 같은 항목을 가진다."""
    ka = {k for k in _keys(_load(os.path.join(ROOT, "config", "config.example.json"))) if not DEVICE_KEY.search(k)}
    kb = {k for k in _keys(_load(os.path.join(OTHER, "config", "config.example.json"))) if not DEVICE_KEY.search(k)}
    assert ka == kb, f"{ME} 에만: {sorted(ka - kb)} · {PAIR} 에만: {sorted(kb - ka)}"


def test_config_json_in_both_and_not_older_than_example():
    """config.json 은 두 폴더 모두 있거나 모두 없다. 있으면 자기 예시 설정의 항목을 모두 가진다."""
    have = {r: os.path.isfile(os.path.join(r, "config.json")) for r in (ROOT, OTHER)}
    if not any(have.values()):
        pytest.skip("config.json 이 두 폴더 모두 없다(두 프로그램 모두 예시 설정으로 뜬다)")
    missing = [os.path.basename(r) for r, ok in have.items() if not ok]
    assert not missing, (f"config.json 이 {missing} 에 없다 — 그쪽 프로그램은 예시 설정(내장 시뮬레이터)으로 뜬다. "
                         "그 폴더의 config/config.example.json 을 복사해 plc 등 필요한 값만 바꾼다")
    for r in (ROOT, OTHER):
        lack = sorted(_keys(_load(os.path.join(r, "config", "config.example.json")))
                      - _keys(_load(os.path.join(r, "config.json"))))
        assert not lack, (f"{os.path.basename(r)}/config.json 에 예시 설정의 항목이 없다: {lack} — "
                          "예시에 새 항목이 생기면 두 폴더의 config.json 에도 넣는다")
