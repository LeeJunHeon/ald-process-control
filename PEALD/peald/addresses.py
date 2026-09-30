"""
addresses.py — PLC 주소표 (두 장비 PLC 에 이미 반영된 확정 사양).

홀딩 레지스터 번호 = D 번호. D01001 → 레지스터 1001.
값은 부호 없는 16비트로 주고받고, 부호 있는 값(히터 온도 ×0.1 ℃)만 해석할 때 바꾼다.
DWORD 는 아래 주소가 하위 워드다 (D00024 하위, D00025 상위).

★ 이 파일은 PLC 래더와 맺은 약속이다. 숫자를 바꾸면 래더도 함께 바꿔야 한다.
  코드 어디에서도 D 번호를 직접 쓰지 않고 반드시 여기 상수를 쓴다.
"""

# ===================== 상태 (PLC → PC, 읽기) =====================
STATUS_BASE = 0
STATUS_COUNT = 81                   # D00000 ~ D00080 을 한 번에 읽는다

D_PLC_HB = 0                        # PLC 하트비트 (500 ms 마다 +1)
D_STATE = 1                         # 장비 상태 (아래 STATE_*)
D_ACK_NO = 2                        # 마지막 처리 명령 번호
D_ACK_RESULT = 3                    # 명령 결과 (아래 RESULT_*)
D_INTERLOCK = 4                     # 인터락 비트
D_ALARM0 = 5
D_ALARM1 = 6
D_ALARM_NEW = 7                     # 1 = 확인 안 한 새 알람 (부저)
D_INPUT0 = 8
D_INPUT1 = 9
D_VALVE_OUT = 10                    # 공정 밸브 실제 출력
D_AUX_OUT = 14                      # 보조 출력 실제
D_DEVICE_ID = 19                    # 장비 ID (PLC 가 매 스캔 쓴다 — PEALD 0x5045 'PE' / Powder 0x5057 'PW')
                                    # ★ 두 PLC 는 주소표가 같다. 주소를 잘못 넣으면 명령이 다른 장비로 간다 —
                                    #   PC 는 연결 직후 이 값을 먼저 확인하고, 맞지 않으면 아무것도 쓰지 않는다.

D_SEQ_STATE = 20                    # 시퀀서 상태 (아래 SEQ_*)
D_SEQ_BLOCK = 21                    # 블록 (1부터)
D_SEQ_STEP = 22                     # 스텝 (1부터)
D_SEQ_GROUP_PASS = 23               # 반복 그룹 회차
D_SEQ_BLOCK_PASS = 24               # 블록 반복 회차 (DWORD: 24 하위 / 25 상위)
D_SEQ_STEP_MS = 26                  # 스텝 경과 ms (DWORD: 26 하위 / 27 상위)
D_RECIPE_SUM_PLC = 28               # PLC 가 계산한 레시피 합계
D_RECIPE_OK = 29                    # 1 = 레시피 표 통과

D_HEATER_PV = 30                    # D00030~41 CH01~12 현재 온도 (×0.1 ℃, 부호 있음)
D_HEATER_OUT = 42                   # D00042~53 히터 출력 %
D_TC_COMM = 54                      # 온도조절기 통신 정상 (b0 국번1 b1 국번2 b2 국번3)
D_HEATER_ALARM = 55                 # 히터 알람 (채널 b0~b11)

D_CVG_RAW = 60                      # CVG 원시값
D_CM_RAW = 61                       # 커패시턴스 게이지 원시값 (설치 시)
D_PCV_RAW = 62                      # PEALD: PCV 개도
D_RF_FWD_RAW = 63                   # PEALD: RF 순방향
D_RF_REF_RAW = 64                   # PEALD: RF 반사
D_O3_RAW = 65                       # Powder: O3 출력·농도

D_MFC_PV = 70                       # D00070~77 MFC1~8 현재 유량 (원시값)
D_SCAN_MAX = 80                     # 최대 스캔 시간 (진단)

