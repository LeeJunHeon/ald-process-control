"""v0.5.1 — 밝은 기본 테마 · 배관도 격자 다시 그리기 · 칸 넘침 · 조작 창 작은 것 · 남은 한글.

★ 화면의 순수 함수(terms.js · faceplate.js · schematic.js)는 node 로 부른다(node 가 없으면 건너뜀). 브라우저 시험은 없다.
"""
import os
import re
import csv
import json
import datetime

import pytest

from peald import config as C
from peald import device as DEV
from peald import plclink
from peald.state import AlarmTracker, ALARM_UNKNOWN, State

from test_v0411 import _node, JS
from test_v0412 import _schem, _alarm_file
from test_v050 import _fp, _state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANGUL = re.compile("[가-힣]")


def _src(*parts):
    return open(os.path.join(ROOT, "frontend", *parts), encoding="utf-8").read()


# ===================== A1 · A2. 기본은 밝은 테마, 저장된 테마 설정을 따른다 =====================
def test_default_theme_is_light():
    assert DEV.THEME == "light"
    assert C.DEFAULTS["window"]["theme"] == "light"
    assert '<html lang="ko" data-theme="light">' in _src("index.html")
    assert State().device_info()["theme"] == "light"


@pytest.mark.parametrize("saved,shown", [("dark", "dark"), ("light", "light"), ("DARK", "dark"), ("blue", "light")])
def test_saved_theme_is_followed(saved, shown):
    s = State()
    s.cfg = {"window": {"side": DEV.DEFAULT_SIDE, "theme": saved}}
    assert s.device_info()["theme"] == shown


def test_theme_setting_is_validated_and_editable():
    import copy
    from peald import settings
    cfg = copy.deepcopy(C.DEFAULTS)
    cfg["window"]["theme"] = "blue"
    assert any("window.theme" in m for _l, m in C.validate(cfg))
    cfg["window"]["theme"] = "dark"
    assert not any("window.theme" in m for _l, m in C.validate(cfg))
    assert any(f["path"] == "window.theme" for f in settings._F)
    assert "'window.theme'" in _src("js", "views", "setup.js")


def test_dark_theme_is_one_step_lighter():
    tok = _src("css", "tokens.css")
    dark = dict(re.findall(r"--([\w-]+):\s*([^;]+);", tok.split(':root[data-theme="dark"]')[1]))

    def lum(h):
        h = h.strip().lstrip("#")
        return sum(int(h[i:i + 2], 16) for i in (0, 2, 4)) / 3
    assert lum(dark["bg"]) >= 0x22 and lum(dark["panel"]) >= 0x28       # v0.5.0 은 #17191c · #1d2023
    assert abs(lum(dark["panel"]) - lum(dark["bg"])) <= 10               # 대비는 줄이고 칸은 테두리로


# ===================== A3. 결과 문구 색 · Restart 칩 · 로그 줄 · 알람 이름 점 =====================
def test_result_level_colors():
    out = _node("const T = require(%s); console.log(JSON.stringify(%s.map(T.resultLevel)));" % (
        json.dumps(os.path.join(JS, "terms.js")),
        json.dumps(["정상 종료", "정상 종료 (사이클 후 정지와 겹침)", "정상 종료 (끝난 뒤 안전 정지: 펌프 알람)",
                    "중단 (운전자 중단)", "사이클 후 정지 (블록 1 · 사이클 3)", "중단 (끝 확인 안 됨)",
                    "중단 (안전 정지 — 가스 누출)", "중단 (PLC 중단)", "중단 (레시피 표 오류 — 블록 2 적재 거절)",
                    "베이스 압력 대기 중"], ensure_ascii=False)))
    assert out == ["", "", "warn", "warn", "warn", "warn", "stop", "stop", "stop", ""]
    css = _src("css", "style.css")
    assert ".procmsg { font-size: var(--fs-sm); color: var(--ink-dim);" in css
    assert ".procmsg.warn { color: var(--warn); }" in css and ".procmsg.stop { color: var(--stop); }" in css
    assert ".logbox .ln.ok { color: var(--ink); }" in css
    main = _src("js", "views", "main.js")
    assert "T.resultLevel(msg)" in main and "'Last: '" not in main
    assert '<span class="chip">Last</span>' in main


