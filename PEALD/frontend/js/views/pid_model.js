/* ============================================================
 * views/pid_model.js — PEALD 배관도 모델 (v0.8.0). 순수 데이터 — 색은 없다(그리기가 CSS 변수로).
 *
 * ★ 장비마다 다른 것은 이 파일 하나(그리기 views/schematic.js · 검사 js/pidcheck.js 는 두 장비 같은 파일).
 * ★ 좌표를 고치면 PidCheck.validate(PidModel) 가 빈 배열인지 먼저 본다(시험이 본다).
 *
 *   W 920 · H 680 · G 8(격자)
 *   boxes     {id, kind(mfc · vessel · chamber · rf · o3gen · pump), x, y, w, h, ports {이름: [변 l·r·t·b, 모서리에서 거리]},
 *              fp, title, grp, lines(상자 안 글자 — 기준선 y, 상자 기준 x), rows(상자 안 누르기 줄), hitLabel}
 *   junctions {id: {x, y, kind 'tee'(점) | 'joint'(곧은 이음, 점 없음)}}
 *   terminals {id: {x, y}} — 열린 끝(N2 공급 · O2 공급 · '→ Exhaust')
 *   pipes     [{key, kind(pipe · exh), path: ['상자.포트' | 갈래 · 끝 id | [x, y](꺾는 점)], arrow}]
 *   valves    [{tag, x, y, o 'h'|'v', fp(없으면 'valve:태그', null 이면 조작 없음), note, grp}]
 *   inline    [{id, x, y, r, fp, label}]   labels [{text, bind, worst, x, y, w, anchor, kind(title · line · name), fp}]
 * 흐름 색 규칙(어느 배관이 언제 파란가)은 schematic.js 의 FLOW 에 둔다.
 * ============================================================ */
