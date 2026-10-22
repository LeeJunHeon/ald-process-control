/* ============================================================
 * views/panels.js — 운전 탭 · P&ID 탭이 같이 쓰는 칸 부품 (v0.8.0).
 *
 *   start   Start Conditions — 자리 10 개 고정(장비 구조 START_SLOTS, 좁은 2×5 · 넓은 5×2)
 *   ann     Interlock · Input — 램프판(장비 구조 ANNUNCIATOR 의 줄 · 자리 그대로, 좁은 8 열 · 넓은 6 열)
 *   valves  Valves — 밸브 띠(장비 구조 VALVE_STRIP 순서 그대로)
 *   events  Recent Events — 넓은 배치만(줄 수는 배치가 정할 때 칸 높이로 한 번)
 *
 * ★ 부품마다 build(root, state, opt) · update(root, state, live). 바인드는 root 안에서만(core.bind(name, root)) —
 *   같은 부품이 두 탭에 있어도 섞이지 않는다.
 * ★ 칸 · 자리 수는 build 때 장비 구조로만 정해진다. update 는 글자 · 클래스 · title 만 바꾼다(core.html 은 같으면 안 씀).
 *   항목이 몇 개 문제든 모든 항목이 늘 보인다 — 정상 항목을 한 칸으로 묶거나 넘친 것을 숨기지 않는다.
 * ★ 상태는 글자 굵기 · 자리가 아니라 색 · 점 · 테두리로만 말한다(칸 폭이 흔들리지 않게).
 * ============================================================ */
