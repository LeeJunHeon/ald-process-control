/* ============================================================
 * uimodel.js — 운전 화면의 순수 판단 (v0.8.0). DOM 이 없다 — node 로 require 해서 시험한다.
 *
 * ★ 두 프로그램(PEALD · Powder)이 바이트까지 같은 파일이다(시험이 비교한다). 장비마다 다른 것은 서버의
 *   장비 구조(device.py → state.structure)로만 받는다.
 * ★ 칸 · 자리 수는 배치와 장비 구조로만 정해진다 — 여기 함수들은 실시간 값으로 항목 수 · 순서를 바꾸지 않는다
 *   (상태 · 글자 · 색만 바뀐다). 묶음 표시('+N OK' · '+N more')는 없다.
 *
 *   pickLayout · layoutBox  창 크기 → 배치(좁은 960×1000 · 넓은 1600×840) · 배율 · 캔버스
 *   stateLevel · stripModel 상단 상태 띠 다섯 칸
 *   checkDetail · startSlots 시작 조건 자리 10 개
 *   lamp · annunciator      램프판(Interlock · Input)
 *   valveStrip              밸브 띠
 *   events                  최근 사건(넓은 배치)
 *   toastMerge · busyStore  알림 합치기 · 명령 진행 중 잠금
 * ============================================================ */
