/* ============================================================
 * views/recipe.js — 레시피 탭.
 *
 * 편집은 화면에서 하지만 계산·검증은 전부 서버가 한다(recipe_preview).
 * ★ 화면에서 밸브 조합이나 시간을 다시 계산하면, 서버와 결과가 갈리는 순간
 *   "화면에는 통과인데 시작하면 거절"이 된다. 계산은 한 곳(recipe_model.py)에만 둔다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var cur = null;        // 편집 중인 레시피(사본)
  var curName = '';      // 불러온 원본 이름
  var origJson = '';     // 되돌리기용
  var pick = { block: 0, step: 0 };
  var filter = '';
  var listSig = '';

  /* ===================== render ===================== */
  function render(s) {
    renderList(s);
    if (!cur && (s.recipes || []).length) load(s.recipes[0]);
    renderAll(s);
  }

  function update() { /* 레시피 탭은 실시간 값이 없다 — 실행 중 안내 띠만 갱신한다 */
    var s = core.state;
    if (!s) return;
    var strip = core.bind('recipeRunning');
    if (!strip) return;
    var p = (s.live || {}).process || {};
    var on = core.isRunning() && p.recipe && p.recipe === curName;
    strip.hidden = !on;
    if (on) strip.textContent = '● 현재 ' + (s.chamber || {}).name +
      '에서 실행 중인 레시피입니다. 저장한 내용은 다음 실행부터 적용됩니다.';
  }

  /* ---------- 목록 ---------- */
  function renderList(s) {
    var box = core.bind('recipeList');
    if (!box) return;
    var names = (s.recipes || []).filter(function (n) {
      return !filter || n.toLowerCase().indexOf(filter) >= 0;
    });
    var sig = names.join('|') + '#' + curName;
    if (sig === listSig) return;
    listSig = sig;
    core.setText('recipeCount', (s.recipes || []).length + '개');
    box.innerHTML = names.length ? '' : '<div class="empty">레시피가 없습니다</div>';
    names.forEach(function (n) {
      var it = core.h('div', { cls: 'ritem' + (n === curName ? ' on' : ''), 'data-recipe': n });
      it.appendChild(core.h('div', { cls: 'n', text: n }));
      it.appendChild(core.h('div', { cls: 'm', 'data-meta': n, text: '' }));
      box.appendChild(it);
    });
  }

  /* ---------- 전체 다시 그리기 ---------- */
  function renderAll(s) {
    renderInfo(s);
    renderBlocks(s);
    renderSteps(s);
    renderDetail(s);
    update();
    requestPreview();
  }

  function renderInfo(s) {
    var box = core.bind('recipeInfo');
    if (!box) return;
    if (!cur) { box.innerHTML = '<div class="empty">레시피를 선택하세요</div>'; return; }
    var c = cur.conditions || {};
    box.innerHTML =
      '<div class="grid2" style="margin-bottom:12px">' +
      f('레시피 이름', 'name', cur.name, 'text') +
      f('메모', 'memo', cur.memo || '', 'text') + '</div>' +
      '<div class="grid3">' +
      f('스테이지 SV', 'stage_sv', c.stage_sv, 'number', '°C') +
      f('안정화 대기', 'soak_min', c.soak_min, 'number', 'min') +
      f('시작 베이스 압력', 'base_pressure_torr', fmt.torr(c.base_pressure_torr), 'text', 'Torr') +
      f('공정 스로틀', 'throttle_pct', c.throttle_pct, 'number', '%') +
      f('종료 후', 'end_action', c.end_action, 'text') + '</div>';
  }

  function f(label, key, val, type, suffix) {
    return '<div class="field"><label>' + core.esc(label) + '</label><div class="inwrap">' +
      '<input class="inp" type="' + type + '" data-rf="' + key + '" value="' + core.esc(val) + '">' +
      (suffix ? '<span class="suffix">' + core.esc(suffix) + '</span>' : '') + '</div></div>';
  }

  function renderBlocks(s) {
    var tbl = core.bind('blockTbl');
    if (!tbl) return;
    if (!cur) { tbl.innerHTML = ''; return; }
    var html = '<thead><tr><th>#</th><th>블록</th><th>시퀀스</th><th>반복</th><th>소요 시간</th></tr></thead><tbody>';
    (cur.blocks || []).forEach(function (b, i) {
      html += '<tr class="click' + (i === pick.block ? ' pick' : '') + '" data-block="' + i + '">' +
        '<td>' + (i + 1) + '</td><td><b>' + core.esc(b.name) + '</b></td>' +
        '<td class="l">' + core.esc((b.steps || []).map(function (x) { return x.name; }).join(' → ')) + '</td>' +
        '<td><input class="inp mono" data-brep="' + i + '" value="' + core.esc(b.repeat) +
        '" style="width:56px;text-align:right;padding:2px 5px"></td>' +
        '<td class="mono" data-btime="' + i + '">' + fmt.DASH + '</td></tr>';
    });
    tbl.innerHTML = html + '</tbody>';
    var g = cur.groups || [];
    core.setText('groupNote', g.length
      ? g.map(function (x) { return '블록 ' + (x.from_block + 1) + '–' + (x.to_block + 1) + ' ×' + x.repeat; }).join(' · ')
      : '반복 그룹 없음 — 여러 블록을 묶어 반복하려면 [반복 그룹]을 누르세요 (라미네이트용)');
  }

  function renderSteps(s) {
    var b = block();
    core.setText('stepHead', '');
    var head = core.bind('stepHead');
    if (head) head.innerHTML = (b ? '블록 ' + (pick.block + 1) + ' · ' + core.esc(b.name) : '블록 —') +
      '<span class="note" data-bind="cycleTime">' + fmt.DASH + '</span>' +
      '<span class="note"><button class="btn sm" data-cmd="step_add">+ 스텝</button>' +
      '<button class="btn sm danger-line" data-cmd="step_del">삭제</button></span>';
    var tbl = core.bind('stepTbl');
    if (!tbl) return;
    if (!b) { tbl.innerHTML = ''; return; }
    var lines = (core.state || {}).lines || [];
    var html = '<thead><tr><th>#</th><th>스텝</th><th>시간 s</th><th>전구체 (P)</th><th>반응물 (R)</th>' +
      '<th>베이스 N2 sccm</th></tr></thead><tbody>';
    (b.steps || []).forEach(function (st, i) {
      var p = [], r = [];
      (st.lines || []).forEach(function (sel) {
        var ln = lines.filter(function (l) { return l.id === sel.line; })[0];
        if (!ln) return;
        var lbl = sel.line + ' · ' + modeLabel(sel.mode);
        (ln.side === 'precursor' ? p : r).push(core.chip(lbl, ln.side === 'precursor' ? 'warn' : 'ok'));
      });
      var n2 = Object.keys(b.mfc || {}).filter(function (k) {
        var ln = lines.filter(function (l) { return l.id === k; })[0];
        return ln && ln.kind === 'n2';
      }).map(function (k) { return k + ' ' + fmt.num(b.mfc[k], 0); }).join(' · ');
      html += '<tr class="click' + (i === pick.step ? ' pick' : '') + '" data-step="' + i + '">' +
        '<td>' + (i + 1) + '</td><td><b>' + core.esc(st.name) + '</b></td>' +
        '<td class="mono">' + fmt.sec(st.time_s) + '</td>' +
        '<td class="l">' + (p.join(' ') || '<span class="dim">' + fmt.DASH + '</span>') + '</td>' +
        '<td class="l">' + (r.join(' ') || '<span class="dim">' + fmt.DASH + '</span>') + '</td>' +
        '<td class="mono dim">' + core.esc(n2 || fmt.DASH) + '</td></tr>';
    });
    tbl.innerHTML = html + '</tbody>';
  }

  function renderDetail(s) {
    var box = core.bind('stepDetail');
    if (!box) return;
    var st = step(), b = block();
    var head = core.bind('detailHead');
    if (head) head.textContent = st ? ('스텝 ' + (pick.step + 1) + ' · ' + st.name + ' — 상세') : '스텝 상세';
    if (!st) { box.innerHTML = '<div class="empty">스텝을 선택하세요</div>'; return; }
    var lines = ((core.state || {}).lines || []).filter(function (l) { return l.kind !== 'n2'; });
    var n2 = ((core.state || {}).lines || []).filter(function (l) { return l.kind === 'n2'; });

    var left = '<div class="grid2" style="margin-bottom:12px">' +
      '<div class="field"><label>스텝 이름</label><input class="inp" data-sf="name" value="' + core.esc(st.name) + '"></div>' +
      '<div class="field"><label>시간</label><div class="inwrap">' +
      '<input class="inp" type="number" step="0.01" data-sf="time_s" value="' + core.esc(st.time_s) + '">' +
      '<span class="suffix">s</span></div></div></div>' +
      '<div class="grid3">' + n2.concat(lines.filter(function (l) {
        return sel(st, l.id) && sel(st, l.id).mode === 'carrier';
      })).map(function (l) {
        return '<div class="field"><label>' + core.esc(l.id + (l.kind === 'n2' ? '' : ' 캐리어')) +
          '</label><div class="inwrap"><input class="inp mono" type="number" data-mfcf="' + core.esc(l.id) +
          '" value="' + core.esc((b.mfc || {})[l.id] != null ? b.mfc[l.id] : 0) +
          '"><span class="suffix">sccm</span></div></div>';
      }).join('') + '</div>' +
      '<div class="hint" style="margin-top:8px">최소 스텝 시간 0.02 s (PLC 타이머 기준) · MFC 는 블록 단위 설정입니다</div>';

    var right = '<div class="hint" style="margin-bottom:6px">이 스텝에서 여는 라인</div>' +
      '<div class="linecheck">' + lines.map(function (l) {
        var se = sel(st, l.id);
        return '<label class="' + (l.enabled ? '' : 'off') + '">' +
          '<input type="checkbox" data-lchk="' + core.esc(l.id) + '"' + (se ? ' checked' : '') +
          (l.enabled ? '' : ' disabled') + '>' +
          '<span class="lid">' + core.esc(l.id) + '</span>' +
          '<span class="mat">' + core.esc(l.enabled ? (l.material || l.label) : '미장착') + '</span>' +
          (se ? '<select class="inp" data-lmode="' + core.esc(l.id) + '" style="width:92px;padding:2px 5px">' +
            (l.modes || []).map(function (m) {
              return '<option value="' + core.esc(m) + '"' + (m === se.mode ? ' selected' : '') + '>' +
                core.esc(modeLabel(m)) + '</option>';
            }).join('') + '</select>' : '') + '</label>';
      }).join('') + '</div>' +
      '<div class="hint" style="margin:12px 0 6px">열리는 밸브 (자동 계산)</div>' +
      '<div class="tagchips" data-bind="openTags"><span class="dim">' + fmt.DASH + '</span></div>';

    box.innerHTML = '<div class="row"><div class="grow">' + left + '</div><div class="grow">' + right + '</div></div>';
  }

  function modeLabel(m) {
    var md = ((core.state || {}).modes || {})[m];
    return md && md.label ? md.label : m;
  }

  /* ===================== 서버 왕복 ===================== */
  function requestPreview() {
    if (cur) w.app.send('recipe_preview', { recipe: cur });
  }

  w.app.on('recipe', function (msg) {
    curName = msg.name;
    cur = msg.recipe;
    origJson = JSON.stringify(cur);
    pick = { block: Math.min(1, (cur.blocks || []).length - 1), step: 0 };
    if (pick.block < 0) pick.block = 0;
    listSig = '';
    renderList(core.state || {});
    renderAll(core.state);
    applyPreview(msg.preview);
  });

  w.app.on('preview', function (msg) { applyPreview(msg.preview); });

  function applyPreview(pv) {
    if (!pv) return;
    var strip = core.bind('verify');
    var sum = pv.summary || {};
    (sum.blocks || []).forEach(function (b, i) {
      var e = d.querySelector('[data-btime="' + i + '"]');
      if (e) e.textContent = fmt.dur(b.total_s);
    });
    var ct = core.bind('cycleTime');
    var bs = (sum.blocks || [])[pick.block];
    if (ct && bs) ct.textContent = '1 사이클 ' + fmt.sec(bs.cycle_s, 1) + ' s · ' + bs.repeat + ' 회';
    var tags = (pv.opens || []).filter(function (o) { return o.block === pick.block && o.step === pick.step; })[0];
    var tb = core.bind('openTags');
    if (tb) tb.innerHTML = tags && tags.tags.length
      ? tags.tags.map(function (t) { return core.chip(t, 'ok'); }).join('')
      : '<span class="dim">' + fmt.DASH + '</span>';
    if (strip) {
      var errs = (pv.errors || []).filter(function (e) { return e.level === 'err'; });
      strip.classList.toggle('bad', errs.length > 0);
      strip.innerHTML = errs.length
        ? '✕ 검증 실패 <span class="detail">' + core.esc(errs.map(function (e) {
            return (e.where ? e.where + ': ' : '') + e.msg; }).slice(0, 3).join(' / ')) + '</span>'
        : '✓ 검증 통과 <span class="detail">' + core.esc(
            '전구체·반응물 동시 개방 없음 · 모든 스텝 ≥ 0.02 s · 총 ' + fmt.dur(sum.total_s) +
            ' (+ 안정화 ' + (sum.soak_min || 0) + ' min)') + '</span>';
    }
    // 목록의 부제(블록 수·사이클·시간)
    var meta = d.querySelector('[data-meta="' + curName + '"]');
    if (meta) meta.textContent = '블록 ' + (sum.blocks || []).length +
      ' · ' + (sum.max_cycles || 0) + ' 사이클 · ' + fmt.dur(sum.total_s);
  }

  function load(name) { w.app.send('recipe_load', { name: name }); }

  /* ===================== 편집 이벤트 ===================== */
  function block() { return cur && (cur.blocks || [])[pick.block]; }
  function step() { var b = block(); return b && (b.steps || [])[pick.step]; }
  function sel(st, id) {
    return ((st || {}).lines || []).filter(function (x) { return x.line === id; })[0];
  }

  d.addEventListener('click', function (ev) {
    if (core.tab !== 'recipe') return;
    var it = ev.target.closest('[data-recipe]');
    if (it) { load(it.getAttribute('data-recipe')); return; }
    var br = ev.target.closest('[data-block]');
    if (br && !ev.target.closest('input')) { pick = { block: +br.dataset.block, step: 0 }; renderAll(core.state); return; }
    var sr = ev.target.closest('[data-step]');
    if (sr && !ev.target.closest('input')) { pick.step = +sr.dataset.step; renderSteps(core.state); renderDetail(core.state); requestPreview(); return; }

    var b = ev.target.closest('[data-cmd]');
    if (!b || b.disabled) return;
    switch (b.dataset.cmd) {
      case 'recipe_save':
        if (!cur) return;
        w.app.send('recipe_save', { name: cur.name, recipe: cur });
        break;
      case 'recipe_saveas':
        if (!cur) return;
        var nn = prompt('새 이름으로 저장', cur.name + '_copy');
        if (nn) { cur.name = nn; w.app.send('recipe_save', { name: nn, recipe: cur }); }
        break;
      case 'recipe_delete':
        if (curName && confirm('레시피를 삭제할까요?\n' + curName)) w.app.send('recipe_delete', { name: curName });
        break;
      case 'recipe_revert':
        if (origJson) { cur = JSON.parse(origJson); renderAll(core.state); }
        break;
      case 'recipe_new':
        cur = { name: '새 레시피', memo: '', conditions: (cur && cur.conditions) || {}, blocks: [], groups: [] };
        curName = ''; origJson = JSON.stringify(cur); pick = { block: 0, step: 0 };
        renderAll(core.state);
        break;
      case 'block_add':
        if (!cur) return;
        cur.blocks.push({ name: '새 블록', repeat: 1, mfc: {}, steps: [{ name: '스텝 1', time_s: 1, lines: [] }] });
        pick = { block: cur.blocks.length - 1, step: 0 };
        renderAll(core.state);
        break;
      case 'group_add':
        if (!cur || !cur.blocks.length) return;
        var rep = parseInt(prompt('블록 ' + (pick.block + 1) + '부터 몇 번 반복할까요?', '10'), 10);
        if (rep > 0) { (cur.groups = cur.groups || []).push({ from_block: pick.block, to_block: pick.block, repeat: rep }); renderAll(core.state); }
        break;
      case 'step_add':
        if (!block()) return;
        block().steps.push({ name: '새 스텝', time_s: 1, lines: [] });
        pick.step = block().steps.length - 1;
        renderAll(core.state);
        break;
      case 'step_del':
        if (!block() || block().steps.length <= 1) return;
        block().steps.splice(pick.step, 1);
        pick.step = Math.max(0, pick.step - 1);
        renderAll(core.state);
        break;
    }
  });

  d.addEventListener('input', function (ev) {
    var t = ev.target;
    if (t.matches('[data-bind="recipeSearch"]')) {
      filter = t.value.trim().toLowerCase(); listSig = ''; renderList(core.state || {}); return;
    }
    if (!cur) return;
    if (t.hasAttribute('data-rf')) {
      var k = t.getAttribute('data-rf');
      if (k === 'name') cur.name = t.value;
      else if (k === 'memo') cur.memo = t.value;
      // 압력은 지수표기(5.0E-2)로 보여 주고 받는다 — 숫자로 되돌려 저장해야
      // 서버의 계산·비교가 문자열 비교로 어긋나지 않는다.
      else if (k === 'base_pressure_torr') cur.conditions[k] = numOr(t.value, cur.conditions[k]);
      else cur.conditions[k] = t.type === 'number' ? numOr(t.value, cur.conditions[k]) : t.value;
      requestPreview();
    } else if (t.hasAttribute('data-brep')) {
      cur.blocks[+t.getAttribute('data-brep')].repeat = Math.max(1, parseInt(t.value, 10) || 1);
      requestPreview();
    } else if (t.hasAttribute('data-sf')) {
      var st = step();
      if (!st) return;
      var key = t.getAttribute('data-sf');
      st[key] = key === 'time_s' ? numOr(t.value, st.time_s) : t.value;
      renderSteps(core.state); requestPreview();
    } else if (t.hasAttribute('data-mfcf')) {
      var b = block();
      if (b) { (b.mfc = b.mfc || {})[t.getAttribute('data-mfcf')] = numOr(t.value, 0); renderSteps(core.state); requestPreview(); }
    }
  });

  d.addEventListener('change', function (ev) {
    var t = ev.target;
    if (!cur) return;
    if (t.hasAttribute('data-lchk')) {
      var st = step(), id = t.getAttribute('data-lchk');
      if (!st) return;
      st.lines = st.lines || [];
      if (t.checked) {
        var ln = ((core.state || {}).lines || []).filter(function (l) { return l.id === id; })[0];
        st.lines.push({ line: id, mode: (ln && (ln.modes || [])[0]) || 'vapor' });
      } else {
        st.lines = st.lines.filter(function (x) { return x.line !== id; });
      }
      renderSteps(core.state); renderDetail(core.state); requestPreview();
    } else if (t.hasAttribute('data-lmode')) {
      var se = sel(step(), t.getAttribute('data-lmode'));
      if (se) { se.mode = t.value; renderSteps(core.state); renderDetail(core.state); requestPreview(); }
    }
  });

  function numOr(v, dflt) { var n = parseFloat(v); return isFinite(n) ? n : dflt; }

  core.register('recipe', { render: render, update: update });
  w.viewRecipe = { render: render, update: update };
})(window, document);
