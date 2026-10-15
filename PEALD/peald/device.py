"""
device.py — PEALD 장비 정체성과 장비 구조 정의.

★ 여기 있는 값은 설정 파일로 바꿀 수 없다.
  설정 파일을 잘못 복사해도 장비가 바뀌어 보이면 안 되기 때문이다 —
  화면에 "PEALD"라고 떠 있는데 실제로는 다른 장비의 밸브를 여는 사고를 막는다.
  설정으로 바꿀 수 있는 것은 창 위치(left/right)와 포트뿐이다.

★ 장비 구조(밸브·보조 출력·입력·인터락·알람 문구·MFC·히터 기본 이름)도 여기 한 곳에 둔다.
  주소와 비트 번호 자체는 addresses.py(두 장비 공통 약속)에 있고,
  이 파일은 "이 장비에 실제로 있는 것"만 골라 이름을 붙인다.
"""

# ===================== 장비 정체성 (고정) =====================
KEY = "peald"
NAME = "PEALD"                      # 헤더 배지 · 확인 창 제목
TITLE = "PEALD Process Control"           # 창 제목
THEME = "light"                     # v0.5.1: 두 장비 모두 밝은 테마가 기본(설정 window.theme 으로 dark)
ACCENT = "#8b5cf6"                  # 보라 — 헤더 띠·배지·탭 밑줄·확인 창에만 쓴다
ICON = "peald.ico"

DEFAULT_PORT = 8201                 # 웹(화면) 포트
DEFAULT_SIM_PORT = 15201            # 내장 시뮬레이터 포트
DEFAULT_SIDE = "right"              # 기본 창 위치

# 장비 ID — PLC 가 D00019 에 매 스캔 쓰는 값('PE'). PC 가 연결 직후 이것부터 확인한다.
DEVICE_ID = 0x5045

MUTEX_NAME = "VANAM.PEALD.Control"          # 단일 실행 뮤텍스
APP_USER_MODEL_ID = "VANAM.PEALD.Control"   # 작업표시줄에서 두 프로그램을 따로 묶는다
EXE_NAME = "PEALD_Control"
LOG_PREFIX = "PEALD"

# ===================== 공정 밸브 (D00010 / D01004 / 레시피 밸브 워드) =====================
# bit: 비트 번호, tag: 배관도·로그에 쓰는 짧은 이름, name: 사람이 읽는 설명
# pulse: 펄스 구동 밸브(최소 열림 시간 PRM_VLV_MIN_MS 적용 대상)
VALVES = [
    {"bit": 0, "tag": "PV-1",  "name": "Canister 1 → Precursor Line",     "pulse": True},
    {"bit": 1, "tag": "PV-2",  "name": "Canister 2 → Precursor Line",     "pulse": True},
    {"bit": 2, "tag": "PV-3",  "name": "Canister 3 → Precursor Line",     "pulse": True},
    {"bit": 3, "tag": "PV-A1", "name": "Assist N2 → Canister 2 Inlet", "pulse": False},
    {"bit": 4, "tag": "PV-A2", "name": "Assist N2 → Canister 3 Inlet", "pulse": False},
    {"bit": 5, "tag": "PV-R",  "name": "Reactant Manifold → Chamber",      "pulse": False},
    {"bit": 6, "tag": "PV-R1", "name": "Purge N2 → Manifold",          "pulse": False},
    {"bit": 7, "tag": "PV-R2", "name": "H2O Canister → Manifold",     "pulse": False},
    {"bit": 8, "tag": "PV-R3", "name": "O2 → Manifold",               "pulse": False},
]
MANUAL_VALVE_MASK = 0x01FF          # 수동 요청이 반영되는 비트 (b0~b8)

# 전구체 쪽과 반응물 쪽 — 동시 개방 감지의 기준(알람0 b15)
PRECURSOR_VALVE_BITS = (0, 1, 2)
REACTANT_VALVE_BITS = (5,)

