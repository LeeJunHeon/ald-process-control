/* ============================================================
 * views/schematic.js — 배관도. config 로 자동 생성한다.
 *
 * ★ 라인 개수·종류·밸브 구성은 현장마다 다르다. 그림을 손으로 그려 두면 라인이 하나
 *   늘 때마다 코드를 고쳐야 한다 → 라인 kind 별 '행 템플릿'으로 그린다.
 *
 * 행 템플릿
 *   n2       : MFC ─ ALD ─ 매니폴드
 *   canister : MFC ─ IN ─ [캐니스터] ─ OUT ─ ALD ─ 매니폴드   (+ 위쪽 BYP 우회 배관)
 *   gas      : MFC ─ [발생기] ─ ALD ─ 매니폴드
 *
 * 겹침 방지 규칙(레이아웃 상수로 강제한다)
 *   - 역할 라벨(IN/OUT/BYP/ALD)은 밸브 아래 LBL_DY, 행 간격은 ROW_H.
 *     LBL_DY + 글자높이 < ROW_H - BYP_UP 이어야 위아래 행이 안 닿는다.
 *   - 색은 전부 CSS 변수(getComputedStyle)로 읽는다. 여기에 hex 를 쓰지 않는다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';

  /* ---- 레이아웃 상수 (전부 여기 모은다) ---- */
  var VB_W = 600, VB_H = 452;
  var X_ID = 6, X_MAT = 38, X_MFC = 98, MFC_W = 88, MFC_H = 30;
  var X_IN = 214, X_CAN = 250, CAN_W = 24, CAN_H = 22, X_OUT = 290, X_ALD = 372;
  var X_MANI = 400;
  var CH_L = 448, CH_R = 586, CH_T = 45, CH_B = 300;
  var ROW_Y0 = 30, ROW_H = 46, SIDE_GAP = 22;
  var LBL_DY = 15, BYP_UP = 20, VW = 7, VH = 6;
  var X_DOWN = 517;                    // 챔버 아래 배기 경로 중심
  var Y_TV = 326, Y_TRAP = 356, Y_RV = 396, Y_PUMP = 420;
  var X_VENT = 548, Y_VENT = 22;

  var rows = [];      // 계산된 행 정보(갱신 때 다시 쓴다)
  var svg = null;

  function css(name) {
    return getComputedStyle(d.documentElement).getPropertyValue(name).trim();
  }

  function el(tag, attrs) {
    var e = d.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
    return e;
  }

  function text(x, y, s, opt) {
    var t = el('text', {
      x: x, y: y, 'font-size': (opt && opt.size) || 9,
      'text-anchor': (opt && opt.anchor) || 'start',
      'font-weight': (opt && opt.weight) || 400,
      fill: (opt && opt.fill) || 'var(--ink-dim)',
      'font-family': 'var(--font-sans)'
    });
    t.textContent = s;
    return t;
  }

  /** 나비 밸브 아이콘. 열림은 채우고, 닫힘은 외곽선만 그린다. */
  function valve(tag, cx, cy, role) {
    var g = el('g', { 'data-valve': tag, style: 'cursor:pointer' });
    var p = el('path', {
      d: 'M' + (cx - VW) + ',' + (cy - VH) + ' L' + cx + ',' + cy + ' L' + (cx - VW) + ',' + (cy + VH) + ' Z ' +
         'M' + (cx + VW) + ',' + (cy - VH) + ' L' + cx + ',' + cy + ' L' + (cx + VW) + ',' + (cy + VH) + ' Z',
      'stroke-width': 1.4, 'data-vpath': tag
    });
    g.appendChild(p);
    if (role) g.appendChild(text(cx, cy + LBL_DY, role, { anchor: 'middle', size: 7.5, fill: 'var(--ink-faint)' }));
    var hit = el('rect', { x: cx - 11, y: cy - 11, width: 22, height: 22, fill: 'transparent' });
    g.appendChild(hit);
    return g;
  }

  function pipe(x1, y1, x2, y2, key) {
    return el('line', {
      x1: x1, y1: y1, x2: x2, y2: y2, 'stroke-width': 2,
      'stroke-linecap': 'round', 'data-pipe': key || '', stroke: 'var(--pipe)'
    });
  }

  function path(dstr, key, cls) {
    return el('path', {
      d: dstr, fill: 'none', 'stroke-width': 2, 'stroke-linecap': 'round',
      'data-pipe': key || '', stroke: 'var(--' + (cls || 'pipe') + ')'
    });
  }

  /* ===================== render ===================== */
  function render(s) {
    svg = d.getElementById('schemSvg');
    if (!svg) return;
    svg.setAttribute('viewBox', '0 0 ' + VB_W + ' ' + VB_H);
    svg.innerHTML = '';
    rows = [];

    var lines = s.lines || [];
    var pre = lines.filter(function (l) { return l.side === 'precursor'; });
    var re = lines.filter(function (l) { return l.side === 'reactant'; });

    var y = ROW_Y0;
    pre.forEach(function (ln) { rows.push(drawRow(ln, y)); y += ROW_H; });
    var preEnd = y - ROW_H;
    y += SIDE_GAP;
    var reStart = y;
    re.forEach(function (ln) { rows.push(drawRow(ln, y)); y += ROW_H; });
    var reEnd = y - ROW_H;

    drawManifold(pre, ROW_Y0, preEnd, CH_T + 28, 'P 라인');
    drawManifold(re, reStart, reEnd, CH_B - 48, 'R 라인');
    drawChamber(s);
    drawExhaust(s);
    drawLegend(s);
    update(s.live || {});
  }

  function drawRow(ln, cy) {
    var off = !ln.enabled;
    var info = { line: ln, cy: cy, off: off };

    svg.appendChild(text(X_ID, cy + 3.5, ln.id, {
      size: 11, weight: 700, fill: off ? 'var(--ink-faint)' : 'var(--ink)'
    }));
    svg.appendChild(text(X_MAT, cy + 3.5, off ? '미장착' : (ln.material || ''), {
      size: 10, fill: off ? 'var(--ink-faint)' : 'var(--ink-dim)'
    }));

    // MFC 박스 (SV/PV 는 update 에서 채운다)
    var bx = X_MFC, by = cy - MFC_H / 2;
    svg.appendChild(el('rect', {
      x: bx, y: by, width: MFC_W, height: MFC_H, rx: 4,
      fill: off ? 'transparent' : 'var(--panel-2)',
      stroke: off ? 'var(--line-2)' : 'var(--primary)',
      'stroke-width': 1, 'stroke-dasharray': off ? '3 3' : null
    }));
    if (off) {
      svg.appendChild(text(bx + MFC_W / 2, cy + 3, '—', { anchor: 'middle', size: 11, fill: 'var(--ink-faint)' }));
    } else {
      var pv = text(bx + 6, cy - 3, '0.0', { size: 11, weight: 700, fill: 'var(--pv)' });
      pv.setAttribute('data-mfcpv', ln.id);
      svg.appendChild(pv);
      svg.appendChild(text(bx + MFC_W - 5, cy - 3, 'sccm', { anchor: 'end', size: 7, fill: 'var(--ink-faint)' }));
      var sv = text(bx + 6, cy + 9, 'SV 0.0', { size: 8, weight: 600, fill: 'var(--sv)' });
      sv.setAttribute('data-mfcsv', ln.id);
      svg.appendChild(sv);
    }

    var v = ln.valves || {};
    var xEnd = X_MFC + MFC_W;

    if (ln.kind === 'canister') {
      svg.appendChild(pipe(xEnd, cy, X_IN - VW, cy, ln.id + ':a'));
      svg.appendChild(pipe(X_IN + VW, cy, X_CAN, cy, ln.id + ':b'));
      svg.appendChild(el('rect', {
        x: X_CAN, y: cy - CAN_H / 2, width: CAN_W, height: CAN_H, rx: 3,
        fill: ln.heated ? 'var(--canister)' : 'var(--panel-2)',
        stroke: ln.heated ? 'var(--canister-line)' : 'var(--line-2)', 'stroke-width': 1.2
      }));
      svg.appendChild(pipe(X_CAN + CAN_W, cy, X_OUT - VW, cy, ln.id + ':c'));
      svg.appendChild(pipe(X_OUT + VW, cy, X_ALD - VW, cy, ln.id + ':d'));
      if (v.IN) svg.appendChild(valve(v.IN, X_IN, cy, 'IN'));
      if (v.OUT) svg.appendChild(valve(v.OUT, X_OUT, cy, 'OUT'));
      if (v.BYP) {
        // 캐리어 우회 — MFC 출구에서 위로 올라가 OUT 뒤로 합류한다.
        var ytop = cy - BYP_UP, xj = X_OUT + 16;
        svg.appendChild(path('M' + (xEnd + 14) + ',' + cy + ' V' + ytop + ' H' + xj + ' V' + cy,
                             ln.id + ':byp'));
        svg.appendChild(valve(v.BYP, (xEnd + 14 + xj) / 2, ytop, 'BYP'));
      }
    } else if (ln.kind === 'gas') {
      var gw = 74, gx = X_CAN - 24;
      svg.appendChild(pipe(xEnd, cy, gx, cy, ln.id + ':a'));
      svg.appendChild(el('rect', {
        x: gx, y: cy - 11, width: gw, height: 22, rx: 4,
        fill: 'var(--panel-2)', stroke: 'var(--line-2)', 'stroke-width': 1
      }));
      svg.appendChild(text(gx + gw / 2, cy + 3.5, ln.generator || '발생기',
                           { anchor: 'middle', size: 9, fill: 'var(--ink-dim)' }));
      svg.appendChild(pipe(gx + gw, cy, X_ALD - VW, cy, ln.id + ':d'));
    } else {
      svg.appendChild(pipe(xEnd, cy, X_ALD - VW, cy, ln.id + ':a'));
    }

    if (v.ALD) svg.appendChild(valve(v.ALD, X_ALD, cy, 'ALD'));
    svg.appendChild(pipe(X_ALD + VW, cy, X_MANI, cy, ln.id + ':ald'));
    return info;
  }

  function drawManifold(lines, yTop, yBot, yJoin, label) {
    if (!lines.length) return;
    var lo = Math.min(yTop, yJoin), hi = Math.max(yBot, yJoin);
    svg.appendChild(pipe(X_MANI, lo, X_MANI, hi, 'mani:' + label));
    svg.appendChild(pipe(X_MANI, yJoin, CH_L, yJoin, 'manijoin:' + label));
    svg.appendChild(text(X_MANI + 6, yJoin - 5, label, { size: 8, fill: 'var(--ink-faint)' }));
  }

  function drawChamber(s) {
    var io = s.chamber_io || {}, g = s.gauges || {};
    svg.appendChild(el('rect', {
      x: CH_L, y: CH_T, width: CH_R - CH_L, height: CH_B - CH_T, rx: 6,
      fill: 'var(--chamber-bg)', stroke: 'var(--chamber-line)', 'stroke-width': 2
    }));
    svg.appendChild(text(CH_L + 12, CH_T + 22, '공정 챔버', { size: 12, weight: 700, fill: 'var(--ink)' }));
    var wall = text(CH_L + 12, CH_T + 36, '', { size: 8.5, fill: 'var(--ink-faint)' });
    wall.setAttribute('data-bind-svg', 'chWall');
    svg.appendChild(wall);

    var gy = CH_T + 68;
    [['baratron', g.baratron], ['convectron', g.convectron]].forEach(function (pair) {
      if (!pair[1]) return;
      svg.appendChild(text(CH_L + 12, gy, pair[1].label || pair[0], { size: 9, fill: 'var(--ink-faint)' }));
      var val = text(CH_L + 12, gy + 17, '—', { size: 15, weight: 700, fill: 'var(--primary)' });
      val.setAttribute('data-bind-svg', 'g:' + pair[0]);
      svg.appendChild(val);
      svg.appendChild(text(CH_L + 74, gy + 17, pair[1].unit || 'Torr', { size: 8, fill: 'var(--ink-faint)' }));
      gy += 46;
    });

    // 스테이지
    var st = text((CH_L + CH_R) / 2, CH_B - 42, '', { anchor: 'middle', size: 10, weight: 700, fill: 'var(--pv)' });
    st.setAttribute('data-bind-svg', 'chStage');
    svg.appendChild(st);
    svg.appendChild(el('rect', {
      x: CH_L + 26, y: CH_B - 34, width: CH_R - CH_L - 52, height: 10, rx: 3,
      fill: 'var(--stage)', stroke: 'var(--line-2)', 'stroke-width': 1
    }));
    svg.appendChild(el('rect', { x: (CH_L + CH_R) / 2 - 5, y: CH_B - 24, width: 10, height: 12, fill: 'var(--stage)' }));

    // 벤트 (챔버 위)
    var vent = (io.vent || {});
    svg.appendChild(text(X_VENT + 14, Y_VENT - 6, vent.label || 'Vent', { size: 8.5, fill: 'var(--ink-faint)' }));
    svg.appendChild(pipe(X_VENT, Y_VENT, X_VENT, CH_T, 'vent'));
    if (vent.tag) svg.appendChild(valve(vent.tag, X_VENT, Y_VENT + 14, ''));
    svg.appendChild(text(X_VENT - 12, Y_VENT + 18, 'VV', { anchor: 'end', size: 7.5, fill: 'var(--ink-faint)' }));
  }

  function drawExhaust(s) {
    var io = s.chamber_io || {};
    svg.appendChild(path('M' + X_DOWN + ',' + CH_B + ' V' + Y_PUMP, 'exh', 'exh'));

    // 스로틀 밸브(개도 %)
    svg.appendChild(el('circle', { cx: X_DOWN, cy: Y_TV, r: 11, fill: 'var(--panel)', stroke: 'var(--exh)', 'stroke-width': 1.6 }));
    svg.appendChild(el('line', { x1: X_DOWN - 7, y1: Y_TV + 6, x2: X_DOWN + 7, y2: Y_TV - 6, stroke: 'var(--exh)', 'stroke-width': 1.6 }));
    var tv = text(X_DOWN + 18, Y_TV + 3.5, '', { size: 9.5, weight: 600, fill: 'var(--ink-dim)' });
    tv.setAttribute('data-bind-svg', 'tv');
    svg.appendChild(tv);

    // Hot trap
    var tl = (io.hot_trap || {}).label || 'Hot trap';
    svg.appendChild(el('rect', { x: X_DOWN - 50, y: Y_TRAP - 11, width: 100, height: 22, rx: 4, fill: 'var(--panel-2)', stroke: 'var(--line-2)' }));
    var tt = text(X_DOWN, Y_TRAP + 3.5, tl, { anchor: 'middle', size: 9, fill: 'var(--ink-dim)' });
    tt.setAttribute('data-bind-svg', 'trap');
    svg.appendChild(tt);

    // 러핑 밸브
    var rv = io.rv || {};
    if (rv.tag) svg.appendChild(valve(rv.tag, X_DOWN, Y_RV, ''));
    svg.appendChild(text(X_DOWN + 16, Y_RV + 3.5, 'RV', { size: 8, fill: 'var(--ink-faint)' }));

    // Dry pump
    svg.appendChild(el('rect', { x: X_DOWN - 52, y: Y_PUMP, width: 104, height: 24, rx: 12, fill: 'var(--panel)', stroke: 'var(--line-2)' }));
    svg.appendChild(el('circle', { cx: X_DOWN - 34, cy: Y_PUMP + 12, r: 4, fill: 'var(--valve-closed)' })).setAttribute('data-bind-svg', 'pumpdot');
    var pt = text(X_DOWN + 4, Y_PUMP + 16, (io.dry_pump || {}).label || 'Dry pump', { anchor: 'middle', size: 9.5, weight: 600, fill: 'var(--ink)' });
    svg.appendChild(pt);
  }

  function drawLegend(s) {
    var box = core.bind('schemLegend');
    if (!box) return;
    box.innerHTML =
      '<span><svg width="16" height="10" style="vertical-align:-1px"><path d="M2,1 L8,5 L2,9 Z M14,1 L8,5 L14,9 Z" fill="var(--valve-open)"/></svg> 열림</span>' +
      '<span><svg width="16" height="10" style="vertical-align:-1px"><path d="M2,1 L8,5 L2,9 Z M14,1 L8,5 L14,9 Z" fill="none" stroke="var(--valve-closed)" stroke-width="1.2"/></svg> 닫힘</span>' +
      '<span><i style="background:var(--flow)"></i>N2 흐름</span>' +
      '<span><i style="background:var(--pipe)"></i>대기</span>' +
      '<span><i style="background:var(--exh)"></i>배기</span>' +
      '<span><i style="background:var(--canister);border:1px solid var(--canister-line)"></i>가열 캐니스터</span>';
  }

  /* ===================== update ===================== */
  function update(t) {
    if (!svg) return;
    var valves = t.valves || {}, mfc = t.mfc || {}, heat = t.heaters || {},
        gauges = t.gauges || {}, io = t.io || {};
    var s = core.state || {};

    // 밸브
    Array.prototype.forEach.call(svg.querySelectorAll('[data-vpath]'), function (p) {
      var on = !!valves[p.getAttribute('data-vpath')];
      p.setAttribute('fill', on ? 'var(--valve-open)' : 'none');
      p.setAttribute('stroke', on ? 'var(--valve-open)' : 'var(--valve-closed)');
    });

    // MFC 값
    Array.prototype.forEach.call(svg.querySelectorAll('[data-mfcpv]'), function (e) {
      var m = mfc[e.getAttribute('data-mfcpv')] || {};
      e.textContent = fmt.flow(m.pv);
    });
    Array.prototype.forEach.call(svg.querySelectorAll('[data-mfcsv]'), function (e) {
      var m = mfc[e.getAttribute('data-mfcsv')] || {};
      e.textContent = 'SV ' + fmt.flow(m.sv);
    });

    // 배관 흐름 색
    var sideOpen = { precursor: false, reactant: false };
    rows.forEach(function (r) {
      var ln = r.line, v = ln.valves || {}, on = !r.off && (mfc[ln.id] || {}).pv > 0.05;
      var aldOpen = !!valves[v.ALD];
      var inOpen = !!valves[v.IN], outOpen = !!valves[v.OUT], bypOpen = !!valves[v.BYP];
      var reachOut = ln.kind === 'canister' ? ((inOpen && outOpen) || bypOpen) : true;
      paint(ln.id + ':a', on);
      paint(ln.id + ':b', on && inOpen);
      paint(ln.id + ':c', on && inOpen && outOpen);
      paint(ln.id + ':byp', on && bypOpen);
      paint(ln.id + ':d', on && reachOut);
      paint(ln.id + ':ald', on && reachOut && aldOpen);
      if (aldOpen && on && reachOut) sideOpen[ln.side] = true;
    });
    paint('mani:P 라인', sideOpen.precursor); paint('manijoin:P 라인', sideOpen.precursor);
    paint('mani:R 라인', sideOpen.reactant);  paint('manijoin:R 라인', sideOpen.reactant);
    paint('vent', !!io.vent);

    // 챔버 값
    setSvg('g:baratron', fmt.torr(gauges.baratron));
    setSvg('g:convectron', fmt.torr(gauges.convectron));
    setSvg('tv', 'TV ' + fmt.pct(io.tv_pct) + ' %');
    setSvg('chStage', 'Stage  ' + fmt.temp((heat.stage || {}).pv) + ' °C');
    setSvg('chWall', labelOf(s, 'lid') + ' ' + fmt.temp((heat.lid || {}).pv) +
                     ' · ' + labelOf(s, 'wall') + ' ' + fmt.temp((heat.wall || {}).pv));
    var trap = (heat.hottrap || {});
    setSvg('trap', ((s.chamber_io || {}).hot_trap || {}).label + (trap.pv != null ? ' ' + fmt.temp(trap.pv) + ' °C' : ''));
    var dot = svg.querySelector('[data-bind-svg="pumpdot"]');
    if (dot) dot.setAttribute('fill', io.dry_pump ? 'var(--valve-open)' : 'var(--valve-closed)');
  }

  function labelOf(s, hid) {
    var h = (s.heaters || []).filter(function (x) { return x.id === hid; })[0];
    return h ? h.label : hid;
  }

  function paint(key, flowing) {
    Array.prototype.forEach.call(svg.querySelectorAll('[data-pipe="' + key + '"]'), function (e) {
      if (e.getAttribute('stroke') === 'var(--exh)') return;   // 배기 경로 색은 유지
      e.setAttribute('stroke', flowing ? 'var(--flow)' : 'var(--pipe)');
    });
  }

  function setSvg(key, v) {
    var e = svg.querySelector('[data-bind-svg="' + key + '"]');
    if (e) e.textContent = v;
  }

  /* ---- 밸브 클릭 → 수동 조작 ---- */
  d.addEventListener('click', function (ev) {
    var g = ev.target.closest('[data-valve]');
    if (!g) return;
    var tag = g.getAttribute('data-valve');
    if (!core.canOperate()) { core.toast('원격 접속은 보기 전용입니다', 'warn'); return; }
    if (core.isRunning()) { core.toast('공정 중에는 수동 조작을 할 수 없습니다', 'warn'); return; }
    var cur = ((core.state || {}).live || {}).valves || {};
    w.app.send('valve_toggle', { tag: tag, open: !cur[tag] });
  });

  w.viewSchematic = { render: render, update: update };
})(window, document);
