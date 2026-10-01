/* ============================================================
 * views/recipe.js — 레시피 탭 (목록 + 편집기).
 *
 * ★ 계산·검증은 전부 서버에서 한다. 화면은 편집한 내용을 보내고, 돌아온 오류·경고·
 *   예상 시간을 그리기만 한다. 화면이 따로 계산하면 두 값이 달라지고, 운전자는
 *   어느 쪽을 믿어야 할지 알 수 없다.
 *
 * ★ 편집 중인 것과 PLC 에 올라가 있는 것은 다른 것이다. 둘을 나란히 보여 준다 —
 *   "화면에서 고쳤는데 장비는 옛 레시피로 돈다"가 가장 흔한 사고다.
 * ★ 입력 칸은 레시피를 새로 열 때 · 운전자가 칸을 더하고 뺄 때만 다시 만든다. 상태를 다시
 *   받을 때마다(명령 뒤 · 저장 직후) 다시 그리면 치던 값이 지워지고 포커스가 빠진다.
 * ★ '저장됨'은 서버의 저장 결과(recipe_saved)를 받은 뒤에만 — 거절되면 dirty 를 그대로 둔다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var cur = null;         // 편집 중인 레시피 (서버에서 받은 것 + 화면 수정)
  var curName = '';       // 저장된 이름 ('' = 아직 저장 안 함)
  var dirty = false;
  var check = { errors: [], warnings: [] };
  var summary = {};
  var lim = {};
  var sel = { block: 1 };
  var sendTimer = null;
  var drawn = false;        // 지금 cur 로 편집기 칸을 만들었는가
  var limSig = '';
  var editNo = 0;           // 편집할 때마다 +1 — 저장 요청 뒤에 또 고쳤는지 본다
  var saving = null;        // { name, editNo } — 답을 기다리는 저장
  var lastPlcConn = null;

  /* ===================== 서버에서 오는 것 ===================== */
  w.app.on('recipe', function (m) {
    cur = m.recipe;
    curName = m.name || '';
    dirty = false;
    saving = null;                       // 기다리던 저장은 버린다 — '저장하는 중…' 도 지운다
    core.setText('rcSaveMsg', '');
    check = m.check || { errors: [], warnings: [] };
    summary = m.summary || {};
    sel.block = 1;
    draw();
  });

  /** 저장 결과. 성공이면 그 이름으로 · 저장 요청 뒤에 더 고치지 않았으면 dirty 를 내린다. */
  w.app.on('recipe_saved', function (m) {
    var sv = saving;
    saving = null;
    if (!sv) {
      // ★ 기다리던 저장이 없다(그사이 [새로] · 다른 레시피 열기) — 편집 중인 레시피 · 이름 · dirty 는
      //   건드리지 않고 목록만 갱신한다(남아 있을 수 있는 '저장하는 중…' 은 지운다)
      if ((core.bind('rcSaveMsg') || {}).textContent === '저장하는 중…') core.setText('rcSaveMsg', '');
      drawList(core.state || {});
      return;
    }
    if (!m.ok) {
      // 거절 — 편집은 그대로(dirty 유지), 이유는 서버 알림과 함께 여기에도 남긴다
      dirty = true;
      core.setText('rcSaveMsg', '저장 안 됨 — ' + (m.why || ''));
      drawStatus();
      return;
    }
    core.setText('rcSaveMsg', '');
    curName = m.name;
    if (cur) cur.name = m.name;
    dirty = !!(sv && sv.editNo !== editNo);
    var nm = d.querySelector('[data-rcf="name"]');
    if (nm && d.activeElement !== nm) nm.value = m.name;
    drawStatus();
    drawList(core.state || {});
  });

  w.app.on('recipe_check', function (m) {
    check = m.check || { errors: [], warnings: [] };
    summary = m.summary || {};
    drawStatus();
    markCells();
  });

  /** 편집한 내용을 서버로 보내 검증받는다. 타이핑마다 보내지 않고 조금 모은다. */
  function validateSoon() {
    dirty = true;
    editNo++;
    if (sendTimer) clearTimeout(sendTimer);
    sendTimer = setTimeout(function () {
      sendTimer = null;
      if (cur) w.app.send('recipe_validate', { recipe: cur });
    }, 250);
  }

  /* ===================== render ===================== */
  function render(s) {
    lim = s.recipe_limits || {};
    drawList(s);
    drawPlcRow(s);
    // ★ 편집기 칸은 새로 열 때만 만든다(한계가 바뀐 때 제외) — 치던 값 · 포커스를 지킨다
    var sig = JSON.stringify(lim) + '|' + JSON.stringify((s.device || {}).key || '');
    if (!cur) drawEmpty();
    else if (!drawn || sig !== limSig) draw();
    else drawStatus();
    limSig = sig;
  }

  function update(t) {
    var p = t.process || {};
    // 실행 중에는 저장·삭제·올리기를 막는다(PLC 는 작업본으로 돌지만 화면이 헷갈린다).
    var run = !!p.running;
    // 시작 흐름(올리기·베이스 압력 대기·명령 1) 동안에도 올리기·시작·선택 레시피 덮어쓰기를 막는다
    var flow = !!(p.phase && p.phase !== 'idle');
    ['rcSave', 'rcSaveAs', 'rcDelete', 'rcRename', 'rcUpload', 'rcStart'].forEach(function (k) {
      var b = core.bind(k);
      if (!b) return;
      var block = !core.canOperate() || ((run || flow) && k !== 'rcSaveAs');
      if (k === 'rcUpload' || k === 'rcStart') block = block || !core.plcOk();
      b.disabled = block;
    });
    core.setText('rcRunNote', !core.canOperate() ? '보기 전용 — 저장 · 올리기는 이 PC 에서만'
      : run ? '공정 중입니다 — 저장·올리기는 공정이 끝난 뒤에 하세요'
        : (flow ? '시작 절차 진행 중 — 끝나거나 대기 취소 뒤에 바꿀 수 있습니다' : ''));
    var conn = core.plcOk() + '|' + core.offline;           // PLC 끊김 ↔ 서버 끊김도 다시 그린다
    if (conn !== lastPlcConn) { lastPlcConn = conn; drawPlcRow(core.state || {}); }
    var nb = d.querySelector('[data-rcbtn="new"]');
    if (nb) nb.disabled = false;
  }

  /* ---------- 목록 ---------- */
  function drawList(s) {
    var box = core.bind('rcList');
    if (!box) return;
    var list = s.recipes || [];
    core.html(box, list.length ? list.map(function (r) {
      return '<div class="rc-item' + (r.name === curName ? ' on' : '') +
        '" tabindex="0" role="button" data-rcopen="' + core.esc(r.name) + '">' +
        '<div class="n">' + core.esc(r.name) + '</div>' +
        '<div class="m">' + core.esc(r.memo || '') + '</div>' +
        '<div class="s mono">블록 ' + core.esc(r.block_count) + ' · 번호 ' + core.esc(r.number) +
        (r.modified ? ' · ' + core.esc(r.modified) : '') + '</div></div>';
    }).join('') : '<div class="empty">저장된 레시피가 없습니다 — [새로]를 누르세요</div>');
  }

  /** 지금 PLC 에 올라가 있는 레시피 한 줄. */
  function drawPlcRow(s) {
    var e = core.bind('rcPlc');
    if (!e) return;
    var p = s.plc_recipe || {};
    // ★ PLC 가 끊겼으면 마지막으로 읽은 것일 뿐이다 — 흐리게 · 'PLC 끊김'
    var conn = core.plcOk();
    var row = e.closest('.plcrow');
    if (row) row.classList.toggle('stale', !conn);
    var pre = conn ? '' : core.chip(core.downText(), 'off') + ' <span class="dim">마지막으로 읽은 것:</span> ';
    if (!p.number) { core.html(e, pre + '<span class="dim">PLC 레시피 정보를 아직 읽지 않았습니다</span>'); return; }
    core.html(e, pre + core.chip(p.plc_ok ? '✓ PLC 검사 통과' : '✕ PLC 검사 미통과', p.plc_ok ? 'ok' : 'warn') +
      ' <b>' + core.esc(p.name || '(이름 모름)') + '</b>' +
      ' <span class="mono dim">번호 ' + core.esc(p.number) + ' · 스텝 ' + core.esc(p.step_count) +
      ' · 블록 ' + core.esc(p.block_count) + ' · 합계 ' + fmt.hex16(p.checksum) + '</span>' +
      (p.name ? '' : ' <span class="warn-txt">이 번호에 맞는 로컬 레시피가 없습니다</span>'));
  }

  function drawEmpty() {
    drawn = false;
    var box = core.bind('rcEdit');
    if (box) box.innerHTML =
      '<div class="empty" style="padding:36px 20px;line-height:2">' +
      '왼쪽에서 레시피를 고르거나 <b>[새로]</b> 를 눌러 만드세요.<br>' +
      '스텝(밸브·시간) · 블록(MFC' + (lim.mfc_count ? ' ' + lim.mfc_count + '채널' : '') +
      ') · 반복 그룹을 편집해 PLC 표로 올립니다.</div>';
    drawStatus();
  }

  /* ---------- 편집기 ---------- */
  function draw() {
    if (!cur) { drawEmpty(); return; }
    var box = core.bind('rcEdit');
    if (!box) return;
    drawn = true;
    var blocks = arr(cur.blocks);
    if (sel.block > blocks.length) sel.block = blocks.length || 1;

    var html =
      '<div class="rc-head">' +
      '<label>이름<input type="text" data-rcf="name" maxlength="' + core.esc(lim.name_max || 80) +
      '" value="' + core.esc(cur.name || '') + '"></label>' +
      '<label class="grow">메모<input type="text" data-rcf="memo" maxlength="' + core.esc(lim.memo_max || 500) +
      '" value="' + core.esc(cur.memo || '') + '"></label>' +
      '</div>' +
      '<div class="rc-tabs">' + blocks.map(function (b, i) {
        return '<button class="rc-btab' + (i + 1 === sel.block ? ' on' : '') +
          '" data-rcblk="' + (i + 1) + '">' + (i + 1) + '. ' +
          core.esc(b.name || '블록') + '</button>';
      }).join('') +
      '<button class="rc-btab add" data-rcadd="block" title="블록 추가">＋</button>' +
      '</div>';

    var b = blocks[sel.block - 1];
    html += b ? blockHtml(b, sel.block) : '<div class="empty">블록을 추가하세요</div>';
    html += groupsHtml();
    box.innerHTML = html;
    drawStatus();
    markCells();
  }

  function blockHtml(b, no) {
    var mfc = arr(b.mfc_sccm);
    var h = '<div class="rc-block">' +
      '<div class="rc-brow">' +
      '<label>블록 이름<input type="text" data-rcb="name" maxlength="' + core.esc(lim.label_max || 40) +
      '" value="' + core.esc(b.name || '') + '"></label>' +
      '<label>반복(사이클)<input type="number" min="1" max="' + core.esc(lim.block_repeat_max || 1000000) +
      '" data-rcb="repeat" value="' + attr(b.repeat) + '"></label>';
    for (var i = 0; i < (lim.mfc_count || 0); i++) {
      h += '<label>MFC' + (i + 1) + ' sccm<input type="number" min="0" step="0.1" ' +
        'data-rcm="' + i + '" value="' + attr(mfc[i]) + '"></label>';
    }
    if ((core.state.device || {}).has_pcv) {
      h += '<label>PCV %<input type="number" min="0" max="100" data-rcb="pcv_pct" value="' +
        attr(b.pcv_pct) + '"></label>';
    }
    if ((core.state.device || {}).has_rf) {
      h += '<label>RF W<input type="number" min="0" step="1" data-rcb="rf_w" value="' +
        attr(b.rf_w) + '"></label>';
    }
    if ((core.state.device || {}).has_o3) {
      h += '<label>O3 설정<input type="number" min="0" step="0.1" data-rcb="o3" value="' +
        attr(b.o3) + '"></label>';
    }
    h += '<button class="btn sm danger-line" data-rcdel="block" title="이 블록 삭제">블록 삭제</button>' +
      '</div>';

    // 스텝 표
    var valves = lim.recipe_valves || [];
    h += '<table class="tbl rc-steps"><thead><tr><th>#</th><th class="l">스텝 이름</th><th>시간 ms</th>' +
      valves.map(function (v) { return '<th class="vh">' + core.esc(v) + '</th>'; }).join('') +
      ((core.state.device || {}).has_rf ? '<th class="vh">RF</th>' : '') +
      '<th class="vh" title="이 스텝에서 일시정지를 받아도 안전한가">정지<br>허용</th>' +
      '<th></th></tr></thead><tbody>';
    arr(b.steps).forEach(function (st, i) {
      st = st || {};
      h += '<tr data-rcstep="' + (i + 1) + '">' +
        '<td class="mono">' + (i + 1) + '</td>' +
        '<td class="l"><input type="text" data-rcs="name" maxlength="' + core.esc(lim.label_max || 40) +
        '" value="' + core.esc(st.name || '') + '"></td>' +
        '<td><input type="number" class="ms" min="' + core.esc(lim.step_ms_min || 20) + '" max="' +
        core.esc(lim.step_ms_max || 3276700) + '" data-rcs="time_ms" value="' + attr(st.time_ms) + '"></td>' +
        valves.map(function (v) {
          return '<td><span class="cb' + (hasValve(st, v) ? ' on' : '') +
            '" tabindex="0" role="checkbox" title="' + core.esc(v) + '" data-rcv="' + core.esc(v) + '"></span></td>';
        }).join('') +
        ((core.state.device || {}).has_rf
          ? '<td><span class="cb' + (st.rf ? ' on' : '') + '" tabindex="0" role="checkbox" data-rcflag="rf"></span></td>' : '') +
        '<td><span class="cb' + (st.pause_ok ? ' on' : '') + '" tabindex="0" role="checkbox" data-rcflag="pause_ok"></span></td>' +
        '<td><button class="xbtn" data-rcdel="step" title="이 스텝 삭제">✕</button></td></tr>';
    });
    h += '</tbody></table>' +
      '<div class="rc-brow"><button class="btn sm" data-rcadd="step">＋ 스텝 추가</button>' +
      '<span class="dim mono" data-bind="rcBlkSum"></span></div>' +
      '</div>';
    return h;
  }

  /** 새 그룹 기본값 — 마지막 그룹 뒤 첫 블록부터 끝 블록까지. 자리가 없으면 null. */
  function nextGroup() {
    var gs = arr(cur.groups);
    var n = arr(cur.blocks).length;
    var lastTo = 0;
    gs.forEach(function (g) { var t = Number((g || {}).to_block) || 0; if (t > lastTo) lastTo = t; });
    if (lastTo + 1 > n) return null;
    return { from_block: lastTo + 1, to_block: n, repeat: 2 };
  }

  function groupsHtml() {
    var gs = arr(cur.groups);
    var n = arr(cur.blocks).length;
    var room = nextGroup();
    var full = gs.length >= (lim.group_max || 5);
    var why = full ? '반복 그룹은 최대 ' + (lim.group_max || 5) + '개입니다'
      : !room ? '마지막 그룹 뒤에 남은 블록이 없습니다 — 블록을 더하거나 그룹 범위를 줄이세요' : '';
    var h = '<div class="rc-groups"><div class="rc-ghead">반복 그룹' +
      '<span class="hint">블록 여러 개를 묶어 다시 돌립니다. 겹치게 둘 수 없습니다.' +
      (why ? ' <span class="warn-txt">' + core.esc(why) + '</span>' : '') + '</span>' +
      '<button class="btn sm" data-rcadd="group"' + (why ? ' disabled title="' + core.esc(why) + '"' : '') +
      '>＋ 그룹</button></div>';
    if (!gs.length) h += '<div class="dim">없음</div>';
    gs.forEach(function (g, i) {
      // ★ 키는 서버·저장 파일·PLC 표와 같은 from_block / to_block (from / to 가 아니다)
      g = g || {};
      h += '<div class="rc-grow" data-rcgroup="' + (i + 1) + '">' +
        '<span class="mono">' + (i + 1) + '</span>' +
        '<label>시작 블록<input type="number" min="1" max="' + n + '" data-rcg="from_block" value="' +
        attr(g.from_block) + '"></label>' +
        '<label>끝 블록<input type="number" min="1" max="' + n + '" data-rcg="to_block" value="' +
        attr(g.to_block) + '"></label>' +
        '<label>반복<input type="number" min="1" max="' + core.esc(lim.group_repeat_max || 32767) +
        '" data-rcg="repeat" value="' + attr(g.repeat) + '"></label>' +
        '<button class="xbtn" data-rcdel="group">✕</button></div>';
    });
    return h + '</div>';
  }

  /* ---------- 검증 결과 · 요약 ---------- */
  function drawStatus() {
    var e = core.bind('rcCheck');
    if (e) {
      var items = (check.errors || []).map(function (x) { return row(x, 'err'); })
        .concat((check.warnings || []).map(function (x) { return row(x, 'warn'); }));
      e.innerHTML = items.length ? items.join('')
        : (cur ? '<div class="ck ok">문제 없습니다 — 올릴 수 있습니다</div>' : '');
    }
    var sm = core.bind('rcSum');
    if (sm) {
      sm.innerHTML = !cur ? '' :
        '<span class="mono">번호 ' + (summary.number == null ? fmt.DASH : summary.number) + '</span>' +
        ' · 스텝 ' + (summary.step_count || 0) + ' / ' + (lim.step_max || 100) +
        ' · 블록 ' + (summary.block_count || 0) + ' / ' + (lim.block_max || 10) +
        ' · 그룹 ' + (summary.group_count || 0) + ' / ' + (lim.group_max || 5) +
        ' · 예상 <b>' + fmt.hms((summary.total_ms || 0) / 1000) + '</b>' +
        (summary.prep_ms ? ' <span class="dim">(블록 준비 ' + fmt.ms(summary.prep_ms, 0) + ' s 포함)</span>' : '');
    }
    var bs = core.bind('rcBlkSum');
    if (bs) {
      var b = (summary.blocks || [])[sel.block - 1];
      bs.textContent = b ? ('1 사이클 ' + fmt.ms(b.cycle_ms) + ' s × ' + b.repeat +
        '회 = ' + fmt.hms(b.total_ms / 1000)) : '';
    }
    core.setText('rcName', curName || '(저장 안 함)');
    var dt = core.bind('rcDirty');
    if (dt) dt.hidden = !dirty;
  }

  function row(x, lvl) {
    var where = [];
    if (x.block) where.push('블록 ' + x.block);
    if (x.step) where.push('스텝 ' + x.step);
    return '<div class="ck ' + lvl + '" data-rcgo="' +
      core.esc([x.block || '', x.step || '', x.field || ''].join('|')) + '">' +
      (where.length ? '<span class="loc">' + where.join(' · ') + '</span>' : '') +
      core.esc(x.msg || x.text || '') + '</div>';
  }

  /** 오류 항목이 가리키는 칸에 표시를 남긴다 — 어디를 고쳐야 하는지 바로 보이게. */
  function markCells() {
    Array.prototype.forEach.call(d.querySelectorAll('#view-recipe .bad'), function (e) {
      e.classList.remove('bad');
    });
    (check.errors || []).forEach(function (x) {
      if (!x.step || Number(x.block) !== sel.block) return;
      var tr = d.querySelector('[data-rcstep="' + x.step + '"]');
      if (tr) tr.classList.add('bad');
    });
  }

  /* ===================== 편집 이벤트 ===================== */
  /** 속성 값 — ★ 모든 값은 이스케이프해서 넣는다. 조작된 레시피 파일(USB 로 받은 것 등)의
   *  '1"><img onerror=…>' 가 운전 PC 화면에서 스크립트로 돌면 로컬 권한으로 장비 명령을 보낼 수 있다. */
  function attr(v) { return core.esc((v === null || v === undefined) ? '' : v); }

  function hasValve(st, tag) { return arr(st.valves).indexOf(tag) >= 0; }

  /** 목록이 아닌 값(형식이 틀린 파일)은 빈 목록으로 그린다 — 오류는 서버 검증이 알려 준다. */
  function arr(v) { return Array.isArray(v) ? v : []; }

  function curBlock() { return (cur.blocks || [])[sel.block - 1]; }

  function stepOf(el) {
    var tr = el.closest('[data-rcstep]');
    if (!tr) return null;
    var b = curBlock();
    return b ? (b.steps || [])[Number(tr.dataset.rcstep) - 1] : null;
  }

  d.addEventListener('input', function (ev) {
    if (!cur || !ev.target.closest('#view-recipe')) return;
    var t = ev.target;
    if (t.dataset.rcf) { cur[t.dataset.rcf] = t.value; validateSoon(); return; }
    var b = curBlock();
    if (t.dataset.rcb && b) {
      b[t.dataset.rcb] = t.dataset.rcb === 'name' ? t.value : numOrNull(t.value);
      if (t.dataset.rcb === 'name') redrawTabsSoon();
      validateSoon();
      return;
    }
    if (t.dataset.rcm !== undefined && b) {
      b.mfc_sccm = b.mfc_sccm || [];
      b.mfc_sccm[Number(t.dataset.rcm)] = numOrNull(t.value);
      validateSoon();
      return;
    }
    if (t.dataset.rcs) {
      var st = stepOf(t);
      if (st) {
        st[t.dataset.rcs] = t.dataset.rcs === 'name' ? t.value : numOrNull(t.value);
        validateSoon();
      }
      return;
    }
    if (t.dataset.rcg) {
      var gr = t.closest('[data-rcgroup]');
      var g = (cur.groups || [])[Number(gr.dataset.rcgroup) - 1];
      if (g) { g[t.dataset.rcg] = numOrNull(t.value); validateSoon(); }
    }
  });

  function numOrNull(v) {
    if (v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  var tabTimer = null;
  function redrawTabsSoon() {
    if (tabTimer) return;
    tabTimer = setTimeout(function () { tabTimer = null; drawTabsOnly(); }, 400);
  }

  function drawTabsOnly() {
    Array.prototype.forEach.call(d.querySelectorAll('[data-rcblk]'), function (b) {
      var i = Number(b.dataset.rcblk);
      var blk = (cur.blocks || [])[i - 1];
      if (blk) b.textContent = i + '. ' + (blk.name || '블록');
    });
  }

  d.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t.closest('#view-recipe') && !t.closest('[data-rcbtn]')) return;

    var open = t.closest('[data-rcopen]');
    if (open) { openRecipe(open.dataset.rcopen); return; }

    var blk = t.closest('[data-rcblk]');
    if (blk) { sel.block = Number(blk.dataset.rcblk); draw(); return; }

    var cb = t.closest('.cb');
    if (cb && cur) {
      var st = stepOf(cb);
      if (!st) return;
      if (cb.dataset.rcv) toggleValve(st, cb.dataset.rcv);
      else if (cb.dataset.rcflag) st[cb.dataset.rcflag] = !st[cb.dataset.rcflag];
      cb.classList.toggle('on');
      validateSoon();
      return;
    }

    var add = t.closest('[data-rcadd]');
    if (add && cur && !add.disabled) { addThing(add.dataset.rcadd); return; }

    var del = t.closest('[data-rcdel]');
    if (del && cur) { delThing(del); return; }

    var btn = t.closest('[data-rcbtn]');
    if (btn && !btn.disabled) doButton(btn.dataset.rcbtn);
  });

  /** 보조 밸브(PV-A1/A2)는 짝이 되는 반응물 밸브와 함께 열려야 의미가 있다.
   *  서버가 경고로 알려 주지만, 켤 때 짝을 같이 켜 주면 실수가 줄어든다. */
  function toggleValve(st, tag) {
    st.valves = st.valves || [];
    var i = st.valves.indexOf(tag);
    if (i >= 0) st.valves.splice(i, 1);
    else {
      st.valves.push(tag);
      var pair = (lim.assist_pair || {})[tag];
      if (pair && st.valves.indexOf(pair) < 0) st.valves.push(pair);
    }
  }

  function addThing(what) {
    if (what === 'block') {
      cur.blocks = cur.blocks || [];
      if (cur.blocks.length >= (lim.block_max || 10)) {
        core.toast('블록은 최대 ' + (lim.block_max || 10) + '개입니다', 'warn');
        return;
      }
      var mfc = [];
      for (var i = 0; i < (lim.mfc_count || 0); i++) mfc.push(0);
      cur.blocks.push({ name: '블록 ' + (cur.blocks.length + 1), repeat: 1, mfc_sccm: mfc, steps: [] });
      sel.block = cur.blocks.length;
    } else if (what === 'step') {
      var b = curBlock();
      if (!b) return;
      b.steps = b.steps || [];
      cur.blocks.reduce(function (n, x) { return n + (x.steps || []).length; }, 0);
      b.steps.push({ name: '스텝 ' + (b.steps.length + 1), time_ms: 100, valves: [], pause_ok: false });
    } else if (what === 'group') {
      cur.groups = cur.groups || [];
      if (cur.groups.length >= (lim.group_max || 5)) {
        core.toast('반복 그룹은 최대 ' + (lim.group_max || 5) + '개입니다', 'warn');
        return;
      }
      var ng = nextGroup();
      if (!ng) { core.toast('마지막 그룹 뒤에 남은 블록이 없습니다', 'warn'); return; }
      cur.groups.push(ng);
    }
    draw();
    validateSoon();
  }

  function delThing(btn) {
    var what = btn.dataset.rcdel;
    if (what === 'block') {
      core.confirmAsk('블록을 지울까요?', '블록 ' + sel.block + ' 과 그 안의 스텝이 모두 사라집니다.',
        '삭제', function () {
          cur.blocks.splice(sel.block - 1, 1);
          sel.block = Math.max(1, sel.block - 1);
          draw(); validateSoon();
        });
      return;
    }
    if (what === 'step') {
      var tr = btn.closest('[data-rcstep]');
      var b = curBlock();
      if (b && tr) b.steps.splice(Number(tr.dataset.rcstep) - 1, 1);
    } else if (what === 'group') {
      var gr = btn.closest('[data-rcgroup]');
      if (gr) cur.groups.splice(Number(gr.dataset.rcgroup) - 1, 1);
    }
    draw();
    validateSoon();
  }

  /* ===================== 단추 ===================== */
  function openRecipe(name) {
    if (dirty) {
      core.confirmAsk('저장하지 않은 편집이 있습니다',
        '<b>' + core.esc(name) + '</b> 를 열면 지금 편집한 내용이 사라집니다.',
        '그래도 열기', function () { dirty = false; w.app.send('recipe_load', { name: name }); });
      return;
    }
    w.app.send('recipe_load', { name: name });
  }

  /** 저장 요청 — 답(recipe_saved)이 올 때까지 이름 · dirty 를 바꾸지 않는다. */
  function sendSave(nm) {
    saving = { name: nm, editNo: editNo };
    core.setText('rcSaveMsg', '저장하는 중…');
    if (!w.app.send('recipe_save', { name: nm, recipe: cur })) {
      saving = null;
      core.setText('rcSaveMsg', '저장 안 됨 — 서버에 연결되어 있지 않습니다');
    }
  }

  function exists(nm) {
    return ((core.state || {}).recipes || []).some(function (r) { return r.name === nm; });
  }

  function doButton(which) {
    if (which === 'new' && dirty) {
      core.confirmAsk('저장하지 않은 편집이 있습니다',
        '새 레시피를 만들면 지금 편집한 내용이 사라집니다.',
        '그래도 새로', function () { dirty = false; doButton('new'); });
      return;
    }
    if (which === 'new') {
      var mk = [];
      for (var i = 0; i < (lim.mfc_count || 0); i++) mk.push(0);
      cur = {
        format: lim.format, name: '', memo: '',
        blocks: [{ name: '블록 1', repeat: 1, mfc_sccm: mk, steps: [] }], groups: []
      };
      curName = '';
      saving = null;
      core.setText('rcSaveMsg', '');
      sel.block = 1;
      draw();
      validateSoon();
      return;
    }
    if (!cur) { core.toast('레시피를 먼저 고르세요', 'warn'); return; }

    if (which === 'save') {
      var nm = (cur.name || '').trim();
      if (!nm) { core.toast('레시피 이름을 넣으세요', 'warn'); return; }
      if (nm !== curName && exists(nm)) { confirmOverwrite(nm); return; }
      sendSave(nm);
      return;
    }
    if (which === 'saveas') {
      askName('다른 이름으로 저장', cur.name || '', function (nm) {
        // ★ 이미 있는 이름이면 덮어쓰기 확인 — PLC 에 올린 레시피를 조용히 바꾸지 않게
        if (exists(nm)) { confirmOverwrite(nm); return; }
        cur.name = nm;
        var f = d.querySelector('[data-rcf="name"]');
        if (f) f.value = nm;
        sendSave(nm);
      });
      return;
    }
    if (which === 'rename') {
      if (!curName) { core.toast('먼저 저장하세요', 'warn'); return; }
      askName('이름 바꾸기', curName, function (nm) {
        w.app.send('recipe_rename', { name: curName, new_name: nm });
        curName = nm;
        cur.name = nm;
        draw();
      });
      return;
    }
    if (which === 'delete') {
      if (!curName) { core.toast('저장된 레시피가 아닙니다', 'warn'); return; }
      core.confirmAsk('레시피를 삭제할까요?',
        '<b>' + core.esc(curName) + '</b> 파일을 지웁니다. 되돌릴 수 없습니다.',
        '삭제', function () {
          w.app.send('recipe_delete', { name: curName });
          cur = null; curName = ''; dirty = false;
          drawEmpty();
        });
      return;
    }
    if ((check.errors || []).length) {
      core.toast('검증 오류 ' + check.errors.length + '건을 먼저 고치세요', 'warn');
      return;
    }
    if (which === 'upload') {
      if (!curName || dirty) { core.toast('먼저 저장하세요 — 저장된 내용을 올립니다', 'warn'); return; }
      core.confirmAsk('PLC 에 레시피를 올릴까요?',
        '<b>' + core.esc(curName) + '</b> (번호 ' + summary.number + ')<br>' +
        'PLC 레시피 표 영역을 덮어씁니다. 공정 중에는 올릴 수 없습니다.',
        '올리기', function () { w.app.send('recipe_upload', { name: curName }); });
      return;
    }
    if (which === 'start') {
      if (!curName || dirty) { core.toast('먼저 저장하세요 — 저장된 내용으로 시작합니다', 'warn'); return; }
      w.app.send('recipe_select', { name: curName });
      core.setTab('main');
      core.toast('운전 탭에서 시작 조건을 확인하고 [공정 시작]을 누르세요', 'info');
    }
  }

  function confirmOverwrite(nm) {
    core.confirmAsk('같은 이름의 레시피가 있습니다',
      '<b>' + core.esc(nm) + '</b> 파일을 지금 편집한 내용으로 덮어씁니다.<br>' +
      'PLC 에 올라가 있는 레시피라면 다음에 올릴 때 바뀐 내용이 올라갑니다.',
      '덮어쓰기', function () {
        cur.name = nm;
        var f = d.querySelector('[data-rcf="name"]');
        if (f) f.value = nm;
        sendSave(nm);
      });
  }

  /** 이름 입력 — 브라우저 prompt 는 pywebview 창에서 동작이 제각각이라 쓰지 않는다. */
  function askName(title, initial, cb) {
    core.confirmAsk(title,
      '<label class="onelabel">이름<input type="text" id="rcAskName" value="' +
      core.esc(initial) + '" maxlength="' + core.esc(lim.name_max || 80) + '"></label>' +
      '<div class="hint">\\ / : * ? " &lt; &gt; | 는 쓸 수 없습니다.</div>',
      '확인', function () {
        var e = d.getElementById('rcAskName');
        var nm = e ? (e.value || '').trim() : '';
        if (!nm) { core.toast('이름을 넣으세요', 'warn'); return; }
        cb(nm);
      });
    setTimeout(function () {
      var e = d.getElementById('rcAskName');
      if (e && d.activeElement === e) e.select();   // 포커스는 확인 창이 이 칸에 둔다
    }, 30);
  }

  /* ---------- 오류 항목 → 해당 칸으로 ---------- */
  d.addEventListener('click', function (ev) {
    var g = ev.target.closest('[data-rcgo]');
    if (!g) return;
    var p = (g.dataset.rcgo || '').split('|');
    if (p[0]) { sel.block = Number(p[0]); draw(); }
    if (p[1]) {
      var tr = d.querySelector('[data-rcstep="' + p[1] + '"]');
      if (tr) {
        tr.scrollIntoView({ block: 'center' });
        var inp = tr.querySelector('input');
        if (inp) inp.focus();
      }
    }
  });

  // 이름 입력 창은 Enter 로 확인
  d.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && ev.target && ev.target.id === 'rcAskName') {
      ev.preventDefault();
      var ok = d.querySelector('[data-cf="ok"]');
      if (ok) ok.click();
    }
  });

  core.register('recipe', { render: render, update: update });
  w.viewRecipe = { render: render, update: update, dirty: function () { return !!(cur && dirty); } };
})(window, document);
