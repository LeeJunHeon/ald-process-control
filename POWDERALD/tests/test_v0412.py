"""v0.4.12 — 트렌드 그림 바뀔 때만 · 이력 막대 · 배관도 다시 배치(글자 크기) · 알람 이력 불러오기 · 이어받기 ·
포트 먼저 묶기 · 시뮬레이터(재개 블록 준비 · 끝 스캔 중단 · D00026 내림 · 한 스캔 도우미).

★ 화면의 순수 함수(decimate.js · chart.js · schematic.js)는 node 로 부른다(node 가 없으면 건너뜀).
★ 배관도 글자 크기 · 겹침의 실제 화면 확인은 브라우저로 — 브라우저를 띄울 수 없으면 건너뛴다.
★ 시뮬레이터는 가짜 시계 · 한 스캔 전체(conftest FakeSim · FakeSim.cmd).
"""
import os
import csv
import json
import time
import types
import socket
import asyncio
import datetime
import threading
import urllib.request

import pytest

from powderald import addresses as A
from powderald import device as DEV
from powderald import paths
from powderald import process
from powderald import window
from powderald.config import host_valid
from powderald.state import AlarmTracker, ALARM_UNKNOWN, state

from conftest import FakeSim, free_port
from test_v047 import rig                                                  # noqa: F401  (픽스처)
from test_v0411 import _node, _dec, JS, _runner, _s, _load, _blocks
from test_v048 import pump_down

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bit = A.bit


# ===================== A1 그림이 바뀔 때만 다시 그린다 =====================
def test_a1_tail_key_same_when_last_column_picture_same():
    out = _dec(r'''
      const pts = []; for (let i = 0; i < 3600 * 5; i++) pts.push([i * 200, 1, 1, 1 + (i % 2) * 0.001]);
      const ypx = v => Math.round(v * 10);                 // 0.1 단위 = 1 화소
      const x0 = 0, x1 = 3600 * 1000, cols = 700;
      const k0 = D.tailKey(pts, x0, x1, cols, ypx);
      const last = pts[pts.length - 1][0];
      pts.push([last + 1, 1, 1, 1.0004]);                  // 같은 열 · 같은 화소 → 같은 그림
      const k1 = D.tailKey(pts, x0, x1, cols, ypx);
      pts.push([last + 2, 3, 3, 3]);                        // 같은 열의 최대 · 끝이 바뀐다
      const k2 = D.tailKey(pts, x0, x1, cols, ypx);
      console.log(JSON.stringify({ k0, k1, k2 }));
    ''')
    assert out["k0"] == out["k1"], out
    assert out["k2"] != out["k1"], out


def test_a1_tail_key_changes_on_new_column_and_ignores_points_outside():
    out = _dec(r'''
      const pts = [[0, 1, 1, 1], [500, 1, 1, 1]];
      const ypx = v => Math.round(v);
      const a = D.tailKey(pts, 0, 1000, 10, ypx);           // 마지막 점은 열 5
      pts.push([650, 1, 1, 1]);                             // 열 6 — 새 열
      const b = D.tailKey(pts, 0, 1000, 10, ypx);
      pts.push([2000, 9, 9, 9]);                            // 창 밖(x1 뒤)은 보지 않는다
      const c = D.tailKey(pts, 0, 1000, 10, ypx);
      console.log(JSON.stringify({ a, b, c }));
    ''')
    assert out["a"] != out["b"] and out["b"] == out["c"], out


def _chart(body: str):
    """chart.js 를 그리기 흉내(가짜 canvas · 2d 문맥)로 — 다시 그린 횟수(clearRect)를 센다."""
    return _node(r'''
      global.window = global; global.devicePixelRatio = 1; global.addEventListener = () => {};
      global.document = { documentElement: {} };
      global.getComputedStyle = () => ({ getPropertyValue: () => '' });
      global.Decimate = require(%s);
      require(%s);
      global.fmt = { DASH: '—', torr: String, num: v => Number(v).toFixed(1) };
      let draws = 0;
      const ctx = new Proxy({}, { get(t, k) {
        if (k === 'measureText') return s => ({ width: String(s).length * 6 });
        if (k === 'clearRect') return () => { draws++; };
        if (k in t) return t[k];
        return () => {};
      }, set(t, k, v) { t[k] = v; return true; } });
      const canvas = { clientWidth: 900, clientHeight: 220, width: 0, height: 0, getContext: () => ctx,
                       addEventListener() {}, getBoundingClientRect: () => ({ width: 900, height: 220, left: 0, top: 0 }) };
      const c = HistChart(canvas, {});
      %s
    ''' % (json.dumps(os.path.join(JS, "decimate.js")), json.dumps(os.path.join(JS, "chart.js")), body))


