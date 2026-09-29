/* ============================================================
 * views/schematic.js — Powder ALD 배관도 (인라인 SVG).
 *
 * 왼쪽 공급 → 가운데 분말 반응기 → 오른쪽·아래 배기 순서로 읽히게 그린다.
 *
 *   [전구체]  MFC1 ─ 전구체 라인(CH2) ─ 챔버
 *             라인 아래에 캐니스터 3개
 *               캐니스터1(1port) ─PV-1→ 라인
 *               캐니스터2(2port, CH4): MFC2 ─PV-A1→ 입구, 출구 ─PV-2→ 라인
 *               캐니스터3(2port, CH5): MFC2 ─PV-A2→ 입구, 출구 ─PV-3→ 라인
 *   [반응물]  O3 발생기(O2 공급) ─PV-R→ 챔버
 *             발생기 출구에서 갈라져 ─PV-B─ IV-B → 바이패스 로터리펌프 → 배기
 *             ★ PV-B 는 PLC 가 자동으로 연다(발생기 운전 중 PV-R 이 닫혀 있으면 열림).
 *               수동·레시피로 쓰지 않는다.
 *   [챔버]    분말 반응기(CH1), CVG(+CM), VV(N2 벤트)
 *   [배기]    챔버 ─ IV-E ─ 포집 트랩(CH6) ─ 전용 펌프 → 배기
 *
 * ★ 밸브 열림/닫힘은 D00010(실제 출력)을, VV·IV-E·펌프·IV-B·바이패스 펌프·O3 발생기는
 *   D00014 를 본다. '요청'이 아니라 '실제 출력'을 그려야 화면과 장비가 어긋나지 않는다.
 * ★ 색은 CSS 변수만 쓴다. 이 파일에 hex 를 쓰지 않는다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var VB_W = 600, VB_H = 507;   // 배관도 패널 비율(602×509)에 맞춘다

  // 밸브 아이콘 크기
  var VW = 6, VH = 5;

  var svg = null;
  var built = false;

  /* ---------- 작은 빌더 ---------- */
  function el(tag, a) {
    var e = d.createElementNS(NS, tag);
    for (var k in (a || {})) if (a[k] !== null && a[k] !== undefined) e.setAttribute(k, a[k]);
    return e;
  }

  function add(e) { svg.appendChild(e); return e; }

  function txt(x, y, s, o) {
    o = o || {};
    var t = el('text', {
      x: x, y: y, 'font-size': o.size || 8.5,
      'text-anchor': o.anchor || 'start',
      'font-weight': o.weight || 400,
      fill: o.fill || 'var(--ink-dim)',
      'font-family': o.mono ? 'var(--font-mono)' : 'var(--font-sans)'
    });
    t.textContent = s;
    if (o.bind) t.setAttribute('data-sv', o.bind);
    return add(t);
  }

  /** 배관. key 를 주면 update() 에서 흐름 색을 바꾼다. */
  function pipe(dstr, key, kind) {
    return add(el('path', {
      d: dstr, fill: 'none', 'stroke-width': 1.8, 'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      stroke: 'var(--' + (kind || 'pipe') + ')',
      'data-pipe': key || '', 'data-kind': kind || 'pipe'
    }));
  }

  /** 나비 밸브. 열림은 채우고 닫힘은 외곽선만. */
  function valve(tag, cx, cy, label, below) {
    var g = add(el('g', {}));
    g.appendChild(el('path', {
      d: 'M' + (cx - VW) + ',' + (cy - VH) + ' L' + cx + ',' + cy + ' L' + (cx - VW) + ',' + (cy + VH) + ' Z ' +
         'M' + (cx + VW) + ',' + (cy - VH) + ' L' + cx + ',' + cy + ' L' + (cx + VW) + ',' + (cy + VH) + ' Z',
      'stroke-width': 1.2, 'data-valve': tag
    }));
    if (label !== false) {
      var t = el('text', {
        x: cx, y: below ? cy + 13 : cy - 9, 'font-size': 7.5, 'text-anchor': 'middle',
        fill: 'var(--ink-faint)', 'font-family': 'var(--font-sans)'
      });
      t.textContent = tag;
      g.appendChild(t);
    }
    return g;
  }

  function box(x, y, wd, ht, o) {
    o = o || {};
    return add(el('rect', {
      x: x, y: y, width: wd, height: ht, rx: o.r === undefined ? 3 : o.r,
      fill: o.fill || 'var(--panel-2)',
      stroke: o.stroke || 'var(--line-2)',
      'stroke-width': o.sw || 1,
      'stroke-dasharray': o.dash || null,
      'data-fillbind': o.fillbind || null
    }));
  }

  /** MFC 상자: 이름 · 현재값 / 설정값 */
  function mfcBox(x, y, no, name) {
    box(x, y, 70, 30, { stroke: 'var(--primary)' });
    txt(x + 4, y + 11, 'MFC' + no + ' ' + name, { size: 7, fill: 'var(--ink-faint)' });
    txt(x + 4, y + 24, '—', { size: 10, weight: 700, fill: 'var(--pv)', mono: true, bind: 'mfc' + no + 'pv' });
    txt(x + 66, y + 24, '—', { size: 7, fill: 'var(--sv)', anchor: 'end', mono: true, bind: 'mfc' + no + 'sv' });
  }

  /** 가열 용기(캐니스터·트랩): 온도 채널을 함께 보여 준다 */
  function vessel(x, y, wd, ht, label, ch) {
    box(x, y, wd, ht, {
      fill: ch ? 'var(--vessel)' : 'var(--vessel-cold)',
      stroke: ch ? 'var(--vessel-line)' : 'var(--line-2)'
    });
    txt(x + wd / 2, y + ht / 2 - 1, label, { size: 8, anchor: 'middle', fill: 'var(--ink)', weight: 600 });
    if (ch) txt(x + wd / 2, y + ht / 2 + 10, '—', { size: 8, anchor: 'middle', mono: true,
                                                    fill: 'var(--pv)', bind: 'h' + ch });
  }

  /* ===================== 그리기 ===================== */
  function build() {
    svg.setAttribute('viewBox', '0 0 ' + VB_W + ' ' + VB_H);
    svg.innerHTML = '';

    // --- 좌표 격자 (전부 여기 모은다. 겹침은 이 숫자만 고쳐서 잡는다) ---
    var CH_L = 330, CH_R = 480, CH_T = 60, CH_B = 320;   // 분말 반응기
    var P_Y = 55;        // 전구체 라인
    var A_Y = 185;       // 어시스트 라인
    var CAN_T = 100, CAN_H = 40;
    var O3_Y = 290;      // O3 라인
    var EX_X = 405;      // 배기 중심

    /* ---------- 전구체 ---------- */
    txt(6, 18, '전구체', { size: 9.5, weight: 700, fill: 'var(--ink)' });
    mfcBox(6, 40, 1, '전구체 캐리어');
    pipe('M76,' + P_Y + ' H' + CH_L, 'pline');
    txt(322, P_Y - 7, '전구체 라인', { size: 7.5, anchor: 'end' });
    txt(322, P_Y + 14, '—', { size: 8, anchor: 'end', mono: true, fill: 'var(--pv)', bind: 'h2' });

    var cans = [
      { x: 92,  out: 117, tag: 'PV-1', label: '캐니스터1', ch: 0 },
      { x: 162, out: 200, tag: 'PV-2', label: '캐니스터2', ch: 4, inlet: 172, atag: 'PV-A1' },
      { x: 232, out: 270, tag: 'PV-3', label: '캐니스터3', ch: 5, inlet: 242, atag: 'PV-A2' }
    ];
    cans.forEach(function (c) {
      vessel(c.x, CAN_T, 50, CAN_H, c.label, c.ch);
      pipe('M' + c.out + ',' + CAN_T + ' V' + P_Y, 'can' + c.tag);
      valve(c.tag, c.out, 78, true);
      if (c.inlet) {
        pipe('M' + c.inlet + ',' + A_Y + ' V' + (CAN_T + CAN_H), 'asst' + c.atag);
        valve(c.atag, c.inlet, 162, true, true);
      }
    });

    mfcBox(6, 170, 2, '어시스트');
    pipe('M76,' + A_Y + ' H250', 'aline');
    txt(258, A_Y + 3, '어시스트 N2', { size: 7.5 });

    /* ---------- 반응물 (O3 라인) ---------- */
    txt(6, 238, '반응물 (O3)', { size: 9.5, weight: 700, fill: 'var(--ink)' });
    txt(6, 256, 'O2 공급', { size: 7.5 });
    pipe('M44,260 V' + (O3_Y - 18), 'o2in');
    box(6, O3_Y - 18, 76, 36, { stroke: 'var(--line-2)' });
    txt(46, O3_Y - 4, 'O3 발생기', { size: 8, anchor: 'middle', weight: 600, fill: 'var(--ink)' });
    txt(46, O3_Y + 10, '—', { size: 8, anchor: 'middle', mono: true, fill: 'var(--pv)', bind: 'o3' });
    add(el('circle', { cx: 14, cy: O3_Y + 12, r: 3.5, fill: 'var(--valve-closed)',
                       'data-aux': 'O3GEN' }));

    // 발생기 → 챔버
    pipe('M82,' + O3_Y + ' H' + CH_L, 'toChamberR');
    valve('PV-R', 180, O3_Y, true);

    // 발생기 출구에서 갈라지는 바이패스 (PLC 자동)
    pipe('M120,' + O3_Y + ' V378', 'byp');
    valve('PV-B', 120, 334, true, true);
    txt(131, 356, 'PLC 자동', { size: 6.5, fill: 'var(--ink-faint)' });
    pipe('M120,378 H210', 'byp2');
    valve('IV-B', 170, 378, true, true);
    box(214, 364, 96, 28, { r: 13, fill: 'var(--panel)' });
    add(el('circle', { cx: 228, cy: 378, r: 4, fill: 'var(--valve-closed)', 'data-aux': 'BPMP' }));
    txt(268, 381, '바이패스 펌프', { size: 8, anchor: 'middle', weight: 600, fill: 'var(--ink)' });
    pipe('M310,378 H344', 'byp3', 'exh');
    txt(348, 381, '→ 배기', { size: 7.5 });

    /* ---------- 챔버 ---------- */
    box(CH_L, CH_T, CH_R - CH_L, CH_B - CH_T, {
      fill: 'var(--chamber-bg)', stroke: 'var(--chamber-line)', sw: 2, r: 6
    });
    txt(CH_L + 10, CH_T + 20, '분말 반응기', { size: 11, weight: 700, fill: 'var(--ink)' });
    txt(CH_L + 10, CH_T + 34, '챔버', { size: 7.5 });
    txt(CH_L + 42, CH_T + 34, '—', { size: 7.5, mono: true, bind: 'lid' });

    txt(CH_L + 10, CH_T + 62, '챔버 온도', { size: 7.5 });
    txt(CH_L + 10, CH_T + 78, '—', { size: 11, weight: 700, mono: true, fill: 'var(--pv)', bind: 'h1' });

    txt(CH_L + 10, CH_T + 120, 'CVG', { size: 7.5 });
    txt(CH_L + 10, CH_T + 137, '—', { size: 12, weight: 700, mono: true,
                                      fill: 'var(--primary)', bind: 'cvg' });
    txt(CH_L + 10, CH_T + 149, 'Torr', { size: 7, fill: 'var(--ink-faint)' });
    var cmg = add(el('g', { 'data-cm': '1' }));
    var t1 = el('text', { x: CH_L + 80, y: CH_T + 120, 'font-size': 7.5, fill: 'var(--ink-dim)',
                          'font-family': 'var(--font-sans)' });
    t1.textContent = 'CM';
    cmg.appendChild(t1);
    var t2 = el('text', { x: CH_L + 80, y: CH_T + 137, 'font-size': 11, 'font-weight': 700,
                          fill: 'var(--primary)', 'font-family': 'var(--font-mono)', 'data-sv': 'cm' });
    t2.textContent = '—';
    cmg.appendChild(t2);

    // 분말 베드
    add(el('rect', { x: CH_L + 20, y: CH_B - 34, width: (CH_R - CH_L) - 40, height: 14, rx: 3,
                     fill: 'var(--stage)', stroke: 'var(--line-2)', 'stroke-width': .8 }));
    txt(CH_L + 10, CH_B - 38, '분말 베드', { size: 6.5, fill: 'var(--ink-faint)' });

    // VV (N2 벤트)
    pipe('M560,' + (CH_T + 30) + ' H' + CH_R, 'vv');
    txt(566, CH_T + 33, 'N2 벤트', { size: 7.5 });
    valve('VV', 522, CH_T + 30, true);

    /* ---------- 배기 ---------- */
    pipe('M' + EX_X + ',' + CH_B + ' V462', 'exh', 'exh');
    valve('IV-E', EX_X, 348, false);
    txt(EX_X + 16, 351, 'IV-E 배기 격리', { size: 7.5 });
    vessel(EX_X - 48, 386, 96, 32, '포집 트랩', 6);
    box(EX_X - 48, 445, 96, 26, { r: 13, fill: 'var(--panel)' });
    add(el('circle', { cx: EX_X - 32, cy: 458, r: 4, fill: 'var(--valve-closed)',
                       'data-aux': 'PMP' }));
    txt(EX_X + 8, 461, '전용 펌프', { size: 8.5, anchor: 'middle', weight: 600, fill: 'var(--ink)' });
    txt(EX_X + 56, 461, '→ 배기', { size: 7.5 });

    built = true;
    legend();
  }

  function legend() {
    var box2 = core.bind('schemLegend');
    if (!box2) return;
    box2.innerHTML =
      '<span><svg width="14" height="9" style="vertical-align:-1px">' +
      '<path d="M2,1 L7,4.5 L2,8 Z M12,1 L7,4.5 L12,8 Z" fill="var(--valve-open)"/></svg> 열림</span>' +
      '<span><svg width="14" height="9" style="vertical-align:-1px">' +
      '<path d="M2,1 L7,4.5 L2,8 Z M12,1 L7,4.5 L12,8 Z" fill="none" stroke="var(--valve-closed)" stroke-width="1"/></svg> 닫힘</span>' +
      '<span><i style="background:var(--flow)"></i>흐름</span>' +
      '<span><i style="background:var(--pipe)"></i>대기</span>' +
      '<span><i style="background:var(--exh)"></i>배기</span>' +
      '<span><i style="background:var(--vessel);border:1px solid var(--vessel-line)"></i>가열</span>';
  }

  /* ===================== render / update ===================== */
  function render(s) {
    svg = d.getElementById('schemSvg');
    if (!svg) return;
    if (!built) build();
    update(s.live || {});
  }

  function setSv(key, v) {
    var e = svg.querySelector('[data-sv="' + key + '"]');
    if (e) e.textContent = v;
  }

  function paint(key, on, kind) {
    Array.prototype.forEach.call(svg.querySelectorAll('[data-pipe="' + key + '"]'), function (e) {
      var base = e.getAttribute('data-kind') || 'pipe';
      e.setAttribute('stroke', on ? 'var(--flow)' : 'var(--' + (kind || base) + ')');
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
      p.setAttribute('stroke-dasharray', '3 2');
    } else {
      p.removeAttribute('stroke-dasharray');
    }
  }

  function update(t) {
    if (!svg || !built) return;
    var st = core.state || {};
    var str = st.structure || {};
    var conn = !!(t.plc && t.plc.connected);

    // --- 밸브 (D00010 실제 출력) ---
    // 요청(D04012)했는데 출력이 안 나간 밸브(허가 대기)는 점선으로 구분한다.
    var pend = conn ? ((t.manual || {}).pending || []).concat((t.manual || {}).aux_pending || []) : [];
    var vmap = {};
    (str.valves || []).forEach(function (v) {
      var on = conn && t.valves != null && core.bit(t.valves, v.bit);
      vmap[v.tag] = on;
      setValve(v.tag, on);
      markPending(v.tag, pend.indexOf(v.tag) >= 0);
    });
    // --- 보조 출력 (D00014) ---
    var amap = {};
    (str.aux || []).forEach(function (a) {
      var on = conn && t.aux != null && core.bit(t.aux, a.bit);
      amap[a.tag] = on;
      setValve(a.tag, on);
      markPending(a.tag, pend.indexOf(a.tag) >= 0);
    });
    ['PMP', 'BPMP', 'O3GEN'].forEach(function (tag) {
      var dot = svg.querySelector('[data-aux="' + tag + '"]');
      if (dot) dot.setAttribute('fill', amap[tag] ? 'var(--valve-open)' : 'var(--valve-closed)');
    });

    // --- 값 ---
    var mfc = t.mfc || [];
    for (var i = 1; i <= 2; i++) {
      var m = mfc[i - 1] || {};
      setSv('mfc' + i + 'pv', conn ? fmt.flow(m.pv) : fmt.DASH);
      setSv('mfc' + i + 'sv', conn ? ('SV ' + fmt.flow(m.sv)) : 'SV ' + fmt.DASH);
    }
    var heaters = t.heaters || [];
    [1, 2, 4, 5, 6].forEach(function (ch) {
      var hh = heaters[ch - 1] || {};
      setSv('h' + ch, conn && hh.enabled ? fmt.temp(hh.pv) + ' °C' : fmt.DASH);
    });
    var p = t.pressure || {};
    setSv('cvg', conn ? fmt.torr(p.cvg) : fmt.DASH);
    setSv('cm', conn && p.cm_installed ? fmt.torr(p.cm) : fmt.DASH);
    var cmg = svg.querySelector('[data-cm]');
    if (cmg) cmg.style.display = (p && p.cm_installed) ? '' : 'none';

    var ex = t.extra || {};
    setSv('o3', conn ? (ex.o3_on ? fmt.num(ex.o3_pv, 1) + ' ' + (ex.o3_unit || '') : 'OFF')
                     : fmt.DASH);
    var lid = conn && t.inputs0 != null ? (core.bit(t.inputs0, 5) ? '닫힘' : '열림') : fmt.DASH;
    setSv('lid', lid);

    // --- 흐름 색 ---
    var f1 = conn && (mfc[0] || {}).pv > 1;
    var f2 = conn && (mfc[1] || {}).pv > 1;
    paint('pline', f1 || vmap['PV-1'] || vmap['PV-2'] || vmap['PV-3']);
    paint('aline', f2);
    paint('canPV-1', !!vmap['PV-1']);
    paint('canPV-2', !!vmap['PV-2']);
    paint('canPV-3', !!vmap['PV-3']);
    paint('asstPV-A1', f2 && vmap['PV-A1']);
    paint('asstPV-A2', f2 && vmap['PV-A2']);
    paint('o2in', !!ex.o3_on);
    paint('toChamberR', !!vmap['PV-R']);
    paint('byp', !!vmap['PV-B']);
    paint('byp2', vmap['PV-B'] && amap['IV-B']);
    paint('byp3', !!amap['BPMP'], 'exh');
    paint('vv', !!amap['VV']);
    paint('exh', !!amap['IV-E'], 'exh');
  }

  core.register('schematic', { render: render, update: update });
  w.viewSchematic = { render: render, update: update };
})(window, document);
