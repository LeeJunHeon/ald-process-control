/* ============================================================
 * views/setup.js — 설정 탭.
 *
 * 이번 단계에서는 값을 '보여 주기'까지다. 저장은 서버가 "다음 단계에서 구현" 알림만 낸다.
 * ★ 관리자 PIN 은 화면 표시만 한다. PIN·해시는 코드에도 저장소에도 두지 않는다 —
 *   나중에 저장할 때는 데이터 폴더(추적 제외)에 해시로만 둔다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  function render(s) {
    var L = core.bind('setupLeft'), R = core.bind('setupRight');
    if (!L || !R) return;
    var plc = s.plc_cfg || {}, vac = s.vacuum || {}, log = s.log_cfg || {},
        srv = s.server_cfg || {}, ch = s.chamber || {};

    L.innerHTML =
      panel('PLC 통신', chipNote('연결됨 · 응답 12 ms', 'ok') + btn('연결 시험'),
        '<div class="grid2">' +
        fld('PLC IP', plc.ip) + fld('포트', plc.port) +
        fld('Unit ID', plc.unit_id) + fld('폴링 주기', plc.poll_ms, 'ms') +
        fld('하트비트 주기', plc.heartbeat_s, 's') + fld('끊김 판정', plc.timeout_s, 's') +
        '</div>') +
      panel('진공 · 압력', '',
        '<div class="grid2">' +
        fld('시작 베이스 압력', fmt.torr(vac.start_base_torr), 'Torr') +
        fld('베이스 도달 제한', vac.base_timeout_s, 's') +
        fld('벤트 ATM 도달 제한', vac.vent_timeout_s, 's') +
        fld('공정 압력 편차 경고', '±' + vac.process_dev_pct, '%') +
        '</div>') +
      panel('로그', '',
        '<div class="grid2">' +
        fld('데이터 로그 폴더', 'data/' + (ch.id || '') + '/datalog') +
        fld('로그 레벨', log.level) +
        fld('기록 주기', log.datalog_interval_s, 's') +
        fld('보관 기간', log.datalog_keep_days, '일') +
        '</div>') +
      panel('조작 권한', '',
        '<label style="display:flex;gap:8px;align-items:center;margin-bottom:8px">' +
        '<input type="radio" name="acc" checked disabled> 이 PC에서만 조작 (원격 접속은 보기 전용)</label>' +
        '<label style="display:flex;gap:8px;align-items:center;color:var(--ink-faint)">' +
        '<input type="radio" name="acc" disabled> 관리자 PIN을 입력한 원격 사용자도 조작</label>' +
        '<div class="hint" style="margin-top:10px">서버 주소 http://' + core.esc(srv.host || '') + ':' + core.esc(srv.port || '') +
        ' — 조작 명령은 이 PC(127.0.0.1)에서만 받습니다.</div>' +
        '<div style="margin-top:10px">' + btn('관리자 PIN 변경') + '</div>') +
      panel('설정 파일', '',
        '<div style="font-weight:700;font-size:var(--fs-xl)">' + core.esc(ch.config_file || fmt.DASH) + '</div>' +
        '<div class="hint" style="margin-top:4px">라인 구성 · 밸브 태그 · 히터 채널 · 인터락을 담습니다.</div>');

    R.innerHTML =
      panel('라인 구성', '<span class="hint">' + core.esc(ch.config_file || '') + '</span>',
        '<table class="tbl"><thead><tr><th>라인</th><th>이름</th><th>공급 방식</th><th>MFC</th><th>사용</th></tr></thead><tbody>' +
        (s.lines || []).map(function (l) {
          var modes = (l.modes || []).map(function (m) {
            return ((s.modes || {})[m] || {}).label || m;
          }).join(' · ');
          return '<tr><td><b>' + core.esc(l.id) + '</b></td>' +
            '<td>' + core.esc(l.enabled ? (l.material || l.label) : fmt.DASH) + '</td>' +
            '<td>' + core.esc(l.enabled ? modes : fmt.DASH) + '</td>' +
            '<td class="mono">' + core.esc((l.mfc || {}).full_scale + ' ' + ((l.mfc || {}).gas || '')) + '</td>' +
            '<td>' + core.chip(l.enabled ? '사용' : '미장착', l.enabled ? 'ok' : 'neutral') + '</td></tr>';
        }).join('') + '</tbody></table>') +
      panel('히터 알람 · 안정 판정 (°C)', '',
        '<table class="tbl"><thead><tr><th>채널</th><th>사용</th><th>편차 경고</th><th>최대</th><th>안정 판정</th></tr></thead><tbody>' +
        (s.heaters || []).map(function (h) {
          return '<tr><td>' + core.esc(h.label) + '</td>' +
            '<td>' + (h.enabled ? '예' : '<span class="dim">아니오</span>') + '</td>' +
            '<td>' + (h.enabled ? '±' + fmt.temp(h.dev_warn) : fmt.DASH) + '</td>' +
            '<td>' + (h.enabled ? h.max : fmt.DASH) + '</td>' +
            '<td>' + (h.enabled && h.stable_band ? '±' + fmt.temp(h.stable_band) + ' / ' + h.stable_sec + ' s' : fmt.DASH) + '</td></tr>';
        }).join('') + '</tbody></table>') +
      panel('MFC 알람', '',
        '<div class="grid3">' +
        fld('편차 경고', '±' + fmt.num((s.alarm_cfg || {}).mfc_dev_pct_fs, 1), '% FS') +
        fld('판정 지연', (s.alarm_cfg || {}).mfc_delay_s, 's') +
        fld('편차 지속 시', (s.alarm_cfg || {}).mfc_action || '경고만') +
        '</div>') +
      '<div class="panel"><div class="panel-body">' +
      '<div class="hint" style="margin-bottom:12px">🔒 설정 저장은 관리자 PIN이 필요합니다. 공정 중에는 저장할 수 없습니다.</div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end">' +
      '<button class="btn" data-cmd="settings_revert">되돌리기</button>' +
      '<button class="btn primary" data-cmd="settings_save">저장</button></div></div></div>';
  }

  function update() { /* 설정 탭은 실시간 값이 없다 */ }

  /* ---------- 작은 빌더 ---------- */
  function panel(title, note, body) {
    return '<div class="panel" style="flex:0 0 auto"><div class="panel-head">' + core.esc(title) +
      (note ? '<span class="note">' + note + '</span>' : '') + '</div>' +
      '<div class="panel-body">' + body + '</div></div>';
  }

  function fld(label, val, suffix) {
    // ★ 읽기 전용이다 — 이번 단계에서는 값을 바꿔도 저장되지 않으므로 disabled 로 둔다.
    return '<div class="field"><label>' + core.esc(label) + '</label><div class="inwrap">' +
      '<input class="inp" value="' + core.esc(val == null ? fmt.DASH : val) + '" disabled>' +
      (suffix ? '<span class="suffix">' + core.esc(suffix) + '</span>' : '') + '</div></div>';
  }

  function btn(t) { return '<button class="btn sm" data-cmd="settings_todo">' + core.esc(t) + '</button>'; }
  function chipNote(t, lv) { return core.chip(t, lv, true); }

  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cmd]');
    if (!b) return;
    if (b.dataset.cmd === 'settings_save') w.app.send('settings_save');
    else if (b.dataset.cmd === 'settings_todo' || b.dataset.cmd === 'settings_revert') {
      core.toast('다음 단계에서 구현됩니다', 'info');
    }
  });

  core.register('setup', { render: render, update: update });
  w.viewSetup = { render: render, update: update };
})(window, document);
