/* ============================================================
 * core.js — 상태 수신 → 뷰 디스패치, 탭, fit(), 토스트, 확인 창, 알람 창.
 *
 * 뷰 모듈은 전부 render(state) / update(live) 두 함수 형태로 통일한다.
 *   render : 구조가 바뀔 때(접속·설정 변경) 한 번. DOM 을 다시 만든다.
 *   update : 5 Hz. 이미 만들어진 DOM 의 값만 바꾼다. 여기서 DOM 을 새로 만들면
 *            초당 5회 레이아웃이 다시 계산돼 화면이 눈에 띄게 버벅인다.
 *
 * DOM 연결은 data-bind 속성으로만 한다.
 *
 * 창(모달) 쌓임 순서: 알람 창 > 확인 창 > 나머지 창(수동 조작 · PIN · 설정 · 시뮬레이터).
 *   Esc 는 맨 위 창을 닫는다(확인 창은 취소). 창이 열리면 포커스가 창 안으로, 닫히면 원래 자리로.
 * 서버(웹소켓)가 끊기면 모든 실시간 값을 '—' 로, 상태 칩 '서버 끊김', 조작 잠금(setOffline).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var VIEWS = {};
  var lastState = null;
  var curTab = 'main';
  var confirmCb = null;
  var confirmKey = '';
  var offline = false;
  var alarmDismissed = null;      // 원격에서 닫은 알람 창 — { seq: 그때의 알람 번호, codes: 그때의 코드들 }

  /* ===================== DOM 도우미 ===================== */
  function bind(name, root) { return (root || d).querySelector('[data-bind="' + name + '"]'); }

  /** 서버·PLC 에서 온 문자열을 HTML 에 넣을 때 쓴다. */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function h(tag, attrs, kids) {
    var e = d.createElement(tag);
    for (var k in (attrs || {})) {
      if (k === 'text') e.textContent = attrs[k];
      else if (k === 'html') e.innerHTML = attrs[k];
      else if (k === 'cls') e.className = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) e.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }

  function setText(name, value, root) {
    var e = bind(name, root);
    if (e) e.textContent = value;
  }

  /** 내용이 바뀔 때만 innerHTML 을 쓴다. ★ 1 초에 5 번 같은 칩을 다시 만들면 title 툴팁이
   *  뜨지 않고(요소가 계속 바뀐다) 레이아웃도 다시 계산된다. */
  function html(el, markup) {
    if (!el) return;
    if (el._html === markup) return;
    el._html = markup;
    el.innerHTML = markup;
  }

  function chip(text, level, title) {
    return '<span class="chip ' + (level || '') + '"' +
      (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(text) + '</span>';
  }

  /** 비트 검사 — 화면은 비트 번호를 서버에서 받은 구조 정의로만 다룬다. */
  function bit(word, n) { return !!((Number(word) >> n) & 1); }

  /* ===================== 탭 ===================== */
  function setTab(name) {
    curTab = name;
    Array.prototype.forEach.call(d.querySelectorAll('.tab'), function (b) {
      b.classList.toggle('on', b.dataset.tab === name);
    });
    Array.prototype.forEach.call(d.querySelectorAll('.view'), function (v) {
      v.classList.toggle('on', v.id === 'view-' + name);
    });
    // 탭을 처음 열 때는 DOM 크기가 0이라 캔버스 계산이 틀어진다 → 전환 직후 한 번 더 그린다.
    var v = VIEWS[name];
    if (v && lastState) { try { v.render(lastState); } catch (e) { console.error(e); } }
  }

  d.addEventListener('click', function (ev) {
    var t = ev.target.closest('.tab');
    if (t && t.dataset.tab) setTab(t.dataset.tab);
  });

  /* ===================== 상태 디스패치 ===================== */
  function applyState(s) {
    lastState = s;
    setOffline(false);
    applyDevice(s);
    for (var k in VIEWS) {
      try { VIEWS[k].render(s); } catch (e) { console.error('render ' + k, e); }
    }
    if (s.live) applyLive(s.live);
    // 조작판이 열려 있으면 스위치를 다시 그린다(누른 뒤 서버가 보낸 상태로 — 열 때만 그리면 그대로 남는다)
    var sm = d.getElementById('simModal');
    if (sm && !sm.hidden) renderSimPanel();
  }

  /** PLC 하트비트 멈춤(STOP 등) — 연결은 돼 있어도 PLC 값은 멈춘 옛 값이다. 화면에는 끊김처럼 '—' 로
   *  보여 주고(알람도 모름) 조작을 잠근다. plc.stalled 로 머리말 · 잠금 문구를 구분한다. */
  function blankStalled(t) {
    var p = t.plc || {};
    return { clock: t.clock, date: t.date, alarm_popup_seq: t.alarm_popup_seq, alarm_hist_ver: t.alarm_hist_ver,
             datalog: t.datalog, scan_max_ms: t.scan_max_ms,
             plc: Object.assign({}, p, { connected: false, stalled: true }),
             alarms: [], heaters: [], mfc: [], process: {}, manual: {}, seq: {}, extra: {}, pressure: {} };
  }

  function applyLive(t) {
    if (!lastState) return;
    if (!t.offline && t.plc && t.plc.connected && t.plc.hb_stalled) t = blankStalled(t);
    // ★ 끊긴 동안에는 빈 live 가 곧 현재 상태다 — 탭을 바꿔 다시 그려도 옛 값이 되살아나지 않게
    lastState.live = t;
    if (!t.offline) {
      setText('clock', t.clock || fmt.DASH);
      setText('date', t.date || fmt.DASH);
    }
    var ck = d.querySelector('#app .clock');
    if (ck) ck.classList.toggle('stale', !!t.offline);   // 끊긴 동안 시계는 멈춘 시각을 흐리게
    applyHeader(lastState, t);
    for (var k in VIEWS) {
      try { VIEWS[k].update(t); } catch (e) { console.error('update ' + k, e); }
    }
    // 알람이 하나도 없으면(모두 풀렸거나 PLC 가 끊겨 모를 때) 빈 알람 창을 새로 띄우지 않는다
    if (t.alarm_popup && !t.offline && (t.alarms || []).length && !dismissedStill(t)) showAlarmModal();
  }

  /** 원격에서 닫은 알람 창은 새 알람이 오기 전까지 다시 띄우지 않는다.
   *  새 알람 = 서버의 알람 번호(alarm_popup_seq — D00007 0→1 마다 +1)가 바뀌었거나 그때 없던 코드.
   *  ★ 번호로 본다 — 같은 코드의 알람이 풀렸다가 다시 나도(비상정지 → 리셋 → 다시 비상정지) 다시 뜬다. */
  function dismissedStill(t) {
    if (!alarmDismissed) return false;
    var now = (t.alarms || []).map(function (a) { return a.code; });
    var fresh = (t.alarm_popup_seq != null && t.alarm_popup_seq !== alarmDismissed.seq) ||
      now.some(function (c) { return alarmDismissed.codes.indexOf(c) < 0; });
    if (fresh) alarmDismissed = null;
    return !fresh;
  }

  /* ===================== 서버 끊김 ===================== */
  /** 웹소켓이 끊기면 마지막 값을 남기지 않는다 — '—' · '서버 끊김' · 조작 잠금.
   *  다시 연결되면 서버가 state 를 보내 applyState 가 되돌린다. */
  function setOffline(on) {
    if (on === offline) return;
    offline = on;
    // ★ 다시 연결됐다 — 서버가 다시 시작됐으면 알람 번호가 처음부터라 옛 기억이 새 알람을 막는다
    if (!on) alarmDismissed = null;
    d.documentElement.classList.toggle('offline', on);
    if (on && lastState) {
      var prev = lastState.live || {};
      applyLive({ offline: true, clock: prev.clock, date: prev.date,
                  plc: { connected: false, addr: (prev.plc || {}).addr }, alarms: [], heaters: [],
                  mfc: [], process: {}, manual: {}, seq: {}, extra: {}, pressure: {} });
    }
  }

  /** 장비 정체성 — 접속할 때 한 번 정해지고 바뀌지 않는다. */
  function applyDevice(s) {
    var dev = s.device || {};
    d.documentElement.setAttribute('data-theme', dev.theme === 'dark' ? 'dark' : 'light');
    d.documentElement.style.setProperty('--accent', dev.accent || '');
    d.title = dev.title || '공정 제어';
    setText('devBadge', dev.name || fmt.DASH);
    setText('cfDev', dev.name || '');
    setText('amDev', dev.name || '');
    setText('smDev', dev.name || '');
    setText('plcAddr', 'PLC ' + (dev.plc_addr || fmt.DASH));
    setText('sbVer', 'v' + (dev.app_version || ''));
    var sim = bind('simChip');
    if (sim) sim.hidden = !dev.simulate;
    var acc = bind('access');
    if (acc) {
      var local = (s.access || {}).local;
      acc.textContent = '조작 권한: ' + (local ? '이 PC' : '보기 전용');
      acc.classList.toggle('remote', !local);
    }
  }

  // 장비 상태(D00001) → 칩 색. 대기 회색 / 공정 초록 / 일시정지 주황 / 안전정지 빨강
  var STATE_LEVEL = { 0: 'off', 1: 'off', 2: 'ok', 3: 'ok', 4: 'warn', 5: 'warn', 6: 'stop' };

  function applyHeader(s, t) {
    var band = bind('idBand');
    if (band) {
      var plc0 = t.plc || {};
      band.hidden = !idBlocked(plc0);
      if (plc0.id_state === 'wrong') {
        band.textContent = '다른 장비의 PLC 입니다 — 주소를 확인하세요 · 모든 조작을 막았습니다 (읽은 ID ' +
          idText(plc0.device_id) + ' / 이 장비 ' + idText(plc0.expected_id) + ')';
      } else if (plc0.id_state === 'missing') {
        band.textContent = 'PLC 장비 ID 가 없습니다(0) — 이 장비 PLC 인지 확인할 수 없어 모든 조작을 막았습니다';
      }
    }
    if (t.plc && t.plc.config_error) setText('plcAddr', 'PLC 주소 없음');
    var pill = bind('stateChip');
    if (pill) {
      if (t.offline) {
        pill.className = 'chip big stop';
        pill.textContent = '서버 끊김';
      } else if (t.plc && t.plc.stalled) {
        pill.className = 'chip big stop';
        pill.textContent = 'PLC 하트비트 멈춤';
      } else if (!t.plc || !t.plc.connected) {
        pill.className = 'chip big off';
        pill.textContent = 'PLC 끊김';
      } else if (!t.plc.hb_ok) {
        pill.className = 'chip big warn';
        pill.textContent = 'PLC 하트비트 멈춤';
      } else {
        var st = t.state || {};
        pill.className = 'chip big ' + (STATE_LEVEL[st.code] || 'off');
        pill.textContent = st.name || fmt.DASH;
      }
    }
    var alarms = t.alarms || [];
    var ac = bind('alarmChip');
    if (ac) {
      var crit = alarms.some(function (a) { return a.crit; });
      // PLC 가 끊겼으면 알람을 모른다 — '알람 없음' 이 아니라 '—'
      var known = !t.offline && t.plc && t.plc.connected;
      ac.className = 'chip ' + (!known ? 'off' : crit ? 'stop' : alarms.length ? 'warn' : '');
      ac.textContent = !known ? '알람 ' + fmt.DASH : alarms.length
        ? '⚠ ' + (crit ? '중대 ' : '경고 ') + alarms.length + '건'
        : '알람 없음';
    }
    // 데이터 로그는 기록 중인 파일 이름까지 보여 준다 — 나중에 그 파일을 찾아야 한다.
    var rec = bind('sbRec');
    if (rec) {
      var dl = t.datalog || {};
      rec.hidden = !dl.active && !dl.error;
      rec.className = 'rec' + (dl.error ? ' err' : '');
      rec.textContent = dl.error ? '⚠ ' + dl.error
        : '● 데이터 로그 기록 중 (' + (dl.file || '') + '.csv)';
    }
    var plc = t.plc || {};
    var dot = bind('sbDot');
    if (dot) dot.classList.toggle('off', !plc.connected);
    if (t.offline) { setText('sbText', '서버 연결 끊김 — 다시 연결하는 중 · 값은 표시하지 않습니다'); return; }
    if (plc.stalled) {
      setText('sbText', 'PLC ' + (plc.addr || fmt.DASH) + ' 연결됨 · 하트비트 멈춤 — PLC 가 STOP 이거나 멈췄습니다 · ' +
        '값은 표시하지 않고 명령을 보내지 않습니다');
      return;
    }
    setText('sbText', plc.config_error ? 'PLC 주소 없음 — 연결하지 않았습니다' : [
      'PLC ' + (plc.addr || fmt.DASH) + (plc.connected ? ' 연결됨' : ' 연결 안 됨'),
      plc.connected ? (plc.hb_ok ? '하트비트 정상' : '하트비트 멈춤') : '',
      plc.connected && plc.rtt_ms != null ? '응답 ' + plc.rtt_ms + ' ms' : '',
      t.scan_max_ms != null ? '최대 스캔 ' + scanText(t.scan_max_ms) : ''
    ].filter(Boolean).join(' · '));
    var sb = bind('sbText');
    var tip = t.scan_max_ms === 0 ? '최대 스캔: PLC 가 아직 쓰지 않음(D00080 = 0)' : '';
    if (sb && sb.title !== tip) sb.title = tip;
  }

  /** D00080(스캔 최대)은 래더가 아직 쓰지 않아 실장비에서 늘 0 — 0 이면 '—'. */
  function scanText(v) { return v ? v + ' ms' : fmt.DASH; }

  /** 장비 ID 때문에 쓰기가 막힌 상태 — 다른 장비(wrong) 또는 ID 필수인데 0(missing). */
  function idBlocked(p) { return !!p && (p.id_state === 'wrong' || p.id_state === 'missing'); }

  function idText(v) {
    if (v == null) return fmt.DASH;
    var n = Number(v) & 0xFFFF;
    var c = function (x) { return x > 32 && x < 127 ? String.fromCharCode(x) : '?'; };
    return '0x' + n.toString(16).toUpperCase().padStart(4, '0') + " '" + c(n >> 8) + c(n & 0xFF) + "'";
  }

  /* ===================== 토스트 ===================== */
  function toast(msg, level) {
    var box = d.getElementById('toasts');
    if (!box) return;
    var el = h('div', { cls: 'toast ' + (level || 'info'), text: msg });
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, 4200);
  }

  /* ===================== 확인 창 ===================== */
  /** 위험하거나 되돌리기 어려운 명령은 반드시 이걸 거친다.
   *  제목에 장비 이름을 고유색으로 크게 보여 준다 — 두 창이 나란히 떠 있을 때
   *  다른 장비에 명령을 보내는 사고를 막는 마지막 방어선이다. */
  function confirmAsk(title, body, okLabel, cb) {
    var m = d.getElementById('confirmModal');
    var key = title + '|' + body + '|' + (okLabel || '');
    if (!m.hidden) {
      // ★ 떠 있는 동안 온 요청은 새로 띄우지 않는다 — 두 번째 클릭이 확인 콜백을 바꾸면
      //   운전자가 본 문구와 실제로 실행되는 동작이 달라진다.
      if (key !== confirmKey) toast('열려 있는 확인 창을 먼저 닫으세요', 'warn');
      return;
    }
    setText('cfTitle', title);
    var b = bind('cfBody');
    if (b) b.innerHTML = body;
    var ok = d.querySelector('[data-cf="ok"]');
    if (ok) ok.textContent = okLabel || '실행';
    confirmCb = cb;
    confirmKey = key;
    // 위험한 동작의 확인 창 — 포커스는 '취소' 에(Enter 한 번으로 실행되지 않게).
    // 입력 칸이 있는 확인 창(이름 입력)은 그 칸에 — 늦게 오는 '취소' 포커스가 친 글자를 빼앗지 않게
    showModal('confirmModal', b && b.querySelector('input') ? '[data-bind="cfBody"] input' : '[data-cf="cancel"]');
  }

  /** 확인 창 닫기 — 취소 · Esc · 다른 경로 모두 여기로. 콜백을 지운다. */
  function closeConfirm(run) {
    var cb = confirmCb;
    confirmCb = null;
    confirmKey = '';
    hideModal('confirmModal');
    if (run && cb) cb();
  }

  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cf]');
    if (!b) return;
    closeConfirm(b.dataset.cf === 'ok');
  });

  /* ===================== 창 공통 (포커스 · Esc) ===================== */
  var returnFocus = {};
  var KEY_IGNORE_MS = 500;      // 창이 뜬 직후 Enter · Space 를 무시하는 시간(누르던 키가 창 단추를 누르지 않게)
  var keyIgnoreUntil = 0;
  var FOCUSABLE = 'input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),' +
    'button:not([disabled]):not([hidden]),[tabindex="0"]';

  /** 열린 창 중 맨 위(Esc 순서와 같다). 없으면 null. */
  function topModal() {
    for (var i = 0; i < ESC_ORDER.length; i++) {
      var m = d.getElementById(ESC_ORDER[i][0]);
      if (m && !m.hidden) return m;
    }
    var any = d.querySelector('.modal:not([hidden])');
    return any || null;
  }

  /** 뒤(본 화면 · 아래 창)를 inert 로 — 마우스 · Tab · 화면 읽기 모두 맨 위 창 안에만 머문다. */
  function syncInert() {
    var top = topModal();
    var app = d.getElementById('app');
    if (app) app.inert = !!top;
    Array.prototype.forEach.call(d.querySelectorAll('.modal'), function (m) {
      m.inert = !!top && m !== top;
    });
  }

  function showModal(id, focusSel) {
    var m = d.getElementById(id);
    if (!m) return;
    if (m.hidden) {
      returnFocus[id] = d.activeElement;
      keyIgnoreUntil = Date.now() + KEY_IGNORE_MS;
    }
    m.hidden = false;
    syncInert();
    var f;
    if (focusSel === 'self') {
      // 알람 창 — 창 자체에 포커스(Enter · Space 한 번에 닫히지 않게)
      f = m.querySelector('.modal-box') || m;
      if (!f.hasAttribute('tabindex')) f.setAttribute('tabindex', '-1');
    } else {
      f = (focusSel && m.querySelector(focusSel)) || m.querySelector(FOCUSABLE);
    }
    if (f) setTimeout(function () { try { f.focus(); } catch (e) { /* 없음 */ } }, 0);
  }

  function hideModal(id) {
    var m = d.getElementById(id);
    if (!m || m.hidden) return;
    m.hidden = true;
    syncInert();
    var back = returnFocus[id];
    returnFocus[id] = null;
    var top = topModal();
    // 아래에 다른 창이 남아 있으면 그 창 안으로(돌아갈 곳이 그 창 밖이면 창의 첫 칸)
    if (top && !(back && top.contains(back))) back = top.querySelector(FOCUSABLE) || top.querySelector('.modal-box');
    if (back && back.focus && d.body.contains(back)) { try { back.focus(); } catch (e) { /* 없음 */ } }
  }

  /** Tab 가두기 — 맨 위 창 안의 칸들만 돈다. */
  function trapTab(ev, top) {
    var list = Array.prototype.filter.call(top.querySelectorAll(FOCUSABLE), function (e) {
      return e.offsetParent !== null || e === d.activeElement;
    });
    if (!list.length) { ev.preventDefault(); return; }
    var first = list[0], last = list[list.length - 1];
    var inside = top.contains(d.activeElement);
    if (ev.shiftKey && (!inside || d.activeElement === first || !list.includes(d.activeElement))) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && (!inside || d.activeElement === last || !list.includes(d.activeElement))) {
      ev.preventDefault(); first.focus();
    }
  }

  // Esc — 맨 위 창부터(알람 > 확인 > 나머지)
  var ESC_ORDER = [
    ['alarmModal', function () { closeAlarm(); }],
    ['confirmModal', function () { closeConfirm(false); }],
    ['cfgModal', function () { hideModal('cfgModal'); }],
    ['pinModal', function () { hideModal('pinModal'); }],
    ['simModal', function () { hideModal('simModal'); }],
    ['manualModal', function () { hideModal('manualModal'); }]
  ];
  // 창 키보드 — 캡처 단계에서 다른 모든 keydown 처리(이름 칸 Enter 확인 등)보다 먼저
  w.addEventListener('keydown', function (ev) {
    var top = topModal();
    if (!top) return;
    if (ev.key === 'Tab') { trapTab(ev, top); return; }
    if ((ev.key === 'Enter' || ev.key === ' ') && (Date.now() < keyIgnoreUntil || ev.repeat)) {
      // 창이 막 떴다 · 키를 누르고 있다 — 누르던 키가 창 단추를 누르지 않게
      ev.preventDefault();
      ev.stopImmediatePropagation();
    }
  }, true);
  d.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') {
      for (var i = 0; i < ESC_ORDER.length; i++) {
        var m = d.getElementById(ESC_ORDER[i][0]);
        if (m && !m.hidden) { ev.preventDefault(); ESC_ORDER[i][1](); return; }
      }
      return;
    }
    // 키보드로 누를 수 있는 칸(밸브 칸 · 스위치 · 체크 칸 · 목록 항목) — Enter / Space
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches &&
        ev.target.matches('[tabindex="0"]:not(input):not(button):not(select)')) {
      ev.preventDefault();
      ev.target.click();
    }
  });

  /* ===================== 알람 창 ===================== */
  function showAlarmModal() {
    var m = d.getElementById('alarmModal');
    if (!m || !m.hidden) { renderAlarmModal(); return; }
    renderAlarmModal();
    showModal('alarmModal', 'self');
  }

  /** 알람 창 닫기. ★ 원격(보기 전용)은 명령을 보내지 않고 이 화면에서만 닫는다 —
   *  보내면 서버가 거절하고 알람 창이 곧 다시 떠, 로컬 운전자가 닫을 때까지 원격 화면이 막힌다. */
  function closeAlarm() {
    hideModal('alarmModal');
    if (canOperate()) w.app.send('alarm_popup_close');
    else {
      var lv = (lastState || {}).live || {};
      alarmDismissed = { seq: lv.alarm_popup_seq, codes: (lv.alarms || []).map(function (a) { return a.code; }) };
    }
  }

  function renderAlarmModal() {
    var t = (lastState || {}).live || {};
    var tbl = bind('amTbl');
    if (!tbl) return;
    var rows = (t.alarms || []).map(function (a) {
      return '<tr><td>' + chip(a.crit ? '중대' : '경고', a.crit ? 'stop' : 'warn') + '</td>' +
        '<td class="mono">' + esc(a.since) + '</td>' +
        '<td class="l">' + esc(a.name) + '</td></tr>';
    }).join('');
    // 서버 · PLC 가 끊겼으면 알람을 모른다 — '없습니다' 가 아니라 '알 수 없음'
    var known = !t.offline && t.plc && t.plc.connected;
    html(tbl, !known ? '<tr><td class="empty">' + (t.offline ? '서버' : 'PLC') +
      ' 연결이 끊겨 지금 알람을 알 수 없습니다</td></tr>'
      : rows || '<tr><td class="empty">현재 알람이 없습니다</td></tr>');
    // 원격은 '닫기' 만 — 확인 · 리셋은 조작이다
    var op = canOperate();
    Array.prototype.forEach.call(d.querySelectorAll('#alarmModal [data-am="ack"],#alarmModal [data-am="reset"]'),
      function (b) { b.hidden = !op; });
  }

  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-am]');
    if (!b) return;
    var a = b.dataset.am;
    if (a === 'close') {
      closeAlarm();
    } else if (a === 'ack' && canOperate()) {
      w.app.send('alarm_ack');
      w.app.send('alarm_popup_close');
    } else if (a === 'reset' && canOperate()) {
      w.app.send('alarm_reset');
      closeAlarm();
    }
  });

  /* ===================== 시뮬레이터 조작판 ===================== */
  d.addEventListener('click', function (ev) {
    if (ev.target.closest('[data-bind="simChip"]')) {
      renderSimPanel();
      showModal('simModal', '[data-sm="close"]');
      return;
    }
    if (ev.target.closest('[data-sm="close"]')) {
      hideModal('simModal');
      return;
    }
    var sw = ev.target.closest('[data-fault]');
    if (sw && !sw.classList.contains('dis') && simOperate()) {
      w.app.send('sim_fault', { key: sw.dataset.fault, on: !sw.classList.contains('on') });
    }
  });

  function renderSimPanel() {
    var box = bind('simList');
    if (!box) return;
    var list = (lastState || {}).sim_faults || [];
    var op = simOperate();
    box.innerHTML = list.map(function (f) {
      return '<div class="row2"><span>' + esc(f.name) + '</span>' +
        '<span class="sw' + (f.on ? ' on' : '') + (op ? '' : ' dis') + '" tabindex="' + (op ? 0 : -1) +
        '" role="switch" aria-checked="' + !!f.on + '" data-fault="' + esc(f.key) + '"' +
        (op ? '' : ' title="이 PC 에서만 바꿀 수 있습니다"') + '></span></div>';
    }).join('') || '<div class="empty">시뮬레이터가 아닙니다</div>';
  }

  /* ===================== 종료 ===================== */
  w.requestExitConfirm = function () {
    askExit();
    return true;
  };

  function askExit() {
    var dirty = w.viewRecipe && w.viewRecipe.dirty && w.viewRecipe.dirty();
    confirmAsk('프로그램을 종료할까요?',
      'PC 가 꺼지면 PLC 가 <b>PC 통신 끊김</b> 알람을 내고 안전 정지합니다.<br>' +
      '장비를 계속 돌려 둘 계획이면 종료하지 마세요.' +
      (dirty ? '<br><br><b>레시피 편집기에 저장하지 않은 편집이 있습니다</b> — 종료하면 사라집니다.' : ''),
      '종료', function () {
        if (!w.app.send('exit') && w.pywebview && w.pywebview.api) w.pywebview.api.force_close();
      });
  }

  /* ===================== fit ===================== */
  var CANVAS_W = 960, CANVAS_H = 1000;
  var lastScale = 0;

  function fit() {
    var w0 = w.innerWidth, h0 = w.innerHeight;
    if (!w0 || !h0) { requestAnimationFrame(fit); return; }
    var app = d.getElementById('app');
    var s = Math.min(w0 / CANVAS_W, h0 / CANVAS_H);
    var hh = Math.max(CANVAS_H, Math.min(h0 / s, CANVAS_H * 1.6));
    app.style.height = hh + 'px';
    app.style.zoom = s;
    if (Math.abs(s - lastScale) > 0.001) {
      lastScale = s;
      if (lastState && VIEWS.trend) {
        try { VIEWS.trend.render(lastState); } catch (e) { /* 탭 미표시 */ }
      }
    }
  }

  w.addEventListener('resize', fit);
  w.addEventListener('load', fit);
  if (d.fonts && d.fonts.ready) d.fonts.ready.then(fit);

  /* ===================== 공개 ===================== */
  // ★ 다른 장비의 PLC 에 붙어 있거나 서버가 끊겼으면 이 PC 에서도 조작하지 못한다
  /** 시뮬레이터 조작판 — 시뮬레이터 모드 · 이 PC 면 늘 쓸 수 있다. ★ PLC STOP 결함으로 화면이
   *  잠겨도(하트비트 멈춤) 조작판까지 잠기면 STOP 을 풀 수 없다. */
  function simOperate() {
    return !offline && !!(lastState && (lastState.access || {}).local) && !!(lastState.sim_faults || []).length;
  }

  function canOperate() {
    if (offline) return false;
    if (!(lastState && (lastState.access || {}).local)) return false;
    var p = (lastState.live || {}).plc;
    return !idBlocked(p) && !(p && p.stalled);
  }

  w.core = {
    bind: bind, esc: esc, h: h, html: html, setText: setText, chip: chip, bit: bit,
    toast: toast, setTab: setTab, fit: fit, confirmAsk: confirmAsk, closeConfirm: closeConfirm,
    showModal: showModal, hideModal: hideModal, setOffline: setOffline,
    get offline() { return offline; },
    askExit: askExit, renderSimPanel: renderSimPanel, renderAlarmModal: renderAlarmModal,
    applyState: applyState, applyLive: applyLive,
    get state() { return lastState; },
    get tab() { return curTab; },
    /** 늦게 등록된 뷰에도 이미 받은 상태를 바로 먹여 준다.
     *  스크립트 태그는 각각 따로 받아 실행되므로, views/*.js 를 받는 사이에
     *  WebSocket 의 첫 state 가 도착할 수 있다(로컬호스트에서는 흔하다). */
    register: function (name, mod) {
      VIEWS[name] = mod;
      if (!lastState) return;
      try {
        mod.render(lastState);
        if (lastState.live) mod.update(lastState.live);
      } catch (e) { console.error('late render ' + name, e); }
    },
    canOperate: canOperate,
    /** 이 PC(로컬) 접속인가 — 종료 단추처럼 PLC 와 무관한 것만 이걸로 판단한다 */
    isLocal: function () { return !offline && !!(lastState && (lastState.access || {}).local); },
    /** 마지막으로 받은 권한이 이 PC 였는가 — 서버가 끊겨도 '프로그램 종료'(force_close)는 누를 수 있게 */
    wasLocal: function () { return !!(lastState && (lastState.access || {}).local); },
    scanText: scanText,
    /** 끊김을 말하는 글자 — 서버가 끊겼으면 '서버 끊김'(운전자가 PLC 를 보러 가지 않게) */
    downText: function () {
      if (offline) return '서버 끊김';
      var p = ((lastState || {}).live || {}).plc || {};
      return p.stalled ? 'PLC 하트비트 멈춤' : 'PLC 끊김';
    },
    idText: idText,
    idBlocked: idBlocked,
    plcOk: function () {
      var t = (lastState || {}).live || {};
      return !offline && !!(t.plc && t.plc.connected);
    }
  };

  fit();
})(window, document);
