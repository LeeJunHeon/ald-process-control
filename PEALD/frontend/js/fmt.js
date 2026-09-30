/* ============================================================
 * fmt.js — 숫자 · 단위 · 시간 포맷.
 *
 * ★ 숫자 포맷은 이 파일에서만 한다. 같은 값이 탭마다 다른 자릿수로 보이면
 *   운전자가 "어느 쪽이 맞나"를 판단할 수 없다.
 *
 * 규칙
 *   압력  : 1 Torr 이상 → 소수 2자리 (759.67), 0.1 이상 → 소수 3자리 (0.500),
 *           그 미만 → 지수표기 (1.8E-2), 0 → '0'.
 *           진공 영역은 자릿수 자체가 정보라서 지수표기가 읽기 쉽다.
 *           서버가 만드는 압력 글자(process._torr)도 같은 규칙이다.
 *   온도·유량·전력 : 소수 1자리    개도 : 정수 %    온도 기호는 ℃ 하나만 쓴다
 *   시간  : hh:mm:ss    ms → 초 : fmt.ms
 *   값 없음(null / undefined / NaN) : '—'  ← 0 과 반드시 구분한다.
 *           PLC 가 끊겼을 때 0 을 보여 주면 "유량이 0"으로 오해한다.
 * ============================================================ */
(function (w) {
  'use strict';
  var DASH = '\u2014';

  function isNil(v) {
    return v === null || v === undefined || v === ''
      || (typeof v === 'number' && !isFinite(v));
  }

  function num(v, digits) {
    if (isNil(v)) return DASH;
    var n = Number(v);
    return isFinite(n) ? n.toFixed(digits === undefined ? 1 : digits) : DASH;
  }

  function torr(v) {
    if (isNil(v)) return DASH;
    var n = Number(v);
    if (!isFinite(n)) return DASH;
    if (n === 0) return '0';
    if (n >= 1) return n.toFixed(2);
    if (n >= 0.1) return n.toFixed(3);
    var e = n.toExponential(1).split('e');
    return e[0] + 'E' + (Number(e[1]) >= 0 ? '+' : '-') + Math.abs(Number(e[1]));
  }

  function temp(v) { return num(v, 1); }
  function flow(v) { return num(v, 1); }
  function watt(v) { return num(v, 1); }
  function pct(v) { return isNil(v) ? DASH : Math.round(Number(v)) + ''; }
  function int(v) { return isNil(v) ? DASH : String(Math.round(Number(v))); }

  function hms(sec) {
    if (isNil(sec)) return DASH;
    var s = Math.max(0, Math.floor(Number(sec)));
    return pad(Math.floor(s / 3600)) + ':' + pad(Math.floor((s % 3600) / 60)) + ':' + pad(s % 60);
  }

  /** PLC 가 주는 ms 를 초.소수 로 */
  function ms(v, digits) {
    if (isNil(v)) return DASH;
    var n = Number(v);
    if (!isFinite(n)) return DASH;          // 'abc' · NaN 은 '—' (NaN 글자를 내보내지 않는다)
    return (n / 1000).toFixed(digits === undefined ? 1 : digits);
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function clockAt(msv) {
    var d = new Date(msv);
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  /** 16비트 워드를 0x0000 형태로 (설정·진단 표시용) */
  function hex16(v) { return isNil(v) ? DASH : '0x' + (Number(v) & 0xFFFF).toString(16).toUpperCase().padStart(4, '0'); }

  w.fmt = { DASH: DASH, isNil: isNil, num: num, torr: torr, temp: temp, flow: flow,
            watt: watt, pct: pct, int: int, hms: hms, ms: ms, pad: pad,
            clockAt: clockAt, hex16: hex16 };
})(window);
