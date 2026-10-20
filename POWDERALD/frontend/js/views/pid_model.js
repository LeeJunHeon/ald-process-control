/* ============================================================
 * views/pid_model.js — Powder ALD 배관도 모델 (v0.8.0). 순수 데이터 — 색은 없다(그리기가 CSS 변수로).
 *
 * ★ 장비마다 다른 것은 이 파일 하나(그리기 views/schematic.js · 검사 js/pidcheck.js 는 두 장비 같은 파일).
 * ★ 좌표를 고치면 PidCheck.validate(PidModel) 가 빈 배열인지 먼저 본다(시험이 본다).
 * ★ 전구체 · 반응기 · 배기 자리는 PEALD 와 같은 격자 · 같은 자리. 반응물은 O3 발생기(O2 공급) → PV-R → 반응기,
 *   발생기 출구에서 갈라져 PV-B(PLC 자동) → IV-B → 바이패스 펌프 → 배기.
 *
 *   W 920 · H 680 · G 8(격자)
 *   boxes     {id, kind(mfc · vessel · chamber · o3gen · pump), x, y, w, h, ports {이름: [변 l·r·t·b, 모서리에서 거리]},
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
    key: 'powderald', W: 920, H: 680, G: 8,
    boxes: [
      mfc(1, 120, 'Precursor Carrier', 0),
      mfc(2, 320, 'Assist', 0),
      can('can1', 176, 'Canister 1', 0, { out: ['t', 56] }, 0),
      can('can2', 312, 'Canister 2', 4, { out: ['t', 80], 'in': ['b', 32] }, 0),
      can('can3', 448, 'Canister 3', 5, { out: ['t', 80], 'in': ['b', 32] }, 0),
      { id: 'o3', kind: 'o3gen', x: 88, y: 464, w: 144, h: 64, ports: { 'in': ['l', 32], out: ['r', 32] }, fp: 'o3:O3',
        title: 'O3 Generator', grp: 1,
        lines: [{ text: 'O3 Generator', y: 22, x: 12, weight: 700, tone: 'ink' },
                { bind: 'o3', worst: '200.0 g/Nm3', y: 46, x: 28, mono: true }],
        dot: { x: 18, y: 42, aux: 'O3GEN' } },
      { id: 'bpump', kind: 'pump', x: 280, y: 624, w: 32, h: 32, grp: 1, title: 'Bypass Pump', aux: 'BPMP',
        ports: { 'in': ['t', 16], out: ['r', 16] } },
      { id: 'ch', kind: 'chamber', x: 624, y: 88, w: 240, h: 288, grp: 2, title: 'Powder Reactor',
        ports: { p: ['l', 64], r: ['l', 224], vv: ['t', 48], ex: ['b', 120] },
        lines: [{ text: 'Powder Reactor', y: 30, x: 16, size: 15, weight: 700, tone: 'ink' },
                { text: 'Lid', y: 62, x: 16, tone: 'dim' },
                { bind: 'lid', worst: 'Closed', y: 62, x: 224, anchor: 'end', mono: true },
                { text: 'Reactor · CH1', y: 90, x: 16, tone: 'dim' },
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
        rows: [{ y: 90, fp: 'heater:1', title: 'CH1 Powder Reactor' }] },
      { id: 'trap', kind: 'vessel', x: 696, y: 512, w: 96, h: 64, grp: 3, fp: 'heater:6', title: 'CH6 Trap',
        ports: { 'in': ['t', 48], out: ['b', 48] },
        lines: [{ text: 'Trap', y: 17, x: 48, anchor: 'middle', weight: 700, tone: 'ink' },
                { bind: 'h6', worst: V, y: 36, x: 48, anchor: 'middle', mono: true },
                { text: 'CH6', y: 55, x: 48, anchor: 'middle', tone: 'faint' }] },
      { id: 'pump', kind: 'pump', x: 728, y: 616, w: 32, h: 32, grp: 3, fp: 'pump:PMP', title: 'Main Pump', aux: 'PMP',
        ports: { 'in': ['t', 16], out: ['r', 16] },
        hitLabel: { text: 'Main Pump', x: 640, y: 624, w: 80 } }
    ],
    junctions: {
      j1: { x: 232, y: 152, kind: 'tee' }, j2: { x: 392, y: 152, kind: 'tee' }, j3: { x: 528, y: 152, kind: 'tee' },
      ja: { x: 344, y: 352, kind: 'tee' },
      jo3: { x: 296, y: 496, kind: 'tee' }, jb: { x: 296, y: 560, kind: 'joint' }
    },
    terminals: { n2: { x: 520, y: 40 }, exo: { x: 832, y: 632 }, o2: { x: 16, y: 496 }, bpo: { x: 392, y: 640 } },
    pipes: [
      { key: 'pline', path: ['mfc1.out', 'j1', 'j2', 'j3', 'ch.p'] },
      { key: 'canPV-1', path: ['can1.out', 'j1'] },
      { key: 'canPV-2', path: ['can2.out', 'j2'] },
      { key: 'canPV-3', path: ['can3.out', 'j3'] },
      { key: 'aline', path: ['mfc2.out', 'ja'] },
      { key: 'asstPV-A1', path: ['ja', 'can2.in'] },
      { key: 'asstPV-A2', path: ['ja', [480, 352], 'can3.in'] },
      { key: 'o2in', path: ['o2', 'o3.in'], arrow: true },
      { key: 'o3out', path: ['o3.out', 'jo3'] },
      { key: 'toChamberR', path: ['jo3', [584, 496], [584, 312], 'ch.r'] },
      { key: 'byp', path: ['jo3', 'jb'] },
      { key: 'byp2', path: ['jb', 'bpump.in'] },
      { key: 'byp3', kind: 'exh', path: ['bpump.out', 'bpo'] },
      { key: 'vv', path: ['ch.vv', [672, 40], 'n2'] },
      { key: 'exh', kind: 'exh', path: ['ch.ex', 'trap.in'] },
      { key: 'exh2', kind: 'exh', path: ['trap.out', 'pump.in'] },
      { key: 'exh3', kind: 'exh', path: ['pump.out', 'exo'] }
    ],
    valves: [
      { tag: 'PV-1', x: 232, y: 184, o: 'v', grp: 0 }, { tag: 'PV-2', x: 392, y: 184, o: 'v', grp: 0 },
      { tag: 'PV-3', x: 528, y: 184, o: 'v', grp: 0 }, { tag: 'PV-A1', x: 344, y: 320, o: 'v', grp: 0 },
      { tag: 'PV-A2', x: 480, y: 320, o: 'v', grp: 0 },
      { tag: 'PV-B', x: 296, y: 528, o: 'v', grp: 1, note: 'PLC Auto' },
      { tag: 'IV-B', x: 296, y: 592, o: 'v', fp: null, grp: 1, aux: true },
      { tag: 'PV-R', x: 584, y: 400, o: 'v', grp: 1 },
      { tag: 'VV', x: 600, y: 40, o: 'h', fp: 'vent:VV', grp: 2, aux: true },
      { tag: 'IV-E', x: 744, y: 440, o: 'v', fp: null, grp: 3, aux: true }
    ],
    inline: [],
    labels: [
      { kind: 'title', text: 'PRECURSOR', x: 16, y: 88, w: 96 },
      { text: 'Precursor Line · CH2', bind: 'h2', worst: V, x: 152, y: 128, w: 224, fp: 'heater:2', grp: 0,
        title: 'CH2 Precursor Line' },
      { text: 'Assist N2', x: 152, y: 328, w: 64 },
      { kind: 'title', text: 'REACTANT', x: 16, y: 416, w: 80 },
      { text: 'O2 Supply', x: 16, y: 472, w: 72 },
      { kind: 'name', text: 'Bypass Pump', x: 176, y: 632, w: 96, anchor: 'end' },
      { text: '→ Exhaust', x: 400, y: 632, w: 72 },
      { text: 'N2 Vent', x: 456, y: 32, w: 56, anchor: 'end' },
      { kind: 'title', text: 'EXHAUST', x: 664, y: 392, w: 72, anchor: 'end' },
      { kind: 'name', text: 'Main Pump', x: 640, y: 624, w: 80, anchor: 'end' },
      { text: '→ Exhaust', x: 840, y: 624, w: 72 }
    ]
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = M;
  else root.PidModel = M;
})(typeof window !== 'undefined' ? window : this);
