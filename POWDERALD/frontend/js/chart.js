/* ============================================================
 * chart.js — 그래프 공용 (실시간 트렌드 · 트렌드 이력 · 데이터 로그 보기). 외부 라이브러리 없이 canvas.
 *
 * ★ 가장 중요한 규칙: 플롯 영역 안에는 글자도 지시선도 두지 않는다.
 *   - 라벨 · 값은 플롯 오른쪽 바깥 '라벨 띠'에만 쓴다.
 *   - 지시선은 플롯 오른쪽 경계(x1)에서, 그 선의 마지막 값 높이에서 시작해 띠 안에서만 꺾는다.
 *     (예전처럼 선의 마지막 점에서 출발하면, 데이터가 오른쪽 끝보다 먼저 끝났을 때 지시선이
 *      플롯을 비스듬히 가로질러 데이터처럼 보인다)
 *   - 선의 끝은 플롯 안에 작은 점으로만 표시한다.
 *   - 선이 오른쪽 끝보다 먼저 끝났으면 띠의 값을 흐리게 하고 그 값의 시각을 붙인다.
 *   - 마우스를 올리면 플롯 안에는 세로 십자선만, 커서 시각의 값은 띠의 값 자리에,
 *     커서 시각은 x 축 아래(플롯 밖)에 보인다.
 *   - 라벨은 띠 높이에 다 안 들어가도 절대 겹치지 않는다 — 줄 간격을 줄이고, 그래도 넘치면
 *     같은 값을 한 줄로 묶고, 그래도 넘치면 띠 안에서만 휠로 스크롤한다. 긴 이름은 말줄임
 *     (마우스를 올리면 전체 이름).
 *
 * 그 밖
 *   - 서버가 구간을 최대 2000 묶음(최소·최대·평균)으로 줄여 보낸다 — 평균은 선, 최소~최대는 옅은 띠.
 *   - ★ 그리기 전에 보이는 구간만 자르고 플롯 폭보다 점이 많으면 화소 열마다 처음 · 최소 · 최대 · 끝만
 *     남긴다(decimate.js — 실시간 1 시간 · 11 계열도 한 번 그리기 수 ms). 띠는 열마다 하나, 1 px 이상일 때만.
 *   - ★ 같은 그림이면 다시 그리지 않는다(v0.4.12): 창이 1 px 이상 움직였거나, 새 점이 마지막 보이는 열의
 *     처음 · 최소 · 최대 · 끝(과 띠) 화소를 바꿨을 때만(Decimate.tailKey — 지난 그림의 y 축으로 화소를 잰다).
 *     점 개수 · 마지막 점이 바뀌었다고 다시 그리지 않는다(1 시간 보기에서 5 Hz 로 다시 그리던 것). 캔버스 크기는 바뀔 때만 바꾸고,
 *     해상도는 실제 보이는 화소(CSS zoom × 기기 배율)에 맞춘다.
 *   - 묶음 사이가 gap 보다 벌어지면(꺼져 있던 구간) 선을 잇지 않는다.
 *   - 압력은 로그 축 — 0 이하 값은 그리지도 축 범위에 넣지도 않는다.
 *   - 설정값 선(…설정 · _sv)은 그 현재값과 같은 색의 점선(colorMap).
 *   - 끌어서 확대(onZoom 이 있을 때). 창 배율(zoom)을 거친 마우스 좌표를 고쳐 쓴다.
 *   - bands: 배경 띠(데이터 로그의 블록·스텝 구간) — 이름은 커서 시각 옆에(플롯 밖).
 *
 * 사용: var c = HistChart(canvas, { onZoom: fn(x0, x1) });
 *       c.set({ series, x0, x1, logY, noNeg, gap, xLabel, bands, dead, tol });
 *       series: [{ label, unit, color, dashed, hidden, pts: [[x, min, max, avg], …] }]
 * ============================================================ */
