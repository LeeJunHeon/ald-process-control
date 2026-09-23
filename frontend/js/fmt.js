/* ============================================================
 * fmt.js — 숫자 · 단위 · 시간 포맷.
 *
 * ★ 숫자 포맷은 이 파일에서만 한다. 같은 값이 탭마다 다른 자릿수로 보이면
 *   운전자가 "어느 쪽이 맞나"를 판단할 수 없다.
 *
 * 규칙
 *   압력  : 1 Torr 미만 → 소수 3자리, 0.01 미만 → 지수표기 (1.8E-2)
 *   온도  : 소수 1자리    유량 : 소수 1자리
 *   시간  : hh:mm:ss
 *   값 없음(null / undefined / NaN) : '—'  ← 0 과 반드시 구분한다.
 * ============================================================ */
(function (w) {
  'use strict';

  var DASH = '—';   // em dash

  function isNil(v) { return v === null || v === undefined || v === '' || (typeof v === 'number' && !isFinite(v)); }

  function num(v, digits) {
    if (isNil(v)) return DASH;
    var n = Number(v);
    return isFinite(n) ? n.toFixed(digits === undefined ? 1 : digits) : DASH;
  }

  /** 압력. 작은 값은 지수표기로 바꿔야 1.8E-2 와 0.018 이 같은 폭으로 읽힌다. */
  function torr(v) {
    if (isNil(v)) return DASH;
    var n = Number(v);
    if (!isFinite(n)) return DASH;
    if (n >= 1) return n.toFixed(2);
    if (n >= 0.1) return n.toFixed(3);
    var e = n.toExponential(1).split('e');
    return e[0] + 'E' + (Number(e[1]) >= 0 ? '+' : '-') + Math.abs(Number(e[1]));
  }

  function temp(v) { return num(v, 1); }
  function flow(v) { return num(v, 1); }
  function pct(v) { return isNil(v) ? DASH : Math.round(Number(v)) + ''; }

  /** 초 → hh:mm:ss */
  function hms(sec) {
    if (isNil(sec)) return DASH;
    var s = Math.max(0, Math.floor(Number(sec)));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return pad(h) + ':' + pad(m) + ':' + pad(s % 60);
  }

  /** 초 → h:mm:ss (레시피 소요 시간처럼 앞자리를 채우지 않는 표기) */
  function dur(sec) {
    if (isNil(sec)) return DASH;
    var s = Math.max(0, Math.floor(Number(sec)));
    return Math.floor(s / 3600) + ':' + pad(Math.floor((s % 3600) / 60)) + ':' + pad(s % 60);
  }

  /** 스텝 시간처럼 소수가 의미 있는 초 표기 */
  function sec(v, digits) {
    if (isNil(v)) return DASH;
    return Number(v).toFixed(digits === undefined ? 2 : digits);
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  /** 시각(초 단위 x축 라벨) */
  function clockAt(ms) {
    var d = new Date(ms);
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  w.fmt = { DASH: DASH, isNil: isNil, num: num, torr: torr, temp: temp, flow: flow,
            pct: pct, hms: hms, dur: dur, sec: sec, pad: pad, clockAt: clockAt };
})(window);
