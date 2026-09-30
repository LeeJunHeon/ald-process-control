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
    if (!w.viewTrendHist || w.viewTrendHist.mode === 'live') {
      core.setText('trendInfo', conn ? (t.clock || '') : 'PLC 끊김 — 기록 멈춤');
    }
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
      g.fillText(axisLabel(key, v, rng.step), x0 - 6, y);
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
    return { lo: lo, hi: hi, ticks: tk, log: false, step: step };
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

  /** 눈금 글자. ★ 눈금 간격에 맞춘 자릿수로 쓴다 — 정수로 반올림하면
   *  간격 0.5 인 축이 0, 1, 1, 2 처럼 겹쳐 보인다. */
  function axisLabel(key, v, step) {
    if (key === 'p') {
      var e = Math.round(Math.log(v) / Math.LN10);
      return e === 0 ? '1' : '1E' + (e > 0 ? '+' : '') + e;
    }
    var dp = step > 0 ? Math.max(0, -Math.floor(Math.log(step) / Math.LN10 + 1e-9)) : 0;
    return v.toFixed(Math.min(dp, 4));
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

/* ============================================================
 * 트렌드 이력 — [실시간 | 이력] 중 이력.
 *
 * 서버의 날짜별 이력(1 Hz)에서 구간을 최대 2000 묶음(최소·최대·평균)으로 받아 그린다.
 * 끌어서 확대, [되돌리기]로 한 단계씩 되돌린다. 선택 구간을 CSV 로 data/export/ 에 저장한다.
 * ★ 프로그램이 꺼져 있던 구간은 선을 잇지 않는다(서버가 줄을 남기지 않았다).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var mode = 'live';
  var t0 = 0, t1 = 0;             // 초(epoch)
  var stack = [];                 // 확대 되돌리기
  var chosen = null;              // 고른 열 {key:true}
  var charts = {};
  var lastRes = null;

  function cssVar(n) { return getComputedStyle(d.documentElement).getPropertyValue(n).trim(); }

  function cols() { return ((core.state || {}).trend_cols) || []; }

  /** 열마다 고정 색 — 묶음 안 순서로 정한다(체크박스 색 표시와 그래프 선이 같게). */
  function colorOf(key) {
    var c = cols().filter(function (x) { return x.key === key; })[0];
    if (!c) return cssVar('--series-1');
    var i = cols().filter(function (x) { return x.group === c.group; })
      .map(function (x) { return x.key; }).indexOf(key);
    return cssVar('--series-' + ((i % 6) + 1));
  }

  function defaults() {
    var s = core.state || {};
    var str = s.structure || {};
    var on = { p: true };
    (str.mfc || []).forEach(function (m) { on['mfc' + m.no + '_pv'] = true; });
    (str.heaters || []).forEach(function (h) { if (h.enabled) on['h' + h.ch + '_pv'] = true; });
    cols().forEach(function (c) { if (c.group === 'x' && /_(pv|fwd)$/.test(c.key)) on[c.key] = true; });
    return on;
  }

  function render() {
    if (!chosen) chosen = defaults();
    buildSeriesPicker();
    ['p', 't', 'm', 'x'].forEach(function (k) {
      var cv = d.querySelector('[data-hchart="' + k + '"]');
      if (cv && !charts[k]) charts[k] = HistChart(cv, core.bind('hTip'), { onZoom: zoom });
    });
    var hasX = cols().some(function (c) { return c.group === 'x'; });
    var xp = core.bind('hXPanel');
    if (xp) xp.hidden = !hasX;
    var dev = (core.state || {}).device || {};
    core.setText('hXHead', dev.has_o3 ? 'O3' : 'RF · PCV');
    if (mode === 'hist') drawAll();
  }

  function buildSeriesPicker() {
    var box = core.bind('hSeries');
    if (!box) return;
    var names = { p: '압력', m: 'MFC', t: '히터', x: '장비 전용' };
    var html = '';
    ['p', 'm', 't', 'x'].forEach(function (g) {
      var list = cols().filter(function (c) { return c.group === g; });
      if (!list.length) return;
      html += '<span class="hs-g">' + names[g] + '</span>' + list.map(function (c) {
        return '<label class="hs"><input type="checkbox" data-hcol="' + core.esc(c.key) + '"' +
          (chosen[c.key] ? ' checked' : '') + '><i class="sw-c" style="background:' + colorOf(c.key) +
          '"></i>' + core.esc(c.label) + '</label>';
      }).join('');
    });
    box.innerHTML = html;
  }

  function setMode(m) {
    mode = m;
    Array.prototype.forEach.call(d.querySelectorAll('[data-tmode]'), function (b) {
      b.classList.toggle('primary', b.dataset.tmode === m);
    });
    var hist = m === 'hist';
    ['histBar', 'hSeries', 'histPanels'].forEach(function (n) { var e = core.bind(n); if (e) e.hidden = !hist; });
    ['liveBar', 'livePanels'].forEach(function (n) { var e = core.bind(n); if (e) e.hidden = hist; });
    if (hist && !t1) quick(3600);
    else if (hist) drawAll();
    else if (w.viewTrend && core.state) w.viewTrend.render(core.state);
  }

  function toLocalInput(sec) {
    var x = new Date(sec * 1000);
    return x.getFullYear() + '-' + fmt.pad(x.getMonth() + 1) + '-' + fmt.pad(x.getDate()) + 'T' +
      fmt.pad(x.getHours()) + ':' + fmt.pad(x.getMinutes()) + ':' + fmt.pad(x.getSeconds());
  }

  function fromInput(name) {
    var e = core.bind(name);
    if (!e || !e.value) return NaN;
    return new Date(e.value).getTime() / 1000;
  }

  function quick(sec) {
    t1 = Math.floor(Date.now() / 1000);
    t0 = t1 - sec;
    stack = [];
    load();
  }

  function syncInputs() {
    var a = core.bind('hT0'), b = core.bind('hT1');
    if (a) a.value = toLocalInput(t0);
    if (b) b.value = toLocalInput(t1);
    var u = core.bind('hUndo');
    if (u) u.disabled = !stack.length;
  }

  function zoom(a, b) {
    stack.push([t0, t1]);
    t0 = a / 1000; t1 = b / 1000;
    load();
  }

  function load() {
    syncInputs();
    var keys = Object.keys(chosen).filter(function (k) { return chosen[k]; });
    core.setText('trendInfo', '불러오는 중…');
    fetch('api/trend/history?t0=' + t0 + '&t1=' + t1 + '&cols=' + encodeURIComponent(keys.join(',')))
      .then(function (r) { return r.json(); })
      .then(function (js) {
        if (js.error) {
          core.setText('trendInfo', js.error);
          core.toast(js.error, 'warn');
          return;
        }
        lastRes = js;
        var bs = js.bucket_s || 1;
        core.setText('trendInfo', (js.rows || []).length + '묶음 · 묶음당 ' +
          (bs >= 60 ? (bs / 60).toFixed(1) + ' 분' : bs.toFixed(1) + ' s'));
        drawAll();
      })
      .catch(function () { core.setText('trendInfo', '이력을 읽지 못했습니다'); });
  }

  function xLabel(ms, span, full) {
    var x = new Date(ms);
    var hm = fmt.pad(x.getHours()) + ':' + fmt.pad(x.getMinutes());
    var date = fmt.pad(x.getMonth() + 1) + '-' + fmt.pad(x.getDate());
    if (full) return x.getFullYear() + '-' + date + ' ' + hm + ':' + fmt.pad(x.getSeconds());
    return span > 2 * 86400000 ? date + ' ' + hm : (span > 600000 ? hm : hm + ':' + fmt.pad(x.getSeconds()));
  }

  function drawAll() {
    if (mode !== 'hist') return;
    var js = lastRes || { cols: [], rows: [], bucket_s: 1 };
    var meta = {};
    cols().forEach(function (c) { meta[c.key] = c; });
    var gap = Math.max(3 * (js.bucket_s || 1), 5) * 1000;
    var per = { p: [], t: [], m: [], x: [] };
    (js.cols || []).forEach(function (key, i) {
      var c = meta[key];
      if (!c || !per[c.group]) return;
      per[c.group].push({
        label: c.label, unit: c.unit,
        color: colorOf(key),
        pts: (js.rows || []).map(function (r) {
          var v = r[i + 1];
          return v ? [r[0] * 1000, v[0], v[1], v[2]] : [r[0] * 1000, null, null, null];
        })
      });
    });
    ['p', 't', 'm', 'x'].forEach(function (k) {
      if (!charts[k]) return;
      charts[k].set({ series: per[k], x0: t0 * 1000, x1: t1 * 1000, logY: k === 'p',
                      noNeg: k !== 't', gap: gap, xLabel: xLabel, bands: [] });
    });
  }

  d.addEventListener('click', function (ev) {
    var m = ev.target.closest('[data-tmode]');
    if (m) { setMode(m.dataset.tmode); return; }
    var q = ev.target.closest('[data-hquick]');
    if (q) { quick(parseInt(q.dataset.hquick, 10)); return; }
    var a = ev.target.closest('[data-hact]');
    if (!a || a.disabled) return;
    var act = a.dataset.hact;
    if (act === 'load') {
      var x0 = fromInput('hT0'), x1 = fromInput('hT1');
      if (!(x1 > x0)) { core.toast('끝 시각이 시작 시각보다 뒤여야 합니다', 'warn'); return; }
      stack = []; t0 = x0; t1 = x1; load();
    } else if (act === 'undo') {
      var prev = stack.pop();
      if (prev) { t0 = prev[0]; t1 = prev[1]; load(); }
    } else if (act === 'export') {
      if (!core.canOperate()) { core.toast('원격 접속은 보기 전용입니다', 'warn'); return; }
      w.app.send('trend_export', { t0: t0, t1: t1 });
    } else if (act === 'folder') {
      w.app.send('open_folder', { which: 'export' });
    }
  });

  d.addEventListener('change', function (ev) {
    var c = ev.target.closest('[data-hcol]');
    if (!c) return;
    chosen[c.dataset.hcol] = c.checked;
    load();
  });

  core.register('trendhist', { render: render, update: function () {} });
  w.viewTrendHist = { setMode: setMode, get mode() { return mode; } };
})(window, document);
