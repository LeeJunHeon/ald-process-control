/* ============================================================
 * pidcheck.js — 배관도 모델 검사기 (v0.8.0). 순수 함수 — PidCheck.validate(model) → 어긋난 것 목록(빈 배열이면 통과).
 *
 * ★ 두 프로그램이 바이트까지 같은 파일이다. 모델(views/pid_model.js)의 좌표를 고칠 때는 이 검사를 먼저 돌려 0 건을 지킨다.
 *
 * 규칙(어긋남 글은 '규칙 · 대상 · 좌표'):
 *  ① grid     상자 · 밸브 · 갈래 · 끝 · 꺾는 점 좌표가 G(8) 격자 위
 *  ② hv       배관은 가로 · 세로 토막만
 *  ③ end      배관 양 끝은 포트 · 갈래 · 끝(terminal)만 — 포트는 상자 테두리 위, 모서리에서 16 이상(corner),
 *             그 변에 수직으로 나간다(dir)
 *  ④ box      배관이 어떤 상자 안도 지나지 않고 테두리를 따라가지 않는다
 *  ⑤ overlap  다른 배관 토막과 겹치거나(같은 선 위) 8 미만으로 붙어 나란히 가지 않는다(near)
 *  ⑥ cross    X 교차 없음 · 한 배관 끝이 다른 배관 가운데에 닿으면 그 자리에 tee 가 있어야 한다(tee-missing)
 *  ⑦ tee      tee 갈래는 배관 끝 3 개 이상, joint 는 정확히 2 개(곧은 이음)
 *  ⑧ valve    밸브 · inline(PCV)은 곧은 토막 위, 방향이 맞고(o 'h' ↔ 가로) 토막 끝에서 12 이상
 *  ⑨ text     글자 상자(어림 폭 — terms.js T.textPx 와 같은 규칙, 13 단위)는 그림 안, 서로 · 상자(자기 상자 안 글은
 *             빼고) · 배관 · 밸브와 겹치지 않음 · 글자가 그 상자 폭 안(text-wide)
 *  ⑩ inner    상자 안 글자가 상자 폭 - 16 안 · 상자 테두리 안 · 서로 겹치지 않음
 *  ⑪ hit      누르기 영역(fp)끼리 겹치지 않음 · 밸브 누르기 영역은 18×18 이상이고 이름 글자를 덮음
 * ============================================================ */
