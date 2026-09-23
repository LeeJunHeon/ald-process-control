/* ============================================================
 * views/alarm.js — 알람 · 로그 탭.
 *
 * 현재 알람(등급 칩 + 확인 버튼) / 알람 이력 표 / 공정 로그(모노스페이스).
 * ★ 로그는 서버에서 온 문자열이다 — textContent 로만 넣는다(innerHTML 금지).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var LEVEL = { err: ['중대', 'stop'], warn: ['경고', 'warn'], info: ['정보', 'info'] };
  var lastSig = '';

  function render(s) {
    renderHistory(s);
    renderLogs(s);
    renderAlarms(s, ((s.live || {}).alarms) || s.alarms || []);
  }

  function update(t) {
    var s = core.state;
    if (!s) return;
    renderAlarms(s, t.alarms || []);
  }

  /* ---------- 현재 알람 ---------- */
  function renderAlarms(s, alarms) {
    var tbl = core.bind('alarmTbl');
    if (!tbl) return;
    var sig = alarms.map(function (a) { return a.code + a.ack; }).join('|');
    if (sig === lastSig) return;
    lastSig = sig;

    var n = { err: 0, warn: 0, info: 0 };
    alarms.forEach(function (a) { n[a.level] = (n[a.level] || 0) + 1; });
    core.setText('alarmCount', '중대 ' + (n.err || 0) + ' · 경고 ' + (n.warn || 0) + ' · 정보 ' + (n.info || 0));

    if (!alarms.length) {
      tbl.innerHTML = '<tbody><tr><td class="empty" colspan="6">현재 알람이 없습니다</td></tr></tbody>';
    } else {
      var html = '<thead><tr><th>등급</th><th>발생</th><th>코드</th><th>내용</th><th>상태</th><th></th></tr></thead><tbody>';
      alarms.forEach(function (a) {
        var lv = LEVEL[a.level] || LEVEL.info;
        html += '<tr class="' + (a.ack ? '' : 'hl') + '">' +
          '<td>' + core.chip(lv[0], lv[1]) + '</td>' +
          '<td class="mono">' + core.esc(a.ts) + '</td>' +
          '<td class="mono"><b>' + core.esc(a.code) + '</b></td>' +
          '<td class="l">' + core.esc(a.msg) + '</td>' +
          '<td><b style="color:var(--' + (a.ack ? 'ok' : 'warn') + ')">' + (a.ack ? '확인됨' : '미확인') + '</b></td>' +
          '<td><button class="btn sm" data-ack="' + core.esc(a.code) + '"' + (a.ack ? ' disabled' : '') + '>확인</button></td></tr>';
      });
      tbl.innerHTML = html + '</tbody>';
    }
    core.setText('alarmHint',
      '경고: 표시·기록만 합니다. 중대: PLC가 공정을 멈추고 ALD 밸브를 닫은 뒤 N2 퍼지 상태로 둡니다.');
  }

  /* ---------- 이력 ---------- */
  function renderHistory(s) {
    var tbl = core.bind('histTbl');
    if (!tbl) return;
    var h = s.alarm_history || [];
    if (!h.length) { tbl.innerHTML = '<tbody><tr><td class="empty" colspan="5">이력이 없습니다</td></tr></tbody>'; return; }
    var html = '<thead><tr><th>발생</th><th>해제</th><th>등급</th><th>코드</th><th>내용</th></tr></thead><tbody>';
    h.forEach(function (a) {
      var lv = LEVEL[a.level] || LEVEL.info;
      html += '<tr><td class="mono">' + core.esc((a.date || '') + ' ' + a.ts) + '</td>' +
        '<td class="mono dim">' + core.esc(a.cleared || fmt.DASH) + '</td>' +
        '<td>' + core.chip(lv[0], lv[1]) + '</td>' +
        '<td class="mono"><b>' + core.esc(a.code) + '</b></td>' +
        '<td class="l">' + core.esc(a.msg) + '</td></tr>';
    });
    tbl.innerHTML = html + '</tbody>';
  }

  /* ---------- 공정 로그 ---------- */
  function renderLogs(s) {
    var box = core.bind('logBox');
    if (!box) return;
    box.innerHTML = '';
    (s.logs || []).forEach(function (l) { box.appendChild(line(l)); });
    box.scrollTop = box.scrollHeight;
    core.setText('logNote', '데이터 폴더: data/' + ((s.chamber || {}).id || '') + '/  ·  기록 주기 ' +
      ((s.log_cfg || {}).datalog_interval_s || 1) + ' s');
  }

  function line(l) {
    var e = core.h('div', { cls: 'ln ' + (l.level || 'info') });
    e.appendChild(core.h('span', { cls: 'ts', text: l.ts || '' }));
    e.appendChild(core.h('span', { text: l.msg || '' }));   // ★ textContent — 이스케이프 불필요
    return e;
  }

  /** app.js 가 로그 메시지를 받을 때마다 부른다(전체를 다시 그리지 않는다). */
  function appendLog(msg) {
    var box = core.bind('logBox');
    if (!box) return;
    var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.appendChild(line(msg));
    while (box.children.length > 500) box.removeChild(box.firstChild);
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-ack]');
    if (b && !b.disabled) { w.app.send('alarm_ack', { code: b.getAttribute('data-ack') }); return; }
    var c = ev.target.closest('[data-cmd]');
    if (!c) return;
    if (c.dataset.cmd === 'alarm_ack_all') w.app.send('alarm_ack');
    else if (c.dataset.cmd === 'alarm_reset') w.app.send('alarm_reset');
  });

  core.register('alarm', { render: render, update: update });
  w.viewAlarm = { render: render, update: update, appendLog: appendLog };
})(window, document);
