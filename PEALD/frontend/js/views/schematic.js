/* ============================================================
 * views/schematic.js — 배관도(P&ID 탭, 인라인 SVG) (v0.8.0).
 *
 * ★ 그림은 장비별 모델(views/pid_model.js — window.PidModel)을 읽어 만든다. 이 파일은 두 장비 같은 파일이다.
 *   모델은 포트 기반(배관 끝 = 상자 포트 · 갈래 · 열린 끝)이고 검사기(js/pidcheck.js)가 규칙 11 가지를 본다 —
 *   끝과 끝이 맞지 않거나 배관이 상자를 지나는 그림은 시험에서 걸린다.
 * ★ 밸브 열림/닫힘은 D00010(실제 출력)을, VV·IV-E·IV-B·펌프는 D00014 를 본다. '요청'이 아니라 '실제 출력'.
 *   요청은 있는데 출력이 1 s 넘게 없는 것(서버 manual.pending — v0.8.2)은 노랑 점선(Pending). 끊김 · 멈춤이면 흐린 점선(Unknown).
 * ★ 색은 CSS 변수만. 글자는 13 단위 이상 · 굵기 400 · 700.
 * ★ 조작 가능한 요소(data-fp)를 누르면 그 옆에 조작 창(faceplate.js)이 뜬다. 누르기 영역 · Tab 순서는
 *   검사기와 같은 규칙(PidCheck.hitAreas) — 무리(Precursor → Reactant → Chamber → Exhaust) 다음 위 → 아래, 왼쪽 → 오른쪽.
 * ★ 그림만 쓰는 함수는 document.createElementNS · getElementById 와 core.register · bind · state · bit, fmt.* 만 쓴다
 *   (시험의 가짜 DOM 이 이것만 준다).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var G = 8;                    // 좌표 격자(모델과 같다)
  var FS = 13;                  // 글자 기본(그림 단위)
  var BOX_SW = 1, BOX_R = 3, PIPE_W = 1.8;
  var VW = 7, VH = 6;           // 밸브 아이콘 반폭 · 반높이(검사기 valveGeom 과 같다)

  var svg = null;
  var built = false;
  var model = null;

  function el(tag, a) {
    var e = d.createElementNS(NS, tag);
    for (var k in (a || {})) if (a[k] !== null && a[k] !== undefined) e.setAttribute(k, a[k]);
    return e;
  }

  var TONE = { ink: 'var(--ink)', dim: 'var(--ink-dim)', faint: 'var(--ink-faint)', sv: 'var(--sv)', warn: 'var(--warn)',
               pv: 'var(--pv)' };

  function text(parent, x, y, s, o) {
    o = o || {};
    var t = el('text', {
      x: x, y: y, 'font-size': o.size || FS, 'text-anchor': o.anchor || 'start', 'font-weight': o.weight || 400,
      fill: TONE[o.tone || (o.bind ? 'pv' : 'dim')], 'font-family': o.mono ? 'var(--font-mono)' : 'var(--font-sans)'
    });
    t.textContent = s;
    if (o.bind) t.setAttribute('data-sv', o.bind);
    if (o.spacing) t.setAttribute('letter-spacing', o.spacing);
    parent.appendChild(t);
    return t;
  }

  /** 툴팁(전체 이름)을 단 묶음 */
  function group(parent, title, attrs) {
    var g = el('g', attrs || {});
    if (title) {
      var t = el('title', {});
      t.textContent = title;
      g.appendChild(t);
    }
    parent.appendChild(g);
    return g;
  }

  function C() { return w.PidCheck; }

  /* ===================== 그리기 ===================== */
  function drawBox(b) {
    var g = group(svg, b.title || null);
    if (b.kind === 'pump') {
      var cx = b.x + b.w / 2, cy = b.y + b.h / 2;
      g.appendChild(el('circle', { cx: cx, cy: cy, r: b.w / 2, fill: 'var(--panel)', stroke: 'var(--line-2)', 'stroke-width': 1.4 }));
      g.appendChild(el('circle', { cx: cx, cy: cy, r: 5, fill: 'var(--valve-closed)', 'data-aux': b.aux }));
      return;
    }
    var look = b.kind === 'chamber' ? ['var(--chamber-bg)', 'var(--chamber-line)']
      : b.kind === 'vessel' ? ['var(--vessel)', 'var(--vessel-line)'] : ['var(--panel-2)', 'var(--line-2)'];
    g.appendChild(el('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: BOX_R, fill: look[0], stroke: look[1],
                               'stroke-width': BOX_SW }));
    var sub = { cm: null, unconf: null };
    (b.lines || []).forEach(function (ln) {
      var parent = g;
      if (ln.attr) {
        if (!sub[ln.attr]) sub[ln.attr] = group(g, null, ln.attr === 'cm' ? { 'data-cm': '1' } : { 'data-unconf': '1' });
        parent = sub[ln.attr];
      }
      if (ln.rule) {
        parent.appendChild(el('line', { x1: b.x + 16, y1: b.y + ln.y, x2: b.x + b.w - 16, y2: b.y + ln.y,
                                        stroke: 'var(--line)', 'stroke-width': 1 }));
        return;
      }
      text(parent, b.x + (ln.x == null ? 8 : ln.x), b.y + ln.y, ln.bind ? fmt.DASH : ln.text,
           { size: ln.size, weight: ln.weight, mono: ln.mono, anchor: ln.anchor, tone: ln.tone, bind: ln.bind });
    });
    if (b.dot) g.appendChild(el('circle', { cx: b.x + b.dot.x, cy: b.y + b.dot.y, r: 4, fill: 'var(--valve-closed)',
                                            'data-aux': b.dot.aux }));
  }

  function pathD(pp) {
    var pts = pp.path.map(function (p) { return C().point(model, p); });
    var dd = 'M' + pts[0].x + ',' + pts[0].y;
    for (var i = 1; i < pts.length; i++) dd += pts[i].y === pts[i - 1].y ? ' H' + pts[i].x : ' V' + pts[i].y;
    return { d: dd, pts: pts };
  }

  function drawPipe(pp) {
    var kind = pp.kind || 'pipe', pd = pathD(pp);
    svg.appendChild(el('path', { d: pd.d, fill: 'none', 'stroke-width': PIPE_W, 'stroke-linecap': 'butt',
                                 'stroke-linejoin': 'miter', stroke: 'var(--' + kind + ')',
                                 'data-pipe': pp.key, 'data-kind': kind }));
    if (pp.arrow) {
      var e = pd.pts[pd.pts.length - 1], q = pd.pts[pd.pts.length - 2];
      var dx = Math.sign(e.x - q.x), dy = Math.sign(e.y - q.y);
      var bx = e.x - dx * 8, by = e.y - dy * 8;
      svg.appendChild(el('path', { d: 'M' + (bx - dy * 4) + ',' + (by - dx * 4) + ' L' + e.x + ',' + e.y + ' L' +
                                      (bx + dy * 4) + ',' + (by + dx * 4) + ' Z',
                                   fill: 'var(--ink-faint)', 'data-arrow': pp.key }));
    }
  }

  /** 갈래 점(tee) — 흐름 색은 그 갈래를 처음 지나는 배관을 따른다(joint 는 점 없음) */
  function drawJunctions() {
    Object.keys(model.junctions || {}).forEach(function (k) {
      var j = model.junctions[k];
      if (j.kind !== 'tee') return;
      var owner = (model.pipes || []).filter(function (pp) { return pp.path.indexOf(k) >= 0; })[0] || {};
      var kind = owner.kind || 'pipe';
      svg.appendChild(el('circle', { cx: j.x, cy: j.y, r: 2.6, fill: 'var(--' + kind + ')', 'data-jn': owner.key || k,
                                     'data-kind': kind }));
    });
  }

  function drawValve(v) {
    var g = group(svg, null);
    var dd = v.o === 'v'
      ? 'M' + (v.x - VH) + ',' + (v.y - VW) + ' L' + v.x + ',' + v.y + ' L' + (v.x + VH) + ',' + (v.y - VW) + ' Z ' +
        'M' + (v.x - VH) + ',' + (v.y + VW) + ' L' + v.x + ',' + v.y + ' L' + (v.x + VH) + ',' + (v.y + VW) + ' Z'
      : 'M' + (v.x - VW) + ',' + (v.y - VH) + ' L' + v.x + ',' + v.y + ' L' + (v.x - VW) + ',' + (v.y + VH) + ' Z ' +
        'M' + (v.x + VW) + ',' + (v.y - VH) + ' L' + v.x + ',' + v.y + ' L' + (v.x + VW) + ',' + (v.y + VH) + ' Z';
    g.appendChild(el('path', { d: dd, 'stroke-width': 1.2, 'data-valve': v.tag, fill: 'none', stroke: 'var(--valve-closed)' }));
    var lab = C().valveGeom(v).label;
    if (v.o === 'v') {
      text(g, lab.x, v.y + 4.5, v.tag, { tone: 'faint' });
      if (v.note) {
        var nx = lab.x + Math.ceil(C().textW(v.tag + ' ', FS));
        var nt = text(g, nx, v.y + 4.5, '(' + v.note + ')', { tone: 'faint' });
        nt.setAttribute('data-vnote', v.tag);
      }
    } else {
      text(g, v.x, v.y - 15, v.tag, { tone: 'faint', anchor: 'middle' });
    }
  }

  function drawInline(v) {
    var g = group(svg, v.title || v.id);
    g.appendChild(el('circle', { cx: v.x, cy: v.y, r: v.r, fill: 'var(--panel)', stroke: 'var(--exh)', 'stroke-width': 1.4 }));
    g.appendChild(el('path', { d: 'M' + (v.x - 6) + ',' + (v.y + 6) + ' L' + (v.x + 6) + ',' + (v.y - 6),
                               stroke: 'var(--exh)', 'stroke-width': 1.4 }));
    if (v.label) drawLabel(v.label, g);
  }

  /** 글자 자리 — 상자(왼쪽 위 · 폭 · 높이 16) 안에. 이름 + 값(bind)은 이름 폭 + 12 뒤 */
  function drawLabel(l, parent) {
    parent = parent || svg;
    var y = l.y + 12.5, title = l.kind === 'title', name = l.kind === 'name';
    var s = title ? String(l.text).toUpperCase() : l.text;
    var o = { weight: (title || name) ? 700 : 400, tone: title ? 'faint' : name ? 'ink' : 'dim', spacing: title ? 0.5 : null };
    if (l.bind) {
      var nt = text(parent, l.x, y, s, o);
      var vx = l.x + Math.ceil(C().textW(s, FS)) + 12;
      var vt = text(parent, vx, y, fmt.DASH, { mono: true, bind: l.bind });
      if (typeof nt.getComputedTextLength === 'function') {
        try { var L = nt.getComputedTextLength(); if (L > 0) vt.setAttribute('x', Math.ceil(l.x + L + 12)); } catch (e) { /* 아직 안 그려짐 */ }
      }
      return;
    }
    if (l.anchor === 'end') { o.anchor = 'end'; text(parent, l.x + l.w, y, s, o); }
    else text(parent, l.x, y, s, o);
  }

  function hitLayer() {
    var layer = el('g', { 'data-hmi': '1' });
    svg.appendChild(layer);
    C().hitAreas(model).sort(function (a, b) { return (a.grp - b.grp) || (a.r.y - b.r.y) || (a.r.x - b.r.x); })
      .forEach(function (h) {
        var g = el('g', { 'data-fp': h.fp, 'data-grp': h.grp, tabindex: 0, role: 'button', 'aria-label': h.title,
                          'aria-haspopup': 'dialog' });
        var t = el('title', {});
        t.textContent = h.title + ' — click to operate';
        g.appendChild(t);
        g.appendChild(el('path', { 'class': 'fp-hit', d: 'M' + h.r.x + ',' + h.r.y + ' h' + h.r.w + ' v' + h.r.h + ' h' + (-h.r.w) + ' Z' }));
        layer.appendChild(g);
      });
  }

  function build() {
    model = w.PidModel;
    if (!model || !C()) return;
    svg.setAttribute('viewBox', '0 0 ' + model.W + ' ' + model.H);
    svg.innerHTML = '';
    (model.pipes || []).forEach(drawPipe);
    drawJunctions();
    (model.boxes || []).forEach(drawBox);
    (model.inline || []).forEach(drawInline);
    (model.valves || []).forEach(drawValve);
    (model.labels || []).forEach(function (l) { drawLabel(l); });
    hitLayer();
    built = true;
    legend();
  }

  /** 범례 — 영문 용어 + 한글 뜻(툴팁). P&ID 칸 머리 */
  var LEGEND = [
    ['s.open', 'valve-open', '', '밸브 출력 켜짐(열림)'],
    ['s.closed', 'closed', '', '밸브 출력 꺼짐(닫힘)'],
    ['s.pending', 'pending', '', '요청은 남아 있는데 출력이 1 s 넘게 꺼져 있음(허가 · 조건 대기) — 수동 밸브라면 PLC 가 지우지 않은 요청(래더 P50 확인)'],
    ['s.unknown', 'unknown', '', '연결이 끊겨 상태를 모름'],
    ['s.flow', 'flow', '', '가스가 흐르는 배관(MFC 유량 또는 열린 밸브)'],
    ['s.auto', 'auto', 'o3', 'PLC 가 자동으로 다루는 밸브(PV-B)']
  ];
  function legend() {
    var box = core.bind('pidLegend');
    if (!box) return;
    var dev = (core.state || {}).device || {};
    var sw = function (cls) { return '<i class="lg ' + cls + '"></i>'; };
    box.innerHTML = LEGEND.filter(function (x) { return !x[2] || (x[2] === 'o3' && dev.has_o3); }).map(function (x) {
      return '<span title="' + core.esc(x[3]) + '">' + sw(x[1]) + T(x[0]) + '</span>';
    }).join('') + '<span class="dim" title="밸브 · 상자를 누르면 그 옆에 조작 창이 뜹니다">' + T('s.clickToOperate') + '</span>';
  }

  /* ===================== render / update ===================== */
  function render(s) {
    svg = d.getElementById('schemSvg');
    if (!svg) return;
    if (!built) build();
    else legend();
    update(s.live || {});
  }

  function setSv(key, v) {
    var e = svg.querySelector('[data-sv="' + key + '"]');
    if (e && e.textContent !== v) e.textContent = v;
  }

  function paint(key, on, kind) {
    Array.prototype.forEach.call(svg.querySelectorAll('[data-pipe="' + key + '"]'), function (e) {
      var base = e.getAttribute('data-kind') || 'pipe';
      e.setAttribute('stroke', on ? 'var(--flow)' : 'var(--' + (kind || base) + ')');
    });
    Array.prototype.forEach.call(svg.querySelectorAll('[data-jn="' + key + '"]'), function (e) {
      var base = e.getAttribute('data-kind') || 'pipe';
      e.setAttribute('fill', on ? 'var(--flow)' : 'var(--' + (kind || base) + ')');
    });
  }

  function setValve(tag, open) {
    var p = svg.querySelector('[data-valve="' + tag + '"]');
    if (!p) return;
    p.setAttribute('fill', open ? 'var(--valve-open)' : 'none');
    p.setAttribute('stroke', open ? 'var(--valve-open)' : 'var(--valve-closed)');
  }

  function markPending(tag, pending) {
    var p = svg.querySelector('[data-valve="' + tag + '"]');
    if (!p) return;
    if (pending) {
      p.setAttribute('stroke', 'var(--warn)');
      p.setAttribute('fill', 'var(--warn-bg)');
      p.setAttribute('stroke-dasharray', '3 2');
    } else {
      p.removeAttribute('stroke-dasharray');
    }
  }

  function inBit(str, tag) {
    var k = (str.inputs0 || []).filter(function (x) { return x.tag === tag; })[0];
    return k ? k.bit : -1;
  }

  function update(t) {
    if (!svg || !built) return;
    var st = core.state || {};
    var str = st.structure || {};
    var conn = !!(t.plc && t.plc.connected);
    svg.classList.toggle('unknown', !conn);

    // --- 밸브 (D00010 실제 출력) · 보조 출력 (D00014) ---
    var pend = conn ? ((t.manual || {}).pending || []).concat((t.manual || {}).aux_pending || []) : [];
    var vmap = {}, amap = {};
    (str.valves || []).forEach(function (v) {
      var on = conn && t.valves != null && core.bit(t.valves, v.bit);
      vmap[v.tag] = on;
      setValve(v.tag, on);
      markPending(v.tag, !on && pend.indexOf(v.tag) >= 0);
    });
    (str.aux || []).forEach(function (a) {
      var on = conn && t.aux != null && core.bit(t.aux, a.bit);
      amap[a.tag] = on;
      setValve(a.tag, on);
      markPending(a.tag, !on && pend.indexOf(a.tag) >= 0);
    });
    Array.prototype.forEach.call(svg.querySelectorAll('[data-aux]'), function (dot) {
      dot.setAttribute('fill', amap[dot.getAttribute('data-aux')] ? 'var(--valve-open)' : 'var(--valve-closed)');
    });

    // --- 값 ---
    var mfc = t.mfc || [];
    (str.mfc || []).forEach(function (md) {
      var m = mfc[md.no - 1] || {};
      setSv('mfc' + md.no + 'pv', conn ? fmt.flow(m.pv) : fmt.DASH);
      setSv('mfc' + md.no + 'sv', 'SP ' + (conn ? fmt.flow(m.sv) : fmt.DASH) + ' sccm');
    });
    var heaters = t.heaters || [];
    for (var ch = 1; ch <= 6; ch++) {
      var hh = heaters[ch - 1] || {};
      setSv('h' + ch, conn && hh.enabled && hh.comm_ok !== false ? fmt.temp(hh.pv) + ' ℃' : fmt.DASH);
    }
    var h1 = heaters[0] || {};
    setSv('h1sv', conn && h1.enabled ? fmt.temp(h1.sv) + ' ℃' : fmt.DASH);
    var p = t.pressure || {};
    setSv('cvg', conn ? fmt.torr(p.cvg) : fmt.DASH);
    setSv('cm', conn && p.cm_installed ? fmt.torr(p.cm) : fmt.DASH);
    var cmg = svg.querySelector('[data-cm]');
    if (cmg) cmg.style.display = (p && p.cm_installed) ? '' : 'none';
    var ung = svg.querySelector('[data-unconf]');
    if (ung) ung.style.display = (st.unconfirmed || []).indexOf('CVG Pressure') >= 0 ? '' : 'none';

    var ex = t.extra || {};
    setSv('pcv', conn ? fmt.pct(ex.pcv_pv) + ' %' : fmt.DASH);
    setSv('rf', conn ? (ex.rf_on ? fmt.watt(ex.rf_fwd) + ' W (Ref ' + fmt.watt(ex.rf_ref) + ')' : T('s.off')) : fmt.DASH);
    setSv('o3', conn ? (ex.o3_on ? fmt.num(ex.o3_pv, 1) + ' ' + (ex.o3_unit || '') : T('s.off')) : fmt.DASH);
    var lb = inBit(str, 'LID');
    setSv('lid', conn && t.inputs0 != null && lb >= 0 ? (core.bit(t.inputs0, lb) ? T('s.closed') : T('s.open')) : fmt.DASH);

    // --- 흐름 색(없는 배관은 건너뛴다) ---
    var f = [0, 1, 2, 3].map(function (i) { return conn && (mfc[i] || {}).pv > 1; });
    paint('pline', f[0] || vmap['PV-1'] || vmap['PV-2'] || vmap['PV-3']);
    paint('aline', f[1]);
    paint('canPV-1', !!vmap['PV-1']);
    paint('canPV-2', !!vmap['PV-2']);
    paint('canPV-3', !!vmap['PV-3']);
    paint('asstPV-A1', f[1] && vmap['PV-A1']);
    paint('asstPV-A2', f[1] && vmap['PV-A2']);
    paint('rPV-R1', f[2] && vmap['PV-R1']);
    paint('rPV-R2', !!vmap['PV-R2']);
    paint('rPV-R3', f[3] && vmap['PV-R3']);
    paint('o2in', !!ex.o3_on);
    paint('o3out', !!ex.o3_on && (vmap['PV-R'] || vmap['PV-B']));
    paint('toChamberR', !!vmap['PV-R']);
    paint('byp', !!vmap['PV-B']);
    paint('byp2', !!vmap['PV-B'] && !!amap['IV-B']);
    paint('byp3', !!amap.BPMP, 'exh');
    paint('vv', !!amap.VV);
    paint('rfline', !!ex.rf_on);
    paint('exh', !!amap['IV-E'], 'exh');
    paint('exh2', !!amap['IV-E'] && !!amap.PMP, 'exh');
    paint('exh3', !!amap.PMP, 'exh');
    if (w.viewFaceplate) w.viewFaceplate.update();
  }

  core.register('schematic', { render: render, update: update });
  w.viewSchematic = { render: render, update: update, grid: G };
})(window, document);
