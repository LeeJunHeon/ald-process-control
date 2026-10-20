"""v0.5.4 — 768 운전 탭 줄 높이 · 인터락 목록 문제 항목만 + N OK.

★ v0.8.0: 운전 탭은 두 배치 고정 격자(core.js fit — uimodel.js 의 pickLayout · layoutBox)로 바뀌었다. 1 번은 새 fit 의
  동작(긴 창 · 처음 state 뒤 다시 fit)으로 옮기고, '+N OK' 묶음(2 번)은 그 기능을 없애 지웠다 — 모든 항목이 늘 보인다는
  것은 test_v080(test_no_grouping_and_no_measuring_in_operation_parts · 램프판 · 시작 조건 시험)이 본다.
★ _fn(소스에서 함수 떼기)은 여러 시험이 쓴다 — 그대로 둔다.
"""
import os
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


# ===================== 1. 긴 캔버스(768 폭) — v0.8.0: 두 배치 fit =====================
_FIT = r'''
  const UI = require(%s);
  var VIEWS = {}, lastState = null; const renders = [];
  function requestAnimationFrame() {}
  const app = { style: { setProperty(k, v) { this[k] = v; } } };
  const w = { innerWidth: 1280, innerHeight: 900, UI: UI };
  const d = { getElementById: () => app, documentElement: { a: {}, setAttribute(k, v) { this.a[k] = v; } } };
  %s
  %s
  const snap = () => ({ layout: d.documentElement.a['data-layout'], w: app.style.width, h: app.style.height,
                        zoom: Math.round(app.style.zoom * 1000) / 1000 });
  const out = {};
  fit(); out.base = snap();                                        // 1280×900
  w.innerWidth = 768; w.innerHeight = 1024; fit(); out.tall = snap(); // 768×1024 — 긴 캔버스(좁은 배치)
  w.innerWidth = 1280; w.innerHeight = 720; fit(); out.wide = snap(); // 1280×720 — 넓은 배치(높이 900 까지 채움)
  console.log(JSON.stringify(out));
'''


def _fit_src():
    core = _src("js", "core.js")
    i = core.index("  var curLayout = '';")
    return core[i:core.index("  function fit()", i)], _fn(core, "fit")


def test_1_row_flex_fixes_top_row_and_grows_bottom():
    """v0.8.0: 긴 창(768×1024)은 좁은 배치 · 캔버스 높이만 는다(960×1280) — 위 칸(270 · 램프판 · 64)은 고정 px 이라
    늘어난 높이는 Heater | MFC 줄(minmax(0, 1fr))이 받는다."""
    head, fit = _fit_src()
    out = _node(_FIT % (json.dumps(os.path.join(JS, "uimodel.js")), head, fit))
    assert out["base"] == {"layout": "narrow", "w": "1200px", "h": "1000px", "zoom": 0.9}, out
    assert out["tall"] == {"layout": "narrow", "w": "960px", "h": "1280px", "zoom": 0.8}, out
    assert out["wide"] == {"layout": "wide", "w": "1600px", "h": "900px", "zoom": 0.8}, out
    css = _src("css", "style.css")
    assert ".opgrid.narrow { grid-template-rows: 270px var(--ann-h) 64px minmax(0, 1fr);" in css


def test_1_fit_main_rows_uses_flex_and_refits_after_first_state():
    core = _src("js", "core.js")
    assert "fitMainRows" not in core and "rowFlex" not in core                 # v0.8.0: 운전 탭 줄 늘이기는 없앴다
    # 첫 상태를 그린 뒤 한 번 더 잰다(표가 그려지기 전 높이가 아니라) — 그대로
    assert "if (!fitAfterState) { fitAfterState = true; fit(); }" in core
    i = core.index("function applyState(s)")
    assert core.index("fitAfterState = true; fit();", i) > core.index("VIEWS[k].render(s)", i)
    assert "w.UI.pickLayout(w0, h0, curLayout)" in _fn(core, "fit")


def test_version_054():
    from peald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 5, 4)   # v0.6.0: 버전이 오를 때 깨지지 않게
