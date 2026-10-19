/* ============================================================
 * views/datalog.js — 데이터 로그 보기 탭.
 *
 * 목록(시작 시각·레시피·번호·걸린 시간·결과, 날짜·이름 거르기) → 하나를 열면
 *   그래프: 공정 시간축(경과 s). 블록·스텝 구간을 배경 띠로, 마우스를 올리면 스텝 이름.
 *           서버가 최대 2000 묶음(최소·최대·평균)으로 줄여 보낸다.
 *   표   : 200 줄씩.
 *   레시피: 그때 레시피 사본(읽기 전용).
 * ★ 화면에서 지우기는 두지 않는다 — 정리는 보존 기간으로만 한다.
 * ★ 열기 · 표 요청마다 번호를 붙여 가장 최근 요청의 응답만 그린다(늦게 온 옛 응답이 덮지 않게).
 * ★ 한 그래프 안 선끼리 색이 겹치지 않고, 설정값은 그 현재값과 같은 색의 점선(chart.js colorMap).
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
  var openNo = 0, tableNo = 0;

  function render() {
    ['p', 'm', 't', 'x'].forEach(function (k) {
      var cv = d.querySelector('[data-dlchart="' + k + '"]');
      if (cv && !charts[k]) {
        charts[k] = HistChart(cv, {
          onZoom: function (a, b) { zoom = [a, b]; draw(); }
        });
        cv.addEventListener('dblclick', function () { zoom = null; draw(); });
      }
    });
    folderLock();
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
      if (txt && [it.recipe, it.operator, it.sample].join(' ').toLowerCase().indexOf(txt) < 0) return false;
      return true;
    });
    box.innerHTML = list.map(function (it) {
      var res = it.result || '';
      // 서버가 정한 수준(이벤트 로그와 같은 규칙) — 옛 서버면 글로 짐작
      var lvl = it.level || (/정상/.test(res) ? 'ok' : (/중단|알 수 없음|기록 중단/.test(res) ? 'warn' : 'off'));
      return '<div class="dl-item' + (it.name === cur ? ' on' : '') + '" tabindex="0" data-dlname="' + core.esc(it.name) + '">' +
        '<div class="a"><span class="mono">' + core.esc((it.started || '').slice(0, 16)) + '</span>' +
        // ★ v0.4.14: 긴 결과는 칩 안에서 말줄임 + title 에 전체 글(768 폭에서 칸을 넘어 시각을 세 줄로 밀던 것)
        core.chip(res || '진행 중?', lvl, res || '진행 중?') + '</div>' +
        '<div class="b">' + core.esc(it.recipe || '') +
        (it.number != null ? ' <span class="dim">#' + core.esc(it.number) + '</span>' : '') +
        // v0.7.0(A2): 작업자 · 시료
        // v0.7.1(U5): 띄어쓰기 없는 긴 시료 이름이 시간 · 줄 수를 밀어내지 않게 — 줄임표 + title
        ((it.operator || it.sample) ? ' <span class="dim dl-who" title="' +
          core.esc([it.operator, it.sample].filter(Boolean).join(' · ')) + '">· ' +
          core.esc([it.operator, it.sample].filter(Boolean).join(' · ')) + '</span>' : '') +
        '<span class="dim right">' + (it.took_s != null ? fmt.hms(it.took_s) : fmt.DASH) +
        (it.rows != null ? ' · ' + core.esc(it.rows) + ' rows' : '') + (it.guessed ? ' (추정)' : '') + '</span></div></div>';
    }).join('') || '<div class="empty">데이터 로그가 없습니다</div>';
  }

  function open(name) {
    cur = name; zoom = null; offset = 0;
    var my = ++openNo;
    paintList();
    core.setText('dlName', name);
    fetch('api/datalog/chart?name=' + encodeURIComponent(name)).then(function (r) {
      if (!r.ok) throw new Error('없음');
      return r.json();
    }).then(function (js) {
      if (my !== openNo) return;            // ★ 가장 최근에 연 파일의 응답만
      data = js;
      var m = js.meta || {};
      var box = core.bind('dlMeta');
      if (box) {
        box.innerHTML = core.chip(m.result || '—', m.level || (/정상/.test(m.result || '') ? 'ok' : 'warn')) + ' ' +
          '<span class="dim">' + core.esc(m.recipe || '') + (m.number != null ? ' #' + core.esc(m.number) : '') +
          ' · ' + (m.took_s != null ? fmt.hms(m.took_s) : fmt.DASH) + ' · ' + (m.rows != null ? core.esc(m.rows) + ' rows' : '') +
          (m.guessed ? ' · 메타 없음(CSV 로 추정)' : '') + '</span>' +
          ((m.operator || m.sample || m.memo)
            ? '<div class="dim dl-run">' + (m.operator ? 'Operator ' + core.esc(m.operator) : '') +
              (m.sample ? (m.operator ? ' · ' : '') + 'Sample ' + core.esc(m.sample) : '') +
              (m.memo ? '<div class="dl-memo" title="' + core.esc(m.memo) + '">' + core.esc(m.memo) + '</div>' : '') + '</div>'
            : '');
      }
      buildPicker();
      var rb = core.bind('dlRecipeBox');
      if (rb) rb.textContent = js.recipe ? JSON.stringify(js.recipe, null, 2) : '레시피 사본이 없습니다';
      setView(view);
    }).catch(function () { if (my === openNo) core.toast('파일을 열 수 없습니다: ' + name, 'warn'); });
  }

  function buildPicker() {
    var box = core.bind('dlSeries');
    if (!box || !data) return;
    var cm = colors();
    core.html(box, (data.cols || []).map(function (c, i) {
      return '<label class="hs"><input type="checkbox" data-dlcol="' + i + '"' +
        (hidden[c.label] ? '' : ' checked') + '>' + HistChart.swatch(cm[c.label]) + core.esc(c.label) + '</label>';
    }).join(''));
  }

  /** 열 이름 → {color, dashed} — 묶음(그래프)마다 따로, 보이는 현재값끼리 겹치지 않게. */
  function colors() {
    var out = {};
    ['p', 'm', 't', 'x'].forEach(function (g) {
      var ids = ((data || {}).cols || []).filter(function (c) { return c.group === g; })
        .map(function (c) { return c.label; });
      var cm = HistChart.colorMap(ids, function (l) { return !hidden[l]; });
      for (var k in cm) out[k] = cm[k];
    });
    return out;
  }

  function draw() {
    if (!data || view !== 'chart') return;
    var rows = data.rows || [];
    var tMin = rows.length ? rows[0][0] : 0, tMax = rows.length ? rows[rows.length - 1][0] : 1;
    var x0 = zoom ? zoom[0] : tMin, x1 = zoom ? zoom[1] : Math.max(tMax, tMin + 1);
    var per = { p: [], m: [], t: [], x: [] };
    var cm = colors();
    (data.cols || []).forEach(function (c, i) {
      if (!per[c.group]) return;
      var lu = HistChart.splitUnit(c.label);          // 띠 값에도 단위를 붙인다
      per[c.group].push({
        label: lu.label, unit: lu.unit, hidden: !!hidden[c.label],
        color: (cm[c.label] || {}).color, dashed: (cm[c.label] || {}).dashed,
        pts: rows.map(function (r) {
          var v = r[i + 1];
          return v ? [r[0], v[0], v[1], v[2]] : [r[0], null, null, null];
        })
      });
    });
    var bands = (data.segments || []).map(function (s, i) {
      return { x0: s.t0, x1: s.t1, alt: i % 2 === 1,
               label: T('v.block') + ' ' + s.block + ' · ' + T('v.step') + ' ' + s.step + (s.name ? ' — ' + s.name : '') };
    });
    var xp = core.bind('dlXPanel');
    if (xp) xp.hidden = !per.x.length;
    var gap = Math.max(5, 3 * (tMax - tMin) / 2000);
    ['p', 'm', 't', 'x'].forEach(function (k) {
      if (!charts[k]) return;
      charts[k].set({ series: per[k], x0: x0, x1: x1, logY: k === 'p', noNeg: k !== 't',
                      gap: gap, bands: bands, xLabel: xLabel, dead: false });
    });
  }

  function xLabel(s, span, full) {
    return (full ? T('v.elapsed') + ' ' : '') + fmt.hms(s);
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
    var my = ++tableNo;
    fetch('api/datalog/rows?name=' + encodeURIComponent(cur) + '&offset=' + offset)
      .then(function (r) { return r.json(); }).then(function (js) {
        if (my !== tableNo) return;
        var t = core.bind('dlTbl');
        if (!t) return;
        t.innerHTML = '<thead><tr>' + (js.head || []).map(function (h) {
          return '<th>' + core.esc(h) + '</th>';
        }).join('') + '</tr></thead><tbody>' + (js.rows || []).map(function (r) {
          return '<tr>' + r.map(function (c) { return '<td>' + core.esc(T.oldValue(c)) + '</td>'; }).join('') + '</tr>';   // v0.5.1(D6)
        }).join('') + '</tbody>';
        core.setText('dlPage', (js.total ? (js.offset + 1) + '~' + Math.min(js.total, js.offset + 200) : 0) +
          ' / ' + (js.total || 0) + ' rows');
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
    if (a.dataset.dlact === 'folder' && !a.disabled && core.canOperate()) w.app.send('open_folder', { which: 'datalog' });
  });

  d.addEventListener('input', function (ev) {
    if (ev.target.closest('[data-bind="dlDate"],[data-bind="dlText"]')) paintList();
  });

  d.addEventListener('change', function (ev) {
    var c = ev.target.closest('[data-dlcol]');
    if (!c || !data) return;
    var col = data.cols[parseInt(c.dataset.dlcol, 10)];
    if (col) hidden[col.label] = !c.checked;
    buildPicker();
    draw();
  });

  /** '폴더 열기' — live 를 받을 때마다 정한다(끊김 · 멈춤 뒤에도 켜져 있지 않게), 툴팁에 이유 */
  function folderLock() {
    var fb = d.querySelector('[data-dlact="folder"]');
    if (!fb) return;
    var why = core.lockReason();
    fb.disabled = !!why;
    var tip = why ? why + ' — 쓸 수 없습니다' : '';
    if (fb.title !== tip) fb.title = tip;
  }

  core.register('datalog', { render: render, update: function () { folderLock(); } });
  w.viewDatalog = { refresh: refresh, open: open, charts: charts };
})(window, document);
