/* ============================================================
 * views/strip.js — 상단 상태 띠 (v0.8.0). Operation · P&ID 탭에서만 보인다(CSS — #app[data-tab]).
 *
 *   DEVICE STATE · PROCESS · CHAMBER PRESSURE · PUMP · EXHAUST · VENT · ALARM — 다섯 칸, 높이 84 고정.
 *
 * ★ 판단은 UI.stripModel(순수 — uimodel.js). 여기서는 글자 · 클래스만 바꾼다(5 Hz). 칸 · 자리는 바뀌지 않는다.
 * ★ 같은 상태를 여러 곳에 쓰지 않는다 — 이 두 탭에서는 머리의 상태 · 알람 칩을 숨긴다(띠가 크게 보인다).
 * ★ 남은 PC 끊김 트립의 툴팁은 viewMain.tripTip(규칙 한 곳).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var built = false;
  var TIPS = {
    dev: 'PLC 장비 상태(D00002)와 시퀀서 상태 — 초록 = Ready · Running, 노랑 = Paused · Stopping, 빨강 = Safe Stop, 회색 = Init · Idle',
    proc: '선택한 레시피 · 진행 막대 · 사이클 · 남은 시간, 공정이 아니면 마지막 결과',
    pr: '챔버 압력(CVG 게이지) — 공정이 아니면 베이스 압력 목표와 도달 여부, CM 을 달았으면 CM 값',
    exh: 'Pump = 드라이(메인) 펌프 운전 · IV-E = 배기 격리 밸브(챔버와 펌프 사이) · VV = 벤트 밸브(N2 로 챔버를 대기압까지 채움)',
    alm: '현재 PLC 알람 — 누르면 Alarm/Log 탭. 빨간 테두리 = 확인 전(부저 울림 — Alarm Ack)'
  };

  function el(name) { return core.bind(name, d.getElementById('strip')); }
  function txt(name, v) { var e = el(name); if (e && e.textContent !== v) e.textContent = v; }
  function cls(name, v) { var e = el(name); if (e && e.className !== v) e.className = v; }
  function tip(name, v) { var e = el(name); if (e && e.title !== (v || '')) e.title = v || ''; }

  function build() {
    var box = d.getElementById('strip');
    if (!box || built) return;
    built = true;
    var name = function (k) { return '<div class="sn" title="' + core.esc(TIPS[k]) + '">' + T('strip.' + k) + '</div>'; };
    box.innerHTML =
      '<div class="cell c-dev">' + name('dev') +
      '<div class="sv"><i class="dot" data-bind="stDevDot"></i><span class="kpi" data-bind="stDev">—</span></div>' +
      '<div class="ss" data-bind="stDevSub"></div></div>' +
      '<div class="cell c-proc">' + name('proc') +
      '<div class="sv"><span class="kpi name" data-bind="stProc">—</span></div>' +
      '<div class="bar"><span data-bind="stProcBar"></span></div>' +
      '<div class="ss" data-bind="stProcSub"></div></div>' +
      '<div class="cell c-pr">' + name('pr') +
      '<div class="sv"><span class="kpi mono" data-bind="stPr">—</span><span class="u">Torr · CVG</span>' +
      '<span class="unconf" data-bind="stPrUn" hidden>' + T('s.unconfirmed') + '</span></div>' +
      '<div class="ss" data-bind="stPrSub"></div></div>' +
      '<div class="cell c-exh">' + name('exh') +
      [0, 1, 2].map(function (i) {
        return '<div class="mini"><span class="mn" data-bind="stExN' + i + '"></span><i class="dot" data-bind="stExD' + i + '"></i>' +
          '<span class="mv" data-bind="stExV' + i + '">—</span></div>';
      }).join('') + '</div>' +
      '<button class="cell c-alm" data-bind="stAlm" type="button">' + name('alm') +
      '<div class="sv"><i class="dot" data-bind="stAlmDot"></i><span class="kpi" data-bind="stAlmText">—</span></div>' +
      '<div class="ss" data-bind="stAlmSub"></div></button>';
  }

  function render() { build(); update((core.state || {}).live || {}); }

  function update(t) {
    if (!built) return;
    var s = core.state || {};
    var m = w.UI.stripModel(s, t, core.offline);

    cls('stDevDot', 'dot ' + m.dev.lvl);
    txt('stDev', m.dev.text);
    txt('stDevSub', m.dev.sub);
    cls('stDevSub', 'ss ' + (m.dev.subLvl || ''));
    var trip = '';
    if (m.dev.trip && w.viewMain && w.viewMain.tripTip) {
      trip = 'PC Trip — ' + w.viewMain.tripTip(t.plc || {}, (t.state || {}).code === 6);
    }
    tip('stDevSub', trip || m.dev.title);

    txt('stProc', m.proc.text);
    tip('stProc', m.proc.title);
    cls('stProc', 'kpi name' + (m.proc.dim ? ' dim' : ''));
    var bar = el('stProcBar');
    if (bar) {
      var wd = (m.proc.bar * 100).toFixed(1) + '%';
      if (bar.style.width !== wd) bar.style.width = wd;
      var bc = m.proc.barLvl === 'warn' ? 'warn' : '';
      if (bar.className !== bc) bar.className = bc;
    }
    var ps = el('stProcSub');
    if (ps) {
      core.html(ps, (m.proc.last ? '<span class="chip">Last</span> ' : '') + core.esc(m.proc.sub));
      var pc = 'ss ' + (m.proc.subLvl === 'stop' ? 'alarm' : m.proc.subLvl || '');
      if (ps.className !== pc) ps.className = pc;
      if (ps.title !== m.proc.sub) ps.title = m.proc.sub;
    }

    txt('stPr', m.pr.text);
    cls('stPr', 'kpi mono' + (m.pr.lvl === 'unknown' ? ' dim' : ''));
    var un = el('stPrUn');
    if (un) { un.hidden = !m.pr.unconf; un.title = m.pr.title; }
    txt('stPrSub', m.pr.sub);
    cls('stPrSub', 'ss ' + (m.pr.subLvl || ''));
    tip('stPrSub', m.pr.sub);

    m.exh.forEach(function (x, i) {
      txt('stExN' + i, x.name);
      cls('stExD' + i, 'dot ' + x.lvl);
      txt('stExV' + i, x.text);
      cls('stExV' + i, 'mv ' + x.lvl);
    });

    cls('stAlm', 'cell c-alm' + (m.alm.attn ? ' attn' : ''));
    cls('stAlmDot', 'dot ' + m.alm.lvl);
    txt('stAlmText', m.alm.text);
    txt('stAlmSub', m.alm.sub);
    cls('stAlmSub', 'ss ' + (m.alm.subLvl || ''));
    tip('stAlm', (m.alm.title ? m.alm.title + '\n' : '') + TIPS.alm);
  }

  if (d && d.addEventListener) {
    d.addEventListener('click', function (ev) {
      if (ev.target.closest && ev.target.closest('[data-bind="stAlm"]')) core.setTab('alarm');
    });
  }

  core.register('strip', { render: render, update: update });
  w.viewStrip = { render: render, update: update };
})(window, document);