# ===================== 명령 (PC → PLC, 쓰기) =====================
D_PC_HB = 1000                      # PC 하트비트
D_CMD_NO = 1001                     # 명령 번호
D_CMD_CODE = 1002                   # 명령 코드
D_CMD_ARG = 1003                    # 인자 (예비)
D_MANUAL_VALVE = 1004               # D01004~07 수동 밸브 요청 (bit0~63)
D_MANUAL_AUX = 1008                 # 수동 보조 출력 요청
D_HEATER_POWER = 1010               # 히터 전원 요청 b0~b11
D_HEATER_SV = 1012                  # D01012~23 CH01~12 설정 온도 (×0.1 ℃)
D_MFC_SV = 1030                     # D01030~37 MFC1~8 수동 설정 (원시값)
D_PCV_SV = 1040                     # PEALD: PCV 목표
D_RF_SV = 1041                      # PEALD: RF 전력 (대기 중 시험용)
D_O3_SV = 1042                      # Powder: O3 설정

# 명령 영역 되읽기 범위 — 명령 번호 이어 가기(연결 직후)와
# 히터 목표·전원 요청 표시(1 s 주기)에 쓴다. ★ 수동 밸브·보조·AO 의 '지금 값'은
# 여기가 아니라 PLC 반영 영역(D04012~D04131)이 기준이다.
CMD_READ_BASE = 1000
CMD_READ_COUNT = 43                 # D01000 ~ D01042

# ===================== 파라미터 (PC → PLC) =====================
D_PRM_PC_WDT_MS = 1100              # PC 하트비트 끊김 판정 ms
D_PRM_BASE_PRESS = 1101             # 공정 시작 베이스 압력 (CVG 원시값, 0 이면 시작 금지)
D_PRM_PUMP_TIMEOUT = 1102           # 베이스 도달 제한 s
D_PRM_VENT_TIMEOUT = 1103           # 대기압 도달 제한 s
D_PRM_MFC_STABLE = 1104             # 블록 시작 MFC 안정 판정 s
D_PRM_MFC_TOL = 1105                # MFC1 허용 편차 (원시값, 0 = 감시 안 함)
D_PRM_MFC_TIMEOUT = 1106            # MFC 안정 대기 제한 s
D_PRM_VALVE_MIN_MS = 1107           # 펄스 밸브 최소 열림 ms
D_PRM_RF_MAX = 1108                 # PEALD: RF 설정 상한 (AO 원시값, 0 = RF 금지)
D_PRM_RF_REF_MAX = 1109             # PEALD: 반사 전력 한계 (AI 원시값)
D_PRM_HEATER_MAX = 1110             # D01110~21 CH01~12 과온 한계 (×0.1 ℃)
D_PRM_RF_REF_MS = 1122              # PEALD: 반사 초과 허용 ms
D_PRM_RF_MAX_PRESS = 1123           # PEALD: RF 허가 최대 압력 (CVG 원시값)
D_PRM_O3_MAX = 1124                 # Powder: O3 설정 상한 (AO 원시값, 0 = O3 금지)

# PLC 가 첫 스캔에 0 인 것만 채워 넣는 기본값.
# ★ 베이스 압력·허용 편차·히터 한계·RF/O3 상한은 0 이면 기능을 막거나 끄므로
#   기본값이 없다 — PC 가 매번 써 넣어야 한다.
PRM_PLC_DEFAULTS = {
    D_PRM_PC_WDT_MS: 3000,
    D_PRM_PUMP_TIMEOUT: 600,
    D_PRM_VENT_TIMEOUT: 300,
    D_PRM_MFC_STABLE: 3,
    D_PRM_MFC_TIMEOUT: 60,
    D_PRM_VALVE_MIN_MS: 200,
}

