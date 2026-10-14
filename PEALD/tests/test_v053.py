"""v0.5.3 — 상태 목록 문제 항목 먼저 · 인터락 상자 높이 · Heater 메모 · H2O 상자 · 768 배관도 크기 · PLC Auto 표시.

★ 화면의 순수 함수(terms.js · schematic.js)는 node 로 부른다(node 가 없으면 건너뜀). 브라우저 시험은 없다.
"""
import os
import re
import json

from peald import device as DEV

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
def test_6_tall_canvas_gives_extra_height_to_bottom_row():
    core = _src("js", "core.js")
    assert "fitMainRows(app, hh);" in core
    body = core[core.index("function fitMainRows(app, hh) {"):]
    body = body[:body.index("\n  }\n") + 4]
    assert "if (hh <= CANVAS_H + 1) return;" in body                         # 기본 높이(1280×900 등)는 그대로
    assert "main.style.maxHeight = h0 + 'px';" in body and "bottom.style.flexGrow = '1';" in body
    assert "if (name === 'main') fit();" in core
    assert '<svg id="schemSvg" preserveAspectRatio="xMidYMin meet">' in _src("index.html")   # 위 붙임 유지


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
    from peald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 5, 3)   # v0.5.4: 버전이 오를 때 깨지지 않게
