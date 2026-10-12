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
 * ★ v0.4.12 다시 배치: 글자는 13 단위 이상(좁은 창 768 에서 9 px 이상), 줄 간격은 글꼴 줄 높이가 닿지 않게,
 *   밸브 이름은 밸브 옆(배관 위에 겹치지 않게), MFC 상자 안은 'MFC1' 처럼 줄인 이름 — 전체 이름은 툴팁.
 * ★ v0.5.0: 이름은 영문 용어(terms.js) · 상자는 같은 회색(가열 여부로 칠하지 않는다 — 온도 글자만) ·
 *   밸브 열림 = 초록 채움 · 닫힘 = 회색 외곽선 · 모름 = 점선 · 흐름 선만 파랑. 조작 가능한 요소(data-fp)를
 *   누르면 그 옆에 조작 창(faceplate.js)이 뜬다 — 배관도가 HMI 다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var VB_W = 600, VB_H = 507;   // 배관도 패널 비율(602×509)에 맞춘다

  // 밸브 아이콘 크기
  var VW = 6, VH = 5;

  // ★ v0.4.12 글자 크기(viewBox 단위). 배관도 패널은 좁은 창(768)에서 1 단위 ≈ 0.72 px(PEALD) · 0.80 px(Powder)라
  //   13 단위면 화면 9 px 이상(960 창은 11 px 이상). 이 아래로 줄이지 않는다
  var FS = 13, FV = 14, FH = 14, FT = 15;
  // ★ v0.5.0(A3): 상자 테두리 · 모서리는 한 규칙 — 1 단위 회색 선, 모서리 3(펌프는 둥근 끝)
  var BOX_SW = 1, BOX_R = 3;

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

  /** 조작 가능한 요소(배관도 HMI) — 누르면 그 옆에 조작 창(faceplate)이 뜬다. kind:key 와 둘레(x, y, w, h).
   *  ★ 맞는 자리는 사각형(rect)이 아니라 path 로 — 글자 · 상자 겹침 검사는 rect 를 상자로 본다. */
  function hmi(kind, key, x, y, wd, ht, title) {
    hits.push({ kind: kind, key: key, x: x, y: y, w: wd, h: ht, title: title || key });
  }

  function hitLayer() {
    var layer = add(el('g', { 'data-hmi': '1' }));
    hits.forEach(function (h) {
      var g = el('g', { 'data-fp': h.kind + ':' + h.key, tabindex: 0, role: 'button',
                        'aria-label': h.title, 'aria-haspopup': 'dialog' });
      var t = el('title', {});
      t.textContent = h.title + ' — 누르면 조작 창';
      g.appendChild(t);
      g.appendChild(el('path', {
        'class': 'fp-hit', rx: 3,
        d: 'M' + h.x + ',' + h.y + ' h' + h.w + ' v' + h.h + ' h' + (-h.w) + ' Z'
      }));
      layer.appendChild(g);
    });
  }

  /** 나비 밸브. 열림은 초록 채움, 닫힘은 회색 외곽선, 모름은 점선(CSS #schemSvg.unknown).
   *  ★ v0.4.12: 이름은 밸브 옆에 — side 'r'(오른쪽) · 'l'(왼쪽) · 'a'(위) · 'b'(아래). false 면 이름 없음.
   *  ★ v0.5.0: fp 를 주면 조작 가능한 요소로(기본 'valve', 벤트는 'vent', 조작 없는 밸브는 null). */
  function valve(tag, cx, cy, side, fp) {
    var g = add(el('g', {}));
    g.appendChild(el('path', {
      d: 'M' + (cx - VW) + ',' + (cy - VH) + ' L' + cx + ',' + cy + ' L' + (cx - VW) + ',' + (cy + VH) + ' Z ' +
         'M' + (cx + VW) + ',' + (cy - VH) + ' L' + cx + ',' + cy + ' L' + (cx + VW) + ',' + (cy + VH) + ' Z',
      'stroke-width': 1.2, 'data-valve': tag
    }));
    if (side) {
      var a = { x: cx, y: cy + FS * 0.35, anchor: 'middle' };
      if (side === 'r') a = { x: cx + VW + 3, y: cy + FS * 0.35, anchor: 'start' };
      else if (side === 'l') a = { x: cx - VW - 3, y: cy + FS * 0.35, anchor: 'end' };
      else if (side === 'a') a = { x: cx, y: cy - VH - 4, anchor: 'middle' };
      else if (side === 'b') a = { x: cx, y: cy + VH + 3 + FS * 0.8, anchor: 'middle' };
      var t = el('text', {
        x: a.x, y: a.y, 'font-size': FS, 'text-anchor': a.anchor,
        fill: 'var(--ink-faint)', 'font-family': 'var(--font-sans)'
      });
      t.textContent = tag;
      g.appendChild(t);
    }
    if (fp !== null) hmi(fp || 'valve', tag, cx - 9, cy - 8, 18, 16, tag);
    return g;
  }

  function box(x, y, wd, ht, o) {
    o = o || {};
    return add(el('rect', {
      x: x, y: y, width: wd, height: ht, rx: o.r === undefined ? BOX_R : o.r,
      fill: o.fill || 'var(--panel-2)',
      stroke: o.stroke || 'var(--line-2)',
      'stroke-width': BOX_SW,
      'stroke-dasharray': o.dash || null
    }));
  }

  /** MFC 상자(MFC_W × MFC_H): 'MFC1' / Actual / Setpoint('SP') 세 줄. 전체 이름은 툴팁.
   *  배관이 붙는 높이는 y + MFC_PIPE. 줄 간격은 글꼴 줄 높이가 닿지 않게 20 이상 */
  var MFC_W = 92, MFC_H = 64, MFC_PIPE = 32;
  function mfcBox(x, y, no, name) {
    group('MFC' + no + ' ' + name, function () {
      box(x, y, MFC_W, MFC_H);
      txt(x + 5, y + 17, 'MFC' + no, { fill: 'var(--ink-faint)' });
      txt(x + 5, y + 38, '—', { size: FV, weight: 600, fill: 'var(--pv)', mono: true, bind: 'mfc' + no + 'pv' });
      txt(x + 5, y + 58, '—', { fill: 'var(--sv)', mono: true, bind: 'mfc' + no + 'sv' });
    });
    hmi('mfc', no, x, y, MFC_W, MFC_H, 'MFC' + no + ' ' + name);
  }

  /** 용기(캐니스터 · 트랩): 이름(한 줄 또는 두 줄 배열)과 온도 채널. ★ v0.5.0(A3): 가열 여부와 상관없이 같은
   *  회색 상자 — 온도 글자만. ch 가 있으면 히터 조작 요소. 두 줄이면 높이 50 이상. */
  function vessel(x, y, wd, ht, label, ch, title) {
    group(title || null, function () {
      box(x, y, wd, ht, { fill: 'var(--vessel)', stroke: 'var(--vessel-line)' });
      var mid = y + ht / 2, lines = [].concat(label);
      var o = { anchor: 'middle', fill: 'var(--ink)', weight: 600 };
      if (ch) {
        txt(x + wd / 2, mid - 8, lines[0], o);
        txt(x + wd / 2, mid + 11, '—', { anchor: 'middle', mono: true, fill: 'var(--pv)', bind: 'h' + ch });
      } else if (lines.length > 1) {
        txt(x + wd / 2, mid - 6, lines[0], o);
        txt(x + wd / 2, mid + 13, lines[1], o);
      } else {
        txt(x + wd / 2, mid + FS * 0.35, lines[0], o);
      }
    });
    if (ch) hmi('heater', ch, x, y, wd, ht, title || [].concat(label).join(' '));
  }

  /** 펌프(둥근 상자 + 운전 점 + 이름). cx = 배관 중심. 펌핑 조작 요소 */
  function pump(cx, y, name, aux, wd) {
    wd = wd || 110;
    box(cx - wd / 2, y, wd, 30, { r: 15, fill: 'var(--panel)' });
    add(el('circle', { cx: cx - wd / 2 + 13, cy: y + 15, r: 4, fill: 'var(--valve-closed)', 'data-aux': aux }));
    txt(cx + 8, y + 15 + FS * 0.35, name, { anchor: 'middle', weight: 600, fill: 'var(--ink)' });
    if (aux === 'PMP') hmi('pump', 'PMP', cx - wd / 2, y, wd, 30, name);
  }

  /** 범례 — 영문 용어(terms.js). 조작 가능한 요소는 누르면 조작 창이 뜬다 */
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
      '<span><i style="background:var(--pipe)"></i>' + T('s.idlePipe') + '</span>' +
      '<span><i style="background:var(--exh)"></i>' + T('s.exhaust') + '</span>' +
      '<span class="dim">누르면 조작 창</span>';
  }

  /* ===================== 그리기 ===================== */
  // --- 좌표 격자 (두 장비 공통 — 왼쪽 공급 · 오른쪽 챔버 · 아래 배기). 겹침은 이 숫자만 고쳐서 잡는다 ---
  var CH_L = 380, CH_R = 590, CH_T = 70, CH_B = 330;   // 챔버
  var P_Y = 90;          // 전구체 라인(MFC1 상자 y 58 + 32)
  var A_Y = 216;         // 어시스트 라인(MFC2 상자 y 184 + 32)
  var CAN_T = 132, CAN_H = 52, CAN_W = 82;
  var CX = 390;          // 챔버 안 글자 왼쪽
  var EX_X = 466;        // 배기 중심

  /** 전구체 · 어시스트(두 장비 같음) */
  function buildPrecursor() {
    txt(6, 20, T('eq.precursor'), { size: FH, weight: 700, fill: 'var(--ink)' });
    mfcBox(6, P_Y - MFC_PIPE, 1, 'Precursor Carrier');
    pipe('M' + (6 + MFC_W) + ',' + P_Y + ' H' + CH_L, 'pline');
    txt(108, P_Y - 8, T('eq.pline'));
    txt(226, P_Y - 8, '—', { mono: true, fill: 'var(--pv)', bind: 'h2' });
    hmi('heater', 2, 104, P_Y - 24, 190, 20, 'CH2 ' + T('eq.pline'));

    var cans = [
      { x: 104, out: 145, tag: 'PV-1', label: T('eq.can1'), ch: 0 },
      { x: 194, out: 261, tag: 'PV-2', label: T('eq.can2'), ch: 4, inlet: 209, atag: 'PV-A1' },
      { x: 284, out: 351, tag: 'PV-3', label: T('eq.can3'), ch: 5, inlet: 299, atag: 'PV-A2' }
    ];
    cans.forEach(function (c) {
      vessel(c.x, CAN_T, CAN_W, CAN_H, c.label, c.ch, c.ch ? 'CH' + c.ch + ' ' + c.label : c.label);
      pipe('M' + c.out + ',' + CAN_T + ' V' + P_Y, 'can' + c.tag);
      valve(c.tag, c.out, (P_Y + CAN_T) / 2, 'l');
      if (c.inlet) {
        pipe('M' + c.inlet + ',' + A_Y + ' V' + (CAN_T + CAN_H), 'asst' + c.atag);
        valve(c.atag, c.inlet, (A_Y + CAN_T + CAN_H) / 2, 'l');
      }
    });

    mfcBox(6, A_Y - MFC_PIPE, 2, 'Assist');
    pipe('M' + (6 + MFC_W) + ',' + A_Y + ' H306', 'aline');
    txt(106, A_Y + 18, T('eq.assist'));          // 어시스트 라인 밑(오른쪽 끝은 챔버에 닿는다)
  }

  /** 챔버 안 압력(CVG · CM) — y = 'CVG' 글자 기준선 */
  function buildGauges(y) {
    txt(CX, y, 'CVG');
    txt(CX, y + 22, '—', { size: FT, weight: 600, mono: true, fill: 'var(--pv)', bind: 'cvg' });
    txt(CX, y + 42, 'Torr', { fill: 'var(--ink-faint)' });
    group(null, function () {
      parent.setAttribute('data-cm', '1');
      txt(CX + 90, y, 'CM');
      txt(CX + 90, y + 22, '—', { size: FT, weight: 600, mono: true, fill: 'var(--pv)', bind: 'cm' });
    });
  }

  /** N2 Vent(VV) — 챔버 위 왼쪽에서 들어간다. 벤트 조작 요소 */
  function buildVent() {
    pipe('M392,' + CH_T + ' V20', 'vv');
    txt(386, 24, 'N2 ' + T('eq.vent'), { anchor: 'end' });
    valve('VV', 392, 44, 'l', 'vent');
  }

  /** 배기 아래쪽 — 트랩 · 펌프 · '→ Exhaust' (두 장비 같음) */
  function buildExhaustTail(trapTop, pumpTop, pumpName, pumpW) {
    vessel(EX_X - 45, trapTop, 90, 52, T('eq.trap'), 6, 'CH6 ' + T('eq.trap'));
    pump(EX_X, pumpTop, pumpName, 'PMP', pumpW);
    txt(EX_X + pumpW / 2 + 6, pumpTop + 15 + FS * 0.35, '→ ' + T('eq.exhaust'));
  }

  function build() {
    svg.setAttribute('viewBox', '0 0 ' + VB_W + ' ' + VB_H);
    svg.innerHTML = '';
    hits = [];
    var O3_Y = 306;      // O3 라인(발생기 상자 y 280 + 26)
    var B_X = 150, B_Y = 420;   // 바이패스 갈래 · 바이패스 펌프 줄

    buildPrecursor();

    /* ---------- 반응물 (O3 라인) ---------- */
    txt(6, 270, T('eq.reactant'), { size: FH, weight: 700, fill: 'var(--ink)' });
    pipe('M90,258 V280', 'o2in');
    txt(96, 272, T('eq.o2'));
    box(6, 280, 120, 52);
    txt(24, 298, T('eq.o3gen'), { weight: 600, fill: 'var(--ink)' });
    txt(66, 320, '—', { anchor: 'middle', mono: true, fill: 'var(--pv)', bind: 'o3' });
    add(el('circle', { cx: 16, cy: 293, r: 4, fill: 'var(--valve-closed)', 'data-aux': 'O3GEN' }));
    hmi('o3', 'O3', 6, 280, 120, 52, T('eq.o3gen'));

    // 발생기 → 챔버
    pipe('M126,' + O3_Y + ' H' + CH_L, 'toChamberR');
    valve('PV-R', 300, O3_Y, 'a');

    // 발생기 출구에서 갈라지는 바이패스 (PLC 자동)
    pipe('M' + B_X + ',' + O3_Y + ' V' + B_Y, 'byp');
    valve('PV-B', B_X, 340, 'r');
    txt(B_X + 9, 366, T('eq.plcAuto'), { fill: 'var(--ink-faint)' });
    pipe('M' + B_X + ',' + B_Y + ' H230', 'byp2');
    valve('IV-B', 190, B_Y, 'a', null);
    box(230, B_Y - 15, 120, 30, { r: 15, fill: 'var(--panel)' });
    add(el('circle', { cx: 244, cy: B_Y, r: 4, fill: 'var(--valve-closed)', 'data-aux': 'BPMP' }));
    txt(296, B_Y + FS * 0.35, T('eq.bypassPump'), { anchor: 'middle', weight: 600, fill: 'var(--ink)' });
    // 바이패스 펌프 → 배기는 아래로(오른쪽은 배기 트랩 자리)
    pipe('M290,' + (B_Y + 15) + ' V452', 'byp3', 'exh');
    txt(290, 470, '↓ ' + T('eq.exhaust'), { anchor: 'middle' });

    /* ---------- 챔버 ---------- */
    box(CH_L, CH_T, CH_R - CH_L, CH_B - CH_T, { fill: 'var(--chamber-bg)', stroke: 'var(--chamber-line)' });
    txt(CX, CH_T + 26, T('eq.reactor'), { size: FT, weight: 700, fill: 'var(--ink)' });
    txt(CX, CH_T + 46, T('eq.lid'));
    txt(CX + 36, CH_T + 46, '—', { mono: true, fill: 'var(--pv)', bind: 'lid' });

    txt(CX, CH_T + 75, T('eq.chamberTemp'));
    txt(CX, CH_T + 96, '—', { size: FV, weight: 600, mono: true, fill: 'var(--pv)', bind: 'h1' });
    hmi('heater', 1, CX - 4, CH_T + 61, 140, 42, 'CH1 ' + T('eq.reactor'));

    buildGauges(CH_T + 120);

    // 분말 베드
    add(el('rect', { x: CH_L + 20, y: CH_B - 34, width: (CH_R - CH_L) - 40, height: 14, rx: 3,
                     fill: 'var(--stage)', stroke: 'var(--line-2)', 'stroke-width': .8 }));
    txt(CX, CH_B - 40, T('eq.bed'), { fill: 'var(--ink-faint)' });

    buildVent();

    /* ---------- 배기 ---------- */
    // 배관은 용기 · 펌프 사이만 잇는다(상자 뒤로 지나가지 않게)
    pipe('M' + EX_X + ',' + CH_B + ' V402 M' + EX_X + ',454 V462', 'exh', 'exh');
    valve('IV-E', EX_X, 366, false, null);
    txt(EX_X + 16, 366 + FS * 0.35, 'IV-E Isolation');
    buildExhaustTail(402, 462, 'Main Pump', 104);

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
    paint('toChamberR', !!vmap['PV-R']);
    paint('byp', !!vmap['PV-B']);
    paint('byp2', vmap['PV-B'] && amap['IV-B']);
    paint('byp3', !!amap['BPMP'], 'exh');
    paint('vv', !!amap['VV']);
    paint('exh', !!amap['IV-E'], 'exh');
    if (w.viewFaceplate) w.viewFaceplate.update();
  }

  core.register('schematic', { render: render, update: update });
  w.viewSchematic = { render: render, update: update };
})(window, document);