(function (root) {
  'use strict';

  var MIN_CORNER = 16, MIN_NEAR = 8, MIN_VALVE_END = 12, LABEL_H = 16;
  var VALVE_HALF_W = 7, VALVE_HALF_H = 6;      // 밸브 아이콘(가로 배관 기준) 반폭 · 반높이

  /** 글자 폭 어림(그림 단위) — 보통 글자는 terms.js T.textPx 와 같은 규칙(굵게 기준 · 넉넉히), 고정폭 숫자는 0.55 em */
  function textW(s, size, mono) {
    s = String(s == null ? '' : s);
    if (mono) {
      var w = 0;
      for (var i = 0; i < s.length; i++) w += s.charCodeAt(i) < 128 ? 0.55 : 1.0;
      return w * size;
    }
    if (root.T && root.T.textPx) return root.T.textPx(s, size);
    var u = 0;
    s.split('').forEach(function (ch) {
      u += ' .'.indexOf(ch) >= 0 ? 0.3 : '()'.indexOf(ch) >= 0 ? 0.36 : ch === '-' ? 0.38
        : '/ftjr'.indexOf(ch) >= 0 ? 0.4 : 'ilI'.indexOf(ch) >= 0 ? 0.3 : 'mwMW'.indexOf(ch) >= 0 ? 0.9
          : /[0-9]/.test(ch) ? 0.6 : /[A-Z]/.test(ch) ? 0.68 : /[a-z]/.test(ch) ? 0.56 : 1.0;
    });
    return u * size * 1.05;
  }

  function onGrid(v, G) { return Math.abs(v / G - Math.round(v / G)) < 1e-9; }
  function hit(a, b) { return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h; }
  function inside(a, b) { return a.x >= b.x && a.y >= b.y && a.x + a.w <= b.x + b.w && a.y + a.h <= b.y + b.h; }
  function rectU(a, b) {
    var x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
    return { x: x, y: y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y };
  }

  function boxById(m, id) { for (var i = 0; i < m.boxes.length; i++) if (m.boxes[i].id === id) return m.boxes[i]; return null; }

  /** 포트 좌표 · 나가는 방향 */
  function portXY(b, name) {
    var p = (b.ports || {})[name];
    if (!p) return null;
    var side = p[0], k = p[1];
    if (side === 'l') return { x: b.x, y: b.y + k, dx: -1, dy: 0, side: side, k: k, len: b.h };
    if (side === 'r') return { x: b.x + b.w, y: b.y + k, dx: 1, dy: 0, side: side, k: k, len: b.h };
    if (side === 't') return { x: b.x + k, y: b.y, dx: 0, dy: -1, side: side, k: k, len: b.w };
    return { x: b.x + k, y: b.y + b.h, dx: 0, dy: 1, side: side, k: k, len: b.w };
  }

  /** 배관 경로의 점 하나 → {x, y, ref: {type, id}} */
  function point(m, p) {
    if (Array.isArray(p)) return { x: p[0], y: p[1], ref: { type: 'bend' } };
    if (typeof p === 'string' && p.indexOf('.') > 0) {
      var i = p.indexOf('.'), b = boxById(m, p.slice(0, i));
      var q = b && portXY(b, p.slice(i + 1));
      return q ? { x: q.x, y: q.y, ref: { type: 'port', id: p, box: b.id, port: q } } : null;
    }
    var j = (m.junctions || {})[p];
    if (j) return { x: j.x, y: j.y, ref: { type: 'junction', id: p, kind: j.kind } };
    var t = (m.terminals || {})[p];
    if (t) return { x: t.x, y: t.y, ref: { type: 'terminal', id: p } };
    return null;
  }

  /** 모델 → 배관 토막 [{key, h, a, b, c, p0, p1, i(경로 안 몇 번째), n(토막 수)}] · 점 목록 */
  function segments(m, bad) {
    var segs = [], paths = [];
    (m.pipes || []).forEach(function (pp) {
      var pts = pp.path.map(function (p) {
        var q = point(m, p);
        if (!q) bad.push('end · ' + pp.key + ' · unknown point ' + JSON.stringify(p));
        return q;
      });
      if (pts.some(function (q) { return !q; })) return;
      paths.push({ key: pp.key, pts: pts });
      for (var i = 1; i < pts.length; i++) {
        var a = pts[i - 1], b = pts[i];
        if (a.x !== b.x && a.y !== b.y) { bad.push('hv · ' + pp.key + ' · ' + a.x + ',' + a.y + ' → ' + b.x + ',' + b.y); continue; }
        if (a.x === b.x && a.y === b.y) { bad.push('hv · ' + pp.key + ' · zero segment ' + a.x + ',' + a.y); continue; }
        var h = a.y === b.y;
        segs.push({ key: pp.key, h: h, a: h ? Math.min(a.x, b.x) : Math.min(a.y, b.y), b: h ? Math.max(a.x, b.x) : Math.max(a.y, b.y),
                    c: h ? a.y : a.x, p0: a, p1: b, i: i - 1, n: pts.length - 1 });
      }
    });
    return { segs: segs, paths: paths };
  }

  function segRect(s) { return s.h ? { x: s.a, y: s.c, w: s.b - s.a, h: 0 } : { x: s.c, y: s.a, w: 0, h: s.b - s.a }; }
  function onSeg(s, x, y) { return s.h ? (y === s.c && x >= s.a && x <= s.b) : (x === s.c && y >= s.a && y <= s.b); }
  function isEnd(s, x, y) { return (s.p0.x === x && s.p0.y === y) || (s.p1.x === x && s.p1.y === y); }

  /** 밸브 아이콘 상자 · 이름 글자 상자 · 누르기 영역 — 그리기(schematic.js)와 같은 규칙 */
  function valveGeom(v) {
    var hw = v.o === 'h' ? VALVE_HALF_W : VALVE_HALF_H, hh = v.o === 'h' ? VALVE_HALF_H : VALVE_HALF_W;
    var icon = { x: v.x - hw, y: v.y - hh, w: 2 * hw, h: 2 * hh };
    var name = v.tag + (v.note ? ' (' + v.note + ')' : '');
    var tw = Math.ceil(textW(name, 13));
    var label = v.o === 'h' ? { x: v.x - tw / 2, y: v.y - 27, w: tw, h: LABEL_H } : { x: v.x + 13, y: v.y - 8, w: tw, h: LABEL_H };
    var hitR;
    if (v.o === 'h') {
      var half = Math.max(9, Math.ceil(tw / 2) + 2);
      hitR = { x: v.x - half, y: v.y - 27, w: 2 * half, h: 36 };
    } else {
      hitR = { x: v.x - 9, y: v.y - 9, w: 9 + 13 + tw + 3, h: 18 };
    }
    return { icon: icon, label: label, hit: hitR, name: name };
  }

  /** 글자 자리(labels) — 상자(왼쪽 위 · 폭 · 높이 16)와 실제 글자 폭 */
  function labelGeom(l) {
    var size = l.size || 13;
    var txt = l.text || '';
    var w = textW(txt, size, false) + (l.kind === 'title' ? 0.5 * txt.length : 0);     // 제목은 글자 사이 0.5
    if (l.bind) w += (txt ? 12 : 0) + textW(l.worst || '', size, true);
    return { rect: { x: l.x, y: l.y, w: l.w, h: LABEL_H }, need: w };
  }

  /** 상자 안 글자 — 그리기와 같은 자리(줄마다 {text, bind, worst, size, mono, x(상자 기준), y(기준선), anchor}) */
  function innerRects(b) {
    return (b.lines || []).filter(function (ln) { return !ln.rule; }).map(function (ln) {
      var size = ln.size || 13, s = ln.bind ? (ln.worst || '') : (ln.text || '');
      var w = textW(s, size, !!ln.mono), x = b.x + (ln.x == null ? 8 : ln.x);
      var x0 = ln.anchor === 'end' ? x - w : ln.anchor === 'middle' ? x - w / 2 : x;
      // 글자 높이 — 기준선 위 1.16 em · 아래 0.29 em(넉넉히)
      return { s: s, rect: { x: x0, y: b.y + ln.y - 1.16 * size, w: w, h: 1.45 * size } };
    });
  }

  function validate(m) {
    var bad = [], G = m.G || 8;
    var W = m.W, H = m.H;
    // ① 격자
    (m.boxes || []).forEach(function (b) {
      [b.x, b.y, b.w, b.h].forEach(function (v) { if (!onGrid(v, G)) bad.push('grid · box ' + b.id + ' · ' + [b.x, b.y, b.w, b.h]); });
    });
    (m.valves || []).forEach(function (v) { if (!onGrid(v.x, G) || !onGrid(v.y, G)) bad.push('grid · valve ' + v.tag + ' · ' + v.x + ',' + v.y); });
    (m.inline || []).forEach(function (v) { if (!onGrid(v.x, G) || !onGrid(v.y, G)) bad.push('grid · inline ' + v.id + ' · ' + v.x + ',' + v.y); });
    Object.keys(m.junctions || {}).forEach(function (k) {
      var j = m.junctions[k]; if (!onGrid(j.x, G) || !onGrid(j.y, G)) bad.push('grid · junction ' + k + ' · ' + j.x + ',' + j.y);
    });
    Object.keys(m.terminals || {}).forEach(function (k) {
      var t = m.terminals[k]; if (!onGrid(t.x, G) || !onGrid(t.y, G)) bad.push('grid · terminal ' + k + ' · ' + t.x + ',' + t.y);
    });
    (m.pipes || []).forEach(function (pp) {
      pp.path.forEach(function (p) { if (Array.isArray(p) && (!onGrid(p[0], G) || !onGrid(p[1], G))) bad.push('grid · bend ' + pp.key + ' · ' + p); });
    });
    // 포트 — 테두리 위 · 모서리에서 16 이상
    (m.boxes || []).forEach(function (b) {
      Object.keys(b.ports || {}).forEach(function (n) {
        var q = portXY(b, n);
        if (q.k < MIN_CORNER || q.k > q.len - MIN_CORNER) bad.push('corner · ' + b.id + '.' + n + ' · ' + Math.min(q.k, q.len - q.k) + ' from corner');
        if (!onGrid(q.x, G) || !onGrid(q.y, G)) bad.push('grid · port ' + b.id + '.' + n + ' · ' + q.x + ',' + q.y);
      });
    });

    var sg = segments(m, bad), segs = sg.segs;
    // ③ 끝 — 포트 · 갈래 · 끝만, 포트에서는 수직으로 나간다
    sg.paths.forEach(function (p) {
      [[p.pts[0], p.pts[1]], [p.pts[p.pts.length - 1], p.pts[p.pts.length - 2]]].forEach(function (e) {
        var a = e[0], nx = e[1];
        if (a.ref.type === 'bend') { bad.push('end · ' + p.key + ' · loose end ' + a.x + ',' + a.y); return; }
        if (a.ref.type === 'port' && nx) {
          var q = a.ref.port, dx = Math.sign(nx.x - a.x), dy = Math.sign(nx.y - a.y);
          if (dx !== q.dx || dy !== q.dy) bad.push('dir · ' + p.key + ' · ' + a.ref.id + ' not perpendicular');
        }
      });
      for (var i = 1; i < p.pts.length - 1; i++) {
        if (p.pts[i].ref.type === 'port' || p.pts[i].ref.type === 'terminal') bad.push('end · ' + p.key + ' · ' + p.pts[i].ref.id + ' in the middle');
      }
    });
    // ④ 상자
    segs.forEach(function (s) {
      (m.boxes || []).forEach(function (b) {
        var r = s.h ? { lo: b.x, hi: b.x + b.w, c0: b.y, c1: b.y + b.h } : { lo: b.y, hi: b.y + b.h, c0: b.x, c1: b.x + b.w };
        if (s.c < r.c0 || s.c > r.c1) return;
        var lo = Math.max(s.a, r.lo), hi = Math.min(s.b, r.hi);
        if (hi < lo) return;
        if (hi === lo) {                // 한 점만 닿음 — 그 상자의 포트인 이 토막 끝이면 된다
          var x = s.h ? lo : s.c, y = s.h ? s.c : lo;
          var ok = [s.p0, s.p1].some(function (p) { return p.x === x && p.y === y && p.ref.type === 'port' && p.ref.box === b.id; });
          if (!ok) bad.push('box · ' + s.key + ' touches ' + b.id + ' · ' + x + ',' + y);
          return;
        }
        bad.push('box · ' + s.key + ' through ' + b.id + ' · ' + (s.h ? lo + '..' + hi + ',' + s.c : s.c + ',' + lo + '..' + hi));
      });
    });
    // ⑤ ⑥ 토막끼리
    var J = m.junctions || {};
    function junctionAt(x, y) {
      for (var k in J) if (J[k].x === x && J[k].y === y) return k;
      return '';
    }
    for (var i = 0; i < segs.length; i++) {
      for (var j = i + 1; j < segs.length; j++) {
        var s = segs[i], t = segs[j];
        var same = s.key === t.key && Math.abs(s.i - t.i) <= 1;
        if (s.h === t.h) {
          var ov = Math.min(s.b, t.b) - Math.max(s.a, t.a), dist = Math.abs(s.c - t.c);
          if (ov > 0 && dist === 0) bad.push('overlap · ' + s.key + ' / ' + t.key + ' · ' + (s.h ? 'y' : 'x') + '=' + s.c);
          else if (ov > 0 && dist < MIN_NEAR) bad.push('near · ' + s.key + ' / ' + t.key + ' · ' + dist + ' apart');
          continue;
        }
        if (same) continue;             // 같은 배관의 꺾임
        var Hs = s.h ? s : t, Vs = s.h ? t : s, x = Vs.c, y = Hs.c;
        if (x < Hs.a || x > Hs.b || y < Vs.a || y > Vs.b) continue;
        var eH = isEnd(Hs, x, y), eV = isEnd(Vs, x, y), jn = junctionAt(x, y);
        if (eH && eV) {
          if (!jn) bad.push('tee-missing · ' + Hs.key + ' / ' + Vs.key + ' · ' + x + ',' + y);
          continue;
        }
        // 한 배관 끝이 다른 배관 가운데에 닿음 — 그 배관을 그 자리의 tee 갈래에서 나눠야 한다(갈래 점만 두면 안 된다)
        if (eH || eV) { bad.push('tee-missing · ' + Hs.key + ' / ' + Vs.key + ' · ' + x + ',' + y); continue; }
        bad.push('cross · ' + Hs.key + ' / ' + Vs.key + ' · ' + x + ',' + y);
      }
    }
    // ⑦ 갈래 — 배관 끝 수(경로 가운데로 지나가면 2)
    Object.keys(J).forEach(function (k) {
      var jj = J[k], ends = 0, dirs = [];
      sg.paths.forEach(function (p) {
        p.pts.forEach(function (q, idx) {
          if (q.ref.type !== 'junction' || q.ref.id !== k) return;
          [p.pts[idx - 1], p.pts[idx + 1]].forEach(function (nb) {
            if (!nb) return;
            ends++;
            dirs.push(nb.y === jj.y ? 'h' : 'v');
          });
        });
      });
      if (jj.kind === 'tee' && ends < 3) bad.push('tee · ' + k + ' · ' + ends + ' ends');
      if (jj.kind === 'joint' && (ends !== 2 || dirs[0] !== dirs[1])) bad.push('joint · ' + k + ' · ' + ends + ' ends ' + dirs.join(''));
    });
    // ⑧ 밸브 · inline
    function onStraight(o, x, y, who) {
      var cand = segs.filter(function (s) { return onSeg(s, x, y); });
      if (!cand.length) { bad.push('valve · ' + who + ' · not on a pipe · ' + x + ',' + y); return; }
      var s = cand[0];
      if (o && (o === 'h') !== s.h) bad.push('valve · ' + who + ' · orientation ' + o);
      var d = s.h ? Math.min(x - s.a, s.b - x) : Math.min(y - s.a, s.b - y);
      if (d < MIN_VALVE_END) bad.push('valve · ' + who + ' · ' + d + ' from segment end · ' + x + ',' + y);
    }
    (m.valves || []).forEach(function (v) { onStraight(v.o, v.x, v.y, v.tag); });
    (m.inline || []).forEach(function (v) { onStraight('', v.x, v.y, v.id); });

    // ⑨ ⑩ 글자
    var texts = [];
    (m.labels || []).forEach(function (l) {
      var g = labelGeom(l), name = l.text || l.bind;
      if (g.need > l.w + 1e-9) bad.push('text-wide · ' + name + ' · ' + Math.ceil(g.need) + ' > ' + l.w);
      if ((l.size || 13) < 13) bad.push('text-small · ' + name);
      texts.push({ s: name, r: g.rect, own: '' });
    });
    (m.valves || []).forEach(function (v) { texts.push({ s: v.tag, r: valveGeom(v).label, own: '' }); });
    (m.boxes || []).forEach(function (b) {
      var qs = innerRects(b);
      qs.forEach(function (q, qi) {
        if (q.rect.w > b.w - 16 + 1e-9) bad.push('inner · ' + b.id + ' · ' + q.s + ' ' + Math.ceil(q.rect.w) + ' > ' + (b.w - 16));
        if (!inside(q.rect, { x: b.x + 1, y: b.y + 1, w: b.w - 2, h: b.h - 2 })) bad.push('inner · ' + b.id + ' · ' + q.s + ' outside box');
        for (var qj = qi + 1; qj < qs.length; qj++) {
          if (hit(q.rect, qs[qj].rect)) bad.push('inner · ' + b.id + ' · ' + q.s + ' / ' + qs[qj].s);
        }
      });
    });
    texts.forEach(function (t) {
      var r = t.r;
      if (r.x < 0 || r.y < 0 || r.x + r.w > W || r.y + r.h > H) bad.push('text · ' + t.s + ' outside drawing');
    });
    for (var a = 0; a < texts.length; a++) {
      for (var c = a + 1; c < texts.length; c++) {
        if (hit(texts[a].r, texts[c].r)) bad.push('text · ' + texts[a].s + ' / ' + texts[c].s);
      }
    }
    texts.forEach(function (t) {
      (m.boxes || []).forEach(function (b) { if (hit(t.r, b)) bad.push('text · ' + t.s + ' / box ' + b.id); });
      segs.forEach(function (s) {
        var sr = segRect(s), pad = { x: sr.x - 1, y: sr.y - 1, w: sr.w + 2, h: sr.h + 2 };
        if (hit(t.r, pad)) bad.push('text · ' + t.s + ' / pipe ' + s.key);
      });
      (m.valves || []).forEach(function (v) { if (hit(t.r, valveGeom(v).icon)) bad.push('text · ' + t.s + ' / valve ' + v.tag); });
      (m.inline || []).forEach(function (v) {
        if (hit(t.r, { x: v.x - v.r, y: v.y - v.r, w: 2 * v.r, h: 2 * v.r })) bad.push('text · ' + t.s + ' / inline ' + v.id);
      });
    });

    // ⑪ 누르기 영역
    var hits = hitAreas(m);
    for (var e = 0; e < hits.length; e++) {
      for (var f = e + 1; f < hits.length; f++) {
        if (hit(hits[e].r, hits[f].r)) bad.push('hit · ' + hits[e].fp + ' / ' + hits[f].fp);
      }
    }
    (m.valves || []).forEach(function (v) {
      if (v.fp === null) return;
      var g = valveGeom(v);
      if (g.hit.w < 18 || g.hit.h < 18) bad.push('hit · ' + v.tag + ' · smaller than 18');
      if (!inside(g.label, g.hit)) bad.push('hit · ' + v.tag + ' · does not cover name');
    });
    return bad;
  }

  /** 누르기 영역 [{fp, r, grp, title}] — 그리기(schematic.js)도 이것을 쓴다 */
  function hitAreas(m) {
    var out = [];
    (m.boxes || []).forEach(function (b) {
      if (!b.fp) return;
      var r = { x: b.x, y: b.y, w: b.w, h: b.h };
      if (b.hitLabel) r = rectU(r, labelGeom(b.hitLabel).rect);
      out.push({ fp: b.fp, r: r, grp: b.grp || 0, title: b.title || b.id });
    });
    (m.valves || []).forEach(function (v) {
      if (v.fp === null) return;
      out.push({ fp: v.fp || ('valve:' + v.tag), r: valveGeom(v).hit, grp: v.grp || 0, title: v.tag });
    });
    (m.inline || []).forEach(function (v) {
      if (!v.fp) return;
      var r = { x: v.x - v.r, y: v.y - v.r, w: 2 * v.r, h: 2 * v.r };
      if (v.label) r = rectU(r, labelGeom(v.label).rect);
      out.push({ fp: v.fp, r: r, grp: v.grp || 0, title: v.title || v.id });
    });
    (m.labels || []).forEach(function (l) {
      if (l.fp) out.push({ fp: l.fp, r: labelGeom(l).rect, grp: l.grp || 0, title: l.title || l.text });
    });
    (m.boxes || []).forEach(function (b) {
      (b.rows || []).forEach(function (rw) {
        if (rw.fp) out.push({ fp: rw.fp, r: { x: b.x + 4, y: b.y + rw.y - 15, w: b.w - 8, h: 20 }, grp: b.grp || 0, title: rw.title || rw.fp });
      });
    });
    return out;
  }

  var PidCheck = { validate: validate, hitAreas: hitAreas, valveGeom: valveGeom, labelGeom: labelGeom,
                   innerRects: innerRects, portXY: portXY, point: point, textW: textW, boxById: boxById };
  if (typeof module !== 'undefined' && module.exports) module.exports = PidCheck;
  else root.PidCheck = PidCheck;
})(typeof window !== 'undefined' ? window : this);
