/* ============================================================
 * core.js — 상태 수신 → 뷰 디스패치, 탭, fit(), 토스트, 확인 창, 알람 창.
 *
 * 뷰 모듈은 전부 render(state) / update(live) 두 함수 형태로 통일한다.
 *   render : 구조가 바뀔 때(접속·설정 변경) 한 번. DOM 을 다시 만든다.
 *   update : 5 Hz. 이미 만들어진 DOM 의 값만 바꾼다. 여기서 DOM 을 새로 만들면
 *            초당 5회 레이아웃이 다시 계산돼 화면이 눈에 띄게 버벅인다.
 *
 * DOM 연결은 data-bind 속성으로만 한다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var VIEWS = {};
  var lastState = null;
  var curTab = 'main';
  var confirmCb = null;

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
    applyDevice(s);
    for (var k in VIEWS) {
      try { VIEWS[k].render(s); } catch (e) { console.error('render ' + k, e); }
    }
    if (s.live) applyLive(s.live);
  }

  function applyLive(t) {
    if (!lastState) return;
    lastState.live = t;
    setText('clock', t.clock || fmt.DASH);
    setText('date', t.date || fmt.DASH);
    applyHeader(lastState, t);
    for (var k in VIEWS) {
      try { VIEWS[k].update(t); } catch (e) { console.error('update ' + k, e); }
    }
    if (t.alarm_popup) showAlarmModal();
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
    var pill = bind('stateChip');
    if (pill) {
      if (!t.plc || !t.plc.connected) {
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
      ac.className = 'chip ' + (crit ? 'stop' : alarms.length ? 'warn' : '');
      ac.textContent = alarms.length
        ? '⚠ ' + (crit ? '중대 ' : '경고 ') + alarms.length + '건'
        : '알람 없음';
    }
    var plc = t.plc || {};
    var dot = bind('sbDot');
    if (dot) dot.classList.toggle('off', !plc.connected);
    setText('sbText', [
      'PLC ' + (plc.addr || fmt.DASH) + (plc.connected ? ' 연결됨' : ' 연결 안 됨'),
      plc.connected ? (plc.hb_ok ? '하트비트 정상' : '하트비트 멈춤') : '',
      plc.connected && plc.rtt_ms != null ? '응답 ' + plc.rtt_ms + ' ms' : '',
      t.scan_max_ms != null ? '최대 스캔 ' + t.scan_max_ms + ' ms' : ''
    ].filter(Boolean).join(' · '));
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
    setText('cfTitle', title);
    var b = bind('cfBody');
    if (b) b.innerHTML = body;
    var ok = d.querySelector('[data-cf="ok"]');
    if (ok) ok.textContent = okLabel || '실행';
    confirmCb = cb;
    d.getElementById('confirmModal').hidden = false;
  }

  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cf]');
    if (!b) return;
    d.getElementById('confirmModal').hidden = true;
    var cb = confirmCb;
    confirmCb = null;
    if (b.dataset.cf === 'ok' && cb) cb();
  });

  /* ===================== 알람 창 ===================== */
  function showAlarmModal() {
    var m = d.getElementById('alarmModal');
    if (!m || !m.hidden) { renderAlarmModal(); return; }
    m.hidden = false;
    renderAlarmModal();
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
    tbl.innerHTML = rows || '<tr><td class="empty">현재 알람이 없습니다</td></tr>';
  }

  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-am]');
    if (!b) return;
    var a = b.dataset.am;
    if (a === 'close') {
      d.getElementById('alarmModal').hidden = true;
      w.app.send('alarm_popup_close');
    } else if (a === 'ack') {
      w.app.send('alarm_ack');
      w.app.send('alarm_popup_close');
    } else if (a === 'reset') {
      w.app.send('alarm_reset');
      w.app.send('alarm_popup_close');
      d.getElementById('alarmModal').hidden = true;
    }
  });

  /* ===================== 시뮬레이터 조작판 ===================== */
  d.addEventListener('click', function (ev) {
    if (ev.target.closest('[data-bind="simChip"]')) {
      renderSimPanel();
      d.getElementById('simModal').hidden = false;
      return;
    }
    if (ev.target.closest('[data-sm="close"]')) {
      d.getElementById('simModal').hidden = true;
      return;
    }
    var sw = ev.target.closest('[data-fault]');
    if (sw) {
      w.app.send('sim_fault', { key: sw.dataset.fault, on: !sw.classList.contains('on') });
    }
  });

  function renderSimPanel() {
    var box = bind('simList');
    if (!box) return;
    var list = (lastState || {}).sim_faults || [];
    box.innerHTML = list.map(function (f) {
      return '<div class="row2"><span>' + esc(f.name) + '</span>' +
        '<span class="sw' + (f.on ? ' on' : '') + '" data-fault="' + esc(f.key) + '"></span></div>';
    }).join('') || '<div class="empty">시뮬레이터가 아닙니다</div>';
  }

  /* ===================== 종료 ===================== */
  w.requestExitConfirm = function () {
    askExit();
    return true;
  };

  function askExit() {
    confirmAsk('프로그램을 종료할까요?',
      'PC 가 꺼지면 PLC 가 <b>PC 통신 끊김</b> 알람을 내고 안전 정지합니다.<br>' +
      '장비를 계속 돌려 둘 계획이면 종료하지 마세요.',
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
  w.core = {
    bind: bind, esc: esc, h: h, setText: setText, chip: chip, bit: bit,
    toast: toast, setTab: setTab, fit: fit, confirmAsk: confirmAsk,
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
    canOperate: function () { return !!(lastState && (lastState.access || {}).local); },
    plcOk: function () {
      var t = (lastState || {}).live || {};
      return !!(t.plc && t.plc.connected);
    }
  };

  fit();
})(window, document);
