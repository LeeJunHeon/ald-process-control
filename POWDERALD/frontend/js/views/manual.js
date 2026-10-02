/* ============================================================
 * views/manual.js — 수동 조작 창.
 *
 * 밸브 / MFC / 히터 / 장비 전용(PCV·RF 또는 O3 라인).
 *
 * ★ 요청(PLC 반영 영역 D04012·D04050)과 출력(D00010·D00014)을 나란히 보여 준다.
 *   요청했는데 안 나간 것은 '대기'로 둔다 — PLC 가 허가를 안 준 것을 '열렸다'로
 *   보여 주면 운전자가 다음 판단을 틀린다.
 * ★ 히터 전원 스위치는 서버가 가진 현재 값 기준으로 목표 상태를 보내고,
 *   응답이 올 때까지 그 스위치를 잠근다(두 번 눌러 되돌아가는 일을 막는다).
 * ★ 잠금 해제가 없으면 밸브를 못 누른다. 공정이 시작되면 서버가 다시 잠근다.
 * ★ 판정은 서버가 한다 — 여기서 막는 것은 손이 미끄러지는 것을 줄이려는 것뿐이고,
 *   실제로 거절하는 쪽은 PLC 다.
 * ★ 입력 칸은 창을 새로 열 때(또는 장비 구조가 바뀔 때)만 다시 만든다. 상태를 다시 받을 때마다
 *   (밸브를 누르거나 잠금 해제를 바꿀 때) 통째로 다시 그리면 치던 MFC·히터·RF 값이 지워진다.
 *   나머지 갱신은 표시 글자 · 상태 클래스만 바꾼다.
 * ============================================================ */
