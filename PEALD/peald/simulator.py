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
MFC_DEV_ABORT_S = 10.0          # 공정 중 MFC1 편차가 이만큼 계속되면 중단
HEATER_SOFT_OT_CH = 6           # 소프트 과온 감시 채널 (CH1~6)

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
    {"key": "pc_hb_stop", "name": "PC 하트비트 멈춤 (시험)"},
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

    def __init__(self, cfg: dict, speed: float = 5.0):
        self.cfg = cfg
        self.speed = max(1.0, float(speed or 1.0))
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
        self.vent_started = None
        self.pump_started = None
        self.heater_pv = [25.0] * 12
        self.mfc_pv = [0.0] * 8
        self.o3_gen_on = False
        self.ivb_on = False
        self.bypass_pump_on = False
        self.o3_ok_since = None
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
        self.prep_start = None
        self.mfc_ok_since = None
        self.dev_bad_since = None
        self.end_reason = ""

        # --- 명령·통신 ---
        self.last_cmd_no = 0
        self.pc_hb_last = 0
        self.pc_hb_at = time.monotonic()
        self.plc_hb_at = time.monotonic()
        self.recipe_check_at = 0.0
        self._t = time.monotonic()

        # 안전 정지 래치
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
        now = time.monotonic()
        dt = min(0.5, now - self._t)
        self._t = now
        if dt <= 0:
            return
        sdt = dt * self.speed          # 물리 시간(배속)

        self._plc_heartbeat(now)
        self._watch_pc_heartbeat(now)
        self._handle_command(now)
        self._auto_clear()
        # ★ 스텝·블록 준비 시간은 sim_speed 와 무관한 실제 시간이다 —
        #   화면이 보여 주는 남은 시간과 맞아야 한다. 물리값만 배속을 따른다.
        self._sequencer(dt, now)
        self._physics(sdt, now)
        self._inputs(now)
        self._alarms(now)
        self._interlocks()
        self._state()
        self._publish()
        self._publish_seq()
        self._recipe_check(now)

    # ---------- 하트비트 ----------
    def _plc_heartbeat(self, now):
        if now - self.plc_hb_at >= 0.5:
            self.plc_hb_at = now
            self.reg[A.D_PLC_HB] = (self.reg[A.D_PLC_HB] + 1) & 0xFFFF

    def _watch_pc_heartbeat(self, now):
        hb = self.reg[A.D_PC_HB]
        # 시험용: PC 하트비트가 멈춘 상황을 흉내 낸다(PC 는 계속 쓰지만 못 본 척한다).
        if self.faults.get("pc_hb_stop"):
            self.pc_hb_last = hb
        elif hb != self.pc_hb_last:
            self.pc_hb_last = hb
            self.pc_hb_at = now
        wdt = self.reg[A.D_PRM_PC_WDT_MS] or 3000
        if (now - self.pc_hb_at) * 1000.0 > wdt:
            self._latch0(A.ALM0_PC_LINK)

    @property
    def pc_link_ok(self) -> bool:
        wdt = self.reg[A.D_PRM_PC_WDT_MS] or 3000
        return (time.monotonic() - self.pc_hb_at) * 1000.0 <= wdt

    # ---------- 명령 핸드셰이크 ----------
    def _handle_command(self, now):
        no = self.reg[A.D_CMD_NO]
        if no == self.last_cmd_no:
            return
        self.last_cmd_no = no
        code = self.reg[A.D_CMD_CODE]
        result = self._execute(code)
        self.reg[A.D_ACK_RESULT] = result
        self.reg[A.D_ACK_NO] = no           # ★ 결과를 먼저 쓰고 번호를 마지막에 쓴다
        log.debug("sim: 명령 %s(%s) → %s", code, no, result)

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
            ok, _total = self._table_check()
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
                self._process_end("운전자 즉시 중단", aborted=True)
            return A.RESULT_OK          # 멈춰 있으면 아무것도 안 하고 0

        if code == A.CMD_ALARM_ACK:
            self.reg[A.D_ALARM_NEW] = 0
            return A.RESULT_OK
        if code == A.CMD_ALARM_RESET:
            self._alarm_reset()
            return A.RESULT_OK

        if code == A.CMD_PUMP_START:
            if self._pump_blocking():
                return A.RESULT_INTERLOCK
            self.pump_req = True
            self.exh_req = True
            self.pump_started = time.monotonic()
            return A.RESULT_OK
        if code == A.CMD_PUMP_STOP:
            self.pump_req = False
            self.exh_req = False
            return A.RESULT_OK
        if code == A.CMD_VENT:
            # 펌프 모터는 그대로, 배기 요청만 내린다 → IV-E 가 닫히고 허가가 서면 VV
            self.exh_req = False
            self.vent_req = True
            self.vent_started = time.monotonic()
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
            self.ao[AO_RF] = min(self.reg[A.D_RF_SV], self.reg[A.D_PRM_RF_MAX])
            # RF 전력이 0 이면 보조 요청을 버린다
            if self.man_aux and self.ao[AO_RF] == 0:
                self.man_aux = 0
        if DEV.HAS_O3:
            self.ao[AO_O3] = min(self.reg[A.D_O3_SV], self.reg[A.D_PRM_O3_MAX])

    def _clear_manual_valve(self):
        self.man_valve = 0

    def _auto_clear(self):
        """PLC 가 스스로 지우는 것 — 내부 사본뿐이다(PC 영역은 그대로)."""
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

    def _process_start(self) -> int:
        """레시피 영역을 작업본으로 복사하고 그룹 1·블록 1 부터 시작한다.
        첫 그룹·첫 블록 적재가 틀리면 시작하지 않고 결과 3 + 알람0 b13."""
        self.work = [self.reg[a] & 0xFFFF
                     for a in range(A.RCP_SUM_BASE, A.RCP_SUM_END + 1)]
        self.group_idx = 1 if self._w(A.D_RCP_GROUP_COUNT) >= 1 else 0
        if self.group_idx and self._group_error(1):
            self._latch0(A.ALM0_RECIPE)
            return A.RESULT_RECIPE
        if self._block_error(1):
            self._latch0(A.ALM0_RECIPE)
            return A.RESULT_RECIPE

        self.running = True
        self.pause_req = False
        self.stop_req = False
        self.end_reason = ""
        self.dev_bad_since = None
        self.group_pass = 1
        self.prev_valves = 0
        # PLC 는 공정을 시작하면 수동 밸브 반영을 스스로 지운다(내부 사본만).
        self._clear_manual_valve()
        if not DEV.HAS_O3:
            # Powder 는 공정 중에도 O3 라인(수동 보조)을 유지한다.
            self.man_aux = 0
        self._load_block(1)
        return A.RESULT_OK

    def _group(self, idx: int):
        """그룹 idx(1부터)의 (시작, 끝, 반복). 없으면 None."""
        if idx < 1 or idx > self._w(A.D_RCP_GROUP_COUNT):
            return None
        b = A.D_RCP_GROUP_BASE + (idx - 1) * A.RCP_GROUP_STRIDE
        return self._w(b), self._w(b + 1), self._w(b + 2)

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
        first = self._w(base + A.RCP_BLOCK_FIRST)
        last = self._w(base + A.RCP_BLOCK_LAST)
        repeat = self._wd(base + A.RCP_BLOCK_REPEAT_LO)
        ns = self._w(A.D_RCP_STEP_COUNT)
        if first < 1 or last < first or last > ns or repeat < 1:
            return f"블록 {n} 항목이 올바르지 않습니다"
        return ""

    def _load_block(self, n: int):
        nb = self._w(A.D_RCP_BLOCK_COUNT)
        if n > nb:
            self._process_end("정상 종료")
            return
        why = self._block_error(n)
        if why:
            self._recipe_error(why)
            return
        base = A.D_RCP_BLOCK_BASE + (n - 1) * A.RCP_BLOCK_STRIDE
        first = self._w(base + A.RCP_BLOCK_FIRST)
        last = self._w(base + A.RCP_BLOCK_LAST)
        repeat = self._wd(base + A.RCP_BLOCK_REPEAT_LO)

        # MFC·장비 전용 AO 를 블록 값으로 바꾼다 (RF·O3 는 상한으로 자른다)
        for no in range(1, DEV.MFC_COUNT + 1):
            self.ao[ao_mfc(no)] = self._w(base + A.RCP_BLOCK_MFC + no - 1)
        if DEV.HAS_PCV:
            self.ao[AO_PCV] = self._w(base + A.RCP_BLOCK_PCV)
        if DEV.HAS_RF:
            # 상한이 0 이면 RF 금지
            self.ao[AO_RF] = min(self._w(base + A.RCP_BLOCK_RF), self.reg[A.D_PRM_RF_MAX])
        if DEV.HAS_O3:
            self.ao[AO_O3] = min(self._w(base + A.RCP_BLOCK_O3), self.reg[A.D_PRM_O3_MAX])

        self.blk = n
        self.cycle = 1
        self.step_no = first
        self.block_first = first
        self.block_last = last
        self.block_repeat = repeat
        self.seq_valves = 0
        self.prev_valves = 0        # 블록 준비 뒤에는 직전 출력이 전부 닫힘
        self.rf_step = False
        self.seq_state = 3          # 블록 준비
        self.prep_start = time.monotonic()
        self.mfc_ok_since = None

    @property
    def block_rf_raw(self) -> int:
        return self.ao[AO_RF] if DEV.HAS_RF else 0

    def _mfc1_dev(self) -> int:
        return abs(self.reg[A.D_MFC_PV] - self.ao[ao_mfc(1)])

    def _prep_tick(self, now):
        """블록 준비 — MFC 가 안정될 때까지 기다린다."""
        tol = self.reg[A.D_PRM_MFC_TOL]
        stable_s = self.reg[A.D_PRM_MFC_STABLE]
        timeout_s = self.reg[A.D_PRM_MFC_TIMEOUT] or 60
        waited = now - (self.prep_start or now)

        if tol == 0:
            # 감시하지 않는 설정이면 안정 판정 시간만 기다린다
            if waited >= stable_s:
                self._load_step()
            return
        if self._mfc1_dev() <= tol:
            if self.mfc_ok_since is None:
                self.mfc_ok_since = now
            if now - self.mfc_ok_since >= stable_s:
                self._load_step()
                return
        else:
            self.mfc_ok_since = None
        if waited > timeout_s:
            self._latch0(A.ALM0_MFC)
            self._process_end("MFC 안정 대기 시간 초과", aborted=True)

    def _load_step(self):
        base = A.D_RCP_STEP_BASE + (self.step_no - 1) * A.RCP_STEP_STRIDE
        t = self._wd(base + A.RCP_STEP_TIME_LO)
        if t < 20 or t > 3_276_700:
            self._recipe_error(f"스텝 {self.step_no} 시간이 범위를 벗어납니다 ({t} ms)")
            return
        valves = self._w(base + A.RCP_STEP_VALVE_LO)
        flags = self._w(base + A.RCP_STEP_FLAGS)

        # 새로 열리는 밸브가 있으면 최소 열림 시간을 보장한다
        if (valves & ~self.prev_valves) and t < self.reg[A.D_PRM_VALVE_MIN_MS]:
            t = self.reg[A.D_PRM_VALVE_MIN_MS]
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
        if not self.running:
            return
        if self.safe_stop:
            self._process_end("안전 정지 요구", aborted=True)
            return
        if self.seq_state == 3:
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
        if self.step_ms >= self.step_dur:
            self._step_done()

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
        tol = self.reg[A.D_PRM_MFC_TOL]
        if tol == 0:
            self.dev_bad_since = None
            return
        if self._mfc1_dev() > tol:
            if self.dev_bad_since is None:
                self.dev_bad_since = now
            elif now - self.dev_bad_since >= MFC_DEV_ABORT_S:
                self._latch0(A.ALM0_MFC)
                self._process_end("공정 중 MFC 편차", aborted=True)
        else:
            self.dev_bad_since = None

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
        self.blk = 0
        self.step_no = 0
        self.cycle = 0
        self.group_idx = 0
        self.group_pass = 0
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
        a0 = self.reg[A.D_ALARM0]
        return any((a0 >> b) & 1 for b in (A.ALM0_PUMP, A.ALM0_AIR, A.ALM0_CW))

    # ---------- 물리 ----------
    def _physics(self, sdt, now):
        if self._pump_blocking():
            self.pump_req = False
            self.exh_req = False
            self.vent_req = False

        # 펌프
        if self.pump_req and not self.pump_on:
            self.pump_on = True
            self.pump_run_at = now
        if not self.pump_req:
            self.pump_on = False
            self.pump_run_at = None
        pump_run = self.pump_on and self.pump_run_at is not None and (now - self.pump_run_at) >= 1.0
        pump_ok = pump_run and not self.faults["pump_alm"]

        # IV-E 출력 = 배기 요청 AND 펌프 허가 AND VV 닫힘 — 조건이 빠지면 바로 닫힌다
        ive = self.exh_req and pump_ok and not self.vv_on
        if ive != self.ive_out:
            self.ive_out = ive
            self.ive_moved_at = now

        # 벤트: 벤트 허가가 서면 VV 를 연다
        if self.vent_req:
            if self._vent_ok() and not self.vv_on:
                self.vv_on = True
            if self.base_pressure >= ATM_TORR * 0.99:
                self.vv_on = False
                self.vent_req = False
            elif self.vent_started and (now - self.vent_started) > (self.reg[A.D_PRM_VENT_TIMEOUT] or 300):
                self._latch0(A.ALM0_VENT_TIMEOUT)
                self.vent_req = False
        if not self.vent_req:
            self.vv_on = False

        # 압력
        ive_open = self.ive_out and self.ive_moved_at is not None and (now - self.ive_moved_at) >= 1.0
        # ★ 펄스·흐름 상승분을 베이스에 직접 더하면 다음 tick 의 수렴 계산이 그 값을
        #   출발점으로 삼아 펄스마다 베이스가 계단식으로 올라간다 — 따로 보관한다.
        self.pulse *= pow(2.718281828, -sdt / 0.6)
        if self.vv_on:
            self.base_pressure += (ATM_TORR - self.base_pressure) * (1 - pow(2.718281828, -sdt / VENT_TAU_S))
        elif pump_run and ive_open:
            self.base_pressure += (ULTIMATE_TORR - self.base_pressure) * (1 - pow(2.718281828, -sdt / PUMP_TAU_S))
            if (self.pump_started and self.reg[A.D_PRM_BASE_PRESS]
                    and (now - self.pump_started) > (self.reg[A.D_PRM_PUMP_TIMEOUT] or 600)
                    and self.conv.cvg.to_raw(self.base_pressure) > self.reg[A.D_PRM_BASE_PRESS]):
                self._latch0(A.ALM0_BASE_TIMEOUT)
                self.pump_started = None
        self.base_pressure = max(1.0e-4, min(ATM_TORR, self.base_pressure))
        # 흐르는 가스가 있으면 공정 압력이 베이스보다 조금 높게 유지된다
        flow_slm = sum(self.mfc_pv[:DEV.MFC_COUNT]) / 1000.0
        rise = flow_slm * FLOW_TORR_PER_SLM if (ive_open and pump_run) else 0.0
        self.pressure = max(1.0e-4, min(ATM_TORR, self.base_pressure + rise + self.pulse))

        # 공정 중에는 시퀀서가 밸브를 쥔다. 아니면 수동 반영(D04012).
        # 전구체와 반응물이 함께 요청되면 어느 쪽이든 둘 다 막는다.
        if self.running:
            req = self.seq_valves & DEV.MANUAL_VALVE_MASK
        else:
            req = self.man_valve & DEV.MANUAL_VALVE_MASK
        pre = any((req >> b) & 1 for b in DEV.PRECURSOR_VALVE_BITS)
        rea = any((req >> b) & 1 for b in DEV.REACTANT_VALVE_BITS)
        self.both_req = pre and rea
        if self.both_req:
            self._latch0(A.ALM0_BOTH_OPEN)
            for b in DEV.PRECURSOR_VALVE_BITS + DEV.REACTANT_VALVE_BITS:
                req &= ~(1 << b)
        self.valve_out = req

        # 수동 보조 반영 (명령 12 대상 비트만)
        aux_req = self.man_aux & DEV.AUX_CMD_MASK

        # 장비 전용
        if DEV.HAS_RF:
            # 공정 중에는 플래그 b1 스텝을 실행하는 동안만 RF 를 켠다
            want_rf = self.rf_step if self.running else bool(aux_req & (1 << A.AUX_RF))
            self.rf_on = want_rf and self._rf_ok()
        if DEV.HAS_O3:
            self._o3_logic(aux_req, now)

        # MFC: AO 설정을 1 s 정도로 따라간다
        for i in range(8):
            sv_raw = self.ao[ao_mfc(i + 1)] if i < DEV.MFC_COUNT else 0
            sv = self.conv.mfc.get(i + 1)
            target = sv.to_eng(sv_raw) if sv and sv.full else 0.0
            target = target or 0.0
            if i == 0 and self.faults.get("mfc1_stuck"):
                target = 0.0        # 시험: MFC1 이 막혀 현재값이 0 에 머문다
            self.mfc_pv[i] += (target - self.mfc_pv[i]) * (1 - pow(2.718281828, -sdt / MFC_TAU_S))

        # 히터: 과온 알람 래치 중에는 매 스캔 전원 묶음 = 0
        if (self.reg[A.D_ALARM0] >> A.ALM0_OT) & 1:
            self.heater_power = 0
        for ch in range(12):
            on = bool((self.heater_power >> ch) & 1)
            sv = heater_temp(self.heater_sv[ch]) if on else 25.0
            self.heater_pv[ch] += (sv - self.heater_pv[ch]) * (1 - pow(2.718281828, -sdt / HEATER_TAU_S))

        self.aux_out = self._aux_word(pump_run, ive_open, aux_req)

    def _vent_ok(self) -> bool:
        """벤트 허가(인터락 b5) = 시퀀서 동작 아님(공정 준비 포함) · IV-E 닫힘 입력 ·
        IV-E 출력 꺼짐 · 비상정지 정상."""
        ive_closed = not self.ive_out and (self.ive_moved_at is None
                                           or (time.monotonic() - self.ive_moved_at) >= 1.0)
        return (not self.running and ive_closed and not self.ive_out
                and not self.faults["emo"])

    def _rf_ok(self) -> bool:
        ilk = self.reg[A.D_INTERLOCK]
        return bool((ilk >> A.ILK_RF_OK) & 1)

    def _o3_logic(self, aux_req, now):
        """Powder: 바이패스 펌프 운전 + IV-B 열림 5 s 뒤에야 O3 허가."""
        self.ivb_on = bool(aux_req & (1 << A.AUX_IVB))
        self.bypass_pump_on = bool(aux_req & (1 << A.AUX_BYPASS_PUMP)) and not self.faults["bp_alm"]
        ready = self.ivb_on and self.bypass_pump_on
        if ready:
            if self.o3_ok_since is None:
                self.o3_ok_since = now
        else:
            self.o3_ok_since = None
        want = bool(aux_req & (1 << A.AUX_O3_GEN))
        self.o3_gen_on = want and bool((self.reg[A.D_INTERLOCK] >> A.ILK_O3_OK) & 1)

    def _aux_word(self, pump_run, ive_open, aux_req) -> int:
        w = 0
        if self.vv_on:
            w |= 1 << A.AUX_VV
        if self.ive_out:
            w |= 1 << A.AUX_IVE
        if self.pump_on:
            w |= 1 << A.AUX_PUMP
        # 경광등·부저
        a0 = self.reg[A.D_ALARM0] | self.reg[A.D_ALARM1]
        if self._critical():
            w |= 1 << A.AUX_LAMP_R
        elif a0:
            w |= 1 << A.AUX_LAMP_Y
        else:
            w |= 1 << A.AUX_LAMP_G
        if self.reg[A.D_ALARM_NEW]:
            w |= 1 << A.AUX_BUZZER
        if DEV.HAS_RF and self.rf_on:
            w |= 1 << A.AUX_RF
        if DEV.HAS_O3:
            if self.o3_gen_on:
                w |= 1 << A.AUX_O3_GEN
            if self.ivb_on:
                w |= 1 << A.AUX_IVB
            if self.bypass_pump_on:
                w |= 1 << A.AUX_BYPASS_PUMP
        return w

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
        if pump_run and not f["pump_alm"]:
            w |= 1 << A.IN0_PUMP_RUN
        if f["pump_alm"]:
            w |= 1 << A.IN0_PUMP_ALM
        settled = self.ive_moved_at is None or (now - self.ive_moved_at) >= 1.0
        if settled:
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
            if self.bypass_pump_on:
                w1 |= 1 << A.IN1_BP_RUN
            if f["bp_alm"]:
                w1 |= 1 << A.IN1_BP_ALM
        self.reg[A.D_INPUT1] = w1

    # ---------- 알람 ----------
    def _latch0(self, b):
        if not (self.reg[A.D_ALARM0] >> b) & 1:
            self.reg[A.D_ALARM0] |= 1 << b
            self.reg[A.D_ALARM_NEW] = 1

    def _latch1(self, b):
        if not (self.reg[A.D_ALARM1] >> b) & 1:
            self.reg[A.D_ALARM1] |= 1 << b
            self.reg[A.D_ALARM_NEW] = 1

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
        f = self.faults
        if f["emo"]:
            self._latch0(A.ALM0_EMO)
        if f["air"]:
            self._latch0(A.ALM0_AIR)
        if f["n2"]:
            self._latch0(A.ALM0_N2)
        if f["cw"]:
            self._latch0(A.ALM0_CW)
        if f["pump_alm"]:
            self._latch0(A.ALM0_PUMP)
        if f["leak"]:
            self._latch0(A.ALM0_LEAK)
        if self._over_temp():
            self._latch0(A.ALM0_OT)
        if f["lid"] and self.reg[A.D_STATE] in (A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING):
            self._latch0(A.ALM0_LID)
        if DEV.HAS_RF and f["rf_alm"]:
            self._latch1(A.ALM1_RF)
        if DEV.HAS_O3:
            if f["o3_alm"]:
                self._latch1(A.ALM1_O3_GEN)
            if f["o3_room"]:
                self._latch1(A.ALM1_O3_ROOM)
            if f["bp_alm"]:
                self._latch1(A.ALM1_BYPASS_PUMP)
        self.safe_stop = self._critical()

    def _critical(self) -> bool:
        return bool((self.reg[A.D_ALARM0] & DEV.CRITICAL_MASK0) or self.reg[A.D_ALARM1])

    def _alarm_reset(self):
        """원인이 사라진 알람만 지운다. PC 링크는 정상일 때만 풀린다."""
        f = self.faults
        keep0 = 0
        cause0 = {
            A.ALM0_EMO: f["emo"], A.ALM0_AIR: f["air"], A.ALM0_N2: f["n2"],
            A.ALM0_CW: f["cw"], A.ALM0_PUMP: f["pump_alm"], A.ALM0_LEAK: f["leak"],
            A.ALM0_OT: self._over_temp(), A.ALM0_PC_LINK: not self.pc_link_ok,
        }
        for b in range(16):
            if (self.reg[A.D_ALARM0] >> b) & 1 and cause0.get(b, False):
                keep0 |= 1 << b
        self.reg[A.D_ALARM0] = keep0

        keep1 = 0
        cause1 = {}
        if DEV.HAS_RF:
            cause1 = {A.ALM1_RF: f["rf_alm"]}
        if DEV.HAS_O3:
            cause1 = {A.ALM1_O3_GEN: f["o3_alm"], A.ALM1_O3_ROOM: f["o3_room"],
                      A.ALM1_BYPASS_PUMP: f["bp_alm"]}
        for b in range(16):
            if (self.reg[A.D_ALARM1] >> b) & 1 and cause1.get(b, False):
                keep1 |= 1 << b
        self.reg[A.D_ALARM1] = keep1
        self.reg[A.D_ALARM_NEW] = 0

    # ---------- 인터락 ----------
    def _interlocks(self):
        i0 = self.reg[A.D_INPUT0]
        i1 = self.reg[A.D_INPUT1]
        b = lambda w, n: bool((w >> n) & 1)  # noqa: E731
        w = 0

        basic = (b(i0, A.IN0_EMO) and b(i0, A.IN0_AIR) and b(i0, A.IN0_N2)
                 and b(i0, A.IN0_LID) and b(i0, A.IN0_CW))
        if basic:
            w |= 1 << A.ILK_BASIC
        pump = b(i0, A.IN0_PUMP_RUN) and not b(i0, A.IN0_PUMP_ALM)
        if pump:
            w |= 1 << A.ILK_PUMP
        base_raw = self.reg[A.D_PRM_BASE_PRESS]
        cvg_raw = self.conv.cvg.to_raw(self.pressure)
        vac = base_raw > 0 and cvg_raw <= base_raw
        if vac:
            w |= 1 << A.ILK_VACUUM
        if self.safe_stop:
            w |= 1 << A.ILK_SAFE_STOP_REQ
        if self.both_req:
            w |= 1 << A.ILK_BOTH_REQ

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
                     and self.reg[A.D_PRM_RF_MAX] > 0 and base_p < p <= rf_max_p)
            if rf_ok:
                w |= 1 << A.ILK_RF_OK
        if DEV.HAS_O3:
            held = (self.o3_ok_since is not None
                    and (time.monotonic() - self.o3_ok_since) >= 5.0)
            o3_ok = (basic and self.ivb_on and self.bypass_pump_on and held
                     and not b(i1, A.IN1_O3_ALM) and not b(i1, A.IN1_O3_ROOM)
                     and self.reg[A.D_PRM_O3_MAX] > 0 and not self.safe_stop)
            if o3_ok:
                w |= 1 << A.ILK_O3_OK

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
        ms = int(self.step_ms) if self.seq_state == 4 else 0
        lo, hi = A.split_dword(ms)
        self.reg[A.D_SEQ_STEP_MS] = lo
        self.reg[A.D_SEQ_STEP_MS + 1] = hi

    # ---------- 상태 영역에 반영 ----------
    def _publish(self):
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
        self.reg[A.D_TC_COMM] = 0b111
        self.reg[A.D_HEATER_ALARM] = 0
        for i in range(8):
            s = self.conv.mfc.get(i + 1)
            self.reg[A.D_MFC_PV + i] = s.to_raw(self.mfc_pv[i]) if s and s.full else 0
        self.reg[A.D_SCAN_MAX] = 12

        if DEV.HAS_PCV:
            self.reg[A.D_PCV_RAW] = self.ao[AO_PCV]
        if DEV.HAS_RF:
            fwd = self.ao[AO_RF] if self.rf_on else 0
            self.reg[A.D_RF_FWD_RAW] = fwd
            ref = int(fwd * (0.30 if self.faults["rf_ref"] else 0.03))
            self.reg[A.D_RF_REF_RAW] = ref
            lim = self.reg[A.D_PRM_RF_REF_MAX]
            if self.rf_on and lim > 0 and ref > lim:
                self._latch1(A.ALM1_RF_REF)
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
