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
TITLE = "Powder ALD 공정 제어"       # 창 제목
THEME = "light"                     # 라이트/다크 반전으로 두 장비를 구분한다
ACCENT = "#0e7490"                  # 청록 — 헤더 띠·배지·탭 밑줄·확인 창에만 쓴다
ICON = "powderald.ico"

DEFAULT_PORT = 8101                 # 웹(화면) 포트
DEFAULT_SIM_PORT = 15101            # 내장 시뮬레이터 포트
DEFAULT_SIDE = "left"               # 기본 창 위치

MUTEX_NAME = "VANAM.POWDERALD.Control"
APP_USER_MODEL_ID = "VANAM.POWDERALD.Control"
EXE_NAME = "POWDERALD_Control"
LOG_PREFIX = "POWDERALD"

# ===================== 공정 밸브 (D00010 / D01004 / 레시피 밸브 워드) =====================
VALVES = [
    {"bit": 0, "tag": "PV-1",  "name": "캐니스터1 → 전구체 라인",     "pulse": True},
    {"bit": 1, "tag": "PV-2",  "name": "캐니스터2 → 전구체 라인",     "pulse": True},
    {"bit": 2, "tag": "PV-3",  "name": "캐니스터3 → 전구체 라인",     "pulse": True},
    {"bit": 3, "tag": "PV-A1", "name": "어시스트 N2 → 캐니스터2 입구", "pulse": False},
    {"bit": 4, "tag": "PV-A2", "name": "어시스트 N2 → 캐니스터3 입구", "pulse": False},
    {"bit": 5, "tag": "PV-R",  "name": "O3 → 챔버",                  "pulse": True},
    # ★ PV-B 는 PLC 가 자동으로 연다(발생기 운전 중 PV-R 이 닫혀 있으면 열림).
    #   수동·레시피로 쓰지 않는다 — 수동 마스크(b0~b5)에 넣지 않은 이유다.
    {"bit": 9, "tag": "PV-B",  "name": "O3 우회 → 바이패스 펌프 (PLC 자동)",
     "pulse": False, "auto": True},
]
MANUAL_VALVE_MASK = 0x003F          # 수동 요청이 반영되는 비트 (b0~b5)

PRECURSOR_VALVE_BITS = (0, 1, 2)
REACTANT_VALVE_BITS = (5,)

# ===================== 보조 출력 (D00014 / D01008) =====================
AUX = [
    {"bit": 0,  "tag": "VV",     "name": "챔버 벤트 (N2)"},
    {"bit": 1,  "tag": "IV-E",   "name": "배기 격리"},
    {"bit": 2,  "tag": "PMP-N2", "name": "펌프 N2 퍼지"},
    {"bit": 3,  "tag": "PMP",    "name": "전용 펌프 기동"},
    {"bit": 4,  "tag": "LMP-R",  "name": "경광등 적"},
    {"bit": 5,  "tag": "LMP-Y",  "name": "경광등 황"},
    {"bit": 6,  "tag": "LMP-G",  "name": "경광등 녹"},
    {"bit": 7,  "tag": "BUZ",    "name": "부저"},
    {"bit": 9,  "tag": "O3GEN",  "name": "O3 발생기"},
    {"bit": 10, "tag": "IV-B",   "name": "바이패스 격리"},
    {"bit": 11, "tag": "BPMP",   "name": "바이패스 로터리펌프"},
]
# 명령 12(수동 적용)로 반영되는 보조 출력 비트 (b9~b11).
# VV·IV-E·펌프는 명령 8~11(펌핑·벤트·전체 닫기)이 다루므로 여기서 제외한다.
AUX_CMD_MASK = 0x0E00

# ===================== 입력 (D00008 / D00009) =====================
INPUTS0 = [
    {"bit": 0,  "tag": "EMO",     "name": "비상정지 정상",   "ok_when": True},
    {"bit": 1,  "tag": "RST-SW",  "name": "판넬 리셋 스위치", "ok_when": True, "momentary": True},
    {"bit": 2,  "tag": "AIR",     "name": "공압 정상",       "ok_when": True},
    {"bit": 3,  "tag": "N2",      "name": "N2 압력 정상",    "ok_when": True},
    {"bit": 4,  "tag": "CW",      "name": "냉각수 정상",     "ok_when": True},
    {"bit": 5,  "tag": "LID",     "name": "챔버 닫힘",       "ok_when": True},
    {"bit": 6,  "tag": "ATM",     "name": "챔버 대기압",     "ok_when": None},
    {"bit": 7,  "tag": "PMP-RUN", "name": "펌프 운전",       "ok_when": None},
    {"bit": 8,  "tag": "PMP-ALM", "name": "펌프 알람",       "ok_when": False},
    {"bit": 9,  "tag": "IVE-O",   "name": "IV-E 열림 리미트", "ok_when": None},
    {"bit": 10, "tag": "IVE-C",   "name": "IV-E 닫힘 리미트", "ok_when": None},
    {"bit": 11, "tag": "LEAK",    "name": "가스 누출",       "ok_when": False},
    {"bit": 12, "tag": "SCRUB",   "name": "배기·스크러버 정상", "ok_when": True, "unused": True},
    {"bit": 13, "tag": "OT",      "name": "히터 과온 차단",   "ok_when": False},
]
INPUTS1 = [
    {"bit": 2, "tag": "O3-RUN",  "name": "O3 발생기 운전",   "ok_when": None},
    {"bit": 3, "tag": "O3-ALM",  "name": "O3 알람",         "ok_when": False},
    {"bit": 4, "tag": "O3-ROOM", "name": "실내 O3 감지",     "ok_when": False},
    {"bit": 5, "tag": "BP-RUN",  "name": "바이패스 펌프 운전", "ok_when": None},
    {"bit": 6, "tag": "BP-ALM",  "name": "바이패스 펌프 알람", "ok_when": False},
]

