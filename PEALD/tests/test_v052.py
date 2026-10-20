"""v0.5.2 — 인터락 목록 겹침 · PRM 표 · 범례 · 배관도 세부(EXHAUST · O3 · O2 · 위로 붙임 · 누르기 영역 · 탭 순서) · 남은 한글.

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


# ===================== A1. 램프판 — 줄 높이 ≥ 글자 높이 · 이름이 안 잘림(v0.8.0) =====================
def test_a1_interlock_rows_fit_text():
    """램프 이름(T.textPx(이름, 12)) + 점 · 여백이 그 열 폭 안 — 좁은 8 열 · 넓은 6 열, 열 폭은 CSS(56 px 묶음 열 + 이름으로
    정해지는 열) · 배치 폭에서 계산한다. 줄 높이 ≥ 1.4 × 글자."""
    css = _src("css", "style.css")
    m = re.search(r"\.anngrid \{ display: grid; grid-auto-rows: (\d+)px; row-gap: (\d+)px; column-gap: (\d+)px; \}", css)
    assert m, "램프판 규칙"
    row, rgap, cgap = (int(x) for x in m.groups())
    fs = int(re.search(r"--fs-sm:\s*(\d+)px", _src("css", "tokens.css")).group(1))
    assert row >= fs * 1.4, (row, fs)                     # 줄 높이 ≥ 글자 줄 높이(눌려 겹치지 않게)
    lamp = re.search(r"\.lamp \{ display: flex; align-items: center; gap: (\d+)px; height: (\d+)px; padding: 0 (\d+)px 0 (\d+)px;", css)
    assert lamp and int(lamp.group(2)) == row, "램프 규칙"
    dot = 8
    extra = dot + int(lamp.group(1)) + int(lamp.group(3)) + int(lamp.group(4)) + 2          # 점 · 사이 · 좌우 여백 · 테두리
    names = {r["group"]: [] for r in DEV.ANNUNCIATOR}
    defs = {("in0:" + k["tag"]): k for k in DEV.INPUTS0}
    defs.update({("in1:" + k["tag"]): k for k in DEV.INPUTS1})
    defs.update({("ilk:" + k["tag"]): k for k in DEV.INTERLOCKS})
    rows = [[defs[i].get("lamp") for i in r["items"]] for r in DEV.ANNUNCIATOR]
    out = _node("const T = require(%s); console.log(JSON.stringify(%s.map(r => r.map(n => T.textPx(n, %d)))));" % (
        json.dumps(os.path.join(JS, "terms.js")), json.dumps(rows), fs))
    # 배치마다 램프판 몸통 폭: 좁은 = 960 − 뷰 여백 32 − 테두리 2 − 몸통 여백 16,
    #   넓은 = 오른쪽 열((1600 − 32 − 6) × 1 / 2.09) − 2 − 16 (P&ID 넓은 오른쪽 열은 이보다 넓다)
    for cols, avail in ((8, 960 - 32 - 2 - 16), (6, (1600 - 32 - 6) / 2.09 - 2 - 16)):
        colw = [0] * cols
        for r in out:
            for i, px in enumerate(r):
                colw[i % cols] = max(colw[i % cols], px + extra)
        need = 56 + sum(colw) + cols * cgap
        assert need <= avail, (cols, need, avail)             # 모든 이름이 열 안에 — 말줄임 없음
    assert ".lamp.alarm { color: var(--stop); background: var(--stop-bg); border-color: var(--stop-line); }" in css


def test_a2_a4_a5_tables_and_kv():
    setup = _src("js", "views", "setup.js")
    assert '<div class="tblx" style="margin-top:8px"><table class="tbl prmtbl">' in setup
    css = _src("css", "style.css")
    assert ".kv .k { white-space: nowrap; flex: 0 0 auto;" in css
    assert ".rc-steps th.vh { padding: 2px 2px; white-space: nowrap; }" in css


# ===================== A3. 그래프 범례 — 두 줄까지, 넘치면 '+N' =====================
def test_a3_legend_two_lines_then_more():
    css = _src("css", "style.css")
    assert ".legend-inline { max-height: 30px; line-height: 15px;" in css           # 두 줄(15 × 2)
    assert ".chartpanel .panel-head { height: auto; min-height: 30px;" in css
    js = _src("js", "views", "trend.js")
    assert "fitLegend(box);" in js and "more.textContent = '+' + hid.length;" in js and "more.title = hid.join" in js


# ===================== A6. 남은 한글 라벨 =====================
def test_a6_no_korean_labels_left():
    main = _src("js", "views", "main.js")
    assert "'레시피를 고르세요'" not in main and "'Select a recipe'" in main
    setup = _src("js", "views", "setup.js")
    assert '<span class="dim">개수는 장비 고정</span>' not in setup and ">Fixed by Device<" in setup
    html = _src("index.html")
    notes = re.findall(r'<span class="note"[^>]*>([^<]*)</span>', html)
    assert notes and not [n for n in notes if HANGUL.search(n)], notes
    sch = _src("js", "views", "schematic.js")
    assert "누르면 조작 창" not in sch and "' — click to operate'" in sch


# ===================== B1 · B12. 'EXHAUST' 제목은 배기 배관 왼쪽, 닿지 않는다 =====================
_TITLES = r'''
  const W = { ' ': .3, '.': .3 };
  const tw = (s, f) => [...s].reduce((a, ch) => a + (W[ch] || (/[A-Z]/.test(ch) ? .72 : .6)), 0) * f * 1.1;   // 넉넉히
  const segs = [];
  all.filter(e => e.tag === 'path' && e.a['data-pipe'] !== undefined).forEach(p => {
    const tk = p.a.d.match(/[MHV]|-?[\d.]+/g); let x = 0, y = 0, i = 0;
    while (i < tk.length) { const c = tk[i++];
      if (c === 'M') { x = +tk[i++]; y = +tk[i++]; }
      else if (c === 'H') { const n = +tk[i++]; segs.push([Math.min(x, n), y, Math.max(x, n), y, p.a['data-pipe']]); x = n; }
      else if (c === 'V') { const n = +tk[i++]; segs.push([x, Math.min(y, n), x, Math.max(y, n), p.a['data-pipe']]); y = n; } }
  });
  // 무리 제목(대문자 · 글자 사이)과 배관 사이 여유 4 단위 이상 — 글자 폭은 넉넉히(× 1.1) 어림한다
  const bad = [], titles = [];
  all.filter(e => e.tag === 'text' && e.a['letter-spacing']).forEach(e => {
    const f = +e.a['font-size'], s = e.textContent, wd = tw(s, f) + s.length * +e.a['letter-spacing'];
    const x = +e.a.x, y = +e.a.y, an = e.a['text-anchor'];
    const x0 = an === 'end' ? x - wd : an === 'middle' ? x - wd / 2 : x, x1 = x0 + wd, y0 = y - 1.16 * f, y1 = y + .29 * f;
    titles.push({ s, x, anchor: an });
    segs.forEach(g => { if (x0 - 4 < g[2] && g[0] < x1 + 4 && y0 - 4 < g[3] && g[1] < y1 + 4) bad.push(s + ' / ' + g[4]); });
  });
  console.log(JSON.stringify({ bad, titles }));
'''


def test_b1_exhaust_title_left_of_pipe():
    out = _schem(_TITLES)
    assert not out["bad"], out["bad"]
    ex = [t for t in out["titles"] if t["s"] == "EXHAUST"]
    # v0.8.0: 배기 배관(챔버 아래 x 744) 왼쪽에 오른쪽 맞춤 — 글자 끝이 배관 x 보다 왼쪽
    exh = _schem(r'''console.log(JSON.stringify(+all.find(e => e.a && e.a['data-pipe'] === 'exh').a.d.match(/M(\d+)/)[1]));''')
    assert len(ex) == 1 and ex[0]["anchor"] == "end" and ex[0]["x"] < exh, (out["titles"], exh)


def test_b12_check_catches_old_exhaust_position():
    # 배기 배관 위로 옮기면(배관 x 744 에서 왼쪽 맞춤) 같은 검사가 배기 배관과 닿음을 잡는다
    body = _TITLES.replace("  // 무리 제목", "  all.filter(e => e.textContent === 'EXHAUST').forEach(e => { e.a.x = '730'; "
                           "e.a.y = '404'; e.a['text-anchor'] = 'start'; });\n  // 무리 제목")
    out = _schem(body)
    assert any(b.startswith("EXHAUST / exh") for b in out["bad"]), out


# ===================== B2 · B3. Powder O3 발생기 점 · O2 공급 =====================
def test_b2_b3_o3_generator_dot_and_o2_supply():
    out = _schem(r'''
      const g = all.find(e => e.tag === 'circle' && e.a['data-aux'] === 'O3GEN');
      const name = all.find(e => e.tag === 'text' && e.textContent === 'O3 Generator');
      const val = all.find(e => e.tag === 'text' && e.a['data-sv'] === 'o3');
      const o2 = all.find(e => e.tag === 'path' && e.a['data-pipe'] === 'o2in');
      const arrow = all.find(e => e.a && e.a['data-arrow'] === 'o2in');
      const box = all.filter(e => e.tag === 'rect').find(r => name && +r.a.x <= +name.a.x && +name.a.x < +r.a.x + +r.a.width &&
                                                          +r.a.y <= +name.a.y && +name.a.y < +r.a.y + +r.a.height);
      console.log(JSON.stringify({ has: !!g, dot: g && [+g.a.cx, +g.a.cy], name: name && [+name.a.x, +name.a.y],
        val: val && [+val.a.x, +val.a.y], o2: o2 && o2.a.d, arrow: !!arrow,
        box: box && [+box.a.x, +box.a.y, +box.a.width, +box.a.height] }));
    ''')
    if not DEV.HAS_O3:
        assert not out["has"] and out.get("o2") is None          # v0.5.3: 없는 키는 JSON 에 안 실린다(KeyError 고침)
        return
    # 점은 제목 줄이 아니라 값 줄 — 값 바로 왼쪽
    assert abs(out["dot"][1] - (out["val"][1] - 4)) <= 1 and out["dot"][1] > out["name"][1] + 10, out
    assert out["dot"][0] < out["val"][0] <= out["dot"][0] + 12, out
    # O2 는 왼쪽 바깥(모델의 열린 끝 o2)에서 발생기 왼쪽 면으로 가로로 들어온다 + 화살표
    m = re.fullmatch(r"M(\d+),(\d+) H(\d+)", out["o2"])
    assert m, out["o2"]
    x0, y, x1 = (int(v) for v in m.groups())
    bx, by, bw, bh = out["box"]
    o2 = _schem("console.log(JSON.stringify(PidModel.terminals.o2));")
    assert (x0, y) == (o2["x"], o2["y"]) and x1 == bx and by < y < by + bh and out["arrow"], out


# ===================== B6. 밸브 누르기 영역은 이름 글자까지 =====================
def test_b6_valve_hit_area_covers_name():
    out = _schem(r'''
      const hits = {}; all.filter(e => e.tag === 'g' && e.a['data-fp'] && e.a['data-fp'].startsWith('valve:')).forEach(g => {
        const m = g.kids.find(k => k.tag === 'path').a.d.match(/M(-?[\d.]+),(-?[\d.]+) h(-?[\d.]+) v(-?[\d.]+)/);
        hits[g.a['data-fp'].slice(6)] = m.slice(1).map(Number); });
      const names = {}; all.filter(e => e.tag === 'text' && hits[e.textContent]).forEach(t => {
        const n = t.textContent, f = +t.a['font-size'], wd = n.length * .62 * f, x = +t.a.x, an = t.a['text-anchor'];
        names[n] = [an === 'middle' ? x - wd / 2 : x, +t.a.y - f * .8, an === 'middle' ? x + wd / 2 : x + wd, +t.a.y]; });
      console.log(JSON.stringify({ hits, names }));
    ''')
    assert len(out["hits"]) >= 6
    for tag, (x, y, w, h) in out["hits"].items():
        nx0, ny0, nx1, ny1 = out["names"][tag]
        assert w >= 18 and h >= 18, (tag, w, h)
        # 이름 글자 대부분(어림 폭의 80 %)과 글자 높이가 누르기 영역 안
        cover = max(0, min(x + w, nx1) - max(x, nx0)) / (nx1 - nx0)
        assert cover >= 0.8 and y <= ny0 + 2 and ny1 <= y + h + 2, (tag, (x, y, w, h), out["names"][tag], cover)


# ===================== B7. Tab 순서 — 무리 다음 위 → 아래 · 왼쪽 → 오른쪽 =====================
def test_b7_tab_order_by_group():
    out = _schem(r'''
      const fp = all.filter(e => e.tag === 'g' && e.a['data-fp']).map(e => {
        const m = e.kids.find(k => k.tag === 'path').a.d.match(/M(-?[\d.]+),(-?[\d.]+)/);
        return [+e.a['data-grp'], +m[2], +m[1], e.a['data-fp']]; });
      console.log(JSON.stringify(fp));
    ''')
    grps = [r[0] for r in out]
    assert grps == sorted(grps) and set(grps) == {0, 1, 2, 3}, grps
    for a, b in zip(out, out[1:]):
        assert a[:3] <= b[:3], (a, b)
    keys = [r[3] for r in out]
    assert keys[0] == "mfc:1" and keys.index("heater:1") > keys.index("valve:PV-R") > keys.index("valve:PV-1")
    assert keys[-1] == "pump:PMP"


# ===================== B4 · B5 · B8 · B9 · B11 =====================
def test_b4_b5_b8_b9_b11_details():
    assert '<svg id="schemSvg" viewBox="0 0 920 680" preserveAspectRatio="xMidYMid meet">' in _src("index.html")
    sch = _src("js", "views", "schematic.js")
    assert "getComputedTextLength" in sch and "w.PidModel" in sch                       # 그림은 모델에서
    out = _schem(r'''
      const dots = all.filter(e => e.tag === 'circle' && e.a['data-jn'] !== undefined).map(e => [+e.a.cx, +e.a.cy]);
      const kinds = {}; PidModel.boxes.forEach(b => { (kinds[b.kind] = kinds[b.kind] || []).push([b.w, b.h]); });
      // 갈래 하나에 닿는 배관 끝 수(네 갈래 없음)
      const ends = {}; PidModel.pipes.forEach(p => p.path.forEach((q, i) => { if (typeof q === 'string' && PidModel.junctions[q])
        ends[q] = (ends[q] || 0) + (i === 0 || i === p.path.length - 1 ? 1 : 2); }));
      console.log(JSON.stringify({ dots, kinds, ends }));
    ''')
    assert out["dots"] and max(out["ends"].values()) == 3, out["ends"]                  # 세 갈래까지(네 갈래 아님)
    for kind in ("mfc", "pump"):
        assert len({tuple(x) for x in out["kinds"][kind]}) == 1, (kind, out["kinds"])   # 같은 종류 상자는 같은 크기
    cans = [x for x in out["kinds"]["vessel"] if x == [112, 72]]
    assert len(cans) == 3                                                                # 캐니스터 셋 같은 크기
    tok = _src("css", "tokens.css")
    dark = dict(re.findall(r"--([\w-]+):\s*([^;]+);", tok.split(':root[data-theme="dark"]')[1]))

    def lum(h):
        h = h.strip().split()[0].lstrip("#")
        return sum(int(h[i:i + 2], 16) for i in (0, 2, 4)) / 3
    assert lum(dark["valve-closed"]) - lum(dark["pipe"]) >= 40                           # 닫힌 밸브가 배관과 구분된다


def test_version_052():
    from peald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 5, 2)   # v0.5.3: 버전이 오를 때 깨지지 않게