# 위 기본값을 설정 키(공학 단위)로 — 이 PRM 들은 원시값 = 공학 단위(ms · s)다.
# ★ 설정에 값이 없을 때 PC 가 쓰는 기본값은 모두 여기서 가져온다(레시피 시간 계산 · 검증 경고 ·
#   PLC 쓰기가 서로 다른 기본값을 쓰면 '경고는 없는데 PLC 는 늘린다'가 된다).
PRM_DEFAULTS = {
    "pc_wdt_ms": PRM_PLC_DEFAULTS[D_PRM_PC_WDT_MS],
    "pump_timeout_s": PRM_PLC_DEFAULTS[D_PRM_PUMP_TIMEOUT],
    "vent_timeout_s": PRM_PLC_DEFAULTS[D_PRM_VENT_TIMEOUT],
    "mfc_stable_s": PRM_PLC_DEFAULTS[D_PRM_MFC_STABLE],
    "mfc_timeout_s": PRM_PLC_DEFAULTS[D_PRM_MFC_TIMEOUT],
    "valve_min_ms": PRM_PLC_DEFAULTS[D_PRM_VALVE_MIN_MS],
}

# ===================== PLC 내부 영역 (읽기 전용) =====================
# PLC 가 스스로 쓰는 영역이라 PC 는 읽기만 한다. 한 번에 읽으려고 연속 구간으로 묶었다.
#   D04012~13  실제로 반영된 수동 밸브 (32비트)
#   D04050     실제로 반영된 수동 보조 출력
#   D04120~31  실제로 출력 중인 설정값 (PCV·MFC·RF·O3)
APPLIED_BASE = 4012
APPLIED_COUNT = 120                 # D04012 ~ D04131 (125 워드 제한 안)

D_APPLIED_VALVE = 4012              # +0 하위 16비트, +1 상위 16비트
D_APPLIED_AUX = 4050

DISPLAY_BASE = 4120                 # D04120 ~ D04131
DISPLAY_COUNT = 12
# 오프셋: 0 PCV / 1~4 MFC1~4 / 10 RF (PEALD), 1~2 MFC1~2 / 11 O3 (Powder)


def applied_off(addr: int) -> int:
    """D 주소를 APPLIED 읽기 결과의 인덱스로."""
    return addr - APPLIED_BASE


# 시작 명령을 받으면 PLC 가 레시피 영역을 통째로 복사해 두는 내부 작업본.
# ★ 실행 중에 레시피 영역(D02000~)을 다시 써도 지금 공정에는 영향이 없다.
#   (그래도 PC 는 공정 중 올리기를 막는다 — 운전자가 헷갈리지 않게)
WORK_BASE = 5000

# ===================== 레시피 표 (PC → PLC, 2단계에서 사용) =====================
D_RCP_STEP_COUNT = 2000             # 스텝 개수 (1~100)
D_RCP_BLOCK_COUNT = 2001            # 블록 개수 (1~10)
D_RCP_GROUP_COUNT = 2002            # 반복 그룹 개수 (0~5)
D_RCP_SUM = 2003                    # 합계
D_RCP_NO = 2004                     # 레시피 번호 (로그 대조용)

D_RCP_STEP_BASE = 2100              # 스텝 n: D02100 + (n-1)*8
RCP_STEP_STRIDE = 8
RCP_STEP_MAX = 100
#   +0 밸브 bit0~15 / +1 bit16~31(예비) / +2~3 예비
#   +4~5 시간 ms (DWORD, 20~3,276,700 — 60,000 넘으면 100 ms 단위)
#   +6 플래그 (b0 스텝 끝 일시정지 허용, b1 RF ON) / +7 예비
RCP_STEP_VALVE_LO = 0
RCP_STEP_TIME_LO = 4
RCP_STEP_FLAGS = 6
RCP_FLAG_PAUSE_OK = 0x0001
RCP_FLAG_RF_ON = 0x0002

D_RCP_BLOCK_BASE = 2900             # 블록 n: D02900 + (n-1)*16
RCP_BLOCK_STRIDE = 16
RCP_BLOCK_MAX = 10
#   +0 첫 스텝 / +1 끝 스텝 / +2~3 반복 횟수(DWORD) / +4~11 MFC1~8 설정(원시값)
#   +12 PCV 목표(PEALD) / +13 RF 전력(PEALD) / +14 O3 설정(Powder) / +15 예비
RCP_BLOCK_FIRST = 0
RCP_BLOCK_LAST = 1
RCP_BLOCK_REPEAT_LO = 2
RCP_BLOCK_MFC = 4
RCP_BLOCK_PCV = 12
RCP_BLOCK_RF = 13
RCP_BLOCK_O3 = 14