(function (root) {
  'use strict';

  var V = '250.0 ℃';        // 값 자리의 가장 긴 값(검사 · 그리기 폭)
  function mfc(no, y, name, grp) {
    return { id: 'mfc' + no, kind: 'mfc', x: 16, y: y, w: 120, h: 64, ports: { out: ['r', 32] }, fp: 'mfc:' + no,
             title: 'MFC' + no + ' ' + name, grp: grp,
             lines: [{ text: 'MFC' + no, y: 17, weight: 700, tone: 'faint' },
                     { bind: 'mfc' + no + 'pv', worst: '1000.0', y: 39, size: 15, weight: 700, mono: true },
                     { bind: 'mfc' + no + 'sv', worst: 'SP 1000.0 sccm', y: 59, mono: true, tone: 'sv' }] };
  }
  function can(id, x, label, ch, ports, grp) {
    var b = { id: id, kind: 'vessel', x: x, y: 216, w: 112, h: 72, ports: ports, title: label, grp: grp,
              lines: ch ? [{ text: label, y: 22, x: 56, anchor: 'middle', weight: 700, tone: 'ink' },
                           { bind: 'h' + ch, worst: V, y: 44, x: 56, anchor: 'middle', mono: true },
                           { text: 'CH' + ch, y: 64, x: 56, anchor: 'middle', tone: 'faint' }]
                : [{ text: label, y: 41, x: 56, anchor: 'middle', weight: 700, tone: 'ink' }] };
    if (ch) { b.fp = 'heater:' + ch; b.title = 'CH' + ch + ' ' + label; }
    return b;
  }

  var M = {
    key: 'peald', W: 920, H: 680, G: 8,
    boxes: [
      mfc(1, 120, 'Precursor Carrier', 0),
      mfc(2, 320, 'Assist', 0),
      can('can1', 176, 'Canister 1', 0, { out: ['t', 56] }, 0),
      can('can2', 312, 'Canister 2', 4, { out: ['t', 80], 'in': ['b', 32] }, 0),
      can('can3', 448, 'Canister 3', 5, { out: ['t', 80], 'in': ['b', 32] }, 0),
      mfc(3, 448, 'Reactant Purge', 1),
      { id: 'h2o', kind: 'vessel', x: 16, y: 528, w: 120, h: 64, ports: { out: ['r', 32] }, title: 'H2O Canister R', grp: 1,
        lines: [{ text: 'H2O', y: 37, x: 60, anchor: 'middle', weight: 700, tone: 'ink' }] },
      mfc(4, 608, 'O2', 1),
      { id: 'man', kind: 'vessel', x: 264, y: 464, w: 96, h: 192, grp: 1, fp: 'heater:3', title: 'CH3 Reactant Manifold',
        ports: { in1: ['l', 16], in2: ['l', 96], in3: ['l', 176], out: ['r', 96] },
        lines: [{ text: 'Manifold', y: 80, x: 48, anchor: 'middle', weight: 700, tone: 'ink' },
                { bind: 'h3', worst: V, y: 102, x: 48, anchor: 'middle', mono: true },
                { text: 'CH3', y: 122, x: 48, anchor: 'middle', tone: 'faint' }] },
      { id: 'rf', kind: 'rf', x: 704, y: 16, w: 160, h: 48, ports: { out: ['b', 96] }, fp: 'rf:RF', title: 'RF Power / Matcher', grp: 2,
        lines: [{ text: 'RF / Matcher', y: 20, tone: 'dim' },
                { bind: 'rf', worst: '300.0 W (Ref 12.0)', y: 40, mono: true }] },
      { id: 'ch', kind: 'chamber', x: 624, y: 88, w: 240, h: 288, grp: 2, title: 'Process Chamber',
        ports: { p: ['l', 64], r: ['l', 224], vv: ['t', 48], rf: ['t', 176], ex: ['b', 120] },
        lines: [{ text: 'Process Chamber', y: 30, x: 16, size: 15, weight: 700, tone: 'ink' },
                { text: 'Hinge Lid', y: 62, x: 16, tone: 'dim' },
                { bind: 'lid', worst: 'Closed', y: 62, x: 224, anchor: 'end', mono: true },
                { text: 'Stage · CH1', y: 90, x: 16, tone: 'dim' },
                { bind: 'h1', worst: V, y: 90, x: 224, anchor: 'end', mono: true, weight: 700 },
                { text: 'Setpoint', y: 114, x: 16, tone: 'dim' },
                { bind: 'h1sv', worst: V, y: 114, x: 224, anchor: 'end', mono: true, tone: 'sv' },
                { rule: true, y: 132 },
                { text: 'CVG', y: 168, x: 16, tone: 'dim' },
                { bind: 'cvg', worst: '760.00', y: 168, x: 176, anchor: 'end', size: 22, weight: 700, mono: true },
                { text: 'Torr', y: 168, x: 184, tone: 'faint' },
                { text: 'Unconfirmed', y: 192, x: 16, tone: 'warn', attr: 'unconf' },
                { text: 'CM', y: 228, x: 16, tone: 'dim', attr: 'cm' },
                { bind: 'cm', worst: '760.00', y: 228, x: 176, anchor: 'end', size: 15, weight: 700, mono: true, attr: 'cm' },
                { text: 'Torr', y: 228, x: 184, tone: 'faint', attr: 'cm' }],
        rows: [{ y: 90, fp: 'heater:1', title: 'CH1 Stage' }] },
      { id: 'trap', kind: 'vessel', x: 696, y: 512, w: 96, h: 64, grp: 3, fp: 'heater:6', title: 'CH6 Trap',
        ports: { 'in': ['t', 48], out: ['b', 48] },
        lines: [{ text: 'Trap', y: 17, x: 48, anchor: 'middle', weight: 700, tone: 'ink' },
                { bind: 'h6', worst: V, y: 36, x: 48, anchor: 'middle', mono: true },
                { text: 'CH6', y: 55, x: 48, anchor: 'middle', tone: 'faint' }] },
      { id: 'pump', kind: 'pump', x: 728, y: 616, w: 32, h: 32, grp: 3, fp: 'pump:PMP', title: 'Dry Pump', aux: 'PMP',
        ports: { 'in': ['t', 16], out: ['r', 16] },
        hitLabel: { text: 'Dry Pump', x: 656, y: 624, w: 64 } }
    ],
    junctions: {
      j1: { x: 232, y: 152, kind: 'tee' }, j2: { x: 392, y: 152, kind: 'tee' }, j3: { x: 528, y: 152, kind: 'tee' },
      ja: { x: 344, y: 352, kind: 'tee' }
    },
    terminals: { n2: { x: 520, y: 40 }, exo: { x: 832, y: 632 } },
    pipes: [
      { key: 'pline', path: ['mfc1.out', 'j1', 'j2', 'j3', 'ch.p'] },
      { key: 'canPV-1', path: ['can1.out', 'j1'] },
      { key: 'canPV-2', path: ['can2.out', 'j2'] },
      { key: 'canPV-3', path: ['can3.out', 'j3'] },
      { key: 'aline', path: ['mfc2.out', 'ja'] },
      { key: 'asstPV-A1', path: ['ja', 'can2.in'] },
      { key: 'asstPV-A2', path: ['ja', [480, 352], 'can3.in'] },
      { key: 'rPV-R1', path: ['mfc3.out', 'man.in1'] },
      { key: 'rPV-R2', path: ['h2o.out', 'man.in2'] },
      { key: 'rPV-R3', path: ['mfc4.out', 'man.in3'] },
      { key: 'toChamberR', path: ['man.out', [584, 560], [584, 312], 'ch.r'] },
      { key: 'rfline', path: ['rf.out', 'ch.rf'] },
      { key: 'vv', path: ['ch.vv', [672, 40], 'n2'] },
      { key: 'exh', kind: 'exh', path: ['ch.ex', 'trap.in'] },
      { key: 'exh2', kind: 'exh', path: ['trap.out', 'pump.in'] },
      { key: 'exh3', kind: 'exh', path: ['pump.out', 'exo'] }
    ],
    valves: [
      { tag: 'PV-1', x: 232, y: 184, o: 'v', grp: 0 }, { tag: 'PV-2', x: 392, y: 184, o: 'v', grp: 0 },
      { tag: 'PV-3', x: 528, y: 184, o: 'v', grp: 0 }, { tag: 'PV-A1', x: 344, y: 320, o: 'v', grp: 0 },
      { tag: 'PV-A2', x: 480, y: 320, o: 'v', grp: 0 },
      { tag: 'PV-R1', x: 200, y: 480, o: 'h', grp: 1 }, { tag: 'PV-R2', x: 200, y: 560, o: 'h', grp: 1 },
      { tag: 'PV-R3', x: 200, y: 640, o: 'h', grp: 1 }, { tag: 'PV-R', x: 584, y: 440, o: 'v', grp: 1 },
      { tag: 'VV', x: 600, y: 40, o: 'h', fp: 'vent:VV', grp: 2, aux: true },
      { tag: 'IV-E', x: 744, y: 472, o: 'v', fp: null, grp: 3, aux: true }
    ],
    inline: [
      { id: 'PCV', x: 744, y: 424, r: 10, fp: 'pcv:PCV', grp: 3, title: 'Inline PCV',
        label: { text: 'PCV', bind: 'pcv', worst: '100 %', x: 760, y: 416, w: 80 } }
    ],
    labels: [
      { kind: 'title', text: 'PRECURSOR', x: 16, y: 88, w: 96 },
      { text: 'Precursor Line · CH2', bind: 'h2', worst: V, x: 152, y: 128, w: 224, fp: 'heater:2', grp: 0,
        title: 'CH2 Precursor Line' },
      { text: 'Assist N2', x: 152, y: 328, w: 64 },
      { kind: 'title', text: 'REACTANT', x: 16, y: 416, w: 80 },
      { text: 'N2 Vent', x: 456, y: 32, w: 56, anchor: 'end' },
      { kind: 'title', text: 'EXHAUST', x: 664, y: 392, w: 72, anchor: 'end' },
      { kind: 'name', text: 'Dry Pump', x: 656, y: 624, w: 64, anchor: 'end' },
      { text: '→ Exhaust', x: 840, y: 624, w: 72 }
    ]
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = M;
  else root.PidModel = M;
})(typeof window !== 'undefined' ? window : this);