def test_a1_chart_redraws_only_when_last_column_changes():
    out = _chart(r'''
      const pts = []; for (let i = 0; i < 3600 * 5; i++) pts.push([i * 200, 1, 1, 1]);
      const S = () => [{ label: 'CVG', unit: 'Torr', color: '#888', pts: pts }];
      const x0 = 0, x1 = 3600 * 1000;
      c.set({ series: S(), x0, x1, gap: 5000 });
      const n0 = draws;
      for (let k = 1; k <= 10; k++) { pts.push([x1 - 100 + k, 1, 1, 1]); c.set({ series: S(), x0, x1, gap: 5000 }); }
      const n1 = draws;                                     // 1 시간 보기 · 같은 값 10 점 → 그대로
      pts.push([x1, 1, 1, 50]); c.set({ series: S(), x0, x1, gap: 5000 });
      const n2 = draws;                                     // 마지막 열의 최대가 바뀜 → 다시
      c.set({ series: S(), x0: x0 + 6000, x1: x1 + 6000, gap: 5000 });
      const n3 = draws;                                     // 창이 1 px 이상 움직임 → 다시
      console.log(JSON.stringify({ n0, n1, n2, n3 }));
    ''')
    assert out["n0"] == 1 and out["n1"] == 1, out
    assert out["n2"] == 2 and out["n3"] == 3, out


# ===================== A4 줄인 띠의 진하기 =====================
def test_a4_band_carries_count_and_column():
    out = _dec(r'''
      const pts = []; for (let i = 0; i < 1000; i++) pts.push([i, (i % 10), (i % 10), (i % 10)]);
      const r = D.view(pts, 0, 999, Infinity, 10);
      const raw = D.view([[0, 1, 2, 1.5], [1, 1, 1, 1]], 0, 1, Infinity, 100);
      const b = r.band[0], q = raw.band[0];
      console.log(JSON.stringify({ b0: [b[0], b[1], b[2], b.n, b.col], n: r.band.length,
                                   raw: raw.band.length, q: [q[0], q[1], q[2], q.n, q.col, q.hs] }));
    ''')
    assert out["n"] == 10 and out["b0"] == [0, 0, 9, 100, 0], out           # 열 0 · 100 점 · 최소 0 ~ 최대 9
    assert out["raw"] == 1 and out["q"] == [0, 1, 2, 1, None, 1], out       # 줄이지 않은 묶음은 열 없음


def test_a4_chart_band_is_denser_with_more_points():
    """줄이기 전에는 점마다 0.15 칸을 겹쳐 그렸다 — 점이 많은 열의 띠는 0.15 보다 진해야 한다."""
    out = _chart(r'''
      const alphas = [];
      // 느린 추세(1 시간에 0 → 10) + 작은 잡음 — 열 하나에 약 28 점이 몇 화소 안에 몰린다
      const pts = []; for (let i = 0; i < 3600 * 5; i++) { const v = i / 1800 + (i % 7) * 0.03; pts.push([i * 200, v, v, v]); }
      Object.defineProperty(ctx, 'globalAlpha', { set(v) { alphas.push(v); }, get() { return 1; } });
      c.set({ series: [{ label: 'x', unit: '', color: '#888', pts }], x0: 0, x1: 3600 * 1000, gap: 5000 });
      console.log(JSON.stringify({ max: Math.max(...alphas.filter(a => a < 1)) }));
    ''')
    assert out["max"] > 0.3, out


# ===================== A3 배관도 글자 크기 · 겹침 =====================
MIN_UNITS = 12.6 if DEV.HAS_RF else 11.2       # 768 창에서 9 px(PEALD 0.717 · Powder 0.802 px/단위)


