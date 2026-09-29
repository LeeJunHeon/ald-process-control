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
TITLE = "PEALD 공정 제어"            # 창 제목
THEME = "dark"                      # 라이트/다크 반전으로 두 장비를 구분한다
ACCENT = "#8b5cf6"                  # 보라 — 헤더 띠·배지·탭 밑줄·확인 창에만 쓴다
ICON = "peald.ico"

DEFAULT_PORT = 8201                 # 웹(화면) 포트
DEFAULT_SIM_PORT = 15201            # 내장 시뮬레이터 포트
DEFAULT_SIDE = "right"              # 기본 창 위치

MUTEX_NAME = "VANAM.PEALD.Control"          # 단일 실행 뮤텍스
APP_USER_MODEL_ID = "VANAM.PEALD.Control"   # 작업표시줄에서 두 프로그램을 따로 묶는다
EXE_NAME = "PEALD_Control"
LOG_PREFIX = "PEALD"

# ===================== 공정 밸브 (D00010 / D01004 / 레시피 밸브 워드) =====================
# bit: 비트 번호, tag: 배관도·로그에 쓰는 짧은 이름, name: 사람이 읽는 설명
# pulse: 펄스 구동 밸브(최소 열림 시간 PRM_VLV_MIN_MS 적용 대상)
VALVES = [
    {"bit": 0, "tag": "PV-1",  "name": "캐니스터1 → 전구체 라인",     "pulse": True},
    {"bit": 1, "tag": "PV-2",  "name": "캐니스터2 → 전구체 라인",     "pulse": True},
    {"bit": 2, "tag": "PV-3",  "name": "캐니스터3 → 전구체 라인",     "pulse": True},
    {"bit": 3, "tag": "PV-A1", "name": "어시스트 N2 → 캐니스터2 입구", "pulse": False},
    {"bit": 4, "tag": "PV-A2", "name": "어시스트 N2 → 캐니스터3 입구", "pulse": False},
    {"bit": 5, "tag": "PV-R",  "name": "반응물 매니폴드 → 챔버",      "pulse": False},
    {"bit": 6, "tag": "PV-R1", "name": "퍼지 N2 → 매니폴드",          "pulse": False},
    {"bit": 7, "tag": "PV-R2", "name": "H2O 캐니스터 → 매니폴드",     "pulse": False},
    {"bit": 8, "tag": "PV-R3", "name": "O2 → 매니폴드",               "pulse": False},
]
MANUAL_VALVE_MASK = 0x01FF          # 수동 요청이 반영되는 비트 (b0~b8)

# 전구체 쪽과 반응물 쪽 — 동시 개방 감지의 기준(알람0 b15)
PRECURSOR_VALVE_BITS = (0, 1, 2)
REACTANT_VALVE_BITS = (5,)

# ===================== 보조 출력 (D00014 / D01008) =====================
AUX = [
    {"bit": 0,  "tag": "VV",     "name": "챔버 벤트 (N2)"},
    {"bit": 1,  "tag": "IV-E",   "name": "배기 격리"},
    {"bit": 2,  "tag": "PMP-N2", "name": "펌프 N2 퍼지"},
    {"bit": 3,  "tag": "PMP",    "name": "드라이펌프 기동"},
    {"bit": 4,  "tag": "LMP-R",  "name": "경광등 적"},
    {"bit": 5,  "tag": "LMP-Y",  "name": "경광등 황"},
    {"bit": 6,  "tag": "LMP-G",  "name": "경광등 녹"},
    {"bit": 7,  "tag": "BUZ",    "name": "부저"},
    {"bit": 8,  "tag": "RF",     "name": "RF ON"},
]
# 명령 12(수동 적용)로 반영되는 보조 출력 비트.
# VV·IV-E·펌프는 명령 8~11(펌핑·벤트·전체 닫기)이 다루므로 여기서 제외한다.
AUX_CMD_MASK = 0x0F00

# ===================== 입력 (D00008 / D00009) =====================
# ok_when: 이 비트가 1이면 정상인가(True) / 1이면 이상인가(False)
INPUTS0 = [
    {"bit": 0,  "tag": "EMO",     "name": "비상정지 정상",   "ok_when": True},
    {"bit": 1,  "tag": "RST-SW",  "name": "판넬 리셋 스위치", "ok_when": True, "momentary": True},
    {"bit": 2,  "tag": "AIR",     "name": "공압 정상",       "ok_when": True},
    {"bit": 3,  "tag": "N2",      "name": "N2 압력 정상",    "ok_when": True},
    {"bit": 4,  "tag": "CW",      "name": "냉각수 정상",     "ok_when": True},
    {"bit": 5,  "tag": "LID",     "name": "리드 닫힘",       "ok_when": True},
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
    {"bit": 0, "tag": "RF-RDY", "name": "RF 준비", "ok_when": None},
    {"bit": 1, "tag": "RF-ALM", "name": "RF 알람", "ok_when": False},
]

