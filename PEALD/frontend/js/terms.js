/* ============================================================
 * terms.js — 화면 공통 용어 사전 (v0.5.0) · 설명창 사전 (v0.8.6).
 *
 * 원칙(v0.8.6)
 *  1. 라벨은 영어 그대로 — 탭 · 단추 · 칸 제목 · 표 머리 · 상태 · 인터락 · 입력 · 알람 이름 · 장비 부품 이름은 이 사전(D)과
 *     서버 device.py 의 이름을 그대로 쓴다. 화면 배치 · 칸 폭은 바꾸지 않는다.
 *  2. 마우스를 올리면(키보드 포커스도) 한국어 설명창 — 첫 줄 한국어 이름, 둘째 줄 무엇인지 한 줄. 설명은 TIP(같은 키)에서,
 *     장비 구조(밸브 · 입력 · 인터락 · 알람 · 히터/MFC 기본 이름)는 device.py 의 ko · desc 를 서버가 보내 준다.
 *  3. 상태가 바뀌는 곳은 설명창에 지금 상태를 붙인다(인터락 · 입력 램프 · 시작 조건 · 막힌 단추의 이유 · 알람 원인/조치).
 *  4. 문장은 한국어로 짧게 — '~함 · ~됨 · ~없음 · ~필요 · ~중' 으로 끝낸다('~습니다 · ~세요 · ~합니다' 를 쓰지 않는다).
 *     같은 뜻을 두 번 말하지 않고, 문장 안의 장비 · 신호 이름은 화면 라벨과 같은 영어 이름을 쓴다.
 * ★ index.html 의 고정 라벨은 data-t="키" 로 이 사전에서 채우고(T.apply), 설명창은 같은 키로 TIP 을 찾는다(core.js).
 *   데이터 로그 머리 · 트렌드 내보내기 열 이름도 같은 용어다(바꾸지 않는다).
 * ============================================================ */