def _schem(body: str):
    return _node(r'''
      function El(tag) { return { tag, a: {}, kids: [], textContent: '', setAttribute(k, v) { this.a[k] = String(v); },
        getAttribute(k) { return this.a[k]; }, appendChild(c) { this.kids.push(c); return c; },
        classList: { toggle() {} }, style: {} }; }
      const svg = El('svg');
      Object.defineProperty(svg, 'innerHTML', { set() { svg.kids = []; } });
      svg.querySelector = () => null; svg.querySelectorAll = () => [];
      global.window = global;
      global.document = { createElementNS: (ns, t) => El(t), getElementById: () => svg };
      global.core = { register() {}, bind() { return null; }, state: {}, bit() { return false; } };
      global.fmt = { DASH: '—', flow: String, temp: String, torr: String, pct: String, watt: String, num: String };
      require(%s);
      viewSchematic.render({ live: {} });
      const all = []; (function walk(e) { all.push(e); e.kids.forEach(walk); })(svg);
      %s
    ''' % (json.dumps(os.path.join(JS, "views", "schematic.js")), body))


def test_a3_schematic_font_floor_and_full_names():
    out = _schem(r'''
      const texts = all.filter(e => e.tag === 'text');
      const titles = all.filter(e => e.tag === 'title').map(e => e.textContent);
      const shown = texts.map(e => e.textContent);
      console.log(JSON.stringify({ min: Math.min(...texts.map(e => Number(e.a['font-size']))), titles, shown,
                                   vb: svg.a.viewBox }));
    ''')
    assert out["vb"] == "0 0 600 507"
    assert out["min"] >= MIN_UNITS, out["min"]
    # 줄인 이름(상자 안 'MFC1')은 툴팁에 전체 이름
    assert "MFC1 전구체 캐리어" in out["titles"] and "MFC2 어시스트" in out["titles"]
    assert "MFC1" in out["shown"] and not any("캐리어" in s for s in out["shown"])
    if DEV.HAS_RF:
        assert "MFC3 반응물 퍼지" in out["titles"] and "H2O 캐니스터 R" in out["titles"]