(function (w, d) {
  'use strict';

  var UI = w.UI;
  function esc(s) { return core.esc(s); }
  function setCls(el, cls) { if (el && el.className !== cls) el.className = cls; }
  function setTitle(el, t) { if (el && el.title !== t) el.title = t || ''; }
  function setTxt(el, t) { if (el && el.textContent !== t) el.textContent = t; }
  function conn(t) { return !t.offline && !!(t.plc && t.plc.connected) && !(t.plc || {}).stalled; }

  /* ===================== Start Conditions ===================== */
  // 아이콘(SVG) — ok 초록 체크 · no 빨강 × · wait 노랑 시계 · na 회색 — · unknown 회색 점선 ?
  var ICON = {
    ok: '<svg viewBox="0 0 16 16"><path d="M3.5 8.5 L6.5 11.5 L12.5 4.5" fill="none" stroke-width="2"/></svg>',
    no: '<svg viewBox="0 0 16 16"><path d="M4 4 L12 12 M12 4 L4 12" fill="none" stroke-width="2"/></svg>',
    wait: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5" fill="none" stroke-width="1.6"/>' +
          '<path d="M8 5 V8 L10 9.5" fill="none" stroke-width="1.6"/></svg>',
    na: '<svg viewBox="0 0 16 16"><path d="M4 8 H12" fill="none" stroke-width="2"/></svg>',
    unknown: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6" fill="none" stroke-width="1.2" stroke-dasharray="2 2"/>' +
             '<path d="M6.3 6.3 A1.8 1.8 0 1 1 8 8.4 V9.3" fill="none" stroke-width="1.3"/><circle cx="8" cy="11.3" r="0.8"/></svg>'
  };
  var ICON_TIP = { ok: 'OK — 조건 충족', no: 'Not ready — 이 조건이 시작을 막음', wait: '시작 뒤 베이스 압력까지 기다림(시작은 막지 않음)',
                   na: '이 장비 · 레시피에는 해당 없음', unknown: '연결 · 장비 ID 를 확인하기 전이라 모름' };

  var start = {
    build: function (root, s) {
      var slots = ((s.structure || {}).start_slots || []);
      root.innerHTML =
        '<div class="panel-head"><span>' + T('panel.start') + '</span>' +
        '<span class="note"><span class="chip" data-bind="scSum">—</span></span></div>' +
        '<div class="panel-body sc-body"><div class="scgrid">' + slots.map(function (sl, i) {
          var o3 = (sl.keys || []).indexOf('o3') >= 0;
          // v0.8.1: 'O3 Line ON' 단추는 설명 줄 자리에(단추가 보이면 설명 글은 숨기고 title 로) — 자리 이름이 줄지 않는다
          return '<div class="sc" data-sc="' + i + '"><span class="sci unknown" data-bind="sci' + i + '"></span>' +
            '<span class="sct"><b>' + esc(sl.label) + '</b><span class="scd" data-bind="scd' + i + '">—</span>' +
            (o3 ? '<button class="btn xs sc-o3" data-fp="o3:O3" data-bind="sco3_' + i + '" hidden>' + T('b.o3On') + '</button>' : '') +
            '</span></div>';
        }).join('') + '</div></div>';
      root._slots = slots;
    },
    update: function (root, s, t) {
      var slots = root._slots || [];
      var p = t.process || {};
      var m = UI.startSlots(slots, p, { conn: conn(t), running: !!p.running });
      m.items.forEach(function (it, i) {
        var ic = core.bind('sci' + i, root);
        if (ic) {
          setCls(ic, 'sci ' + it.icon);
          core.html(ic, ICON[it.icon]);
          setTitle(ic, ICON_TIP[it.icon]);
        }
        var dd = core.bind('scd' + i, root);
        setTxt(dd, it.text || '—');
        var cell = dd && dd.closest('.sc');
        setTitle(cell, it.title || (it.label + ' — ' + (it.text || '')));
        var b = core.bind('sco3_' + i, root);
        if (b) {
          b.hidden = !it.o3btn;
          if (dd) dd.hidden = !!it.o3btn;
          setTitle(b, it.o3btn ? (it.text || '') + ' — 누르면 O3 조작 창' : '');
        }
      });
      var sum = core.bind('scSum', root);
      setTxt(sum, m.summary.text);
      setCls(sum, 'chip ' + (m.summary.lvl === 'alarm' ? 'stop' : m.summary.lvl));
      root.classList.toggle('dimmed', m.dim);
    }
  };

  /* ===================== Interlock · Input (램프판) ===================== */
  var LEGEND = [['ok', 's.okOn', '초록 — 정상 조건 충족 · 허가 있음 · 켜짐'],
                ['warn', 's.notReadyLamp', '노랑 — 허가 조건이 아직 안 됨(Not ready)'],
                ['alarm', 's.alarm', '빨강 — 이상(알람 입력 · 안전 정지 요구 · 동시 요청)'],
                ['off', 's.offNormal', '빈 원 — 꺼짐(정상)']];

  var ann = {
    build: function (root, s, opt) {
      var cols = (opt || {}).cols || 8;
      var rows = UI.annunciator(s.structure, {}, false);
      var r0 = 1, cells = '';
      rows.forEach(function (row) {
        var n = Math.max(1, Math.ceil(row.items.length / cols));
        cells += '<span class="ann-g" style="grid-row:' + r0 + ' / span ' + n + '" title="' + esc(row.title) + '">' +
          esc(T('ann.' + row.group, String(row.group).toUpperCase())) + '</span>';
        row.items.forEach(function (it, i) {
          cells += '<span class="lamp unknown" data-lamp="' + esc(it.key) + '" style="grid-row:' + (r0 + Math.floor(i / cols)) +
            ';grid-column:' + (2 + i % cols) + '" title="' + esc(it.title) + '"><i></i>' + esc(it.name) + '</span>';
        });
        r0 += n;
      });
      root.innerHTML =
        '<div class="panel-head"><span>' + T('panel.ann') + '</span><span class="note ann-legend">' +
        LEGEND.map(function (l) { return '<span title="' + esc(l[2]) + '"><i class="lamp-dot ' + l[0] + '"></i>' + T(l[1]) + '</span>'; }).join('') +
        '</span></div><div class="panel-body ann-body"><div class="anngrid" style="grid-template-columns:56px repeat(' + cols +
        ', minmax(max-content, 1fr))">' + cells + '</div></div>';
      root._lamps = Array.prototype.slice.call(root.querySelectorAll('[data-lamp]'));
    },
    update: function (root, s, t) {
      var c = conn(t);
      var rows = UI.annunciator(s.structure, t, c);
      var k = 0, lamps = root._lamps || [];
      rows.forEach(function (row) {
        row.items.forEach(function (it) {
          var el = lamps[k++];
          if (el) setCls(el, 'lamp ' + it.lvl);
        });
      });
    }
  };

  /* ===================== Valves (밸브 띠) =====================
   * v0.8.1: 값 타일(PCV · RF · O3 · BPMP)은 가장 긴 값이 들어가는 고정 폭(style.css .vt.val[data-vt=…]) — 값이 바뀌어도
   * 그 타일과 뒤 타일의 자리가 그대로다 */
  var VICON = '<svg class="vi" viewBox="0 0 22 14"><path d="M1.5,1.5 L11,7 L1.5,12.5 Z M20.5,1.5 L11,7 L20.5,12.5 Z"/></svg>';

  var valves = {
    build: function (root, s) {
      var rv = (s.recipe_limits || {}).recipe_valves || [];
      var m = UI.valveStrip(s.structure, {}, false, { recipeValves: rv });
      root.innerHTML = '<div class="vs-name"><b title="밸브 띠 — 누르면 그 옆에 조작 창">' + T('panel.valves') + '</b>' +
        '<span class="vs-pend" data-bind="vsPend" hidden></span></div><div class="vs-groups">' +
        m.groups.map(function (g) {
          return '<div class="vs-grp">' + g.map(function (it) {
            if (it.kind === 'value') {
              return '<button class="vt val unknown" data-vt="' + esc(it.key) + '"' + (it.fp ? ' data-fp="' + esc(it.fp) + '"' : '') +
                '><span class="vval mono">—</span><span class="vtag">' + esc(it.label) + '</span></button>';
            }
            if (it.auto) {
              return '<div class="vt auto unknown" data-vt="' + esc(it.key) + '">' + VICON + '<span class="vtag">' + esc(it.label) +
                '</span><span class="vauto">' + T('s.auto') + '</span></div>';
            }
            return '<button class="vt unknown" data-vt="' + esc(it.key) + '"' + (it.fp ? ' data-fp="' + esc(it.fp) + '"' : '') + '>' +
              VICON + '<span class="vtag">' + esc(it.label) + '</span></button>';
          }).join('') + '</div>';
        }).join('') + '</div>';
      root._tiles = Array.prototype.slice.call(root.querySelectorAll('[data-vt]'));
    },
    update: function (root, s, t) {
      var c = conn(t);
      var m = UI.valveStrip(s.structure, t, c, { recipeValves: (s.recipe_limits || {}).recipe_valves || [] });
      var k = 0, tiles = root._tiles || [];
      m.groups.forEach(function (g) {
        g.forEach(function (it) {
          var el = tiles[k++];
          if (!el) return;
          setCls(el, 'vt' + (it.kind === 'value' ? ' val' : '') + (it.auto ? ' auto' : '') + ' ' + (it.state || 'closed'));
          setTitle(el, it.title);
          if (it.kind === 'value') setTxt(el.querySelector('.vval'), it.value || '—');
        });
      });
      var pe = core.bind('vsPend', root);
      if (pe) {
        pe.hidden = !m.pending.length;
        setTxt(pe, T('s.pending') + ' ' + m.pending.length);
        setTitle(pe, 'Pending — 출력이 꺼진 채 1 s 넘게 남은 요청: ' + m.pending.join(' · '));
      }
    }
  };

  /* ===================== Recent Events (넓은 배치) ===================== */
  var events = {
    build: function (root, s, opt) {
      var n = Math.max(1, (opt || {}).rows || 6);
      root.innerHTML = '<div class="panel-head"><span>' + T('panel.events') + '</span>' +
        '<span class="note"><span class="ev-link" role="button" tabindex="0" data-evlink="1">전체 기록은 Alarm/Log 탭</span></span></div>' +
        '<div class="panel-body ev-body">' + Array.apply(null, Array(n)).map(function (_x, i) {
          return '<div class="ev" data-ev="' + i + '"><i class="evd"></i><span class="evt mono"></span><span class="evm"></span></div>';
        }).join('') + '</div>';
      root._n = n;
      root._rows = Array.prototype.slice.call(root.querySelectorAll('[data-ev]'));
    },
    update: function (root) {
      var list = UI.events((core.state || {}).logs, root._n || 0);
      (root._rows || []).forEach(function (row, i) {
        var e = list[i];
        setCls(row, 'ev' + (e ? ' ' + e.lvl : ' empty'));
        var ts = e ? String(e.ts) : '';
        if (ts.indexOf(' ') > 0) ts = ts.split(' ').pop();
        setTxt(row.querySelector('.evt'), ts);
        var mm = row.querySelector('.evm');
        setTxt(mm, e ? e.msg : '');
        setTitle(mm, e ? e.msg : '');
      });
    }
  };

  if (d && d.addEventListener) {
    d.addEventListener('click', function (ev) {
      if (ev.target.closest && ev.target.closest('[data-evlink]')) core.setTab('alarm');
    });
  }

  w.Panels = { start: start, ann: ann, valves: valves, events: events };
})(window, document);