def test_restart_chip_is_neutral():
    js = _src("js", "views", "setup.js")
    assert 'chip warn">Restart' not in js and "core.chip('Restart', 'warn')" not in js
    assert '<span class="chip" title="바꾸면 프로그램을 다시 시작해야 반영됩니다">Restart</span>' in js


def test_alarm_named_inputs_are_gray_when_normal():
    js = _src("js", "views", "main.js")
    assert "if (k.ok_when === false) lvl = on ? 'stop' : '';" in js
    assert "lvl = on ? 'stop' : 'ok'" not in js


# ===================== A4. 글자 크기 셋 · 굵기 둘 · 768 에서 9 px 이상 =====================
def test_font_sizes_and_weights():
    tok = _src("css", "tokens.css")
    sizes = {int(v) for v in re.findall(r"--fs-[\w-]+:\s*(\d+)px", tok)}
    assert sizes == {12, 13, 15}, sizes                   # 960 설계 × 0.8(768 창) → 9.6 px 이상
    css = _src("css", "style.css")
    weights = set(re.findall(r"font-weight:\s*(\w+)", css))
    assert weights <= {"400", "700", "normal", "bold"}, weights
    assert not re.search(r"font-size:\s*\d+px", css)
    chart = _src("js", "chart.js")
    px = [int(v) for v in re.findall(r"(\d+)px '", chart)]
    assert px and min(px) >= 12, px
    sch = _src("js", "views", "schematic.js")
    assert not re.search(r"weight: [5-6]00", sch)