async def _page(port, width, height):
    pw_mod = pytest.importorskip("playwright.async_api")
    pw = await pw_mod.async_playwright().start()
    try:
        b = await pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        await pw.stop()
        pytest.skip(f"브라우저를 띄울 수 없음: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
    pg = await b.new_page(viewport={"width": width, "height": height})
    await pg.goto(f"http://127.0.0.1:{port}/")
    await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
    return pw, b, pg


_SCHEM_JS = """(minPx) => {
  const svg = document.getElementById('schemSvg');
  const k = svg.getScreenCTM().a;
  const texts = [...svg.querySelectorAll('text')].filter(t => t.getBBox().width > 0 &&
                 getComputedStyle(t).display !== 'none' && !t.closest('[style*="display: none"]'));
  const small = texts.filter(t => Number(t.getAttribute('font-size')) * k < minPx - 1e-6)
                     .map(t => t.textContent + ' ' + (Number(t.getAttribute('font-size')) * k).toFixed(2));
  const box = t => t.getBBox();
  const hit = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  const bad = [];
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++)
    if (hit(box(texts[i]), box(texts[j]))) bad.push(texts[i].textContent + ' / ' + texts[j].textContent);
  // 배관(세로 · 가로 토막)과 글자
  const segs = [];
  svg.querySelectorAll('path[data-pipe]').forEach(p => {
    const tk = p.getAttribute('d').match(/[MHV]|-?[\\d.]+/g); let x = 0, y = 0, i = 0;
    while (i < tk.length) { const c = tk[i++];
      if (c === 'M') { x = +tk[i++]; y = +tk[i++]; }
      else if (c === 'H') { const n = +tk[i++]; segs.push({ x: Math.min(x, n), y: y - 0.9, width: Math.abs(n - x), height: 1.8 }); x = n; }
      else if (c === 'V') { const n = +tk[i++]; segs.push({ x: x - 0.9, y: Math.min(y, n), width: 1.8, height: Math.abs(n - y) }); y = n; } }
  });
  texts.forEach(t => segs.forEach(s => { if (hit(box(t), s)) bad.push(t.textContent + ' / 배관'); }));
  // 상자 테두리와 글자 — 글자가 상자에 걸치면(안에 다 들지 않으면) 겹침
  svg.querySelectorAll('rect').forEach(r => {
    const b = r.getBBox();
    texts.forEach(t => { const a = box(t);
      if (hit(a, b) && !(a.x >= b.x && a.x + a.width <= b.x + b.width && a.y >= b.y && a.y + a.height <= b.y + b.height))
        bad.push(t.textContent + ' / 상자'); });
  });
  return { k, small, bad, n: texts.length };
}"""


@pytest.mark.parametrize("width,height,min_px", [(768, 900, 9.0), (960, 1000, 11.0)])
async def test_a3_schematic_fonts_and_overlaps_in_browser(rig, width, height, min_px):
    pw, b, pg = await _page(rig.port, width, height)
    try:
        await pg.evaluate("() => core.setTab('main')")
        # 값 자리에 가장 긴 값을 넣고 잰다
        r = await pg.evaluate("(minPx) => { const svg = document.getElementById('schemSvg');"
                              " svg.querySelectorAll('[data-sv]').forEach(t => { const k = t.getAttribute('data-sv');"
                              " t.textContent = /^mfc\\dsv$/.test(k) ? '설정 1000.0' : /^mfc\\dpv$/.test(k) ? '1000.0' :"
                              " k === 'rf' ? '300.0 W (반사 12.0)' : /^h\\d$/.test(k) ? '250.0 ℃' :"
                              " k === 'pcv' ? '100 %' : k === 'o3' ? '200.0 g/Nm3' : t.textContent; });"
                              " return (" + _SCHEM_JS + ")(minPx); }", min_px)
        assert r["n"] > 20 and not r["small"], r
        assert not r["bad"], r
    finally:
        await b.close()
        await pw.stop()


async def test_a2_a5_trend_bar_buttons_visible_and_cm_hidden(rig):
    for width in (1280, 1024, 960, 768):
        pw, b, pg = await _page(rig.port, width, 900)
        try:
            r = await pg.evaluate("""() => {
                core.setTab('trend'); viewTrendHist.setMode('hist');
                const bar = document.querySelector('.trendbar').getBoundingClientRect();
                const out = [...document.querySelectorAll('[data-bind="histBar"] button, [data-bind="histBar"] input')]
                  .filter(e => { const q = e.getBoundingClientRect();
                                 return q.right > bar.right + 0.5 || q.left < bar.left - 0.5 || q.width === 0; })
                  .map(e => e.textContent || e.getAttribute('data-bind'));
                core.state.structure.cm_installed = false; viewTrendHist.picker();
                const cm = !!document.querySelector('[data-hcol="cm"]');
                core.state.structure.cm_installed = true; viewTrendHist.picker();
                return { out, cm, cmOn: !!document.querySelector('[data-hcol="cm"]') };
            }""")
            assert not r["out"], (width, r)
            assert r["cm"] is False and r["cmOn"] is True, (width, r)      # 단 것만
        finally:
            await b.close()
            await pw.stop()


# ===================== A5 CM 이 없으면 이력 고르기에 내지 않는다 =====================
def test_a5_structure_reports_cm_installed(cfg):
    state.install_config(cfg, [], "example")
    assert state.structure()["cm_installed"] is bool(state.conv.cm.installed)


def test_a5_lock_message_keeps_words():
    css = open(os.path.join(ROOT, "frontend", "css", "style.css"), encoding="utf-8").read()
    i = css.index(".cmdbar .lockmsg {")
    assert "word-break: keep-all" in css[i:css.index("}", i)]


# ===================== B1 알람 이력 불러오기 =====================
def _alarm_file(day, rows, raw_tail=b""):
    os.makedirs(paths.ALARMS_DIR, exist_ok=True)
    p = os.path.join(paths.ALARMS_DIR, f"alarms-{day:%Y%m%d}.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시각", "구분", "코드", "내용", "등급"])
        w.writerows(rows)
    if raw_tail:
        with open(p, "ab") as f:
            f.write(raw_tail)
    return p


def test_b1_broken_bytes_and_truncated_row_are_skipped():
    now = datetime.datetime(2026, 10, 2, 9, 0, 0)
    _alarm_file(now, [["2026-10-02 08:00:00", "발생", "A0-14", "가스 누출", "중대"],
                      ["2026-10-02 08:01:00", "해제", "A0-14", "가스 누출", "중대"]],
                raw_tail=b"2026-10-02 08:02:00,\xb9\xdf\xbb\xfd,A0-07,x,\xc1\n" +    # cp949 조각(깨진 글자)
                         "2026-10-02 08:03:00,발생,A0-07,베이스 도달 시간 초과,경고\r\n".encode("utf-8") +
                         "2026-10-02 08:04:00,발생,A0-00,비상정지,중".encode("utf-8"))   # 쓰다 꺼진 줄
    t = AlarmTracker()
    assert t.load_recent(now) == 2
    assert [h["code"] for h in t.history] == ["A0-07", "A0-14"]


