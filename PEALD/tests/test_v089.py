"""v0.8.9 — Data Log 목록 갱신 · PLC Check 칩 출처 · Python 3.12 시험 뒷정리 · 문구.

1. Data Log 목록은 서버를 따른다 — 탭에 들어올 때 · 기록이 시작되거나 끝날 때(live 의 datalog.active · file) · [Refresh].
   주기 폴링 없음. 다시 읽어도 고른 기록 · 날짜 · 글 필터 · 그래프 확대 구간은 그대로. 열어 둔 '기록 중' 기록이 끝나면 상세도.
2. Recipe 탭 PLC Check 칩은 Operation 탭 Table 칩과 같은 실시간 상태(live 의 D00029)를 따른다. 올리기가 직접 읽은
   D00028 · D00029 는 상태 사본에 바로 얹고, 그보다 먼저 보낸 주기 읽기가 늦게 끝나도 덮지 않는다(PlcLink.read_fresh).
   올리는 동안 '지금 PLC' 요약 되읽기는 기다린다.
3. 시험이 세운 asyncio 서버는 핸들러가 writer 를 닫고 close_server 로 정리한다(Python 3.12 의 wait_closed()).
4. 설명창 '서 있는 알람' → '떠 있는 알람'.
★ 두 폴더 같은 파일이다(패키지 이름만 다르다).
"""
import asyncio
import glob
import os
import time

import pytest

from peald import addresses as A
from peald import device as DEV
from peald import recipe as R
from peald import version
from peald.convert import Converters
from peald.state import state

from test_v088 import _browser, _page, _recipe, _save_recipe, _serve        # 같은 브라우저 도구(v0.8.8)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
JS = os.path.join(ROOT, "frontend", "js")


