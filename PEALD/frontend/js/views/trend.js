/* ============================================================
 * views/trend.js — 트렌드 탭 (압력 · 온도 · MFC). 외부 라이브러리 없이 canvas.
 *
 * 공통 렌더러 draw() 하나로 세 차트를 전부 그린다. 차트마다 그리기 코드를 복사하면
 * 축·라벨 규칙이 조금씩 갈라져 결국 다른 차트처럼 보인다.
 *
 * 차트 규칙
 *   - 플롯 안에는 글자를 두지 않는다. 범례는 제목 띠에 둔다.
 *   - 현재값 라벨은 플롯 오른쪽 바깥 띠에 두고 지시선으로 선 끝과 잇는다.
 *     라벨끼리 겹치면 위아래로 벌린다(spread()).
 *   - 한 차트에 y축은 하나만 쓴다.
 *   - 시리즈 색은 tokens.css 의 --series-1..6 순서로 고정한다.
 *
 * 이력은 GET /api/trend 로 한 번 받고, 이후는 live 로 이어 붙인다.
 * ★ PLC 가 끊긴 구간은 점을 찍지 않는다 — 0 을 채우면 그래프가 거짓말을 한다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var PAD_L = 48, PAD_R = 178, PAD_T = 12, PAD_B = 28;
  var LABEL_VAL_W = 62;
  var MAX_SERIES = 6;
  var LABEL_H = 13;

  var rangeSec = 120;
  var charts = {};
  var hover = null;
  var seeded = false;

  function render(s) {
    buildSeries(s);
    if (!seeded) { seeded = true; refill(); }
    drawAll();
  }

  function buildSeries(s) {
    var str = s.structure || {};
    var def = { p: [{ key: 'cvg', label: 'CVG', unit: 'Torr' }], t: [], m: [] };
    (str.heaters || []).forEach(function (h) {
      if (!h.enabled || def.t.length >= MAX_SERIES) return;
      def.t.push({ key: h.ch, label: 'CH' + h.ch + ' ' + h.name, unit: '°C' });
    });
    (str.mfc || []).forEach(function (m) {
      if (def.m.length >= MAX_SERIES) return;
      def.m.push({ key: m.no, label: 'MFC' + m.no + ' ' + m.name, unit: 'sccm' });
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
    box.innerHTML = charts[k].series.map(function (se) {
      return '<span><i style="background:' + se.color + '"></i>' + core.esc(se.label) + '</span>';
    }).join('');
  }

  function cssVar(n) { return getComputedStyle(d.documentElement).getPropertyValue(n).trim(); }

  function refill() {
    fetch('api/trend?sec=' + rangeSec).then(function (r) { return r.json(); }).then(function (js) {
      // 서버는 monotonic 시계를 쓴다 — 화면의 벽시계로 옮긴다.
      var base = Date.now() - js.now * 1000;
      ['p', 't', 'm'].forEach(function (k) {
        (charts[k] || { series: [] }).series.forEach(function (se) { se.pts = []; });
      });
      (js.slow || []).forEach(function (r) {
        var ms = base + r.t * 1000;
        push('t', r.h || {}, ms);
        push('m', r.m || {}, ms);
      });
      (js.fast || []).forEach(function (r) { pt('p', 'cvg', base + r.t * 1000, r.p); });
      drawAll();
    }).catch(function () { /* 이력이 없어도 live 로 계속 그린다 */ });
  }

  function push(chart, obj, ms) {
    (charts[chart] || { series: [] }).series.forEach(function (se) {
      pt(chart, se.key, ms, obj[se.key]);
    });
  }

  function pt(chart, key, ms, v) {
    var c = charts[chart];
    if (!c || v === null || v === undefined) return;   // ★ 끊긴 구간은 찍지 않는다
    var se = c.series.filter(function (x) { return String(x.key) === String(key); })[0];
    if (!se) return;
    se.pts.push([ms, Number(v)]);
    var cut = ms - 3600000;
    while (se.pts.length && se.pts[0][0] < cut) se.pts.shift();
  }

  function update(t) {
    var ms = Date.now();
    var conn = !!(t.plc && t.plc.connected);
    if (conn) {
      pt('p', 'cvg', ms, (t.pressure || {}).cvg);
      var hm = {}, mm = {};
      (t.heaters || []).forEach(function (h) { if (h.comm_ok) hm[h.ch] = h.pv; });
      (t.mfc || []).forEach(function (m) { mm[m.no] = m.pv; });
      push('t', hm, ms);
      push('m', mm, ms);
    }
    core.setText('trendInfo', conn ? (t.clock || '') : 'PLC 끊김 — 기록 멈춤');
    if (core.tab === 'trend') drawAll();
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

    var x0 = PAD_L, x1 = W - PAD_R, y0 = PAD_T, y1 = H - PAD_B;
    if (x1 <= x0 || y1 <= y0) return;

    var now = Date.now(), tMin = now - rangeSec * 1000, tMax = now;
    // 압력은 자릿수가 정보라 로그 축을 쓴다.
    var logY = (key === 'p');
    var rng = yRange(c.series, tMin, key !== 't', logY);
    var grid = cssVar('--grid'), axis = cssVar('--axis'), ink = cssVar('--ink-dim');
    g.font = '10px ' + cssVar('--font-sans');

    g.strokeStyle = grid; g.lineWidth = 1;
    g.fillStyle = axis; g.textAlign = 'right'; g.textBaseline = 'middle';
    rng.ticks.forEach(function (v) {
      var y = yPos(v, rng, y0, y1, logY);
      g.beginPath(); g.moveTo(x0, r5(y)); g.lineTo(x1, r5(y)); g.stroke();
      g.fillText(axisLabel(key, v), x0 - 6, y);
    });

    g.textAlign = 'center'; g.textBaseline = 'top';
    tickTimes(tMin, tMax).forEach(function (ms) {
      var x = xPos(ms, tMin, tMax, x0, x1);
      g.strokeStyle = grid;
      g.beginPath(); g.moveTo(r5(x), y0); g.lineTo(r5(x), y1); g.stroke();
      g.fillStyle = axis;
      g.fillText(fmt.clockAt(ms), x, y1 + 5);
    });
    g.strokeStyle = axis;
    g.beginPath(); g.moveTo(x0, y1 + .5); g.lineTo(x1, y1 + .5); g.stroke();

    g.save();
    g.beginPath(); g.rect(x0, y0, x1 - x0, y1 - y0); g.clip();
    var ends = [];
    c.series.forEach(function (se) {
      var pts = se.pts.filter(function (p) { return p[0] >= tMin - 2000; });
      if (!pts.length) return;
      g.strokeStyle = se.color; g.lineWidth = 1.4;
      g.beginPath();
      var started = false;
      pts.forEach(function (p, i) {
        var x = xPos(p[0], tMin, tMax, x0, x1), y = yPos(p[1], rng, y0, y1, logY);
        // 시간이 크게 벌어지면(끊긴 구간) 선을 잇지 않는다.
        if (!started || (i > 0 && p[0] - pts[i - 1][0] > 5000)) { g.moveTo(x, y); started = true; }
        else g.lineTo(x, y);
      });
      g.stroke();
      var last = pts[pts.length - 1];
      ends.push({ se: se, y: yPos(last[1], rng, y0, y1, logY), v: last[1],
                  x: xPos(last[0], tMin, tMax, x0, x1) });
    });
    g.restore();

    spread(ends, y0, y1).forEach(function (e) {
      g.strokeStyle = e.se.color; g.lineWidth = 1;
      g.beginPath();
      g.moveTo(e.x, e.y); g.lineTo(x1 + 8, e.ly); g.lineTo(x1 + 16, e.ly);
      g.stroke();
      g.fillStyle = cssVar('--ink'); g.textAlign = 'left'; g.textBaseline = 'middle';
      g.font = 'bold 10px ' + cssVar('--font-sans');
      g.fillText(e.se.label, x1 + 20, e.ly, Math.max(20, PAD_R - 20 - LABEL_VAL_W - 6));
      g.font = '10px ' + cssVar('--font-mono');
      g.fillStyle = ink; g.textAlign = 'right';
      g.fillText(valLabel(key, e.v) + ' ' + e.se.unit, W - 4, e.ly);
    });

    if (hover && hover.chart === key) drawHover(key, g, x0, x1, y0, y1, tMin, tMax, rng, logY);
  }

  function spread(ends, y0, y1) {
    var list = ends.slice().sort(function (a, b) { return a.y - b.y; });
    list.forEach(function (e) { e.ly = e.y; });
    for (var i = 1; i < list.length; i++) {
      if (list[i].ly - list[i - 1].ly < LABEL_H) list[i].ly = list[i - 1].ly + LABEL_H;
    }
    var over = list.length ? list[list.length - 1].ly - y1 : 0;
    if (over > 0) list.forEach(function (e) { e.ly = Math.max(y0 + 6, e.ly - over); });
    return list;
  }

  function drawHover(key, g, x0, x1, y0, y1, tMin, tMax) {
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
        core.esc(r.se.label) + '<b>' + valLabel(key, r.v) + ' ' + core.esc(r.se.unit) + '</b></div>';
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

  /* ---------- 축 ---------- */
  function yRange(series, tMin, noNeg, logY) {
    var lo = Infinity, hi = -Infinity;
    series.forEach(function (se) {
      se.pts.forEach(function (p) {
        if (p[0] < tMin) return;
        if (p[1] < lo) lo = p[1];
        if (p[1] > hi) hi = p[1];
      });
    });
    if (!isFinite(lo)) { lo = logY ? 1e-3 : 0; hi = logY ? 1e3 : 1; }
    if (logY) {
      lo = Math.max(1e-5, lo); hi = Math.max(lo * 10, hi);
      var e0 = Math.floor(Math.log(lo) / Math.LN10);
      var e1 = Math.ceil(Math.log(hi) / Math.LN10);
      var ticks = [];
      for (var e = e0; e <= e1; e++) ticks.push(Math.pow(10, e));
      return { lo: Math.pow(10, e0), hi: Math.pow(10, e1), ticks: ticks, log: true };
    }
    if (hi - lo < 1e-9) hi = lo + Math.max(1, Math.abs(lo) * 0.1);
    var pad = (hi - lo) * 0.12;
    lo -= pad; hi += pad;
    if (noNeg && lo < 0) lo = 0;
    var step = niceStep((hi - lo) / 4);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    var tk = [];
    for (var v = lo; v <= hi + step / 2; v += step) tk.push(Number(v.toFixed(10)));
    return { lo: lo, hi: hi, ticks: tk, log: false };
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

  function yPos(v, rng, y0, y1, logY) {
    if (logY) {
      var lv = Math.log(Math.max(1e-6, v)) / Math.LN10;
      var l0 = Math.log(rng.lo) / Math.LN10, l1 = Math.log(rng.hi) / Math.LN10;
      return y1 - (lv - l0) / (l1 - l0) * (y1 - y0);
    }
    return y1 - (v - rng.lo) / (rng.hi - rng.lo) * (y1 - y0);
  }

  function r5(v) { return Math.round(v) + 0.5; }

  function axisLabel(key, v) {
    if (key === 'p') {
      var e = Math.round(Math.log(v) / Math.LN10);
      return e === 0 ? '1' : '1E' + (e > 0 ? '+' : '') + e;
    }
    return String(Math.round(v));
  }

  function valLabel(key, v) { return key === 'p' ? fmt.torr(v) : fmt.num(v, 1); }

  /* ---------- 이벤트 ---------- */
  d.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-range]');
    if (!b || b.disabled) return;
    rangeSec = parseInt(b.dataset.range, 10) || 120;
    Array.prototype.forEach.call(d.querySelectorAll('[data-range]'), function (x) {
      x.classList.toggle('primary', x === b);
    });
    refill();
  });

  d.addEventListener('mousemove', function (ev) {
    var cv = ev.target.closest('[data-chart]');
    if (!cv) {
      if (hover) { hover = null; d.getElementById('tip').hidden = true; drawAll(); }
      return;
    }
    var r = cv.getBoundingClientRect();
    hover = { chart: cv.dataset.chart, x: ev.clientX - r.left };
    drawAll();
  });

  core.register('trend', { render: render, update: update });
  w.viewTrend = { render: render, update: update };
})(window, document);
