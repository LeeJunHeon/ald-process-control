"""
device.py — Powder ALD 장비 정체성과 장비 구조 정의.

★ 여기 있는 값은 설정 파일로 바꿀 수 없다.
  설정 파일을 잘못 복사해도 장비가 바뀌어 보이면 안 되기 때문이다 —
  화면에 "Powder ALD"라고 떠 있는데 실제로는 다른 장비의 밸브를 여는 사고를 막는다.
  설정으로 바꿀 수 있는 것은 창 위치(left/right)와 포트뿐이다.

★ 장비 구조(밸브·보조 출력·입력·인터락·알람 문구·MFC·히터 기본 이름)도 여기 한 곳에 둔다.
  주소와 비트 번호 자체는 addresses.py(두 장비 공통 약속)에 있고,
  이 파일은 "이 장비에 실제로 있는 것"만 골라 이름을 붙인다.
"""

# ===================== 장비 정체성 (고정) =====================
KEY = "powderald"
NAME = "Powder ALD"                 # 헤더 배지 · 확인 창 제목
TITLE = "Powder ALD Process Control"      # 창 제목
THEME = "light"                     # v0.5.1: 두 장비 모두 밝은 테마가 기본(설정 window.theme 으로 dark)
ACCENT = "#0e7490"                  # 청록 — 헤더 띠·배지·탭 밑줄·확인 창에만 쓴다
ICON = "powderald.ico"

DEFAULT_PORT = 8101                 # 웹(화면) 포트
DEFAULT_SIM_PORT = 15101            # 내장 시뮬레이터 포트
DEFAULT_SIDE = "left"               # 기본 창 위치

# 장비 ID — PLC 가 D00019 에 매 스캔 쓰는 값('PW'). PC 가 연결 직후 이것부터 확인한다.
DEVICE_ID = 0x5057

MUTEX_NAME = "VANAM.POWDERALD.Control"
APP_USER_MODEL_ID = "VANAM.POWDERALD.Control"
EXE_NAME = "POWDERALD_Control"
LOG_PREFIX = "POWDERALD"

# ===================== 공정 밸브 (D00010 / D01004 / 레시피 밸브 워드) =====================
VALVES = [
    {"bit": 0, "tag": "PV-1",  "name": "Canister 1 → Precursor Line",     "pulse": True},
    {"bit": 1, "tag": "PV-2",  "name": "Canister 2 → Precursor Line",     "pulse": True},
    {"bit": 2, "tag": "PV-3",  "name": "Canister 3 → Precursor Line",     "pulse": True},
    {"bit": 3, "tag": "PV-A1", "name": "Assist N2 → Canister 2 Inlet", "pulse": False},
    {"bit": 4, "tag": "PV-A2", "name": "Assist N2 → Canister 3 Inlet", "pulse": False},
    {"bit": 5, "tag": "PV-R",  "name": "O3 → Chamber",                  "pulse": True},
    # ★ PV-B 는 PLC 가 자동으로 연다(발생기 운전 중 PV-R 이 닫혀 있으면 열림).
    #   수동·레시피로 쓰지 않는다 — 수동 마스크(b0~b5)에 넣지 않은 이유다.
    {"bit": 9, "tag": "PV-B",  "name": "O3 Bypass → Bypass Pump (PLC Auto)",
     "pulse": False, "auto": True},
]
MANUAL_VALVE_MASK = 0x003F          # 수동 요청이 반영되는 비트 (b0~b5)

PRECURSOR_VALVE_BITS = (0, 1, 2)
REACTANT_VALVE_BITS = (5,)

# ===================== 보조 출력 (D00014 / D01008) =====================
AUX = [
    {"bit": 0,  "tag": "VV",     "name": "Chamber Vent (N2)"},
    {"bit": 1,  "tag": "IV-E",   "name": "Exhaust Isolation"},
    {"bit": 2,  "tag": "PMP-N2", "name": "Pump N2 Purge"},
    {"bit": 3,  "tag": "PMP",    "name": "Main Pump Run"},
    {"bit": 4,  "tag": "LMP-R",  "name": "Tower Lamp Red"},
    {"bit": 5,  "tag": "LMP-Y",  "name": "Tower Lamp Yellow"},
    {"bit": 6,  "tag": "LMP-G",  "name": "Tower Lamp Green"},
    {"bit": 7,  "tag": "BUZ",    "name": "Buzzer"},
    {"bit": 9,  "tag": "O3GEN",  "name": "O3 Generator"},
    {"bit": 10, "tag": "IV-B",   "name": "Bypass Isolation"},
    {"bit": 11, "tag": "BPMP",   "name": "Bypass Pump"},
]
# 명령 12(수동 적용)로 반영되는 보조 출력 비트 (b9~b11).
# VV·IV-E·펌프는 명령 8~11(펌핑·벤트·전체 닫기)이 다루므로 여기서 제외한다.
AUX_CMD_MASK = 0x0E00