def test_b1_one_bad_file_does_not_drop_the_other():
    now = datetime.datetime(2026, 10, 2, 9, 0, 0)
    os.makedirs(os.path.join(paths.ALARMS_DIR, "alarms-20261001.csv"))                # 어제 '파일'이 폴더
    _alarm_file(now, [["2026-10-02 08:00:00", "발생", "A0-14", "가스 누출", "중대"]])
    t = AlarmTracker()
    assert t.load_recent(now) == 1


def test_b1_large_file_reads_only_the_tail():
    now = datetime.datetime(2026, 10, 2, 9, 0, 0)
    rows = []
    for i in range(100_000):
        ts = f"2026-10-02 {i // 3600 % 24:02d}:{i // 60 % 60:02d}:{i % 60:02d}"
        rows.append([ts, "발생" if i % 2 == 0 else "해제", "A0-07", "베이스 도달 시간 초과", "경고"])
    rows.append(["2026-10-02 08:59:59", "발생", "A0-14", "가스 누출", "중대"])
    _alarm_file(now, rows)
    t = AlarmTracker()
    t0 = time.perf_counter()
    n = t.load_recent(now)
    took = time.perf_counter() - t0
    assert n == 200 and t.history[0]["code"] == "A0-14", t.history[:2]
    assert took < 0.5, took                                         # 통째로 읽던 것 1.65 s


def test_b1_rereported_after_restart_reuses_closed_row():
    now = datetime.datetime.now()
    p = _alarm_file(now, [[f"{now:%Y-%m-%d} 08:00:00", "발생", "A0-07", "베이스 도달 시간 초과", "경고"]])
    t = AlarmTracker()
    assert t.load_recent(now) == 1 and t.history[0]["cleared"] == ALARM_UNKNOWN
    d07 = next(d for d in DEV.ALARMS0 if d["bit"] == 7)
    fresh = t.update(1 << 7, 0)                                     # 다시 시작한 뒤 첫 갱신에 같은 코드
    assert fresh == ["A0-07"] and len(t.history) == 1
    assert t.history[0]["cleared"] == "" and t.history[0]["since"] == "08:00:00"
    assert t.active["A0-07"]["name"] == d07["name"]
    with open(p, encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.reader(f))) == 2, "새 '발생' 줄을 쓰지 않는다"
    t.update(0, 0)
    t.update(1 << 7, 0)                                             # 그 뒤 다시 서면 새 줄
    assert len(t.history) == 2 and t.history[0]["cleared"] == ""


def test_b1_reuse_only_on_first_update():
    now = datetime.datetime.now()
    _alarm_file(now, [[f"{now:%Y-%m-%d} 08:00:00", "발생", "A0-07", "베이스 도달 시간 초과", "경고"]])
    t = AlarmTracker()
    t.load_recent(now)
    t.update(0, 0)                                                  # 첫 갱신에 없었다 — 꺼져 있던 동안 풀렸다
    t.update(1 << 7, 0)
    assert len(t.history) == 2 and t.history[1]["cleared"] == ALARM_UNKNOWN


# ===================== B2 이어받기 =====================
async def test_b2_adopt_retries_and_takes_b13_before_read(cfg, monkeypatch):
    monkeypatch.setattr(process, "ADOPT_READ_GAP_S", 0.0)
    r, tbl = _runner(cfg)
    s = _s(4, st=A.STATE_RUN, a0=0)
    calls = []

    async def read_area():
        calls.append(1)
        if len(calls) < 3:
            return None                                             # 두 번 실패
        s[A.D_ALARM0] = 1 << A.ALM0_RECIPE                          # 되읽는 동안 b13 이 섰다
        return list(tbl["words"])
    state.link = types.SimpleNamespace(connected=True, status=s, read_recipe_area=read_area)
    try:
        async def log(*_a):
            return None
        await r.adopt_running(log)
        assert len(calls) == 3 and r.active_run
        assert r._b13_pre is False, "b13 은 되읽기 전 상태로"
    finally:
        state.link = None


async def test_b2_adopt_gives_up_when_read_keeps_failing(cfg, monkeypatch):
    monkeypatch.setattr(process, "ADOPT_READ_GAP_S", 0.0)
    r, _tbl = _runner(cfg)
    logs = []

    async def read_area():
        return None
    state.link = types.SimpleNamespace(connected=True, status=_s(4, st=A.STATE_RUN), read_recipe_area=read_area)
    try:
        async def log(m, *_a):
            logs.append(m)
        await r.adopt_running(log)
        assert not r.active_run and r.run is None
        assert any("읽지 못했습니다" in m for m in logs)
    finally:
        state.link = None


