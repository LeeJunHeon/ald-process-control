/* ============================================================
 * chart.js — 이력 그래프 공용 (트렌드 이력 · 데이터 로그 보기). 외부 라이브러리 없이 canvas.
 *
 * 서버가 구간을 최대 2000 묶음(최소·최대·평균)으로 줄여 보낸다.
 *   - 평균은 선, 최소~최대는 옅은 띠로 그린다(짧은 펄스가 평균에 묻혀 사라지지 않게).
 *   - 묶음 사이가 크게 벌어지면(프로그램이 꺼져 있던 구간) 선을 잇지 않는다.
 *   - 압력은 로그 축.
 *   - 끌어서 확대, [되돌리기]로 한 단계씩 되돌린다. 마우스를 올리면 커서 값.
 *   - bands: 배경 띠(데이터 로그의 블록·스텝 구간). 마우스를 올리면 이름을 보여 준다.
 *
 * 사용: var c = HistChart(canvas, tipEl, { onZoom: fn(x0, x1) });
 *       c.set({ series, x0, x1, logY, gap, xLabel, bands, unit });
 * ============================================================ */
(function (w, d) {
  'use strict';

  var PAD_L = 52, PAD_R = 12, PAD_T = 10, PAD_B = 26;

  function cssVar(n) { return getComputedStyle(d.documentElement).getPropertyValue(n).trim(); }

  function HistChart(canvas, tip, opts) {
    opts = opts || {};
    var st = { series: [], x0: 0, x1: 1, logY: false, gap: Infinity, bands: [],
               xLabel: function (x) { return String(x); } };
    var drag = null, hoverX = null;
    var g = canvas.getContext('2d');

    function set(o) { for (var k in o) st[k] = o[k]; draw(); }

    function geom() {
      var W = canvas.clientWidth, H = canvas.clientHeight;
      return { W: W, H: H, x0: PAD_L, x1: W - PAD_R, y0: PAD_T, y1: H - PAD_B };
    }

    function range() {
      var lo = Infinity, hi = -Infinity;
      st.series.forEach(function (se) {
        if (se.hidden) return;
        se.pts.forEach(function (p) {
          if (!p || p[0] < st.x0 || p[0] > st.x1) return;
          var a = st.logY ? p[3] : p[1], b = st.logY ? p[3] : p[2];
          if (a == null || b == null) return;
          if (st.logY && a <= 0) return;
          if (a < lo) lo = a;
          if (b > hi) hi = b;
        });
      });
      if (!isFinite(lo)) { lo = st.logY ? 1e-3 : 0; hi = st.logY ? 1e3 : 1; }
      if (st.logY) {
        var e0 = Math.floor(Math.log(Math.max(1e-6, lo)) / Math.LN10);
        var e1 = Math.ceil(Math.log(Math.max(lo * 10, hi)) / Math.LN10);
        if (e1 <= e0) e1 = e0 + 1;
        var tk = [];
        for (var e = e0; e <= e1; e++) tk.push(Math.pow(10, e));
        return { lo: Math.pow(10, e0), hi: Math.pow(10, e1), ticks: tk, step: 0 };
      }
      if (hi - lo < 1e-9) hi = lo + Math.max(1, Math.abs(lo) * 0.1);
      var pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
      if (lo < 0 && st.noNeg) lo = 0;
      var step = nice((hi - lo) / 4);
      lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
      var t2 = [];
      for (var v = lo; v <= hi + step / 2; v += step) t2.push(Number(v.toFixed(10)));
      return { lo: lo, hi: hi, ticks: t2, step: step };
    }

    function nice(raw) {
      var e = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
      var f = raw / e;
      return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
    }

    function yPos(v, r, G) {
      if (st.logY) {
        var lv = Math.log(Math.max(1e-9, v)) / Math.LN10;
        var l0 = Math.log(r.lo) / Math.LN10, l1 = Math.log(r.hi) / Math.LN10;
        return G.y1 - (lv - l0) / (l1 - l0) * (G.y1 - G.y0);
      }
      return G.y1 - (v - r.lo) / (r.hi - r.lo) * (G.y1 - G.y0);
    }

    function xPos(x, G) { return G.x0 + (x - st.x0) / (st.x1 - st.x0) * (G.x1 - G.x0); }
    function xAt(px, G) { return st.x0 + (px - G.x0) / (G.x1 - G.x0) * (st.x1 - st.x0); }

    function yLabel(v, r) {
      if (st.logY) {
        var e = Math.round(Math.log(v) / Math.LN10);
        return e === 0 ? '1' : '1E' + (e > 0 ? '+' : '') + e;
      }
      var dp = r.step > 0 ? Math.max(0, -Math.floor(Math.log(r.step) / Math.LN10 + 1e-9)) : 0;
      return v.toFixed(Math.min(dp, 4));
    }

    function draw() {
      if (!canvas.clientWidth) return;
      var dpr = w.devicePixelRatio || 1;
      var G = geom();
      canvas.width = G.W * dpr; canvas.height = G.H * dpr;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, G.W, G.H);
      if (G.x1 <= G.x0 || G.y1 <= G.y0 || st.x1 <= st.x0) return;
      var r = range();
      var grid = cssVar('--grid'), axis = cssVar('--axis');
      g.font = '10px ' + cssVar('--font-sans');

      // 배경 띠(블록·스텝)
      st.bands.forEach(function (b, i) {
        if (b.x1 < st.x0 || b.x0 > st.x1) return;
        var a = Math.max(G.x0, xPos(b.x0, G)), z = Math.min(G.x1, xPos(b.x1, G));
        g.fillStyle = (b.alt ? cssVar('--band-b') : cssVar('--band-a')) || (i % 2 ? '#8881' : '#8882');
        g.fillRect(a, G.y0, Math.max(1, z - a), G.y1 - G.y0);
      });

      g.strokeStyle = grid; g.lineWidth = 1;
      g.fillStyle = axis; g.textAlign = 'right'; g.textBaseline = 'middle';
      r.ticks.forEach(function (v) {
        var y = yPos(v, r, G);
        g.beginPath(); g.moveTo(G.x0, Math.round(y) + .5); g.lineTo(G.x1, Math.round(y) + .5); g.stroke();
        g.fillText(yLabel(v, r), G.x0 - 6, y);
      });
      g.textAlign = 'center'; g.textBaseline = 'top';
      var nx = Math.max(2, Math.floor((G.x1 - G.x0) / 110));
      for (var i = 0; i <= nx; i++) {
        var xv = st.x0 + (st.x1 - st.x0) * i / nx, px = xPos(xv, G);
        g.strokeStyle = grid;
        g.beginPath(); g.moveTo(Math.round(px) + .5, G.y0); g.lineTo(Math.round(px) + .5, G.y1); g.stroke();
        g.fillStyle = axis;
        g.fillText(st.xLabel(xv, st.x1 - st.x0), Math.min(G.x1 - 20, Math.max(G.x0 + 20, px)), G.y1 + 5);
      }

      g.save();
      g.beginPath(); g.rect(G.x0, G.y0, G.x1 - G.x0, G.y1 - G.y0); g.clip();
      st.series.forEach(function (se) {
        if (se.hidden) return;
        var prev = null;
        // 최소~최대 띠
        g.fillStyle = se.color; g.globalAlpha = 0.15;
        se.pts.forEach(function (p) {
          if (!p || p[1] == null) return;
          var x = xPos(p[0], G);
          var ya = yPos(st.logY ? Math.max(p[1], 1e-9) : p[1], r, G), yb = yPos(p[2], r, G);
          g.fillRect(x - 0.5, Math.min(ya, yb), 1.5, Math.max(1, Math.abs(ya - yb)));
        });
        g.globalAlpha = 1;
        g.strokeStyle = se.color; g.lineWidth = 1.4;
        g.beginPath();
        se.pts.forEach(function (p) {
          if (!p || p[3] == null || (st.logY && p[3] <= 0)) { prev = null; return; }
          var x = xPos(p[0], G), y = yPos(p[3], r, G);
          // ★ 묶음 사이가 벌어지면(꺼져 있던 구간) 잇지 않는다
          if (!prev || p[0] - prev[0] > st.gap) g.moveTo(x, y); else g.lineTo(x, y);
          prev = p;
        });
        g.stroke();
      });
      g.restore();

      if (drag && drag.x1 != null) {
        g.fillStyle = cssVar('--accent'); g.globalAlpha = 0.18;
        g.fillRect(Math.min(drag.px0, drag.px1), G.y0, Math.abs(drag.px1 - drag.px0), G.y1 - G.y0);
        g.globalAlpha = 1;
      }
      if (hoverX != null) drawHover(G, r);
    }

    function nearest(pts, x) {
      var best = null, bd = Infinity;
      for (var i = 0; i < pts.length; i++) {
        var p = pts[i];
        if (!p) continue;
        var dd = Math.abs(p[0] - x);
        if (dd < bd) { bd = dd; best = p; }
      }
      return best && bd <= Math.max(st.gap, (st.x1 - st.x0) / 200) ? best : null;
    }

    function drawHover(G, r) {
      var px = Math.max(G.x0, Math.min(G.x1, hoverX));
      var x = xAt(px, G);
      g.strokeStyle = cssVar('--axis'); g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(Math.round(px) + .5, G.y0); g.lineTo(Math.round(px) + .5, G.y1); g.stroke();
      g.setLineDash([]);
      if (!tip) return;
      var band = st.bands.filter(function (b) { return x >= b.x0 && x <= b.x1; })[0];
      var rows = st.series.filter(function (se) { return !se.hidden; }).map(function (se) {
        var p = nearest(se.pts, x);
        return p && p[3] != null ? '<div class="tr"><i style="background:' + se.color + '"></i>' +
          core.esc(se.label) + '<b>' + (st.logY ? fmt.torr(p[3]) : fmt.num(p[3], 1)) + ' ' +
          core.esc(se.unit || '') + '</b></div>' : '';
      }).join('');
      tip.hidden = false;
      tip.innerHTML = '<div class="tt">' + core.esc(st.xLabel(x, 0, true)) + '</div>' +
        (band ? '<div class="tb">' + core.esc(band.label) + '</div>' : '') + (rows || '<div class="tr">값 없음</div>');
      var rect = canvas.getBoundingClientRect(), host = tip.offsetParent || d.body;
      var hr = host.getBoundingClientRect();
      var left = rect.left - hr.left + px + 14;
      if (left + 220 > hr.width) left = rect.left - hr.left + px - 230;
      tip.style.left = left + 'px';
      tip.style.top = (rect.top - hr.top + G.y0 + 6) + 'px';
    }

    canvas.addEventListener('mousedown', function (ev) {
      var G = geom(), px = ev.offsetX;
      if (px < G.x0 || px > G.x1) return;
      drag = { px0: px, px1: px, x1: null };
    });
    canvas.addEventListener('mousemove', function (ev) {
      hoverX = ev.offsetX;
      if (drag) { drag.px1 = ev.offsetX; drag.x1 = true; }
      draw();
    });
    canvas.addEventListener('mouseleave', function () {
      hoverX = null; if (tip) tip.hidden = true; draw();
    });
    w.addEventListener('mouseup', function () {
      if (!drag) return;
      var G = geom(), dd = drag;
      drag = null;
      if (dd.x1 && Math.abs(dd.px1 - dd.px0) > 6 && opts.onZoom) {
        var a = xAt(Math.max(G.x0, Math.min(dd.px0, dd.px1)), G);
        var b = xAt(Math.min(G.x1, Math.max(dd.px0, dd.px1)), G);
        opts.onZoom(a, b);
      } else draw();
    });

    return { set: set, draw: draw, state: st };
  }

  w.HistChart = HistChart;
})(window, document);
