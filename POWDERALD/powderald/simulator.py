"""
simulator.py — 내장 PLC 시뮬레이터 (Modbus TCP 서버 + 래더 동작 흉내).

실장비 없이 개발·시험하기 위한 것이다. plc.simulate 가 true 면 프로그램 안에서
127.0.0.1:sim_port 에 Modbus TCP 서버를 띄우고, PLC 링크는 그 주소에 붙는다.
★ 실장비와 똑같은 통신 코드를 탄다 — 시뮬레이터용 지름길을 만들지 않는다.
  그래야 여기서 통과한 것이 현장에서도 통과한다.

물리값(압력·온도 곡선)은 대략이어도 되지만
상태 코드·결과 코드·비트 의미는 주소표(addresses.py)와 정확히 맞춘다.

★ 래더처럼 PC 영역(D01000~D01124)은 명령이 들어올 때 '읽기만' 하고,
  실제 출력은 PLC 내부 사본(D04012~13 밸브·D04050 보조·D04120~ AO 설정·
  히터 전원 묶음·히터 목표 온도 사본)으로 만든다. PLC 가 스스로 지우는 것도 이 사본뿐이다.
  내부 로직이 PC 영역을 쓰면 예외가 난다 — 실제 PLC 와 어긋난 동작을 시험이 통과시키지 않게.

★ 래더와 같은 판단을 한다(v0.4.8) — tick 하나 = 래더 한 스캔, 순서도 래더와 같다:
    물리(시뮬레이터 전용) → 입력 이미지 → P25 하트비트·명령 → P30 인터락 → P35 알람 → P40 시퀀서 →
    P45 펌프·벤트 → P50 수동 → P60 출력 → P70 램프
  같은 스캔에서 앞 프로그램이 정한 값을 뒤 프로그램이 쓴다(예: P30 의 안전 정지 요구 → P40 중단).
  타이머는 래더 TON(입력이 이어져 켜진 시간 ≥ 설정값, 입력이 꺼지면 바로 꺼짐, 설정값 0 은 그 스캔).
  PRM 은 PC 가 쓴 값을 그대로 쓴다 — 0 을 기본값으로 바꾸지 않는다(기본값 채우기는 전원 투입 첫 스캔뿐).
  알람 리셋은 알람 워드를 모두 0 으로 만든 뒤 같은 스캔의 P35 가 원인이 남은 알람을 다시 세운다.
  시간은 clock(기본 time.monotonic)으로 잰다 — 시험은 가짜 시계를 넣는다.
"""

import time
import struct
import asyncio
import logging

from . import addresses as A
from . import device as DEV
from .convert import Converters, heater_raw, heater_temp

log = logging.getLogger(__name__)

TICK_S = 0.020                  # 20 ms 주기
REG_COUNT = 4300                # D00000 ~ D04299 (표시용 영역까지)

# PC 가 쓰는 영역 — PLC(시뮬레이터 내부 로직)는 절대 쓰지 않는다.
PC_AREA_FIRST = A.D_PC_HB       # D01000
PC_AREA_LAST = A.D_PRM_O3_MAX   # D01124

ATM_TORR = 760.0
ULTIMATE_TORR = 3.0e-3          # 이 장비가 도달할 수 있는 최저 압력
PUMP_TAU_S = 8.0
VENT_TAU_S = 5.0
ATM_INPUT_TORR = 700.0          # 이 압력을 넘으면 '챔버 대기압' 입력이 1
HEATER_TAU_S = 25.0
MFC_TAU_S = 1.0
PULSE_RISE_TORR = 0.02          # 펄스 스텝에서 잠깐 오르는 폭
FLOW_TORR_PER_SLM = 0.06        # 흐름(MFC 합)이 만드는 공정 압력 상승
TIME_EPS_MS = 1e-6              # 가짜 시계 소수 오차 여유(스텝 끝 판정 · D00026 내림)
MFC_DEV_ABORT_S = 10.0          # 공정 중 MFC1 편차가 이만큼 계속되면 중단
HEATER_SOFT_OT_CH = 6           # 소프트 과온 감시 채널 (CH1~6)
PV_R_BIT = 5                    # PV-R (PEALD 반응물 매니폴드 / Powder O3 → 챔버)
PV_B_BIT = 9                    # Powder: PV-B O3 우회 → 바이패스 펌프 (PLC 자동)

# 시뮬레이터 조작판에서 켤 수 있는 이상 입력
FAULTS = [
    {"key": "emo",       "name": "비상정지"},
    {"key": "air",       "name": "공압 저하"},
    {"key": "n2",        "name": "N2 저하"},
    {"key": "cw",        "name": "냉각수 이상"},
    {"key": "lid",       "name": "리드 열림"},
    {"key": "pump_alm",  "name": "펌프 알람"},
    {"key": "leak",      "name": "가스 누출"},
    {"key": "ot",        "name": "히터 과온"},
    {"key": "rf_notready", "name": "RF 준비 끔", "dev": "peald"},
    {"key": "rf_alm",    "name": "RF 알람", "dev": "peald"},
    {"key": "rf_ref",    "name": "반사 전력 증가", "dev": "peald"},
    {"key": "o3_alm",    "name": "O3 발생기 알람", "dev": "powderald"},
    {"key": "o3_room",   "name": "실내 O3 감지", "dev": "powderald"},
    {"key": "bp_alm",    "name": "바이패스 펌프 알람", "dev": "powderald"},
    {"key": "mfc1_stuck", "name": "MFC1 막힘 (현재값 0)"},
    {"key": "tc1_comm",  "name": "온도조절기 국번1 통신 끊김"},
    {"key": "tc2_comm",  "name": "온도조절기 국번2 통신 끊김"},
    {"key": "tc3_comm",  "name": "온도조절기 국번3 통신 끊김"},
    {"key": "pc_hb_stop", "name": "PC 하트비트 멈춤 (시험)"},
    {"key": "plc_stop",  "name": "PLC STOP (시험 — 스캔 · PLC 하트비트 멈춤)"},
    {"key": "ive_stuck", "name": "IV-E 리미트 입력 안 따라옴 (5 s 뒤 알람1 b0)"},
    {"key": "pump_nofb", "name": "펌프 운전 피드백 없음 (10 s 뒤 알람0 b5)"},
    {"key": "bp_nofb",   "name": "바이패스 펌프 운전 피드백 없음 (10 s 뒤 알람1 b5)", "dev": "powderald"},
]


def visible_faults():
    return [f for f in FAULTS if f.get("dev") in (None, DEV.KEY)]


class PcAreaWrite(RuntimeError):
    """시뮬레이터 내부 로직이 PC 영역을 쓰려 했다 — 실제 PLC 는 그러지 않는다."""


class _Regs(list):
    """레지스터 배열. 인덱스 쓰기(내부 경로)로 PC 영역을 건드리면 예외를 낸다.
    PC 영역은 Modbus 쓰기 경로(PlcSim.write)로만 바뀐다."""

    def __setitem__(self, i, v):
        if isinstance(i, slice):
            lo, hi, _ = i.indices(len(self))
            if lo <= PC_AREA_LAST and hi > PC_AREA_FIRST:
                raise PcAreaWrite(f"PC 영역 D{lo:05d}~ 을 내부에서 쓰려 했습니다")
        elif PC_AREA_FIRST <= int(i) <= PC_AREA_LAST:
            raise PcAreaWrite(f"PC 영역 D{int(i):05d} 을 내부에서 쓰려 했습니다")
        super().__setitem__(i, v)

    def pc_set(self, i, v):
        """Modbus 쓰기 경로 전용."""
        super().__setitem__(i, v)


# 명령을 평가하는 프로그램(P25 는 요청만 세운다)
SEQ_CMDS = (A.CMD_PROCESS_START, A.CMD_PAUSE, A.CMD_RESUME, A.CMD_STOP_AFTER_CYCLE, A.CMD_ABORT)
PUMP_CMDS = (A.CMD_PUMP_START, A.CMD_PUMP_STOP, A.CMD_VENT, A.CMD_ALL_CLOSE)
MANUAL_CMDS = (A.CMD_MANUAL_APPLY, A.CMD_HEATER_APPLY, A.CMD_MFC_APPLY)
ALL_CMDS = SEQ_CMDS + PUMP_CMDS + MANUAL_CMDS + (A.CMD_ALARM_ACK, A.CMD_ALARM_RESET)


class TON:
    """래더 TON — 입력이 이어져 켜진 시간이 설정값 이상이면 켜지고, 입력이 꺼지면 바로 꺼진다.
    설정값 0 이면 입력이 켜진 그 스캔에 켜진다. (100 ms 타이머 설정 = PRM × 10 → 초로는 PRM,
    1 ms 타이머 설정 = ms → 초로는 ms / 1000)"""
    __slots__ = ("since", "q")

    def __init__(self):
        self.since = None
        self.q = False

    def run(self, inp: bool, preset_s: float, now: float) -> bool:
        if not inp:
            self.since = None
            self.q = False
            return False
        if self.since is None:
            self.since = now
        self.q = (now - self.since) >= preset_s - 1e-9
        return self.q


def _ao_offsets() -> dict:
    return {d["key"]: d["offset"] for d in DEV.DISPLAY_SETPOINTS}


AO_OFF = _ao_offsets()
AO_PCV = AO_OFF.get("pcv", 0)
AO_RF = AO_OFF.get("rf", 10)
AO_O3 = AO_OFF.get("o3", 11)


def ao_mfc(no: int) -> int:
    """MFC no(1부터)의 AO 설정 위치 (D04121~)."""
    return no


