/* ============================================================
 * views/trend.js — 트렌드 탭 (압력 · 온도 · MFC). 외부 라이브러리 없이 canvas.
 *
 * 실시간 · 이력 모두 chart.js 의 HistChart 하나로 그린다. 차트마다 그리기 코드를 따로 두면
 * 축·라벨 규칙이 조금씩 갈라져 결국 다른 차트처럼 보인다(규칙은 chart.js 머리 주석).
 *   - 플롯 안에는 글자도 지시선도 두지 않는다. 라벨·값은 오른쪽 띠에만.
 *   - PLC 가 끊기면 띠 값은 '—'. 끊긴 구간은 점을 찍지 않는다 — 0 을 채우면 그래프가 거짓말을 한다.
 *   - 사용하는 히터 채널은 모두 그린다(12 개까지). 한 그래프 안 색은 겹치지 않는다.
 *
 * 이력은 GET /api/trend 로 한 번 받고, 이후는 live 로 이어 붙인다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var rangeSec = 120;
  var charts = {};
  var seeded = false;
  var dead = true;
  var refillNo = 0;

  function render(s) {
    buildSeries(s);
    if (!seeded) { seeded = true; refill(); }
    drawAll();
  }

  function buildSeries(s) {
    var str = s.structure || {};
    var def = { p: [{ key: 'cvg', label: 'CVG', unit: 'Torr' }], t: [], m: [] };
    (str.heaters || []).forEach(function (h) {
      if (!h.enabled) return;
      def.t.push({ key: h.ch, label: 'CH' + h.ch + ' ' + h.name, unit: '℃' });
    });
    (str.mfc || []).forEach(function (m) {
      def.m.push({ key: m.no, label: 'MFC' + m.no + ' ' + m.name, unit: 'sccm' });
    });
    ['p', 't', 'm'].forEach(function (k) {
      var cv = d.querySelector('[data-chart="' + k + '"]');
      if (!cv) return;
      var old = (charts[k] || {}).series || [];
      var cm = HistChart.colorMap(def[k].map(function (x) { return String(x.key); }),
                                  function () { return true; });
      charts[k] = {
        chart: (charts[k] && charts[k].canvas === cv) ? charts[k].chart : HistChart(cv),
        canvas: cv,
        series: def[k].map(function (sd) {
          var prev = old.filter(function (o) { return o.key === sd.key; })[0];
          return { key: sd.key, label: sd.label, unit: sd.unit, color: cm[String(sd.key)].color,
                   pts: (prev && prev.pts) || [] };
        })
      };
      legend(k, cm);
    });
  }

  function legend(k, cm) {
    var box = core.bind({ p: 'lgP', t: 'lgT', m: 'lgM' }[k]);
    if (!box) return;
    core.html(box, charts[k].series.map(function (se) {
      return '<span>' + HistChart.swatch(cm[String(se.key)]) + core.esc(se.label) + '</span>';
    }).join(''));
  }

  function refill() {
    var my = ++refillNo;
    fetch('api/trend?sec=' + rangeSec).then(function (r) { return r.json(); }).then(function (js) {
      if (my !== refillNo) return;          // 늦게 온 옛 응답은 버린다
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
    var n = Number(v);
    se.pts.push([ms, n, n, n]);
    var cut = ms - 3600000;
    while (se.pts.length && se.pts[0][0] < cut) se.pts.shift();
  }

  function update(t) {
    var ms = Date.now();
    var conn = !!(t.plc && t.plc.connected) && !t.offline;
    dead = !conn;
    if (conn) {
      pt('p', 'cvg', ms, (t.pressure || {}).cvg);
      var hm = {}, mm = {};
      (t.heaters || []).forEach(function (h) { if (h.comm_ok) hm[h.ch] = h.pv; });
      (t.mfc || []).forEach(function (m) { mm[m.no] = m.pv; });
      push('t', hm, ms);
      push('m', mm, ms);
    }
    if (!w.viewTrendHist || w.viewTrendHist.mode === 'live') {
      core.setText('trendInfo', t.offline ? '서버 끊김 — 기록 멈춤'
        : conn ? (t.clock || '') : 'PLC 끊김 — 기록 멈춤');
    }
    if (core.tab === 'trend') drawAll();
  }

  function drawAll() {
    if (w.viewTrendHist && w.viewTrendHist.mode !== 'live') return;
    var now = Date.now();
    ['p', 't', 'm'].forEach(function (k) {
      var c = charts[k];
      if (!c) return;
      c.chart.set({ series: c.series, x0: now - rangeSec * 1000, x1: now, logY: k === 'p',
                    noNeg: k !== 't', gap: 5000, tol: 3000, dead: dead,
                    xLabel: function (ms) { return fmt.clockAt(ms); }, bands: [] });
    });
  }

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

  core.register('trend', { render: render, update: update });
  w.viewTrend = { render: render, update: update, charts: charts };
})(window, document);

/* ============================================================
 * 트렌드 이력 — [실시간 | 이력] 중 이력.
 *
 * 서버의 날짜별 이력(1 Hz)에서 구간을 최대 2000 묶음(최소·최대·평균)으로 받아 그린다.
 * 끌어서 확대, [되돌리기]로 한 단계씩 되돌린다. 선택 구간을 CSV 로 data/export/ 에 저장한다.
 * ★ 프로그램이 꺼져 있던 구간은 선을 잇지 않는다(서버가 줄을 남기지 않았다).
 * ★ 요청마다 번호를 붙여 가장 최근 요청의 응답만 그린다(늦게 온 옛 응답이 새 결과를 덮지 않게).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var mode = 'live';
  var t0 = 0, t1 = 0;             // 초(epoch)
  var stack = [];                 // 확대 되돌리기
  var chosen = null;              // 고른 열 {key:true}
  var charts = {};
  var lastRes = null;
  var loadNo = 0;

  function cols() { return ((core.state || {}).trend_cols) || []; }

  /** 묶음 하나의 색 — 보이는 현재값끼리 겹치지 않고 설정값은 같은 색 점선(체크박스와 선이 같게). */
  function colorsOf(group) {
    var ids = cols().filter(function (c) { return c.group === group; }).map(function (c) { return c.key; });
    return HistChart.colorMap(ids, function (k) { return !!chosen[k]; });
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
      if (cv && !charts[k]) charts[k] = HistChart(cv, { onZoom: zoom });
    });
    var hasX = cols().some(function (c) { return c.group === 'x'; });
    var xp = core.bind('hXPanel');
    if (xp) xp.hidden = !hasX;
    var dev = (core.state || {}).device || {};
    core.setText('hXHead', dev.has_o3 ? 'O3' : 'RF · PCV');
    var fb = d.querySelector('[data-hact="folder"]'), eb = d.querySelector('[data-hact="export"]');
    if (fb) fb.disabled = !core.canOperate();
    if (eb) eb.disabled = !core.canOperate();
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
      var cm = colorsOf(g);
      html += '<span class="hs-g">' + names[g] + '</span>' + list.map(function (c) {
        return '<label class="hs"><input type="checkbox" data-hcol="' + core.esc(c.key) + '"' +
          (chosen[c.key] ? ' checked' : '') + '>' + HistChart.swatch(cm[c.key]) + core.esc(c.label) + '</label>';
      }).join('');
    });
    core.html(box, html);
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
    var my = ++loadNo;
    var keys = Object.keys(chosen).filter(function (k) { return chosen[k]; });
    core.setText('trendInfo', '불러오는 중…');
    fetch('api/trend/history?t0=' + t0 + '&t1=' + t1 + '&cols=' + encodeURIComponent(keys.join(',')))
      .then(function (r) { return r.json(); })
      .then(function (js) {
        if (my !== loadNo) return;          // ★ 가장 최근 요청의 응답만
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
      .catch(function () { if (my === loadNo) core.setText('trendInfo', '이력을 읽지 못했습니다'); });
  }

  function xLabel(ms, span, full) {
    var x = new Date(ms);
    var hm = fmt.pad(x.getHours()) + ':' + fmt.pad(x.getMinutes());
    var date = fmt.pad(x.getMonth() + 1) + '-' + fmt.pad(x.getDate());
    if (full) return x.getFullYear() + '-' + date + ' ' + hm + ':' + fmt.pad(x.getSeconds());
    if (!span) return hm;                        // 띠의 '마지막 값 시각'
    return span > 2 * 86400000 ? date + ' ' + hm : (span > 600000 ? hm : hm + ':' + fmt.pad(x.getSeconds()));
  }

  function drawAll() {
    if (mode !== 'hist') return;
    var js = lastRes || { cols: [], rows: [], bucket_s: 1 };
    var meta = {};
    cols().forEach(function (c) { meta[c.key] = c; });
    var gap = Math.max(3 * (js.bucket_s || 1), 5) * 1000;
    var per = { p: [], t: [], m: [], x: [] };
    var cmap = { p: colorsOf('p'), t: colorsOf('t'), m: colorsOf('m'), x: colorsOf('x') };
    (js.cols || []).forEach(function (key, i) {
      var c = meta[key];
      if (!c || !per[c.group] || !chosen[key]) return;
      var cc = cmap[c.group][key] || {};
      per[c.group].push({
        label: c.label, unit: c.unit === '°C' ? '℃' : c.unit,
        color: cc.color, dashed: cc.dashed,
        pts: (js.rows || []).map(function (r) {
          var v = r[i + 1];
          return v ? [r[0] * 1000, v[0], v[1], v[2]] : [r[0] * 1000, null, null, null];
        })
      });
    });
    ['p', 't', 'm', 'x'].forEach(function (k) {
      if (!charts[k]) return;
      charts[k].set({ series: per[k], x0: t0 * 1000, x1: t1 * 1000, logY: k === 'p',
                      noNeg: k !== 't', gap: gap, xLabel: xLabel, bands: [], dead: false });
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
      if (!core.canOperate()) return;
      w.app.send('open_folder', { which: 'export' });
    }
  });

  d.addEventListener('change', function (ev) {
    var c = ev.target.closest('[data-hcol]');
    if (!c) return;
    chosen[c.dataset.hcol] = c.checked;
    buildSeriesPicker();
    load();
  });

  core.register('trendhist', { render: render, update: function () {} });
  w.viewTrendHist = { setMode: setMode, get mode() { return mode; }, charts: charts };
})(window, document);
