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
    var plc = c.plc || {};
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
      panel('PLC 파라미터 (PC 가 쓴 원시값 · 되읽은 값)', '<span data-bind="prmChip"></span>',
        '<table class="tbl"><thead><tr><th class="l">항목</th><th>쓴 원시값</th><th>되읽은 값</th>' +
        '<th>공학 단위</th><th>주소</th></tr></thead><tbody data-bind="prmBody">' +
        '<tr><td class="l dim" colspan="5">PLC 에 연결되면 표시합니다</td></tr></tbody></table>' +
        '<div class="hint" style="margin-top:6px">연결할 때마다 설정을 원시값으로 바꿔 PLC 에 쓰고, 1 s 마다 되읽습니다. ' +
        '공학 단위는 되읽은 값을 거꾸로 환산한 것입니다. 0 이면 PLC 가 해당 기능을 막거나 감시하지 않습니다.</div>') +

      panel('히터 과온 한계', '',
        '<table class="tbl"><thead><tr><th class="l">채널</th><th>사용</th><th>국번</th><th>한계</th><th>기본 설정</th></tr></thead><tbody>' +
        ((s.structure || {}).heaters || []).map(function (h) {
          return '<tr class="' + (h.enabled ? '' : 'dim') + '"><td>CH' + h.ch + ' ' + core.esc(h.name) + '</td>' +
            '<td>' + (h.enabled ? '예' : '아니오') + '</td>' +
            '<td>' + core.esc(h.station == null ? fmt.DASH : h.station) + '</td>' +
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
    var body = core.bind('prmBody');
    var rows = (t.plc || {}).prm || [];
    if (body && rows.length) {
      body.innerHTML = rows.map(function (r) {
        var ev = r.eng == null ? fmt.DASH
          : r.unit === 'Torr' ? fmt.torr(r.eng)
            : (Number.isInteger(r.eng) ? String(r.eng) : fmt.num(r.eng, 1));
        return '<tr' + (r.match ? '' : ' class="bad"') + '><td class="l">' + core.esc(r.name) + '</td>' +
          '<td class="mono">' + r.written + '</td>' +
          '<td class="mono">' + (r.readback == null ? fmt.DASH : r.readback) +
          (r.match ? '' : ' ' + core.chip('불일치', 'stop')) + '</td>' +
          '<td class="mono">' + ev + '<span class="unit">' + core.esc(r.unit || '') + '</span></td>' +
          '<td class="mono dim">' + core.esc(r.addr) + '</td></tr>';
      }).join('');
    } else if (body && !conn) {
      body.innerHTML = '<tr><td class="l dim" colspan="5">PLC 끊김 — 값 없음</td></tr>';
    }
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

  function srcChip(src) {
    return src === 'file' ? core.chip('현장 설정', 'ok') : core.chip('예시 설정', 'warn');
  }

  core.register('setup', { render: render, update: update });
  w.viewSetup = { render: render, update: update };
})(window, document);
