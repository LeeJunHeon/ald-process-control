"""v0.8.7 — 설명창 · 문구의 작은 문제 4 가지.

1. 설명창은 어떤 창(알람 창 · 확인 창 · 조작 창 · 조작판)보다 위에 뜬다. 알람 창의 알람 이름에도 Alarm/Log 탭과 같은 설명창.
2. 설명창 첫 줄은 한국어 이름 — 영어 글만 되풀이하는 설명창은 없다(상태 띠 · Process 칸 · MFC 칸 · 배관도 …).
   두 화면 크기 × 모든 탭에서 설명창이 붙은 요소에 모두 올려 본다.
3. 변환 흔적('~마기 필요' · '~말기 필요' 등)이 두 폴더 어디에도 없다.
4. 문장 안의 단추 이름은 화면 라벨과 같다('[새로]' → '[New]').
★ 두 폴더 같은 파일이다(패키지 이름만 다르다).
"""
import json
import os
import re
import threading

import pytest

from powderald import device as DEV
from powderald import paths
from powderald import version
from powderald.state import state

from conftest import free_port

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
FRONT = os.path.join(ROOT, "frontend")


def test_version_is_at_least_087():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")[:3]) >= (0, 8, 7)


# ===================== 1. 설명창은 모든 창 위 =====================
def test_tooltip_z_index_is_above_every_window():
    css = open(os.path.join(FRONT, "css", "style.css"), encoding="utf-8").read()
    tip = int(re.search(r"#tipbox \{[^}]*z-index: (\d+)", css).group(1))
    others = [int(z) for z in re.findall(r"z-index: (\d+)", css)]
    assert tip == max(others) and others.count(tip) == 1, (tip, sorted(set(others)))


def test_alarm_modal_names_get_the_same_tooltip_as_the_alarm_table():
    core = open(os.path.join(FRONT, "js", "core.js"), encoding="utf-8").read()
    body = core[core.index("function renderAlarmModal"):core.index("function renderAlarmModal") + 2000]
    assert "tipKo(df.ko || a.name, df.cause ? '원인: ' + df.cause : '')" in body
    assert "tipAttr(ck ? '조치: ' + ck : '')" in body
    assert "'<div class=\"am-check\">확인: ' + esc(ck)" in body             # 창 안 '확인: …' 줄은 그대로


# ===================== 3 · 4. 문구 =====================
def _text_files():
    for folder in ("POWDERALD", "POWDERALD"):
        base = os.path.join(REPO, folder)
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if x not in ("__pycache__", "data", "build", "dist", ".git")]
            for f in fn:
                if f.endswith((".py", ".js", ".html", ".md", ".json", ".css")):
                    yield os.path.join(dp, f)
    yield os.path.join(REPO, "README.md")


def test_no_conversion_leftovers():
    bad = []
    for p in _text_files():
        if not os.path.isfile(p) or p.endswith("test_v087.py"):
            continue
        txt = open(p, encoding="utf-8").read()
        # '~하지 마세요' · '~치세요' · '~로 하세요' 를 기계적으로 바꾼 흔적('고치기 필요' 는 바른 말이라 빼고 찾는다)
        for word in re.findall(r"마기 필요|말기 필요|창으로 필요|(?<![가-힣])치기 필요", txt):
            bad.append(f"{os.path.relpath(p, REPO)}: {word}")
    assert not bad, bad


def test_plc_ctl_force_help():
    src = open(os.path.join(ROOT, "test", "plc_ctl.py"), encoding="utf-8").read()
    assert "(쓰지 말 것 — 다른 장비 PLC 일 수 있음)" in src


def test_sentences_name_buttons_by_their_labels():
    rc = open(os.path.join(FRONT, "js", "views", "recipe.js"), encoding="utf-8").read()
    assert "<b>[New]</b>" in rc and "[새로]" not in rc
    st = open(os.path.join(FRONT, "js", "views", "setup.js"), encoding="utf-8").read()
    assert "[Review & Save] 누르기 필요" in st and "저장 검토 누르기" not in st
    py = open(os.path.join(ROOT, "powderald", "process.py"), encoding="utf-8").read()
    assert "[O3 Line ON]" in py and "[O3 Line 켜기]" not in py