# ===================== B. 배관도 — 8 격자 · 가로/세로만 · 겹침 0 · Tab 순서 =====================
_GEOM = r'''
  const G = 8, on = v => Math.abs(v / G - Math.round(v / G)) < 1e-9;
  const rects = all.filter(e => e.tag === 'rect').map(e => ({ x0: +e.a.x, y0: +e.a.y, x1: +e.a.x + +e.a.width,
                                                              y1: +e.a.y + +e.a.height, w: +e.a.width, h: +e.a.height }));
  const pipes = all.filter(e => e.tag === 'path' && e.a['data-pipe'] !== undefined);
  const dots = all.filter(e => e.tag === 'circle' && e.a['data-jn'] !== undefined).map(e => [+e.a.cx, +e.a.cy]);
  const valves = all.filter(e => e.tag === 'path' && e.a['data-valve']).map(e => {
    const m = e.a.d.match(/L(-?[\d.]+),(-?[\d.]+)/); return { tag: e.a['data-valve'], x: +m[1], y: +m[2] }; });
  const bad = [], segs = [];
  rects.forEach(r => { if (![r.x0, r.y0, r.w, r.h].every(on)) bad.push('rect off grid ' + JSON.stringify(r)); });
  valves.forEach(v => { if (!on(v.x) || !on(v.y)) bad.push('valve off grid ' + v.tag); });
  dots.forEach(p => { if (!on(p[0]) || !on(p[1])) bad.push('dot off grid ' + p); });
  pipes.forEach(p => {
    const d = p.a.d;
    if (/[^MHV\d\s.,-]/.test(d)) bad.push('pipe not H/V ' + d);
    const tk = d.match(/[MHV]|-?[\d.]+/g); let x = 0, y = 0, i = 0;
    while (i < tk.length) { const c = tk[i++];
      if (c === 'M') { x = +tk[i++]; y = +tk[i++]; if (!on(x) || !on(y)) bad.push('pipe off grid ' + d); }
      else if (c === 'H') { const n = +tk[i++]; if (!on(n)) bad.push('pipe off grid ' + d);
        segs.push({ k: p.a['data-pipe'], h: 1, a: Math.min(x, n), b: Math.max(x, n), c: y, e: [[x, y], [n, y]] }); x = n; }
      else if (c === 'V') { const n = +tk[i++]; if (!on(n)) bad.push('pipe off grid ' + d);
        segs.push({ k: p.a['data-pipe'], h: 0, a: Math.min(y, n), b: Math.max(y, n), c: x, e: [[x, y], [x, n]] }); y = n; } }
  });
  // 배관이 상자 안을 지나거나 테두리를 따라 가지 않는다(테두리에 끝이 닿는 것만)
  segs.forEach(s => rects.forEach(r => {
    const lo = s.h ? r.x0 : r.y0, hi = s.h ? r.x1 : r.y1, c0 = s.h ? r.y0 : r.x0, c1 = s.h ? r.y1 : r.x1;
    if (s.c >= c0 && s.c <= c1 && Math.min(s.b, hi) - Math.max(s.a, lo) > 0)
      bad.push('pipe/box ' + s.k + ' ' + JSON.stringify([r.x0, r.y0, r.x1, r.y1]));
  }));
  const isEnd = (s, x, y) => s.e.some(p => p[0] === x && p[1] === y);
  const hasDot = (x, y) => dots.some(p => p[0] === x && p[1] === y);
  for (let i = 0; i < segs.length; i++) for (let j = i + 1; j < segs.length; j++) {
    const s = segs[i], t = segs[j];
    if (s.h === t.h) {          // 겹쳐 그린 배관
      if (s.c === t.c && Math.min(s.b, t.b) - Math.max(s.a, t.a) > 0) bad.push('pipe/pipe ' + s.k + ' / ' + t.k);
      continue;
    }
    const H = s.h ? s : t, V = s.h ? t : s, x = V.c, y = H.c;
    if (x < H.a || x > H.b || y < V.a || y > V.b) continue;
    if (isEnd(H, x, y) && isEnd(V, x, y)) continue;             // 꺾임(모서리)
    if (!hasDot(x, y)) bad.push((isEnd(H, x, y) || isEnd(V, x, y) ? 'branch without dot ' : 'crossing ') +
                                H.k + ' / ' + V.k + ' @' + x + ',' + y);
  }
  // 상자끼리 겹치지 않는다
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i], b = rects[j];
    if (a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1) bad.push('box/box ' + JSON.stringify([a, b]));
  }
  // Tab 순서 — v0.5.2(B7): 무리(Precursor → Reactant → Chamber → Exhaust) 다음 위 → 아래, 왼쪽 → 오른쪽
  const fp = all.filter(e => e.tag === 'g' && e.a['data-fp']).map(e => {
    const m = e.kids.find(k => k.tag === 'path').a.d.match(/M(-?[\d.]+),(-?[\d.]+)/); return [+e.a['data-grp'], +m[2], +m[1]]; });
  const cmp = (a, b) => (a[0] - b[0]) || (a[1] - b[1]) || (a[2] - b[2]);
  for (let i = 1; i < fp.length; i++) if (cmp(fp[i - 1], fp[i]) > 0) bad.push('tab order ' + i);
  console.log(JSON.stringify({ bad, nseg: segs.length, nrect: rects.length, ndot: dots.length, nfp: fp.length,
                               tabs: all.filter(e => e.a && e.a.tabindex !== undefined).length }));
'''


def test_pid_grid_hv_lines_no_overlap_and_tab_order():
    # v0.5.2: Tab 순서는 무리 순서가 먼저(B7) — y · x 만 보던 검사를 고쳤다
    out = _schem(_GEOM)
    assert not out["bad"], out["bad"]
    assert out["nseg"] > 15 and out["ndot"] >= 4 and out["nfp"] >= 15 and out["tabs"] == out["nfp"], out


