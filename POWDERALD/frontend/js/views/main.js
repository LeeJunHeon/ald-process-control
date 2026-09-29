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
    process_abort: { t: '공정을 즉시 중단할까요?', b: '진행 중인 스텝을 그 자리에서 끊고 모든 공정 밸브를 닫습니다.<br>웨이퍼는 중간 상태로 남습니다 — 되돌릴 수 없습니다.', ok: '즉시 중단' },
    process_stop_after_cycle: { t: '이번 사이클 후에 정지할까요?', b: '지금 돌고 있는 사이클을 끝까지 마친 뒤 정지합니다.<br>남은 블록·그룹 반복은 실행하지 않습니다.', ok: '사이클 후 정지' },
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
        '<div class="kv"><span class="k">레시피 <span data-bind="sqRcpChip"></span></span>' +
        '<span class="v" data-bind="sqRec">—</span></div>' +
        '<div class="kv"><span class="k">블록/스텝</span><span class="v" data-bind="sqBS">—</span></div>' +
        '<div class="kv"><span class="k">경과/남은/예정</span><span class="v" data-bind="sqTime">—</span></div>' +
        '<div class="bar"><span data-bind="sqBar"></span></div>' +
        '<div class="steplist" data-bind="sqSteps"></div>' +
        '<div class="ck-list" data-bind="sqChecks"></div>' +
        '<div class="procmsg" data-bind="seqMsg" hidden></div>' +
        '<div class="procbar">' +
        '<button class="btn sm primary" data-cmd="process_start">공정 시작</button>' +
        '<button class="btn sm" data-cmd="process_pause">일시정지</button>' +
        '<button class="btn sm" data-cmd="process_resume">재개</button>' +
        '<button class="btn sm" data-cmd="process_stop_after_cycle">사이클 후 정지</button>' +
        '<button class="btn sm danger" data-cmd="process_abort">즉시 중단</button>' +
        '<button class="btn sm" data-cmd="process_cancel_wait" data-bind="sqCancel" hidden>대기 취소</button>' +
        '<button class="btn sm" data-bind="mnOpen">수동 조작…</button>' +
        '</div>';
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
    core.setText('devPanelHead', dev.has_o3 ? 'O3 라인' : 'RF · PCV');
    var body = core.bind('devPanelBody');
    if (!body) return;
    if (dev.has_o3) {
      body.innerHTML =
        '<div class="kv"><span class="k">O3 설정 / 현재</span><span class="v" data-bind="o3V">—</span></div>' +
        '<div class="kv"><span class="k">바이패스 펌프 · IV-B</span><span class="v" data-bind="o3Byp">—</span></div>' +
        '<div class="ilks" data-bind="o3Chips"></div>';
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
    core.setText('sqBS', conn
      ? (q.block || 0) + ' / ' + (q.step || 0) + ' · ' + (q.block_pass || 0) +
        '회 · 그룹 ' + (q.group_pass || 0)
      : fmt.DASH);
    // 스텝 경과와 레시피 표 통과는 위 두 줄에 붙여 보여 준다(패널을 낮게 유지한다).
    var bs = core.bind('sqBS');
    if (bs && conn) {
      bs.innerHTML = core.esc(bs.textContent) + ' · 스텝 ' + fmt.ms(q.step_ms) + ' s';
    }
    var rc = core.bind('sqRcpChip');
    if (rc) {
      rc.innerHTML = !conn ? '' : core.chip(q.recipe_ok ? '표 통과' : '표 미통과',
        q.recipe_ok ? 'ok' : 'off', 'PLC 합계 ' + fmt.hex16(q.recipe_sum));
    }
    var chip = core.bind('seqChip');
    if (chip) {
      // 시퀀서 상태는 장비 상태와 다를 때만 함께 보여 준다(같은 말이 두 번 보이면 읽지 않는다).
      var qn = (t.seq || {}).name || '';
      chip.innerHTML = conn && t.state
        ? (qn && qn !== t.state.name ? core.chip(qn, 'info') + ' ' : '') +
          core.chip(t.state.name, stateLevel(t.state.code))
        : '';
    }
    updateProcess(t, conn);
  }

  /* ---------- 공정 진행 ---------- */
  function updateProcess(t, conn) {
    var p = t.process || {};
    var run = !!p.running;

    core.setText('sqRec', !conn ? fmt.DASH
      : (p.recipe || '레시피를 고르세요') + (p.number ? ' · 번호 ' + p.number : ''));

    if (run && p.total_ms) {
      core.setText('sqTime', fmt.hms(p.elapsed_s) + ' / ' + fmt.hms((p.remaining_ms || 0) / 1000) +
        ' / ' + (p.eta || fmt.DASH));
    } else if (p.estimate && p.estimate.total_ms) {
      core.setText('sqTime', '예상 ' + fmt.hms(p.estimate.total_ms / 1000));
    } else {
      core.setText('sqTime', fmt.DASH);
    }

    var bar = core.bind('sqBar');
    if (bar) {
      var done = (run && p.total_ms) ? (1 - (p.remaining_ms || 0) / p.total_ms) : 0;
      bar.style.width = Math.max(0, Math.min(100, done * 100)).toFixed(1) + '%';
      bar.className = p.paused ? 'warn' : '';
    }

    // 스텝 목록 — 지금 스텝을 굵게. 열리는 밸브 이름을 함께 보여 준다.
    var sl = core.bind('sqSteps');
    if (sl) {
      var steps = p.steps || [];
      sl.innerHTML = (run && steps.length)
        ? steps.map(function (x, i) {
            var on = (i + 1) === p.step_in_block;
            return '<span class="st' + (on ? ' on' : '') + '">' + core.esc(x.name || (i + 1)) +
              '<i>' + fmt.ms(x.ms) + 's</i></span>';
          }).join('')
        : '';
    }

    // 시작 조건 — 판정은 서버가 했고 여기서는 안 된 것만 보여 준다.
    var cl = core.bind('sqChecks');
    if (cl) {
      var bad = (p.checks || []).filter(function (c) { return !c.ok; });
      // ★ 안 된 것만, 그것도 앞 셋만 보여 준다 — 패널이 넘치면 아무것도 안 읽힌다.
      //   전체 목록은 시작을 누를 때 확인 창에서 다시 보여 준다.
      cl.innerHTML = (!run && bad.length)
        ? bad.slice(0, 3).map(function (c) {
            return core.chip(c.label + ': ' + c.detail, c.key === 'base' ? 'warn' : 'stop');
          }).join('') + (bad.length > 3 ? core.chip('외 ' + (bad.length - 3) + '건', 'stop') : '')
        : '';
    }

    var msg = p.message || '';
    if (p.last_result && !run && !msg) msg = '지난 공정: ' + p.last_result;
    var pm = core.bind('seqMsg');
    if (pm) { pm.textContent = msg; pm.hidden = !msg; }
    var cancel = core.bind('sqCancel');
    if (cancel) cancel.hidden = (p.phase !== 'base_wait');

    // 상태에 맞는 단추만 살린다 — 누를 수 없는 단추를 눌러 보게 두지 않는다.
    var local = core.canOperate();
    var busy = p.phase && p.phase !== 'idle';
    en('process_start', local && conn && !run && !busy && !!p.can_start);
    en('process_pause', local && conn && run && !p.paused);
    en('process_resume', local && conn && !!p.paused);
    en('process_stop_after_cycle', local && conn && run && !p.paused);
    en('process_abort', local && conn && run);
    en('process_cancel_wait', local && p.phase === 'base_wait');
  }

  function en(cmd, ok) {
    var b = d.querySelector('.procbar [data-cmd="' + cmd + '"]');
    if (b) b.disabled = !ok;
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
      if (pw) pw.innerHTML = (!conn || h.power == null) ? fmt.DASH
        : core.chip(h.power ? 'ON' : 'OFF', h.power ? 'ok' : 'off');
      var st = d.querySelector('[data-hst="' + def.ch + '"]');
      if (!st) return;
      if (!conn) { st.innerHTML = fmt.DASH; return; }
      if (!h.comm_ok) st.innerHTML = core.chip('통신 끊김', 'stop');
      else if (h.alarm) st.innerHTML = core.chip('조절기 알람', 'stop');
      else if (def.max_c == null) st.innerHTML = core.chip('한계 미정', 'warn', '과온 한계가 없어 PLC 소프트 과온 감시가 꺼집니다 — PC 가 설정·전원 켜기를 막습니다');
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
    if (!(s.device || {}).has_o3) return;
    var e = t.extra || {};
    var u = e.o3_unit || '';
    core.setText('o3V', conn ? fmt.num(e.o3_sv, 1) + ' / ' + fmt.num(e.o3_pv, 1) + ' ' + u : fmt.DASH);
    var aux = auxMap(s, t, conn);
    core.setText('o3Byp', !conn ? fmt.DASH
      : (aux['BPMP'] ? '펌프 ON' : '펌프 OFF') + ' · ' + (aux['IV-B'] ? 'IV-B 열림' : 'IV-B 닫힘'));
    var chips = core.bind('o3Chips');
    if (!chips) return;
    if (!conn) { chips.innerHTML = ''; return; }
    var str = s.structure || {};
    var out = [core.chip(e.o3_on ? 'O3 발생기 ON' : 'O3 발생기 OFF', e.o3_on ? 'ok' : 'off')];
    (str.interlocks || []).forEach(function (k) {
      if (k.tag !== 'O3 허가') return;
      out.push(core.chip('O3 허가', core.bit(t.interlock, k.bit) ? 'ok' : 'warn',
                         k.why || '바이패스 펌프 + IV-B 열림 뒤 5 s'));
    });
    (str.inputs1 || []).forEach(function (k) {
      out.push(inputChip(t.inputs1, k, '입력1 b' + k.bit));
    });
    chips.innerHTML = out.join('');
  }

  /* ---------- 잠금 ---------- */
  function updateLocks(s, t, conn) {
    var local = core.canOperate();
    var msg = '';
    if (!local) msg = '🔒 원격 접속은 보기 전용입니다';
    else if (!conn) msg = '🔒 PLC 연결이 끊겨 명령을 보낼 수 없습니다';

    // ★ 공정 단추는 updateProcess 가 상태별로 따로 판단한다 — 여기서 덮어쓰지 않는다.
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
    if (c === 'process_start') { askStart(); return; }
    var cf = confirmFor(c);
    if (cf) {
      core.confirmAsk(cf.t, cf.b, cf.ok, function () { w.app.send(c); });
    } else {
      w.app.send(c);
    }
  });

  /** 확인 창 문구. Powder 는 O3 라인이 켜져 있으면 전체 닫기가 라인도 한꺼번에 끈다고 알린다. */
  function confirmFor(c) {
    var cf = CONFIRM[c];
    if (!cf || c !== 'all_close') return cf;
    var st = core.state || {};
    if (!(st.device || {}).has_o3) return cf;
    var t = st.live || {};
    var aux = auxMap(st, t, !!(t.plc && t.plc.connected));
    var mn = t.manual || {};
    var o3on = aux['O3GEN'] || aux['IV-B'] || aux['BPMP'] || (mn.aux_request || 0) !== 0;
    if (!o3on) return cf;
    return { t: cf.t, ok: cf.ok,
      b: cf.b + '<br><br><b>O3 라인(발생기·IV-B·바이패스 펌프)도 한꺼번에 꺼집니다</b> — ' +
         '순서대로 끄려면 수동 조작의 O3 라인 끄기를 먼저 하세요.' };
  }

  /** 시작은 흐름이다 — 무엇을 올리고 무엇을 기다리는지 먼저 알려 준다. */
  function askStart() {
    var p = ((core.state || {}).live || {}).process || {};
    var est = p.estimate || {};
    core.confirmAsk('공정을 시작할까요?',
      '레시피 <b>' + core.esc(p.recipe || '') + '</b>' +
      (p.number ? ' (번호 ' + p.number + ')' : '') + '<br>' +
      '스텝 ' + (est.step_count || 0) + ' · 블록 ' + (est.block_count || 0) +
      ' · 예상 <b>' + fmt.hms((est.total_ms || 0) / 1000) + '</b><br><br>' +
      'PLC 에 레시피 표를 올리고, 베이스 압력에 도달하면 시작합니다.<br>' +
      '대기 중에는 [대기 취소]로 멈출 수 있습니다.' +
      ((p.checks || []).filter(function (c) { return !c.ok; }).length
        ? '<br><br>아직 안 된 조건:<br>' +
          (p.checks || []).filter(function (c) { return !c.ok; })
            .map(function (c) { return '· ' + core.esc(c.label) + ' — ' + core.esc(c.detail); })
            .join('<br>')
        : ''),
      '시작', function () { w.app.send('process_start'); });
  }

  core.register('main', { render: render, update: update });
  w.viewMain = { render: render, update: update };
})(window, document);