# ===================== 입력 (D00008 / D00009) =====================
# short: 화면 상태 목록에 쓰는 이름(v0.6.1 B4) — 허가 · 정상 조건 입력은 상태가 들어가지 않은 이름, 점 색이 상태를 말한다
INPUTS0 = [
    {"bit": 0,  "tag": "EMO",     "name": "EMO OK",   "ok_when": True, "short": "EMO", "lamp": "EMO",
     "desc": "비상정지 회로 정상(버튼이 눌리지 않음) — 꺼지면 비상정지"},
    {"bit": 1,  "tag": "RST-SW",  "name": "Panel Reset SW", "ok_when": True, "momentary": True,
     "desc": "패널 리셋 스위치(누르는 동안만 켜짐) — 램프판에 넣지 않는다"},
    {"bit": 2,  "tag": "AIR",     "name": "Air OK",       "ok_when": True, "short": "Air", "lamp": "Air",
     "desc": "공압(압축 공기) 압력 스위치 정상"},
    {"bit": 3,  "tag": "N2",      "name": "N2 Pressure OK",    "ok_when": True, "short": "N2 Pressure",
     "lamp": "N2 Pressure", "desc": "N2 공급 압력 스위치 정상"},
    {"bit": 4,  "tag": "CW",      "name": "Cooling Water OK",     "ok_when": True, "short": "Cooling Water",
     "lamp": "Cooling Water", "desc": "냉각수 유량 스위치 정상"},
    {"bit": 5,  "tag": "LID",     "name": "Chamber Closed",       "ok_when": True, "short": "Lid", "lamp": "Lid",
     "desc": "챔버 닫힘 스위치"},
    {"bit": 6,  "tag": "ATM",     "name": "Chamber ATM",     "ok_when": None, "lamp": "Chamber ATM",
     "desc": "챔버가 대기압 — 벤트가 끝났다는 표시"},
    {"bit": 7,  "tag": "PMP-RUN", "name": "Pump Running",       "ok_when": None, "lamp": "Pump Running",
     "desc": "펌프가 실제로 돌고 있다는 피드백"},
    {"bit": 8,  "tag": "PMP-ALM", "name": "Pump Alarm",       "ok_when": False, "lamp": "Pump Alarm",
     "desc": "펌프 알람 — 켜지면 이상"},
    {"bit": 9,  "tag": "IVE-O",   "name": "IV-E Open Limit", "ok_when": None, "lamp": "IV-E Open",
     "desc": "IV-E(배기 격리 밸브) 열림 리미트 스위치"},
    {"bit": 10, "tag": "IVE-C",   "name": "IV-E Closed Limit", "ok_when": None, "lamp": "IV-E Closed",
     "desc": "IV-E(배기 격리 밸브) 닫힘 리미트 스위치"},
    {"bit": 11, "tag": "LEAK",    "name": "Gas Leak",       "ok_when": False, "lamp": "Gas Leak",
     "desc": "가스 누출 감지기 — 켜지면 이상"},
    {"bit": 12, "tag": "SCRUB",   "name": "Scrubber OK", "ok_when": True, "short": "Scrubber", "unused": True,
     "desc": "스크러버 정상 입력 — 이 장비는 쓰지 않는다(램프판에 넣지 않는다)"},
    {"bit": 13, "tag": "OT",      "name": "Heater Overtemp Trip",   "ok_when": False, "lamp": "Heater OT",
     "desc": "히터 과온 트립(하드웨어 온도 스위치) — 켜지면 이상"},
]
INPUTS1 = [
    {"bit": 2, "tag": "O3-RUN",  "name": "O3 Generator Running",   "ok_when": None, "lamp": "O3 Generator",
     "desc": "O3 발생기 운전 피드백"},
    {"bit": 3, "tag": "O3-ALM",  "name": "O3 Alarm",         "ok_when": False, "lamp": "O3 Alarm",
     "desc": "O3 발생기 알람 — 켜지면 이상"},
    {"bit": 4, "tag": "O3-ROOM", "name": "Room O3 Detected",     "ok_when": False, "lamp": "Room O3",
     "desc": "실내 O3 감지기 — 켜지면 출입 금지"},
    {"bit": 5, "tag": "BP-RUN",  "name": "Bypass Pump Running", "ok_when": None, "lamp": "Bypass Pump",
     "desc": "바이패스 펌프 운전 피드백"},
    {"bit": 6, "tag": "BP-ALM",  "name": "Bypass Pump Alarm", "ok_when": False, "lamp": "Bypass Alarm",
     "desc": "바이패스 펌프 알람 — 켜지면 이상"},
]