(function (root) {
  'use strict';

  var D = {
    // 탭
    'tab.main': 'Operation', 'tab.pid': 'P&ID', 'tab.recipe': 'Recipe', 'tab.trend': 'Trend', 'tab.datalog': 'Data Log',
    'tab.alarm': 'Alarm/Log', 'tab.setup': 'Settings',
    // 칸
    'panel.pid': 'P&ID', 'panel.process': 'Process', 'panel.start': 'Start Conditions', 'panel.ann': 'Interlock · Input',
    'panel.valves': 'Valves', 'panel.events': 'Recent Events', 'panel.mfcRf': 'MFC · RF · PCV', 'panel.mfcO3': 'MFC · O3',
    'panel.heater': 'Heater (℃)', 'panel.mfc': 'MFC', 'panel.rfpcv': 'RF / PCV', 'panel.o3': 'O3',
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
    'eq.mainPump': 'Main Pump',
    // 상단 상태 띠 · 램프판 묶음
    'strip.dev': 'Device State', 'strip.proc': 'Process', 'strip.pr': 'Chamber Pressure', 'strip.exh': 'Exhaust · Vent',
    'strip.alm': 'Alarm',
    'ann.safety': 'Safety', 'ann.permit': 'Permit', 'ann.state': 'State', 'ann.rf': 'RF', 'ann.o3': 'O3',
    // 값
    'v.setpoint': 'Setpoint', 'v.actual': 'Actual', 'v.output': 'Output', 'v.power': 'Power', 'v.status': 'Status',
    'v.fullScale': 'Full Scale', 'v.gas': 'Gas', 'v.block': 'Block', 'v.step': 'Step', 'v.cycle': 'Cycle',
    'v.group': 'Group', 'v.elapsed': 'Elapsed', 'v.remaining': 'Remaining', 'v.planned': 'Planned',
    'v.plannedEnd': 'Planned End', 'v.offDelay': 'O3 Off Delay', 'v.generator': 'Generator', 'v.request': 'Request',
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
    's.paused': 'Paused', 's.pending': 'Pending', 's.commLost': 'Comm Lost', 's.ctrlAlarm': 'Ctrl Alarm',
    's.tableOk': 'Table OK', 's.tableNg': 'Table NG', 's.noAlarm': 'No Alarm', 's.critical': 'Critical',
    's.warning': 'Warning', 's.flow': 'Flow', 's.clickToOperate': 'Click to operate', 's.idlePipe': 'Idle', 's.exhaust': 'Exhaust', 's.heated': 'Heated',
    's.serverLost': 'Server Lost', 's.plcLost': 'PLC Lost', 's.plcStalled': 'PLC Stalled', 's.noAddress': 'No PLC Address',
    's.feedbackOk': 'Feedback OK', 's.noFeedback': 'No Feedback', 's.permitWait': 'Permit Wait', 's.match': 'Match', 's.mismatch': 'Mismatch',
    's.unconfirmed': 'Unconfirmed', 's.thisPc': 'This PC', 's.viewOnly': 'View Only', 's.recording': 'Recording',
    's.locked': 'Locked', 's.unlocked': 'Unlocked', 's.notSaved': 'Not Saved',
    's.auto': 'Auto', 's.ready': 'Ready', 's.notReady': 'Not ready', 's.yes': 'Yes', 's.no': 'No', 's.none': 'None',
    's.alarm': 'Alarm', 's.okOn': 'OK · On', 's.offNormal': 'Off · Normal', 's.notReadyLamp': 'Not ready',
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

  /** v0.8.6: 설명창 사전 — D 와 같은 키, [한국어 이름, 설명 한 줄]. 영어 약어 · 단위만 있는 값에는 두지 않는다 */
  var TIP = {
    'tab.main': ['운전', '상태 띠 · 시작 조건 · 램프판 · 밸브 · 히터 · MFC'], 'tab.pid': ['배관도', '장비 배관과 밸브 상태 — 누르면 조작 창'],
    'tab.recipe': ['레시피', '레시피 편집 · 검증 · PLC 올리기'], 'tab.trend': ['트렌드', '압력 · 온도 · 유량 그래프(실시간 · 이력)'],
    'tab.datalog': ['데이터 로그', '공정마다 남긴 기록 보기'], 'tab.alarm': ['알람 · 로그', '알람 · 이력 · 프로그램 알림 · 로그'],
    'tab.setup': ['설정', '설정 편집 · PLC 파라미터 · 정비'],
    'panel.pid': ['배관도', '장비 배관과 밸브 상태'], 'panel.process': ['공정', '진행 중 공정의 위치 · 시간'],
    'panel.start': ['시작 조건', '공정을 시작하려면 모두 통과 필요'], 'panel.ann': ['인터락 · 입력', 'PLC 인터락과 입력 신호 램프'],
    'panel.valves': ['밸브', '공정 밸브 · 벤트 · 배기 상태 — 누르면 조작 창'], 'panel.events': ['최근 사건', '이벤트 로그 최근 줄'],
    'panel.mfcRf': ['유량 · RF · 압력 제어', 'MFC 유량 · RF 전력 · PCV'], 'panel.mfcO3': ['유량 · O3', 'MFC 유량 · O3 농도'],
    'panel.heater': ['히터', '채널별 실제 · 설정 온도와 전원'], 'panel.mfc': ['질량 유량 제어기', 'MFC 설정 · 실제 유량'],
    'panel.rfpcv': ['RF 전원 · 압력 제어 밸브', 'RF 전력 · PCV 개도'], 'panel.o3': ['오존', 'O3 농도 · O3 라인'],
    'panel.savedRecipes': ['저장된 레시피', '이 PC 의 레시피 목록'], 'panel.edit': ['편집', '블록 · 스텝 편집'],
    'panel.check': ['검사', '서버가 판정한 검증 결과'], 'panel.datalog': ['공정 데이터 로그', '공정마다 남긴 기록 파일'],
    'panel.table': ['표', '기록 값 표'], 'panel.recipeCopy': ['레시피 사본', '그 공정 때의 레시피'],
    'panel.alarms': ['현재 알람', '지금 서 있는 알람'], 'panel.notices': ['프로그램 알림', 'PLC 알람이 아니라 이 프로그램이 판단한 것'],
    'panel.alarmHist': ['알람 이력', '발생 · 해제 기록'], 'panel.log': ['프로그램 로그', '명령 · 사건 기록'],
    'panel.manual': ['수동 조작', '밸브 · MFC · 히터 수동 조작'],
    'chart.pressure': ['압력', '챔버 압력 그래프(Torr)'], 'chart.temp': ['온도', '히터 온도 그래프(℃)'],
    'chart.mfc': ['MFC 유량', 'MFC 유량 그래프(sccm)'], 'chart.heater': ['히터', '히터 온도 그래프(℃)'],
    'chart.mfcShort': ['MFC 유량', 'MFC 유량 그래프(sccm)'], 'chart.device': ['장비', '장비 상태 줄'],
    'eq.chamber': ['공정 챔버', '기판이 들어가는 챔버'], 'eq.reactor': ['파우더 반응기', '파우더가 들어가는 반응기'],
    'eq.can1': ['캐니스터 1', '전구체 원료 용기 1'], 'eq.can2': ['캐니스터 2', '전구체 원료 용기 2'], 'eq.can3': ['캐니스터 3', '전구체 원료 용기 3'],
    'eq.h2o': ['H2O 캐니스터', '반응물 H2O 용기'], 'eq.pline': ['전구체 라인', '캐니스터 → 챔버 전구체 배관'],
    'eq.manifold': ['반응물 매니폴드', '반응물 · 퍼지 가스가 모이는 배관'], 'eq.trap': ['트랩', '배기 앞 부산물 트랩'],
    'eq.dryPump': ['드라이 펌프', '챔버 배기 펌프'], 'eq.exhPump': ['배기 펌프', '배기 펌프'], 'eq.bypassPump': ['바이패스 펌프', 'O3 바이패스 라인 펌프'],
    'eq.o3gen': ['O3 발생기', 'O2 로 O3 를 만드는 장치'], 'eq.vent': ['벤트', 'N2 로 챔버를 대기압까지 채움'],
    'eq.rf': ['RF 전원 · 매처', '플라즈마용 RF 전원과 정합기'], 'eq.electrode': ['RF 전극', '플라즈마를 만드는 전극'],
    'eq.stage': ['스테이지', '기판 받침(가열)'], 'eq.bed': ['파우더 베드', '파우더가 놓이는 자리'], 'eq.lid': ['리드', '챔버 뚜껑'],
    'eq.hingeLid': ['힌지 리드', '여닫는 챔버 뚜껑'], 'eq.assist': ['어시스트 N2', '캐니스터 어시스트 N2'], 'eq.o2': ['O2 공급', 'O3 발생기로 가는 O2'],
    'eq.exhIso': ['배기 격리', '챔버와 펌프 사이 격리(IV-E)'], 'eq.plcAuto': ['PLC 자동', 'PLC 가 스스로 여닫음'],
    'eq.exhaust': ['배기', '펌프 쪽 배관'], 'eq.pcv': ['인라인 PCV', '공정 압력을 맞추는 제어 밸브'], 'eq.chamberTemp': ['챔버 온도', '챔버 히터 온도'],
    'eq.precursor': ['전구체', '첫째 반응 원료'], 'eq.reactant': ['반응물', '둘째 반응 원료'], 'eq.reactantO3': ['반응물(O3)', 'O3 반응물'],
    'eq.pump': ['펌프', '배기 펌프'], 'eq.mainPump': ['메인 펌프', '반응기 배기 펌프'],
    'strip.dev': ['장비 상태', 'PLC 장비 상태(Idle · Running · Safe Stop …)'], 'strip.proc': ['공정', '공정 진행 · 마지막 결과'],
    'strip.pr': ['챔버 압력', 'CVG 압력(Torr)'], 'strip.exh': ['배기 · 벤트', '펌프 · IV-E · VV 상태'], 'strip.alm': ['알람', '서 있는 알람 수'],
    'ann.safety': ['안전', '비상정지 · 공압 · 냉각수 등 안전 입력'], 'ann.permit': ['허가', 'PLC 인터락 허가'], 'ann.state': ['상태', '펌프 · IV-E · 대기압 상태 입력'],
    'ann.rf': ['RF 묶음', 'RF 허가 · 준비 · 알람'], 'ann.o3': ['오존 묶음', 'O3 허가 · 발생기 · 바이패스'],
    'v.setpoint': ['설정값', '목표 값'], 'v.actual': ['실제값', '지금 읽은 값'], 'v.output': ['출력', '온도조절기 출력 %'], 'v.power': ['전원', '히터 전원 ON/OFF · 출력'],
    'v.status': ['상태', '지금 상태'], 'v.fullScale': ['풀스케일', 'MFC 최대 유량'], 'v.gas': ['가스', 'MFC 가스 이름'], 'v.block': ['블록', '레시피 블록 번호'],
    'v.step': ['스텝', '블록 안 스텝 번호'], 'v.cycle': ['사이클', '블록 반복 회차'], 'v.group': ['그룹', '그룹 반복 회차'], 'v.elapsed': ['경과', '시작부터 지난 시간'],
    'v.remaining': ['남은 시간', '끝까지 남은 예상 시간'], 'v.planned': ['예정', '예상 전체 시간'], 'v.plannedEnd': ['끝 예정', '예상 끝 시각'],
    'v.offDelay': ['O3 끄기 지연', 'O3 Generator 끈 뒤 바이패스 라인 닫기까지 남은 시간'], 'v.generator': ['발생기', 'O3 Generator 출력'], 'v.request': ['요청', 'PLC 에 반영된 수동 요청'],
    'v.recipe': ['레시피', '레시피 이름'], 'v.channel': ['채널', '히터 채널'], 'v.target': ['목표', '목표 값'], 'v.position': ['위치', 'PCV 개도'],
    'v.forward': ['순방향', 'RF 순방향 전력'], 'v.reflected': ['반사', 'RF 반사 전력'], 'v.number': ['번호', '순번'], 'v.estimate': ['예상', '예상 시간'],
    'v.severity': ['등급', '중대(장비 정지) · 경고'], 'v.raised': ['발생', '발생 시각'], 'v.cleared': ['해제', '해제 시각'], 'v.code': ['코드', '알람 코드'],
    'v.message': ['내용', '알림 내용'], 'v.time': ['시각', '기록 시각'], 'v.name': ['이름', '이름'], 'v.memo': ['메모', '메모'], 'v.repeat': ['반복', '블록 반복 횟수'],
    'v.ms': ['시간', '스텝 시간(ms)'], 'v.stepName': ['스텝 이름', '스텝 이름'], 'v.pauseOk': ['일시정지 허용', '이 스텝 끝에서 일시정지 가능'],
    'v.fromBlock': ['시작 블록', '그룹의 첫 블록'], 'v.toBlock': ['끝 블록', '그룹의 마지막 블록'], 'v.access': ['접속', '이 PC · 원격 보기'],
    'v.station': ['국번', '온도조절기 통신 국번'], 'v.enabled': ['사용', '이 채널 사용 여부'], 'v.limit': ['과온 한계', '넘으면 PLC 가 전원을 끔(℃)'],
    'v.defaultSv': ['기본 설정값', '처음 켤 때 설정 온도(℃)'], 'v.confirmed': ['확인됨', '환산 값을 현장에서 확인함'], 'v.item': ['항목', '설정 항목'],
    'v.before': ['이전', '바꾸기 전 값'], 'v.after': ['이후', '바꾼 뒤 값'], 'v.raw': ['PRM 원시값', 'PC 가 PLC 에 쓸 원시값'], 'v.written': ['쓴 값', 'PLC 에 쓴 원시값'],
    'v.readback': ['되읽은 값', 'PLC 에서 다시 읽은 값'], 'v.match': ['일치', '쓴 값과 되읽은 값 비교'],
    's.open': ['열림', '밸브 출력 켜짐'], 's.closed': ['닫힘', '밸브 출력 꺼짐'], 's.unknown': ['모름', '연결이 끊겨 상태를 모름'], 's.on': ['켜짐', '출력 켜짐'],
    's.off': ['꺼짐', '출력 꺼짐'], 's.normal': ['정상', '조건 맞음'], 's.limitUnset': ['한계 없음', '과온 한계 미설정 — 전원 켜기 막힘'],
    's.idle': ['대기', '공정 아님'], 's.running': ['공정 중', '레시피 실행 중'], 's.paused': ['일시정지', '스텝 끝에서 멈춤'],
    's.pending': ['대기 중 요청', '요청은 있는데 출력이 1 s 넘게 안 나감'], 's.commLost': ['통신 끊김', '온도조절기 통신 없음'],
    's.ctrlAlarm': ['조절기 알람', '온도조절기 알람'], 's.tableOk': ['표 통과', 'PLC 레시피 표 검사 통과'], 's.tableNg': ['표 불통과', 'PLC 레시피 표 검사 실패'],
    's.noAlarm': ['알람 없음', '서 있는 알람 없음'], 's.critical': ['중대', '장비를 세우는 알람'], 's.warning': ['경고', '표시 · 기록만'],
    's.flow': ['흐름', '가스가 흐르는 배관'], 's.clickToOperate': ['눌러서 조작', '밸브 · 상자를 누르면 조작 창'], 's.idlePipe': ['대기', '흐름 없음'],
    's.exhaust': ['배기', '배기 중인 배관'], 's.heated': ['가열', '가열 중인 배관'], 's.serverLost': ['서버 끊김', '화면과 프로그램 연결 끊김'],
    's.plcLost': ['PLC 끊김', 'PLC 와 통신 끊김'], 's.plcStalled': ['PLC 멈춤', 'PLC 하트비트 멈춤(STOP 등)'], 's.noAddress': ['PLC 주소 없음', '설정에 PLC 주소 없음'],
    's.feedbackOk': ['피드백 정상', '운전 피드백 있음'], 's.noFeedback': ['피드백 없음', '출력은 켜졌는데 운전 피드백 없음'],
    's.permitWait': ['허가 대기', 'O3 Permit 을 기다리는 중'], 's.match': ['일치', '쓴 값 = 되읽은 값'], 's.mismatch': ['불일치', '쓴 값 ≠ 되읽은 값'],
    's.unconfirmed': ['미확정', '환산 값을 현장에서 아직 확인하지 않음'], 's.thisPc': ['이 PC', '조작 가능'], 's.viewOnly': ['보기 전용', '원격 접속 — 조작 불가'],
    's.recording': ['기록 중', '공정 데이터 로그 기록 중'], 's.locked': ['잠김', '관리자 잠금'], 's.unlocked': ['풀림', '관리자 잠금 해제됨'], 's.notSaved': ['저장 안 됨', '바꾼 내용 저장 필요'],
    's.auto': ['자동', 'PLC 가 스스로 다룸'], 's.ready': ['준비됨', '조건 맞음'], 's.notReady': ['준비 안 됨', '조건 안 맞음'], 's.yes': ['예', ''], 's.no': ['아니오', ''], 's.none': ['없음', ''],
    's.alarm': ['알람', '이상'], 's.okOn': ['정상 · 켜짐', '정상 입력 켜짐'], 's.offNormal': ['꺼짐 · 정상', '이상 입력 꺼짐'], 's.notReadyLamp': ['준비 안 됨', '허가 조건 안 맞음'],
    'b.start': ['공정 시작', '레시피를 PLC 에 올리고 베이스 압력을 기다린 뒤 시작'], 'b.pause': ['일시정지', '허용 스텝 끝에서 멈춤'],
    'b.resume': ['재개', '일시정지한 공정을 이어서'], 'b.stopAfterCycle': ['사이클 후 정지', '지금 사이클을 마치고 정지'],
    'b.abort': ['즉시 중단', '그 자리에서 끊고 공정 밸브를 모두 닫음'], 'b.cancelWait': ['대기 취소', '베이스 압력 기다림을 멈춤'],
    'b.manual': ['수동 조작', '밸브 · MFC · 히터 수동 조작 창'], 'b.pumpStart': ['펌핑 시작', '펌프 운전 후 IV-E 를 열어 배기'],
    'b.pumpStop': ['펌핑 정지', 'IV-E 를 닫고 펌프 정지'], 'b.vent': ['벤트', 'IV-E 를 닫고 N2 로 대기압까지'], 'b.closeAll': ['전체 밸브 닫기', '수동 밸브 · 배기 · 벤트 요청을 모두 닫음'],
    'b.ack': ['알람 확인', '부저를 끄고 확인 표시'], 'b.reset': ['알람 리셋', '원인이 없어진 알람을 지움'], 'b.exit': ['종료', '프로그램 종료'],
    'b.apply': ['적용', '입력한 값을 PLC 에 보냄'], 'b.open': ['열기', '밸브 열기 요청'], 'b.close': ['닫기', '밸브 닫기 요청'], 'b.unlock': ['잠금 해제', '수동 조작 · 설정 편집 잠금 풀기'],
    'b.lock': ['잠금', '다시 잠금'], 'b.cancel': ['취소', '하지 않고 닫음'], 'b.ok': ['확인', '이대로 진행'], 'b.run': ['실행', '실행'], 'b.on': ['켜기', '켜기 요청'], 'b.off': ['끄기', '끄기 요청'],
    'b.new': ['새로', '빈 레시피 만들기'], 'b.save': ['저장', '이 이름으로 저장'], 'b.saveAs': ['다른 이름 저장', '새 이름으로 저장'], 'b.rename': ['이름 바꾸기', '레시피 이름 바꾸기'],
    'b.delete': ['삭제', '레시피 파일 삭제'], 'b.upload': ['PLC 에 올리기', '레시피 표를 PLC 에 씀(시작은 안 함)'], 'b.startRecipe': ['이 레시피로 시작', '운전 탭에서 시작 조건 확인'],
    'b.live': ['실시간', '실시간 그래프'], 'b.history': ['이력', '저장된 트렌드 이력 보기'], 'b.load': ['불러오기', '고른 구간 불러오기'], 'b.undo': ['되돌리기', '확대 전으로'],
    'b.exportCsv': ['CSV 내보내기', '이 구간을 CSV 로 저장'], 'b.openFolder': ['폴더 열기', '파일이 있는 폴더 열기'], 'b.refresh': ['새로 고침', '목록 다시 읽기'],
    'b.chart': ['그래프', '그래프로 보기'], 'b.table': ['표', '표로 보기'], 'b.recipe': ['레시피', '그때의 레시피 사본'], 'b.prev': ['이전 200', '앞 200 줄'], 'b.next': ['다음 200', '뒤 200 줄'],
    'b.addStep': ['스텝 추가', '블록 끝에 스텝 추가'], 'b.addGroup': ['그룹 추가', '블록 묶음 반복 추가'], 'b.deleteBlock': ['블록 삭제', '이 블록 삭제'],
    'b.revert': ['되돌리기', '바꾼 설정 되돌리기'], 'b.review': ['검토 · 저장', '바뀌는 항목 확인 후 저장'], 'b.changePin': ['PIN 바꾸기', '관리자 PIN 바꾸기'],
    'b.setPin': ['PIN 지정', '관리자 PIN 처음 지정'], 'b.backupFolder': ['백업 폴더 열기', '설정 백업 폴더'], 'b.ackBuzzer': ['확인(부저 끔)', '알람 확인 · 부저 끔'],
    'b.mfcApply': ['MFC 적용', 'MFC 설정 유량을 PLC 에 보냄'], 'b.heaterApply': ['히터 적용', '히터 설정 · 전원을 PLC 에 보냄'],
    'b.o3On': ['O3 라인 켜기', 'Bypass Pump · IV-B → O3 Permit 을 기다린 뒤 O3 Generator'], 'b.o3Off': ['O3 라인 끄기', 'O3 Generator 먼저 끄고 지연 뒤 바이패스 라인 닫음'],
    'b.rfOn': ['RF 켜기', '입력한 전력으로 RF 켜기(시험)'], 'b.rfOff': ['RF 끄기', 'RF 끄기'], 'b.pumping': ['펌핑', '펌핑 시작 · 정지'],
    'h.simulator': ['시뮬레이터', '실장비 대신 내장 PLC 시뮬레이터 — 누르면 조작판'], 'h.control': ['공정 제어', '공정 제어 프로그램']
  };

  function T(key, fallback) {
    return Object.prototype.hasOwnProperty.call(D, key) ? D[key] : (fallback == null ? key : fallback);
  }
  T.dict = D;
  T.tips = TIP;
  /** v0.8.6: 설명창 [한국어 이름, 설명] — 없으면 null */
  T.tip = function (key) {
    return Object.prototype.hasOwnProperty.call(TIP, key) ? TIP[key] : null;
  };
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
    // ★ v0.7.2: 붙인 꼬리(' · PC 끊긴/꺼진 동안 끝남…' · ' · 끝난 뒤 안전 정지: …')는 떼고 본다 — 서버 result_level 과 같게.
    //   예전에는 꼬리의 '안전 정지' 때문에 '중단 (운전자 중단) · 끝난 뒤 안전 정지: …' 가 빨강이었다
    r = String(r || '').replace(/ · PC (끊긴|꺼진) 동안 끝남.*$/, '').replace(/ · 끝난 뒤 안전 정지: .*$/, '');
    if (/^정상 종료/.test(r)) return /안전 정지/.test(r) ? 'warn' : '';
    if (/안전 정지|PLC 재시작|레시피 표 오류|PLC 중단|실패|거절|못 보냄|오류/.test(r)) return 'stop';
    if (/중단|사이클 후 정지|확인 안 됨|알 수 없음/.test(r)) return 'warn';
    return '';
  };

  /** ★ v0.5.2(A1): 글자 폭 어림(px, 굵게 기준 — 넉넉히). v0.8.0: 배관도 검사기(pidcheck.js) · 램프 폭 시험이 쓴다 */
  T.textPx = function (s, px) {
    var w = 0;
    String(s || '').split('').forEach(function (ch) {
      w += ' .'.indexOf(ch) >= 0 ? 0.3 : '()'.indexOf(ch) >= 0 ? 0.36 : ch === '-' ? 0.38
        : '/ftjr'.indexOf(ch) >= 0 ? 0.4 : 'ilI'.indexOf(ch) >= 0 ? 0.3 : 'mwMW'.indexOf(ch) >= 0 ? 0.9
          : /[0-9]/.test(ch) ? 0.6 : /[A-Z]/.test(ch) ? 0.68 : /[a-z]/.test(ch) ? 0.56 : 1.0;
    });
    return w * (px || 12) * 1.05;
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = T;
  else root.T = T;
})(typeof window !== 'undefined' ? window : this);