# ===================== 보조 출력 (D00014 / D01008) =====================
AUX = [
    {"bit": 0,  "tag": "VV",     "name": "Chamber Vent (N2)"},
    {"bit": 1,  "tag": "IV-E",   "name": "Exhaust Isolation"},
    {"bit": 2,  "tag": "PMP-N2", "name": "Pump N2 Purge"},
    {"bit": 3,  "tag": "PMP",    "name": "Dry Pump Run"},
    {"bit": 4,  "tag": "LMP-R",  "name": "Tower Lamp Red"},
    {"bit": 5,  "tag": "LMP-Y",  "name": "Tower Lamp Yellow"},
    {"bit": 6,  "tag": "LMP-G",  "name": "Tower Lamp Green"},
    {"bit": 7,  "tag": "BUZ",    "name": "Buzzer"},
    {"bit": 8,  "tag": "RF",     "name": "RF ON"},
]
# 명령 12(수동 적용)로 반영되는 보조 출력 비트.
# VV·IV-E·펌프는 명령 8~11(펌핑·벤트·전체 닫기)이 다루므로 여기서 제외한다.
AUX_CMD_MASK = 0x0F00

# ===================== 입력 (D00008 / D00009) =====================
# ok_when: 이 비트가 1이면 정상인가(True) / 1이면 이상인가(False)
# short: 화면 상태 목록에 쓰는 이름(v0.6.1 B4) — 허가 · 정상 조건 입력은 상태가 들어가지 않은 이름, 점 색이 상태를 말한다
INPUTS0 = [
    {"bit": 0,  "tag": "EMO",     "name": "EMO OK",   "ok_when": True, "short": "EMO"},
    {"bit": 1,  "tag": "RST-SW",  "name": "Panel Reset SW", "ok_when": True, "momentary": True},
    {"bit": 2,  "tag": "AIR",     "name": "Air OK",       "ok_when": True, "short": "Air"},
    {"bit": 3,  "tag": "N2",      "name": "N2 Pressure OK",    "ok_when": True, "short": "N2 Pressure"},
    {"bit": 4,  "tag": "CW",      "name": "Cooling Water OK",     "ok_when": True, "short": "Cooling Water"},
    {"bit": 5,  "tag": "LID",     "name": "Lid Closed",       "ok_when": True, "short": "Lid"},
    {"bit": 6,  "tag": "ATM",     "name": "Chamber ATM",     "ok_when": None},
    {"bit": 7,  "tag": "PMP-RUN", "name": "Pump Running",       "ok_when": None},
    {"bit": 8,  "tag": "PMP-ALM", "name": "Pump Alarm",       "ok_when": False},
    {"bit": 9,  "tag": "IVE-O",   "name": "IV-E Open Limit", "ok_when": None},
    {"bit": 10, "tag": "IVE-C",   "name": "IV-E Closed Limit", "ok_when": None},
    {"bit": 11, "tag": "LEAK",    "name": "Gas Leak",       "ok_when": False},
    {"bit": 12, "tag": "SCRUB",   "name": "Scrubber OK", "ok_when": True, "short": "Scrubber", "unused": True},
    {"bit": 13, "tag": "OT",      "name": "Heater Overtemp Trip",   "ok_when": False},
]
INPUTS1 = [
    {"bit": 0, "tag": "RF-RDY", "name": "RF Ready", "ok_when": None},
    {"bit": 1, "tag": "RF-ALM", "name": "RF Alarm", "ok_when": False},
]

