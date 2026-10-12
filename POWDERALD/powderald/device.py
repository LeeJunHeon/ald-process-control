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
THEME = "light"                     # 라이트/다크 반전으로 두 장비를 구분한다
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
INPUTS0 = [
    {"bit": 0,  "tag": "EMO",     "name": "EMO OK",   "ok_when": True},
    {"bit": 1,  "tag": "RST-SW",  "name": "Panel Reset SW", "ok_when": True, "momentary": True},
    {"bit": 2,  "tag": "AIR",     "name": "Air OK",       "ok_when": True},
    {"bit": 3,  "tag": "N2",      "name": "N2 Pressure OK",    "ok_when": True},
    {"bit": 4,  "tag": "CW",      "name": "Cooling Water OK",     "ok_when": True},
    {"bit": 5,  "tag": "LID",     "name": "Chamber Closed",       "ok_when": True},
    {"bit": 6,  "tag": "ATM",     "name": "Chamber ATM",     "ok_when": None},
    {"bit": 7,  "tag": "PMP-RUN", "name": "Pump Running",       "ok_when": None},
    {"bit": 8,  "tag": "PMP-ALM", "name": "Pump Alarm",       "ok_when": False},
    {"bit": 9,  "tag": "IVE-O",   "name": "IV-E Open Limit", "ok_when": None},
    {"bit": 10, "tag": "IVE-C",   "name": "IV-E Closed Limit", "ok_when": None},
    {"bit": 11, "tag": "LEAK",    "name": "Gas Leak",       "ok_when": False},
    {"bit": 12, "tag": "SCRUB",   "name": "Scrubber OK", "ok_when": True, "unused": True},
    {"bit": 13, "tag": "OT",      "name": "Heater Overtemp Trip",   "ok_when": False},
]
INPUTS1 = [
    {"bit": 2, "tag": "O3-RUN",  "name": "O3 Generator Running",   "ok_when": None},
    {"bit": 3, "tag": "O3-ALM",  "name": "O3 Alarm",         "ok_when": False},
    {"bit": 4, "tag": "O3-ROOM", "name": "Room O3 Detected",     "ok_when": False},
    {"bit": 5, "tag": "BP-RUN",  "name": "Bypass Pump Running", "ok_when": None},
    {"bit": 6, "tag": "BP-ALM",  "name": "Bypass Pump Alarm", "ok_when": False},
]

# ===================== 인터락 (D00004) =====================
INTERLOCKS = [
    {"bit": 0, "tag": "Basic",        "why": "비상정지·공압·N2·챔버 닫힘·냉각수 정상"},
    {"bit": 1, "tag": "Pump",        "why": "펌프 운전 중이고 펌프 알람 없음"},
    {"bit": 2, "tag": "Vacuum OK",        "why": "베이스 압력 설정이 0보다 크고, 현재 압력이 그 이하"},
    {"bit": 3, "tag": "Start Permit", "why": "공정 밸브 허가 + 진공 + PC 링크 정상 + 레시피 표 통과 + O3 허가"},
    {"bit": 4, "tag": "Valve Permit", "why": "기본 + 펌프 + IV-E 열림 + 안전 정지 요구 없음 + 대기압 아님"},
    {"bit": 5, "tag": "Vent Permit",    "why": "시퀀서 동작 아님(공정 준비 포함) + IV-E 닫힘 입력 + IV-E 출력 꺼짐 + 비상정지 정상"},
    {"bit": 6, "tag": "Precursor/Reactant Conflict", "why": "전구체 밸브와 O3 밸브가 함께 요청됨 (이상)",
     "bad": True},
    {"bit": 7, "tag": "Safe Stop Request", "why": "비상정지 또는 PC 끊김 또는 중대 알람", "bad": True},
    {"bit": 9, "tag": "O3 Permit",      "why": "기본 + IV-B 열림 + 바이패스 펌프 운전·알람 없음 + 그 뒤 5 s + "
                                            "O3 알람·실내 O3 없음 + O3 상한 > 0 + 안전 정지 요구 없음"},
]

# ===================== 알람 =====================
ALARMS0 = [
    {"bit": 0,  "name": "EMO",                  "crit": True},
    {"bit": 1,  "name": "Air Pressure Low",                 "crit": True},
    {"bit": 2,  "name": "N2 Supply Low",              "crit": True},
    {"bit": 3,  "name": "Cooling Water Fault",               "crit": True},
    {"bit": 4,  "name": "Chamber Open in Process",          "crit": True},
    {"bit": 5,  "name": "Pump Alarm / Stop",           "crit": True},
    {"bit": 6,  "name": "PC Link Lost",              "crit": True},
    {"bit": 7,  "name": "Base Pressure Timeout",  "crit": False},
    {"bit": 8,  "name": "ATM Timeout",       "crit": False},
    {"bit": 9,  "name": "Heater Overtemp",                 "crit": True},
    {"bit": 10, "name": "TC Comm Lost",        "crit": False},
    {"bit": 11, "name": "TC Alarm",        "crit": False},
    {"bit": 12, "name": "MFC Fault",                  "crit": True},
    {"bit": 13, "name": "Recipe Table Check Failed",        "crit": False},
    {"bit": 14, "name": "Gas Leak",                 "crit": True},
    {"bit": 15, "name": "Precursor/Reactant Open Conflict", "crit": True},
]
ALARMS1 = [
    {"bit": 0, "name": "IV-E Feedback Fault",     "crit": True},
    {"bit": 3, "name": "O3 Generator Alarm / O3 Permit Lost",  "crit": True},
    {"bit": 4, "name": "Room O3 Detected",                          "crit": True},
    {"bit": 5, "name": "Bypass Pump Alarm / No Feedback",   "crit": True},
]
CRITICAL_MASK0 = 0xD27F

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
