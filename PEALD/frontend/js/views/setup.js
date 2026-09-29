/* ============================================================
 * views/setup.js — 설정 탭 (읽기 전용).
 *
 * 설정값 · PLC 파라미터(되읽은 값) · 연결 정보 · 최대 스캔 시간을 보여 준다.
 * ★ 편집은 3단계다. 이번 단계에서 반쪽만 저장하면 화면과 실제 설정이 어긋난 채로
 *   운전하게 되므로, 아예 입력을 잠가 둔다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  function render(s) {
    var L = core.bind('setupLeft'), R = core.bind('setupRight');
    if (!L || !R) return;
    var c = s.config || {};
    var dev = s.device || {};
    var plc = c.plc || {};
    var prm = c.params || {};
    var unconf = s.unconfirmed || [];

    L.innerHTML =
      panel('설정 파일', srcChip(c.source),
        '<div style="font-weight:700;font-size:var(--fs-xl)">' + core.esc(c.path_name || fmt.DASH) + '</div>' +
        '<div class="hint" style="margin-top:4px">' +
        (c.source === 'example'
          ? '⚠ 예시 설정으로 실행 중입니다. 현장 값을 넣은 config.json 을 exe 옆에 두세요.'
          : '현장 설정을 읽었습니다.') + '</div>' +
        '<div class="hint" style="margin-top:6px">설정은 <b>3단계</b>에서 편집할 수 있게 됩니다.</div>') +

      panel('PLC 통신', '',
        '<div class="grid2">' +
        fld('주소', plc.simulate ? '내장 시뮬레이터' : (plc.host || fmt.DASH)) +
        fld('포트', plc.simulate ? plc.sim_port : plc.port) +
        fld('Unit ID', plc.unit_id) + fld('응답 제한', plc.timeout_ms, 'ms') +
        fld('상태 읽기 주기', plc.poll_ms, 'ms') + fld('하트비트 주기', plc.heartbeat_ms, 'ms') +
        '</div>' +
        (plc.simulate ? '<div class="hint warn" style="margin-top:6px">시뮬레이터 모드 — 실장비에 연결하려면 plc.simulate 를 false 로 두세요.</div>' : '')) +

      panel('아날로그 · 환산', '',
        '<div class="grid2">' +
        fld('원시값 최대', (c.analog || {}).raw_max, '', unconf.indexOf('아날로그 원시값 최대') >= 0) +
        fld('CVG 방식', ((c.pressure || {}).cvg || {}).mode, '', unconf.indexOf('CVG 압력') >= 0) +
        '</div>' +
        (unconf.length
          ? '<div class="hint warn" style="margin-top:8px">환산 미확정: ' + core.esc(unconf.join(' · ')) +
            '<br>현장에서 실제 스케일을 확인한 뒤 설정에서 confirmed 를 true 로 바꾸세요.</div>'
          : '<div class="hint" style="margin-top:8px">모든 환산이 확정되었습니다.</div>')) +

      panel('로그', '',
        '<div class="grid2">' + fld('레벨', (c.log || {}).level) +
        fld('보관 기간', (c.log || {}).keep_days, '일') + '</div>' +
        '<div class="hint" style="margin-top:6px">폴더: data/logs · data/alarms</div>');

    R.innerHTML =
      panel('PLC 파라미터 (PC 가 써 넣는 값)', '<span data-bind="prmChip"></span>',
        '<table class="tbl"><thead><tr><th class="l">항목</th><th>값</th><th>주소</th></tr></thead><tbody>' +
        prow('PC 하트비트 끊김 판정', prm.pc_wdt_ms, 'ms', 'D01100') +
        prow('공정 시작 베이스 압력', prm.base_press_torr, 'Torr', 'D01101') +
        prow('베이스 도달 제한', prm.pump_timeout_s, 's', 'D01102') +
        prow('대기압 도달 제한', prm.vent_timeout_s, 's', 'D01103') +
        prow('MFC 안정 판정', prm.mfc_stable_s, 's', 'D01104') +
        prow('MFC1 허용 편차', prm.mfc_tol_sccm, 'sccm', 'D01105') +
        prow('MFC 안정 제한', prm.mfc_timeout_s, 's', 'D01106') +
        prow('펄스 밸브 최소 열림', prm.valve_min_ms, 'ms', 'D01107') +
        (dev.has_rf
          ? prow('RF 설정 상한', prm.rf_max_w, 'W', 'D01108') +
            prow('반사 전력 한계', prm.rf_ref_max_w, 'W', 'D01109') +
            prow('반사 초과 허용', prm.rf_ref_ms, 'ms', 'D01122') +
            prow('RF 허가 최대 압력', prm.rf_p_max_torr, 'Torr', 'D01123')
          : '') +
        (dev.has_o3 ? prow('O3 설정 상한', prm.o3_max, '', 'D01124') : '') +
        '</tbody></table>' +
        '<div class="hint" style="margin-top:6px">연결할 때마다 원시값으로 바꿔 PLC 에 쓰고 되읽어 확인합니다. ' +
        '0 이면 PLC 가 해당 기능을 막습니다.</div>') +

      panel('히터 과온 한계', '',
        '<table class="tbl"><thead><tr><th class="l">채널</th><th>사용</th><th>한계</th><th>기본 설정</th></tr></thead><tbody>' +
        ((s.structure || {}).heaters || []).map(function (h) {
          return '<tr class="' + (h.enabled ? '' : 'dim') + '"><td>CH' + h.ch + ' ' + core.esc(h.name) + '</td>' +
            '<td>' + (h.enabled ? '예' : '아니오') + '</td>' +
            '<td>' + (h.max_c == null
              ? '<span class="unconf">미정</span>'
              : fmt.temp(h.max_c) + '<span class="unit">°C</span>') + '</td>' +
            '<td>' + (h.default_sv == null ? fmt.DASH : fmt.temp(h.default_sv)) + '</td></tr>';
        }).join('') + '</tbody></table>') +

      panel('진단', '',
        '<div class="kv"><span class="k">PLC 응답 시간</span><span class="v" data-bind="dgRtt">—</span></div>' +
        '<div class="kv"><span class="k">PLC 최대 스캔 시간</span><span class="v" data-bind="dgScan">—</span></div>' +
        '<div class="kv"><span class="k">PLC 하트비트</span><span class="v" data-bind="dgHb">—</span></div>' +
        '<div class="kv"><span class="k">웹 서버</span><span class="v">' +
        core.esc((c.server || {}).host + ':' + (c.server || {}).port) + '</span></div>' +
        '<div class="kv"><span class="k">창 위치</span><span class="v">' +
        core.esc((c.window || {}).side || fmt.DASH) + '</span></div>');

    update(s.live || {});
  }

  function update(t) {
    var conn = !!(t.plc && t.plc.connected);
    core.setText('dgRtt', conn && t.plc.rtt_ms != null ? t.plc.rtt_ms + ' ms' : fmt.DASH);
    core.setText('dgScan', conn && t.scan_max_ms != null ? t.scan_max_ms + ' ms' : fmt.DASH);
    core.setText('dgHb', !conn ? fmt.DASH : (t.plc.hb_ok ? '정상' : '멈춤'));
    var pc = core.bind('prmChip');
    if (pc) {
      var mm = (t.plc || {}).prm_mismatch || [];
      pc.innerHTML = !conn ? '' : core.chip(mm.length ? '되읽기 불일치 ' + mm.length : '되읽기 일치',
                                            mm.length ? 'stop' : 'ok');
    }
  }

  /* ---------- 빌더 ---------- */
  function panel(title, note, body) {
    return '<div class="panel" style="flex:0 0 auto"><div class="panel-head">' + core.esc(title) +
      (note ? '<span class="note">' + note + '</span>' : '') + '</div>' +
      '<div class="panel-body">' + body + '</div></div>';
  }

  function fld(label, val, suffix, unconf) {
    return '<div class="field"><label>' + core.esc(label) +
      (unconf ? '<span class="unconf">환산 미확정</span>' : '') + '</label><div class="inwrap">' +
      '<input class="inp" value="' + core.esc(val == null ? fmt.DASH : val) + '" disabled>' +
      (suffix ? '<span class="suffix">' + core.esc(suffix) + '</span>' : '') + '</div></div>';
  }

  function prow(label, val, unit, addr) {
    return '<tr><td class="l">' + core.esc(label) + '</td>' +
      '<td class="mono">' + (val == null ? '<span class="unconf">미정</span>' : core.esc(val)) +
      (unit ? '<span class="unit">' + core.esc(unit) + '</span>' : '') + '</td>' +
      '<td class="mono dim">' + addr + '</td></tr>';
  }

  function srcChip(src) {
    return src === 'file' ? core.chip('현장 설정', 'ok') : core.chip('예시 설정', 'warn');
  }

  core.register('setup', { render: render, update: update });
  w.viewSetup = { render: render, update: update };
})(window, document);