(function (w, d) {
  'use strict';

  var PAD_L = 52, PAD_R = 196, PAD_T = 10, PAD_B = 30;
  var ROW_H = 13, ROW_MIN = 12, LEAD_W = 14;     // 줄 간격은 12 px 아래로 줄이지 않는다(글자 10 px)
  var PALETTE_N = 12;

  function cssVar(n) { return getComputedStyle(d.documentElement).getPropertyValue(n).trim(); }

  /* ===================== 색 · 설정값 짝 ===================== */
  /** 설정값 이름 → 짝이 되는 현재값 이름. 트렌드 열 키(h1_sv · rf_sv)와 데이터 로그 머리
   *  ('CH1 … 설정 ℃' · 'RF 설정 W' · 'PCV 목표 %') 둘 다 받는다. */
  function baseOf(name) {
    var s = String(name);
    if (s === 'rf_sv') return 'rf_fwd';
    s = s.replace(/_sv$/, '_pv');
    s = s.replace(/ 설정( |$)/, ' 현재$1').replace(/^RF 현재/, 'RF 순방향').replace(/^PCV 목표/, 'PCV 개도');
    return s;
  }

  function isSetpoint(name) {
    var s = String(name);
    return /_sv$/.test(s) || / 설정( |$)/.test(s) || /^PCV 목표/.test(s);
  }

  /** 한 그래프 안의 열 → {color, dashed, on}. 보이는 현재값끼리 색이 겹치지 않고,
   *  설정값은 짝 현재값과 같은 색의 점선. 체크박스 색 표시도 이것을 쓴다. */
  function colorMap(ids, visible) {
    var idx = {}, n = 0, out = {};
    ids.forEach(function (id) {
      if (!visible(id)) return;
      var b = baseOf(id);
      if (!(b in idx)) idx[b] = n++;
    });
    ids.forEach(function (id) {
      var b = baseOf(id);
      out[id] = { color: b in idx ? cssVar('--series-' + ((idx[b] % PALETTE_N) + 1)) : '',
                  dashed: isSetpoint(id), on: b in idx };
    });
    return out;
  }

  /** 체크박스 옆 색 표시 한 개(점선 표시 포함). */
  function swatch(c) {
    if (!c || !c.on) return '<i class="sw-c none"></i>';
    return '<i class="sw-c' + (c.dashed ? ' dash' : '') + '" style="--c:' + c.color + '"></i>';
  }

  /** 데이터 로그 머리 'CH1 이름 현재 ℃' → { label: 'CH1 이름 현재', unit: '℃' } */
  function splitUnit(label) {
    var m = /^(.*?)\s+(Torr|sccm|℃|°C|W|%)$/.exec(String(label));
    return m ? { label: m[1], unit: m[2] === '°C' ? '℃' : m[2] } : { label: String(label), unit: '' };
  }

  /* ===================== 묶음 라벨 ===================== */
  /** 같은 값으로 묶인 이름들 → [{names, tail}]. 이름 = '채널 나머지' — 'CH1 현재' · 'CH1 설정' · 'CH1 Stage·챔버'.
   *  ★ 현재와 설정이 같은 값이면 'CH1 현재 · 설정' 처럼 무엇이 묶였는지 남긴다(예전에는 꼬리 말을 빼
   *    'CH1 · CH1' 이 됐다). 현재만 묶이면 꼬리 말은 모두에 공통일 때만('CH1–CH12'). */
  function mergeSegs(labels) {
    var order = [], byCh = {};
    labels.forEach(function (lb) {
      var parts = String(lb).split(' '), ch = parts[0];
      var kind = isSetpoint(lb) ? 'sp' : 'pv';
      if (!byCh[ch]) { byCh[ch] = { pv: null, sp: null }; order.push(ch); }
      byCh[ch][kind] = lb;
    });
    var both = [], pv = [], sp = [];
    order.forEach(function (ch) {
      var x = byCh[ch];
      if (x.pv && x.sp) both.push(ch); else if (x.sp) sp.push(ch); else pv.push(ch);
    });
    var segs = [];
    if (both.length) segs.push({ names: both, tail: '현재 · 설정' });
    if (pv.length) {
      var last = function (lb) { var w = String(lb).split(' '); return w.length > 1 ? w[w.length - 1] : ''; };
      var lw = last(byCh[pv[0]].pv);
      var common = lw && pv.every(function (ch) { return last(byCh[ch].pv) === lw; });
      // 설정과 함께 묶인 그룹이면 현재 쪽도 '현재'라고 적는다(무엇이 묶였는지)
      segs.push({ names: pv, tail: common ? lw : (both.length || sp.length ? '현재' : '') });
    }
    if (sp.length) segs.push({ names: sp, tail: '설정' });
    return segs;
  }

  function segText(segs) {
    return segs.map(function (s) {
      return (s.raw ? s.names.join(' · ') : runs(s.names)) + (s.tail ? ' ' + s.tail : '');
    }).join(' · ');
  }

  /** ['CH1','CH2','CH3','CH5'] → 'CH1–CH3 · CH5' (이어지는 번호는 줄여 쓴다) */
  function runs(names) {
    var out = [], i = 0;
    while (i < names.length) {
      var m = /^(\D+)(\d+)$/.exec(names[i]), j = i;
      while (m && j + 1 < names.length) {
        var n = /^(\D+)(\d+)$/.exec(names[j + 1]);
        if (!n || n[1] !== m[1] || Number(n[2]) !== Number(m[2]) + (j + 1 - i)) break;
        j++;
      }
      out.push(j - i >= 2 ? names[i] + '–' + names[j] : names.slice(i, j + 1).join(' · '));
      i = j + 1;
    }
    return out.join(' · ');
  }

  /* ===================== 차트 ===================== */
  function HistChart(canvas, opts) {
    opts = opts || {};
    var st = { series: [], x0: 0, x1: 1, logY: false, noNeg: false, gap: Infinity, bands: [],
               dead: false, tol: null, xLabel: function (x) { return String(x); } };
    var drag = null, hoverX = null, hoverY = null, scroll = 0;
    var lastRows = [], lastG = null, lastR = null, lastSig = '', views = [];
    var g = canvas.getContext('2d');

    /** 그림을 바꾸는 것들의 요약 — 같으면 다시 그리지 않는다.
     *  ★ 창이 1 px 이상 움직였거나, 새 점이 마지막 보이는 열의 처음 · 최소 · 최대 · 끝 화소를 바꿨을 때만 달라진다.
     *  열은 그리기와 같은 열(플롯 폭 × 실제 배율), 화소는 지난 그림의 y 축으로(축이 바뀔 만한 새 값은 그 열의
     *  최소 · 최대를 바꾸므로 여기서 걸린다). */
    function signature() {
      var W = canvas.clientWidth, H = canvas.clientHeight;
      var cols = Math.max(1, W - PAD_L - PAD_R);
      var px = (st.x1 - st.x0) / cols;
      var k = (w.devicePixelRatio || 1) * scale();
      var dcols = Math.max(1, Math.round(cols * k));
      var ypx = lastR && lastG ? function (v) {
        if (st.logY && !(v > 0)) return 'n';
        return Math.round(yPos(v, lastR, lastG) * k);
      } : function (v) { return v; };
      var parts = [W, H, k, Math.floor(st.x0 / px), Math.floor(st.x1 / px), st.logY, st.dead, st.gap,
                   st.bands.length, hoverX, hoverY, drag ? drag.px1 : '', scroll];
      st.series.forEach(function (se) {
        parts.push(se.label, se.color, se.hidden ? 1 : 0,
                   se.hidden ? '' : Decimate.tailKey(se.pts, st.x0, st.x1, dcols, ypx));
      });
      return parts.join('|');
    }

    function set(o) {
      for (var k in o) st[k] = o[k];
      var sig = signature();
      if (sig === lastSig) return;
      draw();
    }

    /** CSS zoom 을 거친 실제 보이는 배율(getBoundingClientRect 에는 들어가고 clientWidth 에는 안 들어간다). */
    function scale() {
      var r = canvas.getBoundingClientRect();
      return r.width && canvas.clientWidth ? r.width / canvas.clientWidth : 1;
    }

    function geom() {
      var W = canvas.clientWidth, H = canvas.clientHeight;
      return { W: W, H: H, x0: PAD_L, x1: W - PAD_R, y0: PAD_T, y1: H - PAD_B };
    }

    /** 마우스 좌표 → 캔버스 좌표. ★ 창 배율(app.style.zoom)은 getBoundingClientRect 에는 들어가고
     *  clientWidth 에는 안 들어간다 — 그 비로 고친다(0.8 배율에서 십자선이 75 px 어긋나던 것). */
    function local(ev) {
      var r = canvas.getBoundingClientRect();
      var kx = r.width ? canvas.clientWidth / r.width : 1, ky = r.height ? canvas.clientHeight / r.height : 1;
      return { x: (ev.clientX - r.left) * kx, y: (ev.clientY - r.top) * ky };
    }

    function okVal(v) { return v != null && isFinite(v) && (!st.logY || v > 0); }

    function range() {
      var lo = Infinity, hi = -Infinity;
      function take(v) { if (okVal(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
      st.series.forEach(function (se, si) {
        if (se.hidden) return;
        var vw = views[si];
        // ★ 로그 축은 0 이하를 범위에 넣지 않는다(1E-5 까지 늘어나 선이 바닥에 깔린다).
        //   줄인 점(열마다 처음 · 최소 · 최대 · 끝)과 열 띠(최소 · 최대)로 보면 원래 점 전체와 범위가 같다
        vw.line.forEach(function (p) {
          if (!p || p[0] < st.x0 || p[0] > st.x1) return;
          take(p[1]); take(p[2]); take(p[3]);
        });
        vw.band.forEach(function (b) {
          if (b[0] < st.x0 || b[0] > st.x1) return;
          take(b[1]); take(b[2]);
        });
      });
      if (!isFinite(lo)) { lo = st.logY ? 1e-3 : 0; hi = st.logY ? 1e3 : 1; }
      if (st.logY) {
        var e0 = Math.floor(Math.log(lo) / Math.LN10 + 1e-9);
        var e1 = Math.ceil(Math.log(hi) / Math.LN10 - 1e-9);
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
        var lv = Math.log(v) / Math.LN10;
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

    function valText(v) { return st.logY ? fmt.torr(v) : fmt.num(v, 1); }

    /** 구간 안 마지막 값 {x, v}. 없으면 null. */
    function lastIn(se) {
      for (var i = se.pts.length - 1; i >= 0; i--) {
        var p = se.pts[i];
        if (!p || p[0] > st.x1) continue;
        if (p[0] < st.x0) break;
        if (okVal(p[3])) return { x: p[0], v: p[3] };
      }
      return null;
    }

    /** x 에 가장 가까운 값(허용 간격 안). 점은 x 순서라 이분 탐색. */
    function valueAt(se, x) {
      var pts = se.pts, lo = 0, hi = pts.length - 1;
      if (hi < 0) return null;
      while (lo < hi) {
        var mid = (lo + hi) >> 1;
        if (pts[mid][0] < x) lo = mid + 1; else hi = mid;
      }
      var best = null, bd = Infinity;
      for (var i = Math.max(0, lo - 2); i <= Math.min(pts.length - 1, lo + 2); i++) {
        var p = pts[i];
        if (!p || !okVal(p[3])) continue;
        var dd = Math.abs(p[0] - x);
        if (dd < bd) { bd = dd; best = p; }
      }
      var lim = Math.max(isFinite(st.gap) ? st.gap : 0, (st.x1 - st.x0) / 200);
      return best && bd <= lim ? { x: best[0], v: best[3] } : null;
    }

    function tolerance() {
      if (st.tol != null) return st.tol;
      return Math.max(isFinite(st.gap) ? st.gap : 0, (st.x1 - st.x0) * 0.01);
    }

    /* ---------- 그리기 ---------- */
    function draw() {
      if (!canvas.clientWidth) return;
      lastSig = signature();
      // ★ 해상도 = 실제 보이는 화소(기기 배율 × CSS zoom) — clientWidth × dpr 로만 두면 zoom 0.8~0.9 에서
      //   백킹 스토어가 보이는 크기와 달라 글자가 흐리다. 크기는 바뀔 때만 바꾼다(바꾸면 캔버스를 다시 만든다)
      var k = (w.devicePixelRatio || 1) * scale();
      var G = geom();
      lastG = G;
      var bw = Math.round(G.W * k), bh = Math.round(G.H * k);
      if (canvas.width !== bw) canvas.width = bw;
      if (canvas.height !== bh) canvas.height = bh;
      g.setTransform(bw / G.W, 0, 0, bh / G.H, 0, 0);
      g.clearRect(0, 0, G.W, G.H);
      if (G.x1 <= G.x0 || G.y1 <= G.y0 || st.x1 <= st.x0) return;
      var cols = Math.max(1, Math.round((G.x1 - G.x0) * k));
      views = st.series.map(function (se) {
        return se.hidden ? { line: [], band: [] } : Decimate.view(se.pts, st.x0, st.x1, st.gap, cols);
      });
      var r = range();
      lastR = r;
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
      g.strokeStyle = axis;
      g.beginPath(); g.moveTo(G.x0, G.y1 + .5); g.lineTo(G.x1, G.y1 + .5); g.stroke();

      g.save();
      g.beginPath(); g.rect(G.x0, G.y0, G.x1 - G.x0, G.y1 - G.y0); g.clip();
      st.series.forEach(function (se, si) {
        if (se.hidden) return;
        var vw = views[si];
        // 최소~최대 띠(설정값 점선에는 두지 않는다) — 열마다 하나, 높이 1 px 이상일 때만
        // ★ v0.4.12: 줄이기 전에는 점마다 1.5 px 폭 · 0.15 칸을 겹쳐 그려 점이 많은 열일수록 진했다. 줄인 열은
        //   그 겹침(점 수 × 1.5 px × 열 배율 × 점 높이 ÷ 열 높이)만큼 진하게 — 한 열 폭으로 겹치지 않게 그린다
        if (!se.dashed) {
          g.fillStyle = se.color;
          var colW = (G.x1 - G.x0) / cols, perUnit = (G.y1 - G.y0) / ((r.hi - r.lo) || 1);
          vw.band.forEach(function (bd) {
            var a = bd[1], b = bd[2];
            if (st.logY) {
              if (!(b > 0)) return;
              if (!(a > 0)) a = b;                       // 0 묶음이 띠를 세로로 채우지 않게
            }
            var ya = yPos(a, r, G), yb = yPos(b, r, G), h = Math.abs(ya - yb);
            if (h < 1) return;
            if (bd.col == null) {                          // 줄이지 않은 묶음 — 예전 그대로
              g.globalAlpha = 0.15;
              g.fillRect(xPos(bd[0], G) - 0.5, Math.min(ya, yb), 1.5, h);
              return;
            }
            var rowsPx = bd.n + (st.logY ? 0 : (bd.hs || 0) * perUnit);   // 점마다 칸 높이(최소 1 px)의 합
            var stack = Math.max(1, rowsPx * 1.5 * k / h);
            g.globalAlpha = Math.min(0.9, 1 - Math.pow(0.85, stack));
            g.fillRect(G.x0 + bd.col * colW, Math.min(ya, yb), Math.max(colW, 1 / k), h);
          });
          g.globalAlpha = 1;
        }
        g.strokeStyle = se.color; g.lineWidth = 1.4;
        g.setLineDash(se.dashed ? [5, 3] : []);
        g.beginPath();
        var prev = null;
        vw.line.forEach(function (p) {
          if (!p || !okVal(p[3])) { prev = null; return; }
          var x = xPos(p[0], G), y = yPos(p[3], r, G);
          // ★ 묶음 사이가 벌어지면(꺼져 있던 구간) 잇지 않는다 — decimate 가 그 자리에 null 을 넣는다
          //   (줄인 점끼리는 같은 구간이어도 gap 보다 벌어질 수 있어 여기서 간격으로 보지 않는다)
          if (!prev) g.moveTo(x, y); else g.lineTo(x, y);
          prev = p;
        });
        g.stroke();
        g.setLineDash([]);
        // 선의 끝 — 플롯 안에는 작은 점만
        var last = lastIn(se);
        if (last) {
          g.fillStyle = se.color;
          g.beginPath(); g.arc(xPos(last.x, G), yPos(last.v, r, G), 2.2, 0, Math.PI * 2); g.fill();
        }
      });
      g.restore();

      if (drag && drag.moved) {
        g.fillStyle = cssVar('--accent'); g.globalAlpha = 0.18;
        g.fillRect(Math.min(drag.px0, drag.px1), G.y0, Math.abs(drag.px1 - drag.px0), G.y1 - G.y0);
        g.globalAlpha = 1;
      }
      var hx = hoverX != null && hoverX >= G.x0 && hoverX <= G.x1 ? hoverX : null;
      drawBand(G, r, hx);
      if (hx != null) drawCursor(G, hx);
      lastSig = signature();          // 이번 그림의 y 축으로 다시 — 다음 비교가 같은 축 기준이 되게
    }

    /* ---------- 라벨 띠 ---------- */
    function drawBand(G, r, hx) {
      var xc = hx != null ? xAt(hx, G) : null;
      var tol = tolerance();
      var rows = [];
      st.series.forEach(function (se) {
        if (se.hidden) return;
        // ★ 지시선 · 줄 자리는 늘 그 선의 오른쪽 끝 값 높이(머리 주석의 규칙) — hover 때도 커서 값은 띠의 글자로만
        var end = lastIn(se);
        var info = xc != null ? valueAt(se, xc) : end;
        var stale = !st.dead && xc == null && info && info.x < st.x1 - tol;
        var text = st.dead || !info ? fmt.DASH : valText(info.v);
        if (text !== fmt.DASH && se.unit) text += ' ' + se.unit;
        rows.push({ se: se, label: se.label, text: text, stale: stale,
                    when: stale ? st.xLabel(info.x, 0) : '',
                    v: st.dead || !end ? null : end.v,
                    want: st.dead || !end ? G.y1 : Math.max(G.y0, Math.min(G.y1, yPos(end.v, r, G))) });
      });
      var avail = G.y1 - G.y0;
      var rowH = rows.length ? Math.min(ROW_H, avail / rows.length) : ROW_H;
      if (rowH < ROW_MIN) {
        rows = merge(rows);
        rowH = rows.length ? Math.max(ROW_MIN, Math.min(ROW_H, avail / rows.length)) : ROW_H;
      }
      rows.sort(function (a, b) { return a.want - b.want; });
      var overflow = rows.length * rowH > avail + 0.5;
      if (overflow) {
        // ★ 그래도 넘치면 띠 안에서만 스크롤 — 겹치지 않는 것이 먼저다
        var maxS = rows.length * rowH - avail;
        scroll = Math.max(0, Math.min(scroll, maxS));
        rows.forEach(function (e, i) { e.ly = G.y0 + rowH / 2 + i * rowH - scroll; });
      } else {
        scroll = 0;
        rows.forEach(function (e, i) {
          e.ly = Math.max(e.want, G.y0 + rowH / 2, i ? rows[i - 1].ly + rowH : -Infinity);
        });
        for (var i = rows.length - 1; i >= 0; i--) {
          var lim = i === rows.length - 1 ? G.y1 - rowH / 2 : rows[i + 1].ly - rowH;
          if (rows[i].ly > lim) rows[i].ly = lim;
        }
      }
      lastRows = rows;

      g.save();
      g.beginPath(); g.rect(G.x1 + 1, G.y0 - 2, G.W - G.x1, avail + 4); g.clip();
      var sans = cssVar('--font-sans'), mono = cssVar('--font-mono');
      var ink = cssVar('--ink'), dim = cssVar('--ink-dim'), faint = cssVar('--ink-faint');
      rows.forEach(function (e) {
        if (e.ly < G.y0 - rowH || e.ly > G.y1 + rowH) return;
        var col = e.se.color;
        // 지시선: 플롯 오른쪽 경계(x1)의 값 높이 → 띠 안에서만 꺾는다
        g.strokeStyle = col; g.lineWidth = 1;
        g.setLineDash(e.se.dashed ? [3, 2] : []);
        g.beginPath();
        if (e.v != null) { g.moveTo(G.x1 + 1, e.want); g.lineTo(G.x1 + LEAD_W - 5, e.ly); }
        else g.moveTo(G.x1 + LEAD_W - 5, e.ly);
        g.lineTo(G.x1 + LEAD_W - 1, e.ly);
        g.stroke();
        g.setLineDash([]);
        g.font = '10px ' + mono; g.textAlign = 'right'; g.textBaseline = 'middle';
        var val = e.text + (e.when ? ' · ' + e.when : '');
        g.fillStyle = e.stale || e.v == null ? faint : dim;
        g.fillText(val, G.W - 4, e.ly);
        var vw = g.measureText(val).width;
        g.font = 'bold 10px ' + sans; g.textAlign = 'left';
        g.fillStyle = e.stale ? faint : ink;
        var room = G.W - 4 - vw - 6 - (G.x1 + LEAD_W + 2);
        e.shown = fitLabel(e, room);
        g.fillText(e.shown, G.x1 + LEAD_W + 2, e.ly);
        e.rowH = rowH;
      });
      g.restore();
      if (overflow) {
        g.fillStyle = faint; g.font = '9px ' + sans; g.textAlign = 'right'; g.textBaseline = 'top';
        g.fillText('휠로 더 보기', G.W - 4, G.y1 + 3);
      }
    }

    /** 같은 값(같은 글자)인 줄을 하나로 — 'CH2 · CH3 · CH4 현재 25.0 ℃' */
    function merge(rows) {
      var by = {}, out = [];
      rows.forEach(function (e) {
        var k = e.text + '|' + e.stale + '|' + e.when;
        if (e.text === fmt.DASH && !st.dead) k += '|' + e.label;       // 값 없음은 묶지 않는다
        if (by[k]) { by[k].group.push(e); return; }
        e.group = [e];
        by[k] = e;
        out.push(e);
      });
      out.forEach(function (e) {
        if (e.group.length < 2) return;
        e.full = e.group.map(function (x) { return x.label; }).join(' · ');
        e.segs = mergeSegs(e.group.map(function (x) { return x.label; }));
        e.label = segText(e.segs);
        e.want = e.group.reduce(function (s, x) { return s + x.want; }, 0) / e.group.length;
      });
      return out;
    }

    function fitLabel(e, room) {
      if (g.measureText(e.label).width <= room || !e.segs) return ellipsize(e.label, room);
      // ★ 긴 묶음 — 무엇이 묶였는지('현재 · 설정' 등)는 남기고 채널 목록만 'CH4 외 5' 처럼 줄인다
      var short = e.segs.map(function (s) {
        return { names: s.names.length > 1 ? [s.names[0] + ' 외 ' + (s.names.length - 1)] : s.names,
                 tail: s.tail, raw: true };
      });
      var t = segText(short);
      if (g.measureText(t).width <= room) return t;
      var n = e.group.length;
      t = e.segs[0].names[0] + ' 외 ' + (n - 1) + '줄';
      return ellipsize(t, room);
    }

    function ellipsize(text, room) {
      if (room <= 8) return '';
      if (g.measureText(text).width <= room) return text;
      var t = text;
      while (t.length > 1 && g.measureText(t + '…').width > room) t = t.slice(0, -1);
      return t + '…';
    }

    /* ---------- 커서 ---------- */
    function drawCursor(G, px) {
      var x = xAt(px, G);
      g.strokeStyle = cssVar('--axis'); g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(Math.round(px) + .5, G.y0); g.lineTo(Math.round(px) + .5, G.y1); g.stroke();
      g.setLineDash([]);
      // 커서 시각(과 배경 띠 이름)은 x 축 아래 — 플롯 밖
      var band = st.bands.filter(function (b) { return x >= b.x0 && x <= b.x1; })[0];
      var text = st.xLabel(x, 0, true) + (band ? ' · ' + band.label : '');
      g.font = 'bold 10px ' + cssVar('--font-sans');
      var tw = g.measureText(text).width + 10;
      var bx = Math.max(G.x0, Math.min(G.x1 - tw, px - tw / 2));
      g.fillStyle = cssVar('--panel'); g.fillRect(bx, G.y1 + 2, tw, PAD_B - 4);
      g.strokeStyle = cssVar('--axis'); g.strokeRect(bx + .5, G.y1 + 2.5, tw - 1, PAD_B - 5);
      g.fillStyle = cssVar('--ink'); g.textAlign = 'left'; g.textBaseline = 'middle';
      g.fillText(text, bx + 5, G.y1 + PAD_B / 2);
    }

    /* ---------- 마우스 ---------- */
    canvas.addEventListener('mousedown', function (ev) {
      if (!opts.onZoom) return;
      var G = geom(), p = local(ev);
      if (p.x < G.x0 || p.x > G.x1) return;
      drag = { px0: p.x, px1: p.x, moved: false };
    });
    canvas.addEventListener('mousemove', function (ev) {
      var p = local(ev);
      hoverX = p.x; hoverY = p.y;
      if (drag) { drag.px1 = p.x; drag.moved = Math.abs(drag.px1 - drag.px0) > 2; }
      draw();
      // 띠 위에서는 줄 전체 이름을 브라우저 툴팁으로
      var G = lastG, title = '';
      if (G && p.x > G.x1) {
        lastRows.forEach(function (e) {
          if (Math.abs(p.y - e.ly) <= (e.rowH || ROW_H) / 2) {
            title = (e.full || e.se.label) + ' — ' + e.text + (e.when ? ' (' + e.when + ')' : '');
          }
        });
      }
      if (canvas.title !== title) canvas.title = title;
    });
    canvas.addEventListener('mouseleave', function () { hoverX = hoverY = null; draw(); });
    canvas.addEventListener('wheel', function (ev) {
      var G = lastG, p = local(ev);
      if (!G || p.x <= G.x1) return;
      ev.preventDefault();
      scroll += ev.deltaY > 0 ? ROW_H * 2 : -ROW_H * 2;
      draw();
    }, { passive: false });
    w.addEventListener('mouseup', function () {
      if (!drag) return;
      var G = geom(), dd = drag;
      drag = null;
      if (dd.moved && Math.abs(dd.px1 - dd.px0) > 6 && opts.onZoom) {
        var a = xAt(Math.max(G.x0, Math.min(dd.px0, dd.px1)), G);
        var b = xAt(Math.min(G.x1, Math.max(dd.px0, dd.px1)), G);
        opts.onZoom(a, b);
      } else draw();
    });

    return {
      set: set, draw: draw, state: st,
      /** 시험·확인용: 지금 띠의 줄들과 커서 → 차트 좌표 */
      debug: function () {
        return { rows: lastRows.map(function (e) {
                   return { label: e.shown, ly: e.ly, want: e.want, text: e.text, stale: e.stale };
                 }),
                 hoverX: hoverX, geom: lastG,
                 drawn: views.map(function (v) { return v.line.length; }) };
      },
      /** 시험 · 확인용: 마우스 위치(캔버스 좌표)를 놓고 다시 그린다 */
      hoverAt: function (x, y) { hoverX = x; hoverY = y; draw(); }
    };
  }

  HistChart.colorMap = colorMap;
  HistChart.swatch = swatch;
  HistChart.baseOf = baseOf;
  HistChart.isSetpoint = isSetpoint;
  HistChart.splitUnit = splitUnit;
  HistChart.mergeSegs = mergeSegs;
  HistChart.segText = segText;
  w.HistChart = HistChart;
})(window, document);
