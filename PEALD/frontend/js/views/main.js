/* ============================================================
 * views/main.js — 운전 탭.
 *
 * 공정 진행 / 압력·배기·인터락 / 장비 전용(RF·PCV) / 히터 표 / MFC 표 / 명령줄
 *
 * ★ PLC 연결이 끊기면 모든 값은 '—' 이고 명령 버튼은 잠근다. 마지막 값을 계속
 *   보여 주면 운전자가 현재 상태로 오해한다.
 * ★ 위험하거나 되돌리기 어려운 명령은 확인 창을 거친다(제목에 장비 이름).
 * ★ 5 Hz 갱신은 바뀐 칸만 고친다(core.html) — 칩을 매번 다시 만들면 이유 툴팁이 뜨지 않는다.
 * ★ v0.5.0(A4): 인터락 · 입력 · 시작 조건 · RF/O3 상태는 이름 + 작은 상태 점 목록(.stl/.sti). 문제인 항목만
 *   노랑 · 빨강 점과 글자, 정상은 회색 글자 + 초록 점. 용어는 영문(terms.js · 서버 device 정의).
 * ============================================================ */
(function (w, d) {
  'use strict';

  // 확인 창이 필요한 명령 — 되돌리기 어렵거나 챔버 상태를 크게 바꾼다.
  var CONFIRM = {
    pump_stop: { t: 'Pumping Stop 을 할까요?', b: '배기 격리(IV-E)를 닫고 펌프를 멈춥니다. 챔버 압력이 서서히 올라갑니다.', ok: T('b.pumpStop') },
    vent: { t: 'Vent 를 시작할까요?', b: '배기 격리(IV-E)를 닫고 N2 로 챔버를 대기압까지 올립니다.<br>공정 중에는 실행할 수 없습니다.', ok: T('b.vent') },
    all_close: { t: '전체 밸브를 닫을까요?', b: '공정 밸브와 수동 보조 출력 요청을 모두 지우고 배기 격리를 닫습니다.<br>펌프는 그대로 둡니다.', ok: T('b.closeAll') },
    process_abort: { t: '공정을 즉시 중단할까요?', b: '진행 중인 스텝을 그 자리에서 끊고 모든 공정 밸브를 닫습니다.<br>웨이퍼는 중간 상태로 남습니다 — 되돌릴 수 없습니다.', ok: T('b.abort') },
    process_stop_after_cycle: { t: '이번 사이클 후에 정지할까요?', b: '지금 돌고 있는 사이클을 끝까지 마친 뒤 정지합니다.<br>남은 블록·그룹 반복은 실행하지 않습니다.', ok: T('b.stopAfterCycle') },
    alarm_reset: { t: '알람을 리셋할까요?', b: '원인이 사라진 알람만 지웁니다. 원인이 남아 있으면 즉시 다시 걸립니다.<br>PC 통신 끊김 알람도 이것으로 풀립니다.', ok: T('b.reset') }
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
        '<div class="kv"><span class="k">' + T('v.recipe') + ' <span data-bind="sqRcpChip"></span></span>' +
        '<span class="v" data-bind="sqRec">—</span></div>' +
        '<div class="kv"><span class="k">' + T('v.block') + ' / ' + T('v.step') + '</span><span class="v" data-bind="sqBS">—</span></div>' +
        '<div class="kv"><span class="k">' + T('v.elapsed') + ' / ' + T('v.remaining') + ' / ' + T('v.planned') +
        '</span><span class="v" data-bind="sqTime">—</span></div>' +
        '<div class="bar"><span data-bind="sqBar"></span></div>' +
        '<div class="steplist" data-bind="sqSteps"></div>' +
        '<div class="stl c2" data-bind="sqChecks"></div>' +
        '<div class="procmsg" data-bind="seqMsg" hidden></div>' +
        '<div class="procbar">' +
        '<button class="btn sm start" data-cmd="process_start">' + T('b.start') + '</button>' +
        '<button class="btn sm" data-cmd="process_pause">' + T('b.pause') + '</button>' +
        '<button class="btn sm" data-cmd="process_resume">' + T('b.resume') + '</button>' +
        '<button class="btn sm" data-cmd="process_stop_after_cycle">' + T('b.stopAfterCycle') + '</button>' +
        '<button class="btn sm abort" data-cmd="process_abort">' + T('b.abort') + '</button>' +
        '<button class="btn sm" data-cmd="process_cancel_wait" data-bind="sqCancel" hidden>' + T('b.cancelWait') + '</button>' +
        '<button class="btn sm" data-bind="mnOpen">' + T('b.manual') + '</button>' +
        '</div>';
    }
    var vac = core.bind('vacBody');
    if (vac) {
      vac.innerHTML =
        '<div class="gauges">' +
        '<div class="gauge"><div class="k">CVG<span class="unconf" data-bind="cvgUnconf" hidden>' + T('s.unconfirmed') + '</span></div>' +
        '<div class="v"><span data-bind="gCvg">—</span><span class="u">Torr</span></div></div>' +
        '<div class="gauge" data-bind="gCmBox" hidden><div class="k">CM</div>' +
        '<div class="v"><span data-bind="gCm">—</span><span class="u">Torr</span></div></div>' +
        '</div>' +
        '<div class="kv"><span class="k">Pump / Exhaust</span><span class="v" data-bind="vPump">—</span></div>' +
        '<div class="kv"><span class="k">IV-E / VV</span><span class="v" data-bind="vIve">—</span></div>' +
        '<div class="hint" style="margin-top:2px">Interlock / Input</div>' +
        '<div class="ilks stl" data-bind="ilks"></div>';
    }
  }

  /* ---------- 히터 표 ---------- */
  function buildHeaters(s) {
    var tbl = core.bind('heaterTbl');
    if (!tbl) return;
    var heaters = (s.structure || {}).heaters || [];
    var html = '<thead><tr><th>' + T('v.channel') + '</th><th>' + T('v.setpoint') + '</th><th>' + T('v.actual') +
      '</th><th>' + T('v.output') + '</th><th>' + T('v.power') + '</th><th>' + T('v.status') + '</th></tr></thead><tbody>';
    heaters.forEach(function (h) {
      if (!h.enabled) return;   // 사용 채널만 보여 준다
      html += '<tr data-hrow="' + h.ch + '">' +
        '<td>CH' + core.esc(h.ch) + ' ' + core.esc(h.name) + '</td>' +
        '<td class="sv" data-hsv="' + h.ch + '">—</td>' +
        '<td class="pv" data-hpv="' + h.ch + '">—</td>' +
        '<td data-hout="' + h.ch + '">—</td>' +
        '<td data-hpow="' + h.ch + '">—</td>' +
        '<td data-hst="' + h.ch + '"></td></tr>';
    });
    core.html(tbl, html + '</tbody>');
    var miss = heaters.filter(function (h) { return h.enabled && h.max_c == null; });
    core.setText('tcNote', miss.length ? '⚠ ' + T('s.limitUnset') + ' ' + miss.length + ' ch' : '');
    var tn = core.bind('tcNote');            // v0.5.3: 설명 문장은 툴팁(한글)
    if (tn) tn.title = miss.length ? 'PLC 한계에 0 을 써서 막습니다' : '';
  }

  /* ---------- MFC 표 ---------- */
  function buildMfc(s) {
    var tbl = core.bind('mfcTbl');
    if (!tbl) return;
    var mfc = (s.structure || {}).mfc || [];
    var html = '<thead><tr><th>MFC</th><th>' + T('v.gas') + '</th><th>' + T('v.fullScale') + '</th><th>' +
      T('v.setpoint') + '</th><th>' + T('v.actual') + '</th></tr></thead><tbody>';
    mfc.forEach(function (m) {
      html += '<tr><td>' + core.esc(m.no) + ' ' + core.esc(m.name) + '</td>' +
        '<td>' + core.esc(m.gas || fmt.DASH) + '</td>' +
        '<td>' + (m.full_scale_sccm == null
          ? '<span class="unconf">Unset</span>'
          : core.esc(fmt.int(m.full_scale_sccm)) + '<span class="unit">sccm</span>') + '</td>' +
        '<td class="sv" data-msv="' + m.no + '">—</td>' +
        '<td class="pv" data-mpv="' + m.no + '">—</td></tr>';
    });
    core.html(tbl, html + '</tbody>');
  }

  /* ---------- 장비 전용 패널 (PEALD: RF · PCV) ---------- */
  function buildDevPanel(s) {
    var dev = s.device || {};
    var panel = core.bind('devPanel');
    if (!panel) return;
    if (!dev.has_rf && !dev.has_o3) { panel.hidden = true; return; }
    panel.hidden = false;
    core.setText('devPanelHead', dev.has_rf ? T('panel.rfpcv') : T('panel.o3'));
    var body = core.bind('devPanelBody');
    if (!body) return;
    if (dev.has_rf) {
      body.innerHTML =
        '<div class="kv"><span class="k">RF Forward / Reflected</span><span class="v" data-bind="rfFwd">—</span></div>' +
        '<div class="kv"><span class="k">RF Setpoint (Output)</span><span class="v" data-bind="rfSv">—</span></div>' +
        '<div class="kv"><span class="k">PCV Target / Position</span><span class="v" data-bind="pcvV">—</span></div>' +
        '<div class="stl" data-bind="rfChips"></div>';
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
      ? (q.block || 0) + ' / ' + (q.step || 0) + ' · ' + T('v.cycle') + ' ' + (q.block_pass || 0) +
        ' · ' + T('v.group') + ' ' + (q.group_pass || 0)
      : fmt.DASH);
    // 스텝 경과와 레시피 표 통과는 위 두 줄에 붙여 보여 준다(패널을 낮게 유지한다).
    var bs = core.bind('sqBS');
    if (bs && conn) {
      bs.textContent = bs.textContent + ' · ' + T('v.step') + ' ' + fmt.ms(q.step_ms) + ' s';
    }
    var rc = core.bind('sqRcpChip');
    if (rc) {
      core.html(rc, !conn ? '' : core.chip(q.recipe_ok ? T('s.tableOk') : '✕ ' + T('s.tableNg'),
        q.recipe_ok ? 'ok' : 'off', 'PLC Sum ' + fmt.hex16(q.recipe_sum)));
    }
    var chip = core.bind('seqChip');
    if (chip) {
      // 시퀀서 상태는 장비 상태와 다를 때만 함께 보여 준다(같은 말이 두 번 보이면 읽지 않는다).
      var qn = (t.seq || {}).name || '';
      core.html(chip, conn && t.state
        ? (qn && qn !== t.state.name ? core.chip(qn, 'off') + ' ' : '') +
          core.chip(t.state.name, stateLevel(t.state.code))
        : '');
    }
    updateProcess(t, conn);
  }

  /* ---------- 공정 진행 ---------- */
  function updateProcess(t, conn) {
    var p = t.process || {};
    var run = !!p.running;

    core.setText('sqRec', !conn ? fmt.DASH
      : (p.recipe || 'Select a recipe') + (p.number ? ' · No. ' + p.number : ''));
    if (!conn) p = {};

    if (run && p.total_ms) {
      // v0.4.15: 이어받은 공정은 시작 시각을 모른다 — 경과는 이어받은 뒤부터(v0.4.16 앞말을 짧게 — 1280 폭에서 두 줄로 넘었다)
      core.setText('sqTime', (p.adopted ? 'Adopted +' : '') + fmt.hms(p.elapsed_s) + ' / ' + fmt.hms((p.remaining_ms || 0) / 1000) +
        ' / ' + (p.eta || fmt.DASH));
    } else if (p.estimate && p.estimate.total_ms) {
      core.setText('sqTime', T('v.estimate') + ' ' + fmt.hms(p.estimate.total_ms / 1000));
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
      core.html(sl, (run && steps.length)
        ? steps.map(function (x, i) {
            var on = (i + 1) === p.step_in_block;
            return '<span class="st' + (on ? ' on' : '') + '">' + core.esc(x.name || (i + 1)) +
              '<i>' + core.esc(fmt.ms(x.ms)) + 's</i></span>';
          }).join('')
        : '');
    }

    // 시작 조건 — 판정은 서버가 했고 여기서는 안 된 것만 보여 준다.
    var cl = core.bind('sqChecks');
    if (cl) {
      var bad = (p.checks || []).filter(function (c) { return !c.ok; });
      // ★ 안 된 것만, 그것도 앞 셋만 보여 준다 — 패널이 넘치면 아무것도 안 읽힌다.
      //   전체 목록은 시작을 누를 때 확인 창에서 다시 보여 준다.
      // ★ v0.5.0(A4): 같은 모양의 짧은 목록 — 이름 + 상태 점, 설명은 툴팁
      core.html(cl, (!run && bad.length)
        ? bad.slice(0, 3).map(function (c) {
            var det = checkDetail(c);
            return sti(c.label, c.key === 'base' ? 'warn' : 'stop', c.label + (det ? ' — ' + det : ''));
          }).join('') + (bad.length > 3 ? sti('+' + (bad.length - 3) + ' more', 'stop',
            bad.slice(3).map(function (c) { var det = checkDetail(c); return c.label + (det ? ' — ' + det : ''); })
              .join('\n')) : '') +
          // O3 허가·발생기가 빠졌으면 수동 창의 O3 라인 켜기로 바로 간다(설정값은 거기서 확인)
          (bad.some(function (c) { return c.action === 'o3_on'; })
            ? ' <button class="btn sm" data-mnopen="o3">' + T('b.o3On') + '</button>' : '')
        : '');
    }

    // ★ v0.5.1(A3 · D4): 결과 수준으로 색(정상 = 회색 · 중단 등 = 노랑 · 실패 = 빨강). 'Last' 는 영문 칩, 문장은 한글 그대로
    var msg = p.message || '', last = false;
    if (p.last_result && !run && !msg) { msg = p.last_result; last = true; }
    var pm = core.bind('seqMsg');
    if (pm) {
      var lv = T.resultLevel(msg);
      var html = msg ? (last ? '<span class="chip">Last</span> ' : '') + core.esc(msg) : '';
      if (pm.innerHTML !== html) pm.innerHTML = html;
      pm.className = 'procmsg' + (lv ? ' ' + lv : '');
      pm.hidden = !msg;
    }
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

  /** 시작 조건 글자 — 베이스 압력은 게이지와 같은 규칙(fmt.torr)으로 여기서 만든다. */
  function checkDetail(c) {
    if (c.key === 'base' && ('cur' in c || 'target' in c)) {
      return 'Actual ' + fmt.torr(c.cur) + ' / Target ' + fmt.torr(c.target) + ' Torr';
    }
    return c.detail || '';
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
    if (un) un.hidden = ((s.unconfirmed || []).indexOf('CVG Pressure') < 0);
    var cmBox = core.bind('gCmBox');
    if (cmBox) cmBox.hidden = !(p && p.cm_installed);
    core.setText('gCm', conn ? fmt.torr(p.cm) : fmt.DASH);

    var aux = auxMap(s, t, conn);
    var i0 = t.inputs0;
    core.setText('vPump', !conn ? fmt.DASH
      : 'Pump ' + (aux['PMP'] ? T('s.on') : T('s.off')) + ' · ' +
        (core.bit(i0, 7) ? T('s.feedbackOk') : T('s.noFeedback')));
    core.setText('vIve', !conn ? fmt.DASH
      : 'IV-E ' + (aux['IV-E'] ? T('s.open') : T('s.closed')) + ' · VV ' + (aux['VV'] ? T('s.open') : T('s.closed')));

    var box = core.bind('ilks');
    if (!box) return;
    if (!conn) { core.html(box, sti(core.downText(), '')); return; }
    var str = s.structure || {};
    var out = [];
    (str.interlocks || []).forEach(function (k) {
      var on = core.bit(t.interlock, k.bit);
      // bad:true 인 비트는 '켜져 있으면 이상'이다(동시 요청·안전 정지 요구).
      var lvl = k.bad ? (on ? 'stop' : '') : (on ? 'ok' : 'warn');
      if (k.bad && !on) return;      // 이상이 없으면 굳이 보여 주지 않는다
      out.push({ name: k.tag, lvl: lvl, why: k.why });
    });
    // 위 kv 줄에서 이미 보여 준 입력은 칩으로 또 내지 않는다(패널이 넘친다).
    var SHOWN = { 'IVE-O': 1, 'IVE-C': 1, 'PMP-RUN': 1, 'ATM': 1 };
    (str.inputs0 || []).forEach(function (k) {
      if (k.unused || k.momentary || SHOWN[k.tag]) return;
      out.push(inputChip(t.inputs0, k, 'Input0 b' + k.bit));
    });
    // 입력 워드1(RF·O3 관련)은 아래 장비 전용 패널에서 이미 보여 준다 —
    // 같은 칩을 두 번 그리면 인터락 패널이 넘친다.
    core.html(box, stiList(out));
    snapIlks(box);
  }

  /** 인터락 목록 칸 높이를 줄 높이의 배수로 — 줄이 반쯤 잘리지 않게(넘치는 줄은 스크롤로 본다).
   *  ★ v0.5.4: 넘칠 때만이 아니라 늘 — 상자의 실제 높이(clientHeight)를 18 의 배수로 내려 맞춘다 */
  function snapIlks(box) {
    box.style.maxHeight = '';
    var h = box.clientHeight;
    if (!h) return;
    box.style.maxHeight = snapRows(h) + 'px';
  }

  function snapRows(h) { return Math.max(STI_ROW, Math.floor(h / STI_ROW) * STI_ROW); }

  /** 입력 한 줄(상태 점).
   *  ★ ok_when=false 인 입력(펌프 알람·가스 누출·과온·RF 알람)은 '이상일 때 1'이다 —
   *    v0.5.1(A3): 이름이 알람이라 '정상 = 초록 점'이면 '알람이 켜짐'으로 읽힌다 — 정상(0)이면 회색 점,
   *    이상(1)일 때만 빨강. ok_when=true 는 1 이 정상(초록).
   *    ok_when 이 없는 입력(운전 · 리미트)은 켜짐 = 초록 점, 꺼짐 = 회색 점(이상 아님). */
  function inputChip(word, k, why) {
    var on = core.bit(word, k.bit);
    var lvl;
    if (k.ok_when === false) lvl = on ? 'stop' : '';
    else if (k.ok_when === true) lvl = on ? 'ok' : 'stop';
    else lvl = on ? 'ok' : '';
    return { name: k.name, lvl: lvl, why: why };
  }

  /** ★ v0.5.3: 상태 목록은 알람(빨강) → 경고(노랑) → 그 밖 순(T.stiOrder) — 문제 항목이 위로.
   *  ★ v0.5.4: 알람이나 경고가 있으면 문제 항목만 그리고, 정상 항목은 회색 '+N OK' 한 칸(올리면 툴팁 · 누르면 알림에
   *  전체 이름) — 칸이 좁아도 문제 항목이 스크롤 아래로 숨지 않게(T.stiCompact) */
  function stiList(items) {
    var c = T.stiCompact(items);
    var h = c.items.map(function (i) { return sti(i.name, i.lvl, i.why); }).join('');
    if (c.ok.length) {
      var names = c.ok.map(function (i) { return i.name; }).join(' · ');
      h += '<span class="sti more" tabindex="0" role="button" data-okall="' + core.esc(names) + '" title="' +
        core.esc(names) + '"><i></i>+' + c.ok.length + ' OK</span>';
    }
    return h;
  }

  if (d && d.addEventListener) {
    d.addEventListener('click', function (ev) {
      var m = ev.target.closest && ev.target.closest('[data-okall]');
      if (m) core.toast('OK — ' + m.getAttribute('data-okall'), 'info');
    });
  }

  /** ★ v0.5.3: 알람 · 경고 항목은 줄 수 제한(max-height)과 상관없이 늘 보이게 — 맨 위에 모여 있으니
   *  그 줄 수만큼은 상자 높이를 늘린다(그 밖 항목은 스크롤). ★ v0.5.4: 높이가 늘 수 있는 장비 전용 칸(RF · O3)에만 쓴다 —
   *  인터락 목록은 높이 고정 칸 안이라 늘지 않으므로 문제 항목만 + '+N OK' 와 snapIlks 로 맞춘다 */
  var STI_ROW = 18;
  function keepProblems(box) {
    if (!box || !w.getComputedStyle) return;
    var probs = box.querySelectorAll('.sti.stop, .sti.warn');
    if (!probs.length) return;
    var cs = w.getComputedStyle(box);
    var cols = String(cs.gridTemplateColumns || '').split(' ').filter(Boolean).length || 1;
    var n = 0, wide = false;
    Array.prototype.forEach.call(probs, function (p) {
      var sp = p.classList.contains('w2') ? Math.min(2, cols) : 1;
      n += sp;
      if (sp > 1) wide = true;
    });
    var need = (Math.ceil(n / cols) + (wide && cols > 2 ? 1 : 0)) * STI_ROW;
    var cur = parseFloat(box.style.maxHeight) || parseFloat(cs.maxHeight);
    if (isFinite(cur) && need > cur) box.style.maxHeight = need + 'px';
  }

  /** 상태 점 목록 한 줄 — 이름 + 점. lvl: ok(초록 점) · warn · stop(점과 글자) · ''(회색 점) */
  function sti(name, lvl, why) {
    // ★ v0.5.2(A1): 한 열에 안 들어가는 이름은 두 열을 쓴다(잘리지 않게)
    return '<span class="sti' + (lvl ? ' ' + lvl : '') + (T.stiSpan(name) > 1 ? ' w2' : '') + '"' + (why ? ' title="' + core.esc(why) + '"' : '') +
      '><i></i>' + core.esc(name) + '</span>';
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
        ? fmt.int(h.out_pct) + ' %' : fmt.DASH);
      var pw = d.querySelector('[data-hpow="' + def.ch + '"]');
      if (pw) core.html(pw, (!conn || h.power == null) ? fmt.DASH
        : core.chip(h.power ? T('s.on') : T('s.off'), h.power ? 'ok' : 'off'));
      var st = d.querySelector('[data-hst="' + def.ch + '"]');
      if (!st) return;
      if (!conn) { core.html(st, fmt.DASH); return; }
      if (!h.comm_ok) core.html(st, core.chip(T('s.commLost'), 'stop', h.power_block || ''));
      else if (h.alarm) core.html(st, core.chip(T('s.ctrlAlarm'), 'stop'));
      else if (def.max_c == null) core.html(st, core.chip(T('s.limitUnset'), 'warn', '과온 한계가 없어 PLC 소프트 과온 감시가 꺼집니다 — PC 가 설정·전원 켜기를 막습니다'));
      else core.html(st, core.chip(T('s.normal'), 'ok'));
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
    if (!conn) { core.html(chips, ''); return; }
    var str = s.structure || {};
    var out = [{ name: 'RF ' + (e.rf_on ? T('s.on') : T('s.off')), lvl: e.rf_on ? 'ok' : '' }];
    (str.interlocks || []).forEach(function (k) {
      if (k.tag !== 'RF Permit') return;
      var ok = core.bit(t.interlock, k.bit);
      out.push({ name: k.tag, lvl: ok ? 'ok' : 'warn', why: k.why });
    });
    // 알람 성격 입력은 정상 회색·알람 빨강 (Powder O3 알람 칩과 같은 규칙)
    (str.inputs1 || []).forEach(function (k) {
      out.push(inputChip(t.inputs1, k, 'Input1 b' + k.bit));
    });
    chips.style.maxHeight = '';
    core.html(chips, stiList(out));
    keepProblems(chips);
  }

  /* ---------- 잠금 ---------- */
  var RUN_LOCKED = { pump_stop: 1, vent: 1, all_close: 1 };

  function updateLocks(s, t, conn) {
    var local = core.canOperate();
    var msg = '';
    var p = t.plc || {};
    if (t.offline) msg = '🔒 서버 연결이 끊겨 명령을 보낼 수 없습니다 — 다시 연결하는 중';
    else if (p.config_error) msg = '🔒 PLC 주소가 없어 연결하지 않았습니다 — 설정 탭에서 이 장비 PLC 주소를 넣으세요';
    else if (p.id_state === 'wrong') msg = '🔒 다른 장비의 PLC 입니다 — 주소를 확인하세요';
    else if (p.id_state === 'missing') msg = '🔒 PLC 장비 ID 가 없습니다(0) — 이 장비 PLC 인지 확인할 수 없습니다';
    else if (p.stalled) msg = '🔒 PLC 하트비트 멈춤 — PLC 가 STOP 이거나 멈췄습니다 · 명령을 보내지 않습니다';
    else if (!core.canOperate()) msg = '🔒 원격 접속은 보기 전용입니다';
    else if (!conn) msg = '🔒 PLC 연결이 끊겨 명령을 보낼 수 없습니다';

    // ★ 공정 단추는 updateProcess 가 상태별로 따로 판단한다 — 여기서 덮어쓰지 않는다.
    //   명령줄 · 알람 탭의 확인 · 리셋 모두 같은 규칙(원격 · 다른 장비 · 끊김이면 잠금).
    Array.prototype.forEach.call(d.querySelectorAll('[data-cmd]'), function (b) {
      if (b.closest('.procbar')) return;
      var c = b.dataset.cmd;
      // 종료는 다른 장비의 PLC 여도 된다(이 PC 에서만). ★ 서버가 끊겨도 마지막 권한이 이 PC 였으면
      //   누를 수 있다 — 끊긴 채 창을 닫는 길(force_close)이 바로 이것이다
      if (c === 'exit') { b.disabled = !core.wasLocal(); return; }
      // ★ 공정 중에는 펌핑 정지 · 벤트 · 전체 닫기를 잠근다(PLC 도 거절하지만 누를 수 있게 두지 않는다)
      var lockRun = RUN_LOCKED[c] && conn && !!(t.process || {}).running;
      b.disabled = !local || !conn || lockRun;
      var tip = lockRun ? '공정 중에는 쓸 수 없습니다 — 먼저 중단하세요' : '';
      if (b.title !== tip && (lockRun || b.title === '공정 중에는 쓸 수 없습니다 — 먼저 중단하세요')) b.title = tip;
    });
    core.setText('lockMsg', msg);
    var lm = core.bind('lockMsg');
    if (lm && lm.title !== msg) lm.title = msg;           // 줄을 바꿔 다 보인다(툴팁에도 같은 글)
    // '수동 조작…' — 서버 끊김 · PLC 끊김 · 멈춤이면 잠근다(값도 명령도 없다). 원격은 보기로 연다
    var mo = core.bind('mnOpen');
    if (mo) {
      var why = t.offline ? '서버 연결 끊김' : p.stalled ? 'PLC 하트비트 멈춤' : !conn ? 'PLC 끊김' : '';
      mo.disabled = !!why;
      var mt = why ? why + ' — 수동 조작을 열 수 없습니다' : '';
      if (mo.title !== mt) mo.title = mt;
    }
    core.setText('schemNote', !conn ? core.downText() + ' — No Data'
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
      (p.number ? ' (번호 ' + core.esc(p.number) + ')' : '') + '<br>' +
      T('v.step') + ' ' + core.esc(est.step_count || 0) + ' · ' + T('v.block') + ' ' + core.esc(est.block_count || 0) +
      ' · ' + T('v.estimate') + ' <b>' + fmt.hms((est.total_ms || 0) / 1000) + '</b><br><br>' +
      'PLC 에 레시피 표를 올리고, 베이스 압력에 도달하면 시작합니다.<br>' +
      '대기 중에는 [Cancel Wait]로 멈출 수 있습니다.' +
      ((p.checks || []).filter(function (c) { return !c.ok; }).length
        ? '<br><br>아직 안 된 조건:<br>' +
          (p.checks || []).filter(function (c) { return !c.ok; })
            .map(function (c) { return '· ' + core.esc(c.label) + ' — ' + core.esc(checkDetail(c)); })
            .join('<br>')
        : ''),
      T('b.start'), function () { w.app.send('process_start'); });
  }

  core.register('main', { render: render, update: update });
  // confirmFor — 배관도 조작 창(faceplate)이 펌핑 · 벤트에 같은 확인 창을 쓴다
  w.viewMain = { render: render, update: update, confirmFor: confirmFor };
})(window, document);