# ===================== 인터락 (D00004) =====================
INTERLOCKS = [
    {"bit": 0, "tag": "기본",        "why": "비상정지·공압·N2·챔버 닫힘·냉각수 정상"},
    {"bit": 1, "tag": "펌프",        "why": "펌프 운전 중이고 펌프 알람 없음"},
    {"bit": 2, "tag": "진공",        "why": "베이스 압력 설정이 0보다 크고, 현재 압력이 그 이하"},
    {"bit": 3, "tag": "공정 시작 허가", "why": "공정 밸브 허가 + 진공 + PC 링크 정상 + 레시피 표 통과 + O3 허가"},
    {"bit": 4, "tag": "공정 밸브 허가", "why": "기본 + 펌프 + IV-E 열림 + 안전 정지 요구 없음 + 대기압 아님"},
    {"bit": 5, "tag": "벤트 허가",    "why": "공정 중이 아니고 + IV-E 닫힘 + 비상정지 정상"},
    {"bit": 6, "tag": "전구체·반응물 동시 요청", "why": "전구체 밸브와 O3 밸브가 함께 요청됨 (이상)",
     "bad": True},
    {"bit": 7, "tag": "안전 정지 요구", "why": "비상정지 또는 PC 끊김 또는 중대 알람", "bad": True},
    {"bit": 9, "tag": "O3 허가",      "why": "기본 + IV-B 열림 + 바이패스 펌프 운전·알람 없음 + 그 뒤 5 s + "
                                            "O3 알람·실내 O3 없음 + O3 상한 > 0 + 안전 정지 요구 없음"},
]

# ===================== 알람 =====================
ALARMS0 = [
    {"bit": 0,  "name": "비상정지",                  "crit": True},
    {"bit": 1,  "name": "공압 저하",                 "crit": True},
    {"bit": 2,  "name": "N2 공급 저하",              "crit": True},
    {"bit": 3,  "name": "냉각수 이상",               "crit": True},
    {"bit": 4,  "name": "공정 중 챔버 열림",          "crit": True},
    {"bit": 5,  "name": "펌프 알람 / 정지",           "crit": True},
    {"bit": 6,  "name": "PC 통신 끊김",              "crit": True},
    {"bit": 7,  "name": "베이스 압력 도달 시간 초과",  "crit": False},
    {"bit": 8,  "name": "대기압 도달 시간 초과",       "crit": False},
    {"bit": 9,  "name": "히터 과온",                 "crit": True},
    {"bit": 10, "name": "온도조절기 통신 끊김",        "crit": False},
    {"bit": 11, "name": "온도조절기 자체 알람",        "crit": False},
    {"bit": 12, "name": "MFC 이상",                  "crit": True},
    {"bit": 13, "name": "레시피 표 검증 실패",        "crit": False},
    {"bit": 14, "name": "가스 누출",                 "crit": True},
    {"bit": 15, "name": "전구체·반응물 동시 개방 시도", "crit": True},
]
ALARMS1 = [
    {"bit": 0, "name": "IV-E 동작 이상 (명령과 리미트 불일치)",     "crit": True},
    {"bit": 3, "name": "O3 발생기 알람 또는 공정 중 O3 허가 끊김",  "crit": True},
    {"bit": 4, "name": "실내 오존 감지",                          "crit": True},
    {"bit": 5, "name": "바이패스 펌프 알람 또는 운전 피드백 없음",   "crit": True},
]
CRITICAL_MASK0 = 0xD27F

# ===================== 히터 =====================
HEATER_COUNT = 12
HEATER_DEFAULT_NAMES = [
    "챔버 (분말 반응기)", "전구체 라인", "예비 3 (O3 라인 비가열)", "캐니스터2", "캐니스터3",
    "포집 트랩", "예비 7", "예비 8", "예비 9", "예비 10", "예비 11", "예비 12",
]
HEATER_POWER_MASK = 0x003B          # 명령 13 으로 전원을 다루는 채널 (CH1·2·4·5·6)

# ===================== MFC =====================
MFC_COUNT = 2
MFC_DEFAULT_NAMES = ["전구체 캐리어", "어시스트"]

# ===================== 장비 전용 아날로그 =====================
HAS_RF = False
HAS_PCV = False
HAS_O3 = True

DISPLAY_SETPOINTS = [
    {"offset": 1,  "key": "mfc1", "name": "MFC1 설정"},
    {"offset": 2,  "key": "mfc2", "name": "MFC2 설정"},
    {"offset": 11, "key": "o3",   "name": "O3 설정"},
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
