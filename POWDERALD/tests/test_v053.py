"""v0.5.3 — 상태 목록 문제 항목 먼저 · 인터락 상자 높이 · Heater 메모 · H2O 상자 · 768 배관도 크기 · PLC Auto 표시.

★ 화면의 순수 함수(terms.js · schematic.js)는 node 로 부른다(node 가 없으면 건너뜀). 브라우저 시험은 없다.
"""
import os
import re
import json

from powderald import device as DEV

from test_v0411 import _node, JS
from test_v0412 import _schem

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANGUL = re.compile("[가-힣]")


def _src(*parts):
    return open(os.path.join(ROOT, "frontend", *parts), encoding="utf-8").read()


# ===================== 2. 상태 목록 — 알람 → 경고 → 그 밖, 문제 항목은 늘 보임 =====================
def test_2_status_list_problems_first():
    items = [{"name": "A", "lvl": "ok"}, {"name": "B", "lvl": ""}, {"name": "C", "lvl": "warn"},
             {"name": "D", "lvl": "stop"}, {"name": "E", "lvl": "ok"}, {"name": "F", "lvl": "stop"},
             {"name": "G", "lvl": "warn"}]
    out = _node("const T = require(%s); console.log(JSON.stringify(T.stiOrder(%s).map(i => i.name)));" % (
        json.dumps(os.path.join(JS, "terms.js")), json.dumps(items)))
    assert out == ["D", "F", "C", "G", "A", "B", "E"]          # 같은 수준 안에서는 원래 순서


def test_2_all_status_lists_use_order_and_keep_problems():
    main = _src("js", "views", "main.js")
    # 인터락 목록 · RF(PEALD) / O3(Powder) 목록 모두 같은 규칙
    assert main.count("stiList(out)") == 2, main.count("stiList(out)")
    # v0.5.4: 인터락 목록은 높이 고정 칸이라 keepProblems 로 늘 수 없다 — 문제 항목만 + '+N OK'(stiList) 와 snapIlks 로
    assert "core.html(box, stiList(out));\n    snapIlks(box);\n  }" in main
    assert "chips.style.maxHeight = '';\n    core.html(chips, stiList(out));\n    keepProblems(chips);" in main
    # v0.5.4: 순서는 T.stiCompact 안의 T.stiOrder 가 정한다(알람 → 경고 → 그 밖)
    assert "var c = T.stiCompact(items);" in main
    # 문제 항목(빨강 · 노랑)이 차지하는 줄 수만큼은 줄 수 제한(max-height)보다 높이를 늘린다
    assert "box.querySelectorAll('.sti.stop, .sti.warn')" in main and "box.style.maxHeight = need + 'px'" in main
    assert "out.push(sti(" not in main and "return sti(k.name" not in main


# ===================== 3. 인터락 상자 높이 = 줄 높이(18)의 배수 =====================
def test_3_interlock_box_height_is_row_multiple():
    css = _src("css", "style.css")
    m = re.search(r"#view-main \.ilks\.stl \{[^}]*grid-auto-rows: (\d+)px;\s*min-height: (\d+)px;[^}]*row-gap: 0;", css)
    assert m, "인터락 목록 규칙"
    row, minh = int(m.group(1)), int(m.group(2))
    assert minh % row == 0 and minh // row >= 4, (row, minh)
    m = re.search(r"\[data-bind=\"devPanelBody\"\] \.stl \{[^}]*grid-auto-rows: (\d+)px; max-height: (\d+)px;[^}]*row-gap: 0;", css)
    assert m and int(m.group(2)) % int(m.group(1)) == 0, m and m.groups()
    main = _src("js", "views", "main.js")
    # v0.5.4: 자르는 높이는 늘(넘칠 때만이 아니라) 18 의 배수 — snapRows
    assert "function snapRows(h) { return Math.max(STI_ROW, Math.floor(h / STI_ROW) * STI_ROW); }" in main
    assert "var STI_ROW = 18;" in main


# ===================== 4. Heater 칸 메모는 영문 라벨 =====================
def test_4_heater_note_english():
    main = _src("js", "views", "main.js")
    m = re.search(r"core\.setText\('tcNote', ([^;]*);", main)
    assert m and not HANGUL.search(m.group(1)), m and m.group(1)
    assert "tn.title = miss.length ? 'PLC 한계에 0 을 써서 막습니다' : '';" in main


# ===================== 5. H2O 상자 안은 'H2O' 한 줄(PEALD) =====================
def test_5_h2o_box_single_line():
    out = _schem(r'''
      const shown = all.filter(e => e.tag === 'text').map(e => e.textContent);
      const titles = all.filter(e => e.tag === 'title').map(e => e.textContent);
      console.log(JSON.stringify({ shown, titles }));
    ''')
    assert "Canister R" not in out["shown"]
    if DEV.HAS_RF:
        assert "H2O" in out["shown"] and "H2O Canister R" in out["titles"]


