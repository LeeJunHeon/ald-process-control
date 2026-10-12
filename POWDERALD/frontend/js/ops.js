/* ============================================================
 * ops.js — 수동 조작 명령 만들기 한 곳 (v0.5.0).
 *
 * ★ 「수동 조작…」 창(manual.js)과 배관도 조작 창(faceplate.js)이 같은 명령 · 같은 인자를 보내도록
 *   명령은 여기서만 만든다(새 명령 · 새 판단을 만들지 않는다 — 판단은 서버와 PLC 가 한다).
 *   반환은 [명령, 인자] — Ops.send 가 app.send 로 보낸다.
 * ★ blockReason 은 조작을 막는 이유(문장 조각, 한글)를 정한다 — 공정 중 · 원격 보기 · PLC 끊김 ·
 *   하트비트 멈춤 · 다른 장비 · 서버 끊김. 조작 창은 열리되 단추는 막히고 이 이유가 보인다.
 * ============================================================ */
(function (root) {
  'use strict';

  function one(k, v) { var o = {}; o[k] = v; return o; }

  var Ops = {
    valve: function (tag, on) { return ['manual_valve', { tag: tag, on: !!on }]; },
    unlock: function (on) { return ['manual_unlock', { on: !!on }]; },
    mfcMany: function (map) { return ['manual_mfc', { sccm: map }]; },
    mfc: function (no, sccm) { return Ops.mfcMany(one(String(no), Number(sccm))); },
    heaterSvMany: function (map) { return ['manual_heater', { sv: map }]; },
    heaterSv: function (ch, sv) { return Ops.heaterSvMany(one(String(ch), Number(sv))); },
    heaterPower: function (ch, on) { return ['manual_heater', { power: one(String(ch), !!on) }]; },
    pcv: function (pct) { return ['manual_pcv', { pct: Number(pct) }]; },
    rfOn: function (watt) { return ['manual_rf', { on: true, watt: Number(watt) }]; },
    rfOff: function () { return ['manual_rf', { on: false }]; },
    o3: function (action, value) {
      return action === 'off' ? ['manual_o3', { action: 'off' }]
        : ['manual_o3', { action: action, value: Number(value) }];
    },
    /** 펌핑 시작 · 정지 · 벤트 — 명령줄 단추와 같은 명령(인자 없음) */
    plain: function (cmd) { return [cmd]; },

    send: function (pair) {
      if (!root.app) return false;
      return pair.length > 1 ? root.app.send(pair[0], pair[1]) : root.app.send(pair[0]);
    },

    /** 지금 상태 → 막는 이유 판단에 쓰는 것만. */
    ctx: function (state, offline) {
      var s = state || {}, t = s.live || {}, p = t.plc || {};
      return {
        offline: !!(offline || t.offline), local: !!(s.access || {}).local,
        connected: !!p.connected, stalled: !!p.stalled, idState: p.id_state || '',
        configError: !!p.config_error, running: !!((t.process || {}).running)
      };
    },

    /** 조작을 막는 이유 — 없으면 ''. 순서는 core.lockReason · plcReason 과 같다(+ 공정 중). */
    blockReason: function (c) {
      if (c.offline) return '서버 연결 끊김';
      if (!c.local) return '원격 접속 — 보기 전용';
      if (c.idState === 'wrong') return '다른 장비의 PLC';
      if (c.idState === 'missing') return 'PLC 장비 ID 없음';
      if (c.stalled) return 'PLC 하트비트 멈춤';
      if (c.configError) return 'PLC 주소 없음';
      if (!c.connected) return 'PLC 끊김';
      if (c.running) return '공정 중';
      return '';
    }
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = Ops;
  else root.Ops = Ops;
})(typeof window !== 'undefined' ? window : this);
