/* ============================================================
 * views/schematic.js — Powder ALD 배관도 (인라인 SVG).
 *
 * 왼쪽 공급 → 가운데 배관 · 밸브 → 오른쪽 분말 반응기 → 아래 배기 순서로 읽히게 그린다.
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
 * ★ 밸브 열림/닫힘은 D00010(실제 출력)을, VV·IV-E·펌프·RF 는 D00014 를 본다.
 *   '요청'이 아니라 '실제 출력'을 그려야 화면과 장비가 어긋나지 않는다.
 * ★ 색은 CSS 변수만 쓴다. 이 파일에 hex 를 쓰지 않는다.
 * ★ v0.5.0: 이름은 영문 용어(terms.js) · 상자는 같은 회색 · 밸브 열림 = 초록 채움 · 닫힘 = 회색 외곽선 ·
 *   모름 = 점선 · 흐름 선만 파랑. 조작 가능한 요소(data-fp)를 누르면 그 옆에 조작 창(faceplate.js)이 뜬다.
 * ★ v0.5.1 다시 그림: 상자 · 밸브 · 배관 좌표는 모두 8 격자. 배관은 가로 · 세로만(직각으로 꺾음), 겹치거나
 *   상자 테두리를 따라 가지 않고, 갈래에는 작은 점. 같은 종류는 같은 크기(MFC 88×64 한 열 · 캐니스터 80×56
 *   한 줄 · 펌프는 원 하나). 밸브 이름은 가로 배관이면 위, 세로 배관이면 오른쪽. 라인 이름 · 온도는 라인 왼쪽
 *   끝 위에 한 줄. 묶음 제목은 작은 회색 대문자. 글자는 13 단위 이상(768 창에서 9 px 이상), 겹침 0.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var VB_W = 600, VB_H = 507;   // 배관도 패널 비율(602×509)에 맞춘다
  var G = 8;                    // 좌표 격자

  // 밸브 아이콘 크기
  var VW = 6, VH = 5;

  // 글자 크기(viewBox 단위) — 작은 글자 13 · 기본 14 · 제목 15. 768 창에서 1 단위 ≈ 0.72 px 라 13 단위면 9 px 이상
  var FS = 13, FV = 14, FT = 15;
  // 상자 테두리 · 모서리는 한 규칙 — 1 단위 회색 선, 모서리 3. 배관은 한 굵기
  var BOX_SW = 1, BOX_R = 3, PIPE_W = 1.8;

  var svg = null;
  var built = false;
  var parent = null;            // group() 안에서 그리는 동안의 부모(툴팁 <title> 을 함께 묶는다)
  var hits = [];                // 조작 가능한 요소(faceplate) — build 끝에 맨 위 겹으로 놓는다

  /* ---------- 작은 빌더 ---------- */
  function el(tag, a) {
    var e = d.createElementNS(NS, tag);
    for (var k in (a || {})) if (a[k] !== null && a[k] !== undefined) e.setAttribute(k, a[k]);
    return e;
  }

  function add(e) { (parent || svg).appendChild(e); return e; }

  /** 툴팁(전체 이름)을 단 묶음 — 줄인 이름은 마우스를 올리면 전체 이름이 보인다. */
  function group(title, fn) {
    var g = add(el('g', {}));
    if (title) {
      var t = el('title', {});
      t.textContent = title;
      g.appendChild(t);
    }
    var prev = parent;
    parent = g;
    try { fn(); } finally { parent = prev; }
    return g;
  }

  function txt(x, y, s, o) {
    o = o || {};
    var t = el('text', {
      x: x, y: y, 'font-size': o.size || FS,
      'text-anchor': o.anchor || 'start',
      'font-weight': o.weight || 400,
      fill: o.fill || 'var(--ink-dim)',
      'font-family': o.mono ? 'var(--font-mono)' : 'var(--font-sans)'
    });
    t.textContent = s;
    if (o.bind) t.setAttribute('data-sv', o.bind);
    if (o.spacing) t.setAttribute('letter-spacing', o.spacing);
    return add(t);
  }

  /** 묶음 제목 — 작은 회색 대문자, 묶음 왼쪽 위 */
  function title(x, y, s) {
    return txt(x, y, String(s).toUpperCase(), { weight: 700, fill: 'var(--ink-faint)', spacing: 0.5 });
  }

  /** 배관(가로 · 세로 토막만). key 를 주면 update() 에서 흐름 색을 바꾼다. */
  function pipe(dstr, key, kind) {
    return add(el('path', {
      d: dstr, fill: 'none', 'stroke-width': PIPE_W, 'stroke-linecap': 'butt',
      'stroke-linejoin': 'miter',
      stroke: 'var(--' + (kind || 'pipe') + ')',
      'data-pipe': key || '', 'data-kind': kind || 'pipe'
    }));
  }

  /** 갈래 점 — 배관이 갈라지는 자리. 흐름 색은 그 배관(key)을 따른다 */
  function junction(x, y, key, kind) {
    return add(el('circle', { cx: x, cy: y, r: 2.6, fill: 'var(--' + (kind || 'pipe') + ')',
                              'data-jn': key, 'data-kind': kind || 'pipe' }));
  }

  /** 조작 가능한 요소(배관도 HMI) — 누르면 그 옆에 조작 창(faceplate)이 뜬다. kind:key 와 둘레(x, y, w, h).
   *  ★ 맞는 자리는 사각형(rect)이 아니라 path 로 — 글자 · 상자 겹침 검사는 rect 를 상자로 본다. */
  function hmi(kind, key, x, y, wd, ht, ttl) {
    hits.push({ kind: kind, key: key, x: x, y: y, w: wd, h: ht, title: ttl || key });
  }

  /** 조작 요소를 맨 위 겹으로 — Tab 순서는 위 → 아래, 왼쪽 → 오른쪽 */
  function hitLayer() {
    var layer = add(el('g', { 'data-hmi': '1' }));
    hits.slice().sort(function (a, b) { return (a.y - b.y) || (a.x - b.x); }).forEach(function (h) {
      var g = el('g', { 'data-fp': h.kind + ':' + h.key, tabindex: 0, role: 'button',
                        'aria-label': h.title, 'aria-haspopup': 'dialog' });
      var t = el('title', {});
      t.textContent = h.title + ' — 누르면 조작 창';
      g.appendChild(t);
      g.appendChild(el('path', {
        'class': 'fp-hit',
        d: 'M' + h.x + ',' + h.y + ' h' + h.w + ' v' + h.h + ' h' + (-h.w) + ' Z'
      }));
      layer.appendChild(g);
    });
  }

  /** 나비 밸브. 열림은 초록 채움, 닫힘은 회색 외곽선, 모름은 점선(CSS #schemSvg.unknown).
   *  side 'a' = 가로 배관(이름은 위) · 'r' = 세로 배관(이름은 오른쪽, 아이콘도 세로로).
   *  fp 를 주면 조작 가능한 요소로(기본 'valve', 벤트는 'vent', 조작 없는 밸브는 null). */
  function valve(tag, cx, cy, side, fp) {
    var g = add(el('g', {}));
    var dd = side === 'r'
      ? 'M' + (cx - VH) + ',' + (cy - VW) + ' L' + cx + ',' + cy + ' L' + (cx + VH) + ',' + (cy - VW) + ' Z ' +
        'M' + (cx - VH) + ',' + (cy + VW) + ' L' + cx + ',' + cy + ' L' + (cx + VH) + ',' + (cy + VW) + ' Z'
      : 'M' + (cx - VW) + ',' + (cy - VH) + ' L' + cx + ',' + cy + ' L' + (cx - VW) + ',' + (cy + VH) + ' Z ' +
        'M' + (cx + VW) + ',' + (cy - VH) + ' L' + cx + ',' + cy + ' L' + (cx + VW) + ',' + (cy + VH) + ' Z';
    g.appendChild(el('path', { d: dd, 'stroke-width': 1.2, 'data-valve': tag }));
    var a = side === 'r' ? { x: cx + VH + 5, y: cy + FS * 0.35, anchor: 'start' }
                         : { x: cx, y: cy - VH - 5, anchor: 'middle' };
    var t = el('text', {
      x: a.x, y: a.y, 'font-size': FS, 'text-anchor': a.anchor, 'font-weight': 400,
      fill: 'var(--ink-faint)', 'font-family': 'var(--font-sans)'
    });
    t.textContent = tag;
    g.appendChild(t);
    if (fp !== null) hmi(fp || 'valve', tag, cx - 9, cy - 9, 18, 18, tag);
    return g;
  }

  function box(x, y, wd, ht, o) {
    o = o || {};
    return add(el('rect', {
      x: x, y: y, width: wd, height: ht, rx: BOX_R,
      fill: o.fill || 'var(--panel-2)',
      stroke: o.stroke || 'var(--line-2)',
      'stroke-width': BOX_SW
    }));
  }

  /** MFC 상자(88×64): 'MFC1' / Actual / Setpoint('SP') 세 줄. 전체 이름은 툴팁. 출구는 상자 가운데 높이 */
  var MFC_X = G, MFC_W = 11 * G, MFC_H = 8 * G;
  function mfcBox(y, no, name) {
    var x = MFC_X;
    group('MFC' + no + ' ' + name, function () {
      box(x, y, MFC_W, MFC_H);
      txt(x + 6, y + 17, 'MFC' + no, { fill: 'var(--ink-faint)' });
      txt(x + 6, y + 38, '—', { size: FV, weight: 700, fill: 'var(--pv)', mono: true, bind: 'mfc' + no + 'pv' });
      txt(x + 6, y + 58, '—', { fill: 'var(--sv)', mono: true, bind: 'mfc' + no + 'sv' });
    });
    hmi('mfc', no, x, y, MFC_W, MFC_H, 'MFC' + no + ' ' + name);
    return y + MFC_H / 2;       // 출구 높이
  }

  /** 용기(캐니스터 · 트랩): 이름(한 줄 또는 두 줄 배열)과 온도 채널. 가열 여부와 상관없이 같은 회색 상자 —
   *  온도 글자만. ch 가 있으면 히터 조작 요소. */
  function vessel(x, y, wd, ht, label, ch, ttl) {
    group(ttl || null, function () {
      box(x, y, wd, ht, { fill: 'var(--vessel)', stroke: 'var(--vessel-line)' });
      var mid = y + ht / 2, lines = [].concat(label);
      var o = { anchor: 'middle', fill: 'var(--ink)', weight: 700 };
      if (ch) {
        txt(x + wd / 2, mid - 7, lines[0], o);
        txt(x + wd / 2, mid + 12, '—', { anchor: 'middle', mono: true, fill: 'var(--pv)', bind: 'h' + ch });
      } else if (lines.length > 1) {
        txt(x + wd / 2, mid - 6, lines[0], o);
        txt(x + wd / 2, mid + 13, lines[1], o);
      } else {
        txt(x + wd / 2, mid + FS * 0.35, lines[0], o);
      }
    });
    if (ch) hmi('heater', ch, x, y, wd, ht, ttl || [].concat(label).join(' '));
  }

  /** 펌프 — 원 하나(두 장비 같은 기호). 안의 점이 운전 표시(data-aux). 이름은 왼쪽(anchor end) 또는 아래 */
  var PUMP_R = 12;
  function pump(cx, cy, aux) {
    add(el('circle', { cx: cx, cy: cy, r: PUMP_R, fill: 'var(--panel)', stroke: 'var(--line-2)',
                       'stroke-width': 1.4 }));
    add(el('circle', { cx: cx, cy: cy, r: 4, fill: 'var(--valve-closed)', 'data-aux': aux }));
  }

  /** 범례 — 영문 한 줄(Open · Closed · Unknown · Flow) + 'Click to operate' */
  function legend() {
    var box2 = core.bind('schemLegend');
    if (!box2) return;
    var sw = '<svg width="14" height="9" style="vertical-align:-1px"><path d="M2,1 L7,4.5 L2,8 Z M12,1 L7,4.5 L12,8 Z" ';
    box2.innerHTML =
      '<span>' + sw + 'fill="var(--valve-open)"/></svg> ' + T('s.open') + '</span>' +
      '<span>' + sw + 'fill="none" stroke="var(--valve-closed)" stroke-width="1"/></svg> ' + T('s.closed') + '</span>' +
      '<span>' + sw + 'fill="none" stroke="var(--ink-faint)" stroke-width="1" stroke-dasharray="1.5 1.5"/></svg> ' +
      T('s.unknown') + '</span>' +
      '<span><i style="background:var(--flow)"></i>' + T('s.flow') + '</span>' +
      '<span class="dim">' + T('s.clickToOperate') + '</span>';
  }

  /* ===================== 그리기 ===================== */
  // --- 좌표(모두 8 격자, 두 장비 공통) — 겹침은 이 숫자만 고쳐서 잡는다 ---
  var CH_L = 392, CH_R = 592, CH_T = 64, CH_B = 336;   // 챔버(반응기)
  var CX = 400, CV = 504;                              // 챔버 안 이름 · 값 왼쪽
  var P_Y = 72;            // 전구체 라인
  var A_Y = 224;           // 어시스트 라인
  var CAN_T = 128, CAN_H = 56, CAN_W = 80;
  var EX_X = 472;          // 배기 중심

  /** 전구체 · 어시스트(두 장비 같음) */
  function buildPrecursor() {
    title(G, 24, T('eq.precursor'));
    mfcBox(P_Y - MFC_H / 2, 1, 'Precursor Carrier');
    pipe('M' + (MFC_X + MFC_W) + ',' + P_Y + ' H' + CH_L, 'pline');
    txt(104, P_Y - 6, T('eq.pline'));
    txt(208, P_Y - 6, '—', { mono: true, fill: 'var(--pv)', bind: 'h2' });
    hmi('heater', 2, 100, P_Y - 22, 176, 18, 'CH2 ' + T('eq.pline'));

    var cans = [
      { x: 112, out: 152, tag: 'PV-1', label: T('eq.can1'), ch: 0 },
      { x: 200, out: 256, tag: 'PV-2', label: T('eq.can2'), ch: 4, inlet: 224, atag: 'PV-A1' },
      { x: 288, out: 344, tag: 'PV-3', label: T('eq.can3'), ch: 5, inlet: 312, atag: 'PV-A2' }
    ];
    cans.forEach(function (c) {
      vessel(c.x, CAN_T, CAN_W, CAN_H, c.label, c.ch, c.ch ? 'CH' + c.ch + ' ' + c.label : c.label);
      pipe('M' + c.out + ',' + CAN_T + ' V' + P_Y, 'can' + c.tag);
      junction(c.out, P_Y, 'pline');
      valve(c.tag, c.out, 104, 'r');
      if (c.inlet) {
        pipe('M' + c.inlet + ',' + A_Y + ' V' + (CAN_T + CAN_H), 'asst' + c.atag);
        valve(c.atag, c.inlet, 208, 'r');
      }
    });

    mfcBox(A_Y - MFC_H / 2, 2, 'Assist');
    pipe('M' + (MFC_X + MFC_W) + ',' + A_Y + ' H312', 'aline');
    junction(224, A_Y, 'aline');
    txt(104, A_Y - 6, T('eq.assist'));
  }

  /** 챔버(반응기) — 큰 용기 하나. 안의 글자는 왼쪽 정렬: 이름 · 상태(리드) · 온도 · 압력 */
  function buildVessel(name, lidLabel) {
    box(CH_L, CH_T, CH_R - CH_L, CH_B - CH_T, { fill: 'var(--chamber-bg)', stroke: 'var(--chamber-line)' });
    txt(CX, CH_T + 28, name, { size: FT, weight: 700, fill: 'var(--ink)' });
    txt(CX, CH_T + 52, lidLabel);
    txt(CV, CH_T + 52, '—', { size: FV, mono: true, fill: 'var(--pv)', bind: 'lid' });
    txt(CX, CH_T + 76, T('eq.chamberTemp'));
    txt(CV, CH_T + 76, '—', { size: FV, weight: 700, mono: true, fill: 'var(--pv)', bind: 'h1' });
    hmi('heater', 1, CX - 4, CH_T + 60, CH_R - CX, 22, 'CH1 ' + name);
    txt(CX, CH_T + 100, 'CVG Torr');
    txt(CV, CH_T + 100, '—', { size: FV, weight: 700, mono: true, fill: 'var(--pv)', bind: 'cvg' });
    group(null, function () {
      parent.setAttribute('data-cm', '1');
      txt(CX, CH_T + 124, 'CM Torr');
      txt(CV, CH_T + 124, '—', { size: FV, weight: 700, mono: true, fill: 'var(--pv)', bind: 'cm' });
    });
  }

  /** N2 Vent(VV) — 챔버 위 왼쪽에서 올라가 왼쪽으로. 벤트 조작 요소 */
  function buildVent() {
    pipe('M408,' + CH_T + ' V32 H360', 'vv');
    txt(352, 36, 'N2 ' + T('eq.vent'), { anchor: 'end' });
    valve('VV', 384, 32, 'a', 'vent');
  }

  /** 배기 아래쪽 — 트랩 · 펌프 · '→ Exhaust' (두 장비 같음) */
  function buildExhaustTail(pumpName) {
    vessel(EX_X - 40, 424, 80, 48, T('eq.trap'), 6, 'CH6 ' + T('eq.trap'));
    // 배관은 격자 점(펌프 가운데)까지 — 원이 그 위를 덮는다
    pipe('M' + EX_X + ',472 V488 H520', 'exh2', 'exh');
    pump(EX_X, 488, 'PMP');
    txt(EX_X - PUMP_R - 4, 488 + FS * 0.35, pumpName, { anchor: 'end', weight: 700, fill: 'var(--ink)' });
    hmi('pump', 'PMP', 392, 476, EX_X + PUMP_R - 392, 24, pumpName);
    txt(524, 488 + FS * 0.35, '→ ' + T('eq.exhaust'));
  }

  function build() {
    svg.setAttribute('viewBox', '0 0 ' + VB_W + ' ' + VB_H);
    svg.innerHTML = '';
    hits = [];
    var O3_Y = 320;            // O3 라인(발생기 출구 → 반응기)
    var B_X = 152, B_Y = 432;  // 바이패스 갈래 · 바이패스 펌프 줄

    buildPrecursor();

    /* ---------- 반응물 (O3 라인) ---------- */
    title(G, 288, T('eq.reactant'));
    pipe('M96,264 V296', 'o2in');
    txt(104, 284, T('eq.o2'));
    box(G, 296, 112, 56);
    txt(16, 314, T('eq.o3gen'), { weight: 700, fill: 'var(--ink)' });
    txt(16, 340, '—', { mono: true, fill: 'var(--pv)', bind: 'o3' });
    add(el('circle', { cx: 110, cy: 309, r: 4, fill: 'var(--valve-closed)', 'data-aux': 'O3GEN' }));
    hmi('o3', 'O3', G, 296, 112, 56, T('eq.o3gen'));

    // 발생기 → 갈래 → PV-R → 반응기
    pipe('M120,' + O3_Y + ' H' + B_X, 'o3out');
    pipe('M' + B_X + ',' + O3_Y + ' H' + CH_L, 'toChamberR');
    junction(B_X, O3_Y, 'o3out');
    valve('PV-R', 304, O3_Y, 'a');

    // 발생기 출구에서 갈라지는 바이패스 (PLC 자동) → IV-B → 바이패스 펌프 → 배기
    pipe('M' + B_X + ',' + O3_Y + ' V' + B_Y, 'byp');
    valve('PV-B', B_X, 376, 'r');
    txt(B_X + 10, 400, T('eq.plcAuto'), { fill: 'var(--ink-faint)' });
    pipe('M' + B_X + ',' + B_Y + ' H264', 'byp2');
    valve('IV-B', 200, B_Y, 'a', null);
    pipe('M264,' + B_Y + ' H304', 'byp3', 'exh');         // 펌프 원이 가운데를 덮는다
    pump(264, B_Y, 'BPMP');
    txt(264, 464, T('eq.bypassPump'), { anchor: 'middle', weight: 700, fill: 'var(--ink)' });
    txt(308, B_Y + FS * 0.35, '→ ' + T('eq.exhaust'));

    /* ---------- 반응기 ---------- */
    buildVessel(T('eq.reactor'), T('eq.lid'));
    buildVent();

    /* ---------- 배기 ---------- */
    title(400, 360, T('eq.exhaust'));
    pipe('M' + EX_X + ',' + CH_B + ' V424', 'exh', 'exh');
    valve('IV-E', EX_X, 392, 'r', null);
    buildExhaustTail('Main Pump');

    hitLayer();
    built = true;
    legend();
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
    // ★ 서버 끊김 · PLC 끊김 · 하트비트 멈춤이면 밸브 · 배기 · 펌프 상태를 모른다 — '닫힘 · 쉼'으로 그리지 않고
    //   흐린 점선(Unknown, CSS #schemSvg.unknown)으로
    svg.classList.toggle('unknown', !conn);

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
      setSv('mfc' + i + 'sv', 'SP ' + (conn ? fmt.flow(m.sv) : fmt.DASH));
    }
    var heaters = t.heaters || [];
    [1, 2, 4, 5, 6].forEach(function (ch) {
      var hh = heaters[ch - 1] || {};
      setSv('h' + ch, conn && hh.enabled ? fmt.temp(hh.pv) + ' ℃' : fmt.DASH);
    });
    var p = t.pressure || {};
    setSv('cvg', conn ? fmt.torr(p.cvg) : fmt.DASH);
    setSv('cm', conn && p.cm_installed ? fmt.torr(p.cm) : fmt.DASH);
    var cmg = svg.querySelector('[data-cm]');
    if (cmg) cmg.style.display = (p && p.cm_installed) ? '' : 'none';

    var ex = t.extra || {};
    setSv('o3', conn ? (ex.o3_on ? fmt.num(ex.o3_pv, 1) + ' ' + (ex.o3_unit || '') : T('s.off'))
                     : fmt.DASH);
    var lid = conn && t.inputs0 != null ? (core.bit(t.inputs0, 5) ? T('s.closed') : T('s.open')) : fmt.DASH;
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
    paint('o3out', !!ex.o3_on && (vmap['PV-R'] || vmap['PV-B']));
    paint('toChamberR', !!vmap['PV-R']);
    paint('byp', !!vmap['PV-B']);
    paint('byp2', vmap['PV-B'] && amap['IV-B']);
    paint('byp3', !!amap['BPMP'], 'exh');
    paint('vv', !!amap['VV']);
    paint('exh', !!amap['IV-E'], 'exh');
    paint('exh2', !!amap['IV-E'] && !!amap['PMP'], 'exh');
    if (w.viewFaceplate) w.viewFaceplate.update();
  }

  core.register('schematic', { render: render, update: update });
  w.viewSchematic = { render: render, update: update, grid: G };
})(window, document);