# ===================== 6. 긴 창(768 폭)에서 늘어난 높이는 아래 줄이 받는다 =====================
# ★ v0.6.1(A2): v0.5.4 에서 일부러 바꾼 옛 구현 글자(maxHeight · flexGrow · 'if (hh <= CANVAS_H + 1) return;')를 찾지
#   않고, core.js 의 fit · rowFlex · fitMainRows · setTab 을 그대로 떼어 가짜 DOM 위에서 돌려 동작을 본다.
_FIT = r'''
  var CANVAS_W = 960, CANVAS_H = 1000, lastScale = 0, VIEWS = {}, lastState = null, curTab = 'main';
  function requestAnimationFrame() {}
  const mk = () => ({ style: {}, attrs: {}, classList: { toggle() {} },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }, setAttribute(k, v) { this.attrs[k] = String(v); } });
  const app = mk(), main = mk(), bottom = mk();
  bottom.style.flex = '0 0 auto';                       // index.html 의 아래 줄
  // 위 줄 높이 — 기본 높이 캔버스(1000)에서 재면 616, 늘어난 캔버스에서는 flex 가 나눈 값(다르게)
  Object.defineProperty(main, 'offsetHeight', { get() { return parseFloat(app.style.height) <= 1000 ? 616 : 777; } });
  var w = { innerWidth: 1280, innerHeight: 900 };
  var d = { getElementById: () => app, querySelectorAll: () => [],
            querySelector: s => s.indexOf(':not(.grow)') >= 0 ? bottom : main };
  %s
  %s
  %s
  %s
  const snap = () => ({ main: main.style.flex, bottom: bottom.style.flex, h: app.style.height });
  const out = {};
  fit(); out.base = snap();                                         // 1280×900 — 기본 높이
  w.innerWidth = 768; w.innerHeight = 1024; fit(); out.tall = snap(); // 768×1024 — 긴 캔버스
  // 다른 탭에 있는 동안 창이 바뀌었다가(옛 flex 가 남음) 운전 탭으로 돌아오면 다시 맞춘다
  w.innerWidth = 1280; w.innerHeight = 900; main.style.flex = 'stale'; bottom.style.flex = 'stale';
  curTab = 'trend'; setTab('main'); out.back = snap();
  console.log(JSON.stringify(out));
'''


def test_6_tall_canvas_gives_extra_height_to_bottom_row():
    from test_v054 import _fn
    core = _src("js", "core.js")
    out = _node(_FIT % tuple(_fn(core, n) for n in ("rowFlex", "fitMainRows", "fit", "setTab")))
    # 기본 높이(1280×900 등): 두 줄 flex 를 비운다(위 '' · 아래는 HTML 의 원래 값)
    assert out["base"] == {"main": "", "bottom": "0 0 auto", "h": "1000px"}, out
    # 긴 창: 위 줄은 기본 높이일 때의 높이(h0 = 616)로 고정, 아래 줄만 는다
    assert out["tall"] == {"main": "0 0 616px", "bottom": "1 1 auto", "h": "1280px"}, out
    # 운전 탭으로 돌아오면 다시 맞춘다
    assert out["back"] == {"main": "", "bottom": "0 0 auto", "h": "1000px"}, out
    assert '<svg id="schemSvg" preserveAspectRatio="xMidYMin meet">' in _src("index.html")   # 배관도 위 붙임


# ===================== 7. 'PV-B (PLC Auto)' — PV-B 이름 줄에 =====================
def test_7_plc_auto_on_pv_b_line():
    out = _schem(r'''
      const t = all.filter(e => e.tag === 'text');
      const name = t.find(e => e.textContent === 'PV-B'), note = t.find(e => e.a['data-vnote'] === 'PV-B');
      console.log(JSON.stringify({ alone: t.some(e => e.textContent === 'PLC Auto'),
        name: name ? [+name.a.x, +name.a.y] : null, note: note ? [note.textContent, +note.a.x, +note.a.y] : null }));
    ''')
    assert not out["alone"]                                    # 따로 떠 있는 'PLC Auto' 는 없다
    if DEV.HAS_O3:
        assert out["note"][0] == "(PLC Auto)" and out["note"][2] == out["name"][1], out     # 같은 줄
        assert out["name"][0] < out["note"][1] <= out["name"][0] + 40, out                   # 이름 바로 뒤
    else:
        assert out["name"] is None and out["note"] is None


def test_version_053():
    from powderald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 5, 3)   # v0.5.4: 버전이 오를 때 깨지지 않게
