"""v0.8.8 — 설명창 안전망이 가린 설명창 되살리기.

v0.8.7 의 안전망(첫 줄에 한글이 없으면 설명창을 띄우지 않음)이 '잘린 글 전체를 보여 주는' 설명창을 조용히 지웠다 —
한국어 이름 없이 상태 줄(data-tip)만 단 곳에 글이 영어 · 숫자뿐이면 첫 줄에 한글이 없다.
1. 한글이 없을 수 있는 글(사용자 글 · 파일 이름 · 계열 · 밸브 이름 · PLC/설정의 영어 이름)을 다는 자리는 한국어 이름을 스스로 갖는다
   (Data Log 작업자 · 시료 · 메모 · 결과, Trend '+N', Recipe 밸브 칸, 상단 띠 ALARM, 시퀀서 칩, Recent Events, 누설률 점검 사유 …).
2. 안전망은 남기되 걸린 횟수 · 마지막 요소를 센다(core.tipDropped) — 정상 화면에서는 0.
3. 라벨 글자로 찾은 키는 지금 글자로 다시 찾는다(data-tka) — 'No Alarm' 의 키가 알람 중에도 남지 않게.
★ 두 폴더 같은 파일이다(패키지 이름만 다르다).
"""
import asyncio
import json
import os
import re
import threading
from contextlib import contextmanager

import pytest

from powderald import device as DEV
from powderald import paths
from powderald import recipe as R
from powderald import version
from powderald.state import state

from conftest import free_port

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "frontend", "js")

OPERATOR = "LJH"
SAMPLE = "Si-wafer-A-0001-long-sample-name"
MEMO = ("Run 0123 memo text only English and digits " * 8)[:199] + "Z"      # 200 자 · 영어 · 숫자뿐


def _src(*parts):
    with open(os.path.join(JS, *parts), encoding="utf-8") as f:
        return f.read()


def test_version_is_at_least_088():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")[:3]) >= (0, 8, 8)


# ===================== 소스 — 안전망 세기 · 라벨 키 · 한국어 이름 =====================
def test_core_counts_what_the_safety_net_drops():
    core = _src("core.js")
    assert "if (out.length && !/[가-힣]/.test(out[0][1])) { tipDropOne(e, out[0][1]); return []; }" in core
    assert "get tipDropped() { return { count: tipDrop.count, first: tipDrop.first, html: tipDrop.html }; }" in core
    assert "w.console.warn('설명창 안전망: 첫 줄에 한글 없음 — '" in core


def test_label_key_follows_the_current_text():
    core = _src("core.js")
    assert "x.setAttribute('data-tk', keyOfLabel(x))" not in core          # 예전: 한 번 찾은 키가 굳었다
    assert "if (x.getAttribute('data-tka') !== lk) x.setAttribute('data-tka', lk);" in core
    assert "(e.hasAttribute('data-tka') ? keyOfLabel(e) : '')" in core


def test_free_text_tip_sites_have_korean_names():
    dl = _src("views", "datalog.js")
    assert "dl-who\"' + core.tipKo('작업자 · 시료'" in dl and "dl-memo\"' + core.tipKo('메모'" in dl
    assert "['공정 결과', '이 공정의 끝 결과']" in dl
    assert "core.tipSet(more, '가린 범례', '칸이 좁아 가려진 계열', hid.join" in _src("views", "trend.js")
    rc = _src("views", "recipe.js")
    assert "role=\"checkbox\"' + valveTip(v) + ' data-rcv=" in rc
    assert "w.UI.fpKo((core.state || {}).structure, 'valve:' + tag)" in rc            # 밸브 띠 · 조작 창과 같은 출처
    assert "core.tipSet(el('stAlm'), '현재 알람', TIPS.alm, m.alm.title || '');" in _src("views", "strip.js")
    main = _src("views", "main.js")
    assert "['시퀀서 상태', 'PLC 시퀀서 단계 — 장비 상태와 다를 때만 보임']" in main and "['PC 트립'," in main
    assert "core.tipSet(mm, e ? '이벤트' : '', '', e ? e.msg : '');" in _src("views", "panels.js")
    assert "core.tipKo('점검 사유', '') + core.tipAttr(r.Reason)" in _src("views", "setup.js")
    assert "df.ko || '알람'" in _src("core.js") and _src("views", "alarm.js").count("core.tipKo(df.ko || '알람'") == 2


# ===================== 브라우저 =====================
@contextmanager
def _serve(tmp_path, tweak=None):
    uvicorn = pytest.importorskip("uvicorn")
    from powderald.server import create_app, uvicorn_config
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": True, "sim_port": free_port(), "sim_speed": 50})
    if tweak:
        tweak(raw)
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


