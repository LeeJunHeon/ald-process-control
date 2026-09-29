"""
plclink.py — PLC 링크. 백그라운드 태스크 하나가 소켓을 가진다.

주기 (plc.poll_ms · plc.heartbeat_ms 로 바꿀 수 있다)
  100 ms  상태 영역 D00000~D00080 을 한 번에 읽어 스냅샷 교체
  500 ms  D01000(PC 하트비트) +1 쓰기
  500 ms  PLC 반영 영역 D04012~D04131 읽기 (수동 반영 + 실제로 출력 중인 설정값)
    1 s   명령 영역 D01000~D01042 · PRM 영역 D01100~D01124 되읽기 (히터 목표·전원 표시, PRM 확인)

★ PC 하트비트는 화면·명령 처리와 무관하게 계속 돌아야 한다.
  PLC 는 이 값이 PRM_PC_WDT_MS 동안 안 바뀌면 PC 끊김으로 보고 안전 정지한다.
  화면이 바쁘다고 하트비트가 늦으면 멀쩡한 공정이 멈춘다.

★ 명령은 한 번에 하나만 보낸다. 시간 초과가 나면 같은 명령을 자동으로 다시 보내지
  않는다 — PLC 가 이미 받았을 수 있어 두 번 실행될 위험이 있다.

★ PC 는 수동 요청(밸브·보조·MFC·PCV·RF·O3)을 따로 기억하지 않는다.
  PLC 는 전체 닫기·공정 시작·안전 정지·대기압에서 '내부 사본(D04012~)'만 지우고
  PC 영역(D01004~)은 그대로 둔다. PC 가 옛 요청을 들고 있거나 PC 영역을 기준으로 삼으면
  다음 명령 12 때 PLC 가 지운 요청이 되살아난다. 그래서 명령 12·14 를 보낼 때마다
  명령 잠금 안에서 반영 영역을 새로 읽고, 거기에 이번 변경만 얹어 전부 쓴다.
"""

import time
import asyncio
import logging

from . import addresses as A
from . import device as DEV
from .modbus import ModbusClient, ModbusError, ModbusTimeout

log = logging.getLogger(__name__)

POLL_S = 0.100
HEARTBEAT_S = 0.500
DISPLAY_S = 0.500
CMD_READ_S = 1.0                # 명령 영역·PRM 영역 되읽기
ACK_TIMEOUT_S = 1.5             # 명령 응답을 기다리는 최대 시간
APPLY_SETTLE_S = 0.1            # 명령 12 결과 뒤 반영 영역을 다시 읽기까지(PLC 몇 스캔)
PLC_HB_STALL_S = 2.0            # PLC 하트비트가 이만큼 안 바뀌면 멈춘 것으로 본다
RECONNECT_DELAYS = (1.0, 2.0, 5.0)

PRM_READ_BASE = A.D_PRM_PC_WDT_MS
PRM_READ_COUNT = A.D_PRM_O3_MAX - A.D_PRM_PC_WDT_MS + 1     # D01100 ~ D01124
HEATER_REGS = A.D_HEATER_SV + 12 - A.D_HEATER_POWER         # D01010 ~ D01023


def _ao_offset(key: str):
    for d in DEV.DISPLAY_SETPOINTS:
        if d["key"] == key:
            return d["offset"]
    return None


