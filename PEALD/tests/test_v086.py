"""v0.8.6 — 영어 라벨은 그대로, 마우스를 올리면 한국어 설명창 · 한국어 문장은 짧게.

① 설명 빠짐 없음 — terms.js 의 모든 라벨 키(data-t 가 쓰는 키 포함)에 설명창 사전(TIP) 이 있고, device.py 의 모든 인터락 ·
   입력 · 알람 · 밸브 · 보조 출력 · 시작 조건 자리에 한국어 이름(ko)과 설명이 있다.
② 문장 끝 — 서버(문자열 리터럴 — 독스트링 · 주석 제외)와 화면(js · html — 주석 제외)의 글이 '~습니다 · ~세요 · ~십시오' 로 끝나지
   않는다(예외는 EXCEPTIONS). 명령 이름(알림 · 로그)은 화면 단추 라벨과 같은 영어.
③ 브라우저 — 두 크기(960×1000 · 1600×840)에서 Valve Permit 램프에 올리면 '밸브 허가' 와 빠진 조건, 막힌 Start 에 올리면 막힌 이유,
   그래프 위에서는 설명창이 뜨지 않고, 설명창은 화면 가장자리에서 잘리지 않으며, 브라우저 title 말풍선이 남지 않는다.
★ 두 폴더 같은 파일이다(패키지 이름만 다르다).
"""
import ast
import json
import os
import re
import shutil
import subprocess
import threading

import pytest

from peald import addresses as A
from peald import device as DEV
from peald import paths
from peald import version
from peald.state import state

from conftest import free_port

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONT = os.path.join(ROOT, "frontend")
PKG = os.path.join(ROOT, "peald")
HANGUL = re.compile(r"[가-힣]")
POLITE = re.compile(r"(습니다|세요|십시오)(?![가-힣])")
# 예외 — 문장 끝 검사에서 뺄 글(없으면 비워 둔다)
EXCEPTIONS: set = set()


def test_version_is_at_least_086():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")[:3]) >= (0, 8, 6)


# ===================== ① 설명 빠짐 없음 =====================
def _node(js: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _terms():
    return _node("global.window = undefined; const T = require(%s);"
                 "console.log(JSON.stringify({dict: T.dict, tips: T.tips}));"
                 % json.dumps(os.path.join(FRONT, "js", "terms.js")))


def _front_sources():
    out = {}
    for dp, _dn, fn in os.walk(FRONT):
        for f in fn:
            if f.endswith((".js", ".html")):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, FRONT)] = open(p, encoding="utf-8").read()
    return out


def test_every_label_key_has_a_korean_tooltip():
    tt = _terms()
    d, tips = tt["dict"], tt["tips"]
    missing = [k for k in d if k not in tips or not HANGUL.search(tips[k][0])]
    assert not missing, missing
    assert not [k for k in tips if k not in d], "사전에 없는 설명 키"
    # data-t · data-tk · T('…') 로 쓰는 키는 모두 사전 · 설명에 있다
    used = set()
    for src in _front_sources().values():
        used |= set(re.findall(r'data-t[k]?="([a-zA-Z]+\.[A-Za-z0-9]+)"', src))
    assert used, "data-t 를 하나도 못 찾았다"
    assert not (used - set(tips)), sorted(used - set(tips))
    assert tips["b.pumpStart"] == ["펌핑 시작", "펌프 운전 후 IV-E 를 열어 배기"]


@pytest.mark.parametrize("name", ["INPUTS0", "INPUTS1", "INTERLOCKS", "ALARMS0", "ALARMS1", "VALVES", "AUX", "START_SLOTS"])
def test_device_names_have_korean_name_and_description(name):
    for d in getattr(DEV, name):
        key = d.get("tag") or d.get("label") or d.get("name")
        assert HANGUL.search(d.get("ko") or ""), (name, key, d.get("ko"))
        desc = d.get("desc") or d.get("why") or d.get("cause") or ""
        assert HANGUL.search(desc), (name, key, "설명 없음")
    il = {d["tag"]: d for d in DEV.INTERLOCKS}
    assert il["Valve Permit"]["ko"] == "밸브 허가" and il["Valve Permit"]["desc"] == "공정 밸브를 열어도 되는 조건"


def test_structure_sends_korean_names():
    state.cfg = {"heaters": [{"ch": 1, "name": DEV.HEATER_DEFAULT_NAMES[0], "enabled": True}],
                 "mfc": [{"no": 1, "name": DEV.MFC_DEFAULT_NAMES[0]}]}
    st = state.structure()
    assert all(v.get("ko") for v in st["valves"]) and all(i.get("ko") for i in st["interlocks"])
    assert all(a.get("ko") for a in st["alarm_defs"].values())
    assert DEV.HEATER_DEFAULT_NAMES[0] in st["names_ko"]["heater"] and DEV.MFC_DEFAULT_NAMES[0] in st["names_ko"]["mfc"]
    assert all(s.get("ko") for s in st["start_slots"])