async def _browser():
    pw_mod = pytest.importorskip("playwright.async_api")
    pw = await pw_mod.async_playwright().start()
    try:
        return pw, await pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        await pw.stop()
        pytest.skip(f"브라우저를 띄울 수 없음: {type(e).__name__}")


async def _page(br, port, size, errors, warns):
    pg = await br.new_page(viewport={"width": size[0], "height": size[1]})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "warning" and "설명창 안전망" in m.text and warns.append(m.text))
    await pg.goto(f"http://127.0.0.1:{port}/")
    await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
    await pg.evaluate("() => { core.tipDelay = 0; }")       # 머무름만 줄인다(설명창 글 · 자리는 그대로)
    await asyncio.sleep(0.8)
    return pg


_TIP = """() => { const t = document.getElementById('tipbox');
  if (!t || t.hidden) return null; const r = t.getBoundingClientRect();
  return {text: t.innerText, l: r.left, t: r.top, r: r.right, b: r.bottom, vw: innerWidth, vh: innerHeight}; }"""

_DROPS = "() => core.tipDropped"


async def _hover(pg, locator, wait=0.15):
    box = await locator.bounding_box()
    assert box, locator
    await pg.mouse.move(1, 1)
    await asyncio.sleep(0.05)
    await pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await asyncio.sleep(wait)
    return await pg.evaluate(_TIP)


def _recipe(name, repeat=8):
    rec = R.empty_recipe(name)
    b = R.empty_block("Block A")
    b["repeat"] = repeat
    b["steps"] = [{"name": "Dose", "time_ms": 300, "valves": [DEV.RECIPE_VALVES[0]], "pause_ok": False},
                  {"name": "Purge", "time_ms": 300, "valves": [], "pause_ok": True}]
    if DEV.HAS_RF:
        for s in b["steps"]:
            s["rf"] = False
    rec["blocks"] = [b]
    return rec


async def _save_recipe(pg, name, rec):
    await pg.evaluate("([n, r]) => app.send('recipe_save', { name: n, recipe: r })", [name, rec])
    await pg.wait_for_function("(n) => ((core.state || {}).recipes || []).some(r => r.name === n)", arg=name, timeout=10000)