# ===================== 인터락 (D00004) =====================
# why: 칩에 마우스를 올렸을 때 보여 줄 조건 설명
INTERLOCKS = [
    {"bit": 0, "tag": "기본",        "why": "비상정지·공압·N2·리드·냉각수 정상"},
    {"bit": 1, "tag": "펌프",        "why": "펌프 운전 중이고 펌프 알람 없음"},
    {"bit": 2, "tag": "진공",        "why": "베이스 압력 설정이 0보다 크고, 현재 압력이 그 이하"},
    {"bit": 3, "tag": "공정 시작 허가", "why": "공정 밸브 허가 + 진공 + PC 링크 정상 + 레시피 표 통과"},
    {"bit": 4, "tag": "공정 밸브 허가", "why": "기본 + 펌프 + IV-E 열림 + 안전 정지 요구 없음 + 대기압 아님"},
    {"bit": 5, "tag": "벤트 허가",    "why": "공정 중이 아니고 + IV-E 닫힘 + 비상정지 정상"},
    {"bit": 6, "tag": "전구체·반응물 동시 요청", "why": "전구체 밸브와 반응물 밸브가 함께 요청됨 (이상)",
     "bad": True},
    {"bit": 7, "tag": "안전 정지 요구", "why": "비상정지 또는 PC 끊김 또는 중대 알람", "bad": True},
    {"bit": 8, "tag": "RF 허가",      "why": "공정 밸브 허가 + RF 준비 + RF·반사 알람 없음 + "
                                            "RF 상한 > 0 + 베이스 압력 < 현재 압력 ≤ RF 허가 최대 압력"},
]

# ===================== 알람 =====================
# crit: 중대(장비를 세우는 알람) / 아니면 경고(표시·기록만)
ALARMS0 = [
    {"bit": 0,  "name": "비상정지",                  "crit": True},
    {"bit": 1,  "name": "공압 저하",                 "crit": True},
    {"bit": 2,  "name": "N2 공급 저하",              "crit": True},
    {"bit": 3,  "name": "냉각수 이상",               "crit": True},
    {"bit": 4,  "name": "공정 중 리드 열림",          "crit": True},
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
    {"bit": 0, "name": "IV-E 동작 이상 (명령과 리미트 불일치)", "crit": True},
    {"bit": 1, "name": "RF 파워·매칭박스 알람",                "crit": True},
    {"bit": 2, "name": "RF 반사 전력 초과",                    "crit": True},
]
# 중대 알람 마스크 — PLC 가 안전 정지를 거는 기준(워드0). 워드1은 전부 중대.
CRITICAL_MASK0 = 0xD27F

# ===================== 히터 =====================
# 설정(config.heaters)이 이름·사용 여부·한계·기본 SV 를 덮지만,
# 기본 이름은 장비 구조라서 여기 둔다.
HEATER_COUNT = 12
HEATER_DEFAULT_NAMES = [
    "Stage·챔버", "전구체 라인", "반응물 라인", "캐니스터2", "캐니스터3", "트랩",
    "예비 7", "예비 8", "예비 9", "예비 10", "예비 11", "예비 12",
]
HEATER_POWER_MASK = 0x003F          # 명령 13 으로 전원을 다루는 채널 (CH1~6)

# ===================== MFC =====================
MFC_COUNT = 4                       # 이 장비에 장착된 MFC 개수 (주소 영역은 8개까지)
MFC_DEFAULT_NAMES = ["전구체 캐리어", "어시스트", "반응물 퍼지", "O2"]

# ===================== 장비 전용 아날로그 =====================
HAS_RF = True
HAS_PCV = True
HAS_O3 = False

# 표시용 내부 영역(D04120~)에서 이 장비가 쓰는 항목
DISPLAY_SETPOINTS = [
    {"offset": 0,  "key": "pcv",  "name": "PCV 목표"},
    {"offset": 1,  "key": "mfc1", "name": "MFC1 설정"},
    {"offset": 2,  "key": "mfc2", "name": "MFC2 설정"},
    {"offset": 3,  "key": "mfc3", "name": "MFC3 설정"},
    {"offset": 4,  "key": "mfc4", "name": "MFC4 설정"},
    {"offset": 10, "key": "rf",   "name": "RF 전력"},
]


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