# 글자 겹침 — 가장 긴 값을 넣고 글자 폭을 어림해 글자/글자 · 글자/배관 · 글자/상자 겹침 0, 13 단위 이상, 칸 안
_TEXT = r'''
  const WORST = { cvg: '760.00', cm: '760.00', pcv: '100 %', rf: '300.0 W (Ref 12.0)', lid: 'Closed', o3: '200.0 g/Nm3' };
  for (let i = 1; i <= 4; i++) { WORST['mfc' + i + 'pv'] = '1000.0'; WORST['mfc' + i + 'sv'] = 'SP 1000.0'; }
  for (let i = 1; i <= 6; i++) WORST['h' + i] = '250.0 ℃';
  function cw(ch, mono) {
    const c = ch.codePointAt(0);
    if ((c >= 0xAC00 && c <= 0xD7A3) || ch === '℃' || ch === '—' || ch === '→') return 1.0;
    if (mono) return 0.62;
    if (ch === ' ') return 0.3; if (ch === '·' || ch === '.') return 0.35;
    if ('()'.includes(ch)) return 0.36; if (ch === '-') return 0.38; if (ch === '%') return 0.85; if (ch === '/') return 0.4;
    if ('ilI'.includes(ch)) return 0.3; if ('ftjr'.includes(ch)) return 0.4; if ('mwMW'.includes(ch)) return 0.9;
    if (/[0-9]/.test(ch)) return 0.6; if (/[A-Z]/.test(ch)) return 0.68; if (/[a-z]/.test(ch)) return 0.56;
    return 0.7;
  }
  const texts = [], segs = [], rects = [];
  all.forEach(e => {
    if (e.tag === 'text') {
      const f = Number(e.a['font-size']), s = e.a['data-sv'] && WORST[e.a['data-sv']] || e.textContent;
      const mono = /mono/.test(e.a['font-family'] || ''), bold = Number(e.a['font-weight'] || 400) >= 600;
      let wd = 0; for (const ch of s) wd += cw(ch, mono);
      wd = wd * f * (bold ? 1.05 : 1) + (Number(e.a['letter-spacing'] || 0) * s.length);
      const x = Number(e.a.x), y = Number(e.a.y), an = e.a['text-anchor'];
      const x0 = an === 'middle' ? x - wd / 2 : an === 'end' ? x - wd : x;
      texts.push({ s, f, x0, x1: x0 + wd, y0: y - 1.16 * f, y1: y + 0.29 * f });
    } else if (e.tag === 'path' && e.a['data-pipe'] !== undefined) {
      const tk = e.a.d.match(/[MHV]|-?[\d.]+/g); let x = 0, y = 0, i = 0;
      while (i < tk.length) { const c = tk[i++];
        if (c === 'M') { x = +tk[i++]; y = +tk[i++]; }
        else if (c === 'H') { const n = +tk[i++]; segs.push({ x0: Math.min(x, n), x1: Math.max(x, n), y0: y - .9, y1: y + .9 }); x = n; }
        else if (c === 'V') { const n = +tk[i++]; segs.push({ x0: x - .9, x1: x + .9, y0: Math.min(y, n), y1: Math.max(y, n) }); y = n; } }
    } else if (e.tag === 'rect') {
      rects.push({ x0: +e.a.x, y0: +e.a.y, x1: +e.a.x + +e.a.width, y1: +e.a.y + +e.a.height });
    }
  });
  const hit = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
  const bad = [];
  texts.forEach(t => { if (t.f < 13) bad.push('small ' + t.s); if (t.x0 < 0 || t.x1 > 600 || t.y0 < 0 || t.y1 > 507) bad.push('out ' + t.s); });
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++)
    if (hit(texts[i], texts[j])) bad.push('text/text ' + texts[i].s + ' / ' + texts[j].s);
  texts.forEach(t => segs.forEach(s => { if (hit(t, s)) bad.push('text/pipe ' + t.s); }));
  texts.forEach(t => rects.forEach(r => {
    if (hit(t, r) && !(t.x0 >= r.x0 + 1 && t.x1 <= r.x1 - 1 && t.y0 >= r.y0 + 1 && t.y1 <= r.y1 - 1)) bad.push('text/box ' + t.s);
  }));
  console.log(JSON.stringify({ bad, n: texts.length,
    shown: texts.map(t => t.s), weights: [...new Set(all.filter(e => e.tag === 'text').map(e => e.a['font-weight']))] }));
'''