class PlcSim:
    """레지스터 배열 + 20 ms 래더 동작."""

    def __init__(self, cfg: dict, speed: float = 5.0, clock=None):
        self.cfg = cfg
        self._clock = clock or time.monotonic       # 시험은 가짜 시계를 넣는다
        self.speed = max(1.0, float(speed or 1.0))
        self.device_id = DEV.DEVICE_ID      # D00019 — 시험에서 다른 장비·0 을 흉내 낼 때 바꾼다
        self.conv = Converters(cfg)
        self.reg = _Regs([0] * REG_COUNT)
        self.faults = {f["key"]: False for f in FAULTS}

        # --- PLC 내부 사본 (PC 가 쓰는 요청 영역과 따로) ---
        self.man_valve = 0          # D04012~13 수동 밸브 반영
        self.man_aux = 0            # D04050 수동 보조 반영
        self.ao = [0] * A.DISPLAY_COUNT     # D04120~ AO 설정 (PCV·MFC·RF·O3)
        self.heater_power = 0       # 히터 전원 출력 묶음 (M 영역 — PC 가 못 읽는다)
        self.heater_sv = [0] * 12   # 명령 13 때 온도조절기로 보낸 목표 온도 사본(원시값)

        # --- 물리 상태 ---
        self.pressure = ATM_TORR    # 화면·게이지에 나가는 값(베이스 + 흐름 + 펄스)
        self.base_pressure = ATM_TORR   # 펌프·벤트가 만드는 바탕 압력
        self.pulse = 0.0            # 펄스 밸브가 열릴 때 잠깐 오르는 분
        self.pump_on = False
        self.pump_run_at = None         # 펌프 기동 시각(피드백 1 s 지연)
        self.ive_out = False            # IV-E 출력
        self.ive_moved_at = None        # IV-E 동작 시각(리미트 1 s 지연)
        self.vv_on = False
        self.pump_req = False       # 펌프 모터를 돌리려는 요청
        # ★ 펌프 모터와 배기 요청(IV-E 를 열어 두려는 의도)을 나눈다.
        #   IV-E 출력은 '배기 요청 AND 펌프 허가 AND VV 닫힘' 인 동안만 켜진다.
        self.exh_req = False
        self.vent_req = False
        self.heater_pv = [25.0] * 12
        self.mfc_pv = [0.0] * 8
        self.o3_gen_on = False
        self.ivb_on = False
        self.bypass_pump_on = False     # 바이패스 펌프 출력(운전 입력은 출력 AND NOT 알람)
        self.rf_on = False
        self.valve_out = 0
        self.aux_out = 0
        self.both_req = False

        # --- 공정 시퀀서 ---
        # ★ 시작하면 레시피 영역을 통째로 작업본에 복사해 그것으로 실행한다.
        #   실행 중에 레시피 영역을 다시 써도 지금 공정에는 영향이 없다.
        self.work = [0] * A.RCP_AREA_COUNT
        self.running = False
        self.seq_state = 0
        self.blk = 0                # 현재 블록 (1부터)
        self.step_no = 0            # 표 전체 기준 스텝 번호
        self.cycle = 0
        self.group_idx = 0          # 지금 불러온 그룹 번호 (1부터, 0 = 없음)
        self.group_pass = 0
        self.step_ms = 0.0          # 현재 스텝 경과 (실제 시간)
        self.step_dur = 0           # 실효 스텝 시간
        self.step_flags = 0
        self.seq_valves = 0         # 시퀀서가 요구하는 밸브
        self.prev_valves = 0        # 직전 출력('새로 열리는 밸브' 판정용)
        self.rf_step = False
        self.pause_req = False      # 일시정지 예약 (취소 명령 없음)
        self.stop_req = False       # 사이클 후 정지 예약 (취소 명령 없음)
        self.end_reason = ""

        # --- 명령·통신 ---
        self.last_cmd_no = 0
        self.plc_hb_at = self._clock()
        self.recipe_check_at = 0.0
        self._t = self._clock()

        # --- 래더 타이머 · 래치 ---
        self.t_wdt = TON()          # P25 PC 와치독 (1 ms, PRM_PC_WDT_MS)
        self.t_air = TON()          # P30 공압 정상 꺼짐 1 s (100 ms, 10)
        self.t_n2 = TON()           # P30 N2 정상 꺼짐 1 s
        self.t_ivb = TON()          # P30 IV-B 출력 뒤 5 s (Powder O3 허가)
        self.t_rf_ref = TON()       # P35 RF 반사 초과 (1 ms, PRM_RF_REF_MS)
        self.t_mfc_to = TON()       # P40 블록 준비 시간 초과 (100 ms, PRM_MFC_TO_S × 10)
        self.t_mfc_ok = TON()       # P40 MFC 안정 판정 (100 ms, PRM_MFC_STABLE × 10)
        self.t_pump_to = TON()      # P45 펌핑 시간 초과 (100 ms, PRM_PUMP_TO_S × 10)
        self.t_vent_to = TON()      # P45 벤트 시간 초과 (100 ms, PRM_VENT_TO_S × 10)
        self.hb_seen = 0            # 마지막으로 본 PC 하트비트 값
        self.pc_link_ok = False     # P25 — 하트비트가 바뀌면 켜지고 와치독이 다 차면 꺼진다
        self.pc_trip = False        # P25 — PC 통신 끊김 트립(리셋은 PC_LINK_OK 일 때만 푼다)
        self.air_bad = False        # P30 — 1 s 이어진 공압 저하
        self.n2_bad = False
        self.o3_ok = False          # P30 ILK_O3_OK (Powder)
        self.vac_done = False       # P45 PMP_VAC_DONE
        self.pump_to_done = False   # P45 펌핑 시간 초과 타이머 출력(알람 리셋 뒤 다시 세우는 원인)
        self.rf_ref_done = False    # P35 RF 반사 타이머 출력
        self.blink = False          # PLC 1 s 클록(0.5 s 켜짐 · 0.5 s 꺼짐)
        # ★ 알람 워드는 내부 래치(M 워드)다. 스캔 어디서든 래치되지만 D00005 · D00006 · 새 알람(D00007)은
        #   P35 의 복사(행 26~36)에서만 바뀐다 — P35 뒤(P40 · P45 · P60)에 선 알람은 다음 스캔에 보인다.
        self.alm0 = 0
        self.alm1 = 0
        self.prev_alm0 = 0          # 앞 스캔 알람 워드(P35 가 복사 — 새 알람 판단)
        self.prev_alm1 = 0
        self.ack_req = False        # 명령 6 — P35 행 끝에서 D00007 = 0
        self.reset_req = False      # 명령 7 — P35 첫 행에서 알람 워드 = 0
        self.t_mfc_dev = TON()      # P40 T0032 공정 중 MFC1 편차 10 s
        self.mfc_to_done = False    # P40 T0024 출력 → 다음 스캔 P35 가 b12
        self.mfc_dev_done = False   # P40 T0032 출력 → 다음 스캔 P35 가 b12
        self.t_ive = TON()          # P35 T0026 IV-E 동작 이상 5.0 s(고정)
        self.t_pump_fb = TON()      # P45 T0025 펌프 운전 피드백 없음 10 s
        self.t_bp_fb = TON()        # P45 T0031 바이패스 펌프 운전 피드백 없음 10 s (Powder)
        self._stopped = False       # PLC STOP 결함 중이었다 — 풀리면 P00 첫 스캔
        self.abort_req = False      # 즉시 중단 요청 — P40 행 130 에서 적용
        self.cmd_req = None         # P25 가 세운 이번 스캔 명령 요청 — 그 명령의 프로그램이 평가한다
        self.vent_to_done = False   # P45 T0022 출력 → 다음 스캔 P35 행 10 이 b8
        self.ive_bad_done = False   # P45 T0026 출력 → 다음 스캔 P35 행 22 가 알람1 b0
        self.lamp_bits = 0          # P70 이 정한 램프 · 부저(M0122) — P60 행 17 이 다음 스캔 D00014 로
        self.aux_copy = 0           # P45 가 복사한 D04050(Powder 바이패스 펌프 · IV-B 는 이것으로)

        # 안전 정지 요구(P30 이 알람 워드로 정한다)
        self.safe_stop = False
        self.reg[A.D_STATE] = A.STATE_IDLE
        # 전원 투입 첫 스캔 — PLC 는 0 인 PRM 만 기본값을 넣는다(주소표의 약속).
        # ★ 운전 중 내부 로직이 PC 영역을 쓰는 일은 없다. 이것만 기동 때 한 번이다.
        for addr, val in A.PRM_PLC_DEFAULTS.items():
            if self.reg[addr] == 0:
                self.reg.pc_set(addr, val)

    # ===================== 레지스터 접근 (Modbus 서버가 쓴다) =====================
    def read(self, addr: int, count: int):
        if addr < 0 or addr + count > REG_COUNT:
            raise IndexError
        return self.reg[addr:addr + count]

    def write(self, addr: int, values):
        """Modbus 쓰기 경로 — PC(또는 시험)가 레지스터를 쓴다."""
        if addr < 0 or addr + len(values) > REG_COUNT:
            raise IndexError
        for i, v in enumerate(values):
            self.reg.pc_set(addr + i, int(v) & 0xFFFF)

    def set_fault(self, key: str, on: bool):
        if key in self.faults:
            self.faults[key] = bool(on)

    # ===================== 주기 =====================
    def tick(self):
        """래더 한 스캔."""
        now = self._clock()
        dt = min(0.5, now - self._t)
        self._t = now
        if dt <= 0:
            return
        sdt = dt * self.speed          # 물리 시간(배속)
        if self.faults.get("plc_stop"):
            # STOP — 스캔이 돌지 않는다(상태 영역 · PLC 하트비트 그대로, Modbus 는 답한다). 출력은 꺼지고
            # 물리만 돈다
            self._stopped = True
            self._outputs_off(now)
            self._physics(sdt, now)
            return
        if self._stopped:
            self._stopped = False
            self._first_scan(now)       # STOP → RUN: P00 첫 스캔, 그 스캔의 나머지 프로그램은 이어서 돈다
        # ★ 스텝·블록 준비·래더 타이머는 sim_speed 와 무관한 실제 시간이다 —
        #   화면이 보여 주는 남은 시간과 맞아야 한다. 물리값만 배속을 따른다.
        self._physics(sdt, now)         # 시뮬레이터 전용 — 앞 스캔 출력으로 물리값
        self._inputs(now)               # 입력 이미지
        self._p25(now)                  # 하트비트 · 명령
        self._interlocks(now)           # P30
        self._alarms(now)               # P35
        self._sequencer(dt, now)        # P40
        self._p45(now)                  # 펌프 · 벤트
        self._auto_clear()              # P50 수동 사본 지우기
        self._p60(now)                  # 출력
        self._p70(now)                  # 램프 · 부저
        self._state()
        self._publish()
        self._publish_seq()
        self._recipe_check(now)

    # ---------- STOP · P00 ----------
    def _outputs_off(self, now):
        """STOP 중 — 출력(디지털 · 아날로그 · 히터 전원)이 꺼진다."""
        if self.ive_out:
            self.ive_out = False
            self.ive_moved_at = now
        self.pump_on = False
        self.pump_run_at = None
        self.vv_on = False
        self.valve_out = 0
        self.aux_out = 0
        self.rf_on = False
        self.o3_gen_on = False
        self.ivb_on = False
        self.bypass_pump_on = False
        self.heater_power = 0
        self.ao = [0] * A.DISPLAY_COUNT

    def _first_scan(self, now):
        """P00 첫 스캔(STOP → RUN · 전원) — 출력 워드 M0120~M0123 0(히터 전원 포함), AO D04120 ×12 0,
        내부 M0001 ×9 0(PC 링크 OK · 트립 · 요청 · 인터락 · 시퀀서 · 알람 워드), D04000 ×54 0(시퀀서 ·
        수동 마스크 · 마지막 명령 번호 · 앞 알람 워드 · 수동 보조), D00001 ×80 0(D00000 그대로),
        D01001 → D04020 · D00002, D01000 → D04021, 0 인 PRM 만 기본값, D00001 = 1, 타이머 초기화.
        ★ 공정은 사라지고 PC 링크는 PC 하트비트가 바뀔 때까지 OK 가 아니다. STOP 중에 쓴 명령은
          실행 없이 D00002 = 그 번호 · D00003 = 0 이 된다."""
        self._outputs_off(now)
        self.man_valve = self.man_aux = 0
        self.pc_link_ok = self.pc_trip = False
        self.pump_req = self.exh_req = self.vent_req = False
        self.pause_req = self.stop_req = False
        self.running = False
        self.seq_state = 0
        self.blk = self.step_no = self.cycle = self.group_idx = self.group_pass = 0
        self.step_ms = 0.0
        self.step_dur = 0
        self.seq_valves = self.prev_valves = 0
        self.rf_step = False
        self.alm0 = self.alm1 = self.prev_alm0 = self.prev_alm1 = 0
        self.ack_req = self.reset_req = self.abort_req = False
        self.safe_stop = self.both_req = False
        self.air_bad = self.n2_bad = self.o3_ok = False
        self.vac_done = self.pump_to_done = self.rf_ref_done = False
        self.mfc_to_done = self.mfc_dev_done = False
        self.end_reason = "PLC 재시작(STOP → RUN)"
        for a in range(1, 81):
            self.reg[a] = 0
        self.last_cmd_no = self.reg[A.D_CMD_NO]
        self.reg[A.D_ACK_NO] = self.last_cmd_no
        self.hb_seen = self.reg[A.D_PC_HB]
        for addr, val in A.PRM_PLC_DEFAULTS.items():
            if self.reg[addr] == 0:
                self.reg.pc_set(addr, val)
        self.reg[A.D_STATE] = A.STATE_IDLE
        for name in ("t_wdt", "t_air", "t_n2", "t_ivb", "t_rf_ref", "t_mfc_to", "t_mfc_ok", "t_pump_to",
                     "t_vent_to", "t_mfc_dev", "t_ive", "t_pump_fb", "t_bp_fb"):
            setattr(self, name, TON())
        self.cmd_req = None
        self.vent_to_done = self.ive_bad_done = False
        self.lamp_bits = self.aux_copy = 0
        # PLC 하트비트 타이머(T0030)도 처음부터 — 첫 증가는 0.5 s 뒤. 표 검사는 다음 1 s 가장자리까지
        # D00028 · D00029 = 0
        self.plc_hb_at = now
        self.recipe_check_at = now

    # ---------- 하트비트 ----------
    def _plc_heartbeat(self, now):
        if now - self.plc_hb_at >= 0.5:
            self.plc_hb_at = now
            self.reg[A.D_PLC_HB] = (self.reg[A.D_PLC_HB] + 1) & 0xFFFF

    def _p25(self, now):
        """P25 — PLC 하트비트 · PC 와치독 · 명령.
        트립 = 와치독(하트비트가 안 바뀐 시간 ≥ PRM_PC_WDT_MS) AND PC_LINK_OK. 전원 직후와 트립 뒤에는
        하트비트가 한 번 바뀔 때까지 PC_LINK_OK = 0 이라 트립하지 않는다(느린 시작 · 쓰기를 막은 연결)."""
        self._plc_heartbeat(now)
        hb = self.reg[A.D_PC_HB]
        changed = False
        if self.faults.get("pc_hb_stop"):
            self.hb_seen = hb               # 시험: PC 는 쓰지만 못 본 척한다
        elif hb != self.hb_seen:
            self.hb_seen = hb
            changed = True
        wdt_done = self.t_wdt.run(not changed, self.reg[A.D_PRM_PC_WDT_MS] / 1000.0, now)
        if changed:
            self.pc_link_ok = True
        if wdt_done and self.pc_link_ok:
            self.pc_trip = True
        if wdt_done:
            self.pc_link_ok = False
        self._handle_command(now)



    # ---------- 명령 핸드셰이크 ----------
    def _handle_command(self, now):
        """P25 — 명령 번호가 바뀌면 D00002 = 번호 · D00003 = 0(모르는 코드 4)과 이번 스캔 요청만 세운다.
        ★ 평가와 거절 결과(1 · 2 · 3)는 그 명령의 프로그램이 같은 스캔의 P30 · P35 결과로 정한다:
          시작 · 일시정지 · 재개 · 사이클 후 정지 · 즉시 중단 = P40, 펌프 · 벤트 · 모두 닫기 = P45,
          수동 · 히터 · MFC = P50(P40 뒤의 공정 중 여부로). 확인 · 리셋은 요청만 — P35 가 처리한다."""
        no = self.reg[A.D_CMD_NO]
        if no == self.last_cmd_no:
            return
        self.last_cmd_no = no
        code = self.reg[A.D_CMD_CODE]
        self.reg[A.D_ACK_RESULT] = A.RESULT_OK if code in ALL_CMDS else A.RESULT_UNKNOWN
        self.reg[A.D_ACK_NO] = no
        if code == A.CMD_ALARM_ACK:
            self.ack_req = True
        elif code == A.CMD_ALARM_RESET:
            self.reset_req = True
        elif code in ALL_CMDS:
            self.cmd_req = code
        log.debug("sim: 명령 %s(%s) 요청", code, no)

    def _run_cmd(self, codes):
        """이 프로그램의 명령이면 지금 평가해 D00003 에 결과를 쓴다. (코드, 결과) 또는 None."""
        code = self.cmd_req
        if code is not None and code in codes:
            self.cmd_req = None
            result = self._execute(code)
            self.reg[A.D_ACK_RESULT] = result
            return code, result
        return None

    def _execute(self, code: int) -> int:
        # 시퀀서 동작 중 = 공정 준비·실행·일시정지·사이클 후 정지 예약
        running = self.running
        ilk = self.reg[A.D_INTERLOCK]

        # 공정 중에는 수동·배기 계열을 받지 않는다.
        if running and code in (9, 10, 11, 12, 13, 14):
            return A.RESULT_STATE

        if code == A.CMD_PROCESS_START:
            if running:
                return A.RESULT_STATE
            if not (ilk >> A.ILK_START_OK) & 1:
                return A.RESULT_INTERLOCK
            # 허가가 있어도 그 순간 표를 다시 검사한다(허가는 1 s 전 검사 결과다).
            # 시작 순간 검사로 D00028/29 를 다시 쓴다.
            ok, total = self._table_check()
            self.reg[A.D_RECIPE_SUM_PLC] = total
            self.reg[A.D_RECIPE_OK] = 1 if ok else 0
            if not ok:
                self._latch0(A.ALM0_RECIPE)
                return A.RESULT_RECIPE
            return self._process_start()
        if code == A.CMD_PAUSE:
            # 블록 준비·실행·사이클 후 정지 예약 중 모두 받는다. 이미 예약돼 있어도 0.
            if not running or self.seq_state == 7:
                return A.RESULT_STATE
            self.pause_req = True
            return A.RESULT_OK
        if code == A.CMD_RESUME:
            if self.seq_state != 7:
                return A.RESULT_STATE
            if self.safe_stop:
                return A.RESULT_INTERLOCK
            self._resume()
            return A.RESULT_OK
        if code == A.CMD_STOP_AFTER_CYCLE:
            if not running:
                return A.RESULT_STATE
            self.stop_req = True
            return A.RESULT_OK
        if code == A.CMD_ABORT:
            if running:
                self.abort_req = True   # 시퀀서에는 스텝 처리 뒤(행 130)에 적용한다
            return A.RESULT_OK          # 멈춰 있으면 아무것도 안 하고 0

        if code == A.CMD_ALARM_ACK:
            self.ack_req = True         # P35 행 끝에서 D00007 = 0
            return A.RESULT_OK
        if code == A.CMD_ALARM_RESET:
            self.reset_req = True       # P35 첫 행에서 알람 워드 = 0
            return A.RESULT_OK

        if code == A.CMD_PUMP_START:
            if self._pump_blocking():
                return A.RESULT_INTERLOCK
            self.pump_req = True
            self.exh_req = True
            # 래더: 펌핑 시작은 벤트 요청을 지운다 → VV 가 바로 닫히고 IV-E 가 열린다
            self.vent_req = False
            self.vv_on = False
            return A.RESULT_OK
        if code == A.CMD_PUMP_STOP:
            self.pump_req = False
            self.exh_req = False
            return A.RESULT_OK
        if code == A.CMD_VENT:
            # 펌프 모터는 그대로, 배기 요청만 내린다 → IV-E 가 닫히고 허가가 서면 VV
            self.exh_req = False
            self.vent_req = True
            return A.RESULT_OK
        if code == A.CMD_ALL_CLOSE:
            self.exh_req = False        # 펌프 모터는 그대로 둔다(사양)
            self.vent_req = False
            self.vv_on = False
            self._clear_manual_valve()
            if not (DEV.HAS_O3 and running):
                self.man_aux = 0
            return A.RESULT_OK

        if code == A.CMD_MANUAL_APPLY:
            if self.safe_stop:
                return A.RESULT_INTERLOCK
            self._manual_apply()
            return A.RESULT_OK
        if code == A.CMD_HEATER_APPLY:
            mask = DEV.HEATER_POWER_MASK
            self.heater_power = self.reg[A.D_HEATER_POWER] & mask
            self.heater_sv = [self.reg[A.D_HEATER_SV + i] for i in range(12)]
            return A.RESULT_OK
        if code == A.CMD_MFC_APPLY:
            for no in range(1, DEV.MFC_COUNT + 1):
                self.ao[ao_mfc(no)] = self.reg[A.D_MFC_SV + no - 1]
            return A.RESULT_OK
        return A.RESULT_UNKNOWN

    def _manual_apply(self):
        """명령 12 — PC 요청 영역을 내부 사본으로 한꺼번에 복사한다."""
        self.man_valve = self.reg[A.D_MANUAL_VALVE] & DEV.MANUAL_VALVE_MASK   # D04013 = 0
        self.man_aux = self.reg[A.D_MANUAL_AUX] & DEV.AUX_CMD_MASK
        if DEV.HAS_PCV:
            self.ao[AO_PCV] = self.reg[A.D_PCV_SV]
        if DEV.HAS_RF:
            self.ao[AO_RF] = self._capped(self.reg[A.D_RF_SV], A.D_PRM_RF_MAX)    # 부호 있는 비교
            # RF 전력이 0 이면 보조 요청을 버린다
            if self.man_aux and self.ao[AO_RF] == 0:
                self.man_aux = 0
        if DEV.HAS_O3:
            self.ao[AO_O3] = self._capped(self.reg[A.D_O3_SV], A.D_PRM_O3_MAX)

    def _clear_manual_valve(self):
        self.man_valve = 0

    def _auto_clear(self):
        """P50. 수동 · 히터 · MFC 명령(P40 뒤의 공정 중 여부로) → PLC 가 스스로 지우는 것 — 내부 사본뿐이다."""
        self._run_cmd(MANUAL_CMDS)
        atm = bool((self.reg[A.D_INPUT0] >> A.IN0_ATM) & 1)
        if self.safe_stop:
            self.man_valve = 0
            self.man_aux = 0
        elif atm:
            self.man_valve = 0

    # ===================== 공정 시퀀서 =====================
    def _w(self, addr: int) -> int:
        """작업본에서 한 워드."""
        i = addr - A.RCP_SUM_BASE
        return self.work[i] & 0xFFFF if 0 <= i < len(self.work) else 0

    def _wd(self, addr: int) -> int:
        return A.dword(self._w(addr), self._w(addr + 1))

    def _prm(self, addr: int) -> int:
        """PRM 을 래더 비교처럼 부호 있는 16비트로 읽는다(타이머 설정값은 이것을 쓰지 않는다)."""
        return A.to_signed16(self.reg[addr])

    def _ws(self, addr: int) -> int:
        """작업본의 한 워드를 래더 비교처럼 부호 있는 16비트로 — 32768 이상은 음수다."""
        return A.to_signed16(self._w(addr))

    def _process_start(self) -> int:
        """레시피 영역을 작업본으로 복사하고 그룹 1·블록 1 부터 시작한다.
        ★ 래더: 시작을 받아들인 뒤(SEQ_START_OK) 블록 1 을 불러오다 틀리면 같은 스캔에 중단한다 —
          수동 반영은 이미 지워졌고 D00021~24 · AO 는 블록 1 을 부르던 값, 시퀀서 8, 결과 3 + 알람0 b13."""
        self.work = [self.reg[a] & 0xFFFF
                     for a in range(A.RCP_SUM_BASE, A.RCP_SUM_END + 1)]
        # 래더: 시작 때만 블록·스텝·사이클·그룹을 지운다(FMOV 0 D04001 11). 끝에서는 지우지 않는다.
        self.blk = self.step_no = self.cycle = self.group_pass = 0
        self.group_idx = 1 if self._w(A.D_RCP_GROUP_COUNT) >= 1 else 0
        self.pause_req = False
        self.stop_req = False
        self.end_reason = ""
        self.t_mfc_dev = TON()
        self.group_pass = 1
        self.prev_valves = 0
        # PLC 는 공정을 시작하면 수동 밸브 반영을 스스로 지운다(내부 사본만).
        self._clear_manual_valve()
        if not DEV.HAS_O3:
            # Powder 는 공정 중에도 O3 라인(수동 보조)을 유지한다.
            self.man_aux = 0
        why = (self._group_error(1) if self.group_idx else "") or self._block_error(1)
        if why:
            # 블록 1 을 정상 적재와 같은 함수로 적재한 뒤 끝낸다 — PCV · O3 AO 는 블록 1 값이 남는다
            self._load_block_values(1)
            self.group_pass = 1
            self._process_end(f"레시피 값 오류 — {why}", aborted=True)
            self._latch0(A.ALM0_RECIPE)
            return A.RESULT_RECIPE
        self.running = True
        self._load_block(1)
        return A.RESULT_OK

    def _group(self, idx: int):
        """그룹 idx(1부터)의 (시작, 끝, 반복). 없으면 None."""
        if idx < 1 or idx > self._w(A.D_RCP_GROUP_COUNT):
            return None
        b = A.D_RCP_GROUP_BASE + (idx - 1) * A.RCP_GROUP_STRIDE
        return self._ws(b), self._ws(b + 1), self._ws(b + 2)

    def _group_error(self, idx: int) -> str:
        g = self._group(idx)
        if g is None:
            return ""
        start, end, rep = g
        nb = self._w(A.D_RCP_BLOCK_COUNT)
        if start < 1 or end < start or end > nb or rep < 1:
            return f"그룹 {idx} 항목이 올바르지 않습니다"
        return ""

    def _block_error(self, n: int) -> str:
        base = A.D_RCP_BLOCK_BASE + (n - 1) * A.RCP_BLOCK_STRIDE
        first = self._ws(base + A.RCP_BLOCK_FIRST)
        last = self._ws(base + A.RCP_BLOCK_LAST)
        repeat = self._wd(base + A.RCP_BLOCK_REPEAT_LO)
        ns = self._w(A.D_RCP_STEP_COUNT)
        if first < 1 or last < first or last > ns or repeat < 1:
            return f"블록 {n} 항목이 올바르지 않습니다"
        return ""

    def _load_block(self, n: int):
        nb = self._w(A.D_RCP_BLOCK_COUNT)
        if n > nb:
            # 래더: INC SEQ_BLOCK 뒤에 끝낸다 — 정상 완료면 D00021 = 블록 수 + 1
            self.blk = n
            self._process_end("정상 종료")
            return
        # ★ 래더(P40 행 130/132)는 블록 위치 · AO 를 먼저 쓰고 검사한다 — 걸리면 그 값이 남은 채 중단
        self._load_block_values(n)
        why = self._block_error(n)
        if why:
            self._recipe_error(why)
            return
        self.seq_state = 3          # 블록 준비

    def _load_block_values(self, n: int):
        """블록 n 의 위치(D00021 · D00022 · D00024 = 1)와 AO(MFC · PCV · RF · O3)를 쓴다.
        RF · O3 는 상한(PRM)보다 크면 상한 — 래더처럼 부호 있는 16비트로 비교한다."""
        base = A.D_RCP_BLOCK_BASE + (n - 1) * A.RCP_BLOCK_STRIDE
        first = self._w(base + A.RCP_BLOCK_FIRST)
        last = self._w(base + A.RCP_BLOCK_LAST)
        repeat = self._wd(base + A.RCP_BLOCK_REPEAT_LO)
        for no in range(1, DEV.MFC_COUNT + 1):
            self.ao[ao_mfc(no)] = self._w(base + A.RCP_BLOCK_MFC + no - 1)
        if DEV.HAS_PCV:
            self.ao[AO_PCV] = self._w(base + A.RCP_BLOCK_PCV)
        if DEV.HAS_RF:
            # 상한이 0 이면 RF 금지
            self.ao[AO_RF] = self._capped(self._w(base + A.RCP_BLOCK_RF), A.D_PRM_RF_MAX)
        if DEV.HAS_O3:
            self.ao[AO_O3] = self._capped(self._w(base + A.RCP_BLOCK_O3), A.D_PRM_O3_MAX)
        self.blk = n
        self.cycle = 1
        self.step_no = first
        self.block_first = first
        self.block_last = last
        self.block_repeat = repeat
        self.seq_valves = 0
        self.prev_valves = 0        # 블록 준비 뒤에는 직전 출력이 전부 닫힘
        self.rf_step = False

    def _capped(self, raw: int, prm_addr: int) -> int:
        """설정값이 상한(PRM)보다 크면 상한 — 부호 있는 16비트 비교, 결과는 원래 워드."""
        lim = self.reg[prm_addr]
        return lim if A.to_signed16(raw) > A.to_signed16(lim) else raw

    @property
    def block_rf_raw(self) -> int:
        return self.ao[AO_RF] if DEV.HAS_RF else 0

    def _mfc1_dev(self) -> int:
        return abs(self.reg[A.D_MFC_PV] - self.ao[ao_mfc(1)])

    def _prep_tick(self, now):
        """블록 준비(시퀀서 3) — 래더 P40.
        시간 초과 타이머(PRM_MFC_TO_S)는 허용 오차와 상관없이 상태 3 이면 잰다. 오차 0 이면 안정 판정은
        안정 시간만 기다리지만, 안정 시간 ≥ 시간 초과면 시간 초과가 먼저 나 b12 로 중단한다."""
        # ★ 래더: T0024(P40 행 105) 출력 → 다음 스캔 P35 행 19~20 이 b12 → 그 다음 스캔 P30 안전 정지
        #   요구로 P40 이 중단(장비 상태 6). P40 이 바로 끝내지 않는다
        self.mfc_to_done = self.t_mfc_to.run(True, self.reg[A.D_PRM_MFC_TIMEOUT], now)
        # ★ 시간 초과(행 105)가 나도 먼저 돌아가지 않는다 — 안정(행 112)이 같은 스캔에 끝나면 스텝을 적재하고,
        #   두 스캔 동안 스텝을 돈 뒤 b12 → 안전 정지로 끝난다
        tol = self._prm(A.D_PRM_MFC_TOL)
        stable_in = True if tol == 0 else self._mfc1_dev() <= tol
        if self.t_mfc_ok.run(stable_in, self.reg[A.D_PRM_MFC_STABLE], now):
            self._load_step()

    def _load_step(self):
        base = A.D_RCP_STEP_BASE + (self.step_no - 1) * A.RCP_STEP_STRIDE
        t = self._wd(base + A.RCP_STEP_TIME_LO)
        if t < 20 or t > 3_276_700:
            self._recipe_error(f"스텝 {self.step_no} 시간이 범위를 벗어납니다 ({t} ms)")
            return
        valves = self._w(base + A.RCP_STEP_VALVE_LO)
        flags = self._w(base + A.RCP_STEP_FLAGS)

        # 새로 열리는 밸브가 있으면 최소 열림 시간을 보장한다
        vmin = self._prm(A.D_PRM_VALVE_MIN_MS)      # 래더 비교는 부호 있는 16비트
        if (valves & ~self.prev_valves) and t < vmin:
            t = vmin
        if t > 60_000:
            t = (t // 100) * 100        # 100 ms 타이머 — 나머지는 버린다

        self.step_flags = flags
        self.step_dur = t
        self.step_ms = 0.0
        self.seq_valves = valves
        self.rf_step = bool(DEV.HAS_RF and (flags & A.RCP_FLAG_RF_ON))
        self.seq_state = 4          # 스텝 실행
        self._pulse_bump()

    def _sequencer(self, dt, now):
        """P40. ★ 안전 정지 요구는 이 스캔의 P30 이 정한 것 — 같은 스캔에 시퀀서도 8 이 된다."""
        ran = self._run_cmd(SEQ_CMDS)       # 시작(행 0 · 21~23 · 133~134) · 재개(행 38) 등
        if self.seq_state != 3 or not self.running:
            # 블록 준비가 아니면 준비 타이머는 꺼진다(입력이 꺼지면 바로 꺼지는 TON)
            self.t_mfc_to.run(False, 0, now)
            self.t_mfc_ok.run(False, 0, now)
            self.mfc_to_done = False
        if not (self.running and self.seq_state in (4, 7)):
            self.t_mfc_dev.run(False, 0, now)
            self.mfc_dev_done = False
        if not self.running:
            self.abort_req = False
            return
        # ★ 중단 여부는 스텝 처리 앞에서 정한다 — 래더 행 130 에서 SEQ_RUN 은 아직 서 있다(행 139 에서야 풀림).
        #   그 스캔에 정상 끝 · 사이클 후 정지로 끝나도 중단이 행 137 을 막고 행 138 이 시퀀서 8 로 만든다
        stop = self.safe_stop or self.abort_req
        if self.safe_stop:
            why = ("MFC 안정 대기 시간 초과 — 안전 정지" if self.mfc_to_done else
                   "공정 중 MFC 편차 — 안전 정지" if self.mfc_dev_done else "안전 정지 요구")
        else:
            why = "운전자 즉시 중단"
        if ran and ran[0] == A.CMD_RESUME and ran[1] == A.RESULT_OK:
            # ★ 재개 스캔(행 38)은 스텝 시간이 흐르지 않는다(행 41 TON 이 다음 스텝 적재 행 112~129 보다 앞).
            #   블록 경계에서 재개해 블록 준비(시퀀서 3)가 됐으면 준비는 그 스캔에 돈다(행 50 → 58 → 78~95 → 103 → 112)
            if self.running and self.seq_state == 3:
                self._prep_tick(now)
        else:
            self._seq_steps(dt, now)        # 행 41~129
        # ★ 안전 정지 · 즉시 중단은 스텝 처리 뒤(행 130)에 시퀀서에 적용한다
        if stop:
            if self.running:
                self._process_end(why, aborted=True)
            elif self.seq_state == 6:
                # 이 스캔에 정상 끝 · 사이클 후 정지로 끝났다 — 래더는 시퀀서 8 · 중단(D00021 은 그대로)
                self.seq_state = 8
                self.end_reason = why
        self.abort_req = False

    def _seq_steps(self, dt, now):
        """P40 행 41~129 — 블록 준비 · 스텝 시간 · 스텝 · 사이클 · 블록 넘김."""
        if self.seq_state == 3:
            # 시작 스캔 · 블록을 적재한 스캔에도 여기서 바로 준비한다(행 95 → 103 · 105 · 112) — 스텝을
            # 적재한 스캔에는 스텝 타이머(행 41)가 돌지 않는다
            self._prep_tick(now)
            return
        if self.seq_state == 7:     # 일시정지 — 시간이 흐르지 않는다
            self._watch_mfc(now)
            return
        if self.seq_state != 4:
            return

        self._watch_mfc(now)
        if not self.running:
            return
        self.step_ms += dt * 1000.0
        if self.step_ms >= self.step_dur - TIME_EPS_MS:     # 가짜 시계 소수 오차(199.999…) 여유
            self._step_done()
            # ★ 블록을 적재한 그 스캔에 블록 준비가 바로 돈다(행 95 → 103 · 105 · 112) — 블록 사이에
            #   '시퀀서 3 · 밸브 모두 닫힘' 스캔이 끼지 않는다
            if self.running and self.seq_state == 3:
                self._prep_tick(now)

    def _pulse_bump(self):
        """펄스 밸브(전구체·반응물)가 새로 열리면 압력이 잠깐 오른다."""
        pulse_bits = 0
        for v in DEV.VALVES:
            if v.get("pulse"):
                pulse_bits |= 1 << v["bit"]
        if (self.seq_valves & ~self.prev_valves) & pulse_bits:
            self.pulse += PULSE_RISE_TORR

    def _watch_mfc(self, now):
        """공정 중(블록 준비 제외, 일시정지 포함) MFC1 편차가 10 s 계속되면 중단."""
        tol = self._prm(A.D_PRM_MFC_TOL)
        # T0032(P40 행 108) — 출력은 다음 스캔 P35 가 b12 로 래치, 그 다음 스캔 안전 정지로 중단
        self.mfc_dev_done = self.t_mfc_dev.run(tol != 0 and self._mfc1_dev() > tol, MFC_DEV_ABORT_S, now)

    def _step_done(self):
        last_of_block = (self.step_no >= self.block_last)
        pause_ok = bool(self.step_flags & A.RCP_FLAG_PAUSE_OK)
        # ① 일시정지 요청 — 허용 스텝이거나 블록의 마지막 스텝에서만 멈춘다
        if self.pause_req and (pause_ok or last_of_block):
            self.pause_req = False
            self.prev_valves = 0        # 일시정지 뒤에는 직전 출력이 전부 닫힘
            self.seq_valves = 0
            self.rf_step = False
            self.seq_state = 7
            return
        self._after_step()

    def _resume(self):
        self.seq_state = 4
        self._after_step()

    def _after_step(self):
        """스텝이 끝난 뒤(또는 일시정지에서 재개한 뒤) ②~⑤."""
        self.prev_valves = self.seq_valves
        if self.step_no < self.block_last:          # ② 다음 스텝
            self.step_no += 1
            self._load_step()
            return
        # ③ 사이클 끝
        if self.stop_req:
            self._process_end("사이클 후 정지")
            return
        if self.cycle < self.block_repeat:
            self.cycle += 1
            self.step_no = self.block_first
            self._load_step()                       # 직전 출력 = 마지막 스텝
            return
        # ④ 블록 끝
        g = self._group(self.group_idx)
        if g and self.blk == g[1]:
            start, _end, rep = g
            if self.group_pass < rep:
                self.group_pass += 1
                self._load_block(start)
                return
            self.group_idx += 1
            self.group_pass = 1
            why = self._group_error(self.group_idx)
            if why:
                # ★ 래더: 그룹 진입이 거절돼도 다음 블록으로 넘어가 그 블록을 적재한 뒤 중단한다
                #   (D00021 = 새 블록 · D00022 = 그 첫 스텝 · D00023 = 1 · D00024 = 1 · PCV · O3 AO = 그 블록 값)
                if self.blk + 1 <= self._w(A.D_RCP_BLOCK_COUNT):
                    self._load_block_values(self.blk + 1)
                else:
                    # 행 65 → 67 · 74: 블록 번호는 올리고 행 80 은 적재를 건너뛴다(스텝 · 사이클은 그대로 —
                    # SEQ_CYCLE 은 행 56(DINC) · 행 92(블록 적재)에서만 쓴다)
                    self.blk += 1
                self._recipe_error(why)
                return
        self._load_block(self.blk + 1)              # ⑤ 넘으면 _load_block 이 종료 처리

    def _recipe_error(self, why: str):
        self._latch0(A.ALM0_RECIPE)
        self._process_end(f"레시피 값 오류 — {why}", aborted=True)

    def _process_end(self, reason: str, aborted: bool = False):
        """정상·중단 공통 정리."""
        self.running = False
        self.seq_state = 8 if aborted else 6
        # ★ 블록·스텝·사이클·그룹 회차는 지우지 않는다 — 래더는 다음 시작 때만 지운다
        self.group_idx = 0
        self.step_ms = 0.0
        self.step_dur = 0
        self.seq_valves = 0
        self.prev_valves = 0
        self.rf_step = False
        self.pause_req = False
        self.stop_req = False
        self.end_reason = reason
        # MFC AO = 0, PEALD RF AO = 0. PCV·O3 AO 는 마지막 값을 유지한다.
        for no in range(1, DEV.MFC_COUNT + 1):
            self.ao[ao_mfc(no)] = 0
        if DEV.HAS_RF:
            self.ao[AO_RF] = 0
        # ★ Powder 의 O3 라인(수동 보조 반영)은 그대로 둔다 — 공정이 끝났다고
        #   발생기를 갑자기 끄면 배관에 남은 O3 를 뺄 곳이 없다.

    def _pump_blocking(self) -> bool:
        """펌핑 시작 결과 1 조건 — 이 동안은 매 스캔 펌프·배기·벤트 요청을 지운다.
        ★ 비상정지는 래치된 알람이 아니라 입력(D00008 b0)을 본다."""
        if self.faults["emo"]:
            return True
        a0 = self.alm0
        return any((a0 >> b) & 1 for b in (A.ALM0_PUMP, A.ALM0_AIR, A.ALM0_CW))

    # ---------- 물리 ----------
    def _physics(self, sdt, now):
        """시뮬레이터 전용 물리 — 앞 스캔의 출력(펌프 · IV-E · VV · AO)으로 압력 · 유량 · 온도를 만든다."""
        pump_run = self.pump_on and self.pump_run_at is not None and (now - self.pump_run_at) >= 1.0
        ive_open = self.ive_out and self.ive_moved_at is not None and (now - self.ive_moved_at) >= 1.0
        # ★ 펄스·흐름 상승분을 베이스에 직접 더하면 다음 tick 의 수렴 계산이 그 값을
        #   출발점으로 삼아 펄스마다 베이스가 계단식으로 올라간다 — 따로 보관한다.
        self.pulse *= pow(2.718281828, -sdt / 0.6)
        if self.vv_on:
            self.base_pressure += (ATM_TORR - self.base_pressure) * (1 - pow(2.718281828, -sdt / VENT_TAU_S))
        elif pump_run and ive_open:
            self.base_pressure += (ULTIMATE_TORR - self.base_pressure) * (1 - pow(2.718281828, -sdt / PUMP_TAU_S))
        self.base_pressure = max(1.0e-4, min(ATM_TORR, self.base_pressure))
        # 흐르는 가스가 있으면 공정 압력이 베이스보다 조금 높게 유지된다
        flow_slm = sum(self.mfc_pv[:DEV.MFC_COUNT]) / 1000.0
        rise = flow_slm * FLOW_TORR_PER_SLM if (ive_open and pump_run) else 0.0
        self.pressure = max(1.0e-4, min(ATM_TORR, self.base_pressure + rise + self.pulse))

        # MFC: AO 설정을 1 s 정도로 따라간다
        for i in range(8):
            sv_raw = self.ao[ao_mfc(i + 1)] if i < DEV.MFC_COUNT else 0
            sv = self.conv.mfc.get(i + 1)
            target = sv.to_eng(sv_raw) if sv and sv.full else 0.0
            target = target or 0.0
            if i == 0 and self.faults.get("mfc1_stuck"):
                target = 0.0        # 시험: MFC1 이 막혀 현재값이 0 에 머문다
            self.mfc_pv[i] += (target - self.mfc_pv[i]) * (1 - pow(2.718281828, -sdt / MFC_TAU_S))

        for ch in range(12):
            on = bool((self.heater_power >> ch) & 1)
            sv = heater_temp(self.heater_sv[ch]) if on else 25.0
            self.heater_pv[ch] += (sv - self.heater_pv[ch]) * (1 - pow(2.718281828, -sdt / HEATER_TAU_S))

    # ---------- P45 펌프 · 벤트 ----------
    def _p45(self, now):
        """P45 — 펌프 · 배기(IV-E) · 벤트(VV) · DP N2.
        벤트 끝: 벤트 요청 AND 대기압 입력 → 같은 스캔에 벤트 요청을 지운다.
        PMP_VAC_DONE: 펌핑 요청 중 ILK_VAC_OK 가 되면 래치, 펌핑 요청이 없어지면 풀림.
        펌핑 시간 초과는 펌핑 요청 AND IV-E 출력 AND NOT VAC_DONE 동안만 잰다(IV-E 가 열린 때부터)."""
        if DEV.HAS_O3:
            # D04050 복사 — 보조 출력(바이패스 펌프 · IV-B · O3 발생기)은 이것으로(한 스캔 늦게).
            # ★ 모두 닫기가 D04050 을 지우는 것(P50 행 16)보다 앞이다 — 그래서 명령 평가보다 먼저 복사한다
            self.aux_copy = self.man_aux
        self._run_cmd(PUMP_CMDS)            # 펌프 시작(행 3~6) · 정지 · 벤트 · 모두 닫기
        i0 = self.reg[A.D_INPUT0]
        ilk = self.reg[A.D_INTERLOCK]
        if self._pump_blocking():
            self.pump_req = False
            self.exh_req = False
            self.vent_req = False
        # 벤트
        if self.vent_req and (i0 >> A.IN0_ATM) & 1:
            self.vent_req = False
        # T0022(행 33) — 알람 b8 은 다음 스캔 P35 행 10 이 래치한다(그 스캔에 리셋이 와도 다시 선다)
        self.vent_to_done = self.t_vent_to.run(self.vent_req, self.reg[A.D_PRM_VENT_TIMEOUT], now)
        if self.vent_to_done:
            self.vent_req = False
        self.vv_on = self.vent_req and bool((ilk >> A.ILK_VENT_OK) & 1)
        # 펌프 모터
        if self.pump_req and not self.pump_on:
            self.pump_on = True
            self.pump_run_at = now
        if not self.pump_req:
            self.pump_on = False
            self.pump_run_at = None
        pump_ok = bool((i0 >> A.IN0_PUMP_RUN) & 1) and not (i0 >> A.IN0_PUMP_ALM) & 1
        # IV-E 출력 = 배기 요청 AND 펌프 허가 AND VV 닫힘 — 조건이 빠지면 바로 닫힌다
        ive = self.exh_req and pump_ok and not self.vv_on
        if ive != self.ive_out:
            self.ive_out = ive
            self.ive_moved_at = now
        # IV-E 동작 이상 T0026(행 29~30, 5.0 s 고정): (출력 AND 열림 입력 꺼짐) OR (출력 꺼짐 AND 닫힘 입력 꺼짐)
        # — 알람1 b0 은 다음 스캔 P35 행 22 가 래치한다
        self.ive_bad_done = self.t_ive.run(
            (self.ive_out and not (i0 >> A.IN0_IVE_OPEN) & 1)
            or (not self.ive_out and not (i0 >> A.IN0_IVE_CLOSE) & 1), 5.0, now)
        # 베이스 도달 · 펌핑 시간 초과
        if not self.exh_req:
            self.vac_done = False
        elif (ilk >> A.ILK_VACUUM) & 1:
            self.vac_done = True
        self.pump_to_done = self.t_pump_to.run(self.exh_req and self.ive_out and not self.vac_done,
                                               self.reg[A.D_PRM_PUMP_TIMEOUT], now)
        # 펌프 운전 피드백(T0025, 행 23): 펌프 출력 AND 운전 입력 꺼짐 10 s → 알람0 b5.
        # 다음 스캔 펌프 요청이 지워진다(행 16~19 — _pump_blocking)
        if self.t_pump_fb.run(self.pump_on and not (i0 >> A.IN0_PUMP_RUN) & 1, 10.0, now):
            self._latch0(A.ALM0_PUMP)
        if DEV.HAS_O3:
            # 바이패스 펌프 출력(DO_BP_RUN, 행 37)과 IV-B(행 40)는 여기서 복사본으로 정한다.
            # DO_IVB = 수동 IV-B 요청 AND 바이패스 펌프 운전 입력 AND NOT 바이패스 펌프 알람 입력
            i1 = self.reg[A.D_INPUT1]
            cp = self.aux_copy & DEV.AUX_CMD_MASK
            bp_run = bool((i1 >> A.IN1_BP_RUN) & 1) and not (i1 >> A.IN1_BP_ALM) & 1
            self.bypass_pump_on = bool(cp & (1 << A.AUX_BYPASS_PUMP))
            self.ivb_on = bool(cp & (1 << A.AUX_IVB)) and bp_run
            # 바이패스 펌프 운전 피드백(T0031, 행 38~39): 이 스캔의 출력 AND 운전 입력 꺼짐 10 s → 알람1 b5
            if self.t_bp_fb.run(self.bypass_pump_on and not (i1 >> A.IN1_BP_RUN) & 1, 10.0, now):
                self._latch1(A.ALM1_BYPASS_PUMP)

    def _vent_ok(self) -> bool:
        """벤트 허가(P30 행 12, 인터락 b5) = NOT SEQ_RUN · DI_IVE_CLOSE · NOT DO_IVE · DI_ESTOP_OK —
        입력 이미지로 정한다(IV-E 리미트가 안 따라오면 열리지 않는다)."""
        i0 = self.reg[A.D_INPUT0]
        return (not self.running and bool((i0 >> A.IN0_IVE_CLOSE) & 1) and not self.ive_out
                and bool((i0 >> A.IN0_EMO) & 1))

    def _p60(self, now):
        """P60 — 출력. 밸브 요청 = (시퀀서 동작 중이면 시퀀서 마스크, 아니면 D04012) AND 밸브 마스크."""
        # ★ 동시 요청(행 0~9, 13): 밸브 요청 = 공정 중이면 시퀀스 마스크, 아니면 수동 마스크(P50 이 이번
        #   스캔에 지운 뒤) & 허용 마스크. ILK_PR_CONFLICT(D00004 b6)는 여기서 정하고 같은 P60 행 13 이
        #   D00004 로 내보낸다(그 스캔에 보인다). 알람0 b15 는 여기서 SET — 공개는 다음 스캔 P35
        if self.running:
            req = self.seq_valves & DEV.MANUAL_VALVE_MASK
        else:
            req = self.man_valve & DEV.MANUAL_VALVE_MASK
        pre = any((req >> b) & 1 for b in DEV.PRECURSOR_VALVE_BITS)
        rea = any((req >> b) & 1 for b in DEV.REACTANT_VALVE_BITS)
        self.both_req = pre and rea
        if self.both_req:
            self.reg[A.D_INTERLOCK] |= 1 << A.ILK_BOTH_REQ
        else:
            self.reg[A.D_INTERLOCK] &= ~(1 << A.ILK_BOTH_REQ) & 0xFFFF
        ilk = self.reg[A.D_INTERLOCK]
        if self.both_req:
            # 전구체와 반응물이 함께 요청되면 동시 요청 — 모든 밸브 출력 0 + 알람0 b15
            self._latch0(A.ALM0_BOTH_OPEN)
            out = 0
        elif not (ilk >> A.ILK_VALVE_OK) & 1:
            # 공정 밸브 허가(인터락 b4)가 없으면 모든 밸브 출력 0
            # (펌프 정지·IV-E 닫힘·안전 정지 요구·챔버 대기압) — 화면에는 '대기'
            out = 0
        else:
            out = req

        # 수동 보조 반영 (명령 12 대상 비트만)
        aux_req = self.man_aux & DEV.AUX_CMD_MASK
        if DEV.HAS_RF:
            # RF = ((동작 중 AND RF 스텝) OR (멈춤 AND D04050 b8)) AND RF 허가(인터락 b8)
            want_rf = self.rf_step if self.running else bool(aux_req & (1 << A.AUX_RF))
            self.rf_on = want_rf and bool((ilk >> A.ILK_RF_OK) & 1)
        if DEV.HAS_O3:
            # ★ 바이패스 펌프 · IV-B 는 P45 가 정했다. O3 발생기도 P45 의 D04050 복사본(P60 행 9 의 M00219)으로 —
            #   명령 12 · 모두 닫기 · 안전 정지 요구 한 스캔 뒤에 바뀐다
            cp = self.aux_copy & DEV.AUX_CMD_MASK
            self.o3_gen_on = bool(cp & (1 << A.AUX_O3_GEN)) and self.o3_ok
            # 래더 P60 순서 그대로:
            #   렁 28 — 공정 밸브 허가(인터락 b4)가 없거나 동시 요청이면 밸브 요청 = 0 (위 out)
            #   렁 35 — '걸러진' 요청에서 PV-R(b5) 만 다시 본다 (VLV_TMP2 = 요청 AND h0020)
            #   렁 40 — 발생기 출력 AND VLV_TMP2 = 0 이면 요청 OR h0200 → PV-B(b9) 열림
            # ★ 그래서 PV-R 을 요청했어도 허가가 없거나 동시 요청이면 PV-B 가 열린다
            #   (발생기가 도는 동안 O3 를 바이패스로 뺀다). 허가 판단 뒤에 더하므로 허가 없이도 열린다.
            if self.o3_gen_on and not (out & (1 << PV_R_BIT)):
                out |= 1 << PV_B_BIT
        self.valve_out = out
        # 보조 출력 워드(D00014) — 출력은 이 스캔 것, 램프 · 부저는 P70 이 앞 스캔에 정한 M0122(행 17 복사)
        w = self.lamp_bits
        if self.vv_on:
            w |= 1 << A.AUX_VV
        if self.ive_out:
            w |= 1 << A.AUX_IVE
        if self.pump_on:
            w |= (1 << A.AUX_PUMP) | (1 << A.AUX_PUMP_N2)
        if DEV.HAS_RF and self.rf_on:
            w |= 1 << A.AUX_RF
        if DEV.HAS_O3:
            if self.o3_gen_on:
                w |= 1 << A.AUX_O3_GEN
            if self.ivb_on:
                w |= 1 << A.AUX_IVB
            if self.bypass_pump_on:
                w |= 1 << A.AUX_BYPASS_PUMP
        self.aux_out = w
        # 히터: 과온 알람 래치 중에는 매 스캔 전원 묶음 = 0
        if (self.alm0 >> A.ALM0_OT) & 1:
            self.heater_power = 0

    def _p70(self, now):
        """P70 — 램프 · 부저 (보조 출력 비트).
        ALM_ANY = 알람 워드 0 또는 1 ≠ 0. R = ALM_ANY AND (새 알람 D00007 = 0 이면 켜짐, 1 이면 깜빡임).
        G = SEQ_RUN AND NOT 일시정지. Y = (NOT SEQ_RUN AND NOT ALM_ANY) OR (일시정지 AND 깜빡임).
        부저 = D00007 = 1. 깜빡임은 PLC 1 s 클록(0.5 s 켜짐 · 0.5 s 꺼짐). DP N2 는 DP 운전 출력을 따른다."""
        self.blink = (now % 1.0) < 0.5
        alm_any = bool(self.alm0 or self.alm1)
        new = bool(self.reg[A.D_ALARM_NEW])
        paused = self.running and self.seq_state == 7
        w = 0
        if alm_any and (not new or self.blink):
            w |= 1 << A.AUX_LAMP_R
        if self.running and not paused:
            w |= 1 << A.AUX_LAMP_G
        if (not self.running and not alm_any) or (paused and self.blink):
            w |= 1 << A.AUX_LAMP_Y
        if new:
            w |= 1 << A.AUX_BUZZER
        self.lamp_bits = w          # M0122 — 다음 스캔 P60 이 D00014 로 내보낸다







    # ---------- 입력 ----------
    def _inputs(self, now):
        f = self.faults
        w = 0
        if not f["emo"]:
            w |= 1 << A.IN0_EMO
        if not f["air"]:
            w |= 1 << A.IN0_AIR
        if not f["n2"]:
            w |= 1 << A.IN0_N2
        if not f["cw"]:
            w |= 1 << A.IN0_CW
        if not f["lid"]:
            w |= 1 << A.IN0_LID
        if self.pressure >= ATM_INPUT_TORR:
            w |= 1 << A.IN0_ATM
        pump_run = self.pump_on and self.pump_run_at is not None and (now - self.pump_run_at) >= 1.0
        if pump_run and not f["pump_alm"] and not f["pump_nofb"]:
            w |= 1 << A.IN0_PUMP_RUN
        if f["pump_alm"]:
            w |= 1 << A.IN0_PUMP_ALM
        settled = self.ive_moved_at is None or (now - self.ive_moved_at) >= 1.0
        if settled and not f["ive_stuck"]:
            w |= (1 << A.IN0_IVE_OPEN) if self.ive_out else (1 << A.IN0_IVE_CLOSE)
        if f["leak"]:
            w |= 1 << A.IN0_LEAK
        if f["ot"]:
            w |= 1 << A.IN0_OT
        w |= 1 << A.IN0_SCRUBBER
        self.reg[A.D_INPUT0] = w

        w1 = 0
        if DEV.HAS_RF:
            if not f["rf_notready"]:
                w1 |= 1 << A.IN1_RF_READY
            if f["rf_alm"]:
                w1 |= 1 << A.IN1_RF_ALM
        if DEV.HAS_O3:
            if self.o3_gen_on:
                w1 |= 1 << A.IN1_O3_RUN
            if f["o3_alm"]:
                w1 |= 1 << A.IN1_O3_ALM
            if f["o3_room"]:
                w1 |= 1 << A.IN1_O3_ROOM
            # 바이패스 펌프 운전 입력 = 출력 AND 펌프 알람 아님
            if self.bypass_pump_on and not f["bp_alm"] and not f.get("bp_nofb"):
                w1 |= 1 << A.IN1_BP_RUN
            if f["bp_alm"]:
                w1 |= 1 << A.IN1_BP_ALM
        self.reg[A.D_INPUT1] = w1

    # ---------- 알람 ----------
    def _latch0(self, b):
        """내부 알람 워드에 래치 — D00005 · D00007 은 P35 의 복사에서만 바뀐다."""
        self.alm0 |= 1 << b

    def _latch1(self, b):
        self.alm1 |= 1 << b

    def _over_temp(self) -> bool:
        """과온 = 과온 스위치 입력 OR (CH1~6 중 한계>0 이고 현재값>한계).
        한계 0 인 채널은 소프트 감시만 안 할 뿐 전원을 막지 않는다."""
        if self.faults["ot"]:
            return True
        for ch in range(HEATER_SOFT_OT_CH):
            lim = heater_temp(self.reg[A.D_PRM_HEATER_MAX + ch])
            if lim > 0 and self.heater_pv[ch] > lim:
                return True
        return False

    def _alarms(self, now):
        """P35. 첫 행: 리셋(명령 7)이면 알람 워드 둘 = 0(PC 통신 트립은 PC_LINK_OK 일 때만 푼다).
        그다음 원인(입력 · 다 찬 타이머 · 트립 · 공정 중 조건)이 있으면 래치 — 원인이 남은 알람은 같은 스캔에
        다시 선다. 끝(행 26~36): D00005 · D00006 = 워드, 새 알람 = 워드 & ~앞 스캔 워드 → D00007 = 1,
        앞 워드 = 워드, 확인 · 리셋이면 D00007 = 0 — 원인이 남은 채 리셋하면 D00007 = 0(부저 끔 · 적색 켜짐)."""
        if self.reset_req:
            self.alm0 = self.alm1 = 0
            if self.pc_link_ok:
                self.pc_trip = False
        i0, i1 = self.reg[A.D_INPUT0], self.reg[A.D_INPUT1]
        ilk = self.reg[A.D_INTERLOCK]
        b = lambda w, n: bool((w >> n) & 1)  # noqa: E731
        if self.mfc_to_done or self.mfc_dev_done:       # 행 19~20 — P40 의 T0024 · T0032(앞 스캔)
            self._latch0(A.ALM0_MFC)
        if self.ive_bad_done:                           # 행 22 — P45 T0026(앞 스캔)
            self._latch1(A.ALM1_IVE)
        if self.vent_to_done:                           # 행 10 — P45 T0022(앞 스캔)
            self._latch0(A.ALM0_VENT_TIMEOUT)
        if not b(i0, A.IN0_EMO):
            self._latch0(A.ALM0_EMO)
        if self.air_bad:
            self._latch0(A.ALM0_AIR)
        if self.n2_bad:
            self._latch0(A.ALM0_N2)
        if not b(i0, A.IN0_CW):
            self._latch0(A.ALM0_CW)
        if b(i0, A.IN0_PUMP_ALM):
            self._latch0(A.ALM0_PUMP)
        if b(i0, A.IN0_LEAK):
            self._latch0(A.ALM0_LEAK)
        if self._over_temp():
            self._latch0(A.ALM0_OT)
        if self.pc_trip:
            self._latch0(A.ALM0_PC_LINK)
        if self.pump_to_done:
            self._latch0(A.ALM0_BASE_TIMEOUT)
        # 리드: SEQ_RUN(시작 순간부터 끝까지 — 블록 준비 포함) AND NOT 리드 닫힘
        if self.running and not b(i0, A.IN0_LID):
            self._latch0(A.ALM0_LID)
        if DEV.HAS_RF:
            if b(i1, A.IN1_RF_ALM):
                self._latch1(A.ALM1_RF)
            # 반사: DO_RF_ON AND AI_RF_REF > PRM_RF_REF_MAX 가 PRM_RF_REF_MS 이어지면 (한계 > 0 조건 없음)
            over = self.rf_on and self.reg[A.D_RF_REF_RAW] > self._prm(A.D_PRM_RF_REF_MAX)
            self.rf_ref_done = self.t_rf_ref.run(over, self.reg[A.D_PRM_RF_REF_MS] / 1000.0, now)
            if self.rf_ref_done:
                self._latch1(A.ALM1_RF_REF)
        if DEV.HAS_O3:
            if b(i1, A.IN1_O3_ALM):
                self._latch1(A.ALM1_O3_GEN)
            if b(i1, A.IN1_O3_ROOM):
                self._latch1(A.ALM1_O3_ROOM)
            if b(i1, A.IN1_BP_ALM):
                self._latch1(A.ALM1_BYPASS_PUMP)
            # SEQ_RUN AND NOT ILK_O3_OK — P30 이 먼저 돌아 SEQ_RUN 이 아직 1 인 스캔에서 판단한다.
            # 공정 중 안전 정지(어떤 중대 알람이든) · 허가만 빠져도 b3 → 중대 → 다음 스캔에 중단
            if self.running and not b(ilk, A.ILK_O3_OK):
                self._latch1(A.ALM1_O3_GEN)
        self._publish_alarms()

    def _publish_alarms(self):
        """P35 행 26~36 — 알람 워드 공개 · 새 알람 판단 · 확인 · 리셋."""
        self.reg[A.D_ALARM0] = self.alm0
        self.reg[A.D_ALARM1] = self.alm1
        if (self.alm0 & ~self.prev_alm0) or (self.alm1 & ~self.prev_alm1):
            self.reg[A.D_ALARM_NEW] = 1
        self.prev_alm0, self.prev_alm1 = self.alm0, self.alm1
        if self.ack_req or self.reset_req:
            self.reg[A.D_ALARM_NEW] = 0
        self.ack_req = self.reset_req = False

    def _critical(self) -> bool:
        return bool((self.alm0 & DEV.CRITICAL_MASK0) or self.alm1)

    # ---------- 인터락 ----------
    def _interlocks(self, now=None):
        """P30. 안전 정지 요구(행 5~9) = 비상정지 입력 꺼짐 OR PC 통신 트립 OR (알람0 & 0xD27F) ≠ 0
        OR 알람1 ≠ 0 — 비상정지를 누른 채 리셋해도 6 이 유지된다."""
        now = self._clock() if now is None else now
        i0 = self.reg[A.D_INPUT0]
        i1 = self.reg[A.D_INPUT1]
        b = lambda w, n: bool((w >> n) & 1)  # noqa: E731
        w = 0
        self.safe_stop = (not b(i0, A.IN0_EMO)) or self.pc_trip or self._critical()
        # 공압 · N2: 정상 입력이 꺼진 상태가 1 s 이어져야(100 ms 타이머 10) 빠진다 — 짧은 떨림은 무시
        self.air_bad = self.t_air.run(not b(i0, A.IN0_AIR), 1.0, now)
        self.n2_bad = self.t_n2.run(not b(i0, A.IN0_N2), 1.0, now)

        basic = (b(i0, A.IN0_EMO) and not self.air_bad and not self.n2_bad
                 and b(i0, A.IN0_LID) and b(i0, A.IN0_CW))
        if basic:
            w |= 1 << A.ILK_BASIC
        pump = b(i0, A.IN0_PUMP_RUN) and not b(i0, A.IN0_PUMP_ALM)
        if pump:
            w |= 1 << A.ILK_PUMP
        base_raw = self._prm(A.D_PRM_BASE_PRESS)
        cvg_raw = self.conv.cvg.to_raw(self.pressure)
        vac = base_raw > 0 and cvg_raw <= base_raw
        if vac:
            w |= 1 << A.ILK_VACUUM
        if self.safe_stop:
            w |= 1 << A.ILK_SAFE_STOP_REQ

        valve_ok = (basic and pump and b(i0, A.IN0_IVE_OPEN)
                    and not self.safe_stop and not b(i0, A.IN0_ATM))
        if valve_ok:
            w |= 1 << A.ILK_VALVE_OK
        if self._vent_ok():
            w |= 1 << A.ILK_VENT_OK

        rf_ok = o3_ok = True
        if DEV.HAS_RF:
            p = self.pressure
            rf_max_p = self.conv.cvg.to_torr(self.reg[A.D_PRM_RF_MAX_PRESS]) or 0
            base_p = self.conv.cvg.to_torr(base_raw) or 0
            rf_ok = (valve_ok and b(i1, A.IN1_RF_READY) and not b(i1, A.IN1_RF_ALM)
                     and self._prm(A.D_PRM_RF_MAX) > 0 and base_p < p <= rf_max_p)
            if rf_ok:
                w |= 1 << A.ILK_RF_OK
        if DEV.HAS_O3:
            # ILK_O3_OK = 기본 AND IV-B 출력 AND 바이패스 펌프 운전 입력 AND NOT 바이패스 펌프 알람 입력
            #   AND IV-B 출력 뒤 5 s AND NOT O3 발생기 알람 AND NOT 실내 O3 AND PRM_O3_MAX > 0 AND NOT SAFE_REQ
            held = self.t_ivb.run(self.ivb_on, 5.0, now)
            o3_ok = (basic and self.ivb_on and b(i1, A.IN1_BP_RUN) and not b(i1, A.IN1_BP_ALM) and held
                     and not b(i1, A.IN1_O3_ALM) and not b(i1, A.IN1_O3_ROOM)
                     and self._prm(A.D_PRM_O3_MAX) > 0 and not self.safe_stop)
            self.o3_ok = o3_ok
            if o3_ok:
                w |= 1 << A.ILK_O3_OK

        # 동시 요청(b6)은 P60 이 정해 같은 스캔에 내보낸다
        start_ok = valve_ok and vac and self.pc_link_ok and bool(self.reg[A.D_RECIPE_OK])
        if DEV.HAS_O3:
            start_ok = start_ok and o3_ok
        if start_ok:
            w |= 1 << A.ILK_START_OK
        self.reg[A.D_INTERLOCK] = w

    def _state(self):
        """장비 상태 — 뒤쪽이 우선한다(안전 정지 > 일시정지 > …)."""
        if self.safe_stop:
            self.reg[A.D_STATE] = A.STATE_SAFE_STOP
        elif not self.running:
            self.reg[A.D_STATE] = A.STATE_IDLE
        elif self.seq_state == 7:
            self.reg[A.D_STATE] = A.STATE_PAUSE
        elif self.stop_req:
            self.reg[A.D_STATE] = A.STATE_STOPPING
        elif self.seq_state == 3:
            self.reg[A.D_STATE] = A.STATE_READY
        else:
            self.reg[A.D_STATE] = A.STATE_RUN

    def _publish_seq(self):
        self.reg[A.D_SEQ_STATE] = self.seq_state
        self.reg[A.D_SEQ_BLOCK] = self.blk
        self.reg[A.D_SEQ_STEP] = self.step_no
        self.reg[A.D_SEQ_GROUP_PASS] = self.group_pass
        lo, hi = A.split_dword(self.cycle)
        self.reg[A.D_SEQ_BLOCK_PASS] = lo
        self.reg[A.D_SEQ_BLOCK_PASS + 1] = hi
        # 스텝 경과는 스텝 실행 중에만 의미가 있다
        # D00026 — 행 153 은 T1000 의 지난 ms 를 내림으로, 60 s 넘는 스텝은 행 155 가 T0010 × 100(100 ms 단위)으로
        #   공개한다. 가짜 시계 소수 오차(19.999…)는 아주 작은 여유로 푼다
        if self.seq_state == 4:
            ms = int(self.step_ms + TIME_EPS_MS)
            if self.step_dur > 60_000:
                ms = (ms // 100) * 100
        else:
            ms = 0
        lo, hi = A.split_dword(ms)
        self.reg[A.D_SEQ_STEP_MS] = lo
        self.reg[A.D_SEQ_STEP_MS + 1] = hi

    # ---------- 상태 영역에 반영 ----------
    def _publish(self):
        self.reg[A.D_DEVICE_ID] = self.device_id & 0xFFFF
        self.reg[A.D_VALVE_OUT] = self.valve_out
        self.reg[A.D_AUX_OUT] = self.aux_out
        self.reg[A.D_CVG_RAW] = self.conv.cvg.to_raw(self.pressure)
        if self.conv.cm.installed:
            self.reg[A.D_CM_RAW] = self.conv.cm.to_raw(self.pressure)
        for ch in range(12):
            self.reg[A.D_HEATER_PV + ch] = heater_raw(self.heater_pv[ch])
            on = bool((self.heater_power >> ch) & 1)
            sv = heater_temp(self.heater_sv[ch])
            gap = max(0.0, sv - self.heater_pv[ch]) if on else 0.0
            self.reg[A.D_HEATER_OUT + ch] = int(min(100, gap * 3)) if on else 0
        comm = 0b111
        for i in range(3):
            if self.faults.get(f"tc{i + 1}_comm"):
                comm &= ~(1 << i)
        self.reg[A.D_TC_COMM] = comm
        self.reg[A.D_HEATER_ALARM] = 0
        for i in range(8):
            s = self.conv.mfc.get(i + 1)
            self.reg[A.D_MFC_PV + i] = s.to_raw(self.mfc_pv[i]) if s and s.full else 0
        self.reg[A.D_SCAN_MAX] = 0      # 래더가 아직 쓰지 않는다(D00080) — 시뮬레이터도 0

        if DEV.HAS_PCV:
            self.reg[A.D_PCV_RAW] = self.ao[AO_PCV]
        if DEV.HAS_RF:
            fwd = self.ao[AO_RF] if self.rf_on else 0
            self.reg[A.D_RF_FWD_RAW] = fwd
            ref = int(fwd * (0.30 if self.faults["rf_ref"] else 0.03))
            self.reg[A.D_RF_REF_RAW] = ref          # 반사 판정은 다음 스캔 P35 가 이 AI 로
        if DEV.HAS_O3:
            self.reg[A.D_O3_RAW] = self.ao[AO_O3] if self.o3_gen_on else 0

        # 반영 영역 — PLC 내부 사본 그대로
        lo, hi = A.split_dword(self.man_valve)
        self.reg[A.D_APPLIED_VALVE] = lo
        self.reg[A.D_APPLIED_VALVE + 1] = hi
        self.reg[A.D_APPLIED_AUX] = self.man_aux
        for i in range(A.DISPLAY_COUNT):
            self.reg[A.DISPLAY_BASE + i] = self.ao[i] & 0xFFFF

    # ---------- 레시피 표 검사 ----------
    def _table_check(self):
        """(통과, PLC 합계). 레시피 영역(D02000~)을 그대로 검사한다."""
        total = 0
        for a in range(A.RCP_SUM_BASE, A.RCP_SUM_END + 1):
            if a == A.D_RCP_SUM:
                continue
            total = (total + self.reg[a]) & 0xFFFF
        ns = self.reg[A.D_RCP_STEP_COUNT]
        nb = self.reg[A.D_RCP_BLOCK_COUNT]
        ng = self.reg[A.D_RCP_GROUP_COUNT]
        ok = (total == self.reg[A.D_RCP_SUM] and 1 <= ns <= A.RCP_STEP_MAX
              and 1 <= nb <= A.RCP_BLOCK_MAX and ng <= A.RCP_GROUP_MAX)
        return ok, total

    def _recipe_check(self, now):
        """1 s 주기 표 검사 — 시퀀서가 멈춰 있을 때(대기·안전 정지 포함)만 갱신한다.
        ★ 알람은 래치하지 않는다 — PC 가 올리는 도중의 반쪽 표로 헛알람이 뜬다.
          알람은 공정 시작 때 다시 검사해 불합격이면 건다."""
        if self.running:
            return
        if now - self.recipe_check_at < 1.0:
            return
        self.recipe_check_at = now
        ok, total = self._table_check()
        self.reg[A.D_RECIPE_SUM_PLC] = total
        self.reg[A.D_RECIPE_OK] = 1 if ok else 0


# ===================== Modbus TCP 서버 =====================
class SimServer:
    """PlcSim 을 Modbus TCP 로 노출한다. 실장비와 같은 통신 경로를 쓰기 위해서다."""

    def __init__(self, sim: PlcSim, host: str = "127.0.0.1", port: int = 15000):
        self.sim = sim
        self.host = host
        self.port = int(port)
        self._server = None
        self._task = None
        self._clients = set()       # 붙어 있는 연결. 종료할 때 직접 끊는다.
        self.error = ""             # 시작 실패 사유(포트 사용 중 등)

    async def start(self) -> bool:
        """시작한다. 포트가 이미 쓰이면 예외를 던지지 않고 False 를 돌려준다.
        ★ 시뮬레이터가 못 떠도 프로그램은 떠야 한다 — 화면에서 원인을 볼 수 있어야 한다."""
        try:
            self._server = await asyncio.start_server(self._client, self.host, self.port)
        except OSError as e:
            self.error = f"시뮬레이터 포트를 열지 못했습니다 ({self.host}:{self.port}) — {e}"
            log.error("%s", self.error)
            return False
        self._task = asyncio.create_task(self._loop())
        log.info("시뮬레이터 시작: %s:%s", self.host, self.port)
        return True

    async def stop(self):
        """★ 남아 있는 연결을 직접 끊는다. server.wait_closed() 는 열린 연결이 있으면
        끝나지 않아 프로그램 종료가 멈춘다."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for w in list(self._clients):
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
            except Exception:  # noqa: BLE001
                pass

    async def _loop(self):
        """20 ms 주기. ★ 예외가 나도 멈추지 않는다 — 멈추면 PLC 가 사라진 것처럼 보인다."""
        while True:
            try:
                self.sim.tick()
            except Exception as e:  # noqa: BLE001
                log.error("시뮬레이터 tick 오류(계속 진행): %s", e)
            await asyncio.sleep(TICK_S)

    async def _client(self, reader, writer):
        self._clients.add(writer)
        try:
            while True:
                head = await reader.readexactly(7)
                tid, proto, length, unit = struct.unpack(">HHHB", head)
                body = await reader.readexactly(max(0, length - 1))
                resp = self._handle(body)
                out = struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp
                writer.write(out)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    def _handle(self, pdu: bytes) -> bytes:
        if not pdu:
            return b"\x80\x01"
        fc = pdu[0]
        try:
            if fc == 3:
                addr, count = struct.unpack(">HH", pdu[1:5])
                if count < 1 or count > 125:
                    return bytes([fc | 0x80, 3])
                vals = self.sim.read(addr, count)
                return (bytes([fc, count * 2])
                        + struct.pack(">" + "H" * count, *[v & 0xFFFF for v in vals]))
            if fc == 6:
                addr, val = struct.unpack(">HH", pdu[1:5])
                self.sim.write(addr, [val])
                return pdu[:5]
            if fc == 16:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                vals = struct.unpack(">" + "H" * count, pdu[6:6 + nbytes])
                self.sim.write(addr, vals)
                return struct.pack(">BHH", fc, addr, count)
        except IndexError:
            return bytes([fc | 0x80, 2])        # 잘못된 주소
        except Exception:  # noqa: BLE001
            return bytes([fc | 0x80, 4])        # 내부 오류
        return bytes([fc | 0x80, 1])            # 지원하지 않는 기능
