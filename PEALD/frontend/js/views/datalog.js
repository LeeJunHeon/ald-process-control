/* ============================================================
 * views/datalog.js — 데이터 로그 보기 탭.
 *
 * 목록(시작 시각·레시피·번호·걸린 시간·결과, 날짜·이름 거르기) → 하나를 열면
 *   그래프: 공정 시간축(경과 s). 블록·스텝 구간을 배경 띠로, 마우스를 올리면 스텝 이름.
 *           서버가 최대 2000 묶음(최소·최대·평균)으로 줄여 보낸다.
 *   표   : 200 줄씩.
 *   레시피: 그때 레시피 사본(읽기 전용).
 * ★ 화면에서 지우기는 두지 않는다 — 정리는 보존 기간으로만 한다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var items = [];
  var cur = null;           // 연 파일 이름
  var data = null;          // chart 응답
  var view = 'chart';
  var offset = 0;
  var charts = {};
  var hidden = {};
  var zoom = null;          // [x0, x1] 초

  function cssVar(n) { return getComputedStyle(d.documentElement).getPropertyValue(n).trim(); }

  function render() {
    ['p', 'm', 't', 'x'].forEach(function (k) {
      var cv = d.querySelector('[data-dlchart="' + k + '"]');
      if (cv && !charts[k]) {
        charts[k] = HistChart(cv, core.bind('dlTip'), {
          onZoom: function (a, b) { zoom = [a, b]; draw(); }
        });
        cv.addEventListener('dblclick', function () { zoom = null; draw(); });
      }
    });
    if (core.tab === 'datalog') {
      if (!items.length) refresh();
      draw();
    }
  }

  function refresh() {
    fetch('api/datalog/list').then(function (r) { return r.json(); }).then(function (js) {
      items = js.items || [];
      paintList();
    }).catch(function () { core.toast('데이터 로그 목록을 읽지 못했습니다', 'warn'); });
  }

  function paintList() {
    var box = core.bind('dlList');
    if (!box) return;
    var day = (core.bind('dlDate') || {}).value || '';
    var txt = ((core.bind('dlText') || {}).value || '').trim().toLowerCase();
    var list = items.filter(function (it) {
      if (day && (it.started || '').slice(0, 10) !== day) return false;
      if (txt && String(it.recipe || '').toLowerCase().indexOf(txt) < 0) return false;
      return true;
    });
    box.innerHTML = list.map(function (it) {
      var res = it.result || '';
      var lvl = /정상/.test(res) ? 'ok' : (/중단|알 수 없음|기록 중단/.test(res) ? 'warn' : 'off');
      return '<div class="dl-item' + (it.name === cur ? ' on' : '') + '" data-dlname="' + core.esc(it.name) + '">' +
        '<div class="a"><span class="mono">' + core.esc((it.started || '').slice(0, 16)) + '</span>' +
        core.chip(res || '진행 중?', lvl) + '</div>' +
        '<div class="b">' + core.esc(it.recipe || '') +
        (it.number != null ? ' <span class="dim">#' + it.number + '</span>' : '') +
        '<span class="dim right">' + (it.took_s != null ? fmt.hms(it.took_s) : fmt.DASH) +
        (it.rows != null ? ' · ' + it.rows + '줄' : '') + (it.guessed ? ' (추정)' : '') + '</span></div></div>';
    }).join('') || '<div class="empty">데이터 로그가 없습니다</div>';
  }

  function open(name) {
    cur = name; zoom = null; offset = 0;
    paintList();
    core.setText('dlName', name);
    fetch('api/datalog/chart?name=' + encodeURIComponent(name)).then(function (r) {
      if (!r.ok) throw new Error('없음');
      return r.json();
    }).then(function (js) {
      data = js;
      var m = js.meta || {};
      var box = core.bind('dlMeta');
      if (box) {
        box.innerHTML = core.chip(m.result || '—', /정상/.test(m.result || '') ? 'ok' : 'warn') + ' ' +
          '<span class="dim">' + core.esc(m.recipe || '') + (m.number != null ? ' #' + m.number : '') +
          ' · ' + (m.took_s != null ? fmt.hms(m.took_s) : fmt.DASH) + ' · ' + (m.rows != null ? m.rows + '줄' : '') +
          (m.guessed ? ' · 메타 없음(CSV 로 추정)' : '') + '</span>';
      }
      buildPicker();
      var rb = core.bind('dlRecipeBox');
      if (rb) rb.textContent = js.recipe ? JSON.stringify(js.recipe, null, 2) : '레시피 사본이 없습니다';
      setView(view);
    }).catch(function () { core.toast('파일을 열 수 없습니다: ' + name, 'warn'); });
  }

  function buildPicker() {
    var box = core.bind('dlSeries');
    if (!box || !data) return;
    box.innerHTML = (data.cols || []).map(function (c, i) {
      return '<label class="hs"><input type="checkbox" data-dlcol="' + i + '"' +
        (hidden[c.label] ? '' : ' checked') + '>' + core.esc(c.label) + '</label>';
    }).join('');
  }

  function draw() {
    if (!data || view !== 'chart') return;
    var rows = data.rows || [];
    var tMin = rows.length ? rows[0][0] : 0, tMax = rows.length ? rows[rows.length - 1][0] : 1;
    var x0 = zoom ? zoom[0] : tMin, x1 = zoom ? zoom[1] : Math.max(tMax, tMin + 1);
    var per = { p: [], m: [], t: [], x: [] };
    (data.cols || []).forEach(function (c, i) {
      if (!per[c.group]) return;
      per[c.group].push({
        label: c.label, unit: '', hidden: !!hidden[c.label],
        color: cssVar('--series-' + ((per[c.group].length % 6) + 1)),
        pts: rows.map(function (r) {
          var v = r[i + 1];
          return v ? [r[0], v[0], v[1], v[2]] : [r[0], null, null, null];
        })
      });
    });
    var bands = (data.segments || []).map(function (s, i) {
      return { x0: s.t0, x1: s.t1, alt: i % 2 === 1,
               label: '블록 ' + s.block + ' · 스텝 ' + s.step + (s.name ? ' — ' + s.name : '') };
    });
    var xp = core.bind('dlXPanel');
    if (xp) xp.hidden = !per.x.length;
    var gap = Math.max(5, 3 * (tMax - tMin) / 2000);
    ['p', 'm', 't', 'x'].forEach(function (k) {
      if (!charts[k]) return;
      charts[k].set({ series: per[k], x0: x0, x1: x1, logY: k === 'p', noNeg: k !== 't',
                      gap: gap, bands: bands, xLabel: xLabel });
    });
  }

  function xLabel(s, span, full) {
    return (full ? '경과 ' : '') + fmt.hms(s);
  }

  function setView(v) {
    view = v;
    Array.prototype.forEach.call(d.querySelectorAll('[data-dlview]'), function (b) {
      b.classList.toggle('primary', b.dataset.dlview === v);
    });
    var map = { chart: 'dlChart', table: 'dlTable', recipe: 'dlRecipe' };
    for (var k in map) { var e = core.bind(map[k]); if (e) e.hidden = k !== v; }
    if (v === 'chart') draw();
    if (v === 'table') loadTable();
  }

  function loadTable() {
    if (!cur) return;
    fetch('api/datalog/rows?name=' + encodeURIComponent(cur) + '&offset=' + offset)
      .then(function (r) { return r.json(); }).then(function (js) {
        var t = core.bind('dlTbl');
        if (!t) return;
        t.innerHTML = '<thead><tr>' + (js.head || []).map(function (h) {
          return '<th>' + core.esc(h) + '</th>';
        }).join('') + '</tr></thead><tbody>' + (js.rows || []).map(function (r) {
          return '<tr>' + r.map(function (c) { return '<td>' + core.esc(c) + '</td>'; }).join('') + '</tr>';
        }).join('') + '</tbody>';
        core.setText('dlPage', (js.total ? (js.offset + 1) + '~' + Math.min(js.total, js.offset + 200) : 0) +
          ' / ' + (js.total || 0) + '줄');
        dataTotal = js.total || 0;
      });
  }
  var dataTotal = 0;

  d.addEventListener('click', function (ev) {
    var it = ev.target.closest('[data-dlname]');
    if (it) { open(it.dataset.dlname); return; }
    var v = ev.target.closest('[data-dlview]');
    if (v) { setView(v.dataset.dlview); return; }
    var pg = ev.target.closest('[data-dlpage]');
    if (pg) {
      var n = offset + parseInt(pg.dataset.dlpage, 10) * 200;
      if (n < 0 || (dataTotal && n >= dataTotal)) return;
      offset = n; loadTable(); return;
    }
    var a = ev.target.closest('[data-dlact]');
    if (!a) return;
    if (a.dataset.dlact === 'refresh') refresh();
    if (a.dataset.dlact === 'folder') w.app.send('open_folder', { which: 'datalog' });
  });

  d.addEventListener('input', function (ev) {
    if (ev.target.closest('[data-bind="dlDate"],[data-bind="dlText"]')) paintList();
  });

  d.addEventListener('change', function (ev) {
    var c = ev.target.closest('[data-dlcol]');
    if (!c || !data) return;
    var col = data.cols[parseInt(c.dataset.dlcol, 10)];
    if (col) hidden[col.label] = !c.checked;
    draw();
  });

  core.register('datalog', { render: render, update: function () {} });
  w.viewDatalog = { refresh: refresh, open: open };
})(window, document);