_POINTS = """() => {
  const out = [];
  document.querySelectorAll('[data-t],[data-tk],[data-tka],[data-tip],[data-tipn]').forEach(e => {
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


async def _sweep(pg, on_tab=None):
    """모든 탭에서 설명창이 붙은 보이는 요소에 모두 올린다 — (본 수, 뜬 수, 문제 목록)."""
    tabs = await pg.evaluate("() => [...document.querySelectorAll('button.tab[data-tab]')].map(b => b.dataset.tab)")
    assert "pid" in tabs and len(tabs) == 7, tabs
    bad, seen, shown = [], 0, 0
    for tab in tabs:
        await pg.mouse.move(1, 1)
        await pg.click(f'button.tab[data-tab="{tab}"]')
        await asyncio.sleep(0.6)
        if on_tab:
            await on_tab(pg, tab)
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
    return seen, shown, bad


async def test_browser_safety_net_counts_what_it_drops(tmp_path):
    """안전망이 조용히 버리지 않는다 — 걸리면 횟수 · 마지막 첫 줄 · 요소를 세고 콘솔에 알린다(양성 대조)."""
    with _serve(tmp_path) as port:
        pw, br = await _browser()
        try:
            errors, warns = [], []
            pg = await _page(br, port, (960, 1000), errors, warns)
            assert (await pg.evaluate(_DROPS))["count"] == 0
            await pg.evaluate("""() => { const e = document.createElement('span');
              e.id = 'v088probe'; e.textContent = 'XYZ'; e.setAttribute('data-tip', 'English only 123');
              e.style.cssText = 'position:fixed;left:300px;top:300px;padding:10px;z-index:50;background:#fff';
              document.body.appendChild(e); }""")
            tip = await _hover(pg, pg.locator("#v088probe"))
            assert tip is None                                            # 안전망은 그대로(띄우지 않음)
            d = await pg.evaluate(_DROPS)
            assert d["count"] >= 1 and d["first"] == "English only 123" and 'id="v088probe"' in d["html"], d
            assert await pg.evaluate("() => core.tipDroppedEl && core.tipDroppedEl.id") == "v088probe"
            assert warns and "English only 123" in warns[0], warns
            assert errors == [], errors
        finally:
            await br.close()
            await pw.stop()


async def test_browser_run_info_tooltips_and_full_sweep(tmp_path):
    """a. 영어 · 숫자뿐인 작업자 · 긴 시료 · 200 자 메모로 공정을 한 번 돌린다(시작 확인 창에 입력) →
          Data Log 목록 .dl-who · 상세 .dl-memo 의 설명창(첫 줄 한국어 이름 + 전체 글). 공정 중 시퀀서 칩도.
       d. 그 기록이 있는 상태에서 두 화면 크기 × 7 탭 전수 — 안전망에 걸린 횟수 0(첫 줄 한글 · 화면 안)."""
    with _serve(tmp_path) as port:
        pw, br = await _browser()
        try:
            errors, warns = [], []
            pg = await _page(br, port, (960, 1000), errors, warns)
            await _save_recipe(pg, "V088 Run", _recipe("V088 Run"))
            await pg.evaluate("() => app.send('recipe_select', { name: 'V088 Run' })")
            await pg.evaluate("() => app.send('pump_start')")
            if DEV.HAS_O3:                                        # Powder: O3 Permit 도 시작 조건(O3 Line ON — 두 단계)
                await pg.evaluate("() => { app.send('manual_unlock', { on: true }); app.send('manual_o3', { action: 'on', value: 100 }); }")
            await pg.wait_for_function("() => ((core.state.live || {}).process || {}).can_start", timeout=90000)
            await pg.click('button.tab[data-tab="main"]')
            await asyncio.sleep(0.4)
            await pg.locator('.procbar [data-cmd="process_start"]:visible').first.click()
            await pg.wait_for_selector('[data-runinfo="operator"]', state="visible")
            await pg.fill('[data-runinfo="operator"]', OPERATOR)
            await pg.fill('[data-runinfo="sample"]', SAMPLE)
            await pg.fill('[data-runinfo="memo"]', MEMO)
            await pg.click('[data-cf="ok"]')
            await pg.wait_for_function("() => ((core.state.live || {}).process || {}).running", timeout=90000)
            # 공정 중 — 시퀀서 칩(장비 상태와 다를 때만 보임 · 이름은 영어)의 설명창 첫 줄은 '시퀀서 상태'
            seq = None
            for _ in range(40):
                chip = pg.locator('[data-bind="pqSeq"] .chip:visible')
                if await chip.count():
                    seq = await _hover(pg, chip.first, 0.1)
                    if seq:
                        break
                await asyncio.sleep(0.1)
            assert seq and seq["text"].split("\n")[0] == "시퀀서 상태", seq
            await pg.wait_for_function(
                "() => { const p = (core.state.live || {}).process || {}; return !p.running && p.last_result; }", timeout=120000)
            # 데이터 로그 파일이 닫힐 때까지(공정 끝 뒤 꼬리 몇 초)
            item = None
            for _ in range(120):
                items = await pg.evaluate("async () => (await (await fetch('api/datalog/list')).json()).items || []")
                item = next((i for i in items if i.get("operator") == OPERATOR and not i.get("writing")), None)
                if item:
                    break
                await asyncio.sleep(0.25)
            assert item and item["sample"] == SAMPLE and item["memo"] == MEMO, item

            await pg.click('button.tab[data-tab="datalog"]')
            who = pg.locator(".dl-item .dl-who").first
            await who.wait_for(state="visible", timeout=10000)
            tip = await _hover(pg, who)
            assert tip and tip["text"].split("\n")[0] == "작업자 · 시료", tip
            assert f"{OPERATOR} · {SAMPLE}" in tip["text"], tip
            await pg.locator(".dl-item").first.click()
            memo = pg.locator(".dl-memo").first
            await memo.wait_for(state="visible", timeout=10000)
            tip = await _hover(pg, memo)
            assert tip and tip["text"].split("\n")[0] == "메모", tip
            assert MEMO in tip["text"].replace("\n", ""), tip
            assert (await pg.evaluate(_DROPS))["count"] == 0
            await pg.close()

            # d. 전수 — 두 크기 × 7 탭(Data Log 탭은 그 기록을 열고)
            async def open_record(p, tab):
                if tab == "datalog":
                    await p.locator(".dl-item").first.click()
                    await p.locator(".dl-memo").first.wait_for(state="visible", timeout=10000)
                    await asyncio.sleep(0.3)
            for size in ((960, 1000), (1600, 840)):
                pg = await _page(br, port, size, errors, warns)
                seen, shown, bad = await _sweep(pg, open_record)
                drops = await pg.evaluate(_DROPS)
                assert drops["count"] == 0, (size, drops)
                assert not bad, "\n".join(bad[:30])
                assert shown > 200 and shown >= seen * 0.9, (size, seen, shown)
                await pg.close()
            assert warns == [], warns
            assert errors == [], errors
        finally:
            await br.close()
            await pw.stop()


async def test_browser_trend_legend_more_chip(tmp_path):
    """b. Trend 범례를 넘치게(히터 채널 12 개를 모두 켬) → '+N' 칩. 올리면 가린 계열 이름이 모두 나온다."""
    def twelve(raw):
        for h in raw["heaters"]:
            if not h.get("enabled"):
                h.update({"enabled": True, "max_c": 200, "default_sv": 50, "name": "Spare Zone " + str(h["ch"])})
    with _serve(tmp_path, twelve) as port:
        pw, br = await _browser()
        try:
            errors, warns = [], []
            pg = await _page(br, port, (960, 1000), errors, warns)
            await pg.click('button.tab[data-tab="trend"]')
            more = pg.locator(".legend-inline .more").first
            await more.wait_for(state="visible", timeout=15000)
            hidden = await pg.evaluate("""() => { const m = document.querySelector('.legend-inline .more');
              return [...m.parentElement.querySelectorAll('span:not(.more)')].filter(e => e.hidden).map(e => e.textContent); }""")
            assert hidden and (await more.text_content()) == f"+{len(hidden)}", hidden
            tip = await _hover(pg, more)
            assert tip, "'+N' 칩에 설명창이 없다"
            lines = tip["text"].split("\n")
            assert lines[0] == "가린 범례" and lines[1] == "칸이 좁아 가려진 계열", lines
            assert all(nm in lines for nm in hidden), (hidden, lines)
            assert (await pg.evaluate(_DROPS))["count"] == 0
            assert errors == [] and warns == [], (errors, warns)
        finally:
            await br.close()
            await pw.stop()


async def test_browser_recipe_valve_cells_name_the_valve(tmp_path):
    """c. Recipe 스텝 표의 밸브 체크 칸 — 설명창 첫 줄은 그 밸브의 한국어 이름(장비 구조 — 밸브 띠 · 조작 창과 같은 출처)."""
    ko = {v["tag"]: (v["ko"], v.get("desc", "")) for v in DEV.VALVES}
    with _serve(tmp_path) as port:
        pw, br = await _browser()
        try:
            errors, warns = [], []
            pg = await _page(br, port, (960, 1000), errors, warns)
            await _save_recipe(pg, "V088 Valves", _recipe("V088 Valves", 1))
            await pg.click('button.tab[data-tab="recipe"]')
            await pg.locator('[data-rcopen="V088 Valves"]').first.click()
            cells = pg.locator('tr[data-rcstep="1"] [data-rcv]')
            await cells.first.wait_for(state="visible", timeout=10000)
            n = await cells.count()
            assert n == len(DEV.RECIPE_VALVES), n
            for i in range(n):
                c = cells.nth(i)
                tag = await c.get_attribute("data-rcv")
                tip = await _hover(pg, c)
                assert tip, tag
                lines = tip["text"].split("\n")
                assert lines[0] == ko[tag][0] and re.search(r"[가-힣]", lines[0]), (tag, lines)
                assert (not ko[tag][1] or lines[1] == ko[tag][1]) and tag in tip["text"], (tag, lines)
            assert (await pg.evaluate(_DROPS))["count"] == 0
            assert errors == [] and warns == [], (errors, warns)
        finally:
            await br.close()
            await pw.stop()


async def test_browser_alarm_strip_names_the_alarm_cell(tmp_path):
    """상단 띠 ALARM — 알람 줄('Critical · Gas Leak · …')은 영어라 예전에는 안전망에 걸렸다. 첫 줄은 '현재 알람'.
    값 글자('No Alarm' → '1 Critical')에서 찾은 라벨 키가 남아 알람 중에 '알람 없음' 이 뜨지 않는다."""
    with _serve(tmp_path) as port:
        pw, br = await _browser()
        try:
            errors, warns = [], []
            pg = await _page(br, port, (960, 1000), errors, warns)
            kpi = pg.locator('[data-bind="stAlmText"]')
            tip = await _hover(pg, kpi)
            assert tip and tip["text"].split("\n")[0] == "알람 없음", tip      # 'No Alarm' 라벨의 설명창
            state.sim.set_fault("leak", True)
            await pg.wait_for_function("() => !document.getElementById('alarmModal').hidden", timeout=10000)
            await asyncio.sleep(2.0)
            for _ in range(10):                                   # 알람 창을 닫는다(새 알람 코드면 다시 뜬다)
                if await pg.evaluate("() => document.getElementById('alarmModal').hidden"):
                    break
                await pg.click('#alarmModal [data-am="ack"]')
                await asyncio.sleep(0.3)
                await pg.click('#alarmModal [data-am="close"]')
                await asyncio.sleep(1.0)
            leak = next(d for d in DEV.ALARMS0 if d["name"] == "Gas Leak")
            for target in (kpi, pg.locator('[data-bind="stAlmSub"]')):
                tip = await _hover(pg, target)
                assert tip, target
                lines = tip["text"].split("\n")
                assert lines[0] == "현재 알람" and any(leak["name"] in x and "Critical" in x for x in lines), lines
                assert "알람 없음" not in tip["text"], lines
            assert (await pg.evaluate(_DROPS))["count"] == 0
            state.sim.set_fault("leak", False)
            assert errors == [] and warns == [], (errors, warns)
        finally:
            await br.close()
            await pw.stop()
