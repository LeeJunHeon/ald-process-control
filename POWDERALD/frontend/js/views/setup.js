/* ============================================================
 * views/setup.js — 설정 탭 (관리자 PIN 으로 잠금 해제한 뒤 편집).
 *
 * ★ 판정·검증·저장은 전부 서버가 한다. 화면은 '경로 → 값' 편집만 모아 보내고,
 *   서버가 돌려준 '바뀌는 항목 표'를 확인 창에 보여 준다.
 * ★ 잠겨 있으면 모든 입력을 막는다. 잠금 해제는 이 PC 에서만 되고, 10 분 동안 조작이
 *   없거나 공정이 시작되거나 [잠금]을 누르면 다시 잠긴다(서버가 판단한다).
 * ★ 편집 중에 스냅샷이 다시 와도 고친 값을 잃지 않는다(edits 에 따로 둔다).
 * ★ 상태를 받을 때마다 통째로 다시 그리지 않는다 — 설정 · 칸 정의 · 장비 · 권한이 바뀔 때만 다시 만들고
 *   (그때도 편집 중이던 칸의 포커스 · 커서를 되돌린다), 나머지는 값만 고친다(update).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var adm = { has_pin: false, unlocked: false, token: '', left_s: 0, blocked_s: 0 };
  var admAt = Date.now();
  var edits = {};           // path → 화면 값
  var pinMode = '';
  var lastPreview = null;
  var builtSig = '';

  /* ===================== 값 도우미 ===================== */
  function getVal(cfg, path) {
    var p = path.split('.');
    if (p[0] === 'mfc' || p[0] === 'heaters') {
      var key = p[0] === 'mfc' ? 'no' : 'ch';
      var it = (cfg[p[0]] || []).filter(function (x) { return Number(x[key]) === Number(p[1]); })[0];
      return it ? it[p[2]] : null;
    }
    var cur = cfg;
    for (var i = 0; i < p.length; i++) {
      if (cur == null || typeof cur !== 'object') return null;
      cur = cur[p[i]];
    }
    return cur;
  }

  function fieldMap() {
    var m = {};
    ((core.state || {}).config_fields || []).forEach(function (f) { m[f.path] = f; });
    return m;
  }

  function locked() { return !(adm.unlocked && core.canOperate()); }

  /* ===================== 그리기 ===================== */
  function render(s) {
    var L = core.bind('setupLeft'), R = core.bind('setupRight');
    if (!L || !R) return;
    var sig = JSON.stringify([s.config, s.config_fields, s.device, s.unconfirmed, (s.access || {}).local]);
    if (sig === builtSig && L.firstChild) { update(s.live || {}); return; }
    builtSig = sig;
    // 다시 만들기 전에 편집 중이던 칸(포커스 · 커서)을 기억했다가 되돌린다
    var fa = d.activeElement && d.activeElement.closest && d.activeElement.closest('[data-cfg]');
    var fpath = fa ? fa.dataset.cfg : null;
    var fsel = fa && fa.selectionStart != null ? [fa.selectionStart, fa.selectionEnd] : null;
    build(s, L, R);
    if (fpath) {
      var back = d.querySelector('[data-cfg="' + fpath + '"]');
      if (back) {
        back.focus();
        if (fsel && back.setSelectionRange) { try { back.setSelectionRange(fsel[0], fsel[1]); } catch (e) { /* 숫자 칸 */ } }
      }
    }
  }

  function build(s, L, R) {
    var c = s.config || {};
    var dev = s.device || {};
    var F = fieldMap();
    var unconf = s.unconfirmed || [];

    function inp(path, extra) {
      var f = F[path];
      if (!f) return '';
      var v = path in edits ? edits[path] : getVal(c, path);
      var chg = path in edits ? ' chg' : '';
      if (f.kind === 'bool') {
        return '<input type="checkbox" data-cfg="' + path + '"' + (v ? ' checked' : '') +
          ' class="' + chg + '">';
      }
      if (path.slice(-5) === '.mode' || path === 'window.theme') {
        // v0.5.1(A1): 테마는 light(기본) · dark 중 고른다
        var opts = path === 'window.theme' ? ['light', 'dark'] : ['loglinear', 'linear', 'table'];
        if (path === 'window.theme' && !v) v = 'light';
        return '<select class="inp' + chg + '" data-cfg="' + path + '">' +
          opts.map(function (o) {
            return '<option' + (v === o ? ' selected' : '') + '>' + o + '</option>';
          }).join('') + '</select>';
      }
      return '<input class="inp' + chg + (f.kind === 'str' ? '' : ' num') + '" data-cfg="' + path + '" value="' +
        core.esc(v == null ? '' : v) + '"' + (f.nullable ? ' placeholder="None"' : '') + (extra || '') + '>';
    }

    function row(path, unit) {
      var f = F[path];
      if (!f) return '';
      return '<tr><td class="l">' + core.esc(f.label) + (f.restart ? ' <span class="chip" title="바꾸면 프로그램을 다시 시작해야 반영됩니다">Restart</span>' : '') +
        '</td><td>' + inp(path) + (unit ? '<span class="unit">' + unit + '</span>' : '') + '</td></tr>';
    }

    function tbl(rows) { return '<table class="tbl form">' + rows + '</table>'; }

    var adminBody =
      '<div class="adm-row"><span data-bind="admChip"></span><span class="right">' +
      '<button class="btn sm primary" data-adm="unlock" data-bind="admUnlock">' + T('b.unlock') + '</button>' +
      '<button class="btn sm" data-adm="lock" data-bind="admLock">' + T('b.lock') + '</button>' +
      '<button class="btn sm" data-adm="change" data-bind="admChange">' + T('b.changePin') + '</button></span></div>' +
      '<div class="hint" style="margin-top:6px">설정 편집에만 PIN 이 필요합니다(레시피·수동 조작은 그대로). ' +
      '10 분 동안 조작이 없거나 공정이 시작되면 다시 잠깁니다. PIN 을 잊었으면 프로그램을 끄고 ' +
      'data/admin_pin.json 을 지운 뒤 다시 켜서 새로 정합니다.</div>';

    var plc = c.plc || {};
    L.innerHTML =
      panel('Admin', '', adminBody) +
      panel('Config File', srcChip(c.source),
        '<div style="font-weight:700;font-size:var(--fs-xl)">' + core.esc(c.path_name || fmt.DASH) + '</div>' +
        '<div class="hint" style="margin-top:4px">' +
        (c.source === 'example'
          ? '⚠ 예시 설정으로 실행 중입니다. 처음 저장할 때 exe 옆에 config.json 을 만듭니다.'
          : '저장할 때마다 이전 파일을 data/config_backup 에 백업합니다(최근 20개).') + '</div>' +
        '<div style="margin-top:6px"><button class="btn sm" data-cfgact="backup"' +
        (core.canOperate() ? '' : ' disabled title="이 PC 에서만 됩니다"') + '>' + T('b.backupFolder') + '</button></div>') +
      panel('PLC Connection', '<span class="chip" title="바꾸면 프로그램을 다시 시작해야 반영됩니다">Restart Required</span>',
        tbl(row('plc.simulate') + row('plc.host') + row('plc.port') + row('plc.unit_id') +
            row('plc.timeout_ms', 'ms') + row('plc.poll_ms', 'ms') + row('plc.heartbeat_ms', 'ms') +
            '<tr><td class="l">Device ID (D00019)</td><td class="mono" data-bind="idCell">—</td></tr>' +
            '<tr><td class="l">Remote View Only (access.local_only)</td><td class="mono">' +
            ((c.access || {}).local_only === false ? 'false' : 'true') + ' <span class="dim">읽기 전용</span></td></tr>') +
        (plc.simulate ? '<div class="hint warn" style="margin-top:6px">시뮬레이터 모드 — 실장비로 바꾸면 저장 전에 한 번 더 확인합니다.</div>' : '')) +
      panel('Analog / Conversion', unconf.length ? '<span class="chip warn">' + T('s.unconfirmed') + ' ' + unconf.length + '</span>' : core.chip('All Confirmed', 'ok'),
        tbl(row('analog.raw_max') + row('analog.confirmed') +
            '<tr><td class="l sub" colspan="2">CVG</td></tr>' +
            row('pressure.cvg.mode') + row('pressure.cvg.volt_max', 'V') + row('pressure.cvg.torr_at_0v', 'Torr') +
            row('pressure.cvg.decades_per_volt') + row('pressure.cvg.torr_per_volt') + row('pressure.cvg.confirmed') +
            '<tr><td class="l sub" colspan="2">Capacitance Gauge (CM)</td></tr>' +
            row('pressure.cm.installed') + row('pressure.cm.mode') + row('pressure.cm.volt_max', 'V') +
            row('pressure.cm.torr_at_0v', 'Torr') + row('pressure.cm.torr_per_volt') + row('pressure.cm.confirmed') +
            (dev.has_rf ? '<tr><td class="l sub" colspan="2">RF / PCV</td></tr>' + row('rf.max_w', 'W') + row('rf.confirmed') +
              row('pcv.full_pct', '%') + row('pcv.confirmed') : '') +
            (dev.has_o3 ? '<tr><td class="l sub" colspan="2">O3</td></tr>' + row('o3.full') + row('o3.unit') + row('o3.confirmed') : '')) +
        (unconf.length ? '<div class="hint warn" style="margin-top:6px">환산 미확정: ' + core.esc(unconf.join(' · ')) + '</div>' : '')) +
      panel('Process', '',
        tbl(row('process.base_wait_timeout_s', 's') + row('process.base_stable_s', 's') +
            row('process.heater_ready.enabled') + row('process.heater_ready.band_c', '℃') +
            row('process.heater_ready.stable_s', 's') + (dev.has_o3 ? row('process.o3_off_delay_s', 's') : '') +
            // v0.7.0(A5): PC 감시 — 프로그램이 꺼져 있으면 동작하지 않는다
            '<tr><td class="l sub" colspan="2">PC Watch in Process (프로그램이 꺼져 있으면 동작하지 않음)</td></tr>' +
            row('process.press_high_torr', 'Torr') + row('process.press_high_s', 's') + row('process.press_high_abort') +
            row('process.heater_dev_c', '℃') + row('process.heater_dev_s', 's'))) +
      maintPanel(row, tbl) +
      panel('Display', '', tbl(row('window.theme'))) +
      panel('Log', '',
        tbl(row('log.keep_days', 'd') + row('log.datalog_interval_s', 's') +
            row('log.datalog_keep_days', 'd') + row('log.trend_keep_days', 'd')) +
        '<div class="hint" style="margin-top:6px">폴더: data/logs · data/alarms · data/datalog · data/trend · data/export</div>') +
      panel('Diagnostics', '',
        '<div class="kv"><span class="k">PLC RTT</span><span class="v" data-bind="dgRtt">—</span></div>' +
        '<div class="kv"><span class="k">PLC Max Scan</span><span class="v" data-bind="dgScan">—</span></div>' +
        '<div class="kv"><span class="k">PLC Heartbeat</span><span class="v" data-bind="dgHb">—</span></div>' +
        '<div class="kv"><span class="k">PC Heartbeat Gap (max)</span><span class="v" data-bind="dgHbGap">—</span></div>' +
        '<div class="kv"><span class="k">Event Loop Lag (10 min max)</span><span class="v" data-bind="dgLag">—</span></div>' +
        '<div class="hint">500 ms 를 넘으면 PC 하트비트가 늦습니다 — 로그에 그때 하던 일이 남습니다.</div>' +
        '<div class="kv"><span class="k">Web Server</span><span class="v">' +
        core.esc((c.server || {}).host + ':' + (c.server || {}).port) + '</span></div>');

    var prmRows = Object.keys(F).filter(function (p) { return p.indexOf('params.') === 0; })
      .map(function (p) { return row(p); }).join('');

    var mfcRows = (c.mfc || []).map(function (m) {
      var b = 'mfc.' + m.no + '.';
      return '<tr><td>MFC' + m.no + '</td><td>' + inp(b + 'name') + '</td><td>' + inp(b + 'gas', ' style="width:60px"') +
        '</td><td>' + inp(b + 'full_scale_sccm') + '</td><td>' + inp(b + 'confirmed') + '</td></tr>';
    }).join('');

    var htRows = (c.heaters || []).map(function (h) {
      var b = 'heaters.' + h.ch + '.';
      return '<tr><td>CH' + h.ch + '</td><td>' + inp(b + 'name') + '</td><td>' + inp(b + 'enabled') +
        '</td><td>' + inp(b + 'max_c') + '</td><td>' + inp(b + 'default_sv') + '</td><td>' +
        inp(b + 'station', ' style="width:44px"') + '</td></tr>';
    }).join('');

    R.innerHTML =
      panel('PLC Parameters', '<span data-bind="prmChip"></span>',
        tbl(prmRows) +
        '<div class="hint" style="margin-top:6px">저장하면 원시값으로 바꿔 PLC 에 다시 쓰고 되읽어 확인합니다. ' +
        '0 이면 PLC 가 해당 기능을 막거나 감시하지 않습니다.</div>' +
        '<div class="tblx" style="margin-top:8px"><table class="tbl prmtbl"><thead><tr><th class="l">PRM / Address</th><th>' + T('v.setpoint') + '</th><th>' + T('v.written') + '</th>' +
        '<th>' + T('v.readback') + '</th><th>' + T('v.match') + '</th></tr></thead><tbody data-bind="prmBody">' +
        '<tr><td class="l dim" colspan="5">PLC 에 연결되면 표시합니다</td></tr></tbody></table></div>') +
      panel('MFC', '<span class="dim" title="개수는 장비 고정">Fixed by Device</span>',
        '<div class="tblx"><table class="tbl form"><thead><tr><th class="l">' + T('v.number') + '</th><th>' + T('v.name') + '</th><th>' + T('v.gas') +
          '</th><th>' + T('v.fullScale') + ' sccm</th><th>' + T('v.confirmed') + '</th></tr></thead>' +
        '<tbody>' + mfcRows + '</tbody></table></div>') +
      panel('Heater', '',
        '<div class="tblx"><table class="tbl form"><thead><tr><th class="l">' + T('v.channel') + '</th><th>' + T('v.name') + '</th><th>' + T('v.enabled') +
        '</th><th>' + T('v.limit') + '</th><th>' + T('v.defaultSv') + '</th><th>' + T('v.station') + '</th></tr></thead><tbody>' + htRows + '</tbody></table></div>' +
        '<div class="hint" style="margin-top:6px">과온 한계가 비어 있으면 PLC 소프트 과온 감시가 꺼지므로 그 채널의 설정·전원 켜기를 PC 가 막습니다.</div>') +
      '<div class="cfgbar"><span data-bind="cfgCount"></span><span class="right">' +
      '<button class="btn sm" data-cfgact="revert" data-bind="cfgRevert">' + T('b.revert') + '</button>' +
      '<button class="btn sm primary" data-cfgact="review" data-bind="cfgReview">' + T('b.review') + '</button></span></div>';

    paintAdmin();
    update(s.live || {});
  }

  /* ===================== v0.7.0(A3 · A4) Maintenance ===================== */
  function maintPanel(row, tbl) {
    var op = core.canOperate();
    return panel('Maintenance', '<span data-bind="lcChip"></span>',
      '<div class="sub-h">Leak Check (압력 상승률)</div>' +
      '<div class="adm-row"><span class="dim" data-bind="lcProg">—</span><span class="right">' +
      '<button class="btn sm primary" data-lc="start"' + (op ? '' : ' disabled title="이 PC 에서만 됩니다"') + '>Leak Check</button>' +
      '<button class="btn sm" data-lc="cancel"' + (op ? '' : ' disabled') + '>' + T('b.cancel') + '</button></span></div>' +
      '<div class="hint">명령 11 로 IV-E 를 닫고(펌프는 그대로) 안정 뒤 압력 상승률을 잽니다. 끝나면 명령 8 로 IV-E 를 다시 엽니다. ' +
      '점검 중 프로그램을 끄면 IV-E 는 닫힌 채 남습니다 — [Pumping Start] 로 여세요.</div>' +
      tbl(row('leakcheck.settle_s', 's') + row('leakcheck.measure_s', 's') + row('leakcheck.limit_mtorr_min', 'mTorr/min') +
          row('leakcheck.max_torr', 'Torr')) +
      '<div class="tblx"><table class="tbl lctbl"><thead><tr><th class="l">Time</th><th>Gauge</th><th>mTorr/min</th>' +
      '<th>Result</th></tr></thead><tbody data-bind="lcBody"><tr><td class="l dim" colspan="4">기록 없음</td></tr></tbody></table></div>' +
      '<div class="sub-h" style="margin-top:8px">Counters</div>' +
      '<div class="tblx"><table class="tbl mttbl"><thead><tr><th class="l">Item</th><th>Value</th><th>Limit</th>' +
      '<th>Reset At</th><th></th></tr></thead><tbody data-bind="mtBody"></tbody></table></div>' +
      '<div class="hint">밸브 열림은 공정 끝 판정 때(정상 종료면 레시피 전체, 아니면 멈춘 위치까지)와 수동 열기에서 셉니다. ' +
      '켜진 시간은 PLC 링크가 붙어 있는 동안(멈춤 제외)만 셉니다 — 프로그램이 꺼져 있던 동안은 모릅니다. ' +
      '[Reset] 은 관리자 잠금 해제가 필요합니다.</div>' +
      tbl(row('maintenance.valve_opens_warn') + row('maintenance.pump_hours_warn', 'h') +
          row('maintenance.rf_hours_warn', 'h') + row('maintenance.o3_hours_warn', 'h')));
  }

  var LC_PHASE = { closing: 'IV-E 닫는 중', settle: '안정 대기', measure: '측정 중', restore: 'IV-E 다시 여는 중' };

  function updateMaint(t) {
    var lc = t.leakcheck || {};
    var pg = lc.progress || {};
    core.setText('lcProg', lc.running
      ? (LC_PHASE[lc.phase] || lc.phase) + ' · ' + Math.round(lc.elapsed_s || 0) + ' s' +
        (pg.pressure != null ? ' · ' + (pg.gauge || '') + ' ' + fmt.torr(pg.pressure) + ' Torr' : '') +
        (pg.slope != null ? ' · ' + fmt.num(pg.slope, 2) + ' mTorr/min' : '') +
        ' (측정 ' + (lc.measure_s || 0) + ' s · 한계 ' + (lc.limit || 0) + ')'
      : '대기 — 측정 ' + (lc.measure_s || 0) + ' s · 한계 ' + (lc.limit || 0) + ' mTorr/min');
    var st = d.querySelector('[data-lc="start"]'), cn = d.querySelector('[data-lc="cancel"]');
    if (st) {
      // v0.7.1(L7): 시작할 수 없는 상태(연결 없음 · 멈춤 · 공정 중 · 시작 절차 중)면 흐리게 두고 이유를 툴팁으로
      var why = !core.canOperate() ? '이 PC 에서만 됩니다' : lc.running ? '누설률 점검 중' : (lc.blocked || '');
      st.disabled = !!why;
      if (st.title !== why) st.title = why;
    }
    if (cn) cn.disabled = !core.canOperate() || !lc.running;
    var lb = core.bind('lcBody');
    var rec = lc.recent || [];
    if (lb) {
      core.html(lb, rec.length ? rec.map(function (r) {
        var res = r.Result || '';
        return '<tr' + (r.Reason ? ' title="' + core.esc(r.Reason) + '"' : '') + '><td class="l mono">' + core.esc(r.Time || '') +
          '</td><td>' + core.esc(r.Gauge || '') + '</td><td class="mono">' + core.esc(r['mTorr/min'] || fmt.DASH) +
          '</td><td>' + core.chip(res, res === 'PASS' ? 'ok' : res === 'FAIL' ? 'stop' : 'warn') + '</td></tr>';
      }).join('') : '<tr><td class="l dim" colspan="4">기록 없음</td></tr>');
    }
    var mb = core.bind('mtBody');
    if (mb) {
      core.html(mb, (t.maintenance || []).map(function (r) {
        return '<tr' + (r.over ? ' class="hl"' : '') + '><td class="l">' + core.esc(r.label) +
          (r.extra ? '<div class="dim small">' + core.esc(r.extra) + '</div>' : '') + '</td>' +
          '<td class="mono">' + core.esc(r.value) + (r.unit ? ' ' + core.esc(r.unit) : '') + '</td>' +
          '<td class="mono">' + (r.limit ? core.esc(r.limit) : 'Off') + '</td>' +
          '<td class="mono">' + core.esc(r.reset_at || fmt.DASH) + '</td>' +
          '<td><button class="btn sm" data-mtreset="' + core.esc(r.key) + '" data-mtlabel="' + core.esc(r.label) + '"' + (locked() ? ' disabled title="관리자 잠금 해제 필요"' : '') +
          '>Reset</button></td></tr>';
      }).join(''));
    }
    var ch = core.bind('lcChip');
    if (ch) core.html(ch, lc.running ? core.chip('Leak Check', 'warn') : '');
  }

  d.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-lc],[data-mtreset]');
    if (!b || b.disabled || !core.canOperate()) return;
    if (b.dataset.lc === 'start') {
      var lc = ((core.state || {}).live || {}).leakcheck || {};
      core.confirmAsk('누설률 점검을 시작할까요?',
        'IV-E 를 닫고(명령 11 — 펌프는 그대로) ' + core.esc(lc.settle_s) + ' s 안정 뒤 ' + core.esc(lc.measure_s) +
        ' s 동안 압력 상승률을 잽니다.<br>끝나면 명령 8(펌핑 시작)로 IV-E 를 다시 엽니다.<br>' +
        '점검 중에는 공정 시작 · 수동 밸브를 막습니다. 펌핑 · 벤트 · 전체 닫기를 누르면 점검을 먼저 취소합니다.',
        'Leak Check', function () { w.app.send('leak_check'); });
    } else if (b.dataset.lc === 'cancel') {
      w.app.send('leak_cancel');
    } else if (b.dataset.mtreset) {
      var key = b.dataset.mtreset;
      // v0.7.1(U6): 키(hours:pump) 대신 항목 이름
      core.confirmAsk('정비 카운터를 리셋할까요?', '<b>' + core.esc(b.dataset.mtlabel || key) +
        '</b> 를 0 으로 만듭니다. 이전 값은 이벤트 로그에 남습니다.',
        'Reset', function () { w.app.send('maint_reset', { token: adm.token, key: key }); });
    }
  });

  function update(t) {
    updateMaint(t);
    var conn = !!(t.plc && t.plc.connected);
    // ★ PLC 하트비트 멈춤 — PLC 값은 모르지만 통신은 된다. PC 쪽 값(응답 · PC 하트비트 간격)은 보인다
    var link = conn || !!(t.plc && t.plc.stalled);
    core.setText('dgRtt', link && t.plc.rtt_ms != null ? t.plc.rtt_ms + ' ms' : fmt.DASH);
    core.setText('dgScan', conn && t.scan_max_ms != null ? core.scanText(t.scan_max_ms) : fmt.DASH);
    var sc = core.bind('dgScan');
    var stip = conn && t.scan_max_ms === 0 ? 'PLC 가 아직 쓰지 않음(D00080 = 0)' : '';
    if (sc && sc.title !== stip) sc.title = stip;
    core.setText('dgHb', (t.plc || {}).stalled ? 'Stalled' : !conn ? fmt.DASH : (t.plc.hb_ok ? T('s.normal') : 'Stalled'));
    core.setText('dgHbGap', link && t.plc.hb_gap_max_ms != null
      ? t.plc.hb_gap_ms + ' ms (최대 ' + t.plc.hb_gap_max_ms + ' ms)' : fmt.DASH);
    var lg = t.loop || {};
    core.setText('dgLag', lg.recent_max_ms == null ? fmt.DASH
      : lg.recent_max_ms + ' ms' + (lg.recent_max_work ? ' (' + lg.recent_max_work + ')' : '') +
        ' · 기동 뒤 최대 ' + (lg.max_ms || 0) + ' ms');
    var ic = core.bind('idCell');
    if (ic) {
      var p = t.plc || {};
      core.html(ic, p.config_error ? core.chip('No Address', 'stop', '주소 없음 — 연결 안 함 · ' + p.config_error)
        : !conn ? fmt.DASH
          : p.id_state === 'ok' ? core.chip(core.idText(p.device_id) + ' ' + T('s.match'), 'ok')
            : p.id_state === 'unset' ? core.chip('0 — ID Unset', 'warn', 'PLC 에 ID 가 아직 없음 — 동작은 막지 않습니다')
              : p.id_state === 'wrong' ? core.chip(core.idText(p.device_id) + ' Wrong Device', 'stop', '다른 장비의 PLC')
                : p.id_state === 'missing' ? core.chip('0 — ID Required', 'stop', '장비 ID 필수(plc.require_device_id) 가 켜져 있습니다')
                : fmt.DASH);
    }
    var body = core.bind('prmBody');
    var rows = (t.plc || {}).prm || [];
    if (body && rows.length) {
      core.html(body, rows.map(function (r) {
        var ev = r.eng == null ? fmt.DASH
          : r.unit === 'Torr' ? fmt.torr(r.eng)
            : (Number.isInteger(r.eng) ? String(r.eng) : fmt.num(r.eng, 1));
        return '<tr' + (r.match === false ? ' class="hl"' : '') + '><td class="l">' + core.esc(r.name) +
          '<div class="mono dim small">' + core.esc(r.addr) + '</div></td>' +
          '<td class="mono">' + (r.setting == null ? '<span class="unconf">None</span>' : core.esc(r.setting)) + '</td>' +
          '<td class="mono">' + core.esc(r.written) + '</td>' +
          '<td class="mono">' + (r.readback == null ? fmt.DASH : core.esc(r.readback)) +
          '<div class="dim small">' + core.esc(ev) + (r.unit ? ' ' + core.esc(r.unit) : '') + '</div></td>' +
          // 멈춤 · 끊김이면 되읽기를 모른다 — 일치 칸도 '—'
          '<td>' + (r.match == null ? fmt.DASH : core.chip(r.match ? T('s.match') : '✕ ' + T('s.mismatch'), r.match ? 'ok' : 'stop')) +
          '</td></tr>';
      }).join(''));
    } else if (body && !conn) {
      core.html(body, '<tr><td class="l dim" colspan="5">' + core.downText() + ' — No Data</td></tr>');
    }
    var pc = core.bind('prmChip');
    if (pc) {
      var mm = (t.plc || {}).prm_mismatch || [];
      core.html(pc, !conn ? '' : core.chip(mm.length ? '✕ Readback ' + T('s.mismatch') + ' ' + mm.length : 'Readback ' + T('s.match'),
                                           mm.length ? 'stop' : 'ok'));
    }
    paintAdmin();
  }

  function paintAdmin() {
    var left = Math.max(0, adm.left_s - Math.floor((Date.now() - admAt) / 1000));
    var blocked = Math.max(0, adm.blocked_s - Math.floor((Date.now() - admAt) / 1000));
    var chip = core.bind('admChip');
    var local = core.canOperate();
    if (chip) {
      core.html(chip, !local ? core.chip('Read Only', 'off', core.lockReason() + ' — 편집 안 됨')
        : blocked ? core.chip('Input Blocked ' + blocked + ' s', 'stop')
          : adm.unlocked && left > 0 ? core.chip(T('s.unlocked') + ' · ' + Math.ceil(left / 60) + ' min', 'ok')
            : (adm.has_pin ? core.chip(T('s.locked'), 'warn') : core.chip('No PIN', 'warn', 'PIN 없음 — 처음 편집할 때 정합니다')));
    }
    var bk = d.querySelector('[data-cfgact="backup"]');
    if (bk) bk.disabled = !local;
    if (adm.unlocked && left <= 0) adm.unlocked = false;
    var u = core.bind('admUnlock');
    if (u) {
      u.textContent = adm.has_pin ? T('b.unlock') : T('b.setPin');
      u.hidden = adm.unlocked;
      u.disabled = !local;
    }
    var l = core.bind('admLock');
    if (l) l.hidden = !adm.unlocked;
    var ch = core.bind('admChange');
    if (ch) ch.disabled = !local || !adm.has_pin;
    var lock = locked();
    Array.prototype.forEach.call(d.querySelectorAll('[data-cfg]'), function (e) { e.disabled = lock; });
    var n = Object.keys(edits).length;
    core.setText('cfgCount', lock ? '🔒 잠겨 있어 편집할 수 없습니다'
      : (n ? '바꾼 항목 ' + n + '개 — 저장 검토를 누르세요' : 'No Changes'));
    var rv = core.bind('cfgReview'); if (rv) rv.disabled = lock || !n;
    var rb = core.bind('cfgRevert'); if (rb) rb.disabled = !n;
  }

  setInterval(function () { if (core.tab === 'setup') paintAdmin(); }, 1000);

  /* ===================== 편집 ===================== */
  function onEdit(e) {
    var path = e.dataset.cfg;
    var f = fieldMap()[path];
    if (!f) return;
    var v = f.kind === 'bool' ? e.checked : e.value;
    var orig = getVal((core.state || {}).config || {}, path);
    var same = f.kind === 'bool' ? (!!orig === v)
      : (String(orig == null ? '' : orig) === String(v).trim());
    if (same) delete edits[path]; else edits[path] = v;
    e.classList.toggle('chg', !same);
    paintAdmin();
  }

  d.addEventListener('input', function (ev) {
    var e = ev.target.closest('[data-cfg]');
    if (e && e.type !== 'checkbox') onEdit(e);
  });
  d.addEventListener('change', function (ev) {
    var e = ev.target.closest('[data-cfg]');
    if (e) onEdit(e);
  });

  /* ===================== 관리자 PIN 창 ===================== */
  function openPin(mode) {
    pinMode = mode;
    var title = { setup: '관리자 PIN 정하기', unlock: '관리자 잠금 해제', change: 'PIN 바꾸기' }[mode];
    var rows = { setup: [['pin', '새 PIN (숫자 4~8자리)'], ['pin2', '한 번 더']],
                 unlock: [['pin', 'PIN']],
                 change: [['old', '현재 PIN'], ['new', '새 PIN (숫자 4~8자리)'], ['new2', '한 번 더']] }[mode];
    core.setText('pnTitle', title);
    core.setText('pnDev', ((core.state || {}).device || {}).name || '');
    core.setText('pnHint', mode === 'setup' ? 'PIN 이 아직 없습니다. 설정 편집에 쓸 PIN 을 정하세요. PIN 은 해시로만 저장됩니다.'
      : '이 PC 에서만 됩니다. 5 번 틀리면 5 분 동안 입력이 막힙니다.');
    core.setText('pnMsg', '');
    var box = core.bind('pnRows');
    box.innerHTML = rows.map(function (r) {
      return '<label class="pinrow">' + r[1] + '<input type="password" inputmode="numeric" maxlength="8" ' +
        'autocomplete="off" class="inp" data-pin="' + r[0] + '"></label>';
    }).join('');
    core.showModal('pinModal', '[data-pin]');
  }

  function pinSubmit() {
    var o = {};
    Array.prototype.forEach.call(d.querySelectorAll('[data-pin]'), function (e) { o[e.dataset.pin] = e.value; });
    var cmd = { setup: 'admin_setup', unlock: 'admin_unlock', change: 'admin_change' }[pinMode];
    if (cmd) w.app.send(cmd, o);
    // ★ 입력한 PIN 을 화면에 남기지 않는다
    Array.prototype.forEach.call(d.querySelectorAll('[data-pin]'), function (e) { e.value = ''; });
  }

  d.addEventListener('click', function (ev) {
    var a = ev.target.closest('[data-adm]');
    if (a && !a.disabled) {
      if (a.dataset.adm === 'unlock') openPin(adm.has_pin ? 'unlock' : 'setup');
      else if (a.dataset.adm === 'change') openPin('change');
      else if (a.dataset.adm === 'lock') w.app.send('admin_lock');
      return;
    }
    var p = ev.target.closest('[data-pn]');
    if (p) {
      if (p.dataset.pn === 'ok') pinSubmit();
      else core.hideModal('pinModal');
      return;
    }
    var c = ev.target.closest('[data-cfgact]');
    if (c && !c.disabled) {
      var act = c.dataset.cfgact;
      if (act === 'revert') { edits = {}; builtSig = ''; render(core.state); }
      else if (act === 'review') w.app.send('config_preview', { token: adm.token, edits: edits });
      else if (act === 'backup' && core.canOperate()) w.app.send('open_folder', { which: 'backup' });
      return;
    }
    var g = ev.target.closest('[data-cg]');
    if (g) {
      core.hideModal('cfgModal');
      if (g.dataset.cg === 'ok' && lastPreview && lastPreview.ok && !lastPreview.blocked) {
        w.app.send('config_save', { token: adm.token, edits: edits });
      }
    }
  });

  d.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && ev.target.closest('[data-pin]')) pinSubmit();
  });

  /* ===================== 서버 응답 ===================== */
  w.app.on('admin', function (m) {
    adm = m; admAt = Date.now();
    if (m.unlocked) core.hideModal('pinModal');
    paintAdmin();
  });

  w.app.on('config_preview', function (m) {
    lastPreview = m;
    core.setText('cgDev', ((core.state || {}).device || {}).name || '');
    var h = '';
    if (m.blocked) h += '<div class="hint warn">' + core.esc(m.blocked) + '</div>';
    if ((m.errors || []).length) {
      h += '<div class="cg-sec stop">검증 오류 — 저장할 수 없습니다</div><ul>' +
        m.errors.map(function (e) { return '<li>' + core.esc(e) + '</li>'; }).join('') + '</ul>';
    }
    if ((m.warnings || []).length) {
      h += '<div class="cg-sec warn">경고</div><ul>' +
        m.warnings.map(function (e) { return '<li>' + core.esc(e) + '</li>'; }).join('') + '</ul>';
    }
    h += '<div class="cg-sec">바뀌는 항목 ' + (m.diff || []).length + '개' +
      (m.first_file ? ' — ' + core.esc(m.path_name) + ' 을(를) 새로 만듭니다' : '') + '</div>' +
      '<table class="tbl"><thead><tr><th class="l">' + T('v.item') + '</th><th>' + T('v.before') + '</th><th>' + T('v.after') +
      '</th><th>' + T('v.raw') + '</th></tr></thead><tbody>' +
      (m.diff || []).map(function (r) {
        return '<tr><td class="l">' + core.esc(r.label) + (r.restart ? ' ' + core.chip('Restart', '', '바꾸면 프로그램을 다시 시작해야 반영됩니다') : '') + '</td>' +
          '<td class="mono">' + show(r.old) + '</td><td class="mono"><b>' + show(r.new) + '</b></td>' +
          '<td class="mono dim">' + (r.raw_new != null ? core.esc(r.prm + ' ' + r.raw_old + ' → ' + r.raw_new) : '') + '</td></tr>';
      }).join('') + '</tbody></table>';
    var extra = (m.prm || []).filter(function (p) {
      return !(m.diff || []).some(function (r) { return r.prm === p.addr; });
    });
    if (extra.length) {
      h += '<div class="cg-sec">환산이 바뀌어 함께 달라지는 PRM 원시값</div><ul>' + extra.map(function (p) {
        return '<li class="mono">' + core.esc(p.addr + ' ' + p.name + ': ' + p.old + ' → ' + p.new) + '</li>';
      }).join('') + '</ul>';
    }
    if (m.restart) h += '<div class="hint warn">PLC 연결 설정은 프로그램을 다시 시작해야 반영됩니다.</div>';
    if (m.host_changed) {
      h += '<div class="hint warn">PLC 주소가 바뀝니다 — 다시 시작하면 연결 때 장비 ID 를 확인합니다 ' +
        '(이 장비 ' + core.esc(m.device_id) + '). 다른 장비의 PLC 면 아무것도 쓰지 않습니다.</div>';
    }
    if (m.sim_to_real) {
      h += '<div class="cg-sec stop">⚠ 시뮬레이터 → 실장비 전환</div><div class="hint warn">다시 시작하면 ' +
        '실제 PLC 에 연결해 파라미터를 씁니다. 주소·포트가 이 장비의 PLC 인지 확인하세요.</div>';
    }
    var b = core.bind('cgBody');
    if (b) b.innerHTML = h;
    var ok = core.bind('cgOk');
    if (ok) {
      ok.disabled = !m.ok || !!m.blocked || !(m.diff || []).length;
      ok.textContent = m.sim_to_real ? '실장비로 전환하고 저장' : T('b.save');
    }
    core.showModal('cfgModal', '[data-cg="cancel"]');
  });

  w.app.on('config_saved', function () {
    edits = {};
    lastPreview = null;
  });

  function show(v) { return v == null ? '<span class="dim">None</span>' : core.esc(typeof v === 'object' ? JSON.stringify(v) : v); }

  /* ---------- 빌더 ---------- */
  function panel(title, note, body) {
    return '<div class="panel" style="flex:0 0 auto"><div class="panel-head">' + core.esc(title) +
      (note ? '<span class="note">' + note + '</span>' : '') + '</div>' +
      '<div class="panel-body">' + body + '</div></div>';
  }

  function srcChip(src) {
    return src === 'file' ? core.chip('Site Config', 'ok') : core.chip('Example Config', 'warn');
  }

  core.register('setup', { render: render, update: update });
  w.viewSetup = { render: render, update: update };
})(window, document);