# ===================== 인터락 (D00004) =====================
INTERLOCKS = [
    {"bit": 0, "tag": "Basic",        "lamp": "Basic", "why": "비상정지·공압·N2·챔버 닫힘·냉각수 정상"},
    {"bit": 1, "tag": "Pump",        "lamp": "Pump", "why": "펌프 운전 중이고 펌프 알람 없음"},
    {"bit": 2, "tag": "Vacuum OK",        "short": "Vacuum", "lamp": "Vacuum",
     "why": "베이스 압력 설정이 0보다 크고, 현재 압력이 그 이하"},
    {"bit": 3, "tag": "Start Permit", "lamp": "Start Permit", "why": "공정 밸브 허가 + 진공 + PC 링크 정상 + 레시피 표 통과 + O3 허가"},
    {"bit": 4, "tag": "Valve Permit", "lamp": "Valve Permit", "why": "기본 + 펌프 + IV-E 열림 + 안전 정지 요구 없음 + 대기압 아님"},
    {"bit": 5, "tag": "Vent Permit",    "lamp": "Vent Permit", "why": "시퀀서 동작 아님(공정 준비 포함) + IV-E 닫힘 입력 + IV-E 출력 꺼짐 + 비상정지 정상"},
    {"bit": 6, "tag": "Precursor/Reactant Conflict", "lamp": "P/R Conflict", "why": "전구체 밸브와 O3 밸브가 함께 요청됨 (이상)",
     "bad": True},
    {"bit": 7, "tag": "Safe Stop Request", "lamp": "Safe Stop Req", "why": "비상정지 또는 중대 알람 또는 PC 끊김 트립 — 공정 중이면 끝난 뒤", "bad": True},
    {"bit": 9, "tag": "O3 Permit",      "lamp": "O3 Permit", "why": "기본 + IV-B 열림 + 바이패스 펌프 운전·알람 없음 + 그 뒤 5 s + "
                                            "O3 알람·실내 O3 없음 + O3 상한 > 0 + 안전 정지 요구 없음"},
]

