"""
plclink.py — PLC 링크. 백그라운드 태스크 하나가 소켓을 가진다.

주기
  100 ms  상태 영역 D00000~D00080 을 한 번에 읽어 스냅샷 교체
  500 ms  D01000(PC 하트비트) +1 쓰기
  500 ms  표시용 내부 영역 D04120~D04131 읽기 (실제로 출력 중인 설정값)

★ PC 하트비트는 화면·명령 처리와 무관하게 계속 돌아야 한다.
  PLC 는 이 값이 PRM_PC_WDT_MS 동안 안 바뀌면 PC 끊김으로 보고 안전 정지한다.
  화면이 바쁘다고 하트비트가 늦으면 멀쩡한 공정이 멈춘다.

★ 명령은 한 번에 하나만 보낸다. 시간 초과가 나면 같은 명령을 자동으로 다시 보내지
  않는다 — PLC 가 이미 받았을 수 있어 두 번 실행될 위험이 있다.
"""

import time
import asyncio
import logging

from . import addresses as A
from . import device as DEV
from .convert import heater_raw
from .modbus import ModbusClient, ModbusError, ModbusTimeout

log = logging.getLogger(__name__)

POLL_S = 0.100
HEARTBEAT_S = 0.500
DISPLAY_S = 0.500
ACK_TIMEOUT_S = 1.5             # 명령 응답을 기다리는 최대 시간
PLC_HB_STALL_S = 2.0            # PLC 하트비트가 이만큼 안 바뀌면 멈춘 것으로 본다
RECONNECT_DELAYS = (1.0, 2.0, 5.0)


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

        self.connected = False
        self.status = [0] * A.STATUS_COUNT      # 마지막으로 읽은 상태 영역
        self.display = [0] * A.DISPLAY_COUNT    # 실제 출력 중인 설정값
        self.rtt_ms = 0
        self.prm_mismatch = []                  # 되읽기가 다른 PRM 이름 목록

        self._cmd_no = 0
        self._cmd_lock = asyncio.Lock()
        self._plc_hb_val = None
        self._plc_hb_at = 0.0
        self._hb = 0
        self._task = None
        self._stop = False

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

    async def _serve(self):
        """연결된 동안 주기 작업을 돈다."""
        next_poll = next_hb = next_disp = 0.0
        while not self._stop:
            now = time.monotonic()
            if now >= next_hb:
                next_hb = now + HEARTBEAT_S
                await self._write_heartbeat()
            if now >= next_poll:
                next_poll = now + POLL_S
                await self._read_status()
            if now >= next_disp:
                next_disp = now + DISPLAY_S
                await self._read_display()
            await asyncio.sleep(0.010)

    # ===================== 연결 직후 =====================
    async def _on_connect(self):
        """① 수동 요청 값을 PLC 에 남아 있는 값으로 맞추고
           ② 다음 명령 번호를 이어 가고
           ③ params 를 원시값으로 바꿔 PRM 영역에 쓰고 되읽어 확인한다."""
        sync = await self.client.read_holding(A.SYNC_BASE, A.SYNC_COUNT)
        self.sync_regs = list(sync)
        # ★ 명령 번호를 이어 가야 한다. 0부터 다시 시작하면 PLC 가 "이미 처리한 번호"로
        #   보고 무시하거나, 옛 명령을 다시 실행한 것처럼 보일 수 있다.
        self._cmd_no = (sync[A.D_CMD_NO - A.SYNC_BASE] + 1) & 0xFFFF
        await self._write_params()

    async def _write_params(self):
        """공학 단위 params 를 원시값으로 바꿔 PRM 영역에 쓰고 되읽어 확인한다."""
        want = self._param_words()
        self.prm_mismatch = []
        for addr, (name, val) in sorted(want.items()):
            try:
                await self.client.write_single(addr, val)
            except ModbusError as e:
                self.prm_mismatch.append(f"{name}: 쓰기 실패 ({e})")
        # 되읽기 — 연속 구간 두 덩어리로 나눠 읽는다.
        for base, count in ((A.D_PRM_PC_WDT_MS, 25),):
            got = await self.client.read_holding(base, count)
            for addr, (name, val) in sorted(want.items()):
                if base <= addr < base + count and got[addr - base] != (val & 0xFFFF):
                    self.prm_mismatch.append(f"{name}: 쓴 값 {val} / 되읽은 값 {got[addr - base]}")
        if self.prm_mismatch:
            self.on_event("warn", "PLC 파라미터 되읽기 불일치 — " + " · ".join(self.prm_mismatch[:3]))

    def _param_words(self) -> dict:
        """{주소: (이름, 원시값)}. 공학 단위 → 원시값 변환은 여기 한 곳에서만 한다."""
        p = self.cfg.get("params") or {}
        conv = self.conv
        out = {
            A.D_PRM_PC_WDT_MS: ("PC 하트비트 판정", int(p.get("pc_wdt_ms") or 3000)),
            A.D_PRM_PUMP_TIMEOUT: ("베이스 도달 제한", int(p.get("pump_timeout_s") or 600)),
            A.D_PRM_VENT_TIMEOUT: ("대기압 도달 제한", int(p.get("vent_timeout_s") or 300)),
            A.D_PRM_MFC_STABLE: ("MFC 안정 판정", int(p.get("mfc_stable_s") or 3)),
            A.D_PRM_MFC_TIMEOUT: ("MFC 안정 제한", int(p.get("mfc_timeout_s") or 60)),
            A.D_PRM_VALVE_MIN_MS: ("밸브 최소 열림", int(p.get("valve_min_ms") or 200)),
            # ★ 베이스 압력은 역함수로 원시값을 만든다(환산이 단조 증가여야 하는 이유).
            A.D_PRM_BASE_PRESS: ("베이스 압력", conv.cvg.to_raw(p.get("base_press_torr"))),
        }
        tol = p.get("mfc_tol_sccm")
        m1 = conv.mfc.get(1)
        out[A.D_PRM_MFC_TOL] = ("MFC1 허용 편차",
                                m1.to_raw(tol) if (m1 and m1.full and tol is not None) else 0)
        # 히터 과온 한계 — max_c 가 null 인 채널은 0 을 쓴다(PLC 가 그 채널을 막는다).
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
            out[A.D_PRM_RF_REF_MS] = ("반사 초과 허용", int(p.get("rf_ref_ms") or 0))
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

    async def _read_display(self):
        self.display = await self.client.read_holding(A.DISPLAY_BASE, A.DISPLAY_COUNT)

    @property
    def plc_hb_ok(self) -> bool:
        """통신은 되는데 PLC 가 STOP 인 경우를 잡는다."""
        if not self.connected or self._plc_hb_at == 0.0:
            return False
        return (time.monotonic() - self._plc_hb_at) <= PLC_HB_STALL_S

    # ===================== 명령 핸드셰이크 =====================
    async def send_command(self, code: int, args: dict = None):
        """인자 → 명령 코드 → 명령 번호 순서로 쓰고 D00002 가 그 번호가 될 때까지 기다린다.

        반환: (결과코드 또는 None, 설명). None 은 응답 없음(시간 초과)."""
        if not self.connected:
            return None, "PLC 에 연결되어 있지 않습니다"
        async with self._cmd_lock:
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