# ===================== 인터락 (D00004) =====================
# why: 칩에 마우스를 올렸을 때 보여 줄 조건 설명
INTERLOCKS = [
    {"bit": 0, "tag": "Basic",        "why": "비상정지·공압·N2·리드·냉각수 정상"},
    {"bit": 1, "tag": "Pump",        "why": "펌프 운전 중이고 펌프 알람 없음"},
    {"bit": 2, "tag": "Vacuum OK",        "why": "베이스 압력 설정이 0보다 크고, 현재 압력이 그 이하"},
    {"bit": 3, "tag": "Start Permit", "why": "공정 밸브 허가 + 진공 + PC 링크 정상 + 레시피 표 통과"},
    {"bit": 4, "tag": "Valve Permit", "why": "기본 + 펌프 + IV-E 열림 + 안전 정지 요구 없음 + 대기압 아님"},
    {"bit": 5, "tag": "Vent Permit",    "why": "시퀀서 동작 아님(공정 준비 포함) + IV-E 닫힘 입력 + IV-E 출력 꺼짐 + 비상정지 정상"},
    {"bit": 6, "tag": "Precursor/Reactant Conflict", "why": "전구체 밸브와 반응물 밸브가 함께 요청됨 (이상)",
     "bad": True},
    {"bit": 7, "tag": "Safe Stop Request", "why": "비상정지 또는 PC 끊김 또는 중대 알람", "bad": True},
    {"bit": 8, "tag": "RF Permit",      "why": "공정 밸브 허가 + RF 준비 + RF·반사 알람 없음 + "
                                            "RF 상한 > 0 + 베이스 압력 < 현재 압력 ≤ RF 허가 최대 압력"},
]

# ===================== 알람 =====================
# crit: 중대(장비를 세우는 알람) / 아니면 경고(표시·기록만)
ALARMS0 = [
    {"bit": 0,  "name": "EMO",                  "crit": True},
    {"bit": 1,  "name": "Air Pressure Low",                 "crit": True},
    {"bit": 2,  "name": "N2 Supply Low",              "crit": True},
    {"bit": 3,  "name": "Cooling Water Fault",               "crit": True},
    {"bit": 4,  "name": "Lid Open in Process",          "crit": True},
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
    {"bit": 0, "name": "IV-E Feedback Fault", "crit": True},
    {"bit": 1, "name": "RF Generator/Matcher Alarm",                "crit": True},
    {"bit": 2, "name": "RF Reflected Power High",                    "crit": True},
]
# 중대 알람 마스크 — PLC 가 안전 정지를 거는 기준(워드0). 워드1은 전부 중대.
CRITICAL_MASK0 = 0xD27F

# ===================== 히터 =====================
# 설정(config.heaters)이 이름·사용 여부·한계·기본 SV 를 덮지만,
# 기본 이름은 장비 구조라서 여기 둔다.
HEATER_COUNT = 12
HEATER_DEFAULT_NAMES = [
    "Stage/Chamber", "Precursor Line", "Reactant Line", "Canister 2", "Canister 3", "Trap",
    "Spare 7", "Spare 8", "Spare 9", "Spare 10", "Spare 11", "Spare 12",
]
HEATER_POWER_MASK = 0x003F          # 명령 13 으로 전원을 다루는 채널 (CH1~6)

# ===================== MFC =====================
MFC_COUNT = 4                       # 이 장비에 장착된 MFC 개수 (주소 영역은 8개까지)
MFC_DEFAULT_NAMES = ["Precursor Carrier", "Assist", "Reactant Purge", "O2"]

# ===================== 장비 전용 아날로그 =====================
HAS_RF = True
HAS_PCV = True
HAS_O3 = False

# 표시용 내부 영역(D04120~)에서 이 장비가 쓰는 항목
DISPLAY_SETPOINTS = [
    {"offset": 0,  "key": "pcv",  "name": "PCV Target"},
    {"offset": 1,  "key": "mfc1", "name": "MFC1 Setpoint"},
    {"offset": 2,  "key": "mfc2", "name": "MFC2 Setpoint"},
    {"offset": 3,  "key": "mfc3", "name": "MFC3 Setpoint"},
    {"offset": 4,  "key": "mfc4", "name": "MFC4 Setpoint"},
    {"offset": 10, "key": "rf",   "name": "RF Power"},
]


# ===================== 레시피에서 쓰는 것 =====================
# 레시피 파일의 형식 태그. ★ 다른 장비 형식은 열지 않는다 —
# 밸브 이름이 같아 보여도 배관이 달라, 그대로 실행하면 엉뚱한 곳을 연다.
RECIPE_FORMAT = "PEALD-recipe/1"

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