# ===================== 운전 화면 배치 (v0.8.0) =====================
# ★ 칸 · 자리는 여기 장비 구조로만 정해진다 — 실시간 값으로 늘거나 줄지 않는다(모든 항목이 늘 보인다).
# 램프판(Interlock · Input) — 줄(묶음) · 자리 고정. 항목 'in0:태그' · 'in1:태그' · 'ilk:태그'.
#   RST-SW(순간 스위치) · SCRUB(쓰지 않음)은 넣지 않는다. 그 밖 입력 · 인터락은 정확히 한 번씩
ANNUNCIATOR = [
    {"group": "safety", "items": ["in0:EMO", "in0:AIR", "in0:N2", "in0:CW", "in0:LID", "in0:LEAK", "in0:OT",
                                  "in0:PMP-ALM"]},
    {"group": "permit", "items": ["ilk:Basic", "ilk:Pump", "ilk:Vacuum OK", "ilk:Valve Permit", "ilk:Start Permit",
                                  "ilk:Vent Permit", "ilk:Precursor/Reactant Conflict", "ilk:Safe Stop Request"]},
    {"group": "state", "items": ["in0:PMP-RUN", "in0:IVE-O", "in0:IVE-C", "in0:ATM"]},
    {"group": "o3", "items": ["ilk:O3 Permit", "in1:O3-RUN", "in1:O3-ALM", "in1:O3-ROOM", "in1:BP-RUN", "in1:BP-ALM"]},
]
# 밸브 띠 — 묶음 순서 그대로. valve: 공정 밸브(D00010) · aux: 보조 출력(D00014) · value: 값 타일
VALVE_STRIP = [
    ["valve:PV-1", "valve:PV-2", "valve:PV-3", "valve:PV-A1", "valve:PV-A2"],
    ["value:O3", "valve:PV-R", "valve:PV-B", "aux:IV-B", "value:BPMP"],
    ["aux:VV", "aux:IV-E"],
]
# 시작 조건 자리 10 개 — 자리 이름은 짧게(서버 check 의 label 은 툴팁). keys 의 항목 중 가장 나쁜 것을 그 자리에 그린다.
#   absent: 그 항목이 없을 때의 글(norecipe: 레시피를 안 골랐을 때)
START_SLOTS = [
    {"label": "PLC Link", "keys": ["plc"]},
    {"label": "Device ID", "keys": ["device_id"]},
    {"label": "Device State", "keys": ["state", "leakcheck"]},
    {"label": "Critical Alarm", "keys": ["alarm"]},
    {"label": "Pump", "keys": ["pump"]},
    {"label": "Valve Permit", "keys": ["valve_ok"]},
    {"label": "Base Pressure", "keys": ["base"]},
    {"label": "Recipe", "keys": ["recipe"]},
    {"label": "Heater Stable", "keys": ["heater"], "absent": "시작 조건에서 끔 (Settings)"},
    {"label": "O3 Line", "keys": ["o3", "o3_gen"]},
]


