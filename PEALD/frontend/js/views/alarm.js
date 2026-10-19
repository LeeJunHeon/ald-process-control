/* ============================================================
 * views/alarm.js — 알람 · 로그 탭.
 *
 * 현재 알람(PLC 알람 워드를 문구로 푼 것) / 프로그램 알림 / 알람 이력 / 프로그램 로그
 *
 * ★ PLC 알람과 PC 자체 알림을 구분해 보여 준다. PC 알림(연결 끊김·하트비트 멈춤·
 *   파라미터 되읽기 불일치·환산 미확정·예시 설정)은 장비가 낸 알람이 아니라 이
 *   프로그램이 판단한 것이라, 같은 목록에 섞으면 운전자가 PLC 를 의심하게 된다.
 * ★ 로그·알람 문구는 서버에서 온 문자열이다 — textContent 로만 넣는다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var lastSig = null;       // null = 아직 안 그림(빈 알람 목록 '' 과 구분 — 처음 열 때 '—' · 빈 표가 남지 않게)
  var openCode = {};        // v0.7.0(A1): 원인 · 확인을 펼친 알람 코드

  /** 알람 정의(원인 · 확인) — structure.alarm_defs */
  function def(code) {
    return (((core.state || {}).structure || {}).alarm_defs || {})[code] || {};
  }

  function render(s) {
    renderHistory(s);
    renderLogs(s);
    renderNotices(s, (s.live || {}));
    lastSig = null;
    renderAlarms(s, s.live || {});
  }

  // 알람 이력 — live 의 alarm_hist_ver 가 바뀌면 다시 받는다(접속 때 한 번만 받으면 그 뒤 이력이 멈춘다)
  var histVer = null, histAsked = 0;

  w.app.on('alarm_history', function (m) {
    histAsked = 0;
    var s = core.state;
    if (!s) return;
    s.alarm_history = m.items || [];
    histVer = m.ver;
    renderHistory(s);
  });

  function update(t) {
    var s = core.state;
    if (!s) return;
    if (!t.offline && t.alarm_hist_ver != null && histVer !== t.alarm_hist_ver &&
        Date.now() - histAsked > 3000) {
      if (histVer == null) histVer = t.alarm_hist_ver;      // 접속 스냅샷이 이미 담고 있다
      else { histAsked = Date.now(); w.app.send('alarm_history'); }
    }
    renderAlarms(s, t);
    renderNotices(s, t);
    core.renderAlarmModal();
  }

  /* ---------- 현재 알람 ---------- */
  function renderAlarms(s, t) {
    var tbl = core.bind('alarmTbl');
    if (!tbl) return;
    var alarms = t.alarms || [];
    // ★ 서버 · PLC 가 끊겼거나 PLC 하트비트가 멈췄으면 지금 알람을 모른다 — '없습니다' · '중대 0' 이 아니라
    //   '알 수 없음'(머리말 '알람 —' 과 같게). 멈춤이면 core 가 live 를 끊김 모양(plc.stalled)으로 바꿔 준다
    var p = t.plc || {};
    var known = !t.offline && !!p.connected;
    var sig = (known ? 'k' : 'u:' + (t.offline ? 's' : p.stalled ? 'h' : 'p')) + '|' +
      alarms.map(function (a) { return a.code + (openCode[a.code] ? '+' : ''); }).join('|');
    if (sig === lastSig) return;
    lastSig = sig;

    if (!known) {
      core.setText('alarmCount', T('s.unknown'));
      var why = t.offline ? '서버 연결이 끊겨' : p.stalled ? 'PLC 하트비트가 멈춰' : 'PLC 연결이 끊겨';
      tbl.innerHTML = '<tbody><tr><td class="empty" colspan="4">' + why +
        ' 지금 알람을 알 수 없습니다</td></tr></tbody>';
      return;
    }
    var crit = alarms.filter(function (a) { return a.crit; }).length;
    core.setText('alarmCount', T('s.critical') + ' ' + crit + ' · ' + T('s.warning') + ' ' + (alarms.length - crit));

    if (!alarms.length) {
      tbl.innerHTML = '<tbody><tr><td class="empty" colspan="4">현재 알람이 없습니다</td></tr></tbody>';
      return;
    }
    var html = '<thead><tr><th>' + T('v.severity') + '</th><th>' + T('v.raised') + '</th><th>' + T('v.code') +
      '</th><th class="l">' + T('v.message') + '</th></tr></thead><tbody>';
    // v0.7.0(A1): 행을 누르면(키보드 Enter/Space 도) 아래로 원인 · 확인이 펼쳐진다
    alarms.forEach(function (a) {
      var df = def(a.code), open = !!openCode[a.code];
      html += '<tr class="almrow' + (a.crit ? ' hl' : '') + '" tabindex="0" role="button" aria-expanded="' + open +
        '" data-almcode="' + core.esc(a.code) + '" title="눌러서 원인 · 확인 보기">' +
        '<td>' + core.chip(a.crit ? T('s.critical') : T('s.warning'), a.crit ? 'stop' : 'warn') + '</td>' +
        '<td class="mono">' + core.esc(a.since) + '</td>' +
        '<td class="mono">' + core.esc(a.code) + '</td>' +
        '<td class="l">' + (open ? '▾ ' : '▸ ') + core.esc(a.name) + '</td></tr>';
      if (open) {
        html += '<tr class="almhelp"><td colspan="4" class="l"><div><b>원인</b> ' + core.esc(df.cause || '—') +
          '</div><div><b>확인</b> ' + core.esc(df.check || '—') + '</div></td></tr>';
      }
    });
    tbl.innerHTML = html + '</tbody>';
  }

  /* ---------- 프로그램 알림 ---------- */
  function renderNotices(s, t) {
    var box = core.bind('pcNotices');
    if (!box) return;
    var out = [];
    var plc = t.plc || {};
    if (t.offline) {
      out.push(['err', '서버 연결 끊김 — 이 화면은 다시 연결될 때까지 값을 보이지 않습니다.']);
    } else if (plc.stalled) {
      out.push(['err', 'PLC 하트비트 멈춤 — ' + (plc.addr || '') + ' 연결은 됐지만 PLC 가 스캔을 돌리지 않습니다' +
        '(STOP 일 수 있습니다). 값은 표시하지 않고 명령을 보내지 않습니다.']);
    } else if (!plc.connected) {
      out.push(['err', 'PLC 연결 끊김 — ' + (plc.addr || '') + ' 에 접속하지 못했습니다. 값 표시와 명령이 멈춥니다.']);
    } else if (!plc.hb_ok) {
      out.push(['warn', 'PLC 하트비트 멈춤 — 통신은 되는데 PLC 가 스캔을 돌리지 않습니다 (STOP 상태일 수 있습니다).']);
    }
    (plc.prm_mismatch || []).forEach(function (m) {
      out.push(['warn', 'PLC 파라미터 되읽기 불일치 — ' + m]);
    });
    // v0.7.0: 서버가 판단하는 알림(남은 PC 끊김 트립 · 정비 한계 · 공정 중 압력 상한 · 히터 이탈)
    if (!t.offline) {
      (t.pc_notices || []).forEach(function (n) { out.push([n[0] === 'err' ? 'err' : 'warn', n[1]]); });
    }
    (s.notices || []).forEach(function (n) {
      out.push([n.level === 'err' ? 'err' : 'warn', n.msg]);
    });
    core.html(box, out.length
      ? out.map(function (x) {
          return '<div class="pcnotice ' + x[0] + '">' + core.esc(x[1]) + '</div>';
        }).join('')
      : '<div class="empty">프로그램 알림이 없습니다</div>');
  }

  /* ---------- 이력 ---------- */
  function renderHistory(s) {
    var tbl = core.bind('histTbl');
    if (!tbl) return;
    var h = s.alarm_history || [];
    core.setText('histNote', '파일: data/alarms/alarms-YYYYMMDD.csv');
    if (!h.length) {
      tbl.innerHTML = '<tbody><tr><td class="empty" colspan="5">이력이 없습니다</td></tr></tbody>';
      return;
    }
    var html = '<thead><tr><th>' + T('v.raised') + '</th><th>' + T('v.cleared') + '</th><th>' + T('v.severity') +
      '</th><th>' + T('v.code') + '</th><th class="l">' + T('v.message') + '</th></tr></thead><tbody>';
    h.forEach(function (a) {
      var df = def(a.code);
      var tip = df.cause ? '원인: ' + df.cause + String.fromCharCode(10) + '확인: ' + df.check : '';
      html += '<tr' + (tip ? ' title="' + core.esc(tip) + '"' : '') + '><td class="mono">' + core.esc((a.date || '') + ' ' + a.since) + '</td>' +
        '<td class="mono">' + core.esc(a.cleared || fmt.DASH) + '</td>' +
        '<td>' + core.chip(a.crit ? T('s.critical') : T('s.warning'), a.crit ? 'stop' : 'warn') + '</td>' +
        '<td class="mono">' + core.esc(a.code) + '</td>' +
        '<td class="l">' + core.esc(a.name) + '</td></tr>';
    });
    tbl.innerHTML = html + '</tbody>';
  }

  /* ---------- 로그 ---------- */
  function renderLogs(s) {
    var box = core.bind('logBox');
    if (!box) return;
    box.innerHTML = '';
    (s.logs || []).forEach(function (l) { box.appendChild(line(l)); });
    box.scrollTop = box.scrollHeight;
  }

  function line(l) {
    var e = core.h('div', { cls: 'ln ' + (l.level || 'info') });
    e.appendChild(core.h('span', { cls: 'ts', text: l.ts || '' }));
    e.appendChild(core.h('span', { text: l.msg || '' }));   // ★ textContent
    return e;
  }

  function appendLog(msg) {
    var box = core.bind('logBox');
    if (!box) return;
    var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.appendChild(line(msg));
    while (box.children.length > 500) box.removeChild(box.firstChild);
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  // 현재 알람 행 펼치기 — 마우스 · 키보드(core.js 가 tabindex="0" 의 Enter/Space 를 click 으로)
  d.addEventListener('click', function (ev) {
    var tr = ev.target.closest && ev.target.closest('tr[data-almcode]');
    if (!tr) return;
    var code = tr.getAttribute('data-almcode');
    openCode[code] = !openCode[code];
    lastSig = null;
    var s = core.state;
    if (s) renderAlarms(s, s.live || {});
    var back = d.querySelector('tr[data-almcode="' + code + '"]');
    if (back && back.focus) back.focus();
  });

  core.register('alarm', { render: render, update: update });
  w.viewAlarm = { render: render, update: update, appendLog: appendLog };
})(window, document);
