"""v0.5.4 — 768 운전 탭 줄 높이 · 인터락 목록 문제 항목만 + N OK.

★ 화면의 순수 함수(core.js 의 rowFlex · main.js 의 snapRows · terms.js 의 stiCompact)는 node 로 부른다
  (node 가 없으면 건너뜀). 브라우저 시험은 없다.
"""
import os
import re
import json

from test_v0411 import _node, JS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts):
    return open(os.path.join(ROOT, "frontend", *parts), encoding="utf-8").read()


def _fn(src, name):
    """소스에서 함수 하나를 떼어 낸다(본문 끝 = 같은 들여쓰기의 '}')."""
    i = src.index("  function " + name + "(")
    line = src[i:src.index("\n", i)]
    if line.rstrip().endswith("}"):                 # 한 줄 함수
        return line
    j = src.index("\n  }\n", i)
    return src[i:j + 4]


# ===================== 1. 긴 캔버스(768 폭) — 위 줄은 기준 높이로 고정, 아래 줄만 는다 =====================
_FLEX = r'''
  %s
  // 세로 flex 배치 계산(남는 높이를 grow 비율로 나눈다 · 모자라면 shrink) — 브라우저와 같은 규칙의 간단판
  function layout(H, items) {
    const p = items.map(it => { const f = (it.flex || it.css).trim().split(/\s+/);
      const basis = f[2] === 'auto' ? it.content : parseFloat(f[2]);
      return { g: +f[0], s: +f[1], b: basis, min: it.min || 0 }; });
    const free = H - p.reduce((a, x) => a + x.b, 0);
    const gs = p.reduce((a, x) => a + x.g, 0), ss = p.reduce((a, x) => a + x.s * x.b, 0);
    return p.map(x => Math.max(x.min, free >= 0 ? x.b + (gs ? free * x.g / gs : 0) : x.b + (ss ? free * x.s * x.b / ss : 0)));
  }
  const MAIN_CSS = '1 1 0px', BOTTOM0 = '0 0 auto';     // style.css 의 .row.grow · index.html 의 아래 줄
  const out = {};
  // 기본 높이(1280×900 · 1024×768 · 1280×720): 손대지 않는다
  out.base = rowFlex(1000, 1000, 616, BOTTOM0);
  // 768×900 · 768×1024: 캔버스 1125 · 1280, 위 줄은 기본 높이일 때의 616 으로 고정
  for (const hh of [1125, 1280]) {
    const f = rowFlex(hh, 1000, 616, BOTTOM0);
    const H = hh - 100;                                    // 머리 · 탭 · 여백(어림)
    const [m, b] = layout(H, [{ flex: f.main, css: MAIN_CSS, content: 616 }, { flex: f.bottom, css: BOTTOM0, content: 250 }]);
    out['h' + hh] = { f, main: m, bottom: b, H };
  }
  // 못 잰 때(운전 탭이 숨어 h0 = 0)는 원래대로
  out.zero = rowFlex(1125, 1000, 0, BOTTOM0);
  console.log(JSON.stringify(out));
'''


def test_1_row_flex_fixes_top_row_and_grows_bottom():
    core = _src("js", "core.js")
    out = _node(_FLEX % _fn(core, "rowFlex"))
    assert out["base"] == {"main": "", "bottom": "0 0 auto"}
    assert out["zero"] == {"main": "", "bottom": "0 0 auto"}
    for hh in ("h1125", "h1280"):
        r = out[hh]
        assert r["f"] == {"main": "0 0 616px", "bottom": "1 1 auto"}, r
        assert r["main"] == 616 and abs(r["main"] + r["bottom"] - r["H"]) < 1e-6, r      # 늘어난 높이는 아래 줄만
        assert r["bottom"] > 250, r


def test_1_fit_main_rows_uses_flex_and_refits_after_first_state():
    core = _src("js", "core.js")
    body = _fn(core, "fitMainRows")
    assert "main.style.flex = f.main;" in body and "bottom.style.flex = f.bottom;" in body
    assert "maxHeight" not in body and "flexGrow" not in body          # v0.5.3 방식(max-height · flexGrow)은 없다
    # 되돌릴 때 아래 줄은 HTML 의 원래 값(0 0 auto)으로 — '' 이면 기본 shrink 1 이 되어 1280 화면이 바뀐다
    assert "bottom.setAttribute('data-flex0', bottom.style.flex || '')" in body
    assert '<div class="row" style="flex:0 0 auto">' in _src("index.html")
    # 첫 상태를 그린 뒤 한 번 더 잰다(표가 그려지기 전 높이가 아니라)
    assert "if (!fitAfterState) { fitAfterState = true; fit(); }" in core
    i = core.index("function applyState(s)")
    assert core.index("fitAfterState = true; fit();", i) > core.index("VIEWS[k].render(s)", i)


# ===================== 2. 문제 항목만 + '+N OK' · 높이 18 의 배수 =====================
def test_2_compact_list_problems_only_plus_ok():
    items = [{"name": "EMO OK", "lvl": "ok"}, {"name": "Air", "lvl": "warn"}, {"name": "Lid", "lvl": ""},
             {"name": "Pump Alarm", "lvl": "stop"}, {"name": "N2", "lvl": "warn"}, {"name": "Heater Overtemp Trip", "lvl": "stop"}]
    calm = [{"name": "A", "lvl": "ok"}, {"name": "B", "lvl": ""}]
    out = _node("const T = require(%s); const f = c => ({ items: c.items.map(i => i.name), ok: c.ok.map(i => i.name) });"
                "console.log(JSON.stringify([f(T.stiCompact(%s)), f(T.stiCompact(%s))]));" % (
                    json.dumps(os.path.join(JS, "terms.js")), json.dumps(items), json.dumps(calm)))
    assert out[0] == {"items": ["Pump Alarm", "Heater Overtemp Trip", "Air", "N2"], "ok": ["EMO OK", "Lid"]}
    assert out[1] == {"items": ["A", "B"], "ok": []}                    # 문제가 없으면 전체


def test_2_main_renders_plus_ok_and_snaps_rows():
    main = _src("js", "views", "main.js")
    sl = _fn(main, "stiList")
    assert "var c = T.stiCompact(items);" in sl
    assert "<i></i>+' + c.ok.length + ' OK</span>'" in sl and 'data-okall="' in sl and 'title="' in sl
    assert "class=\"sti more\"" in sl                                     # 회색(정상 묶음)
    assert "core.toast('OK — ' + m.getAttribute('data-okall'), 'info')" in main        # 누르면 전체 이름
    # 인터락 · RF · O3 목록 모두 같은 stiList
    assert main.count("stiList(out)") == 2
    # 상자의 실제 높이를 18 의 배수로 내려 맞춘다(넘칠 때만이 아니라 늘)
    out = _node("var STI_ROW = 18;\n%s\nconsole.log(JSON.stringify([10, 18, 45, 72, 100, 191].map(snapRows)));" % _fn(main, "snapRows"))
    assert out == [18, 18, 36, 72, 90, 180]
    snap = _fn(main, "snapIlks")
    assert "var h = box.clientHeight;" in snap and "box.style.maxHeight = snapRows(h) + 'px';" in snap
    assert "scrollHeight" not in snap
    css = _src("css", "style.css")
    assert ".sti.more > i { background: var(--off); }" in css


def test_version_054():
    from powderald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 5, 4)   # v0.6.0: 버전이 오를 때 깨지지 않게
