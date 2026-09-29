/* ============================================================
 * views/main.js — 운전 탭.
 *
 * 공정 진행 / 압력·배기·인터락 / 장비 전용(RF·PCV) / 히터 표 / MFC 표 / 명령줄
 *
 * ★ PLC 연결이 끊기면 모든 값은 '—' 이고 명령 버튼은 잠근다. 마지막 값을 계속
 *   보여 주면 운전자가 현재 상태로 오해한다.
 * ★ 위험하거나 되돌리기 어려운 명령은 확인 창을 거친다(제목에 장비 이름).
 * ============================================================ */
(function (w, d) {
  'use strict';

  // 확인 창이 필요한 명령 — 되돌리기 어렵거나 챔버 상태를 크게 바꾼다.
  var CONFIRM = {
    pump_stop: { t: '펌핑을 정지할까요?', b: '배기 격리(IV-E)를 닫고 펌프를 멈춥니다. 챔버 압력이 서서히 올라갑니다.', ok: '펌핑 정지' },
    vent: { t: '벤트를 시작할까요?', b: '배기 격리(IV-E)를 닫고 N2 로 챔버를 대기압까지 올립니다.<br>공정 중에는 실행할 수 없습니다.', ok: '벤트' },
    all_close: { t: '전체 밸브를 닫을까요?', b: '공정 밸브와 수동 보조 출력 요청을 모두 지우고 배기 격리를 닫습니다.<br>펌프는 그대로 둡니다.', ok: '전체 닫기' },
    alarm_reset: { t: '알람을 리셋할까요?', b: '원인이 사라진 알람만 지웁니다. 원인이 남아 있으면 즉시 다시 걸립니다.<br>PC 통신 끊김 알람도 이것으로 풀립니다.', ok: '알람 리셋' }
  };

  var built = false;

  /* ===================== render ===================== */
  function render(s) {
    buildStatic(s);
    buildHeaters(s);
    buildMfc(s);
    buildDevPanel(s);
    update(s.live || {});
  }

  function buildStatic(s) {
    if (built) return;
    built = true;
    var seq = core.bind('seqBody');
    if (seq) {
      seq.innerHTML =
        '<div class="kv"><span class="k">시퀀서</span><span class="v" data-bind="sqName">—</span></div>' +
        '<div class="kv"><span class="k">블록 / 스텝</span><span class="v" data-bind="sqBS">—</span></div>' +
        '<div class="kv"><span class="k">블록 반복 / 그룹 회차</span><span class="v" data-bind="sqRep">—</span></div>' +
        '<div class="kv"><span class="k">스텝 경과</span><span class="v" data-bind="sqMs">—</span></div>' +
        '<div class="kv"><span class="k">레시피 표</span><span class="v" data-bind="sqRcp">—</span></div>' +
        '<div class="hint" style="margin-top:4px">레시피 실행은 <b>2단계</b>. 여기는 PLC 보고값 그대로입니다.</div>';
    }
    var vac = core.bind('vacBody');
    if (vac) {
      vac.innerHTML =
        '<div class="gauges">' +
        '<div class="gauge"><div class="k">CVG<span class="unconf" data-bind="cvgUnconf" hidden>환산 미확정</span></div>' +
        '<div class="v"><span data-bind="gCvg">—</span><span class="u">Torr</span></div></div>' +
        '<div class="gauge" data-bind="gCmBox" hidden><div class="k">커패시턴스 게이지</div>' +
        '<div class="v"><span data-bind="gCm">—</span><span class="u">Torr</span></div></div>' +
        '</div>' +
        '<div class="kv"><span class="k">펌프 · 배기</span><span class="v" data-bind="vPump">—</span></div>' +
        '<div class="kv"><span class="k">IV-E · VV</span><span class="v" data-bind="vIve">—</span></div>' +
        '<div class="hint" style="margin-top:2px">인터락 · 입력</div>' +
        '<div class="ilks" data-bind="ilks"></div>';
    }
  }

  /* ---------- 히터 표 ---------- */
  function buildHeaters(s) {
    var tbl = core.bind('heaterTbl');
    if (!tbl) return;
    var heaters = (s.structure || {}).heaters || [];
    var html = '<thead><tr><th>채널</th><th>설정</th><th>현재</th><th>출력</th><th>전원</th><th>상태</th></tr></thead><tbody>';
    heaters.forEach(function (h) {
      if (!h.enabled) return;   // 사용 채널만 보여 준다
      html += '<tr data-hrow="' + h.ch + '">' +
        '<td>CH' + h.ch + ' ' + core.esc(h.name) + '</td>' +
        '<td class="sv" data-hsv="' + h.ch + '">—</td>' +
        '<td class="pv" data-hpv="' + h.ch + '">—</td>' +
        '<td data-hout="' + h.ch + '">—</td>' +
        '<td data-hpow="' + h.ch + '">—</td>' +
        '<td data-hst="' + h.ch + '"></td></tr>';
    });
    tbl.innerHTML = html + '</tbody>';
    var miss = heaters.filter(function (h) { return h.enabled && h.max_c == null; });
    core.setText('tcNote', miss.length
      ? '⚠ 과온 한계 미정 ' + miss.length + '채널 — PLC 한계에 0 을 써서 막습니다'
      : '');
  }

  /* ---------- MFC 표 ---------- */
  function buildMfc(s) {
    var tbl = core.bind('mfcTbl');
    if (!tbl) return;
    var mfc = (s.structure || {}).mfc || [];
    var html = '<thead><tr><th>MFC</th><th>가스</th><th>풀스케일</th><th>설정</th><th>현재</th></tr></thead><tbody>';
    mfc.forEach(function (m) {
      html += '<tr><td>' + m.no + ' ' + core.esc(m.name) + '</td>' +
        '<td>' + core.esc(m.gas || fmt.DASH) + '</td>' +
        '<td>' + (m.full_scale_sccm == null
          ? '<span class="unconf">미정</span>'
          : fmt.int(m.full_scale_sccm) + '<span class="unit">sccm</span>') + '</td>' +
        '<td class="sv" data-msv="' + m.no + '">—</td>' +
        '<td class="pv" data-mpv="' + m.no + '">—</td></tr>';
    });
    tbl.innerHTML = html + '</tbody>';
  }

  /* ---------- 장비 전용 패널 (PEALD: RF · PCV) ---------- */
  function buildDevPanel(s) {
    var dev = s.device || {};
    var panel = core.bind('devPanel');
    if (!panel) return;
    if (!dev.has_rf && !dev.has_o3) { panel.hidden = true; return; }
    panel.hidden = false;
    core.setText('devPanelHead', dev.has_rf ? 'RF · PCV' : 'O3 라인');
    var body = core.bind('devPanelBody');
    if (!body) return;
    if (dev.has_rf) {
      body.innerHTML =
        '<div class="kv"><span class="k">RF 순방향 / 반사</span><span class="v" data-bind="rfFwd">—</span></div>' +
        '<div class="kv"><span class="k">RF 설정 (실제 출력)</span><span class="v" data-bind="rfSv">—</span></div>' +
        '<div class="kv"><span class="k">PCV 목표 / 개도</span><span class="v" data-bind="pcvV">—</span></div>' +
        '<div class="ilks" data-bind="rfChips"></div>';
    }
  }

  /* ===================== update ===================== */
  function update(t) {
    var s = core.state;
    if (!s) return;
    var conn = !!(t.plc && t.plc.connected);

    updateSeq(t, conn);
    updateVac(s, t, conn);
    updateHeaters(s, t, conn);
    updateMfc(s, t, conn);
    updateDev(s, t, conn);
    updateLocks(s, t, conn);
  }

  function updateSeq(t, conn) {
    var q = t.seq || {};
    core.setText('sqName', conn ? (q.name || fmt.DASH) : fmt.DASH);
    core.setText('sqBS', conn ? (q.block || 0) + ' / ' + (q.step || 0) : fmt.DASH);
    core.setText('sqRep', conn ? (q.block_pass || 0) + ' / ' + (q.group_pass || 0) : fmt.DASH);
    core.setText('sqMs', conn ? fmt.ms(q.step_ms) + ' s' : fmt.DASH);
    var r = core.bind('sqRcp');
    if (r) {
      r.innerHTML = !conn ? fmt.DASH
        : core.chip(q.recipe_ok ? '통과' : '미통과', q.recipe_ok ? 'ok' : 'off',
                    'PLC 합계 ' + fmt.hex16(q.recipe_sum));
    }
    var chip = core.bind('seqChip');
    if (chip) chip.innerHTML = conn && t.state ? core.chip(t.state.name, stateLevel(t.state.code)) : '';
  }

  function stateLevel(code) {
    return ({ 2: 'ok', 3: 'ok', 4: 'warn', 5: 'warn', 6: 'stop' })[code] || 'off';
  }

  function updateVac(s, t, conn) {
    var p = t.pressure || {};
    core.setText('gCvg', conn ? fmt.torr(p.cvg) : fmt.DASH);
    var un = core.bind('cvgUnconf');
    if (un) un.hidden = ((s.unconfirmed || []).indexOf('CVG 압력') < 0);
    var cmBox = core.bind('gCmBox');
    if (cmBox) cmBox.hidden = !(p && p.cm_installed);
    core.setText('gCm', conn ? fmt.torr(p.cm) : fmt.DASH);

    var aux = auxMap(s, t, conn);
    var i0 = t.inputs0;
    core.setText('vPump', !conn ? fmt.DASH
      : (aux['PMP'] ? '펌프 ON' : '펌프 OFF') + ' · ' +
        (core.bit(i0, 7) ? '운전 피드백 정상' : '운전 피드백 없음'));
    core.setText('vIve', !conn ? fmt.DASH
      : (aux['IV-E'] ? 'IV-E 열림' : 'IV-E 닫힘') + ' · ' + (aux['VV'] ? 'VV 열림' : 'VV 닫힘'));

    var box = core.bind('ilks');
    if (!box) return;
    if (!conn) { box.innerHTML = '<span class="chip off">PLC 끊김</span>'; return; }
    var str = s.structure || {};
    var out = [];
    (str.interlocks || []).forEach(function (k) {
      var on = core.bit(t.interlock, k.bit);
      // bad:true 인 비트는 '켜져 있으면 이상'이다(동시 요청·안전 정지 요구).
      var lvl = k.bad ? (on ? 'stop' : 'off') : (on ? 'ok' : 'warn');
      if (k.bad && !on) return;      // 이상이 없으면 굳이 보여 주지 않는다
      out.push(core.chip(k.tag, lvl, k.why));
    });
    // 위 kv 줄에서 이미 보여 준 입력은 칩으로 또 내지 않는다(패널이 넘친다).
    var SHOWN = { 'IVE-O': 1, 'IVE-C': 1, 'PMP-RUN': 1, 'ATM': 1 };
    (str.inputs0 || []).forEach(function (k) {
      if (k.unused || k.momentary || SHOWN[k.tag]) return;
      out.push(inputChip(t.inputs0, k, '입력0 b' + k.bit));
    });
    // 입력 워드1(RF·O3 관련)은 아래 장비 전용 패널에서 이미 보여 준다 —
    // 같은 칩을 두 번 그리면 인터락 패널이 넘친다.
    box.innerHTML = out.join('');
  }

  /** 입력 칩 한 개.
   *  ★ ok_when=false 인 입력(펌프 알람·가스 누출·과온·RF 알람)은 '이상일 때 1'이다.
   *    정상일 때 초록으로 칠하면 "펌프 알람"이 초록으로 보여 거꾸로 읽힌다 →
   *    정상이면 회색(=일어나지 않음), 이상이면 빨강으로 둔다. */
  function inputChip(word, k, why) {
    var on = core.bit(word, k.bit);
    var lvl;
    if (k.ok_when === false) lvl = on ? 'stop' : 'off';
    else if (k.ok_when === true) lvl = on ? 'ok' : 'stop';
    else lvl = on ? 'info' : 'off';
    return core.chip(k.name, lvl, why);
  }

  function auxMap(s, t, conn) {
    var m = {};
    ((s.structure || {}).aux || []).forEach(function (a) {
      m[a.tag] = conn && t.aux != null && core.bit(t.aux, a.bit);
    });
    return m;
  }

  function updateHeaters(s, t, conn) {
    var heaters = t.heaters || [];
    ((s.structure || {}).heaters || []).forEach(function (def) {
      if (!def.enabled) return;
      var h = heaters[def.ch - 1] || {};
      set('[data-hsv="' + def.ch + '"]', conn ? fmt.temp(h.sv) : fmt.DASH);
      set('[data-hpv="' + def.ch + '"]', conn && h.comm_ok ? fmt.temp(h.pv) : fmt.DASH);
      set('[data-hout="' + def.ch + '"]', conn && h.comm_ok && h.out_pct != null
        ? h.out_pct + ' %' : fmt.DASH);
      var pw = d.querySelector('[data-hpow="' + def.ch + '"]');
      if (pw) pw.innerHTML = !conn ? fmt.DASH
        : core.chip(h.power ? 'ON' : 'OFF', h.power ? 'ok' : 'off');
      var st = d.querySelector('[data-hst="' + def.ch + '"]');
      if (!st) return;
      if (!conn) { st.innerHTML = fmt.DASH; return; }
      if (!h.comm_ok) st.innerHTML = core.chip('통신 끊김', 'stop');
      else if (h.alarm) st.innerHTML = core.chip('조절기 알람', 'stop');
      else if (def.max_c == null) st.innerHTML = core.chip('한계 미정', 'warn', '과온 한계가 없어 PLC 가 이 채널을 막습니다');
      else st.innerHTML = core.chip('정상', 'ok');
    });
  }

  function updateMfc(s, t, conn) {
    var mfc = t.mfc || [];
    ((s.structure || {}).mfc || []).forEach(function (def) {
      var m = mfc[def.no - 1] || {};
      set('[data-msv="' + def.no + '"]', conn ? fmt.flow(m.sv) : fmt.DASH);
      set('[data-mpv="' + def.no + '"]', conn ? fmt.flow(m.pv) : fmt.DASH);
    });
  }

  function updateDev(s, t, conn) {
    if (!(s.device || {}).has_rf) return;
    var e = t.extra || {};
    core.setText('rfFwd', conn ? fmt.watt(e.rf_fwd) + ' / ' + fmt.watt(e.rf_ref) + ' W' : fmt.DASH);
    core.setText('rfSv', conn ? fmt.watt(e.rf_sv) + ' W' : fmt.DASH);
    core.setText('pcvV', conn ? fmt.pct(e.pcv_sv) + ' / ' + fmt.pct(e.pcv_pv) + ' %' : fmt.DASH);
    var chips = core.bind('rfChips');
    if (!chips) return;
    if (!conn) { chips.innerHTML = ''; return; }
    var str = s.structure || {};
    var out = [core.chip(e.rf_on ? 'RF ON' : 'RF OFF', e.rf_on ? 'ok' : 'off')];
    (str.interlocks || []).forEach(function (k) {
      if (k.tag !== 'RF 허가') return;
      out.push(core.chip('RF 허가', core.bit(t.interlock, k.bit) ? 'ok' : 'warn', k.why));
    });
    (str.inputs1 || []).forEach(function (k) {
      var on = core.bit(t.inputs1, k.bit);
      out.push(core.chip(k.name, k.ok_when === null ? (on ? 'ok' : 'off') : (on === k.ok_when ? 'ok' : 'stop')));
    });
    chips.innerHTML = out.join('');
  }

  /* ---------- 잠금 ---------- */
  function updateLocks(s, t, conn) {
    var local = core.canOperate();
    var msg = '';
    if (!local) msg = '🔒 원격 접속은 보기 전용입니다';
    else if (!conn) msg = '🔒 PLC 연결이 끊겨 명령을 보낼 수 없습니다';

    Array.prototype.forEach.call(d.querySelectorAll('.cmdbar [data-cmd]'), function (b) {
      var c = b.dataset.cmd;
      if (c === 'exit') { b.disabled = !local; return; }
      b.disabled = !local || !conn;
    });
    core.setText('lockMsg', msg);
    core.setText('schemNote', !conn ? 'PLC 끊김 — 값 없음'
      : (t.state ? t.state.name : ''));
  }

  function set(sel, v) {
    var e = d.querySelector(sel);
    if (e) e.textContent = v;
  }

  /* ---------- 명령 ---------- */
  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cmd]');
    if (!b || b.disabled) return;
    var c = b.dataset.cmd;
    if (c === 'exit') { core.askExit(); return; }
    var cf = CONFIRM[c];
    if (cf) {
      core.confirmAsk(cf.t, cf.b, cf.ok, function () { w.app.send(c); });
    } else {
      w.app.send(c);
    }
  });

  core.register('main', { render: render, update: update });
  w.viewMain = { render: render, update: update };
})(window, document);
