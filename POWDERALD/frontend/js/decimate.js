/* ============================================================
 * decimate.js — 그래프 점 줄이기 · 실시간 점 저장 (DOM 없는 순수 함수 — chart.js · trend.js 가 쓰고,
 * 시험은 node 로 바로 부른다).
 *
 * 점 모양: [x, 최소, 최대, 평균]. x 는 오름차순.
 *
 * view(pts, x0, x1, gap, cols)
 *   보이는 구간(x0 − gap … x1)만 이진 탐색으로 자르고(양끝 바깥 한 점씩 — 선이 경계까지 이어지게),
 *   그 구간의 점이 플롯 폭(cols 열)보다 많으면 화소 열마다 처음 · 최소 · 최대 · 끝(평균 기준)만 남긴다
 *   (계열당 최대 4 × 열 수). 끊김(앞 점과 gap 보다 벌어짐)이 있으면 그 자리에서 열 묶음을 새로 시작하고 line 에
 *   null 을 넣는다 — ★ 그리는 쪽은 null 에서만 선을 끊는다(줄인 점끼리는 같은 구간이어도 gap 보다 벌어질 수 있다).
 *   최소 · 최대 띠는 열마다 하나 [x, 최소, 최대].
 *   반환 { line: [점…], band: [[x, 최소, 최대]…], lo: 첫 보이는 index, hi: 끝 index }
 *
 * Live(rawMs, keepMs) — 실시간 저장: 최근 rawMs 는 받은 그대로, 그보다 오래된 것은 1 s 묶음
 *   (최소 · 최대 · 평균, x = 묶음 안 시각의 평균), keepMs 보다 오래된 것은 앞에서 한 번에 자른다.
 * ============================================================ */
