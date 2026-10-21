/* ============================================================
 * views/main.js — 운전 탭 (v0.8.0 다시 짬).
 *
 *   좁은 배치(960×1000 기준): [Process | Start Conditions] · Interlock · Input · Valves · [Heater | MFC…]
 *   넓은 배치(1600×840 기준): 왼쪽 [Process · Valves · Heater] · 오른쪽 [Start Conditions · Interlock · Input · MFC…]
 *     (v0.8.1: 표 열을 고정하니 Heater | MFC 를 나란히 둘 폭이 없다 — Recent Events 는 P&ID 탭 넓은 배치와 Alarm/Log 탭에)
 * ★ v0.8.1: Heater · MFC 표는 table-layout fixed + colgroup — 이름 열만 남는 폭을 받고(말줄임 · title), 값 열은 가장 긴
 *   값 · 칩(넓은 대체 글꼴로 잼)이 들어가는 px 고정. 값이 바뀌어도, 끊겨도('—') 열 자리가 그대로다.
 *   명령줄 · 상단 상태 띠는 #app 에(이 탭과 P&ID 탭에서만 보인다).
 *
 * ★ 칸 크기 · 자리는 배치와 장비 구조로만 정해진다(고정 격자) — 값이 바뀌어도, 알람이 몇 개든, 끊겨도 어느 칸의 상자도
 *   움직이지 않는다. 모든 항목이 늘 보인다(묶음 표시 없음).
 * ★ PLC 연결이 끊기면 모든 값은 '—' 이고 명령 단추는 잠근다. 마지막 값을 보여 주면 운전자가 현재 상태로 오해한다.
 * ★ 위험하거나 되돌리기 어려운 명령은 확인 창을 거친다(제목에 장비 이름).
 * ★ 5 Hz 갱신은 글자 · 클래스만 바꾼다(core.html — 같으면 안 씀). DOM 은 render(배치 · 구조가 바뀔 때)만 새로 만든다.
 * ★ 한 번 누르면 한 번 — 보낸 명령은 응답(done)이 올 때까지 같은 단추를 막는다(core.busy, 최대 8 s).
 * ★ Process 칸 부품(viewMain.process)은 P&ID 탭 넓은 배치도 쓴다 — 바인드는 그 칸(root) 안에서만.
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

  // 배치 치수(px, 캔버스 기준) — 칸 높이는 이것과 장비 구조로만 정해진다
  var FRAME_H = 259, VIEW_PAD = 16, GAP = 6;
  var NARROW = { r1: 270, valves: 64, annCols: 8, chips: 4 };
  var WIDE = { proc: 200, start: 132, valves: 64, annCols: 6, chips: 7 };
  var LAMP_H = 22, LAMP_GAP = 4, PANEL_HEAD = 30, PANEL_PAD = 12, BORDER = 2;
  var HROW = 26, HROW_DENSE = 22, THEAD = 24;
  // v0.8.1: 표 열 폭(px, 여백 포함) — 이름 열(Channel · MFC)은 나머지. 근거는 README(v0.8.0 절 — 표 열 고정)
  var HEATER_COLS = [null, 72, 58, 108, 108];          // Channel | Setpoint | Actual | Power | Status
  var MFC_COLS = [null, 50, 82, 72, 62];               // MFC | Gas | Full Scale | Setpoint | Actual
  function colgroup(ws) {
    return '<colgroup>' + ws.map(function (x) { return x == null ? '<col>' : '<col style="width:' + x + 'px">'; }).join('') + '</colgroup>';
  }

  /** 램프판 칸 높이 = 머리 + 줄 수 × 22 + 사이 4 + 여백(장비 구조 · 열 수로만) */
  function annH(s, cols) {
    var rows = w.UI.annRows((s || {}).structure, cols);
    return PANEL_HEAD + BORDER + PANEL_PAD + rows * LAMP_H + Math.max(0, rows - 1) * LAMP_GAP;
  }
  function viewsH() { var b = core.box; return (b ? b.h : 1000) - FRAME_H - VIEW_PAD; }

  var builtSig = '';
  var parts = { process: [], start: [], ann: [], valves: [] };

  /* ===================== render ===================== */
  function render(s) {
    var sig = JSON.stringify([core.layout, (core.box || {}).h, (s.device || {}).key, (s.structure || {}).heaters,
                              (s.structure || {}).mfc, (s.structure || {}).annunciator, (s.recipe_limits || {}).recipe_valves]);
    if (sig !== builtSig) {
      builtSig = sig;
      buildLayout(s);
    }
    update(s.live || {});
  }

  function panel(cls, extra) { return '<div class="panel ' + cls + '"' + (extra || '') + '></div>'; }

  function buildLayout(s) {
    var grid = core.bind('opGrid');
    if (!grid) return;
    var wide = core.layout === 'wide', vh = viewsH();
    var cols = wide ? WIDE.annCols : NARROW.annCols, ah = annH(s, cols);
    var dev = s.device || {};
    var mfcHead = dev.has_o3 ? T('panel.mfcO3') : dev.has_rf ? T('panel.mfcRf') : T('panel.mfc');
    var heaterP = '<div class="panel p-heater"><div class="panel-head"><span data-t="panel.heater">' + T('panel.heater') + '</span>' +
      '<span class="note" data-bind="tcNote"></span></div><div class="panel-body tight"><table class="tbl fixed" data-bind="heaterTbl"></table></div></div>';
    var mfcP = '<div class="panel p-mfc"><div class="panel-head">' + mfcHead + '</div>' +
      '<div class="panel-body tight"><table class="tbl fixed" data-bind="mfcTbl"></table></div></div>';
    var hRoom, mRoom;
    if (wide) {
      grid.className = 'opgrid wide';
      grid.style.setProperty('--ann-h', ah + 'px');
      grid.innerHTML = '<div class="opcol cl">' + panel('p-proc', ' data-part="process"') + panel('p-valves', ' data-part="valves"') +
        heaterP + '</div><div class="opcol cr">' + panel('p-start', ' data-part="start"') + panel('p-ann', ' data-part="ann"') +
        mfcP + '</div>';
      hRoom = vh - WIDE.proc - WIDE.valves - 2 * GAP;
      mRoom = vh - WIDE.start - ah - 2 * GAP;
    } else {
      grid.className = 'opgrid narrow';
      grid.style.setProperty('--ann-h', ah + 'px');
      grid.innerHTML = '<div class="oprow r1">' + panel('p-proc', ' data-part="process"') + panel('p-start', ' data-part="start"') +
        '</div>' + panel('p-ann', ' data-part="ann"') + panel('p-valves', ' data-part="valves"') +
        '<div class="oprow r4">' + heaterP + mfcP + '</div>';
      hRoom = mRoom = vh - NARROW.r1 - ah - NARROW.valves - 3 * GAP;
    }
    parts = { process: [], start: [], ann: [], valves: [] };
    Array.prototype.forEach.call(grid.querySelectorAll('[data-part]'), function (root) {
      var k = root.getAttribute('data-part');
      if (k === 'process') processPart.build(root, s, core.layout);
      else if (k === 'ann') w.Panels.ann.build(root, s, { cols: cols });
      else w.Panels[k].build(root, s);
      parts[k].push(root);
    });
    buildHeaters(s, hRoom);
    buildMfc(s, mRoom);
  }

  /** 기본 설정에서 넘치지 않게 — 줄을 더 켜 넘치면 줄 높이를 22 로(구조가 바뀔 때 한 번). 두 표 같은 규칙 */
  function dense(tbl, room, rows) {
    var on = room > 0 && PANEL_HEAD + BORDER + 8 + THEAD + rows * HROW > room;
    tbl.classList.toggle('dense', on);
  }

  /* ---------- 히터 표 ---------- */
  function buildHeaters(s, room) {
    var tbl = core.bind('heaterTbl');
    if (!tbl) return;
    var heaters = (s.structure || {}).heaters || [];
    var n = heaters.filter(function (h) { return h.enabled; }).length;
    // 기본 설정(PEALD 6 · Powder 5 채널)에서 넘치지 않게 — 채널을 더 켜 넘치면 줄 높이를 22 로
    dense(tbl, room, n);
    // v0.8.1: 단위 ℃ 는 칸 이름(Heater (℃))에 — 머리에 붙이면 좁은 배치 이름 열이 남지 않는다. Output 열은 Power 칩에 합친다
    var html = colgroup(HEATER_COLS) + '<thead><tr><th>' + T('v.channel') + '</th><th>' + T('v.setpoint') + '</th><th>' +
      T('v.actual') + '</th><th title="전원(ON/OFF) · 출력(MV — 온도조절기가 내는 출력 %)">' + T('v.power') + '</th><th>' +
      T('v.status') + '</th></tr></thead><tbody>';
    heaters.forEach(function (h) {
      if (!h.enabled) return;   // 사용 채널만 보여 준다
      html += '<tr data-hrow="' + h.ch + '" data-fp="heater:' + h.ch + '" tabindex="0" role="button" title="CH' + core.esc(h.ch) +
        ' ' + core.esc(h.name) + ' — 누르면 조작 창">' +
        '<td class="nm">CH' + core.esc(h.ch) + ' ' + core.esc(h.name) + '</td>' +
        '<td class="sv" data-hsv="' + h.ch + '">—</td>' +
        '<td class="pv" data-hpv="' + h.ch + '">—</td>' +
        '<td data-hpow="' + h.ch + '">—</td>' +
        '<td data-hst="' + h.ch + '"></td></tr>';
    });
    core.html(tbl, html + '</tbody>');
    var miss = heaters.filter(function (h) { return h.enabled && h.max_c == null; });
    core.setText('tcNote', miss.length ? '⚠ ' + T('s.limitUnset') + ' ' + miss.length + ' ch' : '');
    var tn = core.bind('tcNote');            // v0.5.3: 설명 문장은 툴팁(한글)
    if (tn) tn.title = miss.length ? 'PLC 한계에 0 을 써서 막습니다' : '';
  }

  /* ---------- MFC 표 + 장비 전용 줄(PEALD: RF · PCV / Powder: O3) ---------- */
  function buildMfc(s, room) {
    var tbl = core.bind('mfcTbl');
    if (!tbl) return;
    var mfc = (s.structure || {}).mfc || [];
    var dev = s.device || {};
    var html = colgroup(MFC_COLS) + '<thead><tr><th>MFC</th><th>' + T('v.gas') + '</th><th>' + T('v.fullScale') + '</th><th>' +
      T('v.setpoint') + '</th><th>' + T('v.actual') + '</th></tr></thead><tbody>';
    mfc.forEach(function (m) {
      html += '<tr data-fp="mfc:' + core.esc(m.no) + '" tabindex="0" role="button" title="MFC' + core.esc(m.no) + ' ' +
        core.esc(m.name) + ' — 누르면 조작 창"><td class="nm">' + core.esc(m.no) + ' ' + core.esc(m.name) + '</td>' +
        '<td class="nm" title="' + core.esc(m.gas || '') + '">' + core.esc(m.gas || fmt.DASH) + '</td>' +
        '<td>' + (m.full_scale_sccm == null
          ? '<span class="unconf">Unset</span>'
          : core.esc(fmt.int(m.full_scale_sccm)) + '<span class="unit">sccm</span>') + '</td>' +
        '<td class="sv" data-msv="' + m.no + '">—</td>' +
        '<td class="pv" data-mpv="' + m.no + '">—</td></tr>';
    });
    // 장비 전용 줄 — 이름 colspan 2(이름 + Gas) · 값 colspan 3(Full Scale + Setpoint + Actual). 값은 단위까지
    var xn = 0;
    var xrow = function (key, name, fp, title, word) {
      xn++;
      return '<tr class="xrow"' + (fp ? ' data-fp="' + fp + '" tabindex="0" role="button"' : '') + ' title="' + core.esc(title || name) + '"' +
        '><td colspan="2" class="nm">' + name + '</td><td colspan="3" class="' + (word ? 'wv' : 'pv') + '" data-mx="' + key + '">—</td></tr>';
    };
    if (dev.has_rf) html += xrow('rf', 'RF Forward / Reflected', 'rf:RF');
    if (dev.has_pcv) html += xrow('pcv', 'PCV Target / Position', 'pcv:PCV');
    if (dev.has_o3) {
      html += xrow('o3', 'O3 Setpoint / Actual', 'o3:O3') +
        xrow('o3line', 'Gen · Bypass Pump · IV-B', 'o3:O3', 'O3 Generator · Bypass Pump · IV-B(바이패스 격리 밸브)', true) +
        xrow('o3off', T('v.offDelay'), '');
    }
    dense(tbl, room, mfc.length + xn);
    core.html(tbl, html + '</tbody>');
  }

  /* ===================== Process 칸 부품 ===================== */
  var KV = [['Block', 'v.block'], ['Step', 'v.step'], ['Cycle', 'v.cycle'], ['Group', 'v.group'], ['Elapsed', 'v.elapsed'],
            ['Remaining', 'v.remaining'], ['PlannedEnd', 'v.plannedEnd']];
  var processPart = {
    build: function (root, s, layout) {
      root._chips = layout === 'wide' ? WIDE.chips : NARROW.chips;
      root.innerHTML =
        '<div class="panel-head"><span data-t="panel.process">' + T('panel.process') + '</span>' +
        '<span class="pq-chips"><span data-bind="pqSeq"></span><span data-bind="pqTrip"></span></span>' +
        '<span class="note"><span data-bind="sqRcpChip"></span><button class="btn sm" data-bind="mnOpen">' + T('b.manual') +
        '</button></span></div>' +
        '<div class="panel-body pq">' +
        '<div class="pq-kv ' + (layout === 'wide' ? 'k7' : 'k43') + '">' + KV.map(function (k) {
          return '<div class="kvc"><span class="k">' + T(k[1]) + '</span><span class="v mono" data-bind="pq' + k[0] + '">—</span></div>';
        }).join('') + '</div>' +
        '<div class="pq-bar"><div class="bar"><span data-bind="sqBar"></span></div><span class="pct mono" data-bind="pqPct">—</span></div>' +
        '<div class="pq-steps" data-bind="sqSteps"></div>' +
        '<div class="procbar">' +
        '<button class="btn sm start" data-cmd="process_start">' + T('b.start') + '</button>' +
        '<button class="btn sm" data-cmd="process_pause">' + T('b.pause') + '</button>' +
        '<button class="btn sm" data-cmd="process_resume">' + T('b.resume') + '</button>' +
        '<button class="btn sm" data-cmd="process_stop_after_cycle">' + T('b.stopAfterCycle') + '</button>' +
        '<span class="pq-last"><button class="btn sm abort" data-cmd="process_abort">' + T('b.abort') + '</button>' +
        '<button class="btn sm" data-cmd="process_cancel_wait" data-bind="sqCancel" hidden>' + T('b.cancelWait') + '</button></span>' +
        '</div></div>';
    },
    update: function (root, t, conn) { updateProcess(root, t, conn); }
  };

  /* ===================== update ===================== */
  function update(t) {
    var s = core.state;
    if (!s) return;
    var conn = !!(t.plc && t.plc.connected);
    parts.process.forEach(function (r) { updateProcess(r, t, conn); });
    ['start', 'ann', 'valves'].forEach(function (k) {
      parts[k].forEach(function (r) { w.Panels[k].update(r, s, t); });
    });
    updateHeaters(s, t, conn);
    updateMfc(s, t, conn);
    updateLocks(s, t, conn);
  }

  function setIn(root, name, v) {
    var e = core.bind(name, root);
    if (e && e.textContent !== v) e.textContent = v;
  }

  function updateProcess(root, t, conn) {
    var q = t.seq || {};
    var rc = core.bind('sqRcpChip', root);
    if (rc) {
      core.html(rc, !conn ? '' : core.chip(q.recipe_ok ? T('s.tableOk') : '✕ ' + T('s.tableNg'),
        q.recipe_ok ? 'ok' : 'off', 'PLC Sum ' + fmt.hex16(q.recipe_sum)));
    }
    // 시퀀서 상태는 장비 상태와 다를 때만(같은 말이 두 번 보이면 읽지 않는다). 장비 상태는 상단 띠에
    var qn = q.name || '';
    core.html(core.bind('pqSeq', root), conn && t.state && qn && qn !== t.state.name ? core.chip(qn, 'off', 'Sequencer') : '');
    // v0.7.0(P7): 남은 PC 끊김 트립 — 작은 표시. v0.7.1(U4): 짧은 글 'Trip'(자세한 것은 툴팁)
    var trip = conn && t.plc && t.plc.pc_trip;
    core.html(core.bind('pqTrip', root), trip && t.state
      ? core.chip('Trip', 'warn', 'PC Trip — ' + tripTip(t.plc, t.state.code === ST_SAFE_STOP)) : '');

    var p = conn ? (t.process || {}) : {};
    var run = !!p.running;
    var D = fmt.DASH;
    var steps = p.steps || [];
    setIn(root, 'pqBlock', run ? (p.block || 0) + ' / ' + (p.block_count || D) : D);
    setIn(root, 'pqStep', run ? (p.step_in_block || D) + ' / ' + (steps.length || D) : D);
    setIn(root, 'pqCycle', run ? (p.cycle || 0) + ' / ' + (p.block_repeat || D) : D);
    setIn(root, 'pqGroup', run && p.group ? (p.group_pass || 0) + ' / ' + p.group.repeat : D);
    // v0.4.15: 이어받은 공정은 시작 시각을 모른다 — 경과는 이어받은 뒤부터('+')
    setIn(root, 'pqElapsed', run ? (p.adopted ? '+' : '') + fmt.hms(p.elapsed_s) : D);
    // v0.8.1: 공정 전 예상 시간은 'Est. ' 앞말 없이 흐린 색(class est)과 툴팁으로 — 앞말이 붙으면 칸에서 잘린다
    var est = !run && p.estimate && p.estimate.total_ms;
    setIn(root, 'pqRemaining', run && p.total_ms ? fmt.hms((p.remaining_ms || 0) / 1000) : (est ? fmt.hms(p.estimate.total_ms / 1000) : D));
    var rv = core.bind('pqRemaining', root);
    if (rv) {
      rv.classList.toggle('est', !!est);
      var rt = est ? T('v.estimate') + ' — 레시피 기준 예상 시간(공정 전)' : '';
      if (rv.title !== rt) rv.title = rt;
    }
    setIn(root, 'pqPlannedEnd', run ? (p.eta || D) : D);

    var done = (run && p.total_ms) ? (1 - (p.remaining_ms || 0) / p.total_ms) : 0;
    var bar = core.bind('sqBar', root);
    if (bar) {
      var wd = Math.max(0, Math.min(100, done * 100)).toFixed(1) + '%';
      if (bar.style.width !== wd) bar.style.width = wd;
      var bc = (p.paused || p.stop_reserved) ? 'warn' : '';
      if (bar.className !== bc) bar.className = bc;
    }
    setIn(root, 'pqPct', run && p.total_ms ? Math.floor(done * 100) + ' %' : D);

    // 스텝 줄(높이 고정) — 공정 중이면 그 블록 스텝 칩(폭 고정, 지금 스텝이 늘 보이게 앞뒤를 고른다 — 전체 수는 Step 칸).
    // 공정이 아니면 같은 자리에 시작 흐름 문구 또는 'Last' 칩 + 끝 결과(★ v0.5.1(A3): 결과 수준으로 색)
    var sl = core.bind('sqSteps', root);
    if (sl) {
      var html, cls = 'pq-steps';
      if (run && steps.length) {
        var n = root._chips || 4, cur = Math.max(1, p.step_in_block || 1);
        var a = Math.max(0, Math.min(steps.length - n, cur - 1 - Math.floor(n / 2)));
        html = steps.slice(a, a + n).map(function (x, i) {
          var no = a + i + 1, on = no === cur;
          return '<span class="st' + (on ? ' on' : '') + '" title="' + core.esc((x.name || no) + ' · ' + fmt.ms(x.ms) + ' s') +
            '"><b>' + no + '</b> <span class="nm">' + core.esc(x.name || '') + '</span><i>' +
            (on ? core.esc(fmt.ms(p.step_ms)) + ' / ' : '') + core.esc(fmt.ms(x.ms)) + ' s</i></span>';
        }).join('');
      } else {
        var msg = p.message || '', last = false;
        if (p.last_result && !run && !msg) { msg = p.last_result; last = true; }
        if (!msg && conn && !p.recipe) msg = 'Select a recipe';
        var lv = T.resultLevel(msg);
        html = msg ? '<span class="procmsg' + (lv ? ' ' + lv : '') + '" title="' + core.esc(msg) + '">' +
          (last ? '<span class="chip">Last</span> ' : '') + core.esc(msg) + '</span>' : '';
        cls += ' msg';
      }
      core.html(sl, html);
      if (sl.className !== cls) sl.className = cls;
    }
    var cancel = core.bind('sqCancel', root);
    var abort = root.querySelector('[data-cmd="process_abort"]');
    if (cancel) cancel.hidden = (p.phase !== 'base_wait');
    if (abort) abort.hidden = (p.phase === 'base_wait');

    // 상태에 맞는 단추만 살린다 — 누를 수 없는 단추를 눌러 보게 두지 않는다.
    var local = core.canOperate();
    var flow = p.phase && p.phase !== 'idle';
    en('process_start', local && conn && !run && !flow && !!p.can_start);
    en('process_pause', local && conn && run && !p.paused);
    en('process_resume', local && conn && !!p.paused);
    en('process_stop_after_cycle', local && conn && run && !p.paused);
    en('process_abort', local && conn && run);
    en('process_cancel_wait', local && p.phase === 'base_wait');
  }

  /** 공정 단추 — 두 탭(운전 · P&ID 넓은 배치)의 같은 단추를 모두. 진행 중(응답 기다림)이면 막고 이유 */
  function en(cmd, ok) {
    var busy = core.busy.has('cmd:' + cmd);
    Array.prototype.forEach.call(d.querySelectorAll('.procbar [data-cmd="' + cmd + '"]'), function (b) {
      var off = !ok || busy;
      if (b.disabled !== off) b.disabled = off;
      var tip = busy ? core.BUSY_TIP : '';
      if (b.title !== tip) b.title = tip;
    });
  }

  var ST_SAFE_STOP = 6;     // 장비 상태 코드 — 안전 정지(addresses.STATE_SAFE_STOP)

  /** v0.7.2: 'Trip' 칩 툴팁 — Program Notices 와 같은 규칙(v0.7.1 P6). '[Alarm Reset] 으로 풀립니다' 는 트립만이 원인일
   *  때만, 비상정지 · 중대 알람이 같이 있으면 그 원인을 적는다. v0.8.0: 상단 띠(strip.js)도 이 규칙을 쓴다 */
  function tripTip(plc, safeStop) {
    var head = 'PC 연결이 끊겼던 기록(PC Link Lost)이 남아 있습니다. ';
    if (!plc.pc_trip_only) {
      return head + (safeStop ? 'PC 끊김 트립도 남아 있지만 PLC 안전 정지 원인은 ' : 'PLC 안전 정지 원인 ') +
        (plc.pc_trip_cause || '중대 알람') + (safeStop ? ' 입니다' : '') + ' — 원인을 없앤 뒤 [Alarm Reset]';
    }
    return head + (safeStop ? 'PLC 가 안전 정지 상태입니다. ' : '공정이 끝나면 PLC 가 안전 정지합니다. ') +
      '[Alarm Reset] 으로 풀립니다';
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
      // v0.8.1: Power 칩에 출력 % 를 합친다 — 'ON 35 %'(출력은 온도조절기 통신이 될 때만) · 'OFF' · 모름 '—'
      var pw = d.querySelector('[data-hpow="' + def.ch + '"]');
      if (pw) core.html(pw, (!conn || h.power == null) ? fmt.DASH
        : core.chip(h.power ? T('s.on') + (h.comm_ok && h.out_pct != null ? ' ' + fmt.int(h.out_pct) + ' %' : '') : T('s.off'),
          h.power ? 'ok' : 'off'));
      var st = d.querySelector('[data-hst="' + def.ch + '"]');
      if (!st) return;
      if (!conn) { core.html(st, fmt.DASH); return; }
      if (!h.comm_ok) core.html(st, core.chip(T('s.commLost'), 'stop', h.power_block || ''));
      else if (h.alarm) core.html(st, core.chip(T('s.ctrlAlarm'), 'stop', '온도조절기(Controller) 알람'));
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
    var e = t.extra || {}, D = fmt.DASH;
    set('[data-mx="rf"]', conn ? fmt.watt(e.rf_fwd) + ' / ' + fmt.watt(e.rf_ref) + ' W' : D);
    set('[data-mx="pcv"]', conn ? fmt.pct(e.pcv_sv) + ' / ' + fmt.pct(e.pcv_pv) + ' %' : D);
    var u = e.o3_unit || '';
    set('[data-mx="o3"]', conn ? fmt.num(e.o3_sv, 1) + ' / ' + fmt.num(e.o3_pv, 1) + ' ' + u : D);
    var aux = auxMap(s, t, conn);
    set('[data-mx="o3line"]', conn ? (aux['O3GEN'] ? T('s.on') : T('s.off')) + ' · ' + (aux['BPMP'] ? T('s.on') : T('s.off')) +
      ' · ' + (aux['IV-B'] ? T('s.open') : T('s.closed')) : D);
    var left = ((t.manual || {}).o3_off_left_s) || 0;
    set('[data-mx="o3off"]', conn && left > 0 ? left + ' s' : D);
  }

  /* ---------- 잠금 · 명령줄 ---------- */
  var RUN_LOCKED = { pump_stop: 1, vent: 1, all_close: 1 };
  var RUN_TIP = '공정 중에는 쓸 수 없습니다 — 먼저 중단하세요';

  /** 이미 그 상태인 명령 — 조작 창과 같은 규칙(UI.alreadyWhy). 막는 이유(한글) 또는 '' */
  function alreadyWhy(c, s, t, conn) { return conn ? w.UI.alreadyWhy(c, s.structure, t) : ''; }

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
    //   명령줄 · 알람 탭의 확인 · 리셋 모두 같은 규칙(원격 · 다른 장비 · 끊김이면 잠금 · 진행 중이면 잠금).
    var running = conn && !!(t.process || {}).running;
    Array.prototype.forEach.call(d.querySelectorAll('[data-cmd]'), function (b) {
      if (b.closest('.procbar')) return;
      var c = b.dataset.cmd;
      // 종료는 다른 장비의 PLC 여도 된다(이 PC 에서만). ★ 서버가 끊겨도 마지막 권한이 이 PC 였으면
      //   누를 수 있다 — 끊긴 채 창을 닫는 길(force_close)이 바로 이것이다
      if (c === 'exit') { b.disabled = !core.wasLocal(); return; }
      // ★ 공정 중에는 펌핑 정지 · 벤트 · 전체 닫기를 잠근다(PLC 도 거절하지만 누를 수 있게 두지 않는다)
      var lockRun = RUN_LOCKED[c] && running;
      var already = alreadyWhy(c, s, t, conn);
      var busy = core.busy.has('cmd:' + c);
      var off = !local || !conn || !!lockRun || !!already || busy;
      if (b.disabled !== off) b.disabled = off;
      var tip = busy ? core.BUSY_TIP : lockRun ? RUN_TIP : already;
      if (b.title !== tip) b.title = tip;
      // 알람 확인 전(부저 울림)이면 Alarm Ack 를 빨간 테두리로
      if (c === 'alarm_ack') b.classList.toggle('attn', conn && !!t.alarm_new);
    });
    core.setText('lockMsg', msg);
    var lm = core.bind('lockMsg');
    if (lm && lm.title !== msg) lm.title = msg;           // 두 줄까지(넘치면 말줄임) — 툴팁에 전체 글
    // '수동 조작…' — 서버 끊김 · PLC 끊김 · 멈춤이면 잠근다(값도 명령도 없다). 원격은 보기로 연다. 두 탭 모두
    var why = t.offline ? '서버 연결 끊김' : p.stalled ? 'PLC 하트비트 멈춤' : !conn ? 'PLC 끊김' : '';
    var mt = why ? why + ' — 수동 조작을 열 수 없습니다' : '';
    Array.prototype.forEach.call(d.querySelectorAll('[data-bind="mnOpen"]'), function (mo) {
      mo.disabled = !!why;
      if (mo.title !== mt) mo.title = mt;
    });
  }

  function set(sel, v) {
    var e = d.querySelector(sel);
    if (e && e.textContent !== v) e.textContent = v;
  }

  /* ---------- 명령 ---------- */
  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cmd]');
    if (!b || b.disabled) return;
    var c = b.dataset.cmd;
    if (c === 'exit') { core.askExit(); return; }
    if (core.busy.has('cmd:' + c)) return;             // 한 번 누르면 한 번 — 응답(done)을 기다린다
    if (c === 'process_start') { askStart(); return; }
    var go = function () { send(c); };
    var cf = confirmFor(c);
    if (cf) core.confirmAsk(cf.t, cf.b, cf.ok, go);
    else go();
  });

  /** 명령을 보내고 진행 중 잠금(같은 단추를 응답까지 막는다) */
  function send(c, payload) {
    var rid = core.sendBusy('cmd:' + c, c, payload);
    if (core.state && core.state.live) update(core.state.live);
    return rid;
  }

  /** 확인 창 문구. Powder 는 O3 라인이 켜져 있으면 전체 닫기가 라인도 한꺼번에 끈다고 알린다.
   *  ★ v0.8.0: Pumping Start — 수동 밸브 요청이 남아 있는데 Valve Permit 이 없으면, 펌핑으로 허가가 생기는 순간 그 밸브가
   *  운전자 조작 없이 열린다 — 먼저 묻는다(명령줄 · 조작 창 같은 창). 그 밖에는 확인 없이 */
  function confirmFor(c) {
    var st = core.state || {};
    var t = st.live || {};
    if (c === 'pump_start') {
      var mn = t.manual || {};
      if (!mn.valve_request || mn.valve_permit) return null;
      var names = ((st.structure || {}).valves || []).filter(function (v) { return core.bit(mn.valve_request, v.bit); })
        .map(function (v) { return v.tag; });
      if (!names.length) return null;
      return { t: 'Pumping Start 를 할까요?', ok: T('b.pumpStart'),
        b: '수동 밸브 요청이 남아 있습니다: <b>' + core.esc(names.join(' · ')) + '</b> — 펌핑으로 Valve Permit 이 생기면 ' +
           '이 밸브가 바로 열립니다.<br>먼저 그 밸브를 Close 하거나 [Close All Valves] 로 지우세요. 그대로 펌핑할까요?' };
    }
    var cf = CONFIRM[c];
    if (!cf || c !== 'all_close') return cf;
    if (!(st.device || {}).has_o3) return cf;
    var aux = auxMap(st, t, !!(t.plc && t.plc.connected));
    var mn2 = t.manual || {};
    var o3on = aux['O3GEN'] || aux['IV-B'] || aux['BPMP'] || (mn2.aux_request || 0) !== 0;
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
            .map(function (c) { return '· ' + core.esc(c.label) + ' — ' + core.esc(w.UI.checkDetail(c)); })
            .join('<br>')
        : '') + runInfoForm(),
      T('b.start'), function () {
        var rid = w.app.send('process_start', runInfo());
        if (rid) core.busy.start('cmd:process_start', rid);
      });
  }

  /* ---------- v0.7.0(A2) 실행 정보 — 작업자 · 시료 · 메모(모두 선택) ---------- */
  var RUN_INFO = [['operator', 'Operator', 40], ['sample', 'Sample', 80], ['memo', 'Memo', 200]];

  function runInfoForm() {
    var last = (core.state || {}).last_operator || '';
    return '<div class="runinfo">' + RUN_INFO.map(function (f) {
      return '<label><span>' + f[1] + '</span><input class="inp" data-runinfo="' + f[0] + '" maxlength="' + f[2] + '"' +
        (f[0] === 'operator' ? ' value="' + core.esc(last) + '"' : '') + ' placeholder="(선택)"></label>';
    }).join('') + '<div class="hint">Enter 로는 시작하지 않습니다 — [' + core.esc(T('b.start')) + '] 를 누르세요.</div></div>';
  }

  function runInfo() {
    var out = {};
    Array.prototype.forEach.call(d.querySelectorAll('[data-runinfo]'), function (e) {
      out[e.getAttribute('data-runinfo')] = String(e.value || '').trim();
    });
    return out;
  }

  /** 실행 정보 칸의 Enter 는 막는다(시작 단추로만) — 막았으면 true. */
  function runInfoKey(ev) {
    if (ev.key === 'Enter' && ev.target && ev.target.closest && ev.target.closest('[data-runinfo]')) {
      ev.preventDefault();
      ev.stopPropagation();
      return true;
    }
    return false;
  }
  if (d && d.addEventListener) d.addEventListener('keydown', runInfoKey, true);

  core.register('main', { render: render, update: update });
  // confirmFor — 조작 창(faceplate)이 펌핑 · 벤트에 같은 확인 창을 쓴다. process — P&ID 탭 넓은 배치가 같은 칸 부품을 쓴다
  w.viewMain = { render: render, update: update, confirmFor: confirmFor, runInfoKey: runInfoKey, tripTip: tripTip,
                 process: processPart, annH: annH, viewsH: viewsH, send: send };
})(window, document);
