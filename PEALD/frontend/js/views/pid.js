/* ============================================================
 * views/pid.js — P&ID 탭 (v0.8.0).
 *
 *   좁은 배치: 배관도 칸 하나(머리 'P&ID' + 범례).
 *   넓은 배치: 왼쪽 배관도(폭 = 그림 높이 × 920/680 — core.fit 이 --pid-w 로) + 오른쪽 [Process · Interlock · Input ·
 *             Recent Events](운전 탭과 같은 부품 — 바인드는 그 칸 안에서만).
 *
 * ★ 그림 자체는 views/schematic.js(#schemSvg — 이 탭 한 곳). 이 파일은 옆 칸만 만든다(배치 · 구조가 바뀔 때만).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var builtSig = '';
  var side = [];          // [{kind, root}]
  var WIDE_PROC = 200, GAP = 6, PANEL_HEAD = 30, BORDER = 2;

  function render(s) {
    var box = core.bind('pidSide');
    if (!box) return;
    var wide = core.layout === 'wide';
    var sig = JSON.stringify([core.layout, (core.box || {}).h, (s.structure || {}).annunciator]);
    if (sig !== builtSig) {
      builtSig = sig;
      side = [];
      if (!wide) {
        box.innerHTML = '';
      } else {
        var ah = w.viewMain.annH(s, 6), vh = w.viewMain.viewsH();
        box.style.setProperty('--ann-h', ah + 'px');
        box.innerHTML = '<div class="panel p-proc" data-part="process"></div><div class="panel p-ann" data-part="ann"></div>' +
          '<div class="panel p-events" data-part="events"></div>';
        Array.prototype.forEach.call(box.querySelectorAll('[data-part]'), function (root) {
          var k = root.getAttribute('data-part');
          if (k === 'process') w.viewMain.process.build(root, s, 'wide');
          else if (k === 'ann') w.Panels.ann.build(root, s, { cols: 6 });
          else w.Panels.events.build(root, s, { rows: Math.floor((vh - WIDE_PROC - ah - 2 * GAP - PANEL_HEAD - BORDER - 8) / 25) });
          side.push({ kind: k, root: root });
        });
      }
    }
    update(s.live || {});
  }

  function update(t) {
    var s = core.state;
    if (!s || !side.length) return;
    var conn = !!(t.plc && t.plc.connected);
    side.forEach(function (x) {
      if (x.kind === 'process') w.viewMain.process.update(x.root, t, conn);
      else w.Panels[x.kind].update(x.root, s, t);
    });
  }

  core.register('pid', { render: render, update: update });
  w.viewPid = { render: render, update: update };
})(window, document);