D_RCP_GROUP_BASE = 3100             # 그룹 n: D03100 + (n-1)*4
RCP_GROUP_STRIDE = 4
RCP_GROUP_MAX = 5
#   +0 시작 블록 / +1 끝 블록 / +2 반복 횟수 / +3 예비

# ★ 래더(XGK)의 비교 명령은 부호 있는 16비트다. 표의 한 워드 값(그룹 시작·끝·반복, 블록 첫·끝
#   스텝, 개수)은 32768 이상이면 음수로 읽혀 '반복 < 1' → 레시피 오류(SEQ_RCP_ERR)가 된다.
#   그래서 한 워드 칸의 최대는 32767 이고, 그룹 반복 한계도 이 값이다.
RCP_WORD_MAX = 0x7FFF
RCP_GROUP_REPEAT_MAX = RCP_WORD_MAX

# 합계 대상 범위: D02000 ~ D03119 중 D02003(합계 자신)을 뺀 모든 워드
RCP_SUM_BASE = 2000
RCP_SUM_END = 3119                  # 포함
RCP_AREA_COUNT = RCP_SUM_END - RCP_SUM_BASE + 1     # 1120 워드

# ===================== 장비 상태 (D00001) =====================
STATE_INIT = 0
STATE_IDLE = 1
STATE_READY = 2                     # 공정 준비
STATE_RUN = 3
STATE_PAUSE = 4
STATE_STOPPING = 5                  # 사이클 후 정지 중
STATE_SAFE_STOP = 6                 # 안전 정지
STATE_NAMES = {
    STATE_INIT: "초기화", STATE_IDLE: "대기", STATE_READY: "공정 준비",
    STATE_RUN: "공정 중", STATE_PAUSE: "일시정지",
    STATE_STOPPING: "사이클 후 정지 중", STATE_SAFE_STOP: "안전 정지",
}

# ===================== 시퀀서 상태 (D00020) =====================
SEQ_NAMES = {
    0: "대기", 1: "조건 확인", 2: "표 복사", 3: "블록 준비", 4: "스텝 실행",
    5: "다음 스텝 계산", 6: "완료", 7: "일시정지", 8: "중단",
}

# ===================== 명령 코드 (D01002) =====================
CMD_PROCESS_START = 1
CMD_PAUSE = 2
CMD_RESUME = 3
CMD_STOP_AFTER_CYCLE = 4
CMD_ABORT = 5
CMD_ALARM_ACK = 6
CMD_ALARM_RESET = 7
CMD_PUMP_START = 8
CMD_PUMP_STOP = 9
CMD_VENT = 10
CMD_ALL_CLOSE = 11
CMD_MANUAL_APPLY = 12
CMD_HEATER_APPLY = 13
CMD_MFC_APPLY = 14

CMD_NAMES = {
    CMD_PROCESS_START: "공정 시작", CMD_PAUSE: "일시정지", CMD_RESUME: "재개",
    CMD_STOP_AFTER_CYCLE: "사이클 끝나면 정지", CMD_ABORT: "즉시 중단",
    CMD_ALARM_ACK: "알람 확인", CMD_ALARM_RESET: "알람 리셋",
    CMD_PUMP_START: "펌핑 시작", CMD_PUMP_STOP: "펌핑 정지", CMD_VENT: "벤트",
    CMD_ALL_CLOSE: "전체 밸브 닫기", CMD_MANUAL_APPLY: "수동 적용",
    CMD_HEATER_APPLY: "히터 적용", CMD_MFC_APPLY: "MFC 수동 적용",
}