(function (root) {
  'use strict';

  var DASH = '—';
  function T_(k, fb) { return root.T ? root.T(k, fb) : (fb == null ? k : fb); }
  function F() { return root.fmt || { DASH: DASH, torr: String, hms: String, num: String, pct: String, watt: String }; }
  function bit(word, n) { return word != null && !!((Number(word) >> n) & 1); }
  function find(list, pred) { for (var i = 0; i < (list || []).length; i++) if (pred(list[i])) return list[i]; return null; }

  /* ===================== 배치 ===================== */
  // 기준 크기 · 늘어날 수 있는 범위(그 밖은 배율로). ★ 넓은 쪽은 가로 공간을 실제로 쓴다
  var LAYOUTS = {
    narrow: { w: 960, h: 1000, maxW: 1200, maxH: 1600 },
    wide: { w: 1600, h: 840, maxW: 2000, maxH: 1000 }
  };
  var SWITCH_MARGIN = 1.03;       // 흔들림 막기 — 다른 배치로 바꾸는 것은 그쪽 배율이 3 % 이상 클 때만

  function scaleOf(name, w, h) { var L = LAYOUTS[name]; return Math.min(w / L.w, h / L.h); }

  /** 글자가 더 크게 보이는 배치. cur(지금 배치)가 있으면 다른 쪽 배율이 3 % 이상 클 때만 바꾼다(같으면 narrow). */
  function pickLayout(w, h, cur) {
    var sn = scaleOf('narrow', w, h), sw = scaleOf('wide', w, h);
    if (cur === 'narrow') return sw > sn * SWITCH_MARGIN ? 'wide' : 'narrow';
    if (cur === 'wide') return sn > sw * SWITCH_MARGIN ? 'narrow' : 'wide';
    return sw > sn ? 'wide' : 'narrow';
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  /** 배치의 배율 · 캔버스 크기 — 캔버스 = clamp(창 / 배율, 기준, 최대). */
  function layoutBox(name, w, h) {
    var L = LAYOUTS[name], s = scaleOf(name, w, h);
    return { name: name, s: s, w: Math.round(clamp(w / s, L.w, L.maxW)), h: Math.round(clamp(h / s, L.h, L.maxH)) };
  }

  /* ===================== 상태 띠 ===================== */
  var ST_STOPPING = 5;

  /** 장비 상태 코드 → 수준(2 · 3 초록, 4 · 5 노랑, 6 빨강, 그 밖 회색) */
  function stateLevel(code) {
    return ({ 2: 'ok', 3: 'ok', 4: 'warn', 5: 'warn', 6: 'stop' })[code] || 'off';
  }
  var LV = { ok: 'ok', warn: 'warn', stop: 'alarm', off: 'off' };

  function auxMap(str, t, conn) {
    var o = {};
    ((str || {}).aux || []).forEach(function (a) { o[a.tag] = conn && t.aux != null && bit(t.aux, a.bit); });
    return o;
  }

  function inBit(str, tag) {
    var k = find((str || {}).inputs0, function (x) { return x.tag === tag; });
    return k ? k.bit : -1;
  }

  /** 연결 상태 — 'offline' · 'noaddr' · 'stalled' · 'lost' · 'hb' · 'id' · 'ok' */
  function linkState(t, offline) {
    var p = (t || {}).plc || {};
    if (offline || (t || {}).offline) return 'offline';
    if (p.config_error) return 'noaddr';
    if (p.stalled) return 'stalled';
    if (!p.connected) return 'lost';
    if (!p.hb_ok) return 'hb';
    if (p.id_state === 'wrong' || p.id_state === 'missing') return 'id';
    return 'ok';
  }

  var DOWN = {
    offline: ['Server Lost', '서버 연결이 끊겼습니다 — 다시 연결하는 중'],
    noaddr: ['No Address', 'PLC 주소가 없어 연결하지 않았습니다 — Settings 에서 넣으세요'],
    stalled: ['PLC Stalled', 'PLC 하트비트 멈춤 — PLC 가 STOP 이거나 멈췄습니다'],
    lost: ['PLC Lost', 'PLC 연결이 끊겼습니다 — 다시 연결하는 중'],
    hb: ['PLC Stalled', 'PLC 하트비트를 확인하는 중']
  };

  /** 상단 띠 다섯 칸 { dev, proc, pr, exh[pump, ive, vv], alm } — 각 {text, lvl, sub, subLvl, title}.
   *  lvl: ok · warn · alarm · off(빈 원) · unknown(점선). 칸 자리 · 수는 늘 같다(값 · 색만 바뀐다). */
  function stripModel(state, live, offline) {
    var s = state || {}, t = live || {}, str = s.structure || {}, dev = s.device || {};
    var ls = linkState(t, offline), conn = ls === 'ok' || ls === 'id';
    var f = F(), p = conn ? (t.process || {}) : {};
    var out = {};

    /* --- DEVICE STATE --- */
    if (!conn) {
      out.dev = { text: DOWN[ls][0], lvl: 'unknown', sub: DOWN[ls][1], subLvl: '', title: DOWN[ls][1] };
    } else {
      var st = t.state || {}, seq = t.seq || {};
      var name = st.code === ST_STOPPING ? 'Stopping' : (st.name || DASH);
      var sub = (st.code === ST_STOPPING ? 'after Cycle · ' : '') + 'Sequencer · ' + (seq.name || DASH);
      out.dev = { text: name, lvl: LV[stateLevel(st.code)], sub: sub, subLvl: '', title: (st.name || '') + ' — ' + sub };
      if (ls === 'id') {
        out.dev.lvl = 'alarm';
        out.dev.sub = 'Device ID mismatch — 조작 잠김';
        out.dev.subLvl = 'alarm';
      } else if ((t.plc || {}).pc_trip) {
        out.dev.sub = 'PC Trip — Alarm Reset';
        out.dev.subLvl = 'warn';
        out.dev.trip = true;            // 툴팁은 화면이 viewMain.tripTip 으로(규칙 한 곳)
      }
    }

    /* --- PROCESS --- */
    var run = !!p.running;
    var rname = p.recipe ? p.recipe + (p.number ? ' · No. ' + p.number : '') : '';
    out.proc = { text: conn ? (rname || 'No recipe selected') : DASH, lvl: '', dim: conn && !rname,
                 sub: '', subLvl: '', last: false, bar: 0, barLvl: '', title: rname };
    if (run && p.total_ms) {
      out.proc.bar = Math.max(0, Math.min(1, 1 - (p.remaining_ms || 0) / p.total_ms));
      out.proc.barLvl = (p.paused || p.stop_reserved) ? 'warn' : 'ok';
    }
    if (run) {
      var cyc = 'Cycle ' + (p.cycle || 0) + ' / ' + (p.block_repeat || DASH);
      var rem = p.total_ms ? 'Remaining ' + f.hms((p.remaining_ms || 0) / 1000) : '';
      var head = p.adopted ? 'Adopted +' + f.hms(p.elapsed_s) : cyc;
      out.proc.sub = (p.paused ? 'Paused · ' : '') + [head, rem].filter(Boolean).join(' · ');
      out.proc.subLvl = (p.paused || p.stop_reserved) ? 'warn' : '';
    } else if (p.phase && p.phase !== 'idle' && p.message) {
      out.proc.sub = p.message;
      out.proc.subLvl = 'warn';
    } else if (p.last_result) {
      out.proc.sub = p.last_result;
      out.proc.last = true;
      out.proc.subLvl = root.T && root.T.resultLevel ? root.T.resultLevel(p.last_result) : '';
    }

    /* --- CHAMBER PRESSURE --- */
    var pr = conn ? (t.pressure || {}) : {};
    var unconf = (s.unconfirmed || []).indexOf('CVG Pressure') >= 0;
    out.pr = { text: conn ? f.torr(pr.cvg) : DASH, unit: 'Torr · CVG', unconf: unconf, lvl: conn ? '' : 'unknown',
               sub: '', subLvl: '', title: unconf ? 'CVG 환산이 확정되지 않았습니다(Settings — 환산 확인)' : '' };
    if (conn && run) {
      var ex = t.extra || {};
      out.pr.sub = 'In process' + (dev.has_pcv ? ' · PCV ' + f.pct(ex.pcv_pv) + ' %' : '') +
        (pr.cm_installed ? ' · CM ' + f.torr(pr.cm) : '');
    } else if (conn) {
      var base = find(p.checks, function (c) { return c.key === 'base'; });
      if (base) {
        out.pr.sub = 'Base ' + f.torr(base.target) + ' Torr · ' + (base.ok ? 'reached' : 'not reached');
        out.pr.subLvl = base.ok ? '' : 'warn';
      }
      if (pr.cm_installed) out.pr.sub += (out.pr.sub ? ' · ' : '') + 'CM ' + f.torr(pr.cm);
    } else {
      out.pr.sub = DOWN[ls] ? DOWN[ls][0] : '';
    }

    /* --- PUMP · EXHAUST · VENT --- */
    var am = auxMap(str, t, conn), i0 = t.inputs0;
    var fb = bit(i0, inBit(str, 'PMP-RUN')), palm = bit(i0, inBit(str, 'PMP-ALM'));
    var lo = bit(i0, inBit(str, 'IVE-O')), lc = bit(i0, inBit(str, 'IVE-C'));
    var pump, ive, vv;
    if (!conn) {
      pump = { name: 'Pump', text: DASH, lvl: 'unknown' };
      ive = { name: 'IV-E', text: DASH, lvl: 'unknown' };
      vv = { name: 'VV', text: DASH, lvl: 'unknown' };
    } else {
      pump = palm ? { name: 'Pump', text: am.PMP ? 'ON · Alarm' : 'Alarm', lvl: 'alarm' }
        : am.PMP ? (fb ? { name: 'Pump', text: 'ON', lvl: 'ok' } : { name: 'Pump', text: 'ON · No FB', lvl: 'warn' })
          : { name: 'Pump', text: 'OFF', lvl: 'off' };
      ive = am['IV-E'] ? (lo ? { name: 'IV-E', text: 'Open', lvl: 'ok' } : { name: 'IV-E', text: 'Opening', lvl: 'warn' })
        : (lc ? { name: 'IV-E', text: 'Closed', lvl: 'off' } : { name: 'IV-E', text: 'Closing', lvl: 'warn' });
      vv = am.VV ? { name: 'VV', text: 'Open', lvl: 'ok' } : { name: 'VV', text: 'Closed', lvl: 'off' };
    }
    out.exh = [pump, ive, vv];

    /* --- ALARM --- */
    if (!conn) {
      out.alm = { text: 'Alarm ' + DASH, lvl: 'unknown', sub: DOWN[ls] ? DOWN[ls][1] : '', subLvl: '', attn: false };
    } else {
      var al = t.alarms || [];
      var crit = al.filter(function (a) { return a.crit; }), warn = al.filter(function (a) { return !a.crit; });
      var pick = function (list) {
        return list.slice().sort(function (a, b) { return String(b.since || '').localeCompare(String(a.since || '')); })[0];
      };
      if (!al.length) {
        out.alm = { text: T_('s.noAlarm', 'No Alarm'), lvl: 'ok', sub: '', subLvl: '' };
      } else {
        var top = pick(crit.length ? crit : warn);
        out.alm = { text: crit.length ? crit.length + ' Critical' : warn.length + ' Warning',
                    lvl: crit.length ? 'alarm' : 'warn',
                    sub: top.name + (top.since ? ' · ' + top.since : '') + (crit.length && warn.length ? ' · Warning ' + warn.length : ''),
                    subLvl: crit.length ? 'alarm' : 'warn' };
      }
      out.alm.attn = !!t.alarm_new;
      out.alm.title = al.map(function (a) { return (a.crit ? 'Critical' : 'Warning') + ' · ' + a.name + ' · ' + a.since; }).join('\n');
    }
    out.known = conn;
    return out;
  }

  /** 이미 그 상태인 명령(명령줄 · 조작 창 같은 규칙) — 막는 이유 또는 ''. live 는 연결된 값이어야 한다 */
  function alreadyWhy(cmd, structure, live) {
    var am = auxMap(structure, live || {}, true);
    if (cmd === 'pump_start' && am.PMP && am['IV-E']) return '이미 펌핑 중입니다';
    if (cmd === 'pump_stop' && !am.PMP && !am['IV-E']) return '이미 멈춰 있습니다';
    if (cmd === 'vent' && am.VV) return '이미 벤트 중입니다';
    return '';
  }

  /* ===================== 시작 조건 ===================== */
  /** 시작 조건 글 — 베이스 압력은 '현재 → 목표 Torr · 시작 뒤 대기'(게이지와 같은 fmt.torr), 그 밖은 서버 detail. */
  function checkDetail(c) {
    if (c && c.key === 'base' && ('cur' in c || 'target' in c)) {
      var f = F();
      return f.torr(c.cur) + ' → ' + f.torr(c.target) + ' Torr' + (c.ok ? '' : ' · 시작 뒤 대기');
    }
    return (c && c.detail) || '';
  }

  /** 시작 조건 자리 — slots(장비 구조 START_SLOTS) 수 · 순서 그대로. progress: live.process,
   *  ctx: { conn, running }. 반환 { items: [{label, icon, text, title, o3btn}], summary: {text, lvl}, dim }.
   *  icon: ok · no · wait(베이스 압력 — 시작 뒤 대기로 채움) · na(그 항목이 없음) · unknown(연결 · 장비 ID 를 모름) */
  function startSlots(slots, progress, ctx) {
    var p = progress || {}, c = ctx || {};
    var checks = p.checks || [];
    var byKey = {};
    checks.forEach(function (x) { byKey[x.key] = x; });
    var noConn = !c.conn || !checks.length;
    var idOnly = !noConn && checks.every(function (x) { return x.key === 'plc' || x.key === 'device_id'; });
    var items = (slots || []).map(function (sl, i) {
      var got = (sl.keys || []).map(function (k) { return byKey[k]; }).filter(Boolean);
      var it = { label: sl.label, icon: 'unknown', text: '', title: '', o3btn: false };
      if (noConn) {
        if (i === 0) {
          var pc = byKey.plc;
          it.icon = 'no';
          it.text = pc ? (pc.detail || '연결 안 됨') : '연결 안 됨';
          it.title = it.text;
        } else {
          it.text = '연결되면 확인';
        }
        return it;
      }
      if (!got.length) {
        if (idOnly) { it.text = 'Device ID 를 먼저 맞추세요'; return it; }
        it.icon = 'na';
        it.text = (!p.recipe && sl.norecipe) ? sl.norecipe : (sl.absent || DASH);
        it.title = sl.label + ' — ' + it.text;
        return it;
      }
      var bad = got.filter(function (x) { return !x.ok; });
      var show = bad[0] || got[0];
      it.icon = !bad.length ? 'ok' : (bad.every(function (x) { return x.key === 'base'; }) ? 'wait' : 'no');
      it.text = checkDetail(show);
      it.title = got.map(function (x) { var dd = checkDetail(x); return x.label + (dd ? ' — ' + dd : ''); }).join('\n');
      it.o3btn = it.icon === 'no' && bad.some(function (x) { return x.action === 'o3_on'; });
      return it;
    });
    var nNo = items.filter(function (x) { return x.icon === 'no'; }).length;
    var summary = c.running ? { text: '공정 중 — 다음 시작 때 확인', lvl: '' }
      : nNo ? { text: T_('s.notReady', 'Not ready') + ' · ' + nNo, lvl: 'alarm' } : { text: T_('s.ready', 'Ready'), lvl: 'ok' };
    return { items: items, summary: summary, dim: !!c.running };
  }

  /* ===================== 램프판 ===================== */
  /** 램프 하나의 수준 — ok(초록) · warn(노랑 = 아직 조건이 안 됨) · alarm(빨강) · off(빈 원) · unknown(점선).
   *  kind 'ilk': 인터락(bad = 켜지면 이상) · 'in': 입력(ok_when True / False / None). word 가 없으면(끊김) unknown */
  function lamp(word, def, kind) {
    if (word == null || !def) return 'unknown';
    var on = bit(word, def.bit);
    if (kind === 'ilk') return def.bad ? (on ? 'alarm' : 'off') : (on ? 'ok' : 'warn');
    if (def.ok_when === true) return on ? 'ok' : 'alarm';
    if (def.ok_when === false) return on ? 'alarm' : 'off';
    return on ? 'ok' : 'off';
  }

  // 묶음 이름 툴팁(한글 — 용어 설명)
  var ANN_TIPS = {
    safety: '안전 입력 — 초록 = 정상 조건 충족 · 빨강 = 이상 · 빈 원 = 꺼짐(정상)',
    permit: 'PLC 가 계산한 허가 — 초록 = 해도 됨 · 노랑 = 아직 조건이 안 됨',
    state: '상태 입력 — 초록 = 켜짐 · 빈 원 = 꺼짐',
    rf: 'RF 발생기 허가 · 입력 — 초록 = 해도 됨 · 켜짐, 노랑 = 아직 조건이 안 됨, 빨강 = 알람',
    o3: 'O3 라인 허가 · 입력(발생기 · 실내 O3 · 바이패스 펌프) — 초록 = 해도 됨 · 켜짐, 노랑 = 아직 조건이 안 됨, 빨강 = 알람'
  };

  function annDef(str, key) {
    var i = key.indexOf(':'), kind = key.slice(0, i), tag = key.slice(i + 1);
    var list = kind === 'in0' ? str.inputs0 : kind === 'in1' ? str.inputs1 : str.interlocks;
    return { kind: kind, def: find(list, function (x) { return x.tag === tag; }) };
  }

  /** 램프판 — 장비 구조의 줄 · 자리 그대로(상태와 상관없이 같은 수 · 순서 · 이름). */
  function annunciator(structure, live, conn) {
    var str = structure || {}, t = live || {};
    return (str.annunciator || []).map(function (row) {
      return {
        group: row.group, title: ANN_TIPS[row.group] || '',
        items: (row.items || []).map(function (key) {
          var a = annDef(str, key), d = a.def || {};
          var word = !conn ? null : a.kind === 'in0' ? t.inputs0 : a.kind === 'in1' ? t.inputs1 : t.interlock;
          var nm = d.lamp || d.short || d.tag || key;
          var title = a.kind === 'ilk' ? nm + ' — ' + (d.why || '')
            : nm + ' — ' + (d.desc || d.name || '') + ' (입력 워드 ' + (a.kind === 'in0' ? 0 : 1) + ' b' + d.bit + ')';
          return { key: key, name: nm, lvl: lamp(word, a.def, a.kind === 'ilk' ? 'ilk' : 'in'), title: title };
        })
      };
    });
  }

  /** 램프판 줄 수 — 묶음은 새 줄에서 시작하고 cols 를 넘으면 다음 줄(배치가 정할 때 한 번). */
  function annRows(structure, cols) {
    return ((structure || {}).annunciator || []).reduce(function (n, r) {
      return n + Math.max(1, Math.ceil((r.items || []).length / cols));
    }, 0);
  }

  /* ===================== 밸브 띠 ===================== */
  var AUX_FP = { 'VV': 'vent:VV', 'IV-E': 'pump:PMP', 'IV-B': 'o3:O3' };
  var VALUE_FP = { PCV: 'pcv:PCV', RF: 'rf:RF', O3: 'o3:O3', BPMP: 'o3:O3' };
  var STATE_TIP = {
    open: 'Open — 출력 켜짐',
    closed: 'Closed — 출력 꺼짐',
    pending: 'Pending — 요청은 들어갔지만 Valve Permit 이 없어 출력이 꺼져 있음. 허가가 생기면 바로 열립니다',
    unknown: 'Unknown — 연결이 끊겨 상태를 모름',
    auto: 'PLC 가 자동으로 다루는 밸브(O3 발생기 켜짐 · PV-R 닫힘이면 열림)'
  };

  /** 밸브 띠 — 장비 구조(valve_strip)의 묶음 · 순서 그대로. opts: { recipeValves }.
   *  반환 { groups: [[{key, kind, tag, label, state, value, auto, fp, title}]], pending: [이름] } */
  function valveStrip(structure, live, conn, opts) {
    var str = structure || {}, t = live || {}, o = opts || {}, f = F();
    var mn = t.manual || {}, ex = t.extra || {};
    var pend = conn ? (mn.pending || []).concat(mn.aux_pending || []) : [];
    var rv = o.recipeValves || [];
    var am = auxMap(str, t, conn);
    var pending = [];
    var groups = (str.valve_strip || []).map(function (g) {
      return g.map(function (key) {
        var i = key.indexOf(':'), kind = key.slice(0, i), tag = key.slice(i + 1);
        var it = { key: key, kind: kind, tag: tag, label: tag, state: 'unknown', value: '', auto: false, fp: '', title: '' };
        if (kind === 'valve' || kind === 'aux') {
          var d = find(kind === 'valve' ? str.valves : str.aux, function (x) { return x.tag === tag; }) || {};
          var on = conn && (kind === 'valve' ? (t.valves != null && bit(t.valves, d.bit)) : !!am[tag]);
          it.auto = kind === 'valve' && (!!d.auto || (rv.length > 0 && rv.indexOf(tag) < 0));
          it.state = !conn ? 'unknown' : on ? 'open' : pend.indexOf(tag) >= 0 ? 'pending' : 'closed';
          it.fp = it.auto ? '' : kind === 'valve' ? 'valve:' + tag : (AUX_FP[tag] || '');
          it.title = tag + (d.name ? ' — ' + d.name : '') + '\n' + STATE_TIP[it.state] + (it.auto ? '\n' + STATE_TIP.auto : '');
          if (it.state === 'pending') pending.push(tag);
        } else {
          it.fp = VALUE_FP[tag] || '';
          if (!conn) it.value = DASH;
          else if (tag === 'PCV') it.value = f.pct(ex.pcv_pv) + ' %';
          else if (tag === 'RF') it.value = ex.rf_on ? f.watt(ex.rf_fwd) + ' W' : 'OFF';
          else if (tag === 'O3') it.value = ex.o3_on ? f.num(ex.o3_pv, 1) + ' ' + (ex.o3_unit || '') : 'OFF';
          else if (tag === 'BPMP') it.value = am.BPMP ? 'ON' : 'OFF';
          it.state = !conn ? 'unknown' : (tag === 'BPMP' ? (am.BPMP ? 'open' : 'closed')
            : tag === 'RF' ? (ex.rf_on ? 'open' : 'closed') : tag === 'O3' ? (ex.o3_on ? 'open' : 'closed') : '');
          it.title = tag + ' — ' + ({ PCV: 'Inline PCV 개도(Position)', RF: 'RF 순방향 전력', O3: 'O3 농도(Actual)',
                                      BPMP: '바이패스 펌프' })[tag];
        }
        return it;
      });
    });
    return { groups: groups, pending: pending };
  }

  /* ===================== 최근 사건 ===================== */
  /** 최신이 위 — n 줄(배치가 정한 수). level: ok · info · warn · err */
  function events(logs, n) {
    return (logs || []).slice(-Math.max(0, n | 0)).reverse().map(function (l) {
      var lv = l.level === 'error' ? 'err' : (l.level || 'info');
      return { lvl: lv, ts: l.ts || '', msg: l.msg || '' };
    });
  }

  /* ===================== 알림 합치기 ===================== */
  /** list: 보이는 알림 [{msg, level, n}] (오래된 것이 앞). 같은 수준 + 같은 글이 있으면 새로 만들지 않고 n + 1 로
   *  맨 뒤(맨 아래)로 옮긴다. max 개를 넘으면 오래된 것부터 뺀다.
   *  반환 { list: 새 목록, item: 그 알림(합친 것이면 같은 객체), merged, dropped: 뺀 것 } */
  function toastMerge(list, msg, level, max) {
    var lv = level || 'info', arr = (list || []).slice(), item = null;
    for (var i = 0; i < arr.length; i++) {
      if (arr[i].msg === msg && arr[i].level === lv) { item = arr[i]; arr.splice(i, 1); break; }
    }
    var merged = !!item;
    if (item) item.n = (item.n || 1) + 1;
    else item = { msg: msg, level: lv, n: 1 };
    arr.push(item);
    var dropped = [];
    var m = max == null ? 4 : max;
    while (arr.length > m) dropped.push(arr.shift());
    return { list: arr, item: item, merged: merged, dropped: dropped };
  }

  /** 알림 글 — 합친 수가 있으면 '×n' */
  function toastText(item) { return item.msg + (item.n > 1 ? '  ×' + item.n : ''); }

  /* ===================== 진행 중 잠금 ===================== */
  /** 명령을 보낸 뒤 그 응답(done)이 올 때까지 같은 key 의 단추를 막는다(최대 ttl — 8 s 지나면 저절로 풀림).
   *  key 예: 'cmd:pump_start' · 'valve:PV-1' · 'heater:2' · 'mfc' · 'pcv' · 'rf' · 'o3' · 'unlock' · 'leak'. */
  function busyStore(opts) {
    var o = opts || {}, ttl = o.ttl || 8000;
    var now = o.now || function () { return Date.now(); };
    var keys = {};
    function sweep() {
      var t = now();
      for (var k in keys) if (keys[k].until <= t) delete keys[k];
    }
    return {
      start: function (key, rid) { if (key && rid) keys[key] = { rid: rid, until: now() + ttl }; },
      has: function (key) { sweep(); return Object.prototype.hasOwnProperty.call(keys, key); },
      done: function (rid) {
        var hit = false;
        for (var k in keys) if (keys[k].rid === rid) { delete keys[k]; hit = true; }
        return hit;
      },
      clear: function () { keys = {}; },
      keys: function () { sweep(); return Object.keys(keys); }
    };
  }

  var UI = {
    LAYOUTS: LAYOUTS, SWITCH_MARGIN: SWITCH_MARGIN, pickLayout: pickLayout, layoutBox: layoutBox,
    stateLevel: stateLevel, linkState: linkState, stripModel: stripModel, alreadyWhy: alreadyWhy,
    checkDetail: checkDetail, startSlots: startSlots,
    lamp: lamp, annunciator: annunciator, annRows: annRows, ANN_TIPS: ANN_TIPS,
    valveStrip: valveStrip, events: events, toastMerge: toastMerge, toastText: toastText, busyStore: busyStore
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = UI;
  else root.UI = UI;
})(typeof window !== 'undefined' ? window : this);