# ===================== 브라우저 =====================
@pytest.fixture
def served87(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    from powderald.server import create_app, uvicorn_config
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


async def _browser(pw_mod):
    pw = await pw_mod.async_playwright().start()
    try:
        return pw, await pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        await pw.stop()
        pytest.skip(f"브라우저를 띄울 수 없음: {type(e).__name__}")


_TIP = """() => { const t = document.getElementById('tipbox');
  if (!t || t.hidden) return null; const r = t.getBoundingClientRect();
  return {text: t.innerText, l: r.left, t: r.top, r: r.right, b: r.bottom, vw: innerWidth, vh: innerHeight}; }"""


async def test_browser_tooltip_is_above_the_alarm_window(served87):
    import asyncio
    port = served87
    pw, br = await _browser(pytest.importorskip("playwright.async_api"))
    try:
        pg = await br.new_page(viewport={"width": 960, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(f"http://127.0.0.1:{port}/")
        await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
        state.sim.set_fault("leak", True)                       # 중대 알람(Gas Leak) → 알람 창이 뜬다
        await pg.wait_for_function("() => !document.getElementById('alarmModal').hidden", timeout=10000)
        await asyncio.sleep(0.5)
        # Alarm Reset 단추 — 설명창이 보이고 알람 창보다 위
        box = await pg.locator('#alarmModal [data-am="reset"]').bounding_box()
        await pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await asyncio.sleep(0.7)
        tip = await pg.evaluate(_TIP)
        assert tip and tip["text"].startswith("알람 리셋\n"), tip
        top = await pg.evaluate("""() => {
          const t = document.getElementById('tipbox'), m = document.getElementById('alarmModal');
          const zt = Number(getComputedStyle(t).zIndex), zm = Number(getComputedStyle(m).zIndex);
          const r = t.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
          t.style.pointerEvents = 'auto';                       // 맞히기 검사 동안만(설명창은 보통 누르기를 통과시킨다)
          const hit = document.elementFromPoint(x, y);
          t.style.pointerEvents = '';
          return { zt, zm, onTop: !!hit && (hit === t || t.contains(hit)) };
        }""")
        assert top["zt"] > top["zm"] and top["onTop"], top
        if os.environ.get("V087_SHOT"):                         # 캡처(보고용) — 환경 변수로 경로를 줄 때만
            await pg.screenshot(path=os.environ["V087_SHOT"])
        # 알람 이름 — 한국어 알람 이름 · 원인 · 조치
        leak = next(d for d in DEV.ALARMS0 if d["name"] == "Gas Leak")
        await pg.mouse.move(2, 2)
        await asyncio.sleep(0.2)
        nb = await pg.locator('#alarmModal .am-name').first.bounding_box()
        await pg.mouse.move(nb["x"] + 4, nb["y"] + nb["height"] / 2)
        await asyncio.sleep(0.7)
        tip = await pg.evaluate(_TIP)
        assert tip and tip["text"].split("\n")[0] == leak["ko"] == "가스 누출", tip
        assert "원인: " + leak["cause"] in tip["text"] and "조치: " + leak["check"] in tip["text"], tip
        assert await pg.locator('#alarmModal .am-check').count() >= 1        # 창 안 '확인: …' 줄은 그대로
        state.sim.set_fault("leak", False)
        assert errors == [], errors
    finally:
        await br.close()
        await pw.stop()


_POINTS = """() => {
  const out = [];
  document.querySelectorAll('[data-t],[data-tk],[data-tip],[data-tipn]').forEach(e => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const x = Math.min(r.right - 1, Math.max(r.left + 1, r.left + r.width / 2));
    const y = Math.min(r.bottom - 1, Math.max(r.top + 1, r.top + r.height / 2));
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return;
    const hit = document.elementFromPoint(x, y);
    if (!hit || !(hit === e || e.contains(hit))) return;
    out.push({ x, y, html: e.outerHTML.slice(0, 100) });
  });
  return out;
}"""


@pytest.mark.parametrize("size", [(960, 1000), (1600, 840)], ids=["narrow", "wide"])
async def test_browser_every_tooltip_starts_with_a_korean_name(served87, size):
    """모든 탭에서 설명창이 붙은 보이는 요소에 모두 올린다(배관도 요소 포함) — 첫 줄 한글 · 'undefined' · '[object' 없음 · 화면 안."""
    import asyncio
    port = served87
    pw, br = await _browser(pytest.importorskip("playwright.async_api"))
    try:
        pg = await br.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(f"http://127.0.0.1:{port}/")
        await pg.add_style_tag(content="#alarmModal { display: none !important; }")
        await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
        await pg.evaluate("() => { core.tipDelay = 0; }")      # 머무름만 줄여 빨리 훑는다(설명창 글 · 자리는 그대로)
        await asyncio.sleep(1.0)
        tabs = await pg.evaluate("() => [...document.querySelectorAll('button.tab[data-tab]')].map(b => b.dataset.tab)")
        assert "pid" in tabs and len(tabs) == 7, tabs
        bad, seen, shown = [], 0, 0
        for tab in tabs:
            await pg.mouse.move(1, 1)
            await pg.click(f'button.tab[data-tab="{tab}"]')
            await asyncio.sleep(0.6)
            pts = await pg.evaluate(_POINTS)
            assert pts, tab
            for pt in pts:
                seen += 1
                await pg.mouse.move(pt["x"], pt["y"])
                await asyncio.sleep(0.04)
                tip = await pg.evaluate(_TIP)
                if not tip:
                    continue
                shown += 1
                first = tip["text"].split("\n")[0]
                why = []
                if not re.search(r"[가-힣]", first):
                    why.append("첫 줄 한글 없음")
                if "undefined" in tip["text"] or "[object" in tip["text"]:
                    why.append("undefined/[object")
                if tip["l"] < 0 or tip["t"] < 0 or tip["r"] > tip["vw"] + 0.5 or tip["b"] > tip["vh"] + 0.5:
                    why.append("화면 밖")
                if why:
                    bad.append(f"{tab}: {why} {tip['text'][:60]!r} {pt['html']}")
        assert not bad, "\n".join(bad[:30])
        assert shown > 200 and shown >= seen * 0.9, (seen, shown)
        assert errors == [], errors
    finally:
        await br.close()
        await pw.stop()