# ===================== 알람 =====================
# crit: 중대(장비를 세우는 알람) / 아니면 경고(표시·기록만)
# cause · check: 알람 · 로그 탭과 알람 창에 보이는 원인 · 확인 안내(v0.7.0 A1)
# ★ v0.7.0(P): 'PC Link Lost'(b6)는 경고 — 공정 중 PC 가 끊겨도 PLC 는 받은 레시피를 끝까지 돌린다(래더 P30 행 5 마스크 hD23F ·
#   행 7 PC 트립 항에 SEQ_RUN b접점). 트립이 공정 뒤에도 남아 있으면 그때 안전 정지(안전 정지 요구 항이 세운다)
ALARMS0 = [
    {"bit": 0, "name": "EMO", "crit": True,
     "cause": "비상정지 버튼이 눌렸거나 비상정지 회로(배선 · 릴레이)가 끊김.",
     "check": "버튼을 돌려 풀고 원인을 없앤 뒤 Alarm Reset. 버튼이 풀려 있는데 계속 뜨면 비상정지 회로 · 입력 배선."},
    {"bit": 1, "name": "Air Pressure Low", "crit": True,
     "cause": "공압이 1 s 넘게 낮음(공급 밸브 · 레귤레이터 · 압력 스위치).",
     "check": "공압 공급 · 레귤레이터 압력 · 압력 스위치 설정과 배선."},
    {"bit": 2, "name": "N2 Supply Low", "crit": True,
     "cause": "N2 공급 압력이 1 s 넘게 낮음(실린더 잔량 · 공급 밸브 · 레귤레이터 · 압력 스위치).",
     "check": "N2 잔량 · 공급 압력 · 압력 스위치."},
    {"bit": 3, "name": "Cooling Water Fault", "crit": True,
     "cause": "냉각수 정상 입력 꺼짐(칠러 정지 · 유량 부족 · 밸브 닫힘 · 유량 스위치).",
     "check": "칠러 운전 · 유량 · 밸브 · 유량 스위치."},
    {"bit": 4, "name": "Chamber Open in Process", "crit": True,
     "cause": "공정 중(블록 준비 포함) 챔버 닫힘 입력이 꺼짐(뚜껑 · 클램프 · 리미트 스위치).",
     "check": "뚜껑 닫힘 · 스위치 위치와 배선 — 공정 중에는 열지 않는다."},
    {"bit": 5, "name": "Pump Alarm / Stop", "crit": True,
     "cause": "펌프 알람 입력, 또는 운전 요청 뒤 10 s 동안 운전 피드백 없음.",
     "check": "펌프 컨트롤러 알람 · 전원 · 피드백 배선(알람이 있는 동안 펌핑 시작은 거절된다)."},
    {"bit": 6, "name": "PC Link Lost", "crit": False,
     "cause": "PC 하트비트가 PC Heartbeat Timeout 넘게 멈춤(프로그램 꺼짐 · PC 절전/재부팅 · 네트워크). 공정 중이면 공정은 끝까지 가고, 트립이 남아 있으면 끝난 뒤 안전 정지.",
     "check": "프로그램 · 네트워크를 살린 뒤 Alarm Reset(PC 가 붙어 있을 때만 풀린다)."},
    {"bit": 7, "name": "Base Pressure Timeout", "crit": False,
     "cause": "펌핑 시작 뒤 Pump-down Timeout 안에 베이스 압력에 닿지 못함(뚜껑 오링 · 피드스루 · 피팅 누설, 벤트 밸브 샘, 펌프 성능, 베이스 압력 설정이 너무 낮음).",
     "check": "오링 이물 · Leak Check · 펌프 상태 · Base Pressure 설정."},
    {"bit": 8, "name": "ATM Timeout", "crit": False,
     "cause": "벤트 시작 뒤 Vent Timeout 안에 대기압 입력이 켜지지 않음(벤트 N2 · VV · 대기압 스위치).",
     "check": "N2 공급 · VV 열림 · 대기압 스위치 설정과 배선."},
    {"bit": 9, "name": "Heater Overtemp", "crit": True,
     "cause": "과온 트립 입력 또는 채널 온도가 과온 한계를 넘음(SSR 붙음 · 설정 실수 · 열전대 위치/접촉).",
     "check": "히터 표의 채널 · 한계 · 설정값, 열전대, SSR — 히터 전원이 꺼진다."},
    {"bit": 10, "name": "TC Comm Lost", "crit": False,
     "cause": "온도조절기와 PLC 통신 끊김(전원 · RS-485 배선 · 국번/속도 설정).",
     "check": "온도조절기 전원 · 배선 · 국번 — 통신이 없는 채널은 전원 켜기가 막힌다."},
    {"bit": 11, "name": "TC Alarm", "crit": False,
     "cause": "온도조절기가 알람을 냄(센서 단선 · 범위 밖 · 자체 알람).",
     "check": "히터 표의 알람 채널 · 온도조절기 화면 · 열전대."},
    {"bit": 12, "name": "MFC Fault", "crit": True,
     "cause": "블록 준비에서 MFC1 이 MFC Stable Timeout 안에 안정되지 않음, 또는 공정 중 MFC1 편차가 MFC1 Tolerance 를 10 s 넘게 벗어남(가스 공급 압력 · 밸브 · MFC 이상 · 설정값).",
     "check": "가스 공급 · 레시피 MFC1 값 · MFC 전원/신호."},
    {"bit": 13, "name": "Recipe Table Check Failed", "crit": False,
     "cause": "PLC 가 레시피 표를 거절함(합계 · 범위 · 블록/그룹 적재).",
     "check": "레시피 검사 오류 · Upload to PLC 다시."},
    {"bit": 14, "name": "Gas Leak", "crit": True,
     "cause": "가스 누출 감지기 입력.",
     "check": "감지기 표시 · 실린더 밸브 잠금 · 환기 · 안전 담당자에게 알림 — 원인을 찾기 전에 다시 시작하지 않는다."},
    {"bit": 15, "name": "Precursor/Reactant Open Conflict", "crit": True,
     "cause": "전구체 밸브(PV-1~3)와 반응물 밸브(PV-R)가 함께 요청됨.",
     "check": "레시피 스텝 · 수동 밸브(동시에 열지 않는다)."},
]
ALARMS1 = [
    {"bit": 0, "name": "IV-E Feedback Fault", "crit": True,
     "cause": "IV-E 출력과 열림/닫힘 리미트 입력이 5 s 넘게 맞지 않음(공압 · 밸브 고착 · 리미트 스위치).",
     "check": "공압 · IV-E 동작 · 리미트 스위치 위치와 배선."},
    {"bit": 3, "name": "O3 Generator Alarm / O3 Permit Lost", "crit": True,
     "cause": "O3 발생기 알람 입력, 또는 공정 중 O3 허가가 빠짐(바이패스 펌프 · IV-B · 실내 O3 · O3 상한).",
     "check": "발생기 화면 · O2 공급 · 냉각 · 바이패스 펌프와 IV-B."},
    {"bit": 4, "name": "Room O3 Detected", "crit": True,
     "cause": "실내 O3 감지기 입력.",
     "check": "감지기 · 환기 · O3 배관 누설 — 출입을 막고 안전 담당자에게 알린다."},
    {"bit": 5, "name": "Bypass Pump Alarm / No Feedback", "crit": True,
     "cause": "바이패스 펌프 알람 입력, 또는 운전 요청 뒤 10 s 동안 운전 피드백 없음.",
     "check": "바이패스 펌프 전원 · 알람 · 피드백 배선."},
]
CRITICAL_MASK0 = 0xD23F             # v0.7.0: b6 PC Link Lost 를 뺐다(0xD27F → 0xD23F)

