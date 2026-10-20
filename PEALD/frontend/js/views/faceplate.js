/* ============================================================
 * views/faceplate.js — 조작 창(faceplate) (v0.5.0 · v0.8.0).
 *
 * 조작 가능한 요소(data-fp="종류:키" — 배관도 g · 밸브 띠 타일 · 히터/MFC 줄 · 시작 조건의 O3 단추)를 누르면(또는 Tab 으로
 * 가서 Enter) 그 옆에 작은 조작 창이 뜬다 — 누르기 → 조작 창의 단추, 두 단계라 잘못 누름을 막는다.
 *
 *   valve  — 이름 · 출력 · 요청 · Valve Permit · 잠금 · Open / Close (잠금 해제가 필요하다 — 없으면 Unlock)
 *   mfc    — Setpoint 입력 · Apply           heater — Setpoint · Apply · ON / OFF
 *   pcv    — Target(PEALD)                   rf     — Setpoint · ON / OFF(PEALD)
 *   o3     — Setpoint · Apply · ON / OFF(Powder)
 *   pump   — Pumping Start / Stop            vent   — Vent
 *
 * ★ 명령은 「수동 조작…」 창과 같은 것을 같은 인자로 보낸다(ops.js 한 곳). 확인 창도 같은 것.
 * ★ 공정 중 · 원격 보기 · PLC 끊김 · 하트비트 멈춤 · 다른 장비 · 서버 끊김이면 창은 열리되 단추는 막히고 이유가 보인다.
 * ★ v0.8.0: 밸브 Open 은 Valve Permit 이 있어야(없으면 서버도 보내지 않는다 — 허가가 생길 때 저절로 열리는 Pending 을 만들지
 *   않게). 이미 요청 · 열림이면 Open 막힘, Close 는 요청이나 출력이 있을 때만. 이미 그 상태인 펌핑 · 벤트는 막힘.
 *   Powder O3 ON 은 O3 Permit 으로 막지 않는다(ON 이 그 허가를 만드는 순서다 — 바이패스 펌프 → IV-B → 5 s → 허가 → 발생기).
 * ★ v0.8.0: 결과는 보낸 명령의 요청 번호(rid)가 같은 알림만 결과 줄에. 응답(done)이 올 때까지(최대 8 s) 단추를 모두 막는다.
 * ★ 한 번에 하나. 바깥을 누르거나 Esc · 탭 바꿈 · 배치 바꿈에 닫힌다. 값은 실시간(입력 칸은 다시 만들지 않는다).
 * ★ model · command 는 DOM 없는 순수 함수 — 시험은 node 로 부른다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var D = '—';
  var BUSY_TIP = '응답을 기다리는 중';
  function T_(k) { return w.T ? w.T(k) : k; }
  function num(v, f) { return (v == null || !isFinite(v)) ? D : (f ? f(v) : String(v)); }
  function bit(word, n) { return !!((Number(word) >> n) & 1); }
  function ilk(str, tag) { return (str.interlocks || []).filter(function (k) { return k.tag === tag; })[0]; }
  function auxDef(str, tag) { return (str.aux || []).filter(function (a) { return a.tag === tag; })[0]; }

  /* ===================== 순수 함수 ===================== */
  /** 조작 창에 보일 것 — { title, rows: [[이름, 값]], inputs: [{id, label, unit, step, max, off}],
   *  buttons: [{a, label, on(누를 수 있나), cls}], why(막는 이유 — 문장, 한글), whyLvl('warn' 이면 노랑 안내) }.
   *  busy: 이 창의 명령이 응답을 기다리는 중이면 true — 단추를 모두 막는다 */
  function model(kind, key, s, offline, busy) {
    s = s || {};
    var t = s.live || {}, str = s.structure || {}, dev = s.device || {};
    var conn = !!(t.plc && t.plc.connected) && !offline;
    var F = w.fmt || {};
    var block = w.Ops.blockReason(w.Ops.ctx(s, offline));
    var m = { title: String(key), rows: [], inputs: [], buttons: [], why: '', whyLvl: '' };
    var extraWhy = '', extraLvl = '';

    function btn(a, label, ok, cls) { m.buttons.push({ a: a, label: label, on: !block && !busy && ok !== false, cls: cls || '' }); }

    if (kind === 'valve') {
      var v = (str.valves || []).filter(function (x) { return x.tag === key; })[0] || {};
      var mn = t.manual || {};
      var manualTags = (s.recipe_limits || {}).recipe_valves || [];
      var auto = !!v.auto || (manualTags.length > 0 && manualTags.indexOf(key) < 0);
      var out = conn && t.valves != null && bit(t.valves, v.bit);
      var req = conn && bit(mn.valve_request, v.bit);
      var vp = ilk(str, 'Valve Permit');
      var permit = conn && (mn.valve_permit != null ? !!mn.valve_permit : !!(vp && bit(t.interlock, vp.bit)));
      var miss = mn.valve_permit_missing || [];
      m.title = key;
      m.rows.push([T_('v.name'), v.name || D]);
      m.rows.push([T_('v.output'), !conn ? T_('s.unknown') : out ? T_('s.open') : T_('s.closed')]);
      m.rows.push([T_('v.request'), !conn ? D : req ? T_('s.open') : T_('s.none')]);
      m.rows.push(['Valve Permit', !conn ? D : permit ? T_('s.yes') : T_('s.no') + (miss.length ? ' — ' + miss.join(' · ') : '')]);
      m.rows.push(['Valve Lock', mn.unlocked ? T_('s.unlocked') + ' · ' + (mn.unlock_left_s || 0) + ' s' : T_('s.locked')]);
      if (auto) extraWhy = 'PLC 가 자동으로 다루는 밸브입니다 — 수동으로 열고 닫지 않습니다';
      else if (!mn.unlocked) extraWhy = '밸브 조작 잠금이 걸려 있습니다 — Unlock 을 먼저 누르세요';
      else if (conn && req && !out) {
        extraWhy = '요청은 남아 있지만 Valve Permit 이 없어 출력이 꺼져 있습니다. 허가가 생기면 바로 열립니다 — 필요 없으면 Close';
        extraLvl = 'warn';
      } else if (conn && !permit && !out) {
        extraWhy = 'Valve Permit 이 없어 열 수 없습니다 — 필요: ' + (miss.length ? miss.join(' · ') : 'Pump 운전 · IV-E 열림');
      }
      btn('open', T_('b.open'), !auto && !!mn.unlocked && permit && !req && !out);
      btn('close', T_('b.close'), !auto && !!mn.unlocked && (req || out));
      if (!auto && !mn.unlocked) btn('unlock', T_('b.unlock'), true);
    } else if (kind === 'mfc') {
      var md = (str.mfc || []).filter(function (x) { return String(x.no) === String(key); })[0] || {};
      var mv = (t.mfc || [])[Number(key) - 1] || {};
      m.title = 'MFC' + key;
      m.rows.push([T_('v.name'), md.name || D]);
      m.rows.push([T_('v.gas'), md.gas || D]);
      m.rows.push([T_('v.actual'), conn ? num(mv.pv, F.flow) + ' sccm' : D]);
      m.rows.push([T_('v.setpoint'), conn ? num(mv.sv, F.flow) + ' sccm' : D]);
      m.rows.push([T_('v.fullScale'), md.full_scale_sccm == null ? 'Unset' : md.full_scale_sccm + ' sccm']);
      var fsOk = md.full_scale_sccm != null;
      if (!fsOk) extraWhy = '풀스케일이 정해지지 않아 설정할 수 없습니다(설정 탭)';
      m.inputs.push({ id: 'sp', label: T_('v.setpoint'), unit: 'sccm', step: 0.1, off: !!block || !fsOk });
      btn('apply', T_('b.apply'), fsOk);
    } else if (kind === 'heater') {
      var hd = (str.heaters || []).filter(function (x) { return String(x.ch) === String(key); })[0] || {};
      var hv = (t.heaters || [])[Number(key) - 1] || {};
      m.title = 'CH' + key + (hd.name ? ' ' + hd.name : '');
      m.rows.push([T_('v.actual'), conn && hv.comm_ok ? num(hv.pv, F.temp) + ' ℃' : D]);
      m.rows.push([T_('v.setpoint'), conn ? num(hv.sv, F.temp) + ' ℃' : D]);
      m.rows.push([T_('v.power'), !conn || hv.power == null ? D : hv.power ? T_('s.on') : T_('s.off')]);
      m.rows.push([T_('v.status'), !conn ? D : !hv.comm_ok ? T_('s.commLost') : hv.alarm ? T_('s.ctrlAlarm')
        : hd.max_c == null ? T_('s.limitUnset') : T_('s.normal')]);
      var en = !!hd.enabled, lim = hd.max_c != null;
      if (!en) extraWhy = '이 채널은 설정에서 쓰지 않습니다';
      else if (!lim) extraWhy = '과온 한계가 없어 설정 · 전원 켜기를 막습니다(설정 탭)';
      else if (conn && hv.comm_ok === false) extraWhy = hv.power_block || '온도조절기 통신 없음 — 켤 수 없습니다(끄기는 됩니다)';
      m.inputs.push({ id: 'sp', label: T_('v.setpoint'), unit: '℃', step: 1, off: !!block || !en || !lim });
      btn('apply', T_('b.apply'), en && lim);
      btn('on', T_('b.on'), en && lim);
      btn('off', T_('b.off'), en);
    } else if (kind === 'pcv') {
      var ex = t.extra || {};
      m.title = 'PCV';
      m.rows.push([T_('v.position'), conn ? num(ex.pcv_pv, F.pct) + ' %' : D]);
      m.rows.push([T_('v.target'), conn ? num(ex.pcv_sv, F.pct) + ' %' : D]);
      m.inputs.push({ id: 'sp', label: T_('v.target'), unit: '%', step: 1, max: 100, off: !!block });
      btn('apply', T_('b.apply'), true);
    } else if (kind === 'rf') {
      var er = t.extra || {};
      var permitRf = ilk(str, 'RF Permit');
      m.title = 'RF';
      m.rows.push([T_('v.forward') + ' / ' + T_('v.reflected'), conn ? num(er.rf_fwd, F.watt) + ' / ' + num(er.rf_ref, F.watt) + ' W' : D]);
      m.rows.push([T_('v.setpoint'), conn ? num(er.rf_sv, F.watt) + ' W' : D]);
      m.rows.push([T_('v.status'), conn ? (er.rf_on ? T_('s.on') : T_('s.off')) : D]);
      var rfOk = !permitRf || (conn && bit(t.interlock, permitRf.bit));
      if (permitRf) m.rows.push(['RF Permit', conn ? (rfOk ? T_('s.normal') : 'NG') : D]);
      // ★ v0.5.1(D3): RF 허가가 NG 면 ON 을 막고 이유를 보인다(끄기는 그대로). RF Permit 은 RF 와 무관하게 생긴다
      if (!rfOk) extraWhy = 'RF Permit NG — 허가 조건이 맞을 때만 켤 수 있습니다';
      m.inputs.push({ id: 'sp', label: T_('v.setpoint'), unit: 'W', step: 1, off: !!block });
      btn('on', T_('b.on'), rfOk);
      btn('off', T_('b.off'), true);
    } else if (kind === 'o3') {
      var eo = t.extra || {}, mo = t.manual || {};
      var op = ilk(str, 'O3 Permit');
      var am = auxMap(str, t, conn);
      var gen = auxDef(str, 'O3GEN');
      var lineOn = conn && !!gen && bit(mo.aux_request, gen.bit);
      var bpRun = (((str.inputs1 || []).filter(function (k) { return k.tag === 'BP-RUN'; })[0]) || {});
      m.title = T_('eq.o3gen');
      m.rows.push([T_('v.actual'), conn ? num(eo.o3_pv, function (x) { return F.num(x, 1); }) + ' ' + (eo.o3_unit || '') : D]);
      m.rows.push([T_('v.setpoint'), conn ? num(eo.o3_sv, function (x) { return F.num(x, 1); }) + ' ' + (eo.o3_unit || '') : D]);
      m.rows.push([T_('v.generator'), conn ? (am.O3GEN ? T_('s.on') : T_('s.off')) : D]);
      m.rows.push([T_('eq.bypassPump'), conn ? (am.BPMP ? T_('s.on') : T_('s.off')) + ' · ' +
        (bpRun.bit != null && bit(t.inputs1, bpRun.bit) ? T_('s.feedbackOk') : T_('s.noFeedback')) : D]);
      m.rows.push(['IV-B', conn ? (am['IV-B'] ? T_('s.open') : T_('s.closed')) : D]);
      if (op) m.rows.push(['O3 Permit', conn ? (bit(t.interlock, op.bit) ? T_('s.yes') : T_('s.no')) : D]);
      m.rows.push([T_('v.offDelay'), conn && mo.o3_off_left_s > 0 ? mo.o3_off_left_s + ' s' : D]);
      m.note = 'Line ON = 바이패스 펌프 → IV-B → 5 s 뒤 O3 Permit → 발생기 순서';
      var hasSv = conn && eo.o3_sv > 0;
      if (lineOn) extraWhy = 'O3 라인이 이미 켜져 있습니다';
      else if (!hasSv) extraWhy = 'O3 Setpoint 를 넣고 Apply 한 뒤 ON — 0 이면 발생기가 켜져도 O3 가 나오지 않습니다';
      m.inputs.push({ id: 'sp', label: T_('v.setpoint'), unit: eo.o3_unit || '', step: 0.1, off: !!block });
      btn('apply', T_('b.apply'), true);
      btn('on', T_('b.on'), !lineOn && hasSv);         // ★ O3 Permit 으로 막지 않는다(ON 이 그 허가를 만든다)
      btn('off', T_('b.off'), true);
    } else if (kind === 'pump') {
      var ap = auxMap(str, t, conn);
      m.title = dev.has_o3 ? T_('eq.mainPump') : T_('eq.dryPump');
      m.rows.push([T_('v.status'), conn ? (ap['PMP'] ? T_('s.on') : T_('s.off')) : D]);
      m.rows.push(['Feedback', conn ? (bit(t.inputs0, 7) ? T_('s.feedbackOk') : T_('s.noFeedback')) : D]);
      m.rows.push(['IV-E', conn ? (ap['IV-E'] ? T_('s.open') : T_('s.closed')) : D]);
      m.rows.push(['CVG', conn ? num((t.pressure || {}).cvg, F.torr) + ' Torr' : D]);
      var ps = conn ? w.UI.alreadyWhy('pump_start', str, t) : '', pt = conn ? w.UI.alreadyWhy('pump_stop', str, t) : '';
      extraWhy = ps || pt;
      btn('pump_start', T_('b.pumpStart'), !ps);
      btn('pump_stop', T_('b.pumpStop'), !pt);
    } else if (kind === 'vent') {
      var av = auxMap(str, t, conn);
      var vpv = ilk(str, 'Vent Permit');
      m.title = 'VV — ' + T_('eq.vent');
      m.rows.push(['VV', conn ? (av['VV'] ? T_('s.open') : T_('s.closed')) : D]);
      m.rows.push(['IV-E', conn ? (av['IV-E'] ? T_('s.open') : T_('s.closed')) : D]);
      m.rows.push(['CVG', conn ? num((t.pressure || {}).cvg, F.torr) + ' Torr' : D]);
      if (vpv) m.rows.push(['Vent Permit', conn ? (bit(t.interlock, vpv.bit) ? T_('s.normal') : 'NG') : D]);
      var vw = conn ? w.UI.alreadyWhy('vent', str, t) : '';
      extraWhy = vw;
      btn('vent', T_('b.vent'), !vw);
    }
    m.why = block ? block + ' — 조작할 수 없습니다' : busy ? BUSY_TIP : extraWhy;
    m.whyLvl = (!block && !busy) ? extraLvl : '';
    return m;
  }

  function auxMap(str, t, conn) {
    var o = {};
    (str.aux || []).forEach(function (a) { o[a.tag] = conn && t.aux != null && bit(t.aux, a.bit); });
    return o;
  }

  /** 단추 → [명령, 인자] (수동 조작 창과 같은 것 — ops.js). 값이 필요한데 없으면 null.
   *  confirm: 보내기 전에 거칠 확인 창의 종류(수동 창 · 명령줄과 같은 글). */
  function command(kind, key, a, value) {
    var O = w.Ops, has = value !== '' && value != null && isFinite(Number(value));
    if (kind === 'valve') {
      if (a === 'open') return { pair: O.valve(key, true) };
      if (a === 'close') return { pair: O.valve(key, false) };
      if (a === 'unlock') return { pair: O.unlock(true) };
    } else if (kind === 'mfc' && a === 'apply') {
      return has ? { pair: O.mfc(key, Number(value)) } : null;
    } else if (kind === 'heater') {
      if (a === 'apply') return has ? { pair: O.heaterSv(key, Number(value)), confirm: 'heater' } : null;
      if (a === 'on') return { pair: O.heaterPower(key, true) };
      if (a === 'off') return { pair: O.heaterPower(key, false) };
    } else if (kind === 'pcv' && a === 'apply') {
      return has ? { pair: O.pcv(Number(value)) } : null;
    } else if (kind === 'rf') {
      if (a === 'on') return has && Number(value) ? { pair: O.rfOn(Number(value)), confirm: 'rf' } : null;
      if (a === 'off') return { pair: O.rfOff() };
    } else if (kind === 'o3') {
      if (a === 'apply') return has && Number(value) ? { pair: O.o3('set', Number(value)) } : null;
      if (a === 'on') return has && Number(value) ? { pair: O.o3('on', Number(value)), confirm: 'o3' } : null;
      if (a === 'off') return { pair: O.o3('off') };
    } else if (kind === 'pump') {
      if (a === 'pump_start') return { pair: O.plain('pump_start') };
      if (a === 'pump_stop') return { pair: O.plain('pump_stop'), confirm: 'pump_stop' };
    } else if (kind === 'vent' && a === 'vent') {
      return { pair: O.plain('vent'), confirm: 'vent' };
    }
    return null;
  }

  /** 진행 중 잠금 key — 수동 조작 창과 같은 key(한쪽에서 누르면 다른 쪽 단추도 막힘) */
  function busyKey(kind, key, a) {
    if (kind === 'valve') return a === 'unlock' ? 'unlock' : 'valve:' + key;
    if (kind === 'heater') return 'heater:' + key;
    if (kind === 'pump' || kind === 'vent') return 'cmd:' + a;
    return kind;
  }
  function busyKeys(kind, key) {
    if (kind === 'valve') return ['valve:' + key, 'unlock'];
    if (kind === 'heater') return ['heater:' + key];
    if (kind === 'pump') return ['cmd:pump_start', 'cmd:pump_stop'];
    if (kind === 'vent') return ['cmd:vent'];
    return [kind];
  }
  function isBusy(kind, key) {
    var b = w.core && w.core.busy;
    return !!b && busyKeys(kind, key).some(function (k) { return b.has(k); });
  }

  /* ===================== 화면 ===================== */
  var cur = null;          // { kind, key, anchor, rid }

  function box() { return d.getElementById('faceplate'); }

  function open(kind, key, anchor) {
    var b = box();
    if (!b) return;
    if (cur && cur.anchor && cur.anchor.classList) cur.anchor.classList.remove('fp-on');
    cur = { kind: kind, key: key, anchor: anchor, rid: 0 };
    if (anchor && anchor.classList) anchor.classList.add('fp-on');
    var m = model(kind, key, w.core.state, w.core.offline, isBusy(kind, key));
    var h = '<div class="fp-head"><span class="fp-tag" data-fpbind="title"></span>' +
      '<button class="fp-x" data-fpa="x" title="닫기 (Esc)" aria-label="Close">✕</button></div><div class="fp-body">';
    m.rows.forEach(function (r, i) {
      h += '<div class="fp-row"><span class="k">' + w.core.esc(r[0]) + '</span><span class="v" data-fprow="' + i + '"></span></div>';
    });
    m.inputs.forEach(function (x) {
      h += '<div class="fp-row"><span class="k">' + w.core.esc(x.label) + '</span><input type="number" min="0"' +
        (x.max != null ? ' max="' + x.max + '"' : '') + ' step="' + x.step + '" data-fpin="' + x.id + '">' +
        '<span class="u" data-fpunit="' + x.id + '">' + w.core.esc(x.unit || '') + '</span></div>';
    });
    if (m.note) h += '<div class="fp-note">' + w.core.esc(m.note) + '</div>';
    h += '<div class="fp-btns" data-fpbind="btns">' + btnsHtml(m) + '</div><div class="fp-why" data-fpbind="why"></div><div class="fp-res" data-fpbind="res"></div></div>';
    b.innerHTML = h;
    b.setAttribute('aria-label', m.title);
    b.hidden = false;
    // O3 ON 은 지금 설정값으로 — 입력 칸을 그 값으로 채워 둔다(ON 이 입력값을 보낸다)
    var eo = ((w.core.state || {}).live || {}).extra || {};
    var inp = b.querySelector('[data-fpin="sp"]');
    if (kind === 'o3' && inp && eo.o3_sv > 0) inp.value = String(Math.round(eo.o3_sv * 10) / 10);
    place(b, anchor);
    paint();
    var first = b.querySelector('input:not([disabled]), .fp-btn:not([disabled])');
    if (first) { try { first.focus(); } catch (e) { /* 없음 */ } }
  }

  function btnsHtml(m) {
    return m.buttons.map(function (x) {
      return '<button class="btn sm fp-btn ' + x.cls + '" data-fpa="' + x.a + '">' + w.core.esc(x.label) + '</button>';
    }).join('');
  }

  /** 누른 요소 옆 — 오른쪽, 자리가 없으면 왼쪽, 그래도 없으면 아래. #app 안으로 자른다(#app 배율 zoom 을 되돌린다) */
  function place(b, anchor) {
    var app = b.parentNode;
    if (!app || !anchor || !anchor.getBoundingClientRect) return;
    var wr = app.getBoundingClientRect(), ar = anchor.getBoundingClientRect();
    var k = app.offsetWidth ? wr.width / app.offsetWidth : 1;
    var ax = (ar.left - wr.left) / k, ay = (ar.top - wr.top) / k, aw = ar.width / k, ah = ar.height / k;
    var bw = b.offsetWidth || 260, bh = b.offsetHeight || 180;
    var W = app.offsetWidth, H = app.offsetHeight;
    var x, y;
    if (ax + aw + 6 + bw <= W - 2) { x = ax + aw + 6; y = ay + ah / 2 - bh / 2; }
    else if (ax - 6 - bw >= 2) { x = ax - bw - 6; y = ay + ah / 2 - bh / 2; }
    else { x = ax; y = ay + ah + 6; if (y + bh > H - 2) y = ay - bh - 6; }
    x = Math.max(2, Math.min(W - bw - 2, x));
    y = Math.max(2, Math.min(H - bh - 2, y));
    b.style.left = x + 'px';
    b.style.top = y + 'px';
  }

  function close(back) {
    var b = box();
    if (b) { b.hidden = true; b.innerHTML = ''; }
    var a = cur && cur.anchor;
    if (a && a.classList) a.classList.remove('fp-on');
    cur = null;
    if (back && a && a.focus) { try { a.focus(); } catch (e) { /* 없음 */ } }
  }

  /** 값 · 막힘 · 이유를 다시 칠한다(입력 칸은 다시 만들지 않는다 — 치던 값이 지워지지 않게). */
  function paint() {
    var b = box();
    if (!cur || !b || b.hidden) return;
    var busy = isBusy(cur.kind, cur.key);
    var m = model(cur.kind, cur.key, w.core.state, w.core.offline, busy);
    var tt = b.querySelector('[data-fpbind="title"]');
    if (tt && tt.textContent !== m.title) tt.textContent = m.title;
    m.rows.forEach(function (r, i) {
      var e = b.querySelector('[data-fprow="' + i + '"]');
      if (e && e.textContent !== String(r[1])) e.textContent = r[1];
    });
    m.inputs.forEach(function (x) {
      var e = b.querySelector('[data-fpin="' + x.id + '"]');
      if (e && e.disabled !== !!x.off) e.disabled = !!x.off;
      var u = b.querySelector('[data-fpunit="' + x.id + '"]');
      if (u && u.textContent !== (x.unit || '')) u.textContent = x.unit || '';
    });
    // ★ v0.5.1(D2): 단추 묶음이 바뀌면(잠금이 풀려 Unlock 이 사라짐 등) 단추 줄을 다시 만든다
    var bb = b.querySelector('[data-fpbind="btns"]');
    var want = m.buttons.map(function (x) { return x.a; }).join(',');
    if (bb && bb.getAttribute('data-set') !== want) {
      var had = bb.getAttribute('data-set') !== null;
      bb.innerHTML = btnsHtml(m);
      bb.setAttribute('data-set', want);
      if (had && d.activeElement === d.body) {
        var f = bb.querySelector('.fp-btn');
        if (f) { try { f.focus(); } catch (e) { /* 없음 */ } }
      }
    }
    m.buttons.forEach(function (x) {
      var e = b.querySelector('[data-fpa="' + x.a + '"]');
      if (!e) return;
      if (e.disabled !== !x.on) e.disabled = !x.on;
      var tip = x.on ? '' : m.why;
      if (e.title !== tip) e.title = tip;
    });
    var why = b.querySelector('[data-fpbind="why"]');
    if (why) {
      if (why.textContent !== m.why) why.textContent = m.why;
      var wc = 'fp-why' + (m.whyLvl === 'warn' ? ' note' : '');
      if (why.className !== wc) why.className = wc;
    }
  }

  function act(a) {
    if (!cur) return;
    if (a === 'x') { close(true); return; }
    if (isBusy(cur.kind, cur.key)) return;               // 한 번 누르면 한 번
    var b = box();
    var inp = b && b.querySelector('[data-fpin="sp"]');
    var c = command(cur.kind, cur.key, a, inp ? inp.value : null);
    if (!c) { w.core.toast('값을 넣으세요', 'warn'); return; }
    var here = cur;
    var go = function () {
      var rid = w.Ops.send(c.pair);
      if (!rid) return;
      w.core.busy.start(busyKey(here.kind, here.key, a), rid);
      if (cur === here) { cur.rid = rid; result('보냈습니다 — 응답을 기다리는 중', ''); paint(); }
    };
    var val = inp ? Number(inp.value) : 0;
    if (c.confirm === 'heater') {
      w.core.confirmAsk('히터 설정을 바꿀까요?',
        '설정 온도를 바꿉니다. 과온 한계를 넘는 값은 PLC 가 거절합니다.<br>전원이 꺼져 있으면 온도가 오르지 않습니다.',
        T_('b.apply'), go);
    } else if (c.confirm === 'rf') {
      w.core.confirmAsk('RF 를 켤까요?',
        '<b>' + val + ' W</b> 로 RF 를 켭니다. 시험 목적이며 대기 중에만 됩니다.<br>' +
        '압력·반사 전력 조건을 PLC 가 확인하고, 조건이 어긋나면 즉시 끕니다.', T_('b.rfOn'), go);
    } else if (c.confirm === 'o3') {
      w.core.confirmAsk('O3 라인을 켤까요?',
        '바이패스 펌프 → IV-B → 발생기 순서로 켭니다.<br>실내 O3 감지·발생기 알람이 있으면 PLC 가 막습니다.',
        T_('b.o3On'), go);
    } else if ((c.confirm || c.pair[0] === 'pump_start') && w.viewMain && w.viewMain.confirmFor) {
      // 펌핑 시작은 명령줄과 같은 규칙 — 수동 밸브 요청이 남아 있고 허가가 없을 때만 확인 창(confirmFor)
      var cf = w.viewMain.confirmFor(c.confirm || 'pump_start');
      if (cf) w.core.confirmAsk(cf.t, cf.b, cf.ok, go); else go();
    } else {
      go();
    }
  }

  function result(msg, level) {
    var b = box();
    var e = b && b.querySelector('[data-fpbind="res"]');
    if (!e) return;
    e.textContent = msg;
    e.className = 'fp-res' + (level === 'ok' ? ' ok' : level === 'err' ? ' err' : level === 'warn' ? ' warn' : '');
  }

  /** 서버 알림 — 이 창에서 보낸 명령과 요청 번호(rid)가 같은 알림만 결과 줄에(시각으로 고르지 않는다) */
  function onNotice(msg) {
    if (!cur || !cur.rid || !msg || msg.rid !== cur.rid) return;
    result(msg.msg || '', msg.level || '');
  }

  /** 응답 끝(done) — 진행 중 잠금은 core 가 풀었다. 단추를 다시 칠한다 */
  function onDone(msg) {
    if (!cur || !msg || msg.rid !== cur.rid) return;
    paint();
  }

  /* ---------- 사건 ---------- */
  function target(ev) {
    var g = ev.target && ev.target.closest && ev.target.closest('[data-fp]');
    if (!g || g.closest('#faceplate')) return null;
    if (g.disabled || g.hidden) return null;
    return g;
  }

  function openFrom(g) {
    var v = g.getAttribute('data-fp'), i = v.indexOf(':');
    var kind = v.slice(0, i), key = v.slice(i + 1);
    if (cur && cur.kind === kind && String(cur.key) === key && cur.anchor === g) { close(false); return; }
    open(kind, key, g);
  }

  if (d && d.addEventListener) {
    d.addEventListener('click', function (ev) {
      var g = target(ev);
      if (g) { openFrom(g); return; }
      var b = ev.target.closest && ev.target.closest('#faceplate [data-fpa]');
      if (b && !b.disabled) { act(b.getAttribute('data-fpa')); return; }
    });
    // 바깥을 누르면 닫는다(조작 창 · 조작 요소 · 확인 창 · 알람 창 안은 빼고)
    d.addEventListener('mousedown', function (ev) {
      if (!cur) return;
      var el = ev.target;
      if (!el || !el.closest) return;
      if (el.closest('#faceplate') || el.closest('[data-fp]') || el.closest('.modal')) return;
      close(false);
    });
    d.addEventListener('keydown', function (ev) {
      // ★ 키보드: button 은 기본 click 만 쓴다(keydown 에서 또 열면 두 번 토글된다) — SVG g · 표 줄만 Enter/Space 로 연다
      var g = ev.target && ev.target.closest && ev.target.closest('[data-fp]');
      if (g && g.tagName !== 'BUTTON' && !g.closest('#faceplate') && (ev.key === 'Enter' || ev.key === ' ')) {
        ev.preventDefault(); openFrom(g); return;
      }
      if (cur && ev.key === 'Escape') {
        var m = d.querySelector('.modal:not([hidden])');
        if (m) return;                       // 확인 창 · 알람 창이 먼저 Esc 를 받는다
        ev.preventDefault();
        close(true);
      }
    });
  }

  w.viewFaceplate = {
    update: paint, onNotice: onNotice, onDone: onDone, open: open, close: close,
    get current() { return cur ? { kind: cur.kind, key: cur.key } : null; }
  };
  w.Faceplate = { model: model, command: command, busyKey: busyKey };
  if (typeof module !== 'undefined' && module.exports) module.exports = w.Faceplate;
})(typeof window !== 'undefined' ? window : this, typeof document !== 'undefined' ? document : null);