async def test_b2_adopt_skips_when_process_ended_during_read(cfg):
    r, tbl = _runner(cfg)
    s = _s(4, st=A.STATE_RUN)

    async def read_area():
        s[A.D_STATE], s[A.D_SEQ_STATE] = A.STATE_IDLE, 6           # 되읽는 동안 끝났다
        return list(tbl["words"])
    state.link = types.SimpleNamespace(connected=True, status=s, read_recipe_area=read_area)
    try:
        async def log(*_a):
            return None
        await r.adopt_running(log)
        assert not r.active_run and r.run is None
    finally:
        state.link = None


# ===================== B3 포트 =====================
@pytest.mark.parametrize("host,ok", [("", True), (None, True), ("localhost", True), ("127.0.0.1", True),
                                     ("0.0.0.0", True), ("::", True), ("::1", True), ("my-pc", False),
                                     (" 127.0.0.1", False), ("127.0.0.256", False), (5000, False)])
def test_b3_host_format(host, ok):
    assert host_valid(host) is ok


def test_b3_bad_host_is_config_error(cfg):
    from powderald import config as C
    cfg["server"]["host"] = "my-pc"
    assert any(lv == "err" and "server.host" in m for lv, m in C.validate(cfg))


def _ipv6_ok():
    try:
        with socket.socket(socket.AF_INET6) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _ipv6_ok(), reason="IPv6 루프백이 없다")
def test_b3_ipv6_hosts_are_probed_as_ipv6():
    port = free_port()
    assert window.port_probe("::1", port) == "", "IPv6 를 IPv4 로 확인해 늘 '사용 중'이던 것"
    assert window.port_probe("::", port) == ""
    with socket.socket(socket.AF_INET6) as s:
        s.bind(("::1", port))
        s.listen(1)
        assert window.port_probe("::1", port)
        infos = {i[4][0] for i in socket.getaddrinfo("localhost", port, 0, socket.SOCK_STREAM)}
        if "::1" in infos:
            assert window.port_probe("localhost", port), "localhost 를 127.0.0.1 로만 보던 것"


def test_b3_bind_port_holds_the_socket_and_uvicorn_serves_on_it():
    import uvicorn
    port = free_port()
    socks, why = window.bind_port("127.0.0.1", port, total_s=0)
    assert socks and not why

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None,
                                        lifespan="off"))
    th = threading.Thread(target=srv.run, kwargs={"sockets": socks}, daemon=True)
    th.start()
    try:
        body = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as resp:
                    body = resp.read()
                break
            except OSError:
                time.sleep(0.1)
        assert body == b"ok"                                    # 미리 묶은 소켓으로 듣는다
        assert window.port_probe("127.0.0.1", port), "서버가 쥔 포트는 다른 쪽이 못 쓴다"
    finally:
        srv.should_exit = True
        th.join(5)
        window.close_sockets(socks)


def test_b3_window_run_stops_before_server_when_port_busy(monkeypatch):
    shown = []
    monkeypatch.setattr(window, "acquire_single_instance", lambda: True)
    monkeypatch.setattr(window, "set_app_user_model_id", lambda: None)
    monkeypatch.setattr(window, "_msgbox", shown.append)
    monkeypatch.setattr(window, "PORT_WAIT_S", 0.2)
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: (_ for _ in ()).throw(AssertionError("서버를 띄웠다")))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        window.run(object(), "127.0.0.1", port)
    assert shown and "설정 포트" in shown[0]


def test_b3_headless_exits_before_server_when_port_busy(monkeypatch):
    import sys
    import uvicorn
    import run as entry
    from powderald import commands
    monkeypatch.setattr(commands, "set_shutdown_handler", lambda _f: None)   # 창 종료 처리를 걸지 않는다
    monkeypatch.setattr(state, "cfg", state.cfg)                             # 끝나면 되돌린다
    monkeypatch.setattr(window, "PORT_WAIT_S", 0.2)
    monkeypatch.setattr(uvicorn, "Server", lambda *a, **k: (_ for _ in ()).throw(AssertionError("서버를 띄웠다")))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]

        def fake_app(_cfg=""):
            state.cfg = {"server": {"host": "127.0.0.1", "port": port}}
            return object()
        monkeypatch.setattr(entry, "create_app", fake_app)
        monkeypatch.setattr(sys, "argv", ["run.py", "--headless"])
        with pytest.raises(SystemExit) as e:
            entry.main()
    assert e.value.code == 2


