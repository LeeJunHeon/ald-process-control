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


# ===================== 2. 램프판 — 항목마다 칸 하나 · 레벨 클래스(v0.8.0) =====================
# ★ v0.8.0: 옛 '문제 항목 먼저' 상태 목록(stiOrder · stiList)은 고정 램프판(panels.js)으로 바꿨다 — 순서는 장비 구조 그대로이고
#   문제 항목은 색으로 말한다(모든 항목이 늘 보이므로 앞으로 모을 필요가 없다). 같은 의도(문제 항목이 늘 보임)를 램프판 렌더로 본다
_ANN = r'''
  global.window = global; global.T = require(%s); global.UI = require(%s);
  global.core = { esc: s => String(s), bind: () => null, html: () => {}, setTab() {} };
  global.document = { addEventListener() {} };
  require(%s);
  const S = %s;
  const lamps = [];
  const root = { set innerHTML(v) { this.h = v; }, classList: { toggle() {} },
    querySelectorAll() { (this.h.match(/data-lamp="/g) || []).forEach(() => lamps.push({ className: '' })); return lamps; } };
  Panels.ann.build(root, S, { cols: %d });
  Panels.ann.update(root, S, %s);
  console.log(JSON.stringify({ keys: (root.h.match(/data-lamp="([^"]+)"/g) || []).map(x => x.slice(11, -1)),
                               cls: lamps.map(l => l.className), html: root.h }));
'''


def _ann(cols, live):
    st = {"structure": {"annunciator": DEV.ANNUNCIATOR, "inputs0": DEV.INPUTS0, "inputs1": DEV.INPUTS1,
                        "interlocks": DEV.INTERLOCKS}}
    return _node(_ANN % (json.dumps(os.path.join(JS, "terms.js")), json.dumps(os.path.join(JS, "uimodel.js")),
                         json.dumps(os.path.join(JS, "views", "panels.js")), json.dumps(st), cols, json.dumps(live)))


def test_2_status_list_problems_first():
    """모든 항목이 칸 하나씩 — 알람 · 경고가 몇 개든 숨지 않는다(빨강 · 노랑 클래스)."""
    bad = _ann(8, {"plc": {"connected": True}, "inputs0": 0, "inputs1": 0xFFFF, "interlock": 0})
    items = [k for r in DEV.ANNUNCIATOR for k in r["items"]]
    assert bad["keys"] == items and len(bad["cls"]) == len(items)
    lv = dict(zip(items, bad["cls"]))
    assert lv["in0:EMO"] == "lamp alarm" and lv["ilk:Basic"] == "lamp warn" and lv["ilk:Safe Stop Request"] == "lamp off"
    assert not re.search(r"\+\d+ (OK|more)", bad["html"])                      # 묶음 칸 없음


def test_2_all_status_lists_use_order_and_keep_problems():
    """RF(PEALD) / O3(Powder) 상태도 같은 램프판 한 곳(장비 전용 칸의 칩 목록 · 인터락 목록 중복 없음)."""
    ok = _ann(6, {"plc": {"connected": True}, "inputs0": 0, "inputs1": 0, "interlock": 0xFFFF})
    keys = ok["keys"]
    tag = "ilk:RF Permit" if DEV.HAS_RF else "ilk:O3 Permit"
    assert keys.count(tag) == 1 and dict(zip(keys, ok["cls"]))[tag] == "lamp ok"
    main = _src("js", "views", "main.js")
    assert "buildDevPanel" not in main and "rfChips" not in main and "o3Chips" not in main


# ===================== 3. 램프판 높이 = 줄 수 × 22 + 사이(장비 구조로만) =====================
def test_3_interlock_box_height_is_row_multiple():
    css = _src("css", "style.css")
    m = re.search(r"\.anngrid \{ display: grid; grid-auto-rows: (\d+)px; row-gap: (\d+)px;", css)
    assert m and (int(m.group(1)), int(m.group(2))) == (22, 4), m and m.groups()
    assert ".ann-body { padding: 6px var(--sp-3); overflow: hidden; }" in css           # 칸 안 스크롤 없음
    from test_v054 import _fn
    main = _src("js", "views", "main.js")
    out = _node("const w = { UI: require(%s) }; var LAMP_H = 22, LAMP_GAP = 4, PANEL_HEAD = 30, PANEL_PAD = 12, BORDER = 2;\n%s\n"
                "const s = { structure: { annunciator: %s } };"
                "console.log(JSON.stringify([annH(s, 8), annH(s, 6), w.UI.annRows(s.structure, 8), w.UI.annRows(s.structure, 6)]));"
                % (json.dumps(os.path.join(JS, "uimodel.js")), _fn(main, "annH"), json.dumps(DEV.ANNUNCIATOR)))
    h8, h6, r8, r6 = out
    assert (r8, r6) == (4, 6)                                                            # 좁은 4 줄 · 넓은 6 줄(두 장비)
    assert h8 == 30 + 2 + 12 + r8 * 22 + (r8 - 1) * 4 and h6 == 30 + 2 + 12 + r6 * 22 + (r6 - 1) * 4


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


# ===================== 6. 긴 창(768 폭) — v0.8.0: 두 배치 fit =====================
# ★ v0.8.0: 운전 탭의 '위 줄 고정 · 아래 줄 늘림'(rowFlex · fitMainRows)은 없앴다 — 칸은 고정 격자이고 남는 높이는
#   Heater | MFC 줄(좁은) · Recent Events(넓은)가 받는다(CSS minmax(0, 1fr)). 같은 의도(긴 창에서 위 칸 높이가 그대로)를 본다
def test_6_tall_canvas_gives_extra_height_to_bottom_row():
    css = _src("css", "style.css")
    assert ".opgrid.narrow { grid-template-rows: 270px var(--ann-h) 64px minmax(0, 1fr);" in css
    out = _node("const UI = require(%s); console.log(JSON.stringify([UI.layoutBox('narrow', 768, 1024), UI.layoutBox('narrow', 1280, 900)]));"
                % json.dumps(os.path.join(JS, "uimodel.js")))
    assert out[0]["w"] == 960 and out[0]["h"] == 1280 and abs(out[0]["s"] - 0.8) < 1e-9     # 768×1024 — 긴 캔버스
    assert out[1]["w"] == 1200 and out[1]["h"] == 1000
    assert '<svg id="schemSvg" viewBox="0 0 920 680" preserveAspectRatio="xMidYMid meet">' in _src("index.html")


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