class PlcLink:
    def __init__(self, cfg: dict, conv, on_event=None):
        plc = cfg.get("plc") or {}
        self.cfg = cfg
        self.conv = conv
        self.on_event = on_event or (lambda level, msg: None)

        self.simulate = bool(plc.get("simulate", False))
        host = "127.0.0.1" if self.simulate else (plc.get("host") or "127.0.0.1")
        port = int(plc.get("sim_port") or DEV.DEFAULT_SIM_PORT) if self.simulate \
            else int(plc.get("port") or 502)
        self.client = ModbusClient(host, port, int(plc.get("unit_id") or 1),
                                   int(plc.get("timeout_ms") or 1000))
        self.addr_text = f"{host}:{port}"
        # 설정의 주기를 실제로 쓴다(검증은 config.validate 가 한다)
        self.poll_s = self._period(plc.get("poll_ms"), POLL_S)
        self.heartbeat_s = self._period(plc.get("heartbeat_ms"), HEARTBEAT_S)

        self.connected = False
        self.status = [0] * A.STATUS_COUNT      # 마지막으로 읽은 상태 영역
        self.applied = [0] * A.APPLIED_COUNT    # PLC 가 실제로 반영한 값 (읽기 전용 영역)
        self.display = [0] * A.DISPLAY_COUNT    # 실제 출력 중인 설정값
        self.cmd_regs = []                      # D01000~D01042 되읽은 값 (1 s)
        self.rtt_ms = 0
        self.prm_mismatch = []                  # 되읽기가 다른 PRM 이름 목록
        self.prm_written = {}                   # {주소: 쓴 원시값}
        self.prm_readback = {}                  # {주소: 되읽은 원시값}

        self._cmd_no = 0
        self._cmd_lock = asyncio.Lock()
        self._cmd_seq = 0               # 명령을 보낼 때마다(시작·끝) +1 — 주기 비교에서 뺀다
        self._seen = None               # (반영 밸브, 반영 보조, 그때의 _cmd_seq)
        self._ot_prev = False
        self._trip_task = None
        self._plc_hb_val = None
        self._plc_hb_at = 0.0
        self._hb = 0
        self._task = None
        self._stop = False

    @staticmethod
    def _period(ms, default_s):
        try:
            v = int(ms)
        except (TypeError, ValueError):
            return default_s
        return max(0.02, v / 1000.0) if v > 0 else default_s

    # ===================== 수명 주기 =====================
    def start(self):
        self._task = asyncio.create_task(self._run())
        return self._task

    async def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self.client.close()

    # ===================== 메인 루프 =====================
    async def _run(self):
        attempt = 0
        while not self._stop:
            try:
                await self.client.connect()
            except Exception as e:  # noqa: BLE001
                self._drop(f"PLC 연결 실패 ({self.addr_text}): {e}")
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                attempt += 1
                await asyncio.sleep(delay)
                continue

            attempt = 0
            try:
                await self._on_connect()
                self.connected = True
                self.on_event("ok", f"PLC 연결됨 ({self.addr_text})")
                await self._serve()
            except (ModbusTimeout, ModbusError, OSError) as e:
                self._drop(f"PLC 통신 끊김: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._drop(f"PLC 링크 오류: {type(e).__name__}: {e}")
            await self.client.close()
            await asyncio.sleep(RECONNECT_DELAYS[0])

    def _drop(self, msg):
        if self.connected:
            self.on_event("err", msg)
        else:
            log.debug("%s", msg)
        self.connected = False
        self.status = [0] * A.STATUS_COUNT
        self.display = [0] * A.DISPLAY_COUNT
        self.cmd_regs = []
        self._seen = None

    async def _serve(self):
        """연결된 동안 주기 작업을 돈다."""
        next_poll = next_hb = next_disp = next_cmd = 0.0
        while not self._stop:
            now = time.monotonic()
            if now >= next_hb:
                next_hb = now + self.heartbeat_s
                await self._write_heartbeat()
            if now >= next_poll:
                next_poll = now + self.poll_s
                await self._read_status()
            if now >= next_disp:
                next_disp = now + DISPLAY_S
                await self._read_display()
            if now >= next_cmd:
                next_cmd = now + CMD_READ_S
                await self._read_cmd_area()
            await asyncio.sleep(0.010)

    # ===================== 연결 직후 =====================
    async def _on_connect(self):
        """① 다음 명령 번호를 이어 가고
           ② params 를 원시값으로 바꿔 PRM 영역에 쓰고 되읽어 확인하고
           ③ PLC 반영 영역(D04012~)을 읽어 '지금 수동 요청'의 기준으로 삼고
           ④ 과온 알람이 래치돼 있으면 히터 전원 요청(D01010)을 0 으로 맞춘다."""
        regs = await self.client.read_holding(A.CMD_READ_BASE, A.CMD_READ_COUNT)
        self.cmd_regs = list(regs)
        # ★ 명령 번호를 이어 가야 한다. 0부터 다시 시작하면 PLC 가 "이미 처리한 번호"로
        #   보고 무시하거나, 옛 명령을 다시 실행한 것처럼 보일 수 있다.
        self._cmd_no = (regs[A.D_CMD_NO - A.CMD_READ_BASE] + 1) & 0xFFFF
        await self._write_params()
        self._set_applied(await self.client.read_holding(A.APPLIED_BASE, A.APPLIED_COUNT))
        self._seen = None
        self.status = await self.client.read_holding(A.STATUS_BASE, A.STATUS_COUNT)
        self._ot_prev = A.bit(self.status[A.D_ALARM0], A.ALM0_OT)
        if self._ot_prev and self.cmd_reg(A.D_HEATER_POWER):
            await self.client.write_single(A.D_HEATER_POWER, 0)
            self._set_cmd_reg(A.D_HEATER_POWER, 0)
            self.on_event("warn", "과온 알람이 래치돼 있어 히터 전원 요청을 모두 껐습니다")

    async def _write_params(self):
        """공학 단위 params 를 원시값으로 바꿔 PRM 영역에 쓰고 되읽어 확인한다."""
        want = self._param_words()
        self.prm_mismatch = []
        self.prm_written = {addr: val & 0xFFFF for addr, (_n, val) in want.items()}
        for addr, (name, val) in sorted(want.items()):
            try:
                await self.client.write_single(addr, val)
            except ModbusError as e:
                self.prm_mismatch.append(f"{name}: 쓰기 실패 ({e})")
        got = await self.client.read_holding(PRM_READ_BASE, PRM_READ_COUNT)
        self._store_prm_readback(got)
        for addr, (name, val) in sorted(want.items()):
            back = self.prm_readback.get(addr)
            if back is not None and back != (val & 0xFFFF):
                self.prm_mismatch.append(f"{name}: 쓴 값 {val} / 되읽은 값 {back}")
        if self.prm_mismatch:
            self.on_event("warn", "PLC 파라미터 되읽기 불일치 — " + " · ".join(self.prm_mismatch[:3]))

    def _store_prm_readback(self, got):
        self.prm_readback = {PRM_READ_BASE + i: int(v) for i, v in enumerate(got)}

    @staticmethod
    def _num(params: dict, key: str, default):
        """설정값을 정수로. ★ 0 을 '없음'으로 보지 않는다 —
        mfc_stable_s: 0(대기 없음)·valve_min_ms: 0(최소 열림 없음)은 뜻이 있는 값이라,
        `or` 로 처리하면 운전자가 끈 기능이 조용히 되살아난다."""
        v = params.get(key)
        if v is None or v == "":
            v = default
        try:
            return int(v)
        except (TypeError, ValueError):
            return int(default)

    def _param_words(self) -> dict:
        """{주소: (이름, 원시값)}. 공학 단위 → 원시값 변환은 여기 한 곳에서만 한다."""
        from .convert import heater_raw
        p = self.cfg.get("params") or {}
        conv = self.conv
        n = self._num
        out = {
            A.D_PRM_PC_WDT_MS: ("PC 하트비트 판정", n(p, "pc_wdt_ms", 3000)),
            A.D_PRM_PUMP_TIMEOUT: ("베이스 도달 제한", n(p, "pump_timeout_s", 600)),
            A.D_PRM_VENT_TIMEOUT: ("대기압 도달 제한", n(p, "vent_timeout_s", 300)),
            A.D_PRM_MFC_STABLE: ("MFC 안정 판정", n(p, "mfc_stable_s", 3)),
            A.D_PRM_MFC_TIMEOUT: ("MFC 안정 제한", n(p, "mfc_timeout_s", 60)),
            A.D_PRM_VALVE_MIN_MS: ("밸브 최소 열림", n(p, "valve_min_ms", 200)),
            # ★ 베이스 압력은 역함수로 원시값을 만든다(환산이 단조 증가여야 하는 이유).
            A.D_PRM_BASE_PRESS: ("베이스 압력", conv.cvg.to_raw(p.get("base_press_torr"))),
        }
        tol = p.get("mfc_tol_sccm")
        m1 = conv.mfc.get(1)
        out[A.D_PRM_MFC_TOL] = ("MFC1 허용 편차",
                                m1.to_raw(tol) if (m1 and m1.full and tol is not None) else 0)
        # 히터 과온 한계 — max_c 가 null 인 채널은 0 을 쓴다.
        # ★ PLC 는 한계 0 인 채널의 '소프트 과온 감시'만 안 할 뿐 전원을 막지 않는다.
        #   그래서 한계가 없는 채널은 PC 가 목표 온도·전원 켜기를 모두 거절한다.
        for i, h in enumerate(self.cfg.get("heaters") or []):
            if i >= 12:
                break
            mx = h.get("max_c")
            out[A.D_PRM_HEATER_MAX + i] = (f"CH{i + 1} 과온 한계",
                                           heater_raw(mx) if mx is not None else 0)
        if DEV.HAS_RF:
            rf = self.cfg.get("rf") or {}
            out[A.D_PRM_RF_MAX] = ("RF 상한", conv.rf.to_raw(p.get("rf_max_w"))
                                   if rf.get("max_w") else 0)
            out[A.D_PRM_RF_REF_MAX] = ("반사 전력 한계", conv.rf.to_raw(p.get("rf_ref_max_w"))
                                       if rf.get("max_w") else 0)
            out[A.D_PRM_RF_REF_MS] = ("반사 초과 허용", n(p, "rf_ref_ms", 0))
            out[A.D_PRM_RF_MAX_PRESS] = ("RF 허가 최대 압력",
                                         conv.cvg.to_raw(p.get("rf_p_max_torr")))
        if DEV.HAS_O3:
            o3 = self.cfg.get("o3") or {}
            out[A.D_PRM_O3_MAX] = ("O3 상한", conv.o3.to_raw(p.get("o3_max"))
                                   if o3.get("full") else 0)
        return out

    # ===================== 주기 작업 =====================
    async def _write_heartbeat(self):
        """PC 가 살아 있다는 신호. 값 자체는 의미가 없고 '바뀐다'는 사실만 중요하다."""
        self._hb = (self._hb + 1) & 0xFFFF
        await self.client.write_single(A.D_PC_HB, self._hb)

    async def _read_status(self):
        t0 = time.monotonic()
        regs = await self.client.read_holding(A.STATUS_BASE, A.STATUS_COUNT)
        self.rtt_ms = int((time.monotonic() - t0) * 1000)
        self.status = regs
        hb = regs[A.D_PLC_HB]
        now = time.monotonic()
        if hb != self._plc_hb_val:
            self._plc_hb_val = hb
            self._plc_hb_at = now
        ot = A.bit(regs[A.D_ALARM0], A.ALM0_OT)
        if ot and not self._ot_prev:
            # ★ 소켓 루프를 명령 잠금으로 붙잡지 않도록 따로 돈다(하트비트가 늦으면 안 된다).
            self._trip_task = asyncio.create_task(self._heater_trip())
        self._ot_prev = ot

    async def _heater_trip(self):
        """과온 알람이 새로 켜졌다 — PLC 는 전원 묶음을 스스로 0 으로 둔다.
        PC 요청(D01010)도 0 으로 맞춰, 리셋 뒤 다음 명령 13 이 꺼진 채널을 되살리지 않게 한다."""
        try:
            async with self._cmd_lock:
                await self.client.write_single(A.D_HEATER_POWER, 0)
                self._set_cmd_reg(A.D_HEATER_POWER, 0)
        except (ModbusTimeout, ModbusError, OSError) as e:
            self.on_event("err", f"과온 차단 — 히터 전원 요청을 0 으로 쓰지 못했습니다: {e}")
            return
        self.on_event("err", "과온 차단 — PLC 가 히터 전원을 모두 껐습니다")

    async def _read_display(self):
        """PLC 반영 영역(수동 반영 + 출력 중인 설정값)을 한 번에 읽는다.
        두 번의 읽기 사이에 명령이 없었는데 반영 비트가 줄었으면 PLC 가 지운 것이다."""
        seq = self._cmd_seq
        self._set_applied(await self.client.read_holding(A.APPLIED_BASE, A.APPLIED_COUNT))
        v, a = self.applied_valve, self.applied_aux
        if self._seen is not None and self._seen[2] == seq:
            lost_v = self._seen[0] & ~v
            lost_a = self._seen[1] & ~a
            if lost_v or lost_a:
                self.on_event("warn", "PLC 가 수동 요청을 지웠습니다 — "
                              + describe_bits(lost_v, lost_a)
                              + f" ({self.clear_reason(bool(lost_v))})")
        self._seen = (v, a, seq)

    async def _read_cmd_area(self):
        self.cmd_regs = list(await self.client.read_holding(A.CMD_READ_BASE, A.CMD_READ_COUNT))
        self._store_prm_readback(await self.client.read_holding(PRM_READ_BASE, PRM_READ_COUNT))

    def clear_reason(self, valve_lost: bool) -> str:
        """PLC 가 수동 반영을 지운 사유(지금 상태로 추정)."""
        s = self.status
        why = []
        if A.bit(s[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ):
            why.append("안전 정지 요구")
        if valve_lost and A.bit(s[A.D_INPUT0], A.IN0_ATM):
            why.append("챔버 대기압")
        if s[A.D_STATE] in (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING):
            why.append("공정 시작")
        return " · ".join(why) if why else "사유 확인 필요"

    def _set_applied(self, regs):
        self.applied = list(regs)
        off = A.applied_off(A.DISPLAY_BASE)
        self.display = self.applied[off:off + A.DISPLAY_COUNT]

    @property
    def applied_valve(self) -> int:
        o = A.applied_off(A.D_APPLIED_VALVE)
        return A.dword(self.applied[o], self.applied[o + 1]) if len(self.applied) > o + 1 else 0

    @property
    def applied_aux(self) -> int:
        o = A.applied_off(A.D_APPLIED_AUX)
        return self.applied[o] if len(self.applied) > o else 0

    def cmd_reg(self, addr: int):
        """명령 영역(D01000~D01042)에서 마지막으로 되읽은 값. 아직 없으면 None."""
        i = addr - A.CMD_READ_BASE
        return self.cmd_regs[i] if 0 <= i < len(self.cmd_regs) else None

    def _set_cmd_reg(self, addr: int, value: int):
        i = addr - A.CMD_READ_BASE
        if 0 <= i < len(self.cmd_regs):
            self.cmd_regs[i] = int(value) & 0xFFFF

    @property
    def plc_hb_ok(self) -> bool:
        """통신은 되는데 PLC 가 STOP 인 경우를 잡는다."""
        if not self.connected or self._plc_hb_at == 0.0:
            return False
        return (time.monotonic() - self._plc_hb_at) <= PLC_HB_STALL_S

    # ===================== 레시피 올리기 =====================
    async def upload_recipe(self, words, checksum: int):
        """스텝·블록·그룹 영역을 먼저 쓰고 헤더를 마지막에 쓴다 → 되읽어 비교 →
        PLC 검사 결과를 기다린다.

        ★ 헤더(개수·합계)를 마지막에 쓰는 이유: PLC 는 1 s 마다 헤더를 보고 표를 검사한다.
          헤더를 먼저 쓰면 아직 안 올라온 본문으로 검사해 '불합격'이 뜬다.
        반환: (성공, 설명)"""
        if not self.connected:
            return False, "PLC 에 연결되어 있지 않습니다"
        body_from = A.D_RCP_STEP_BASE - A.RCP_SUM_BASE
        try:
            async with self._cmd_lock:
                await self.client.write_multiple(A.D_RCP_STEP_BASE, words[body_from:])
                await self.client.write_multiple(A.RCP_SUM_BASE, words[:body_from])

                back = await self.client.read_holding(A.RCP_SUM_BASE, A.RCP_AREA_COUNT)
                if list(back) != [int(w) & 0xFFFF for w in words]:
                    bad = next((i for i, (a, b) in enumerate(zip(back, words))
                                if a != (int(b) & 0xFFFF)), -1)
                    return False, f"되읽기가 다릅니다 (D{A.RCP_SUM_BASE + bad:05d})"

            # PLC 는 대기 중 1 s 마다 검사한다 — 쓰기 직후 값은 이전 레시피 것일 수 있다.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                regs = await self.client.read_holding(A.D_RECIPE_SUM_PLC, 2)
                if regs[0] == (checksum & 0xFFFF) and regs[1] == 1:
                    return True, "PLC 검사 통과"
                await asyncio.sleep(0.1)
            regs = await self.client.read_holding(A.D_RECIPE_SUM_PLC, 2)
            return False, (f"PLC 표 검사에 실패했습니다 "
                           f"(PLC 합계 {regs[0]:#06x} / 보낸 합계 {checksum:#06x}, 통과 {regs[1]})")
        except (ModbusTimeout, ModbusError, OSError) as e:
            return False, f"PLC 통신 오류: {e}"

    async def read_recipe_area(self):
        """지금 PLC 에 올라가 있는 레시피 표를 읽는다(역변환용)."""
        if not self.connected:
            return None
        try:
            return await self.client.read_holding(A.RCP_SUM_BASE, A.RCP_AREA_COUNT)
        except (ModbusTimeout, ModbusError, OSError):
            return None

    # ===================== 수동 요청 =====================
    @staticmethod
    def manual_from_applied(applied) -> dict:
        """PLC 반영 영역에서 '지금 수동 요청'을 만든다 — 명령 12 의 기준값."""
        o = A.applied_off(A.D_APPLIED_VALVE)
        d0 = A.applied_off(A.DISPLAY_BASE)
        out = {
            "valve": A.dword(applied[o], applied[o + 1]),
            "aux": applied[A.applied_off(A.D_APPLIED_AUX)],
        }
        for key in ("pcv", "rf", "o3"):
            off = _ao_offset(key)
            if off is not None:
                out[key] = applied[d0 + off]
        return out

    async def manual_apply(self, mutate):
        """명령 12. 잠금 안에서 반영 영역을 새로 읽고 mutate(현재값) 로 이번 변경만 얹어
        D01004~07 · D01008 · (PEALD) D01040·D01041 / (Powder) D01042 를 전부 쓴 뒤 보낸다.

        mutate(cur: dict) -> (new: dict, 거절 사유 str). 사유가 있으면 보내지 않는다.
        반환 {result, text, sent, lost_valve, lost_aux}. result None 은 못 보냈거나 응답 없음."""
        if not self.connected:
            return {"result": None, "text": "PLC 에 연결되어 있지 않습니다"}
        async with self._cmd_lock:
            try:
                self._set_applied(await self.client.read_holding(A.APPLIED_BASE, A.APPLIED_COUNT))
            except (ModbusTimeout, ModbusError, OSError) as e:
                return {"result": None, "text": f"PLC 통신 오류: {e}"}
            cur = self.manual_from_applied(self.applied)
            new, why = mutate(dict(cur))
            if why:
                return {"result": None, "text": why, "refused": True}
            new["valve"] = int(new.get("valve", 0)) & DEV.MANUAL_VALVE_MASK
            new["aux"] = int(new.get("aux", 0)) & DEV.AUX_CMD_MASK
            lo, hi = A.split_dword(new["valve"])
            args = {A.D_MANUAL_VALVE: [lo, hi, 0, 0], A.D_MANUAL_AUX: new["aux"]}
            if DEV.HAS_PCV:
                args[A.D_PCV_SV] = int(new.get("pcv", 0)) & 0xFFFF
            if DEV.HAS_RF:
                args[A.D_RF_SV] = int(new.get("rf", 0)) & 0xFFFF
            if DEV.HAS_O3:
                args[A.D_O3_SV] = int(new.get("o3", 0)) & 0xFFFF
            result, text = await self._send_locked(A.CMD_MANUAL_APPLY, args)
            out = {"result": result, "text": text, "sent": new, "before": cur,
                   "lost_valve": 0, "lost_aux": 0}
            if result != A.RESULT_OK:
                return out
            # 결과 0 이어도 PLC 가 곧바로 지우는 경우가 있다(대기압·RF 전력 0) — 다시 읽어 본다.
            try:
                await asyncio.sleep(APPLY_SETTLE_S)
                self._set_applied(await self.client.read_holding(A.APPLIED_BASE, A.APPLIED_COUNT))
            except (ModbusTimeout, ModbusError, OSError):
                return out
            after = self.manual_from_applied(self.applied)
            out["lost_valve"] = new["valve"] & ~after["valve"]
            out["lost_aux"] = new["aux"] & ~after["aux"]
            out["after"] = after
            self._seen = (after["valve"], after["aux"], self._cmd_seq)
            return out

    async def mfc_apply(self, changes: dict):
        """명령 14. MFC AO 현재값(D04121~)에 이번 변경만 얹어 MFC 개수만큼 전부 쓴다.
        changes: {MFC 번호: 원시값}. 반환 (결과, 설명)."""
        if not self.connected:
            return None, "PLC 에 연결되어 있지 않습니다"
        async with self._cmd_lock:
            try:
                self._set_applied(await self.client.read_holding(A.APPLIED_BASE, A.APPLIED_COUNT))
            except (ModbusTimeout, ModbusError, OSError) as e:
                return None, f"PLC 통신 오류: {e}"
            vals = []
            for no in range(1, DEV.MFC_COUNT + 1):
                off = _ao_offset(f"mfc{no}")
                cur = self.display[off] if off is not None else 0
                vals.append(int(changes.get(no, cur)) & 0xFFFF)
            return await self._send_locked(A.CMD_MFC_APPLY, {A.D_MFC_SV: vals})

    async def heater_apply(self, mutate):
        """명령 13. 잠금 안에서 D01010·D01012~23 을 새로 읽고 mutate(전원, 목표[12]) 로
        바꾼 값을 쓴 뒤 보낸다. PLC 가 거절하면 방금 쓴 값을 이전 값으로 되돌려 쓴다.
        반환 {result, text, reverted, power, sv}."""
        if not self.connected:
            return {"result": None, "text": "PLC 에 연결되어 있지 않습니다"}
        async with self._cmd_lock:
            try:
                regs = await self.client.read_holding(A.D_HEATER_POWER, HEATER_REGS)
            except (ModbusTimeout, ModbusError, OSError) as e:
                return {"result": None, "text": f"PLC 통신 오류: {e}"}
            power = regs[0]
            sv = list(regs[A.D_HEATER_SV - A.D_HEATER_POWER:])
            new_power, new_sv, why = mutate(power, list(sv))
            if why:
                return {"result": None, "text": why, "refused": True}
            result, text = await self._send_locked(
                A.CMD_HEATER_APPLY,
                {A.D_HEATER_POWER: int(new_power) & 0xFFFF,
                 A.D_HEATER_SV: [int(v) & 0xFFFF for v in new_sv]})
            out = {"result": result, "text": text, "reverted": False,
                   "power": new_power, "sv": new_sv}
            if result is not None and result != A.RESULT_OK:
                try:
                    await self.client.write_single(A.D_HEATER_POWER, power)
                    await self.client.write_multiple(A.D_HEATER_SV, sv)
                    out.update(reverted=True, power=power, sv=sv)
                except (ModbusTimeout, ModbusError, OSError):
                    pass
            # 되읽기를 기다리지 않고 쓴 값으로 바로 갱신한다(화면이 1 s 늦지 않게).
            self._set_cmd_reg(A.D_HEATER_POWER, out["power"])
            for i, v in enumerate(out["sv"]):
                self._set_cmd_reg(A.D_HEATER_SV + i, v)
            return out

    # ===================== 명령 핸드셰이크 =====================
    async def send_command(self, code: int, args: dict = None):
        """인자 → 명령 코드 → 명령 번호 순서로 쓰고 D00002 가 그 번호가 될 때까지 기다린다.

        반환: (결과코드 또는 None, 설명). None 은 응답 없음(시간 초과)."""
        if not self.connected:
            return None, "PLC 에 연결되어 있지 않습니다"
        async with self._cmd_lock:
            return await self._send_locked(code, args)

    async def _send_locked(self, code: int, args: dict = None):
        """명령 잠금을 쥔 채로 부른다."""
        self._cmd_seq += 1
        try:
            # ① 인자 레지스터 먼저
            for addr, values in (args or {}).items():
                if isinstance(values, (list, tuple)):
                    await self.client.write_multiple(addr, values)
                else:
                    await self.client.write_single(addr, values)
            # ② 명령 코드 → ③ 명령 번호 (번호를 마지막에 써야 PLC 가 완성된 명령을 본다)
            no = self._cmd_no
            await self.client.write_single(A.D_CMD_CODE, code)
            await self.client.write_single(A.D_CMD_NO, no)
            self._cmd_no = (no + 1) & 0xFFFF

            deadline = time.monotonic() + ACK_TIMEOUT_S
            while time.monotonic() < deadline:
                regs = await self.client.read_holding(A.D_ACK_NO, 2)
                if regs[0] == no:
                    result = regs[1]
                    return result, A.RESULT_NAMES.get(result, f"알 수 없는 결과 {result}")
                await asyncio.sleep(0.02)
            # ★ 다시 보내지 않는다 — PLC 가 이미 받았을 수 있다.
            return None, "PLC 응답 없음 (명령이 처리됐는지 확인하세요)"
        except (ModbusTimeout, ModbusError, OSError) as e:
            return None, f"PLC 통신 오류: {e}"
        finally:
            self._cmd_seq += 1


def describe_bits(valve: int, aux: int) -> str:
    """비트 묶음을 밸브·보조 출력 이름으로."""
    names = [v["tag"] for v in DEV.VALVES if (valve >> v["bit"]) & 1]
    names += [a["tag"] for a in DEV.AUX if (aux >> a["bit"]) & 1]
    return " · ".join(names) if names else "—"
