/* ============================================================
 * views/trend.js — 트렌드 탭. 외부 라이브러리 없이 canvas 로 직접 그린다.
 *
 * 공통 렌더러 draw() 하나로 세 차트를 전부 그린다. 차트마다 그리기 코드를 복사하면
 * 축·라벨 규칙이 조금씩 갈라져 결국 다른 차트처럼 보인다.
 *
 * 차트 규칙 (이 파일이 지키는 약속)
 *   - 플롯 안에는 글자를 두지 않는다. 범례는 제목 띠(legend-inline)에 둔다.
 *   - 현재값 라벨은 플롯 오른쪽 바깥 띠에 두고 지시선으로 선 끝과 잇는다.
 *     라벨끼리 겹치면 위아래로 벌린다(spread()).
 *   - 한 차트에 y축은 하나만 쓴다.
 *   - 시리즈 색은 tokens.css 의 --series-1..6 순서로 고정한다.
 *
 * 이력은 GET /api/trend 로 한 번 받고, 이후는 telemetry 로 이어 붙인다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var PAD_L = 46, PAD_R = 178, PAD_T = 12, PAD_B = 30, PULSE_H = 16;
  var LABEL_VAL_W = 58;    // 값+단위가 쓰는 폭. 이름은 그 앞까지만 쓴다(겹침 방지).
  var MAX_SERIES = 6;
  var LABEL_H = 13;        // 오른쪽 값 라벨 한 줄 높이(겹침 판정 기준)

  var rangeSec = 120;
  var autoScroll = true;
  var charts = {};         // key -> {canvas, ctx, series:[{key,label,color,unit,pts}]}
  var pulses = [];         // {t, name}
  var lastStepKey = '';
  var hover = null;        // {chart, x}
  var seeded = false;

  /* ===================== render ===================== */
  function render(s) {
    buildSeries(s);
    seed();
    drawAll();
  }

  function buildSeries(s) {
    var def = {
      p: [{ key: 'baratron', label: 'Baratron', unit: 'Torr' }],
      t: [], m: []
    };
    // 온도: 설정된 히터 중 켜진 것부터 최대 6개. 순서가 색 순서다.
    (s.heaters || []).forEach(function (h) {
      if (!h.enabled || def.t.length >= MAX_SERIES) return;
      def.t.push({ key: h.id, label: h.label, unit: '°C' });
    });
    // MFC: 장착된 라인 최대 6개.
    (s.lines || []).forEach(function (l) {
      if (!l.enabled || def.m.length >= MAX_SERIES) return;
      def.m.push({ key: l.id, label: l.id + (l.kind === 'n2' ? '' : ' 캐리어'), unit: 'sccm' });
    });

    ['p', 't', 'm'].forEach(function (k) {
      var cv = d.querySelector('[data-chart="' + k + '"]');
      if (!cv) return;
      var old = (charts[k] || {}).series || [];
      charts[k] = {
        canvas: cv, ctx: cv.getContext('2d'),
        series: def[k].map(function (sd, i) {
          var prev = old.filter(function (o) { return o.key === sd.key; })[0];
          return { key: sd.key, label: sd.label, unit: sd.unit,
                   color: cssVar('--series-' + (i + 1)), pts: (prev && prev.pts) || [] };
        })
      };
      legend(k);
    });
  }

  function legend(k) {
    var box = core.bind({ p: 'lgP', t: 'lgT', m: 'lgM' }[k]);
    if (!box) return;
    var extra = k === 'p' ? '<span><i style="background:' + cssVar('--series-2') + '"></i>전구체 펄스</span>' +
      '<span><i style="background:' + cssVar('--series-3') + '"></i>반응물 펄스</span>' : '';
    box.innerHTML = charts[k].series.map(function (se) {
      return '<span><i style="background:' + se.color + '"></i>' + core.esc(se.label) + '</span>';
    }).join('') + extra;
  }

  function cssVar(n) { return getComputedStyle(d.documentElement).getPropertyValue(n).trim(); }

  /* ---------- 이력 seeding ---------- */
  function seed() {
    if (seeded) return;
    seeded = true;
    refill();
  }

  function refill() {
    fetch('api/trend?sec=' + rangeSec).then(function (r) { return r.json(); }).then(function (js) {
      // 서버는 monotonic 시계를 쓴다 — 화면의 벽시계로 옮긴다.
      var base = Date.now() - js.now * 1000;
      ['p', 't', 'm'].forEach(function (k) { (charts[k] || { series: [] }).series.forEach(function (se) { se.pts = []; }); });
      (js.slow || []).forEach(function (r) {
        var ms = base + r.t * 1000;
        push('t', r.h || {}, ms);
        push('m', r.m || {}, ms);
      });
      // 압력은 10 Hz 버퍼를 쓴다(0.1 s 펄스가 보이도록).
      (js.fast || []).forEach(function (r) {
        pt('p', 'baratron', base + r.t * 1000, r.p);
      });
      pulses = (js.pulses || []).map(function (x) { return { t: base + x.t * 1000, name: x.line }; });
      drawAll();
    }).catch(function () { /* 이력이 없어도 telemetry 로 계속 그린다 */ });
  }

  function push(chart, obj, ms) {
    (charts[chart] || { series: [] }).series.forEach(function (se) { pt(chart, se.key, ms, obj[se.key]); });
  }

  function pt(chart, key, ms, v) {
    var c = charts[chart];
    if (!c || v == null) return;
    var se = c.series.filter(function (x) { return x.key === key; })[0];
    if (!se) return;
    se.pts.push([ms, Number(v)]);
    // 1시간 × 10 Hz 를 넘지 않게 잘라낸다(메모리 상한을 처음부터 정한다).
    var cut = ms - 3600000;
    while (se.pts.length && se.pts[0][0] < cut) se.pts.shift();
  }

  /* ===================== update ===================== */
  function update(t) {
    var ms = Date.now();
    pt('p', 'baratron', ms, (t.gauges || {}).baratron);
    push('t', mapPv(t.heaters), ms);
    push('m', mapPv(t.mfc), ms);

    // 펄스 표시 띠 — 스텝이 바뀌는 순간 짧은 스텝만 기록한다.
    var p = t.process || {};
    var key = [p.block, p.cycle, p.step].join(':');
    if (p.mode === 'running' && key !== lastStepKey) {
      lastStepKey = key;
      if ((p.step_total_s || 0) <= 1.0 && p.step_name) pulses.push({ t: ms, name: p.step_name });
      if (pulses.length > 3000) pulses.splice(0, pulses.length - 3000);
    }
    core.setText('trendInfo', p.mode === 'running'
      ? '사이클 ' + (p.cycle || 0) + ' / ' + (p.cycles || 0) + '  ·  ' + (t.clock || '')
      : '대기  ·  ' + (t.clock || ''));
    if (core.tab === 'trend') drawAll();
  }

  function mapPv(obj) {
    var o = {};
    for (var k in (obj || {})) o[k] = (obj[k] || {}).pv;
    return o;
  }

  /* ===================== 그리기 ===================== */
  function drawAll() { ['p', 't', 'm'].forEach(draw); }

  function draw(key) {
    var c = charts[key];
    if (!c || !c.canvas.clientWidth) return;
    var dpr = w.devicePixelRatio || 1;
    var W = c.canvas.clientWidth, H = c.canvas.clientHeight;
    c.canvas.width = W * dpr; c.canvas.height = H * dpr;
    var g = c.ctx;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    var strip = key === 'p' ? PULSE_H : 0;
    var x0 = PAD_L, x1 = W - PAD_R, y0 = PAD_T, y1 = H - PAD_B - strip;
    if (x1 <= x0 || y1 <= y0) return;

    var now = Date.now(), tMin = now - rangeSec * 1000, tMax = now;
    var rng = yRange(c.series, tMin, key !== 't');
    var grid = cssVar('--grid'), axis = cssVar('--axis'), ink = cssVar('--ink-dim');
    g.font = '10px ' + cssVar('--font-sans');

    // --- y 축(왼쪽 바깥) ---
    g.strokeStyle = grid; g.lineWidth = 1;
    g.fillStyle = axis; g.textAlign = 'right'; g.textBaseline = 'middle';
    rng.ticks.forEach(function (v) {
      var y = yPos(v, rng, y0, y1);
      g.beginPath(); g.moveTo(x0, r5(y)); g.lineTo(x1, r5(y)); g.stroke();
      g.fillText(fmtVal(key, v, true), x0 - 6, y);
    });

    // --- x 축(플롯 아래 바깥) ---
    g.textAlign = 'center'; g.textBaseline = 'top';
    tickTimes(tMin, tMax).forEach(function (ms) {
      var x = xPos(ms, tMin, tMax, x0, x1);
      g.strokeStyle = grid;
      g.beginPath(); g.moveTo(r5(x), y0); g.lineTo(r5(x), y1); g.stroke();
      g.fillStyle = axis;
      g.fillText(fmt.clockAt(ms), x, y1 + strip + 6);
    });
    g.strokeStyle = axis;
    g.beginPath(); g.moveTo(x0, y1 + .5); g.lineTo(x1, y1 + .5); g.stroke();

    // --- 펄스 표시 띠(압력 차트 아래) ---
    if (strip) {
      pulses.forEach(function (p) {
        if (p.t < tMin) return;
        var x = xPos(p.t, tMin, tMax, x0, x1);
        g.strokeStyle = /H2O|O3|반응|R\d/.test(p.name) ? cssVar('--series-3') : cssVar('--series-2');
        g.lineWidth = 2;
        g.beginPath(); g.moveTo(r5(x), y1 + 4); g.lineTo(r5(x), y1 + strip - 2); g.stroke();
      });
      g.lineWidth = 1;
    }

    // --- 시리즈 선 (플롯 안에는 글자를 쓰지 않는다) ---
    g.save();
    g.beginPath(); g.rect(x0, y0, x1 - x0, y1 - y0); g.clip();
    var ends = [];
    c.series.forEach(function (se) {
      var pts = se.pts.filter(function (p) { return p[0] >= tMin - 2000; });
      if (!pts.length) return;
      g.strokeStyle = se.color; g.lineWidth = 1.4;
      g.beginPath();
      pts.forEach(function (p, i) {
        var x = xPos(p[0], tMin, tMax, x0, x1), y = yPos(p[1], rng, y0, y1);
        if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
      });
      g.stroke();
      var last = pts[pts.length - 1];
      ends.push({ se: se, y: yPos(last[1], rng, y0, y1), v: last[1],
                  x: xPos(last[0], tMin, tMax, x0, x1) });
    });
    g.restore();

    // --- 현재값 라벨(플롯 오른쪽 바깥 띠) + 지시선 ---
    spread(ends, y0, y1).forEach(function (e) {
      g.strokeStyle = e.se.color; g.lineWidth = 1;
      g.beginPath();
      g.moveTo(e.x, e.y); g.lineTo(x1 + 8, e.ly); g.lineTo(x1 + 16, e.ly);
      g.stroke();
      g.fillStyle = cssVar('--ink'); g.textAlign = 'left'; g.textBaseline = 'middle';
      g.font = 'bold 10px ' + cssVar('--font-sans');
      // ★ 이름이 길면 값과 겹친다 — 값 영역 앞까지로 폭을 제한해 canvas 가 줄여 그리게 한다.
      g.fillText(e.se.label, x1 + 20, e.ly, Math.max(20, PAD_R - 20 - LABEL_VAL_W - 6));
      g.font = '10px ' + cssVar('--font-mono');
      g.fillStyle = ink; g.textAlign = 'right';
      g.fillText(fmtVal(key, e.v) + ' ' + e.se.unit, W - 4, e.ly);
    });

    // --- 십자선 + 툴팁 ---
    if (hover && hover.chart === key) drawHover(key, g, x0, x1, y0, y1, tMin, tMax, rng);
  }

  /** 라벨이 겹치지 않도록 위아래로 벌린다(값 순서는 유지). */
  function spread(ends, y0, y1) {
    var list = ends.slice().sort(function (a, b) { return a.y - b.y; });
    list.forEach(function (e) { e.ly = e.y; });
    for (var i = 1; i < list.length; i++) {
      if (list[i].ly - list[i - 1].ly < LABEL_H) list[i].ly = list[i - 1].ly + LABEL_H;
    }
    // 아래로 밀려 플롯을 벗어나면 위로 되민다.
    var over = list.length ? list[list.length - 1].ly - y1 : 0;
    if (over > 0) list.forEach(function (e) { e.ly = Math.max(y0 + 6, e.ly - over); });
    return list;
  }

  function drawHover(key, g, x0, x1, y0, y1, tMin, tMax, rng) {
    var x = Math.max(x0, Math.min(x1, hover.x));
    var ms = tMin + (x - x0) / (x1 - x0) * (tMax - tMin);
    g.strokeStyle = cssVar('--axis'); g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(r5(x), y0); g.lineTo(r5(x), y1); g.stroke();
    g.setLineDash([]);
    var rows = charts[key].series.map(function (se) {
      return { se: se, v: nearest(se.pts, ms) };
    }).filter(function (r) { return r.v != null; });
    var tip = d.getElementById('tip');
    if (!tip) return;
    if (!rows.length) { tip.hidden = true; return; }
    tip.hidden = false;
    tip.innerHTML = '<div class="tt">' + fmt.clockAt(ms) + '</div>' + rows.map(function (r) {
      return '<div class="tr"><i style="background:' + r.se.color + '"></i>' +
        core.esc(r.se.label) + '<b>' + fmtVal(key, r.v) + ' ' + core.esc(r.se.unit) + '</b></div>';
    }).join('');
    var rect = charts[key].canvas.getBoundingClientRect();
    var host = d.getElementById('view-trend').getBoundingClientRect();
    tip.style.left = (rect.left - host.left + x + 14) + 'px';
    tip.style.top = (rect.top - host.top + y0 + 6) + 'px';
  }

  function nearest(pts, ms) {
    if (!pts.length) return null;
    var best = null, bd = Infinity;
    for (var i = pts.length - 1; i >= 0; i--) {
      var dd = Math.abs(pts[i][0] - ms);
      if (dd < bd) { bd = dd; best = pts[i][1]; }
      if (pts[i][0] < ms - 60000) break;
    }
    return bd < 5000 ? best : null;
  }

  /* ---------- 축 계산 ---------- */
  function yRange(series, tMin, noNegative) {
    var lo = Infinity, hi = -Infinity;
    series.forEach(function (se) {
      se.pts.forEach(function (p) {
        if (p[0] < tMin) return;
        if (p[1] < lo) lo = p[1];
        if (p[1] > hi) hi = p[1];
      });
    });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (hi - lo < 1e-9) { hi = lo + Math.max(1e-3, Math.abs(lo) * 0.1 || 1); }
    var pad = (hi - lo) * 0.12;
    lo -= pad; hi += pad;
    // 유량·압력은 음수가 될 수 없다 — 여백 때문에 축이 -50 sccm 까지 내려가면
    // 실제로 그런 값이 있는 것처럼 보인다.
    if (noNegative && lo < 0) lo = 0;
    var step = niceStep((hi - lo) / 4);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    var ticks = [];
    for (var v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
    return { lo: lo, hi: hi, ticks: ticks };
  }

  function niceStep(raw) {
    var e = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var f = raw / e;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
  }

  function tickTimes(tMin, tMax) {
    var span = (tMax - tMin) / 1000;
    var stepS = span <= 150 ? 20 : span <= 700 ? 120 : 600;
    var out = [], start = Math.ceil(tMin / 1000 / stepS) * stepS;
    for (var t = start; t * 1000 <= tMax; t += stepS) out.push(t * 1000);
    return out;
  }

  function xPos(ms, tMin, tMax, x0, x1) { return x0 + (ms - tMin) / (tMax - tMin) * (x1 - x0); }
  function yPos(v, rng, y0, y1) { return y1 - (v - rng.lo) / (rng.hi - rng.lo) * (y1 - y0); }
  function r5(v) { return Math.round(v) + 0.5; }

  function fmtVal(key, v, axisLabel) {
    if (key === 'p') return axisLabel ? (v >= 0.01 ? v.toFixed(2) : v.toExponential(0)) : fmt.torr(v);
    return axisLabel ? String(Math.round(v)) : fmt.num(v, 1);
  }

  /* ===================== 이벤트 ===================== */
  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-range]');
    if (b && !b.disabled) {
      rangeSec = parseInt(b.dataset.range, 10) || 120;
      Array.prototype.forEach.call(d.querySelectorAll('[data-range]'), function (x) {
        x.classList.toggle('primary', x === b);
      });
      refill();
      return;
    }
    var sc = ev.target.closest('[data-bind="autoScroll"]');
    if (sc) autoScroll = !autoScroll;
  });

  d.addEventListener('mousemove', function (ev) {
    var cv = ev.target.closest('[data-chart]');
    if (!cv) { if (hover) { hover = null; d.getElementById('tip').hidden = true; drawAll(); } return; }
    var r = cv.getBoundingClientRect();
    hover = { chart: cv.dataset.chart, x: ev.clientX - r.left };
    drawAll();
  });

  core.register('trend', { render: render, update: update });
  w.viewTrend = { render: render, update: update };
})(window, document);