# ===================== 히터 =====================
HEATER_COUNT = 12
HEATER_DEFAULT_NAMES = [
    "Powder Reactor", "Precursor Line", "Spare 3 (O3 Line, unheated)", "Canister 2", "Canister 3",
    "Trap", "Spare 7", "Spare 8", "Spare 9", "Spare 10", "Spare 11", "Spare 12",
]
HEATER_POWER_MASK = 0x003B          # 명령 13 으로 전원을 다루는 채널 (CH1·2·4·5·6)

# ===================== MFC =====================
MFC_COUNT = 2
MFC_DEFAULT_NAMES = ["Precursor Carrier", "Assist"]

# ===================== 장비 전용 아날로그 =====================
HAS_RF = False
HAS_PCV = False
HAS_O3 = True

DISPLAY_SETPOINTS = [
    {"offset": 1,  "key": "mfc1", "name": "MFC1 Setpoint"},
    {"offset": 2,  "key": "mfc2", "name": "MFC2 Setpoint"},
    {"offset": 11, "key": "o3",   "name": "O3 Setpoint"},
]


# ===================== 레시피에서 쓰는 것 =====================
# 레시피 파일의 형식 태그. ★ 다른 장비 형식은 열지 않는다 —
# 밸브 이름이 같아 보여도 배관이 달라, 그대로 실행하면 엉뚱한 곳을 연다.
RECIPE_FORMAT = "POWDERALD-recipe/1"

# 레시피 스텝에서 고를 수 있는 밸브 (PLC 가 자동으로 다루는 것은 뺀다)
RECIPE_VALVES = [v["tag"] for v in VALVES if not v.get("auto")]

# 전구체 쪽과 반응물 쪽 태그 — 한 스텝에 함께 있으면 안 된다(PLC 가 둘 다 막는다)
PRECURSOR_TAGS = [v["tag"] for v in VALVES if v["bit"] in PRECURSOR_VALVE_BITS]
REACTANT_TAGS = [v["tag"] for v in VALVES if v["bit"] in REACTANT_VALVE_BITS]

# 어시스트 밸브는 짝이 되는 캐니스터 밸브가 열리는 스텝에서만 쓴다.
# (캐니스터로 캐리어를 흘려 놓고 출구를 안 열면 캐니스터만 가압된다)
ASSIST_PAIR = {"PV-A1": "PV-2", "PV-A2": "PV-3"}


def valve_bit(tag: str):
    for v in VALVES:
        if v["tag"] == tag:
            return v["bit"]
    return None


def valve_by_bit(bit):
    for v in VALVES:
        if v["bit"] == bit:
            return v
    return None


def aux_by_bit(bit):
    for a in AUX:
        if a["bit"] == bit:
            return a
    return None


# ===================== 정비 카운터 (v0.7.0 A4) =====================
# 켜진 시간을 세는 보조 출력 — PLC 링크가 붙어 있는 동안(멈춤 제외)만 센다.
# warn: 한계 설정 키(maintenance.<warn>, 0 = 끔). 없으면 한계 없이 세기만 한다.
MAINT_HOURS = [
    {"key": "pump",        "name": "Main Pump (PMP)",    "aux": 3,  "warn": "pump_hours_warn"},
    {"key": "o3",          "name": "O3 Generator",       "aux": 9,  "warn": "o3_hours_warn"},
    {"key": "bypass_pump", "name": "Bypass Pump (BPMP)", "aux": 11, "warn": ""},
]
