/* ============================================================
 * core.js — 상태 수신 → 뷰 디스패치, 탭, fit(), 토스트, 종료 모달.
 *
 * 뷰 모듈은 전부 render(state) / update(telemetry) 두 함수 형태로 통일한다.
 *   render : 구조가 바뀔 때(접속·설정 변경·레시피 목록 변경) 한 번. DOM을 다시 만든다.
 *   update : 5 Hz. 이미 만들어진 DOM의 값만 바꾼다. 여기서 DOM을 새로 만들면
 *            초당 5회 레이아웃이 다시 계산돼 화면이 눈에 띄게 버벅인다.
 *
 * DOM 연결은 data-bind 속성으로만 한다(id 를 흩뿌리지 않는다).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var VIEWS = {};              // 탭 이름 → 뷰 모듈
  var lastState = null;
  var curTab = 'main';

  /* ===================== DOM 도우미 ===================== */
  function bind(name, root) { return (root || d).querySelector('[data-bind="' + name + '"]'); }
  function binds(name, root) { return Array.prototype.slice.call((root || d).querySelectorAll('[data-bind="' + name + '"]')); }

  /** 서버·파일에서 온 문자열(레시피 이름, 메모, 로그)을 HTML에 넣을 때 쓴다.
   *  ★ innerHTML 에 그대로 넣으면 레시피 이름 하나로 화면이 깨진다. */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** 간단한 엘리먼트 생성기. attrs 의 text 는 textContent 로 들어간다(이스케이프 불필요). */
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

  /** 칩 HTML. 등급별 색은 tokens.css 의 --ok/--warn/--stop/--info 가 정한다. */
  function chip(text, level, dot) {
    return '<span class="chip ' + (level || 'neutral') + '">' +
      (dot ? '<span class="dot"></span>' : '') + esc(text) + '</span>';
  }

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
    applyChamber(s);
    for (var k in VIEWS) {
      try { VIEWS[k].render(s); } catch (e) { console.error('render ' + k, e); }
    }
    if (s.live) applyTelemetry(s.live);
  }

  function applyTelemetry(t) {
    if (!lastState) return;
    lastState.live = t;
    // 헤더·상태줄은 어느 탭에서도 보이므로 항상 갱신한다.
    setText('clock', t.clock || fmt.DASH);
    setText('date', t.date || fmt.DASH);
    applyHeaderState(lastState, t);
    for (var k in VIEWS) {
      try { VIEWS[k].update(t); } catch (e) { console.error('update ' + k, e); }
    }
  }

  /** 테마·배지·부제 등 접속 시 한 번만 정해지는 것들. */
  function applyChamber(s) {
    var c = s.chamber || {};
    d.documentElement.setAttribute('data-theme', c.theme === 'dark' ? 'dark' : 'light');
    d.documentElement.style.setProperty('--accent', c.accent || '');
    d.title = (c.name ? c.name + ' · ' : '') + (c.app_name || 'ALD Process Control');
    setText('chamberBadge', c.name || 'ALD');
    setText('chamberSub', [c.subtitle, c.plc_addr ? 'PLC ' + c.plc_addr : ''].filter(Boolean).join(' · ') || fmt.DASH);
    setText('sbVer', 'v' + (c.app_version || '') + ((s.demo || {}).enabled ? '  (데모)' : ''));
    var demo = bind('demoChip');
    if (demo) demo.hidden = !(s.demo || {}).enabled;
    var acc = bind('access');
    if (acc) {
      var local = (s.access || {}).local;
      acc.textContent = '조작 권한: ' + (local ? '이 PC' : '보기 전용');
      acc.classList.toggle('remote', !local);
    }
  }

  var STATE_PILL = {
    running: ['공정 중', 'ok'], paused: ['일시정지', 'warn'],
    stopping: ['정지 예정', 'warn'], idle: ['대기', 'neutral']
  };

  function applyHeaderState(s, t) {
    var p = t.process || {};
    var m = STATE_PILL[p.mode] || STATE_PILL.idle;
    var pill = bind('statePill');
    if (pill) {
      pill.className = 'chip ' + m[1];
      setText('statePillText', p.stop_after_cycle && p.mode === 'running' ? '사이클 후 정지' : m[0]);
    }
    var alarms = t.alarms || [];
    var ac = bind('alarmChip');
    if (ac) {
      var sev = alarms.some(function (a) { return a.level === 'err'; }) ? 'stop'
        : alarms.length ? 'warn' : 'neutral';
      ac.className = 'chip ' + sev;
      ac.textContent = alarms.length ? '⚠ ' + (sev === 'stop' ? '중대 ' : '경고 ') + alarms.length : '알람 없음';
    }
    // 하단 상태줄
    var plc = t.plc || {};
    var dot = bind('sbDot');
    if (dot) dot.classList.toggle('off', !plc.connected);
    setText('sbText', [
      'PLC ' + ((s.chamber || {}).plc_addr || fmt.DASH) + (plc.connected ? ' 연결됨' : ' 연결 안 됨'),
      plc.hb_ok ? '하트비트 정상' : '하트비트 두절',
      p.mode === 'running' ? '데이터 로그 기록 중' : '데이터 로그 대기'
    ].join(' · '));
  }

  /* ===================== 토스트 ===================== */
  function toast(msg, level) {
    var box = d.getElementById('toasts');
    if (!box) return;
    var el = h('div', { cls: 'toast ' + (level || 'info'), text: msg });
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, 3600);
  }

  /* ===================== 종료 모달 ===================== */
  function askExit() { d.getElementById('exitModal').hidden = false; }
  function closeExit() { d.getElementById('exitModal').hidden = true; }

  // 창 X 버튼 → window.py 의 closing 핸들러가 이 함수를 부른다.
  w.requestExitConfirm = function () { askExit(); return true; };

  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cmd]');
    if (!b) return;
    var c = b.dataset.cmd;
    if (c === 'exit') { askExit(); return; }
    if (c === 'exit_cancel') { closeExit(); return; }
    if (c === 'exit_confirm') {
      closeExit();
      // 서버가 살아 있으면 정상 경로로, 아니면 pywebview 직통 브리지로 종료한다.
      if (!w.app.send('exit') && w.pywebview && w.pywebview.api) w.pywebview.api.force_close();
    }
  });

  /* ===================== fit ===================== */
  // 디자인 캔버스 960 × 1000. 창 크기에 맞춰 비율을 유지한 채 확대·축소한다.
  var CANVAS_W = 960, CANVAS_H = 1000;
  var lastScale = 0;

  function fit() {
    var w0 = w.innerWidth, h0 = w.innerHeight;
    if (!w0 || !h0) { requestAnimationFrame(fit); return; }
    var app = d.getElementById('app');
    // 가로·세로 중 빡빡한 쪽 기준으로 균일 축소(왜곡 없음).
    var s = Math.min(w0 / CANVAS_W, h0 / CANVAS_H);
    // 세로가 남으면 캔버스를 늘려 빈 띠를 없앤다(창이 세로로 긴 반쪽 배치가 기본이다).
    var hh = Math.max(CANVAS_H, Math.min(h0 / s, CANVAS_H * 1.6));
    app.style.height = hh + 'px';
    app.style.zoom = s;
    if (Math.abs(s - lastScale) > 0.001) {
      lastScale = s;
      // 배율이 바뀌면 캔버스 차트의 픽셀 크기가 달라진다 → 다시 그린다.
      if (lastState) { try { VIEWS.trend.render(lastState); } catch (e) { /* 탭 미표시 */ } }
    }
  }

  w.addEventListener('resize', fit);
  w.addEventListener('load', fit);
  if (d.fonts && d.fonts.ready) d.fonts.ready.then(fit);

  /* ===================== 공개 ===================== */
  w.core = {
    bind: bind, binds: binds, esc: esc, h: h, setText: setText, chip: chip,
    toast: toast, setTab: setTab, fit: fit,
    applyState: applyState, applyTelemetry: applyTelemetry,
    get state() { return lastState; },
    get tab() { return curTab; },
    /** 뷰 등록.
     * ★ 늦게 등록된 뷰에도 이미 받은 상태를 바로 먹여 준다.
     *   스크립트 태그는 각각 따로 받아 실행되므로, views/*.js 를 받는 사이에
     *   WebSocket 의 첫 state 가 도착할 수 있다(로컬호스트에서는 흔하다).
     *   그때 등록만 하고 끝내면 그 뷰는 다음 구조 변경까지 영영 빈 화면으로 남는다.
     */
    register: function (name, mod) {
      VIEWS[name] = mod;
      if (!lastState) return;
      try {
        mod.render(lastState);
        if (lastState.live) mod.update(lastState.live);
      } catch (e) { console.error('late render ' + name, e); }
    },
    /** 조작 가능 여부 — 원격(보기 전용)이거나 공정 중이면 수동 조작이 잠긴다.
     *  ★ 화면 잠금은 편의일 뿐이다. 실제 차단은 서버(commands.py)가 한다. */
    canOperate: function () { return !!(lastState && (lastState.access || {}).local); },
    isRunning: function () {
      var m = ((lastState || {}).live || {}).process || {};
      return m.mode === 'running' || m.mode === 'paused' || m.mode === 'stopping';
    }
  };

  fit();
})(window, document);