# ===================== 명령 결과 (D00003) =====================
RESULT_OK = 0
RESULT_INTERLOCK = 1                # 인터락 조건 미달
RESULT_STATE = 2                    # 지금 상태에서 할 수 없음
RESULT_RECIPE = 3                   # 레시피 표 오류
RESULT_UNKNOWN = 4                  # PLC 가 모르는 명령
RESULT_NAMES = {
    RESULT_OK: "처리됨",
    RESULT_INTERLOCK: "인터락 조건 미달",
    RESULT_STATE: "지금 상태에서 할 수 없습니다",
    RESULT_RECIPE: "레시피 표 오류",
    RESULT_UNKNOWN: "PLC 가 모르는 명령",
}

# ===================== 인터락 비트 번호 =====================
ILK_BASIC = 0
ILK_PUMP = 1
ILK_VACUUM = 2
ILK_START_OK = 3
ILK_VALVE_OK = 4
ILK_VENT_OK = 5
ILK_BOTH_REQ = 6                    # 전구체·반응물 동시 요청 감지 (이상)
ILK_SAFE_STOP_REQ = 7               # 안전 정지 요구 (이상)
ILK_RF_OK = 8                       # PEALD
ILK_O3_OK = 9                       # Powder

# ===================== 입력 비트 번호 =====================
IN0_EMO = 0
IN0_RESET_SW = 1
IN0_AIR = 2
IN0_N2 = 3
IN0_CW = 4
IN0_LID = 5
IN0_ATM = 6
IN0_PUMP_RUN = 7
IN0_PUMP_ALM = 8
IN0_IVE_OPEN = 9
IN0_IVE_CLOSE = 10
IN0_LEAK = 11
IN0_SCRUBBER = 12
IN0_OT = 13

IN1_RF_READY = 0                    # PEALD
IN1_RF_ALM = 1                      # PEALD
IN1_O3_RUN = 2                      # Powder
IN1_O3_ALM = 3                      # Powder
IN1_O3_ROOM = 4                      # Powder
IN1_BP_RUN = 5                      # Powder
IN1_BP_ALM = 6                      # Powder

# ===================== 보조 출력 비트 번호 =====================
AUX_VV = 0
AUX_IVE = 1
AUX_PUMP_N2 = 2
AUX_PUMP = 3
AUX_LAMP_R = 4
AUX_LAMP_Y = 5
AUX_LAMP_G = 6
AUX_BUZZER = 7
AUX_RF = 8                          # PEALD
AUX_O3_GEN = 9                      # Powder
AUX_IVB = 10                        # Powder
AUX_BYPASS_PUMP = 11                # Powder

# ===================== 알람 비트 번호 =====================
ALM0_EMO = 0
ALM0_AIR = 1
ALM0_N2 = 2
ALM0_CW = 3
ALM0_LID = 4
ALM0_PUMP = 5
ALM0_PC_LINK = 6
ALM0_BASE_TIMEOUT = 7
ALM0_VENT_TIMEOUT = 8
ALM0_OT = 9
ALM0_TC_COMM = 10
ALM0_TC_ALM = 11
ALM0_MFC = 12
ALM0_RECIPE = 13
ALM0_LEAK = 14
ALM0_BOTH_OPEN = 15

ALM1_IVE = 0
ALM1_RF = 1                         # PEALD
ALM1_RF_REF = 2                     # PEALD
ALM1_O3_GEN = 3                     # Powder
ALM1_O3_ROOM = 4                    # Powder
ALM1_BYPASS_PUMP = 5                # Powder


# ===================== 작은 도우미 =====================
def dword(lo: int, hi: int) -> int:
    """아래 주소가 하위 워드인 32비트 값."""
    return (int(hi) << 16) | int(lo)


def split_dword(v: int):
    """32비트 값을 (하위, 상위) 워드로."""
    v = int(v) & 0xFFFFFFFF
    return v & 0xFFFF, (v >> 16) & 0xFFFF


def to_signed16(v: int) -> int:
    """부호 없는 16비트로 받은 값을 부호 있는 값으로 해석한다(히터 온도)."""
    v = int(v) & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def to_unsigned16(v: int) -> int:
    return int(v) & 0xFFFF


def bit(word: int, n: int) -> bool:
    return bool((int(word) >> n) & 1)
