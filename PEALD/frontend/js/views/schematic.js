/* ============================================================
 * views/schematic.js — PEALD 배관도 (인라인 SVG).
 *
 * 왼쪽 공급 → 가운데 배관 · 밸브 → 오른쪽 챔버 → 아래 배기 순서로 읽히게 그린다.
 *
 *   [전구체]  MFC1 ─ 전구체 라인(CH2) ─ 챔버
 *             라인 아래에 캐니스터 3개
 *               캐니스터1(1port) ─PV-1→ 라인
 *               캐니스터2(2port, CH4): MFC2 ─PV-A1→ 입구, 출구 ─PV-2→ 라인
 *               캐니스터3(2port, CH5): MFC2 ─PV-A2→ 입구, 출구 ─PV-3→ 라인
 *   [반응물]  매니폴드(CH3)에 세 입력: MFC3 ─PV-R1→ / H2O 캐니스터 ─PV-R2→ / MFC4(O2) ─PV-R3→
 *             매니폴드 ─PV-R→ 챔버
 *   [챔버]    CH1, RF 파워/매칭박스, 힌지 리드, CVG(+CM), VV(N2 벤트)
 *   [배기]    챔버 ─ Inline PCV ─ IV-E ─ 트랩(CH6) ─ 드라이펌프 → 배기
 *
 * ★ 밸브 열림/닫힘은 D00010(실제 출력)을, VV·IV-E·펌프·RF 는 D00014 를 본다.
 *   '요청'이 아니라 '실제 출력'을 그려야 화면과 장비가 어긋나지 않는다.
 * ★ 색은 CSS 변수만 쓴다. 이 파일에 hex 를 쓰지 않는다.
 * ★ v0.5.0: 이름은 영문 용어(terms.js) · 상자는 같은 회색 · 밸브 열림 = 초록 채움 · 닫힘 = 회색 외곽선 ·
 *   모름 = 점선 · 흐름 선만 파랑. 조작 가능한 요소(data-fp)를 누르면 그 옆에 조작 창(faceplate.js)이 뜬다.
 * ★ v0.5.2: 챔버 상자는 내용 높이로 · 배기 제목은 배관 왼쪽 · 누르기 영역에 밸브 이름까지 · Tab 은 무리 순서 ·
 *   매니폴드 출구를 한 격자 비켜 세 갈래 둘 · 라인 이름과 값 간격 같게(이름 폭 + 12).
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
  var grp = 0;                  // 지금 그리는 무리(0 전구체 · 1 반응물 · 2 챔버 · 3 배기) — Tab 순서

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

  /** 묶음 제목 — 작은 회색 대문자. anchor 'end' 면 x 에서 왼쪽으로(배관 왼쪽에 붙일 때) */
  function title(x, y, s, anchor) {
    return txt(x, y, String(s).toUpperCase(), { weight: 700, fill: 'var(--ink-faint)', spacing: 0.5, anchor: anchor });
  }

  /** 글자 폭 어림(viewBox 단위) — 누르기 영역 · 값 자리를 잡는 데만 쓴다(브라우저에서는 실제 길이로 다시 맞춤) */
  function textW(str, size, mono) {
    var wsum = 0;
    String(str).split('').forEach(function (ch) {
      var c = ch.charCodeAt(0);
      wsum += mono ? 0.62 : (c >= 0xAC00 && c <= 0xD7A3) || ch === '℃' || ch === '—' || ch === '→' ? 1.0
        : ch === ' ' || ch === '.' ? 0.3 : '()'.indexOf(ch) >= 0 ? 0.36 : ch === '-' ? 0.38 : ch === '/' ? 0.4
          : 'ilI'.indexOf(ch) >= 0 ? 0.3 : 'ftjr'.indexOf(ch) >= 0 ? 0.4 : 'mwMW'.indexOf(ch) >= 0 ? 0.9
            : /[0-9]/.test(ch) ? 0.6 : /[A-Z]/.test(ch) ? 0.68 : /[a-z]/.test(ch) ? 0.56 : 0.7;
    });
    return wsum * (size || FS);
  }

  /** 라인 이름 + 값 한 줄 — 값은 이름 폭 + 12 뒤(모든 라인 같은 간격). 브라우저에서는 실제 글자 길이로 맞춘다 */
  var NAME_GAP = 12;
  function labelValue(x, y, name, bind) {
    var n = txt(x, y, name);
    var v = txt(x + Math.ceil(textW(name)) + NAME_GAP, y, '—', { mono: true, fill: 'var(--pv)', bind: bind });
    if (typeof n.getComputedTextLength === 'function') {
      try {
        var L = n.getComputedTextLength();
        if (L > 0) v.setAttribute('x', Math.ceil(x + L + NAME_GAP));
      } catch (e) { /* 아직 그려지지 않음 — 어림값 그대로 */ }
    }
    return Math.ceil(textW(name)) + NAME_GAP + textW('250.0 ℃', FS, true);   // 이름 + 값 폭(누르기 영역)
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
    hits.push({ kind: kind, key: key, x: x, y: y, w: wd, h: ht, title: ttl || key, grp: grp });
  }

  /** 조작 요소를 맨 위 겹으로 — ★ v0.5.2(B7): Tab 순서는 무리(Precursor → Reactant → Chamber → Exhaust) 다음
   *  위 → 아래, 왼쪽 → 오른쪽(열 사이를 오가지 않게) */
  function hitLayer() {
    var layer = add(el('g', { 'data-hmi': '1' }));
    hits.slice().sort(function (a, b) { return (a.grp - b.grp) || (a.y - b.y) || (a.x - b.x); }).forEach(function (h) {
      var g = el('g', { 'data-fp': h.kind + ':' + h.key, 'data-grp': h.grp, tabindex: 0, role: 'button',
                        'aria-label': h.title, 'aria-haspopup': 'dialog' });
      var t = el('title', {});
      t.textContent = h.title + ' — click to operate';
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
    // ★ v0.5.2(B6): 누르기 영역은 아이콘 + 이름 글자까지
    if (fp !== null) {
      var tw = Math.ceil(textW(tag));
      if (side === 'r') hmi(fp || 'valve', tag, cx - 9, cy - 9, 9 + VH + 5 + tw + 3, 18, tag);
      else {
        var hw = Math.max(9, Math.ceil(tw / 2) + 2);
        hmi(fp || 'valve', tag, cx - hw, cy - VH - 5 - 15, 2 * hw, 15 + 5 + VH + 9, tag);
      }
    }
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
  // ★ v0.5.2(B5): 챔버(반응기) 상자는 내용 높이에 맞춰 줄이고(아래 232), 남는 세로 공간은 배기 쪽 간격으로
  var CH_L = 392, CH_R = 592, CH_T = 64, CH_B = 232;   // 챔버(반응기)
  var CH_IN = 216;         // 반응물이 챔버에 들어가는 높이(챔버 왼쪽, 아래에서 한 칸 위)
  var CX = 400, CV = 504;                              // 챔버 안 이름 · 값 왼쪽
  var P_Y = 72;            // 전구체 라인
  var A_Y = 224;           // 어시스트 라인
  var CAN_T = 128, CAN_H = 56, CAN_W = 80;
  var EX_X = 472;          // 배기 중심
  var EX_T = 264;          // 배기 무리 제목 기준선

  /** 전구체 · 어시스트(두 장비 같음) */
  function buildPrecursor() {
    grp = 0;
    title(G, 24, T('eq.precursor'));
    mfcBox(P_Y - MFC_H / 2, 1, 'Precursor Carrier');
    pipe('M' + (MFC_X + MFC_W) + ',' + P_Y + ' H' + CH_L, 'pline');
    var lw = labelValue(104, P_Y - 6, T('eq.pline'), 'h2');
    hmi('heater', 2, 100, P_Y - 22, Math.ceil(lw) + 8, 18, 'CH2 ' + T('eq.pline'));

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
    grp = 2;
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
    grp = 2;
    pipe('M408,' + CH_T + ' V32 H360', 'vv');
    txt(352, 36, 'N2 ' + T('eq.vent'), { anchor: 'end' });
    valve('VV', 384, 32, 'a', 'vent');
  }

  /** 배기 무리 제목 — ★ v0.5.2(B1): 배기 배관(EX_X) 왼쪽에 오른쪽 맞춤(배관에 닿지 않게) */
  function exhaustTitle() {
    grp = 3;
    title(EX_X - G, EX_T, T('eq.exhaust'), 'end');
  }

  /** 펌프 이름 — 두 펌프 같은 자리 규칙(원 왼쪽, 오른쪽 맞춤) */
  function pumpLabel(cx, cy, name) {
    return txt(cx - PUMP_R - 4, cy + FS * 0.35, name, { anchor: 'end', weight: 700, fill: 'var(--ink)' });
  }

  /** 배기 아래쪽 — 트랩(캐니스터와 같은 80×56) · 펌프 · '→ Exhaust' (두 장비 같음) */
  function buildExhaustTail(pumpName) {
    grp = 3;
    vessel(EX_X - 40, 400, CAN_W, CAN_H, T('eq.trap'), 6, 'CH6 ' + T('eq.trap'));
    // 배관은 격자 점(펌프 가운데)까지 — 원이 그 위를 덮는다
    pipe('M' + EX_X + ',456 V488 H520', 'exh2', 'exh');
    pump(EX_X, 488, 'PMP');
    pumpLabel(EX_X, 488, pumpName);
    hmi('pump', 'PMP', 392, 476, EX_X + PUMP_R - 392, 24, pumpName);
    txt(524, 488 + FS * 0.35, '→ ' + T('eq.exhaust'));
  }

  function build() {
    svg.setAttribute('viewBox', '0 0 ' + VB_W + ' ' + VB_H);
    svg.innerHTML = '';
    hits = [];
    // 반응물 세 입력 높이 · 매니폴드. ★ v0.5.2(B8): 매니폴드 출구는 입구(R_Y2)에서 한 격자 아래(R_OUT) —
    //   네 갈래 교차 대신 세 갈래 둘
    var MAN_X = 176, R_Y1 = 328, R_Y2 = 400, R_Y3 = 464, R_OUT = R_Y2 + G;

    buildPrecursor();

    /* ---------- 반응물 ---------- */
    grp = 1;
    title(G, 288, T('eq.reactant'));
    mfcBox(R_Y1 - MFC_H / 2, 3, 'Reactant Purge');
    // ★ v0.5.2(B9): H2O 캐니스터는 다른 캐니스터와 같은 80×56
    vessel(MFC_X, 368, CAN_W, CAN_H, ['H2O', 'Canister R'], 0, T('eq.h2o'));
    mfcBox(R_Y3 - MFC_H / 2, 4, 'O2');

    [[R_Y1, 'PV-R1', MFC_X + MFC_W], [R_Y2, 'PV-R2', MFC_X + CAN_W], [R_Y3, 'PV-R3', MFC_X + MFC_W]].forEach(function (r) {
      pipe('M' + r[2] + ',' + r[0] + ' H' + MAN_X, 'r' + r[1]);
      valve(r[1], 136, r[0], 'a');
    });
    pipe('M' + MAN_X + ',' + R_Y1 + ' V' + R_Y3, 'manifold');
    junction(MAN_X, R_Y2, 'manifold');
    junction(MAN_X, R_OUT, 'toChamberR');
    // 매니폴드 이름 · 온도는 나가는 배관 왼쪽 끝 위 한 줄
    var mw = labelValue(MAN_X + 8, R_OUT - 6, T('eq.manifold'), 'h3');
    hmi('heater', 3, MAN_X + 4, R_OUT - 22, Math.ceil(mw) + 8, 18, 'CH3 ' + T('eq.manifold'));
    // 매니폴드 → PV-R → 챔버 왼쪽 아래
    pipe('M' + MAN_X + ',' + R_OUT + ' H376 V' + CH_IN + ' H' + CH_L, 'toChamberR');
    valve('PV-R', 376, 304, 'r');

    /* ---------- 챔버 ---------- */
    grp = 2;
    box(432, 8, 160, 48);
    txt(440, 28, T('eq.rf'));
    txt(440, 48, '—', { mono: true, fill: 'var(--pv)', bind: 'rf' });
    hmi('rf', 'RF', 432, 8, 160, 48, T('eq.rf'));
    pipe('M512,56 V' + CH_T, 'rfline');

    buildVessel(T('eq.chamber'), T('eq.hingeLid'));
    buildVent();

    /* ---------- 배기 ---------- */
    exhaustTitle();
    pipe('M' + EX_X + ',' + CH_B + ' V400', 'exh', 'exh');           // PCV 원이 그 위를 덮는다
    add(el('circle', { cx: EX_X, cy: 296, r: 8, fill: 'var(--panel)',
                       stroke: 'var(--exh)', 'stroke-width': 1.4 }));
    add(el('path', { d: 'M' + (EX_X - 5) + ',301 L' + (EX_X + 5) + ',291', stroke: 'var(--exh)', 'stroke-width': 1.4 }));
    txt(488, 296 + FS * 0.35, 'PCV');
    txt(520, 296 + FS * 0.35, '—', { mono: true, weight: 700, fill: 'var(--pv)', bind: 'pcv' });
    hmi('pcv', 'PCV', EX_X - 10, 284, 112, 24, T('eq.pcv'));
    valve('IV-E', EX_X, 352, 'r', null);
    buildExhaustTail(T('eq.dryPump'));

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
    var dot = svg.querySelector('[data-aux="PMP"]');
    if (dot) dot.setAttribute('fill', amap['PMP'] ? 'var(--valve-open)' : 'var(--valve-closed)');

    // --- 값 ---
    var mfc = t.mfc || [];
    for (var i = 1; i <= 4; i++) {
      var m = mfc[i - 1] || {};
      setSv('mfc' + i + 'pv', conn ? fmt.flow(m.pv) : fmt.DASH);
      setSv('mfc' + i + 'sv', 'SP ' + (conn ? fmt.flow(m.sv) : fmt.DASH));
    }
    var heaters = t.heaters || [];
    [1, 2, 3, 4, 5, 6].forEach(function (ch) {
      var hh = heaters[ch - 1] || {};
      setSv('h' + ch, conn && hh.enabled ? fmt.temp(hh.pv) + ' ℃' : fmt.DASH);
    });
    var p = t.pressure || {};
    setSv('cvg', conn ? fmt.torr(p.cvg) : fmt.DASH);
    setSv('cm', conn && p.cm_installed ? fmt.torr(p.cm) : fmt.DASH);
    var cmg = svg.querySelector('[data-cm]');
    if (cmg) cmg.style.display = (p && p.cm_installed) ? '' : 'none';

    var ex = t.extra || {};
    setSv('pcv', conn ? fmt.pct(ex.pcv_pv) + ' %' : fmt.DASH);
    setSv('rf', conn ? (ex.rf_on ? fmt.watt(ex.rf_fwd) + ' W (Ref ' + fmt.watt(ex.rf_ref) + ')' : T('s.off'))
                     : fmt.DASH);
    var lid = conn && t.inputs0 != null ? (core.bit(t.inputs0, 5) ? T('s.closed') : T('s.open')) : fmt.DASH;
    setSv('lid', lid);

    // --- 흐름 색 ---
    var f1 = conn && (mfc[0] || {}).pv > 1;
    var f2 = conn && (mfc[1] || {}).pv > 1;
    var f3 = conn && (mfc[2] || {}).pv > 1;
    var f4 = conn && (mfc[3] || {}).pv > 1;
    paint('pline', f1 || vmap['PV-1'] || vmap['PV-2'] || vmap['PV-3']);
    paint('aline', f2);
    paint('canPV-1', !!vmap['PV-1']);
    paint('canPV-2', !!vmap['PV-2']);
    paint('canPV-3', !!vmap['PV-3']);
    paint('asstPV-A1', f2 && vmap['PV-A1']);
    paint('asstPV-A2', f2 && vmap['PV-A2']);
    paint('rPV-R1', f3 && vmap['PV-R1']);
    paint('rPV-R2', !!vmap['PV-R2']);
    paint('rPV-R3', f4 && vmap['PV-R3']);
    paint('manifold', vmap['PV-R1'] || vmap['PV-R2'] || vmap['PV-R3']);
    paint('toChamberR', !!vmap['PV-R']);
    paint('vv', !!amap['VV']);
    paint('rfline', !!ex.rf_on);
    paint('exh', !!amap['IV-E'], 'exh');
    paint('exh2', !!amap['IV-E'] && !!amap['PMP'], 'exh');
    if (w.viewFaceplate) w.viewFaceplate.update();
  }

  core.register('schematic', { render: render, update: update });
  w.viewSchematic = { render: render, update: update, grid: G };
})(window, document);