# ===================== C1 ~ C3 시뮬레이터 =====================
def _start(fs, rec):
    s = fs.sim
    pump_down(fs)
    _load(fs, rec)
    for i in range(80):
        s.write(A.D_PC_HB, [i + 1])
        fs.step(1)
        if bit(s.reg[A.D_INTERLOCK], A.ILK_START_OK):
            break
    assert fs.cmd(A.CMD_PROCESS_START) == A.RESULT_OK


def _step_ms(s):
    return A.dword(s.reg[A.D_SEQ_STEP_MS], s.reg[A.D_SEQ_STEP_MS + 1])


def test_c1_resume_at_block_boundary_runs_block_prep_same_scan(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    _start(fs, _blocks(2, steps=1, step_ms=100))
    assert fs.cmd(A.CMD_PAUSE) == A.RESULT_OK
    for _ in range(100):
        fs.step(1)
        if s.seq_state == 7:
            break
    assert s.seq_state == 7 and s.blk == 1                        # 블록 1 마지막 스텝 끝에서 멈춤
    assert fs.cmd(A.CMD_RESUME) == A.RESULT_OK
    # 래더 행 36 → 50 → 58 → 78~95 → 103 → 112: 블록 2 를 적재하고 준비(안정 0 s)까지 그 스캔에 — 스텝 시간은 0
    assert (s.reg[A.D_SEQ_BLOCK], s.reg[A.D_SEQ_STATE], _step_ms(s)) == (2, 4, 0)
    fs.step(1)
    assert _step_ms(s) == 20


def test_c2_abort_on_normal_end_scan_is_abort(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    _start(fs, _blocks(1, steps=1, step_ms=100))
    for _ in range(100):
        if s.running and s.seq_state == 4 and s.step_ms + 20.5 >= s.step_dur:      # 소수 오차 여유
            break
        fs.step(1)
    else:
        raise AssertionError("마지막 스텝 끝 스캔을 못 찾았다")
    assert fs.cmd(A.CMD_ABORT) == A.RESULT_OK                       # 끝나는 그 스캔에 즉시 중단
    assert not s.running and s.reg[A.D_SEQ_STATE] == 8 and "즉시 중단" in s.end_reason
    assert s.reg[A.D_SEQ_BLOCK] == 2, "D00021 = 블록 수 + 1 은 같다"


def test_c2_safe_stop_on_stop_after_cycle_scan_is_abort(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    _start(fs, _blocks(1, repeat_last=5, steps=1, step_ms=100))
    assert fs.cmd(A.CMD_STOP_AFTER_CYCLE) == A.RESULT_OK
    for _ in range(100):
        if s.running and s.seq_state == 4 and s.step_ms + 20.5 >= s.step_dur:      # 소수 오차 여유
            break
        fs.step(1)
    else:
        raise AssertionError("사이클 끝 스캔을 못 찾았다")
    s.set_fault("emo", True)
    fs.step(1)
    assert not s.running and s.reg[A.D_SEQ_STATE] == 8 and "안전 정지" in s.end_reason
    assert (s.reg[A.D_SEQ_BLOCK], s.reg[A.D_SEQ_BLOCK_PASS]) == (1, 1)


def test_c3_step_end_and_d26_floor_with_fake_clock(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    _start(fs, _blocks(1, repeat_last=3, steps=1, step_ms=200))
    seen = []
    for _ in range(30):
        fs.step(1, 0.02)
        seen.append((s.cycle, _step_ms(s)))
    first = [m for c, m in seen if c == 1]
    assert first == [20, 40, 60, 80, 100, 120, 140, 160, 180], first     # 200 ms 스텝 = 10 스캔(시작 스캔 포함)
    assert seen[len(first)] == (2, 0)


def test_c3_d26_in_100ms_units_over_60s(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    _start(fs, _blocks(1, steps=1, step_ms=70_000))
    for _ in range(123):
        fs.step(1, 0.01)
    assert _step_ms(s) == 1200, _step_ms(s)                          # 1.23 s → 100 ms 단위 내림


# 가져온 이름 정리
_ = (asyncio,)
