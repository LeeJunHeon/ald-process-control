/* ============================================================
 * views/main.js — 운전 탭.
 *
 * 구성: 배관도(schematic.js) · 공정 진행/준비 · 압력·배기·인터락 · 히터 표 · MFC 표 · 명령 바
 *
 * ★ 대기 상태에서는 '공정 진행' 패널이 '공정 준비'(레시피 선택 + 시작 조건 체크리스트)로
 *   통째로 바뀐다. 두 패널을 나란히 두면 화면의 절반이 늘 쓸모없이 비어 있게 된다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var panelMode = null;      // 'run' | 'ready' — 바뀔 때만 DOM을 다시 만든다
  var heaterInputs = {};     // 히터 SV 입력칸 (적용 버튼이 읽어 간다)

  /* ===================== render ===================== */
  function render(s) {
    w.viewSchematic.render(s);
    renderHeaters(s);
    renderMfc(s);
    renderVacuum(s);
    panelMode = null;        // 구조가 바뀌었으니 공정 패널도 다시 만든다
    syncProcPanel(s, (s.live || {}).process || {});
  }

  /* ---------- 히터 표 ---------- */
  function renderHeaters(s) {
    var tbl = core.bind('heaterTbl');
    if (!tbl) return;
    heaterInputs = {};
    var html = '<thead><tr><th>채널</th><th>SV °C</th><th>PV °C</th><th>출력</th><th>상태</th></tr></thead><tbody>';
    (s.heaters || []).forEach(function (h) {
      var ln = (s.lines || []).filter(function (l) { return l.id === h.line; })[0];
      var name = h.label + (ln && ln.material ? ' (' + ln.material + ')' : '');
      var missing = ln && !ln.enabled;
      html += '<tr data-hrow="' + core.esc(h.id) + '">' +
        '<td>' + core.esc(name) + '</td>' +
        '<td class="sv"><input class="cell mono" data-hsv="' + core.esc(h.id) + '"' +
        (h.enabled ? '' : ' disabled') + '></td>' +
        '<td class="pv" data-hpv="' + core.esc(h.id) + '">' + fmt.DASH + '</td>' +
        '<td data-hout="' + core.esc(h.id) + '" class="dim">' + fmt.DASH + '</td>' +
        '<td data-hst="' + core.esc(h.id) + '">' + (missing ? '미장착' : h.enabled ? '' : 'OFF') + '</td></tr>';
    });
    tbl.innerHTML = html + '</tbody>';
    Array.prototype.forEach.call(tbl.querySelectorAll('[data-hsv]'), function (i) {
      heaterInputs[i.getAttribute('data-hsv')] = i;
      var h = (s.heaters || []).filter(function (x) { return x.id === i.getAttribute('data-hsv'); })[0];
      i.value = h && h.enabled ? fmt.temp(h.default_sv) : '';
    });
  }

  /* ---------- MFC 표 ---------- */
  function renderMfc(s) {
    var tbl = core.bind('mfcTbl');
    if (!tbl) return;
    var html = '<thead><tr><th>라인</th><th>용도</th><th>가스</th><th>범위</th><th>SV</th><th>PV</th><th>상태</th></tr></thead><tbody>';
    (s.lines || []).forEach(function (l) {
      var m = l.mfc || {};
      html += '<tr><td><b>' + core.esc(l.id) + '</b></td>' +
        '<td>' + core.esc(l.enabled ? l.label : '미장착') + '</td>' +
        '<td>' + core.esc(l.enabled ? (m.gas || '') : fmt.DASH) + '</td>' +
        '<td>' + core.esc(l.enabled ? (m.full_scale || '') : fmt.DASH) + '</td>' +
        '<td class="sv" data-msv="' + core.esc(l.id) + '">' + fmt.DASH + '</td>' +
        '<td class="pv" data-mpv="' + core.esc(l.id) + '">' + fmt.DASH + '</td>' +
        '<td data-mst="' + core.esc(l.id) + '"></td></tr>';
    });
    tbl.innerHTML = html + '</tbody>';
    var a = s.alarm_cfg || {};
    core.setText('mfcNote', '편차 경고 ±' + fmt.num(a.mfc_dev_pct_fs, 0) + ' % FS · 판정 지연 ' +
      (a.mfc_delay_s || 0) + ' s · 단위 sccm');
  }

  /* ---------- 압력 · 배기 · 인터락 ---------- */
  function renderVacuum(s) {
    var box = core.bind('vacBody');
    if (!box) return;
    var g = s.gauges || {};
    var html = '<div class="gauges">';
    ['baratron', 'convectron'].forEach(function (k) {
      if (!g[k]) return;
      html += '<div class="gauge"><div class="k">' + core.esc(g[k].label + (g[k].note ? ' (' + g[k].note + ')' : '')) +
        '</div><div class="v"><span data-bind="gv:' + k + '">' + fmt.DASH + '</span>' +
        '<span class="u">' + core.esc(g[k].unit || 'Torr') + '</span></div></div>';
    });
    html += '</div>' +
      '<div class="kv"><span class="k">' + core.esc(((s.chamber_io || {}).tv || {}).label || '스로틀 밸브') +
      '</span><span class="v" data-bind="vacTv">' + fmt.DASH + '</span></div>' +
      '<div class="kv"><span class="k">펌프 · 배기</span><span class="v" data-bind="vacPump">' + fmt.DASH + '</span></div>' +
      '<div class="ilks" data-bind="ilks"></div>';
    box.innerHTML = html;
  }

  /* ===================== update ===================== */
  function update(t) {
    var s = core.state;
    if (!s) return;
    w.viewSchematic.update(t);
    updateHeaters(s, t);
    updateMfc(s, t);
    updateVacuum(s, t);
    syncProcPanel(s, t.process || {});
    updateProc(s, t);
    updateLocks(s, t);
  }

  function updateHeaters(s, t) {
    var hv = t.heaters || {};
    (s.heaters || []).forEach(function (h) {
      var v = hv[h.id] || {};
      set('[data-hpv="' + h.id + '"]', fmt.temp(v.pv));
      set('[data-hout="' + h.id + '"]', v.out_pct == null ? fmt.DASH : v.out_pct + ' %');
      var st = d.querySelector('[data-hst="' + h.id + '"]');
      var row = d.querySelector('[data-hrow="' + h.id + '"]');
      if (!st) return;
      if (!v.on) { st.textContent = st.textContent || 'OFF'; if (row) row.classList.remove('hl'); return; }
      var dev = Math.abs((v.pv || 0) - (v.sv || 0));
      var bad = dev > (h.dev_warn || 99);
      st.innerHTML = bad ? core.chip('⚠ 편차 ' + fmt.temp(dev), 'warn') : core.chip('정상', 'ok');
      if (row) row.classList.toggle('hl', bad);
    });
  }

  function updateMfc(s, t) {
    var mv = t.mfc || {}, total = 0;
    (s.lines || []).forEach(function (l) {
      var v = mv[l.id] || {};
      set('[data-msv="' + l.id + '"]', v.sv == null ? fmt.DASH : fmt.flow(v.sv));
      set('[data-mpv="' + l.id + '"]', v.pv == null ? fmt.DASH : fmt.flow(v.pv));
      var st = d.querySelector('[data-mst="' + l.id + '"]');
      if (st) st.innerHTML = !l.enabled ? '<span class="dim">' + fmt.DASH + '</span>'
        : (v.pv > 0.05 ? core.chip('흐름', 'ok') : core.chip('대기', 'neutral'));
      if (v.pv) total += v.pv;
    });
    core.setText('mfcTotal', '총 ' + fmt.flow(total) + ' sccm');
  }

  function updateVacuum(s, t) {
    var g = t.gauges || {}, io = t.io || {};
    set('[data-bind="gv:baratron"]', fmt.torr(g.baratron));
    set('[data-bind="gv:convectron"]', fmt.torr(g.convectron));
    var full = (io.tv_pct || 0) >= 99.5;
    set('[data-bind="vacTv"]', fmt.pct(io.tv_pct) + ' %  ·  ' + (full ? '전개' : '위치 제어'));
    set('[data-bind="vacPump"]', (io.dry_pump ? 'Dry pump ON' : 'Dry pump OFF') + ' · ' +
      (io.rv ? 'RV 열림' : 'RV 닫힘') + (io.vent ? ' · 벤트 열림' : ''));
    var box = core.bind('ilks');
    if (box) {
      var iv = t.interlocks || {};
      box.innerHTML = (s.interlocks || []).map(function (k) {
        return core.chip(k.label, iv[k.id] ? 'ok' : 'stop', true);
      }).join('');
    }
  }

  /* ---------- 공정 진행 / 공정 준비 ---------- */
  function syncProcPanel(s, p) {
    var want = (p.mode && p.mode !== 'idle') ? 'run' : 'ready';
    if (want === panelMode) return;
    panelMode = want;
    core.setText('procTitle', want === 'run' ? '공정 진행' : '공정 준비');
    var head = core.bind('procTitle');
    if (head) head.innerHTML = (want === 'run' ? '공정 진행' : '공정 준비') +
      '<span class="note"><span class="chip neutral" data-bind="procPill">대기</span></span>';
    var box = core.bind('procBody');
    if (!box) return;
    box.innerHTML = want === 'run' ? runHtml() : readyHtml(s);
    if (want === 'ready') fillRecipeSelect(s);
  }

  function runHtml() {
    return '<div class="proc-recipe" data-bind="pRecipe">' + fmt.DASH + '</div>' +
      '<div class="proc-sub" data-bind="pBlock">' + fmt.DASH + '</div>' +
      '<div class="bar-row"><div class="bar-label"><span>사이클</span>' +
      '<span class="v"><b data-bind="pCycle">0</b><span class="tot"> / <span data-bind="pCycles">0</span></span></span></div>' +
      '<div class="bar"><i data-bind="pCycleBar"></i></div></div>' +
      '<div class="bar-row"><div class="bar-label"><span data-bind="pStepName">스텝</span>' +
      '<span class="v"><b data-bind="pStepT">0.0</b><span class="tot"> / <span data-bind="pStepTot">0.0</span> s</span></span></div>' +
      '<div class="bar step"><i data-bind="pStepBar"></i></div></div>' +
      '<div class="steps" data-bind="pSteps"></div>' +
      '<div class="timeline"><div><div class="k">경과</div><div class="v" data-bind="pElapsed">00:00:00</div></div>' +
      '<div><div class="k">남은 시간</div><div class="v" data-bind="pRemain">00:00:00</div></div>' +
      '<div><div class="k">종료 예정</div><div class="v" data-bind="pEta">' + fmt.DASH + '</div></div></div>' +
      // 즉시 중단은 한 줄을 통째로 쓴다 — 급할 때 가장 크고 확실하게 눌려야 한다.
      '<div class="row" style="margin-bottom:6px">' +
      '<button class="btn wide" data-cmd="process_stop_after_cycle">사이클 후 정지</button>' +
      '<button class="btn wide" data-cmd="process_pause">일시정지</button></div>' +
      '<button class="btn danger" style="width:100%" data-cmd="process_abort">즉시 중단</button>';
  }

  function readyHtml(s) {
    return '<div class="field"><label>레시피</label>' +
      '<select class="inp" data-bind="readyRecipe"></select></div>' +
      '<div class="proc-sub" style="margin-top:5px" data-bind="readyInfo">' + fmt.DASH + '</div>' +
      '<div class="hint" style="margin-bottom:5px">시작 조건</div>' +
      '<ul class="checklist" data-bind="readyChecks"></ul>' +
      '<div class="row" style="margin-top:12px">' +
      '<button class="btn primary wide" data-cmd="process_start" style="padding:11px">공정 시작</button>' +
      '<button class="btn" data-cmd="goto_recipe">레시피 편집</button></div>';
  }

  function fillRecipeSelect(s) {
    var sel = core.bind('readyRecipe');
    if (!sel) return;
    sel.innerHTML = '';
    (s.recipes || []).forEach(function (n) {
      sel.appendChild(core.h('option', { value: n, text: n }));
    });
    if (!(s.recipes || []).length) sel.appendChild(core.h('option', { value: '', text: '레시피가 없습니다' }));
  }

  function updateProc(s, t) {
    var p = t.process || {};
    var pill = core.bind('procPill');
    if (pill) {
      var m = { running: ['RUN', 'ok'], paused: ['일시정지', 'warn'], stopping: ['정지 예정', 'warn'] }[p.mode];
      pill.className = 'chip ' + (m ? m[1] : 'neutral');
      pill.textContent = m ? m[0] : '대기';
    }
    if (panelMode === 'run') {
      core.setText('pRecipe', p.recipe || fmt.DASH);
      core.setText('pBlock', '블록 ' + ((p.block || 0) + 1) + ' / ' + (p.block_count || 0) +
        ' · ' + (p.block_name || '') + ' 사이클');
      core.setText('pCycle', p.cycle || 0);
      core.setText('pCycles', p.cycles || 0);
      bar('pCycleBar', (p.cycle || 0) / Math.max(1, p.cycles || 1));
      core.setText('pStepName', '스텝 ' + ((p.step || 0) + 1) + ' / ' + (p.step_count || 0) +
        ' · ' + (p.step_name || ''));
      core.setText('pStepT', fmt.sec(p.step_elapsed_s, 1));
      core.setText('pStepTot', fmt.sec(p.step_total_s, 1));
      bar('pStepBar', (p.step_elapsed_s || 0) / Math.max(0.001, p.step_total_s || 1));
      renderStepBoxes(p);
      core.setText('pElapsed', fmt.hms(p.elapsed_s));
      core.setText('pRemain', fmt.hms(p.remaining_s));
      core.setText('pEta', p.eta || fmt.DASH);
      var sac = d.querySelector('[data-cmd="process_stop_after_cycle"]');
      if (sac) sac.classList.toggle('primary', !!p.stop_after_cycle);
      var pz = d.querySelector('[data-cmd="process_pause"]');
      if (pz) pz.textContent = p.mode === 'paused' ? '재개' : '일시정지';
    } else {
      updateReady(s, t);
    }
  }

  var lastStepSig = '';
  function renderStepBoxes(p) {
    var box = core.bind('pSteps');
    if (!box) return;
    var sig = (p.steps || []).map(function (x) { return x.name + x.time_s; }).join('|');
    if (sig !== lastStepSig) {
      lastStepSig = sig;
      box.innerHTML = (p.steps || []).map(function (x) {
        return '<div class="stepbox"><div class="n">' + core.esc(x.name) + '</div>' +
          '<div class="t">' + fmt.sec(x.time_s) + ' s</div></div>';
      }).join('');
    }
    Array.prototype.forEach.call(box.children, function (c, i) { c.classList.toggle('on', i === p.step); });
  }

  function updateReady(s, t) {
    var list = core.bind('readyChecks');
    if (!list) return;
    var sel = core.bind('readyRecipe');
    var name = sel ? sel.value : '';
    core.setText('readyInfo', name ? ('선택: ' + name) : '레시피를 선택하세요');
    // 시작 조건은 서버가 보내는 값으로 판정한다(화면이 기준을 따로 갖지 않는다).
    var checks = startChecks(s, t);
    list.innerHTML = checks.map(function (c) {
      return '<li class="' + (c.ok ? '' : 'bad') + '"><span class="mk">' + (c.ok ? '✓' : '✕') +
        '</span><span>' + core.esc(c.label) + '</span></li>';
    }).join('');
    var btn = d.querySelector('[data-cmd="process_start"]');
    if (btn) btn.disabled = !core.canOperate() || !name || checks.some(function (c) { return !c.ok; });
  }

  /** 시작 조건 체크리스트. 기준값은 config(state)에서, 현재값은 telemetry 에서 온다. */
  function startChecks(s, t) {
    var out = [];
    var base = (s.vacuum || {}).start_base_torr;
    var p = (t.gauges || {}).baratron;
    out.push({ label: '베이스 압력 ' + fmt.torr(p) + ' Torr (기준 ' + fmt.torr(base) + ')', ok: p != null && p <= base });
    // 히터 안정 판정: 통과한 채널은 한 줄로 묶고, 미달한 채널만 개별로 보여 준다.
    // ★ 채널이 10개인 챔버에서 전부 나열하면 목록이 패널을 넘겨 '공정 시작' 버튼이 밀린다.
    //   운전자가 봐야 하는 것은 "무엇이 아직 안 됐는가"다.
    var okNames = [];
    (s.heaters || []).forEach(function (h) {
      if (!h.enabled || !h.stable_band) return;
      var v = (t.heaters || {})[h.id] || {};
      if (v.sv == null || v.pv == null) return;
      var stable = Math.abs(v.pv - v.sv) <= h.stable_band;
      var label = h.label + ' ' + fmt.temp(v.sv) + ' °C 안정 (±' + fmt.temp(h.stable_band) +
        ' °C, ' + (h.stable_sec || 60) + ' s)';
      if (stable) okNames.push(h.label);
      else out.push({ label: label, ok: false });
    });
    if (okNames.length) {
      out.push({ label: '히터 안정 판정 통과 — ' + okNames.length + '개 채널', ok: true });
    }
    var iv = t.interlocks || {};
    var names = (s.interlocks || []).filter(function (k) { return k.id !== 'plc_hb'; });
    out.push({
      label: '인터락 정상 (' + names.map(function (k) { return k.label; }).join(' · ') + ')',
      ok: names.every(function (k) { return iv[k.id]; })
    });
    var plc = t.plc || {};
    out.push({ label: 'PLC 연결 · 하트비트 정상', ok: !!plc.connected && !!plc.hb_ok });
    return out;
  }

  /* ---------- 잠금 ---------- */
  function updateLocks(s, t) {
    var running = core.isRunning(), local = core.canOperate();
    var lock = running || !local;
    ['pump', 'vent', 'all_close', 'heater_apply'].forEach(function (c) {
      var b = d.querySelector('.cmdbar [data-cmd="' + c + '"]');
      if (b) b.disabled = lock;
    });
    var ex = d.querySelector('.cmdbar [data-cmd="exit"]');
    if (ex) ex.disabled = !local;
    core.setText('lockMsg', !local ? '🔒 원격 접속은 보기 전용입니다'
      : running ? '🔒 공정 중에는 수동 조작이 잠깁니다' : '');
    core.setText('schemNote', !local ? '보기 전용' : running ? '공정 중 · 수동 조작 잠김'
      : '대기 · 밸브를 눌러 수동 조작');
  }

  /* ---------- 도우미 ---------- */
  function set(sel, v) { var e = d.querySelector(sel); if (e) e.textContent = v; }
  function bar(name, ratio) {
    var e = core.bind(name);
    if (e) e.style.width = Math.max(0, Math.min(1, ratio || 0)) * 100 + '%';
  }

  /* ---------- 명령 ---------- */
  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cmd]');
    if (!b || b.disabled) return;
    var c = b.dataset.cmd;
    if (c === 'pump' || c === 'vent' || c === 'all_close') w.app.send(c);
    else if (c === 'heater_apply') {
      var sv = {};
      for (var k in heaterInputs) {
        var v = parseFloat(heaterInputs[k].value);
        if (isFinite(v)) sv[k] = v;
      }
      w.app.send('heater_apply', { sv: sv });
    } else if (c === 'process_start') {
      var sel = core.bind('readyRecipe');
      w.app.send('process_start', { recipe: sel ? sel.value : '' });
    } else if (c === 'process_pause' || c === 'process_abort' || c === 'process_stop_after_cycle') {
      w.app.send(c);
    } else if (c === 'goto_recipe') core.setTab('recipe');
  });

  d.addEventListener('change', function (ev) {
    if (ev.target.matches('[data-bind="readyRecipe"]') && core.state) updateReady(core.state, core.state.live || {});
  });

  core.register('main', { render: render, update: update });
  w.viewMain = { render: render, update: update };
})(window, document);
