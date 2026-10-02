/* ============================================================
 * views/schematic.js — PEALD 배관도 (인라인 SVG).
 *
 * 왼쪽 공급 → 가운데 챔버 → 오른쪽·아래 배기 순서로 읽히게 그린다.
 *
 *   [전구체]  MFC1 ─ 전구체 라인(CH2) ─ 챔버
 *             라인 아래에 캐니스터 3개
 *               캐니스터1(1port) ─PV-1→ 라인
 *               캐니스터2(2port, CH4): MFC2 ─PV-A1→ 입구, 출구 ─PV-2→ 라인
 *               캐니스터3(2port, CH5): MFC2 ─PV-A2→ 입구, 출구 ─PV-3→ 라인
 *   [반응물]  매니폴드(CH3)에 세 입력: MFC3 ─PV-R1→ / H2O 캐니스터 ─PV-R2→ / MFC4(O2) ─PV-R3→
 *             매니폴드 ─PV-R→ 챔버
 *   [챔버]    CH1, RF 전극·RF 파워/매칭박스, 힌지 리드, CVG(+CM), VV(N2 벤트)
 *   [배기]    챔버 ─ Inline PCV ─ IV-E ─ 트랩(CH6) ─ 드라이펌프 → 배기
 *
 * ★ 밸브 열림/닫힘은 D00010(실제 출력)을, VV·IV-E·펌프·RF 는 D00014 를 본다.
 *   '요청'이 아니라 '실제 출력'을 그려야 화면과 장비가 어긋나지 않는다.
 * ★ 색은 CSS 변수만 쓴다. 이 파일에 hex 를 쓰지 않는다.
 * ★ v0.4.12 다시 배치: 글자는 13 단위 이상(좁은 창 768 에서 9 px 이상), 줄 간격은 글꼴 줄 높이가 닿지 않게,
 *   밸브 이름은 밸브 옆(배관 위에 겹치지 않게), MFC 상자 안은 'MFC1' 처럼 줄인 이름 — 전체 이름은 툴팁.
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

  var svg = null;
  var built = false;
  var parent = null;            // group() 안에서 그리는 동안의 부모(툴팁 <title> 을 함께 묶는다)

  /* ---------- 작은 빌더 ---------- */
  function el(tag, a) {
    var e = d.createElementNS(NS, tag);
    for (var k in (a || {})) if (a[k] !== null && a[k] !== undefined) e.setAttribute(k, a[k]);
    return e;
  }

  function add(e) { (parent || svg).appendChild(e); return e; }

  /** 툴팁(전체 이름)을 단 묶음 — 줄인 이름은 마우스를 올리면 전체 이름이 보인다(범례에도 적는다). */
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

  /** 나비 밸브. 열림은 채우고 닫힘은 외곽선만.
   *  ★ v0.4.12: 이름은 밸브 옆에 — side 'r'(오른쪽) · 'l'(왼쪽) · 'a'(위) · 'b'(아래). 세로 배관의 밸브는
   *  'r' · 'l' 로(가운데 정렬로 위 · 아래에 두면 글자가 배관을 가로질렀다). false 면 이름 없음. */
  function valve(tag, cx, cy, side) {
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

  /** MFC 상자(MFC_W × MFC_H): 'MFC1' / 현재값 / 설정값 세 줄. ★ v0.4.12: 상자 안에는 짧은 이름만 — 전체 이름은
   *  툴팁(상자 안에 '전구체 캐리어' 까지 쓰면 글자가 상자를 넘었다). 배관이 붙는 높이는 y + MFC_PIPE.
   *  ★ 줄 간격은 글꼴 줄 높이(위 1.16 · 아래 0.29 em — 한글 글꼴의 getBBox)가 닿지 않게 20 이상 */
  var MFC_W = 92, MFC_H = 64, MFC_PIPE = 32;
  function mfcBox(x, y, no, name) {
    group('MFC' + no + ' ' + name, function () {
      box(x, y, MFC_W, MFC_H, { stroke: 'var(--primary)' });
      txt(x + 5, y + 17, 'MFC' + no, { fill: 'var(--ink-faint)' });
      txt(x + 5, y + 38, '—', { size: FV, weight: 700, fill: 'var(--pv)', mono: true, bind: 'mfc' + no + 'pv' });
      txt(x + 5, y + 58, '—', { fill: 'var(--sv)', mono: true, bind: 'mfc' + no + 'sv' });
    });
  }

  /** 가열 용기(캐니스터·트랩): 이름(한 줄 또는 두 줄 배열)과 온도 채널. 두 줄이면 높이 50 이상. */
  function vessel(x, y, wd, ht, label, ch, title) {
    group(title || null, function () {
      box(x, y, wd, ht, {
        fill: ch ? 'var(--vessel)' : 'var(--vessel-cold)',
        stroke: ch ? 'var(--vessel-line)' : 'var(--line-2)'
      });
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
  }

  /** 펌프(둥근 상자 + 운전 점 + 이름 + '→ 배기'). cx = 배관 중심. */
  function pump(cx, y, name, aux, wd) {
    wd = wd || 110;
    box(cx - wd / 2, y, wd, 30, { r: 15, fill: 'var(--panel)' });
    add(el('circle', { cx: cx - wd / 2 + 13, cy: y + 15, r: 4, fill: 'var(--valve-closed)', 'data-aux': aux }));
    txt(cx + 8, y + 15 + FS * 0.35, name, { anchor: 'middle', weight: 600, fill: 'var(--ink)' });
  }

  /** 범례. ★ 줄인 이름(MFC1 …)의 전체 이름은 상자의 툴팁으로 — 범례 줄을 늘리면 배관도가 작아져 글자가
   *  좁은 창에서 9 px 아래로 내려간다 */
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
      '<span><i style="background:var(--vessel);border:1px solid var(--vessel-line)"></i>가열</span>' +
      '<span><svg width="14" height="9" style="vertical-align:-1px">' +
      '<path d="M2,1 L7,4.5 L2,8 Z M12,1 L7,4.5 L12,8 Z" fill="none" stroke="var(--ink-faint)" stroke-width="1" ' +
      'stroke-dasharray="1.5 1.5"/></svg> 모름</span>';
  }

  /* ===================== 그리기 ===================== */
  // --- 좌표 격자 (두 장비 공통 — 왼쪽 공급 · 오른쪽 챔버 · 아래 배기). 겹침은 이 숫자만 고쳐서 잡는다 ---
  var CH_L = 380, CH_R = 590, CH_T = 70, CH_B = 330;   // 챔버
  var P_Y = 90;          // 전구체 라인(MFC1 상자 y 58 + 32)
  var A_Y = 216;         // 어시스트 라인(MFC2 상자 y 184 + 32)
  var CAN_T = 132, CAN_H = 52, CAN_W = 70;
  var CX = 390;          // 챔버 안 글자 왼쪽

  /** 전구체 · 어시스트(두 장비 같음) */
  function buildPrecursor() {
    txt(6, 20, '전구체', { size: FH, weight: 700, fill: 'var(--ink)' });
    mfcBox(6, P_Y - MFC_PIPE, 1, '전구체 캐리어');
    pipe('M' + (6 + MFC_W) + ',' + P_Y + ' H' + CH_L, 'pline');
    txt(236, P_Y - 8, '전구체 라인');
    txt(311, P_Y - 8, '—', { mono: true, fill: 'var(--pv)', bind: 'h2' });

    var cans = [
      { x: 104, out: 139, tag: 'PV-1', label: '캐니스터1', ch: 0 },
      { x: 190, out: 245, tag: 'PV-2', label: '캐니스터2', ch: 4, inlet: 205, atag: 'PV-A1' },
      { x: 276, out: 331, tag: 'PV-3', label: '캐니스터3', ch: 5, inlet: 291, atag: 'PV-A2' }
    ];
    cans.forEach(function (c) {
      vessel(c.x, CAN_T, CAN_W, CAN_H, c.label, c.ch);
      pipe('M' + c.out + ',' + CAN_T + ' V' + P_Y, 'can' + c.tag);
      valve(c.tag, c.out, (P_Y + CAN_T) / 2 + 0, 'r');
      if (c.inlet) {
        pipe('M' + c.inlet + ',' + A_Y + ' V' + (CAN_T + CAN_H), 'asst' + c.atag);
        valve(c.atag, c.inlet, (A_Y + CAN_T + CAN_H) / 2, 'l');     // 왼쪽 — 오른쪽은 '어시스트 N2'
      }
    });

    mfcBox(6, A_Y - MFC_PIPE, 2, '어시스트');
    pipe('M' + (6 + MFC_W) + ',' + A_Y + ' H296', 'aline');
    txt(302, A_Y + FS * 0.35, '어시스트 N2');
  }

  /** 챔버 안 압력(CVG · CM) — y = 'CVG' 글자 기준선 */
  function buildGauges(y) {
    txt(CX, y, 'CVG');
    txt(CX, y + 22, '—', { size: FT, weight: 700, mono: true, fill: 'var(--primary)', bind: 'cvg' });
    txt(CX, y + 42, 'Torr', { fill: 'var(--ink-faint)' });
    group(null, function () {
      parent.setAttribute('data-cm', '1');
      txt(CX + 90, y, 'CM');
      txt(CX + 90, y + 22, '—', { size: FT, weight: 700, mono: true, fill: 'var(--primary)', bind: 'cm' });
    });
  }

  /** N2 벤트(VV) — 챔버 위 왼쪽에서 들어간다 */
  function buildVent() {
    pipe('M392,' + CH_T + ' V20', 'vv');
    txt(386, 24, 'N2 벤트', { anchor: 'end' });
    valve('VV', 392, 44, 'l');
  }

  function build() {
    svg.setAttribute('viewBox', '0 0 ' + VB_W + ' ' + VB_H);
    svg.innerHTML = '';
    var MAN_X = 170, R_Y1 = 310, R_Y2 = 374, R_Y3 = 438;   // 반응물 매니폴드 · 세 입력 높이
    var EX_X = 470;      // 배기 중심

    buildPrecursor();

    /* ---------- 반응물 ---------- */
    txt(6, 270, '반응물', { size: FH, weight: 700, fill: 'var(--ink)' });
    mfcBox(6, R_Y1 - MFC_PIPE, 3, '반응물 퍼지');
    vessel(6, R_Y2 - 24, MFC_W, 48, ['H2O', '캐니스터 R'], 0, 'H2O 캐니스터 R');
    mfcBox(6, R_Y3 - MFC_PIPE, 4, 'O2');

    [[R_Y1, 'PV-R1'], [R_Y2, 'PV-R2'], [R_Y3, 'PV-R3']].forEach(function (r) {
      pipe('M' + (6 + MFC_W) + ',' + r[0] + ' H' + MAN_X, 'r' + r[1]);
      valve(r[1], 124, r[0], 'a');
    });
    pipe('M' + MAN_X + ',' + R_Y1 + ' V' + R_Y3, 'manifold');
    // ★ 매니폴드 이름 · 온도는 매니폴드 오른쪽 아래(PV-R 배관 밑) — 예전 자리는 PV-R 과 겹쳤다
    txt(MAN_X + 8, 400, '반응물 매니폴드');
    txt(MAN_X + 8, 420, '—', { mono: true, fill: 'var(--pv)', bind: 'h3' });
    // 매니폴드 → PV-R → 챔버 왼쪽 아래
    pipe('M' + MAN_X + ',' + R_Y2 + ' H350 V300 H' + CH_L, 'toChamberR');
    valve('PV-R', 260, R_Y2, 'a');

    /* ---------- 챔버 ---------- */
    box(405, 8, 170, 48, { stroke: 'var(--line-2)' });
    txt(490, 28, 'RF 파워 · 매칭박스', { anchor: 'middle' });
    txt(490, 48, '—', { anchor: 'middle', mono: true, weight: 700, bind: 'rf' });
    pipe('M490,56 V' + CH_T, 'rfline');

    box(CH_L, CH_T, CH_R - CH_L, CH_B - CH_T, {
      fill: 'var(--chamber-bg)', stroke: 'var(--chamber-line)', sw: 2, r: 6
    });
    txt(CX, CH_T + 26, '공정 챔버', { size: FT, weight: 700, fill: 'var(--ink)' });
    txt(CX, CH_T + 46, '힌지 리드');
    txt(CX + 66, CH_T + 46, '—', { mono: true, bind: 'lid' });

    // RF 전극
    add(el('rect', { x: CH_L + 20, y: CH_T + 56, width: (CH_R - CH_L) - 40, height: 5, rx: 2,
                     fill: 'var(--stage)', stroke: 'var(--line-2)', 'stroke-width': .8 }));
    txt(CH_R - 10, CH_T + 82, 'RF 전극', { anchor: 'end', fill: 'var(--ink-faint)' });

    txt(CX, CH_T + 104, 'Stage · 챔버');
    txt(CX, CH_T + 125, '—', { size: FV, weight: 700, mono: true, fill: 'var(--pv)', bind: 'h1' });

    buildGauges(CH_T + 148);

    // 스테이지
    add(el('rect', { x: CH_L + 20, y: CH_B - 28, width: (CH_R - CH_L) - 40, height: 8, rx: 2,
                     fill: 'var(--stage)', stroke: 'var(--line-2)', 'stroke-width': .8 }));
    txt(CX, CH_B - 36, 'Stage', { fill: 'var(--ink-faint)' });

    buildVent();

    /* ---------- 배기 ---------- */
    // 배관은 용기 · 펌프 사이만 잇는다(상자 뒤로 지나가지 않게)
    pipe('M' + EX_X + ',' + CH_B + ' V408 M' + EX_X + ',460 V466', 'exh', 'exh');
    add(el('circle', { cx: EX_X, cy: 352, r: 9, fill: 'var(--panel)',
                       stroke: 'var(--exh)', 'stroke-width': 1.4 }));
    add(el('line', { x1: EX_X - 6, y1: 358, x2: EX_X + 6, y2: 346,
                     stroke: 'var(--exh)', 'stroke-width': 1.4 }));
    txt(EX_X + 16, 350, 'Inline PCV');
    txt(EX_X + 16, 370, '—', { mono: true, weight: 700, bind: 'pcv' });
    valve('IV-E', EX_X, 392, false);
    txt(EX_X + 16, 392 + FS * 0.35, 'IV-E 배기 격리');
    vessel(EX_X - 45, 408, 90, 52, '트랩', 6);
    pump(EX_X, 466, '드라이펌프', 'PMP');
    txt(EX_X + 62, 481 + FS * 0.35, '→ 배기');

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
    //   흐린 점선('모름', CSS #schemSvg.unknown)으로
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
      setSv('mfc' + i + 'sv', '설정 ' + (conn ? fmt.flow(m.sv) : fmt.DASH));
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
    setSv('rf', conn ? (ex.rf_on ? fmt.watt(ex.rf_fwd) + ' W (반사 ' + fmt.watt(ex.rf_ref) + ')' : 'OFF')
                     : fmt.DASH);
    var lid = conn && t.inputs0 != null ? (core.bit(t.inputs0, 5) ? '닫힘' : '열림') : fmt.DASH;
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
  }

  core.register('schematic', { render: render, update: update });
  w.viewSchematic = { render: render, update: update };
})(window, document);