(function (root) {
  'use strict';

  /** x 이상인 첫 index (없으면 길이) */
  function lowerBound(pts, x) {
    var lo = 0, hi = pts.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (pts[mid][0] < x) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  /** x 초과인 첫 index */
  function upperBound(pts, x) {
    var lo = 0, hi = pts.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (pts[mid][0] <= x) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  function finite(v) { return v != null && isFinite(v); }

  function view(pts, x0, x1, gap, cols) {
    pts = pts || [];
    var g = isFinite(gap) ? gap : 0;
    var useGap = isFinite(gap) && gap > 0;      // 0 · Infinity = 끊김을 보지 않는다
    var lo = Math.max(0, lowerBound(pts, x0 - g) - 1);
    var hi = Math.min(pts.length, upperBound(pts, x1) + 1);       // 끝 바깥 한 점
    cols = Math.max(1, Math.floor(cols || 1));
    var line = [], band = [];
    var n = hi - lo;
    var span = x1 - x0 > 0 ? x1 - x0 : 1;
    if (n <= cols) {
      var px = null;
      for (var i = lo; i < hi; i++) {
        var p = pts[i];
        if (!p) continue;
        if (useGap && px !== null && p[0] - px > gap) line.push(null);       // 끊김
        px = p[0];
        line.push(p);
        if (finite(p[1]) && finite(p[2]) && p[2] > p[1]) band.push([p[0], p[1], p[2]]);
      }
      return { line: line, band: band, lo: lo, hi: hi };
    }
    // 열 묶음: 처음 · 최소 · 최대 · 끝(평균 기준), 띠는 열마다 최소~최대 하나
    var cur = -1, first = null, mn = null, mx = null, last = null, bl = Infinity, bh = -Infinity, prevX = null;
    function flush() {
      if (first === null) return;
      var keep = [first, mn, mx, last].filter(function (q) { return q; });
      keep.sort(function (a, b) { return a[0] - b[0]; });
      var seen = null;
      keep.forEach(function (q) { if (q !== seen) { line.push(q); seen = q; } });
      if (bh > bl) band.push([first[0], bl, bh]);
      first = mn = mx = last = null; bl = Infinity; bh = -Infinity;
    }
    for (var j = lo; j < hi; j++) {
      var q = pts[j];
      if (!q) continue;
      var c = Math.floor((q[0] - x0) / span * cols);
      if (q[0] >= x0 && q[0] <= x1) c = Math.min(cols - 1, c);      // x1 의 점이 열을 하나 더 만들지 않게
      // 끊김 — 앞 점과 gap 보다 벌어지면 묶음을 새로 시작하고 선을 끊는다(null)
      var brk = useGap && prevX !== null && q[0] - prevX > gap;
      if (c !== cur || brk) { flush(); cur = c; }
      if (brk) line.push(null);
      prevX = q[0];
      if (first === null) first = q;
      last = q;
      if (finite(q[3])) {
        if (!mn || q[3] < mn[3]) mn = q;
        if (!mx || q[3] > mx[3]) mx = q;
      }
      if (finite(q[1]) && q[1] < bl) bl = q[1];
      if (finite(q[2]) && q[2] > bh) bh = q[2];
    }
    flush();
    return { line: line, band: band, lo: lo, hi: hi };
  }

  /* ---------- 실시간 저장 ---------- */
  function Live(rawMs, keepMs) {
    this.rawMs = rawMs || 120000;
    this.keepMs = keepMs || 3600000;
    this.pts = [];
    this.rawFrom = 0;            // pts[rawFrom..] 는 아직 묶지 않은 받은 그대로의 점
  }

  Live.prototype.push = function (x, v) {
    if (v === null || v === undefined || !isFinite(Number(v))) return;
    var n = Number(v);
    this.pts.push([x, n, n, n]);
    this.compact(x);
  };

  /** 2 분보다 오래된 받은 점을 1 s 묶음으로, 1 시간보다 오래된 것은 앞에서 한 번에 자른다.
   *  ★ shift() 를 점마다 하지 않는다 — 묶을 것이 1 s 이상 쌓였을 때 한 번에 splice. */
  Live.prototype.compact = function (now) {
    var pts = this.pts, edge = now - this.rawMs;
    var end = this.rawFrom;
    while (end < pts.length && pts[end][0] < edge) end++;
    // 마지막 1 s 칸은 아직 더 들어올 수 있다 — 칸이 끝난(그다음 칸 점이 있는) 것만 묶는다
    if (end - this.rawFrom >= 2 && Math.floor(pts[end - 1][0] / 1000) > Math.floor(pts[this.rawFrom][0] / 1000)) {
      var lastSec = Math.floor(pts[end - 1][0] / 1000);
      var stop = end;
      while (stop > this.rawFrom && Math.floor(pts[stop - 1][0] / 1000) === lastSec) stop--;
      var out = [], i = this.rawFrom;
      while (i < stop) {
        var sec = Math.floor(pts[i][0] / 1000), sx = 0, sv = 0, k = 0, mn = Infinity, mx = -Infinity;
        while (i < stop && Math.floor(pts[i][0] / 1000) === sec) {
          var p = pts[i];
          sx += p[0]; sv += p[3]; k++;
          if (p[1] < mn) mn = p[1];
          if (p[2] > mx) mx = p[2];
          i++;
        }
        out.push([sx / k, mn, mx, sv / k]);
      }
      var args = [this.rawFrom, stop - this.rawFrom].concat(out);
      Array.prototype.splice.apply(pts, args);
      this.rawFrom += out.length;
    }
    var cut = lowerBound(pts, now - this.keepMs);
    if (cut > 0) {
      pts.splice(0, cut);
      this.rawFrom = Math.max(0, this.rawFrom - cut);
    }
  };

  Live.prototype.clear = function () { this.pts.length = 0; this.rawFrom = 0; };

  var api = { view: view, Live: Live, lowerBound: lowerBound, upperBound: upperBound };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.Decimate = api;
})(typeof window !== 'undefined' ? window : this);
