"""
simulator.py — 내장 PLC 시뮬레이터 (Modbus TCP 서버 + 래더 동작 흉내).

실장비 없이 개발·시험하기 위한 것이다. plc.simulate 가 true 면 프로그램 안에서
127.0.0.1:sim_port 에 Modbus TCP 서버를 띄우고, PLC 링크는 그 주소에 붙는다.
★ 실장비와 똑같은 통신 코드를 탄다 — 시뮬레이터용 지름길을 만들지 않는다.
  그래야 여기서 통과한 것이 현장에서도 통과한다.

물리값(압력·온도 곡선)은 대략이어도 되지만
상태 코드·결과 코드·비트 의미는 주소표(addresses.py)와 정확히 맞춘다.
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

ATM_TORR = 760.0
ULTIMATE_TORR = 3.0e-3          # 이 장비가 도달할 수 있는 최저 압력
PUMP_TAU_S = 8.0
VENT_TAU_S = 5.0
ATM_INPUT_TORR = 700.0          # 이 압력을 넘으면 '챔버 대기압' 입력이 1
HEATER_TAU_S = 25.0
MFC_TAU_S = 1.0

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
    {"key": "pc_hb_stop", "name": "PC 하트비트 멈춤 (시험)"},
]


def visible_faults():
    return [f for f in FAULTS if f.get("dev") in (None, DEV.KEY)]


class PlcSim:
    """레지스터 배열 + 20 ms 래더 동작."""

    def __init__(self, cfg: dict, speed: float = 5.0):
        self.cfg = cfg
        self.speed = max(1.0, float(speed or 1.0))
        self.conv = Converters(cfg)
        self.reg = [0] * REG_COUNT
        self.faults = {f["key"]: False for f in FAULTS}

        # --- 물리 상태 ---
        self.pressure = ATM_TORR
        self.pump_on = False
        self.pump_run_at = None         # 펌프 기동 시각(피드백 1 s 지연)
        self.ive_cmd = False
        self.ive_moved_at = None        # IV-E 동작 시각(리미트 1 s 지연)
        self.vv_on = False
        self.pump_req = False       # 펌프 모터를 돌리려는 요청
        # ★ 펌프 모터와 'IV-E 를 열어 두려는 의도'를 분리한다.
        #   벤트는 IV-E 만 닫는데, 펌프 요청이 IV-E 를 자동으로 다시 열면
        #   벤트 허가(IV-E 닫힘)가 영원히 나지 않는다.
        self.ive_auto = False
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
        # PLC 는 첫 스캔에 0 인 PRM 만 기본값을 넣는다.
        for addr, val in A.PRM_PLC_DEFAULTS.items():
            if self.reg[addr] == 0:
                self.reg[addr] = val

    # ===================== 레지스터 접근 (Modbus 서버가 쓴다) =====================
    def read(self, addr: int, count: int):
        if addr < 0 or addr + count > REG_COUNT:
            raise IndexError
        return self.reg[addr:addr + count]

    def write(self, addr: int, values):
        if addr < 0 or addr + len(values) > REG_COUNT:
            raise IndexError
        for i, v in enumerate(values):
            self.reg[addr + i] = int(v) & 0xFFFF

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
        self._physics(sdt, now)
        self._inputs(now)
        self._alarms(now)
        self._interlocks()
        self._state()
        self._publish()
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
        st = self.reg[A.D_STATE]
        running = st in (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)
        ilk = self.reg[A.D_INTERLOCK]

        # 공정 중에는 수동·배기 계열을 받지 않는다.
        if running and code in (9, 10, 11, 12, 13, 14):
            return A.RESULT_STATE

        if code == A.CMD_PROCESS_START:
            if running:
                return A.RESULT_STATE
            if not self.reg[A.D_RECIPE_OK]:
                return A.RESULT_RECIPE
            if not (ilk >> A.ILK_START_OK) & 1:
                return A.RESULT_INTERLOCK
            return A.RESULT_OK          # 공정 실행 자체는 2단계
        if code == A.CMD_PAUSE:
            return A.RESULT_STATE if st != A.STATE_RUN else A.RESULT_OK
        if code == A.CMD_RESUME:
            if st != A.STATE_PAUSE:
                return A.RESULT_STATE
            return A.RESULT_INTERLOCK if self.safe_stop else A.RESULT_OK
        if code in (A.CMD_STOP_AFTER_CYCLE, A.CMD_ABORT):
            return A.RESULT_OK if running else A.RESULT_STATE

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
            self.ive_auto = True
            self.pump_started = time.monotonic()
            return A.RESULT_OK
        if code == A.CMD_PUMP_STOP:
            self.pump_req = False
            self.ive_auto = False
            self.ive_cmd = False
            self.ive_moved_at = time.monotonic()
            self.pump_on = False
            self.pump_run_at = None
            return A.RESULT_OK
        if code == A.CMD_VENT:
            self.ive_auto = False       # 펌프 모터는 그대로, IV-E 만 닫는다
            self.ive_cmd = False
            self.ive_moved_at = time.monotonic()
            self.vent_req = True
            self.vent_started = time.monotonic()
            return A.RESULT_OK
        if code == A.CMD_ALL_CLOSE:
            self.ive_auto = False       # 펌프 모터는 그대로 둔다(사양)
            self.ive_cmd = False
            self.ive_moved_at = time.monotonic()
            self.vent_req = False
            self.vv_on = False
            self.write(A.D_MANUAL_VALVE, [0, 0, 0, 0])
            self.reg[A.D_MANUAL_AUX] = 0
            return A.RESULT_OK

        if code == A.CMD_MANUAL_APPLY:
            if self.safe_stop:
                return A.RESULT_INTERLOCK
            return A.RESULT_OK          # 실제 반영은 _physics 가 요청 워드를 보고 한다
        if code in (A.CMD_HEATER_APPLY, A.CMD_MFC_APPLY):
            return A.RESULT_OK
        return A.RESULT_UNKNOWN

    def _pump_blocking(self) -> bool:
        a0 = self.reg[A.D_ALARM0]
        for b in (A.ALM0_EMO, A.ALM0_PUMP, A.ALM0_AIR, A.ALM0_CW):
            if (a0 >> b) & 1:
                return True
        return False

    # ---------- 물리 ----------
    def _physics(self, sdt, now):
        # 비상정지·펌프·공압·냉각수 알람이면 펌핑·벤트 요청을 지운다.
        if self._pump_blocking():
            self.pump_req = False
            self.ive_auto = False
            self.vent_req = False
            self.vv_on = False

        # 펌프
        if self.pump_req and not self.pump_on:
            self.pump_on = True
            self.pump_run_at = now
        if not self.pump_req:
            self.pump_on = False
            self.pump_run_at = None
        pump_run = self.pump_on and self.pump_run_at is not None and (now - self.pump_run_at) >= 1.0

        # IV-E: 펌프가 정상이고 VV 가 닫혀 있으면 연다
        if self.ive_auto and pump_run and not self.vv_on and not self.ive_cmd:
            self.ive_cmd = True
            self.ive_moved_at = now

        # 벤트: IV-E 가 닫힌 뒤 벤트 허가가 되면 VV 를 연다
        if self.vent_req:
            if self._vent_ok() and not self.vv_on:
                self.vv_on = True
            if self.pressure >= ATM_TORR * 0.99:
                self.vv_on = False
                self.vent_req = False
            elif self.vent_started and (now - self.vent_started) > (self.reg[A.D_PRM_VENT_TIMEOUT] or 300):
                self._latch0(A.ALM0_VENT_TIMEOUT)
                self.vent_req = False
                self.vv_on = False

        # 압력
        ive_open = self.ive_cmd and self.ive_moved_at is not None and (now - self.ive_moved_at) >= 1.0
        if self.vv_on:
            self.pressure += (ATM_TORR - self.pressure) * (1 - pow(2.718281828, -sdt / VENT_TAU_S))
        elif pump_run and ive_open:
            self.pressure += (ULTIMATE_TORR - self.pressure) * (1 - pow(2.718281828, -sdt / PUMP_TAU_S))
            if (self.pump_started and self.reg[A.D_PRM_BASE_PRESS]
                    and (now - self.pump_started) > (self.reg[A.D_PRM_PUMP_TIMEOUT] or 600)
                    and self.conv.cvg.to_raw(self.pressure) > self.reg[A.D_PRM_BASE_PRESS]):
                self._latch0(A.ALM0_BASE_TIMEOUT)
                self.pump_started = None
        self.pressure = max(1.0e-4, min(ATM_TORR, self.pressure))

        # 안전 정지 요구면 공정 밸브·수동 보조 요청을 지운다(자동으로 다시 켜지지 않는다)
        if self.safe_stop:
            self.write(A.D_MANUAL_VALVE, [0, 0, 0, 0])
            self.reg[A.D_MANUAL_AUX] = 0

        # 수동 밸브 요청 → 실제 출력. 전구체와 반응물이 함께 요청되면 둘 다 막는다.
        req = self.reg[A.D_MANUAL_VALVE] & DEV.MANUAL_VALVE_MASK
        pre = any((req >> b) & 1 for b in DEV.PRECURSOR_VALVE_BITS)
        rea = any((req >> b) & 1 for b in DEV.REACTANT_VALVE_BITS)
        self.both_req = pre and rea
        if self.both_req:
            self._latch0(A.ALM0_BOTH_OPEN)
            for b in DEV.PRECURSOR_VALVE_BITS + DEV.REACTANT_VALVE_BITS:
                req &= ~(1 << b)
        self.valve_out = req

        # 수동 보조 출력 요청 (명령 12 대상 비트만)
        aux_req = self.reg[A.D_MANUAL_AUX] & DEV.AUX_CMD_MASK

        # 장비 전용
        if DEV.HAS_RF:
            self.rf_on = bool(aux_req & (1 << A.AUX_RF)) and self._rf_ok()
        if DEV.HAS_O3:
            self._o3_logic(aux_req, now)

        # MFC: 설정값을 1 s 정도로 따라간다
        for i in range(8):
            sv_raw = self.reg[A.D_MFC_SV + i]
            sv = self.conv.mfc.get(i + 1)
            target = sv.to_eng(sv_raw) if sv and sv.full else 0.0
            target = target or 0.0
            self.mfc_pv[i] += (target - self.mfc_pv[i]) * (1 - pow(2.718281828, -sdt / MFC_TAU_S))

        # 히터: 켜져 있으면 설정 온도로 천천히 올라가고, 꺼지면 식는다
        power = self.reg[A.D_HEATER_POWER]
        for ch in range(12):
            on = bool((power >> ch) & 1)
            sv = heater_temp(self.reg[A.D_HEATER_SV + ch]) if on else 25.0
            self.heater_pv[ch] += (sv - self.heater_pv[ch]) * (1 - pow(2.718281828, -sdt / HEATER_TAU_S))

        self.aux_out = self._aux_word(pump_run, ive_open, aux_req)

    def _vent_ok(self) -> bool:
        st = self.reg[A.D_STATE]
        ive_closed = not self.ive_cmd and (self.ive_moved_at is None
                                           or (time.monotonic() - self.ive_moved_at) >= 1.0)
        return (st not in (A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)
                and ive_closed and not self.faults["emo"])

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
        if self.ive_cmd:
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
            w |= (1 << A.IN0_IVE_OPEN) if self.ive_cmd else (1 << A.IN0_IVE_CLOSE)
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
        if f["ot"]:
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
            A.ALM0_OT: f["ot"], A.ALM0_PC_LINK: not self.pc_link_ok,
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
        if getattr(self, "both_req", False):
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
        if self.safe_stop:
            self.reg[A.D_STATE] = A.STATE_SAFE_STOP
        elif self.reg[A.D_STATE] in (A.STATE_SAFE_STOP, A.STATE_INIT):
            self.reg[A.D_STATE] = A.STATE_IDLE

    # ---------- 상태 영역에 반영 ----------
    def _publish(self):
        self.reg[A.D_VALVE_OUT] = getattr(self, "valve_out", 0)
        self.reg[A.D_AUX_OUT] = getattr(self, "aux_out", 0)
        self.reg[A.D_CVG_RAW] = self.conv.cvg.to_raw(self.pressure)
        if self.conv.cm.installed:
            self.reg[A.D_CM_RAW] = self.conv.cm.to_raw(self.pressure)
        for ch in range(12):
            self.reg[A.D_HEATER_PV + ch] = heater_raw(self.heater_pv[ch])
            on = bool((self.reg[A.D_HEATER_POWER] >> ch) & 1)
            sv = heater_temp(self.reg[A.D_HEATER_SV + ch])
            gap = max(0.0, sv - self.heater_pv[ch]) if on else 0.0
            self.reg[A.D_HEATER_OUT + ch] = int(min(100, gap * 3)) if on else 0
        self.reg[A.D_TC_COMM] = 0b111
        self.reg[A.D_HEATER_ALARM] = 0
        for i in range(8):
            s = self.conv.mfc.get(i + 1)
            self.reg[A.D_MFC_PV + i] = s.to_raw(self.mfc_pv[i]) if s and s.full else 0
        self.reg[A.D_SCAN_MAX] = 12

        if DEV.HAS_RF:
            self.reg[A.D_PCV_RAW] = self.reg[A.D_PCV_SV]
            fwd = self.reg[A.D_RF_SV] if self.rf_on else 0
            self.reg[A.D_RF_FWD_RAW] = fwd
            ref = int(fwd * (0.30 if self.faults["rf_ref"] else 0.03))
            self.reg[A.D_RF_REF_RAW] = ref
            lim = self.reg[A.D_PRM_RF_REF_MAX]
            if self.rf_on and lim > 0 and ref > lim:
                self._latch1(A.ALM1_RF_REF)
        if DEV.HAS_O3:
            self.reg[A.D_O3_RAW] = self.reg[A.D_O3_SV] if self.o3_gen_on else 0

        # 표시용 내부 영역 — 실제로 출력 중인 설정값
        base = A.DISPLAY_BASE
        for i in range(A.DISPLAY_COUNT):
            self.reg[base + i] = 0
        for d in DEV.DISPLAY_SETPOINTS:
            key, off = d["key"], d["offset"]
            if key == "pcv":
                self.reg[base + off] = self.reg[A.D_PCV_SV]
            elif key == "rf":
                self.reg[base + off] = self.reg[A.D_RF_SV] if self.rf_on else 0
            elif key == "o3":
                self.reg[base + off] = self.reg[A.D_O3_SV] if self.o3_gen_on else 0
            elif key.startswith("mfc"):
                self.reg[base + off] = self.reg[A.D_MFC_SV + int(key[3:]) - 1]

    # ---------- 레시피 표 검사 ----------
    def _recipe_check(self, now):
        if self.reg[A.D_STATE] not in (A.STATE_IDLE, A.STATE_READY):
            return
        if now - self.recipe_check_at < 1.0:
            return
        self.recipe_check_at = now
        total = 0
        for a in range(A.RCP_SUM_BASE, A.RCP_SUM_END + 1):
            if a == A.D_RCP_SUM:
                continue
            total = (total + self.reg[a]) & 0xFFFF
        self.reg[A.D_RECIPE_SUM_PLC] = total
        ns = self.reg[A.D_RCP_STEP_COUNT]
        nb = self.reg[A.D_RCP_BLOCK_COUNT]
        ng = self.reg[A.D_RCP_GROUP_COUNT]
        ok = (total == self.reg[A.D_RCP_SUM] and 1 <= ns <= A.RCP_STEP_MAX
              and 1 <= nb <= A.RCP_BLOCK_MAX and ng <= A.RCP_GROUP_MAX)
        self.reg[A.D_RECIPE_OK] = 1 if ok else 0
        if not ok and (ns or nb):
            self._latch0(A.ALM0_RECIPE)


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