def test_pid_text_no_overlap_and_labels():
    out = _schem(_TEXT)
    assert out["n"] > 40 and not out["bad"], out["bad"]
    assert set(out["weights"]) <= {"400", "700"}, out["weights"]
    shown = out["shown"]
    for s in ("PRECURSOR", "EXHAUST", "REACTANT", "→ Exhaust"):
        assert s in shown, s
    # 뜻 없는 띠(Stage · Powder Bed · RF Electrode)는 없앴다
    assert not {"Stage", "Powder Bed", "RF Electrode", "Stage / Chamber"} & set(shown)


def test_pid_legend_one_line_english():
    js = _src("js", "views", "schematic.js")
    leg = js[js.index("function legend()"):js.index("/* ===================== 그리기")]
    assert "T('s.open')" in leg and "T('s.closed')" in leg and "T('s.unknown')" in leg and "T('s.flow')" in leg
    assert "T('s.clickToOperate')" in leg and not HANGUL.search(leg.split("box2.innerHTML")[1])
    assert "s.idlePipe" not in leg and "s.exhaust" not in leg
    css = _src("css", "style.css")
    assert "#schemSvg [data-fp]:focus .fp-hit { stroke: transparent; }" in css      # 포인터를 올릴 때만(키보드는 focus-visible)


# ===================== C. 칸 넘침 =====================
def test_panels_and_tables_fit():
    html = _src("index.html")
    assert '<div class="panel" style="flex:0 0 540px">' in html                     # 두 장비 같은 배관도 칸 폭
    css = _src("css", "style.css")
    assert ".stl, .stl.c2 { grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); }" in css
    assert ".tblx { max-width: 100%; overflow-x: auto; }" in css
    setup = _src("js", "views", "setup.js")
    assert setup.count('<div class="tblx"><table class="tbl form">') == 2
    assert '<div class="tblx"><table class="tbl rc-steps">' in _src("js", "views", "recipe.js")
    main = _src("js", "views", "main.js")
    assert "return sti(c.label, c.key === 'base' ? 'warn' : 'stop', c.label + (det ? ' — ' + det : ''));" in main


# ===================== D1. 배관도 요소에서 Enter/Space 가 TypeError 를 내지 않는다 =====================
def test_d1_core_keydown_skips_svg_elements():
    js = _src("js", "core.js")
    assert "[tabindex=\"0\"]:not(input):not(button):not(select):not([data-fp])" in js
    assert "typeof ev.target.click === 'function'" in js


# ===================== D2 · D3. 조작 창 =====================
def test_d2_faceplate_rebuilds_button_set():
    st_locked = _state(live__manual={"unlocked": False, "valve_request": 0})
    st_open = _state()
    out = _fp(r'''
      const a = F.model('valve', 'PV-1', %s, false), b = F.model('valve', 'PV-1', %s, false);
      console.log(JSON.stringify([a.buttons.map(x => x.a), b.buttons.map(x => x.a)]));
    ''' % (json.dumps(st_locked), json.dumps(st_open)))
    assert out == [["open", "close", "unlock"], ["open", "close"]]
    js = _src("js", "views", "faceplate.js")
    assert 'data-fpbind="btns"' in js and "bb.getAttribute('data-set') !== want" in js and "bb.innerHTML = btnsHtml(m);" in js


def test_d3_permit_ng_blocks_on():
    kind, tag = ("rf", "RF Permit") if DEV.HAS_RF else ("o3", "O3 Permit")
    bit = next(k["bit"] for k in DEV.INTERLOCKS if k["tag"] == tag)
    ng, ok = _state(), _state(live__interlock=1 << bit)
    out = _fp(r'''
      const a = F.model('%s', 'X', %s, false), b = F.model('%s', 'X', %s, false);
      const on = m => m.buttons.filter(x => x.a === 'on')[0].on, off = m => m.buttons.filter(x => x.a === 'off')[0].on;
      console.log(JSON.stringify({ ng: [on(a), off(a), a.why], ok: [on(b), off(b), b.why] }));
    ''' % (kind, json.dumps(ng), kind, json.dumps(ok)))
    assert out["ng"][0] is False and out["ng"][1] is True and out["ng"][2].startswith(tag + " NG"), out
    assert out["ok"] == [True, True, ""], out