# ===================== ② 문장 끝 =====================
def _py_strings(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            skip.update(range(n.value.lineno, n.value.end_lineno + 1))         # 독스트링 · 맨 문자열(설명)
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.lineno not in skip]


_JS_STR = re.compile(r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`""")


def _js_strings(src, html=False):
    out, i, n = [], 0, len(src)
    while i < n:
        if not html and src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            i = src.find("*/", i + 2) + 2
            continue
        if html and src.startswith("<!--", i):
            i = src.find("-->", i) + 3
            continue
        if src[i] in "'\"`":
            m = _JS_STR.match(src, i)
            if m:
                out.append(m.group(0)[1:-1])
                i = m.end()
                continue
        i += 1
    if html:
        out += re.findall(r">([^<>]*[가-힣][^<>]*)<", src)
    return out


def test_no_polite_endings_in_messages():
    bad = []
    for f in sorted(os.listdir(PKG)):
        if f.endswith(".py"):
            for ln, s in _py_strings(os.path.join(PKG, f)):
                if POLITE.search(s) and s not in EXCEPTIONS:
                    bad.append(f"{f}:{ln}: {s[:80]}")
    for rel, src in _front_sources().items():
        for s in _js_strings(src, rel.endswith(".html")):
            if POLITE.search(s) and s not in EXCEPTIONS:
                bad.append(f"{rel}: {s[:80]}")
    assert not bad, "\n".join(bad[:40])


def test_command_names_are_button_labels():
    assert A.CMD_NAMES[A.CMD_PUMP_START] == "Pumping Start" and A.CMD_NAMES[A.CMD_VENT] == "Vent"
    assert A.CMD_NAMES[A.CMD_ALL_CLOSE] == "Close All Valves" and A.CMD_NAMES[A.CMD_ALARM_RESET] == "Alarm Reset"
    assert A.CMD_NAMES[A.CMD_ABORT] == "Abort" and A.CMD_NAMES[A.CMD_PROCESS_START] == "Start"
    assert not [v for v in A.CMD_NAMES.values() if HANGUL.search(v)]


def test_short_example_sentences():
    """지시의 예 — 줄인 문장 그대로."""
    src = open(os.path.join(PKG, "commands.py"), encoding="utf-8").read()
    assert '"Valve Permit 없음 — 보내지 않음 (필요: " + " · ".join(miss) + ")"' in src
    link = open(os.path.join(PKG, "plclink.py"), encoding="utf-8").read()
    assert '"PLC가 수동 요청 해제 — "' in link and '". 필요하면 다시 조작"' in link
    proc = open(os.path.join(PKG, "process.py"), encoding="utf-8").read()
    assert '"레시피 선택 필요"' in proc and "레시피를 고르세요" not in proc


def test_no_browser_title_tooltips_left():
    """title 속성으로 넣던 설명은 설명창(data-tip)으로 — 그래프 캔버스(chart.js — 그래프의 기존 값 읽기 표시)만 예외."""
    left = []
    for rel, src in _front_sources().items():
        if rel.endswith(("chart.js", "pidcheck.js", "pid_model.js")):
            continue
        for m in re.finditer(r"""\btitle="|\.title\s*=[^=]|el\('title'""", src):
            line = src[: m.start()].count("\n") + 1
            text = src.splitlines()[line - 1].strip()
            if text.startswith(("//", "*", "/*")) or "d.title = dev.title" in text:
                continue
            if 'title="' not in text and re.search(r"\b(m|it|out\.\w+)\.title\s*=", text):
                continue                                    # 화면 모델의 title 필드(조작 창 제목 · 설명창 상태 줄) — DOM 이 아니다
            left.append(f"{rel}:{line}: {text[:90]}")
    assert not left, left


def test_terms_header_states_the_four_principles():
    head = open(os.path.join(FRONT, "js", "terms.js"), encoding="utf-8").read().split("(function (root)")[0]
    for word in ("라벨은 영어 그대로", "한국어 설명창", "지금 상태를 붙인다", "'~함 · ~됨 · ~없음 · ~필요 · ~중'"):
        assert word in head, word


# ===================== ③ 브라우저 =====================
@pytest.fixture
def served86(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    from peald.server import create_app, uvicorn_config
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": True, "sim_port": free_port(), "sim_speed": 50})
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    app = create_app(str(path), single_instance=False)
    port = free_port()
    server = uvicorn.Server(uvicorn_config(app, "127.0.0.1", port))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        yield port
    finally:
        server.should_exit = True
        th.join(10)
        state.link = None
        state.runner = None
        state.sim = None


_TIP_JS = """() => { const t = document.getElementById('tipbox');
  if (!t || t.hidden) return null; const r = t.getBoundingClientRect();
  return {text: t.innerText, l: r.left, t: r.top, r: r.right, b: r.bottom, vw: innerWidth, vh: innerHeight}; }"""


async def _hover(pg, sel):
    import asyncio
    box = await pg.locator(sel).first.bounding_box()
    assert box, sel
    await pg.mouse.move(2, 2)
    await asyncio.sleep(0.1)
    await pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await asyncio.sleep(0.25)
    early = await pg.evaluate(_TIP_JS)                      # 0.4 s 전에는 아직 없다
    await asyncio.sleep(0.45)
    return early, await pg.evaluate(_TIP_JS)


def _inside(tip):
    return tip["l"] >= 0 and tip["t"] >= 0 and tip["r"] <= tip["vw"] + 0.5 and tip["b"] <= tip["vh"] + 0.5


@pytest.mark.parametrize("size", [(960, 1000), (1600, 840)], ids=["narrow", "wide"])
async def test_browser_tooltips(served86, size):
    import asyncio
    port = served86
    pw_mod = pytest.importorskip("playwright.async_api")
    pw = await pw_mod.async_playwright().start()
    try:
        br = await pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        await pw.stop()
        pytest.skip(f"브라우저를 띄울 수 없음: {type(e).__name__}")
    try:
        pg = await br.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(f"http://127.0.0.1:{port}/")
        await pg.add_style_tag(content="#alarmModal { display: none !important; }")
        await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
        await pg.wait_for_function("() => ((core.state.live.manual || {}).valve_permit_missing || []).length > 0", timeout=10000)
        await asyncio.sleep(0.8)
        # Valve Permit 램프 — 한국어 이름 · 설명 · 지금 상태 · 빠진 조건(서버 valve_permit_missing)
        miss = await pg.evaluate("() => core.state.live.manual.valve_permit_missing")
        early, tip = await _hover(pg, '[data-lamp="ilk:Valve Permit"]')
        assert early is None, "0.4 s 전에 떴다"
        assert tip and tip["text"].startswith("밸브 허가\n공정 밸브를 열어도 되는 조건"), tip
        assert "빠진 조건: " + " · ".join(miss) in tip["text"] and _inside(tip), tip
        # 막힌 Start — 이름 · 설명 · 막힌 이유(시작 조건 미달 항목)
        assert await pg.evaluate("() => document.querySelector('.procbar [data-cmd=\"process_start\"]').disabled")
        _e, tip = await _hover(pg, '.procbar [data-cmd="process_start"]')
        assert tip and tip["text"].startswith("공정 시작\n") and "막힘: 시작 조건 미달" in tip["text"], tip
        assert "Valve Permit — " in tip["text"] and _inside(tip), tip
        # 가장자리(오른쪽 아래 Exit · 명령줄) — 잘리지 않는다
        for sel in ('[data-cmd="exit"]', '[data-cmd="pump_start"]'):
            _e, tip = await _hover(pg, sel)
            assert tip and _inside(tip), (sel, tip)
        _e, tip = await _hover(pg, '[data-cmd="pump_start"]')
        assert tip["text"].startswith("펌핑 시작\n펌프 운전 후 IV-E 를 열어 배기"), tip
        # 브라우저 title 말풍선이 남지 않는다(설명창과 겹쳐 뜨지 않게) — 그래프 캔버스만 예외
        titled = await pg.evaluate("() => [...document.querySelectorAll('[title]')].filter(e => e.tagName !== 'CANVAS')"
                                   ".map(e => e.outerHTML.slice(0, 80))")
        assert titled == [], titled
        assert await pg.evaluate("() => document.querySelectorAll('svg title').length") == 0
        # 그래프 위 — 뜨지 않는다(데이터 선을 가리지 않게)
        await pg.click('button.tab[data-tab="trend"]')
        await asyncio.sleep(0.6)
        _e, tip = await _hover(pg, 'canvas[data-chart="p"]')
        assert tip is None, tip
        # 그래프 칸 머리(라벨)에는 뜬다 · 스크롤 · 누르기면 닫힌다
        await pg.click('button.tab[data-tab="main"]')
        await asyncio.sleep(0.4)
        _e, tip = await _hover(pg, '[data-lamp="ilk:Valve Permit"]')
        assert tip
        await pg.mouse.down()
        await pg.mouse.up()
        assert await pg.evaluate(_TIP_JS) is None
        # 키보드 포커스로도 뜬다
        await pg.keyboard.press("Tab")
        await pg.evaluate("() => document.querySelector('[data-cmd=\"pump_start\"]').focus({focusVisible: true})")
        await asyncio.sleep(0.6)
        assert errors == [], errors
    finally:
        await br.close()
        await pw.stop()