(function (w, d) {
  'use strict';

  var built = false;
  var builtSig = '';
  var heaterBusy = {};     // ch → { target, until } — 응답 전까지 스위치 잠금

  function open() {
    core.setText('mnDev', ((core.state || {}).device || {}).name || '');
    built = false;                 // 창을 새로 열 때는 새 칸으로
    build();
    core.showModal('manualModal', '[data-mn="close"]');
    paint();
  }

  /** 입력 칸을 다시 만들어야 하는 구조(밸브·MFC·히터·장비 전용)의 서명. */
  function structSig() {
    var s = core.state || {};
    return JSON.stringify([s.device || {}, (s.structure || {}).valves, (s.structure || {}).mfc,
                           (s.structure || {}).heaters, (s.recipe_limits || {}).recipe_valves]);
  }

  function build() {
    var box = core.bind('mnBody');
    if (!box || built) return;
    builtSig = structSig();
    var s = core.state || {};
    var dev = s.device || {};
    var str = s.structure || {};
    var rv = (s.recipe_limits || {}).recipe_valves || [];

    var h = '<div class="mn-lock">' +
      '<span class="sw" data-bind="mnUnlock" data-mn="unlock" role="switch" tabindex="0"></span>' +
      '<span>밸브 조작 잠금 해제<span class="hint"> 5분 뒤 · 공정이 시작되면 자동으로 잠깁니다</span></span>' +
      '<span class="right mono" data-bind="mnLeft"></span></div>' +
      '<div class="mn-sec">밸브</div><div class="mn-valves">';
    (str.valves || []).forEach(function (v) {
      if (rv.indexOf(v.tag) < 0) return;      // 자동 밸브는 수동으로 다루지 않는다
      h += '<div class="mn-v" role="button" tabindex="0" data-mnv="' + core.esc(v.tag) + '">' +
        '<span class="t">' + core.esc(v.tag) + '</span>' +
        '<span class="n">' + core.esc(v.name || '') + '</span>' +
        '<span class="st" data-mnvst="' + core.esc(v.tag) + '">&mdash;</span></div>';
    });
    h += '</div>';

    h += '<div class="mn-sec">MFC</div><div class="mn-grid">';
    (str.mfc || []).forEach(function (m) {
      h += '<label>' + core.esc(m.no) + ' ' + core.esc(m.name) +
        (m.full_scale_sccm == null ? ' <span class="unconf">풀스케일 미정</span>' : '') +
        '<span class="inrow"><input type="number" min="0" step="0.1" data-mnmfc="' + core.esc(m.no) + '"' +
        (m.full_scale_sccm == null ? ' disabled' : '') + '>' +
        '<span class="unit">sccm</span>' +
        '<span class="cur mono" data-mnmfccur="' + core.esc(m.no) + '">&mdash;</span></span></label>';
    });
    h += '<button class="btn sm primary" data-mn="mfc">MFC 적용</button></div>';

    h += '<div class="mn-sec">히터<span class="hint"> 설정 온도와 전원. 공정 중에는 바꿀 수 없습니다.</span></div>' +
      '<div class="mn-grid">';
    (str.heaters || []).forEach(function (x) {
      if (!x.enabled) return;
      var ch = core.esc(x.ch);
      h += '<label>CH' + ch + ' ' + core.esc(x.name) +
        (x.max_c == null ? ' <span class="unconf">한계 미정</span>' : '') +
        '<span class="inrow"><input type="number" min="0" step="1" data-mnhsv="' + ch + '"' +
        (x.max_c == null ? ' disabled data-nolimit' : '') + '>' +
        '<span class="unit">℃</span>' +
        '<span class="sw sm" role="switch" tabindex="0" data-mnhpow="' + ch + '"></span>' +
        '<span class="cur mono" data-mnhcur="' + ch + '">&mdash;</span></span></label>';
    });
    h += '<button class="btn sm primary" data-mn="heater">히터 적용</button></div>';

    if (dev.has_pcv || dev.has_rf) {
      h += '<div class="mn-sec">PCV · RF</div><div class="mn-grid">';
      if (dev.has_pcv) {
        h += '<label>PCV 목표<span class="inrow">' +
          '<input type="number" min="0" max="100" step="1" data-mnpcv>' +
          '<span class="unit">%</span>' +
          '<button class="btn sm" data-mn="pcv">적용</button></span></label>';
      }
      if (dev.has_rf) {
        h += '<label>RF 시험 전력<span class="inrow">' +
          '<input type="number" min="0" step="1" data-mnrf>' +
          '<span class="unit">W</span>' +
          '<button class="btn sm" data-mn="rf_on">RF 켜기</button>' +
          '<button class="btn sm" data-mn="rf_off">끄기</button></span></label>' +
          '<div class="hint">대기 중에만 됩니다. 전력이 0 이면 PLC 가 RF 요청을 지웁니다.</div>';
      }
      h += '</div>';
    }

    if (dev.has_o3) {
      h += '<div class="mn-sec">O3 라인</div><div class="mn-grid">' +
        '<label>O3 설정<span class="inrow"><input type="number" min="0" step="0.1" data-mno3>' +
        '<button class="btn sm" data-mn="o3_set">적용</button></span></label>' +
        '<label>라인<span class="inrow">' +
        '<button class="btn sm" data-mn="o3_on">켜기 (바이패스 → IV-B → 발생기)</button>' +
        '<button class="btn sm" data-mn="o3_off">끄기</button></span></label>' +
        '<div class="hint" data-bind="mnO3Note">끄기는 발생기를 먼저 끄고, 배관에 남은 O3 를 뺀 뒤 바이패스 라인을 닫습니다.</div>' +
        '</div>';
    }

    h += '<div class="hint warn" data-bind="mnAuxPending"></div>';
    box.innerHTML = h;
    built = true;
  }

  /* ===================== 상태 그리기 ===================== */
  function paint() {
    var m = d.getElementById('manualModal');
    if (!m || m.hidden) return;
    var s = core.state || {};
    var t = s.live || {};
    var mn = t.manual || {};
    var conn = !core.offline && !!(t.plc && t.plc.connected);
    var run = !!((t.process || {}).running);
    var can = core.canOperate() && conn && !run;

    var sw = core.bind('mnUnlock');
    if (sw) {
      sw.classList.toggle('on', !!mn.unlocked);
      // ★ 원격 · 다른 장비 · 끊김 · 공정 중에는 스위치도 잠근다(서버가 거절하긴 하지만 화면에서 먼저)
      sw.classList.toggle('dis', !can);
      sw.tabIndex = can ? 0 : -1;
      sw.setAttribute('aria-checked', String(!!mn.unlocked));
    }
    var why = core.plcReason();
    core.setText('mnLeft', why ? why + ' — 수동 조작 잠김'
      : mn.unlocked ? '잠금 해제 ' + (mn.unlock_left_s || 0) + ' s 남음'
        : (run ? '공정 중 — 수동 조작 잠김' : '잠김'));
    // 숫자 칸도 잠근다(한계가 정해지지 않은 히터 칸은 늘 잠김)
    Array.prototype.forEach.call(d.querySelectorAll('#manualModal input'), function (e) {
      var off = !can || e.hasAttribute('data-nolimit');
      if (e.disabled !== off) e.disabled = off;
    });

    Array.prototype.forEach.call(d.querySelectorAll('[data-mnv]'), function (e) {
      var tag = e.dataset.mnv;
      var vd = ((s.structure || {}).valves || []).filter(function (v) { return v.tag === tag; })[0];
      if (!vd) return;
      var req = conn && core.bit(mn.valve_request, vd.bit);
      var app = conn && core.bit(mn.valve_out, vd.bit);
      e.classList.toggle('on', app);
      e.classList.toggle('req', req && !app);
      e.classList.toggle('dis', !can || !mn.unlocked);
      e.tabIndex = (!can || !mn.unlocked) ? -1 : 0;
      var st = d.querySelector('[data-mnvst="' + tag + '"]');
      if (st) {
        core.html(st, !conn ? fmt.DASH
          : app ? core.chip('● 열림', 'ok')
            : req ? core.chip('◐ 대기', 'warn', 'PLC 가 아직 허가하지 않았습니다')
              : core.chip('○ 닫힘', 'off'));
      }
    });

    ((s.structure || {}).mfc || []).forEach(function (x) {
      var v = (t.mfc || [])[x.no - 1] || {};
      setText('[data-mnmfccur="' + x.no + '"]',
        conn ? '현재 ' + fmt.flow(v.pv) + ' / 설정 ' + fmt.flow(v.sv) : fmt.DASH);
    });
    ((s.structure || {}).heaters || []).forEach(function (x) {
      if (!x.enabled) return;
      var v = (t.heaters || [])[x.ch - 1] || {};
      // 온도조절기 통신이 없는 채널은 PLC 과온 감시가 없다 — 켜기는 서버가 막고, 이유를 여기 보인다
      setText('[data-mnhcur="' + x.ch + '"]', !conn ? fmt.DASH : (v.comm_ok === false
        ? '온도조절기 통신 없음 — 켤 수 없음(끄기는 됨) · 설정 ' + fmt.temp(v.sv)
        : '현재 ' + fmt.temp(v.pv) + ' / 설정 ' + fmt.temp(v.sv)));
      var p = d.querySelector('[data-mnhpow="' + x.ch + '"]');
      var busy = heaterBusy[x.ch];
      if (busy && (!conn || v.power === busy.target || Date.now() > busy.until)) {
        delete heaterBusy[x.ch];
        busy = null;
      }
      if (p) {
        p.classList.toggle('on', !!(conn && v.power));
        p.classList.toggle('busy', !!busy);
        p.classList.toggle('dis', !can);
        p.tabIndex = can ? 0 : -1;
        p.setAttribute('aria-checked', String(!!(conn && v.power)));
        var tt = busy ? '응답을 기다리는 중' : !can ? '지금은 바꿀 수 없습니다(공정 중 · 원격 · 연결 끊김)'
          : (conn && v.comm_ok === false ? (v.power_block || '') : '');
        if (p.title !== tt) p.title = tt;
      }
    });

    var ap = mn.aux_pending || [];
    core.setText('mnAuxPending', conn && ap.length
      ? '허가 대기(요청했지만 출력 안 나감): ' + ap.join(' · ') : '');
    if ((s.device || {}).has_o3) {
      var left = mn.o3_off_left_s || 0;
      core.setText('mnO3Note', left > 0
        ? '발생기를 껐습니다 — ' + left + ' s 뒤 바이패스 라인을 닫습니다 (화면을 닫아도 진행)'
        : '끄기는 발생기를 먼저 끄고, 배관에 남은 O3 를 뺀 뒤 바이패스 라인을 닫습니다.');
    }

    Array.prototype.forEach.call(d.querySelectorAll('#manualModal [data-mn]'), function (b) {
      if (b.dataset.mn === 'close' || b.dataset.mn === 'unlock') return;
      b.disabled = !can;
    });
  }

  function setText(sel, v) {
    var e = d.querySelector(sel);
    if (e) e.textContent = v;
  }

  /* ===================== 조작 ===================== */
  d.addEventListener('click', function (ev) {
    if (ev.target.closest('[data-bind="mnOpen"]') || ev.target.closest('[data-mnopen]')) { open(); return; }
    var v = ev.target.closest('[data-mnv]');
    if (v && !v.classList.contains('dis')) {
      var tag = v.dataset.mnv;
      var on = !v.classList.contains('on') && !v.classList.contains('req');
      w.app.send('manual_valve', { tag: tag, on: on });
      return;
    }
    var pw = ev.target.closest('[data-mnhpow]');
    if (pw && pw.classList.contains('dis')) return;
    if (pw) {
      var ch = Number(pw.dataset.mnhpow);
      if (heaterBusy[ch]) return;
      // ★ 화면 칠이 아니라 서버가 가진 현재 값(D01010 되읽기) 기준으로 목표를 정한다.
      var cur = (((core.state || {}).live || {}).heaters || [])[ch - 1] || {};
      if (cur.power == null) { core.toast('히터 전원 상태를 아직 읽지 못했습니다', 'warn'); return; }
      var target = !cur.power;
      heaterBusy[ch] = { target: target, until: Date.now() + 4000 };
      pw.classList.add('busy');
      w.app.send('manual_heater', { power: kv(ch, target) });
      return;
    }
    var b = ev.target.closest('#manualModal [data-mn]');
    if (!b || b.disabled) return;
    act(b.dataset.mn);
  });

  function kv(k, v) { var o = {}; o[k] = v; return o; }

  function act(what) {
    if (what === 'close') { core.hideModal('manualModal'); return; }
    if (what === 'unlock') {
      var sw = core.bind('mnUnlock');
      if (sw.classList.contains('dis')) return;
      w.app.send('manual_unlock', { on: !sw.classList.contains('on') });
      return;
    }
    if (what === 'mfc') {
      var sc = {};
      Array.prototype.forEach.call(d.querySelectorAll('[data-mnmfc]'), function (e) {
        if (e.value !== '' && !e.disabled) sc[e.dataset.mnmfc] = Number(e.value);
      });
      if (!Object.keys(sc).length) { core.toast('바꿀 MFC 값을 넣으세요', 'warn'); return; }
      w.app.send('manual_mfc', { sccm: sc });
      return;
    }
    if (what === 'heater') {
      var sv = {};
      Array.prototype.forEach.call(d.querySelectorAll('[data-mnhsv]'), function (e) {
        if (e.value !== '' && !e.disabled) sv[e.dataset.mnhsv] = Number(e.value);
      });
      if (!Object.keys(sv).length) { core.toast('바꿀 설정 온도를 넣으세요', 'warn'); return; }
      core.confirmAsk('히터 설정을 바꿀까요?',
        '설정 온도를 바꿉니다. 과온 한계를 넘는 값은 PLC 가 거절합니다.<br>' +
        '전원이 꺼져 있으면 온도가 오르지 않습니다.',
        '적용', function () { w.app.send('manual_heater', { sv: sv }); });
      return;
    }
    if (what === 'pcv') {
      var e1 = d.querySelector('[data-mnpcv]');
      if (!e1 || e1.value === '') { core.toast('PCV 목표를 넣으세요', 'warn'); return; }
      w.app.send('manual_pcv', { pct: Number(e1.value) });
      return;
    }
    if (what === 'rf_on') {
      var e2 = d.querySelector('[data-mnrf]');
      var watt = e2 ? Number(e2.value) : 0;
      if (!watt) { core.toast('RF 전력을 넣으세요 — 0 이면 PLC 가 요청을 지웁니다', 'warn'); return; }
      // ★ RF 는 사람이 옆에 있는 상태에서만 켜야 한다 — 확인 창을 반드시 거친다.
      core.confirmAsk('RF 를 켤까요?',
        '<b>' + watt + ' W</b> 로 RF 를 켭니다. 시험 목적이며 대기 중에만 됩니다.<br>' +
        '압력·반사 전력 조건을 PLC 가 확인하고, 조건이 어긋나면 즉시 끕니다.',
        'RF 켜기', function () { w.app.send('manual_rf', { on: true, watt: watt }); });
      return;
    }
    if (what === 'rf_off') { w.app.send('manual_rf', { on: false }); return; }
    if (what === 'o3_set' || what === 'o3_on') {
      var e3 = d.querySelector('[data-mno3]');
      var val = e3 ? Number(e3.value) : 0;
      if (!val) { core.toast('O3 설정 값을 넣으세요', 'warn'); return; }
      if (what === 'o3_set') { w.app.send('manual_o3', { action: 'set', value: val }); return; }
      core.confirmAsk('O3 라인을 켤까요?',
        '바이패스 펌프 → IV-B → 발생기 순서로 켭니다.<br>' +
        '실내 O3 감지·발생기 알람이 있으면 PLC 가 막습니다.',
        'O3 켜기', function () { w.app.send('manual_o3', { action: 'on', value: val }); });
      return;
    }
    if (what === 'o3_off') {
      // 발생기를 먼저 끄고, 배관에 남은 O3 를 뺀 뒤 바이패스 라인을 닫는다 — 지연은 서버가 센다.
      w.app.send('manual_o3', { action: 'off' });
    }
  }

  core.register('manual', {
    render: function () {
      // ★ 구조가 바뀐 때만 다시 만든다 — 상태를 받을 때마다 다시 만들면 치던 값이 지워진다.
      if (built && structSig() === builtSig) { paint(); return; }
      built = false;
      var m = d.getElementById('manualModal');
      if (m && !m.hidden) { build(); paint(); }
    },
    update: paint
  });
  /** 명령 응답(알림)이 오면 잠근 스위치를 푼다. */
  function onNotice() { heaterBusy = {}; paint(); }

  w.viewManual = { open: open, paint: paint, onNotice: onNotice };
})(window, document);
