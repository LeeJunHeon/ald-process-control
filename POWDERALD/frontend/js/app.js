/* ============================================================
 * app.js — 서버 통신 한 곳.
 *
 * ★ 서버로 나가는 모든 메시지는 send() 하나를 지나간다. 여러 곳에서 직접 ws.send 를
 *   부르면 연결이 끊겼을 때 어떤 명령이 사라졌는지 알 수 없다.
 *
 * 연결이 끊기면 값을 지어내지 않는다 — '—' 로 비우고 2초마다 다시 붙는다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var ws = null, connected = false, reconnectTimer = null;
  var handlers = {};

  function connect() {
    try {
      ws = new WebSocket('ws://' + location.host + '/ws');
    } catch (e) {
      scheduleReconnect();
      return;
    }
    ws.onopen = function () { connected = true; };
    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      dispatch(msg);
    };
    ws.onclose = onDisconnect;
    ws.onerror = function () { try { ws.close(); } catch (e) { /* 이미 닫힘 */ } };
  }

  function onDisconnect() {
    if (connected) core.toast('서버 연결이 끊겼습니다 — 다시 연결합니다', 'warn');
    connected = false;
    var dot = core.bind('sbDot');
    if (dot) dot.classList.add('off');
    core.setText('sbText', '서버 연결 끊김 — 표시된 값은 과거 값입니다');
    scheduleReconnect();
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(function () { reconnectTimer = null; connect(); }, 2000);
  }

  function dispatch(msg) {
    switch (msg && msg.type) {
      case 'state':  core.applyState(msg); break;
      case 'live':   core.applyLive(msg); break;
      case 'notice': core.toast(msg.msg, msg.level); break;
      case 'log':
        if (core.state) {
          (core.state.logs = core.state.logs || []).push({ ts: msg.ts, level: msg.level, msg: msg.msg });
          core.state.logs.splice(0, Math.max(0, core.state.logs.length - 500));
          if (w.viewAlarm) w.viewAlarm.appendLog(msg);
        }
        break;
      default:
        if (handlers[msg.type]) handlers[msg.type](msg);
    }
  }

  function send(cmd, payload) {
    if (!ws || !connected) {
      core.toast('서버에 연결되어 있지 않습니다', 'warn');
      return false;
    }
    var obj = { cmd: cmd };
    for (var k in (payload || {})) obj[k] = payload[k];
    try { ws.send(JSON.stringify(obj)); return true; }
    catch (e) { return false; }
  }

  w.app = {
    send: send,
    on: function (type, fn) { handlers[type] = fn; },
    get connected() { return connected; }
  };

  // ★ 스크립트 파싱 중에 바로 연결하지 않는다. 뷰 모듈(views/*.js)이 아직 로드 중일 때
  //   첫 state 가 도착하면 그 뷰들이 상태를 놓친다. 문서가 다 준비된 뒤에 연결한다.
  //   (core.register 에도 늦게 등록된 뷰를 구제하는 장치를 함께 뒀다 — 이중 안전장치)
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', connect);
  else connect();
})(window, document);