def _src(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def test_version_is_at_least_089():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")[:3]) >= (0, 8, 9)


# ===================== 4. 문구 =====================
def test_floating_alarm_wording():
    terms = _src(JS, "terms.js")
    assert terms.count("떠 있는 알람") == 3 and "서 있는 알람" not in terms


# ===================== 1 · 2. 소스 =====================
def test_datalog_list_follows_the_server_without_polling():
    dl = _src(JS, "views", "datalog.js")
    assert "if (!items.length) refresh();" not in dl                     # 예전: 목록이 비었을 때만 읽었다
    assert "core.register('datalog', { render: render, update: update });" in dl
    assert "var sig = (dl.active ? '1' : '0') + '|' + (dl.file || '');" in dl
    assert "setInterval" not in dl                                       # 주기 폴링 없음
    assert "open(cur, true);" in dl and "if (!keep || name !== cur) { zoom = null; offset = 0; }" in dl


def test_plc_check_chip_has_one_source():
    rc = _src(JS, "views", "recipe.js")
    assert "core.chip(p.plc_ok ?" not in rc                              # 예전: 되읽기 요약의 그때 값
    assert "core.chip(okNow ? 'PLC Check OK' : '✕ PLC Check NG', okNow ? 'ok' : 'warn')" in rc
    assert "if (seq && seq.recipe_ok != null && core.plcOk()) lastPlcOk = !!seq.recipe_ok;" in rc
    lk = _src(ROOT, "peald", "plclink.py")
    assert lk.count("regs = await self.read_fresh(A.D_RECIPE_SUM_PLC, 2)") == 2
    assert "if t0 < f[0]:" in lk and "async with self._rcp_lock:" in lk
    pr = _src(ROOT, "peald", "process.py")
    assert pr.count("head = await link.read_fresh(A.D_RECIPE_SUM_PLC, 2)") == 2


# ===================== 3. 시험 서버 뒷정리 =====================
def test_test_servers_close_their_connections():
    """시험 안에서 asyncio 서버를 세우는 파일은 모두 close_server 로 정리한다(남은 연결을 닫고 wait_closed)."""
    conf = _src(ROOT, "tests", "conftest.py")
    assert "await asyncio.wait_for(srv.wait_closed(), timeout)" in conf
    bad = []
    for p in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py"))):
        if p.endswith("test_v089.py"):
            continue
        txt = _src(p)
        if "asyncio.start_server(" in txt and "close_server(" not in txt:
            bad.append(os.path.basename(p))
    assert not bad, bad
    assert "writer.close()                  # ★ v0.8.9" in _src(ROOT, "tests", "test_modbus.py")


def test_readme_lists_checked_python_versions():
    for p in (os.path.join(ROOT, "README.md"), os.path.join(REPO, "README.md")):
        if os.path.isfile(p):
            assert "Python 3.12 · 3.13" in _src(p), p


# ===================== 2. 직접 읽은 값이 늦은 주기 읽기에 지지 않는다 =====================
async def test_fresh_read_survives_a_slow_periodic_read(link):
    """주기 읽기를 늦춰(읽은 뒤 0.6 s 지나서 사본에 넣게) 경합을 확실히 만든다 — 새로 켠 PLC(표 없음)에 올린 뒤
    상태 사본의 D00029 가 한 번도 0 으로 돌아가지 않는다. 그 뒤 PLC 값이 바뀌면 다음 주기 읽기가 따른다."""
    lk, s, cfg = link
    assert s.reg[A.D_RECIPE_OK] == 0                                    # 표 없음
    orig = lk.client.read_holding

    async def slow(addr, count):
        regs = await orig(addr, count)
        if addr == A.STATUS_BASE and count == A.STATUS_COUNT:
            await asyncio.sleep(0.6)
        return regs
    lk.client.read_holding = slow
    try:
        rec = R.empty_recipe("v089")
        b = R.empty_block("b1")
        b["steps"] = [{"name": "s", "time_ms": 200, "valves": [], "pause_ok": True}]
        if DEV.HAS_RF:
            b["steps"][0]["rf"] = False
        rec["blocks"] = [b]
        tbl = R.to_plc_words(cfg, Converters(cfg), rec)
        ok, why = await lk.upload_recipe(tbl["words"], tbl["checksum"])
        assert ok, why
        seen = []
        end = time.monotonic() + 2.5
        while time.monotonic() < end:
            seen.append(lk.status[A.D_RECIPE_OK])
            await asyncio.sleep(0.02)
        assert set(seen) == {1}, seen
        # PLC 값이 바뀌면(표를 지움) 직접 읽기 뒤에 보낸 주기 읽기가 그대로 따른다 — 얹은 값이 남아 있지 않다
        s.write(A.RCP_SUM_BASE, [0] * A.RCP_AREA_COUNT)
        end = time.monotonic() + 5
        while time.monotonic() < end and lk.status[A.D_RECIPE_OK] != 0:
            await asyncio.sleep(0.05)
        assert lk.status[A.D_RECIPE_OK] == 0
    finally:
        lk.client.read_holding = orig


# ===================== 브라우저 =====================
async def _ready_to_start(pg, name):
    await pg.evaluate("(n) => app.send('recipe_select', { name: n })", name)       # 시작 조건 'Recipe Check'
    await pg.evaluate("() => app.send('pump_start')")
    if DEV.HAS_O3:                                        # Powder: O3 Permit 도 시작 조건(O3 Line ON — 두 단계)
        await pg.evaluate("() => { app.send('manual_unlock', { on: true }); app.send('manual_o3', { action: 'on', value: 100 }); }")
    await pg.wait_for_function("() => ((core.state.live || {}).process || {}).can_start", timeout=90000)


async def _run_once(pg, name, operator):
    """공정을 한 번 — 시작부터 끝(running 이 꺼지고 끝 결과)까지."""
    await pg.evaluate("([n, o]) => app.send('process_start', { name: n, operator: o })", [name, operator])
    await pg.wait_for_function("() => ((core.state.live || {}).process || {}).running", timeout=90000)
    await pg.wait_for_function(
        "() => { const p = (core.state.live || {}).process || {}; return !p.running && p.last_result; }", timeout=120000)


async def _api_items(pg):
    return await pg.evaluate("async () => (await (await fetch('api/datalog/list')).json()).items || []")


async def _log_closed(pg):
    await pg.wait_for_function("() => !(((core.state.live || {}).datalog) || {}).active", timeout=30000)


_FIRST = "() => { const e = document.querySelector('[data-bind=dlList] .dl-item'); return e ? [e.dataset.dlname, (e.querySelector('.chip') || {}).textContent || ''] : null; }"
_META = "() => ((document.querySelector('[data-bind=dlMeta] .chip') || {}).textContent || '')"


async def _log_page(port, br, errors, warns):
    pg = await _page(br, port, (960, 1000), errors, warns)
    await _save_recipe(pg, "V089 Log", _recipe("V089 Log", 2))
    await _ready_to_start(pg, "V089 Log")
    return pg


async def test_browser_datalog_new_record_appears_without_refresh(tmp_path):
    """a. Data Log 탭을 연 뒤 다른 탭에서 공정을 돌리고 끝나고 10 s 뒤 돌아온다 → [Refresh] 없이 새 기록이 목록 맨 위.
       c. 목록이 다시 읽혀도 날짜 · 글 필터와 고른 기록이 그대로."""
    with _serve(tmp_path) as port:
        pw, br = await _browser()
        try:
            errors, warns = [], []
            pg = await _log_page(port, br, errors, warns)
            await _run_once(pg, "V089 Log", "A1")
            await _log_closed(pg)
            it1 = (await _api_items(pg))[0]
            first, day = it1["name"], it1["started"][:10]
            # Data Log 탭 — 기록 1 · 날짜 · 글 필터 · 고르기
            await pg.click('button.tab[data-tab="datalog"]')
            await pg.wait_for_function(f"() => ({_FIRST})() && ({_FIRST})()[0] === {first!r}", timeout=10000)
            await pg.fill('[data-bind="dlDate"]', day)
            await pg.fill('[data-bind="dlText"]', "v089")
            await pg.locator(f'[data-dlname="{first}"]').click()
            await pg.wait_for_function(f"() => ({_META})() !== ''", timeout=10000)
            # 다른 탭에서 공정 → 끝나고 10 s 뒤 돌아온다
            await pg.click('button.tab[data-tab="main"]')
            await _run_once(pg, "V089 Log", "A2")
            await asyncio.sleep(10)
            second = (await _api_items(pg))[0]["name"]
            assert second != first
            await pg.click('button.tab[data-tab="datalog"]')
            try:
                await pg.wait_for_function(f"() => ({_FIRST})() && ({_FIRST})()[0] === {second!r}", timeout=5000)
            except Exception:  # noqa: BLE001
                pytest.fail(f"새 기록이 목록 맨 위에 없다 — 맨 위 {await pg.evaluate(_FIRST)} / 서버 {second}")
            assert "정상 종료" in (await pg.evaluate(_FIRST))[1]
            # c. 필터 · 고른 기록 그대로
            assert await pg.input_value('[data-bind="dlDate"]') == day
            assert await pg.input_value('[data-bind="dlText"]') == "v089"
            assert await pg.evaluate("() => (document.querySelector('.dl-item.on') || {}).dataset.dlname") == first
            assert await pg.text_content('[data-bind="dlName"]') == first
            assert errors == [], errors
        finally:
            await br.close()
            await pw.stop()


async def test_browser_datalog_writing_record_turns_final(tmp_path):
    """b. 공정이 끝난 직후(꼬리 기록 5 s 안) Data Log 탭을 열면 '기록 중' → 그대로 두면 끝 결과로 바뀐다. 열어 둔 상세(결과 칩)도.
       c. 상세를 다시 읽어도 그래프 확대 구간은 그대로."""
    with _serve(tmp_path) as port:
        pw, br = await _browser()
        try:
            errors, warns = [], []
            pg = await _log_page(port, br, errors, warns)
            await _run_once(pg, "V089 Log", "B1")
            await pg.click('button.tab[data-tab="datalog"]')
            await pg.wait_for_function(f"() => {{ const f = ({_FIRST})(); return f && f[1] === '기록 중'; }}", timeout=5000)
            name = (await pg.evaluate(_FIRST))[0]
            await pg.locator(f'[data-dlname="{name}"]').click()
            await pg.wait_for_function(f"() => ({_META})() === '기록 중'", timeout=5000)
            # 그래프를 끌어 확대
            cv = pg.locator('[data-dlchart="p"]')
            bb = await cv.bounding_box()
            y = bb["y"] + bb["height"] / 2
            await pg.mouse.move(bb["x"] + bb["width"] * 0.25, y)
            await pg.mouse.down()
            await pg.mouse.move(bb["x"] + bb["width"] * 0.45, y, steps=5)
            await pg.mouse.up()
            zoom = await pg.evaluate("() => [viewDatalog.charts.p.state.x0, viewDatalog.charts.p.state.x1]")
            try:
                await pg.wait_for_function(
                    f"() => {{ const f = ({_FIRST})(); return f && f[0] === {name!r} && f[1] !== '기록 중'; }}", timeout=20000)
            except Exception:  # noqa: BLE001
                pytest.fail(f"끝난 뒤에도 목록이 '기록 중' 그대로 — {await pg.evaluate(_FIRST)} / 서버 "
                            f"{[(i['name'], i['result']) for i in (await _api_items(pg))[:1]]}")
            assert "정상 종료" in (await pg.evaluate(_FIRST))[1]
            await pg.wait_for_function(f"() => ({_META})().indexOf('정상 종료') >= 0", timeout=10000)
            await asyncio.sleep(0.5)
            assert await pg.evaluate("() => [viewDatalog.charts.p.state.x0, viewDatalog.charts.p.state.x1]") == zoom
            assert (await pg.evaluate("() => (document.querySelector('.dl-item.on') || {}).dataset.dlname")) == name
            assert errors == [], errors
        finally:
            await br.close()
            await pw.stop()


async def test_browser_plc_check_chip_never_ng_after_first_upload(tmp_path):
    """d. 새로 켠 PLC(표 없음)에 처음 올린 뒤 Recipe 탭 PLC Check 칩이 한 번도 NG 로 보이지 않는다 — 여러 번,
       주기 읽기를 늦춰(읽은 뒤 0.8 s 지나서 사본에 넣게) 경합을 확실히 만든 경우를 포함해. 끝에는 Table 칩과 같다."""
    with _serve(tmp_path) as port:
        pw, br = await _browser()
        try:
            errors, warns = [], []
            pg = await _page(br, port, (1600, 840), errors, warns)
            await _save_recipe(pg, "V089 Upload", _recipe("V089 Upload", 2))
            await pg.click('button.tab[data-tab="recipe"]')
            await pg.evaluate("""() => { window.__v089 = [];
              const e = document.querySelector('[data-bind=rcPlc]');
              new MutationObserver(() => window.__v089.push(e.innerText)).observe(e, { childList: true, subtree: true, characterData: true }); }""")
            lk = state.link
            orig = lk.client.read_holding

            async def slow(addr, count):
                regs = await orig(addr, count)
                if addr == A.STATUS_BASE and count == A.STATUS_COUNT:
                    await asyncio.sleep(0.8)
                return regs
            for i, slowed in enumerate([False, False, False, True, True, True]):
                # 새로 켠 PLC 처럼 — 표를 지운다(번호 0 · PLC 검사 불합격)
                state.sim.write(A.RCP_SUM_BASE, [0] * A.RCP_AREA_COUNT)
                for _ in range(60):
                    if state.sim.reg[A.D_RECIPE_OK] == 0:
                        break
                    await asyncio.sleep(0.05)
                assert state.sim.reg[A.D_RECIPE_OK] == 0
                await pg.evaluate("() => app.send('plc_recipe_read')")
                await pg.wait_for_function("() => document.querySelector('[data-bind=rcPlc]').innerText.indexOf('번호 0') >= 0",
                                           timeout=10000)
                await pg.wait_for_function("() => !((core.state.live || {}).seq || {}).recipe_ok", timeout=10000)
                lk.client.read_holding = slow if slowed else orig
                await pg.evaluate("() => { window.__v089 = []; app.send('recipe_upload', { name: 'V089 Upload' }); }")
                await pg.wait_for_function("() => document.querySelector('[data-bind=rcPlc]').innerText.indexOf('PLC Check OK') >= 0",
                                           timeout=15000)
                await asyncio.sleep(6.0)                      # '지금 PLC' 되읽기(5 s)를 한 번 더 지나도
                texts = await pg.evaluate("() => window.__v089")
                lk.client.read_holding = orig
                ng = [x for x in texts if "PLC Check NG" in x]
                assert not ng, (i, slowed, ng[:3])
                now = await pg.evaluate("""() => [document.querySelector('[data-bind=rcPlc]').innerText,
                                                  !!((core.state.live || {}).seq || {}).recipe_ok]""")
                assert "PLC Check OK" in now[0] and now[1] is True, (i, now)
            assert errors == [], errors
        finally:
            await br.close()
            await pw.stop()
