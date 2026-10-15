/* ============================================================
 * terms.js — 화면 공통 용어 사전 (v0.5.0).
 *
 * ★ 장비 · 신호 · 상태 · 칸 이름 · 표 머리 · 탭 · 단추 라벨 같은 '용어'는 여기서만 정한다(영문).
 *   장비 구조의 이름(밸브 · 입력 · 인터락 · 알람 · 히터/MFC 기본 이름)은 서버의 device.py 가 정하고
 *   화면은 받은 그대로 쓴다. 데이터 로그 머리 · 트렌드 내보내기 열 이름도 같은 용어다.
 * ★ 문장(확인 창 · 안내 · 오류 설명 · 툴팁 설명 · 이벤트 로그)은 한글로 둔다 — 여기에 넣지 않는다.
 * ★ index.html 의 고정 라벨은 data-t="키" 로 이 사전에서 채운다(T.apply).
 * ============================================================ */
(function (root) {
  'use strict';

  var D = {
    // 탭
    'tab.main': 'Operation', 'tab.recipe': 'Recipe', 'tab.trend': 'Trend', 'tab.datalog': 'Data Log',
    'tab.alarm': 'Alarm/Log', 'tab.setup': 'Settings',
    // 칸
    'panel.pid': 'P&ID', 'panel.process': 'Process', 'panel.vac': 'Pressure / Exhaust / Interlock',
    'panel.heater': 'Heater', 'panel.mfc': 'MFC', 'panel.rfpcv': 'RF / PCV', 'panel.o3': 'O3',
    'panel.savedRecipes': 'Saved Recipes', 'panel.edit': 'Edit', 'panel.check': 'Check',
    'panel.datalog': 'Process Data Log', 'panel.table': 'Table', 'panel.recipeCopy': 'Recipe Copy',
    'panel.alarms': 'Active Alarms', 'panel.notices': 'Program Notices', 'panel.alarmHist': 'Alarm History',
    'panel.log': 'Program Log', 'panel.manual': 'Manual',
    'chart.pressure': 'Pressure (Torr)', 'chart.temp': 'Temperature (℃)', 'chart.mfc': 'MFC Flow (sccm)',
    'chart.heater': 'Heater (℃)', 'chart.mfcShort': 'MFC (sccm)', 'chart.device': 'Device',
    // 장비
    'eq.chamber': 'Process Chamber', 'eq.reactor': 'Powder Reactor', 'eq.can1': 'Canister 1',
    'eq.can2': 'Canister 2', 'eq.can3': 'Canister 3', 'eq.h2o': 'H2O Canister R', 'eq.pline': 'Precursor Line',
    'eq.manifold': 'Reactant Manifold', 'eq.trap': 'Trap', 'eq.dryPump': 'Dry Pump', 'eq.exhPump': 'Exhaust Pump',
    'eq.bypassPump': 'Bypass Pump', 'eq.o3gen': 'O3 Generator', 'eq.vent': 'Vent', 'eq.rf': 'RF Power / Matcher',
    'eq.electrode': 'RF Electrode', 'eq.stage': 'Stage', 'eq.bed': 'Powder Bed', 'eq.lid': 'Lid',
    'eq.hingeLid': 'Hinge Lid', 'eq.assist': 'Assist N2', 'eq.o2': 'O2 Supply', 'eq.exhIso': 'Exhaust Isolation',
    'eq.plcAuto': 'PLC Auto', 'eq.exhaust': 'Exhaust', 'eq.pcv': 'Inline PCV', 'eq.chamberTemp': 'Chamber Temp',
    'eq.precursor': 'Precursor', 'eq.reactant': 'Reactant', 'eq.reactantO3': 'Reactant (O3)', 'eq.pump': 'Pump',
    // 값
    'v.setpoint': 'Setpoint', 'v.actual': 'Actual', 'v.output': 'Output', 'v.power': 'Power', 'v.status': 'Status',
    'v.fullScale': 'Full Scale', 'v.gas': 'Gas', 'v.block': 'Block', 'v.step': 'Step', 'v.cycle': 'Cycle',
    'v.group': 'Group', 'v.elapsed': 'Elapsed', 'v.remaining': 'Remaining', 'v.planned': 'Planned',
    'v.recipe': 'Recipe', 'v.channel': 'Channel', 'v.target': 'Target', 'v.position': 'Position',
    'v.forward': 'Forward', 'v.reflected': 'Reflected', 'v.number': 'No.', 'v.estimate': 'Est.',
    'v.severity': 'Severity', 'v.raised': 'Raised', 'v.cleared': 'Cleared', 'v.code': 'Code', 'v.message': 'Message',
    'v.time': 'Time', 'v.name': 'Name', 'v.memo': 'Memo', 'v.repeat': 'Repeat', 'v.ms': 'Time ms',
    'v.stepName': 'Step Name', 'v.pauseOk': 'Pause OK', 'v.fromBlock': 'From Block', 'v.toBlock': 'To Block',
    'v.access': 'Access', 'v.station': 'Station', 'v.enabled': 'Enabled', 'v.limit': 'Overtemp Limit ℃',
    'v.defaultSv': 'Default Setpoint ℃', 'v.confirmed': 'Confirmed', 'v.item': 'Item', 'v.before': 'Before',
    'v.after': 'After', 'v.raw': 'PRM Raw', 'v.written': 'Written Raw', 'v.readback': 'Readback', 'v.match': 'Match',
    // 상태
    's.open': 'Open', 's.closed': 'Closed', 's.unknown': 'Unknown', 's.on': 'ON', 's.off': 'OFF',
    's.normal': 'Normal', 's.limitUnset': 'Limit Unset', 's.idle': 'Idle', 's.running': 'Running',
    's.paused': 'Paused', 's.pending': 'Pending', 's.commLost': 'Comm Lost', 's.ctrlAlarm': 'Controller Alarm',
    's.tableOk': 'Table OK', 's.tableNg': 'Table NG', 's.noAlarm': 'No Alarm', 's.critical': 'Critical',
    's.warning': 'Warning', 's.flow': 'Flow', 's.clickToOperate': 'Click to operate', 's.idlePipe': 'Idle', 's.exhaust': 'Exhaust', 's.heated': 'Heated',
    's.serverLost': 'Server Lost', 's.plcLost': 'PLC Lost', 's.plcStalled': 'PLC Stalled', 's.noAddress': 'No PLC Address',
    's.feedbackOk': 'Feedback OK', 's.noFeedback': 'No Feedback', 's.match': 'Match', 's.mismatch': 'Mismatch',
    's.unconfirmed': 'Unconfirmed', 's.thisPc': 'This PC', 's.viewOnly': 'View Only', 's.recording': 'Recording',
    's.locked': 'Locked', 's.unlocked': 'Unlocked', 's.notSaved': 'Not Saved',
    // 단추
    'b.start': 'Start', 'b.pause': 'Pause', 'b.resume': 'Resume', 'b.stopAfterCycle': 'Stop after Cycle',
    'b.abort': 'Abort', 'b.cancelWait': 'Cancel Wait', 'b.manual': 'Manual…', 'b.pumpStart': 'Pumping Start',
    'b.pumpStop': 'Pumping Stop', 'b.vent': 'Vent', 'b.closeAll': 'Close All Valves', 'b.ack': 'Alarm Ack',
    'b.reset': 'Alarm Reset', 'b.exit': 'Exit', 'b.apply': 'Apply', 'b.open': 'Open', 'b.close': 'Close',
    'b.unlock': 'Unlock', 'b.lock': 'Lock', 'b.cancel': 'Cancel', 'b.ok': 'OK', 'b.run': 'Run', 'b.on': 'ON',
    'b.off': 'OFF', 'b.new': 'New', 'b.save': 'Save', 'b.saveAs': 'Save As', 'b.rename': 'Rename',
    'b.delete': 'Delete', 'b.upload': 'Upload to PLC', 'b.startRecipe': 'Start with This', 'b.live': 'Live',
    'b.history': 'History', 'b.load': 'Load', 'b.undo': 'Undo', 'b.exportCsv': 'Export CSV',
    'b.openFolder': 'Open Folder', 'b.refresh': 'Refresh', 'b.chart': 'Chart', 'b.table': 'Table',
    'b.recipe': 'Recipe', 'b.prev': 'Prev 200', 'b.next': 'Next 200', 'b.addStep': '+ Step', 'b.addGroup': '+ Group',
    'b.deleteBlock': 'Delete Block', 'b.revert': 'Revert', 'b.review': 'Review & Save', 'b.changePin': 'Change PIN',
    'b.setPin': 'Set PIN', 'b.backupFolder': 'Open Backup Folder', 'b.ackBuzzer': 'Ack (Buzzer Off)',
    'b.mfcApply': 'Apply MFC', 'b.heaterApply': 'Apply Heater', 'b.o3On': 'O3 Line ON', 'b.o3Off': 'O3 Line OFF',
    'b.rfOn': 'RF ON', 'b.rfOff': 'RF OFF', 'b.pumping': 'Pumping',
    // 머리
    'h.simulator': 'Simulator', 'h.control': 'Process Control'
  };

  function T(key, fallback) {
    return Object.prototype.hasOwnProperty.call(D, key) ? D[key] : (fallback == null ? key : fallback);
  }
  T.dict = D;
  /** data-t="키" 인 요소의 글자를 사전으로 채운다(자식 요소가 있는 칸은 첫 글자 마디만). */
  T.apply = function (scope) {
    if (!scope || !scope.querySelectorAll) return;
    Array.prototype.forEach.call(scope.querySelectorAll('[data-t]'), function (e) {
      var v = T(e.getAttribute('data-t'));
      var first = e.firstChild;
      if (first && first.nodeType === 3) first.nodeValue = v;
      else if (!e.children.length) e.textContent = v;
      else e.insertBefore(e.ownerDocument.createTextNode(v), first);
    });
  };

  /** ★ v0.5.1(D6): 옛 데이터 로그(한글로 적힌 상태 칸 값)를 화면에서만 영문으로 — 파일은 그대로 둔다 */
  var OLD = {
    '초기화': 'Init', '대기': 'Idle', '공정 준비': 'Ready', '공정 중': 'Running', '일시정지': 'Paused',
    '사이클 후 정지 중': 'Stopping after Cycle', '안전 정지': 'Safe Stop',
    '조건 확인': 'Check', '표 복사': 'Table Copy', '블록 준비': 'Block Prep', '스텝 실행': 'Step Run',
    '다음 스텝 계산': 'Next Step', '완료': 'Done', '중단': 'Aborted',
    'PLC 끊김': 'PLC Lost', 'PLC 멈춤': 'PLC Stalled'
  };
  T.oldValue = function (v) {
    return (typeof v === 'string' && Object.prototype.hasOwnProperty.call(OLD, v)) ? OLD[v] : v;
  };

  /** ★ v0.5.1(A3): 공정 결과 · 진행 문구의 수준 — '' = 정상(회색) · 'warn' = 중단 등(노랑) · 'stop' = 실패(빨강).
   *  문장은 서버가 정한 한글 그대로다(process.end_result) — 색만 고른다 */
  T.resultLevel = function (r) {
    r = String(r || '');
    if (/^정상 종료/.test(r)) return /안전 정지/.test(r) ? 'warn' : '';
    if (/안전 정지|PLC 재시작|레시피 표 오류|PLC 중단|실패|거절|못 보냄|오류/.test(r)) return 'stop';
    if (/중단|사이클 후 정지|확인 안 됨|알 수 없음/.test(r)) return 'warn';
    return '';
  };

  /** ★ v0.5.2(A1): 글자 폭 어림(px, 굵게 기준 — 넉넉히). 상태 점 목록에서 긴 이름이 두 열을 쓸지 정한다 */
  T.textPx = function (s, px) {
    var w = 0;
    String(s || '').split('').forEach(function (ch) {
      w += ' .'.indexOf(ch) >= 0 ? 0.3 : '()'.indexOf(ch) >= 0 ? 0.36 : ch === '-' ? 0.38
        : '/ftjr'.indexOf(ch) >= 0 ? 0.4 : 'ilI'.indexOf(ch) >= 0 ? 0.3 : 'mwMW'.indexOf(ch) >= 0 ? 0.9
          : /[0-9]/.test(ch) ? 0.6 : /[A-Z]/.test(ch) ? 0.68 : /[a-z]/.test(ch) ? 0.56 : 1.0;
    });
    return w * (px || 12) * 1.05;
  };
  /** ★ v0.5.3: 상태 목록 순서 — 알람(stop) → 경고(warn) → 그 밖, 같은 수준 안에서는 원래 순서 */
  T.stiRank = function (lvl) { return lvl === 'stop' ? 0 : lvl === 'warn' ? 1 : 2; };
  T.stiOrder = function (items) {
    return (items || []).map(function (it, i) { return [it, i]; })
      .sort(function (a, b) { return (T.stiRank(a[0].lvl) - T.stiRank(b[0].lvl)) || (a[1] - b[1]); })
      .map(function (x) { return x[0]; });
  };
  /** ★ v0.5.4: 알람이나 경고가 하나라도 있으면 문제 항목만(알람 먼저) + 정상 항목은 '+N OK' 한 칸으로 묶는다.
   *  { items: 그릴 항목, ok: 묶은 정상 항목(없으면 []) } — 문제가 없으면 전체를 순서대로 */
  T.stiCompact = function (items) {
    var ord = T.stiOrder(items);
    var probs = ord.filter(function (i) { return T.stiRank(i.lvl) < 2; });
    if (!probs.length) return { items: ord, ok: [] };
    return { items: probs, ok: ord.slice(probs.length) };
  };
  /** ★ v0.6.1(B2): 목록을 rows 줄 × cols 열 안에 맞춘다(stiCompact 결과를 받아). 넘치면 앞에서부터(알람 먼저) 들어가는 만큼만
   *  두고 마지막 칸은 '+N more' — { items, ok, more: 숨은 항목(정상 묶음 포함) }. 배치는 순서 그대로(dense 아님 — B3):
   *  두 열짜리가 줄 끝에 안 들어가면 다음 줄에서 시작한다(style.css 의 .stl.ordered 와 같은 규칙) */
  T.stiFit = function (c, rows, cols) {
    rows = Math.max(1, rows | 0);
    cols = Math.max(1, cols | 0);
    function used(spans) {
      if (!spans.length) return 0;
      var r = 1, col = 0;
      spans.forEach(function (s) {
        if (col + s > cols) { r++; col = 0; }
        col += s;
      });
      return r;
    }
    var spans = c.items.map(function (i) { return Math.min(cols, T.stiSpan(i.name)); });
    if (used(spans.concat(c.ok.length ? [1] : [])) <= rows) return { items: c.items, ok: c.ok, more: [] };
    var k = c.items.length;
    while (k > 0 && used(spans.slice(0, k).concat([1])) > rows) k--;
    return { items: c.items.slice(0, k), ok: [], more: c.items.slice(k).concat(c.ok) };
  };
  /** 항목 중 가장 나쁜 수준('stop' · 'warn' · '') */
  T.stiWorst = function (items) {
    var r = Math.min.apply(null, [2].concat((items || []).map(function (i) { return T.stiRank(i.lvl); })));
    return r === 0 ? 'stop' : r === 1 ? 'warn' : '';
  };
  T.STI_COL = 96;          // 상태 점 목록 한 열의 최소 폭(px) — style.css 의 minmax(96px, 1fr) 와 같게
  T.stiSpan = function (name) {           // 점(7) + 사이(5) + 이름이 한 열을 넘으면 두 열
    return T.textPx(name, 12) + 12 > T.STI_COL ? 2 : 1;
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = T;
  else root.T = T;
})(typeof window !== 'undefined' ? window : this);