# ===================== D4. 남은 한글 라벨 =====================
def test_d4_no_korean_labels_left():
    cfg = C._deep_merge(C.DEFAULTS, {}) if hasattr(C, "_deep_merge") else dict(C.DEFAULTS)
    from peald.convert import Converters
    names = [n for n, _v in plclink.param_words(cfg, Converters(cfg)).values()]
    assert names and not [n for n in names if HANGUL.search(n)], names
    from peald import settings
    labels = {f["path"]: f["label"] for f in settings._F}
    assert "Base Pressure" in names and labels["params.base_press_torr"].startswith("Base Pressure")
    assert "PC Heartbeat Timeout" in names and labels["params.pc_wdt_ms"].startswith("PC Heartbeat Timeout")
    setup = _src("js", "views", "setup.js")
    for old in ("PIN 없음 — 처음 편집할 때 정합니다', 'warn')", "chip warn\">바꾸면 다시 시작해야 반영",
                "class=\"unconf\">없음<", "' — 값 없음", "'잠김'", "'바꾼 항목 없음'"):
        assert old not in setup, old
    assert "core.chip('No PIN', 'warn', 'PIN 없음 — 처음 편집할 때 정합니다')" in setup
    recipe = _src("js", "views", "recipe.js")
    assert "(블록 준비 " not in recipe and "(incl. Block Prep " in recipe
    assert "' — 값 없음'" not in _src("js", "views", "main.js")
    import inspect
    from peald import state as S
    assert '"감시 안 함"' not in inspect.getsource(S)


# ===================== D5. 다시 연 알람 이력 줄은 지금 이름 =====================
def test_d5_reopened_alarm_row_takes_current_name():
    now = datetime.datetime.now()
    p = _alarm_file(now, [[f"{now:%Y-%m-%d} 08:00:00", "발생", "A0-07", "베이스 도달 시간 초과", "경고"]])
    t = AlarmTracker()
    assert t.load_recent(now) == 1 and t.history[0]["cleared"] == ALARM_UNKNOWN
    d07 = next(d for d in DEV.ALARMS0 if d["bit"] == 7)
    assert t.update(1 << 7, 0) == ["A0-07"]
    assert t.history[0]["name"] == d07["name"] == t.active["A0-07"]["name"]
    assert not HANGUL.search(t.history[0]["name"])
    with open(p, encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.reader(f))) == 2                         # 파일은 그대로(새 줄 없음)


# ===================== D6. 옛 데이터 로그 값은 화면에서만 영문 =====================
def test_d6_old_datalog_values_shown_in_english():
    out = _node("const T = require(%s); console.log(JSON.stringify(%s.map(T.oldValue)));" % (
        json.dumps(os.path.join(JS, "terms.js")),
        json.dumps(["대기", "공정 준비", "공정 중", "블록 준비", "스텝 실행", "사이클 후 정지 중", "안전 정지",
                    "PLC 끊김", "PLC 멈춤", "Idle", "0.05", "메모"], ensure_ascii=False)))
    assert out == ["Idle", "Ready", "Running", "Block Prep", "Step Run", "Stopping after Cycle", "Safe Stop",
                   "PLC Lost", "PLC Stalled", "Idle", "0.05", "메모"]
    assert "core.esc(T.oldValue(c))" in _src("js", "views", "datalog.js")


# ===================== E2. 버전 =====================
def test_version_051():
    from peald import version
    # v0.6.1: startswith("0.5.") 는 0.6.0 에서 깨졌다 — test_v053 처럼 숫자 비교
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 5, 1)
