"""test/fake_plc.py — 가짜 PLC (별도 프로세스의 Modbus TCP 서버).

PLC 가 준비되기 전에 '실제 공정처럼' 시험하려고 실제 PLC 자리에 둔다. 프로그램은 설정에서
    plc.simulate = false · plc.host = 127.0.0.1 · plc.port = 이 가짜 PLC 의 포트
로 실제 PLC 에 붙듯이 붙는다(내장 시뮬레이터가 아니라 실장비와 똑같은 경로).

★ PLC 쪽 로직은 새로 짜지 않는다 — 래더 스캔 순서 · 타이머 · 알람 · 장비 ID 는 프로그램의 simulator.PlcSim
  그대로다. 여기서 바꾸는 것은 '장비 물리'(압력 · 유량 · 온도 · RF · PCV · O3 곡선과 잡음)뿐이고, 그 값은
  test/fake_plant.json 한곳에서 바꾼다.
★ 시간: 느린 물리(펌핑 · 벤트 · 승온 · 냉각 · O3 농도)만 --speed 배속(1.0 = 실제 시간). 빠른 기기 반응(펄스 압력 감쇠 ·
  PCV · MFC 지연 · RF 램프)과 래더 타이머 · 스텝 시간은 늘 실제 시간이다 — 배속을 올려도 래더의 MFC 안정 시간 ·
  편차 타이머 · RF 반사 시간이 실제 같은 과도를 본다(v0.6.1 C4).

실행:
    python test/fake_plc.py --port 15301                       (기본: 실제 시간 · 대기압에서 시작)
    python test/fake_plc.py --port 15301 --speed 10 --state vac --log fake_plc.log
    python test/fake_plc.py --port 15301 --device-id 0x1234    (틀린 장비 ID 시험)
제어(고장 넣기 · 끊기 · ID 바꾸기)는 test/plc_ctl.py 가 제어 포트(기본 --port + 1)로 보낸다.
    python test/plc_ctl.py --port 15301 fault emo on
    python test/plc_ctl.py --port 15301 cut blackhole 2.0      (unplug = 케이블 뽑힘: 요청도 버림)
설정: --config 가 없으면 프로그램과 같은 기본 설정(config.json, 없으면 예시 설정)을 읽고, 시작할 때 어느 것인지 찍는다.
제어 파일(--ctl-file)을 주면 그 파일에 한 줄씩 JSON 명령을 덧붙여도 된다(결과는 같은 이름 + .out).
    {"op": "fault", "key": "emo", "on": true}
  UTF-8(BOM 있어도) · UTF-16(PowerShell 5 의 >> 기본)을 모두 읽는다.
"""
import os
import sys
import json
import math
import time
import random
import signal
import asyncio
import argparse

import _pkg

_pkg.utf8_console()
A = _pkg.mod("addresses")
DEV = _pkg.mod("device")
SIM = _pkg.mod("simulator")

PLANT_PATH = os.path.join(_pkg.TEST_DIR, "fake_plant.json")
RUNNING_STATES = (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)


def load_plant(path: str = "") -> dict:
    with open(path or PLANT_PATH, encoding="utf-8-sig") as f:      # 메모장이 붙인 BOM 도 읽는다
        return json.load(f)


def _e(x):
    return math.exp(x)


class PlantSim(SIM.PlcSim):
    """래더는 PlcSim 그대로, 물리만 실제 장비처럼(fake_plant.json)."""

    def __init__(self, cfg, plant: dict, speed: float = 1.0, seed=None):
        super().__init__(cfg, speed)
        self.speed = max(0.1, float(speed or 1.0))      # 1.0 = 실제 시간(PlcSim 은 1 아래를 막는다 — 여기서는 느리게도)
        self.plant = plant
        self.rng = random.Random(seed)
        self.true_pressure = self.base_pressure
        self.heater_vel = [0.0] * 12
        self.heater_reach = [None] * 12     # 승온 중 설정에 처음 닿은 물리 시각(오버슈트 시작)
        self.heater_tgt = [None] * 12       # 그때의 설정(바뀌면 오버슈트를 다시 센다)
        self.sim_t = 0.0                    # 물리 시간(배속을 곱한 누적)
        self.rf_fwd_w = 0.0
        self.pcv_pos = 0.0
        self.o3_conc = 0.0
        # 대기압 입력(압력 스위치)이 켜지는 압력 — 장비 물리라 fake_plant.json 에서(래더는 SIM.ATM_INPUT_TORR 를 본다)
        SIM.ATM_INPUT_TORR = float(plant["pressure"].get("atm_input_torr", SIM.ATM_INPUT_TORR))

    # ---------- 시작 상태 ----------
    def set_state(self, spec):
        """'atm' · 'vac' · 'hot' 또는 {"pressure": …, "heaters": {"1": 150, …}}."""
        if isinstance(spec, str):
            states = self.plant.get("states") or {}
            if spec in states:
                spec = states[spec]
            else:
                with open(spec, encoding="utf-8-sig") as f:
                    spec = json.load(f)
        p = float(spec.get("pressure", self.plant["pressure"]["atm_torr"]))
        self.base_pressure = self.true_pressure = self.pressure = p
        for ch, t in (spec.get("heaters") or {}).items():
            self.heater_pv[int(ch) - 1] = float(t)

    # ---------- 물리 ----------
    def _hp(self, ch: int) -> dict:
        h = self.plant.get("heaters") or {}
        out = dict(h.get("default") or {})
        out.update((h.get("ch") or {}).get(str(ch), {}))
        return out

    def _physics(self, sdt, now):
        # sdt = 배속을 곱한 물리 시간 — 펌핑 · 벤트 · 승온 · O3 만. rdt = 실제 시간 — 빠른 기기 반응(C4)
        rdt = sdt / self.speed
        self.sim_t += sdt
        P = self.plant["pressure"]
        atm = float(P["atm_torr"])
        pump_run = self.pump_on and self.pump_run_at is not None and (now - self.pump_run_at) >= 1.0
        ive_open = self.ive_out and self.ive_moved_at is not None and (now - self.ive_moved_at) >= 1.0
        self.pulse *= _e(-rdt / float(P["pulse_decay_s"]))
        bp = self.base_pressure
        if self.vv_on:
            bp += (atm - bp) * (1 - _e(-sdt / float(P["vent_tau_s"])))
        elif pump_run and ive_open:
            # 로그 곡선 — 높은 압력에서는 빨리, 베이스 가까이에서는 천천히
            lp, lu = math.log10(max(bp, 1e-6)), math.log10(float(P["ultimate_torr"]))
            lp += (lu - lp) * (1 - _e(-sdt / float(P["pumpdown_tau_s"])))
            bp = 10 ** lp
        else:
            bp += float(P["leak_torr_per_s"]) * sdt       # 막혀 있으면 조금씩 샌다
        self.base_pressure = bp = max(1.0e-4, min(atm, bp))

        # PCV 개도 · 흐름이 만드는 공정 압력
        if DEV.HAS_PCV:
            tgt = float(self.conv.pcv.to_eng(self.ao[SIM.AO_PCV]) or 0.0)          # 목표 개도(%)
            self.pcv_pos += (tgt - self.pcv_pos) * (1 - _e(-rdt / float(self.plant["pcv"]["tau_s"])))
        flow_slm = sum(self.mfc_pv[:DEV.MFC_COUNT]) / 1000.0
        rise = 0.0
        if ive_open and pump_run:
            k = 1.0
            if DEV.HAS_PCV and self.pcv_pos > 0.5:
                k = 1.0 + float(P["pcv_gain"]) * (1.0 - min(100.0, self.pcv_pos) / 100.0)
            rise = flow_slm * float(P["flow_torr_per_slm"]) * k
        self.true_pressure = max(1.0e-4, min(atm, bp + rise + self.pulse))
        # 게이지가 읽는 값(래더도 이 값으로 판정한다 — 실제 PLC 가 AI 로 판정하듯)
        noise = float(self.plant["gauges"]["cvg_noise_rel"])
        self.pressure = max(1.0e-4, min(atm, self.true_pressure * (1 + self.rng.gauss(0, noise))))

        # MFC — 설정을 tau 로 따라간다(잡음은 내보낼 때)
        tau = float(self.plant["mfc"]["tau_s"])
        for i in range(8):
            sv_raw = self.ao[SIM.ao_mfc(i + 1)] if i < DEV.MFC_COUNT else 0
            s = self.conv.mfc.get(i + 1)
            target = (s.to_eng(sv_raw) if s and s.full else 0.0) or 0.0
            if i == 0 and self.faults.get("mfc1_stuck"):
                target = 0.0
            self.mfc_pv[i] += (target - self.mfc_pv[i]) * (1 - _e(-rdt / tau))

        # 히터 — 램프 속도 제한이 있는 2차 응답(오버슈트 · 정착), 꺼지면 천천히 식는다
        amb = float((self.plant.get("heaters") or {}).get("ambient_c", 25.0))
        for ch in range(12):
            hp = self._hp(ch + 1)
            on = bool((self.heater_power >> ch) & 1)
            if not on:
                self.heater_vel[ch] = 0.0
                self.heater_reach[ch] = None
                self.heater_pv[ch] += (amb - self.heater_pv[ch]) * (1 - _e(-sdt / float(hp["cool_tau_s"])))
                continue
            sv = SIM.heater_temp(self.heater_sv[ch])
            if self.heater_tgt[ch] != sv:
                self.heater_tgt[ch], self.heater_reach[ch] = sv, None
            # 오버슈트 — 승온해 설정에 처음 닿은 뒤 정착 시간 상수만큼 지나 최고(overshoot_c), 그 뒤 줄어든다
            if self.heater_reach[ch] is None and self.heater_vel[ch] > 0 and self.heater_pv[ch] >= sv - 0.3:
                self.heater_reach[ch] = self.sim_t
            if self.heater_reach[ch] is not None:
                x = (self.sim_t - self.heater_reach[ch]) / float(hp["settle_tau_s"])
                sv += float(hp.get("overshoot_c", 0.0)) * x * _e(1 - x)
            wn = 1.0 / float(hp["settle_tau_s"])
            z = float(hp["damping"])
            ramp = float(hp["ramp_c_per_min"]) / 60.0
            n = max(1, int(sdt / 0.05) + 1)               # 큰 배속에서도 안정하게 잘게 나눈다
            h = sdt / n
            for _ in range(n):
                acc = wn * wn * (sv - self.heater_pv[ch]) - 2 * z * wn * self.heater_vel[ch]
                self.heater_vel[ch] = max(-ramp * 4, min(ramp, self.heater_vel[ch] + acc * h))
                self.heater_pv[ch] += self.heater_vel[ch] * h

        # RF 순방향(설정까지 짧게 오름) · PCV · O3 농도
        if DEV.HAS_RF:
            tgt = float(self.conv.rf.to_eng(self.ao[SIM.AO_RF]) or 0.0) if self.rf_on else 0.0     # W
            self.rf_fwd_w += (tgt - self.rf_fwd_w) * (1 - _e(-rdt / max(0.01, float(self.plant["rf"]["ramp_s"]))))
        if DEV.HAS_O3:
            tgt = float(self.conv.o3.to_eng(self.ao[SIM.AO_O3]) or 0.0) if self.o3_gen_on else 0.0
            self.o3_conc += (tgt - self.o3_conc) * (1 - _e(-sdt / float(self.plant["o3"]["tau_s"])))

    def _pulse_bump(self):
        pulse_bits = 0
        for v in DEV.VALVES:
            if v.get("pulse"):
                pulse_bits |= 1 << v["bit"]
        if (self.seq_valves & ~self.prev_valves) & pulse_bits:
            self.pulse += float(self.plant["pressure"]["pulse_rise_torr"])

    def _publish(self):
        super()._publish()
        g = self.plant["gauges"]
        if self.conv.cm.installed:
            self.reg[A.D_CM_RAW] = self.conv.cm.to_raw(
                self.true_pressure * (1 + self.rng.gauss(0, float(g["cm_noise_rel"]))))
        nfs = float(self.plant["mfc"]["noise_fs"])
        for i in range(DEV.MFC_COUNT):
            s = self.conv.mfc.get(i + 1)
            if s and s.full:
                v = self.mfc_pv[i] + (self.rng.gauss(0, nfs * float(s.full)) if self.mfc_pv[i] > 0.5 else 0.0)
                self.reg[A.D_MFC_PV + i] = s.to_raw(max(0.0, v))
        for ch in range(12):                      # 잡음은 채널마다(fake_plant.json heaters.ch.N.noise_c)
            hn = float(self._hp(ch + 1).get("noise_c", 0.0))
            self.reg[A.D_HEATER_PV + ch] = SIM.heater_raw(self.heater_pv[ch] + self.rng.gauss(0, hn))
        if DEV.HAS_PCV:
            self.reg[A.D_PCV_RAW] = self.conv.pcv.to_raw(max(0.0, self.pcv_pos))
        if DEV.HAS_RF:
            r = self.plant["rf"]
            fwd = max(0.0, self.rf_fwd_w + (self.rng.gauss(0, float(r["noise_w"])) if self.rf_on else 0.0))
            ratio = float(r["fault_reflect_ratio"] if self.faults.get("rf_ref") else r["reflect_ratio"])
            self.reg[A.D_RF_FWD_RAW] = self.conv.rf.to_raw(fwd) if self.rf_on else 0
            self.reg[A.D_RF_REF_RAW] = self.conv.rf.to_raw(fwd * ratio) if self.rf_on else 0
        if DEV.HAS_O3:
            c = self.o3_conc * (1 + self.rng.gauss(0, float(self.plant["o3"]["noise_rel"])))
            self.reg[A.D_O3_RAW] = self.conv.o3.to_raw(max(0.0, c)) if self.o3_gen_on else 0

    # 보기용 요약(plc_ctl status · 제어 포트)
    def summary(self) -> dict:
        return {
            "device": DEV.KEY, "device_id": self.device_id, "speed": self.speed,
            "state": self.reg[A.D_STATE], "state_name": A.STATE_NAMES.get(self.reg[A.D_STATE], "?"),
            "seq": self.reg[A.D_SEQ_STATE], "pressure": self.pressure, "true_pressure": self.true_pressure,
            "heaters": [round(x, 2) for x in self.heater_pv[:DEV.HEATER_COUNT]],
            "mfc": [round(x, 2) for x in self.mfc_pv[:DEV.MFC_COUNT]],
            "pump": self.pump_on, "iv_e": self.ive_out, "vv": self.vv_on,
            "rf_w": round(self.rf_fwd_w, 1) if DEV.HAS_RF else None,
            "o3": round(self.o3_conc, 2) if DEV.HAS_O3 else None,
            "faults": sorted(k for k, v in self.faults.items() if v),
        }


class FakeServer(SIM.SimServer):
    """SimServer + 통신 끊기(블랙홀 · 케이블 뽑힘 · 연결 거부) + PLC 쪽 기록."""

    def __init__(self, sim: PlantSim, host: str, port: int, logf=None, hb_gap_ms: float = 600.0):
        super().__init__(sim, host, port)
        self.blackhole_until = 0.0
        self.unplug_until = 0.0
        self.refusing = False
        self.logf = logf
        self.hb_gap_ms = float(hb_gap_ms)
        self._hb_at = None
        self._last = {}

    def log(self, text: str):
        line = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}  {text}"
        if self.logf:
            self.logf.write(line + "\n")
            self.logf.flush()

    # ---------- 끊기 ----------
    def cut(self, mode: str, seconds: float):
        seconds = max(0.0, float(seconds))
        if mode == "blackhole":
            self.blackhole_until = time.monotonic() + seconds
            self.log(f"통신 끊기: 블랙홀 {seconds:g} s (요청은 받고 답하지 않음)")
        elif mode == "unplug":
            self.unplug_until = time.monotonic() + seconds
            self.log(f"통신 끊기: 케이블 뽑힘 {seconds:g} s (요청도 버리고 답하지 않음)")
        elif mode == "refuse":
            asyncio.get_event_loop().create_task(self._refuse(seconds))
        else:
            raise ValueError("mode 는 blackhole · unplug · refuse")

    async def _refuse(self, seconds: float):
        self.log(f"통신 끊기: 연결 거부 {seconds:g} s (연결을 끊고 포트를 닫음)")
        self.refusing = True
        if self._server:
            self._server.close()
        for w in list(self._clients):
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        await asyncio.sleep(seconds)
        for _ in range(50):
            try:
                self._server = await asyncio.start_server(self._client, self.host, self.port)
                break
            except OSError:
                await asyncio.sleep(0.1)
        self.refusing = False
        self.log("통신 끊기 끝: 다시 연결을 받음")

    async def _client(self, reader, writer):
        if self.refusing:
            writer.close()
            return
        self._clients.add(writer)
        try:
            while True:
                head = await reader.readexactly(7)
                tid, _proto, length, unit = SIM.struct.unpack(">HHHB", head)
                body = await reader.readexactly(max(0, length - 1))
                if time.monotonic() < self.unplug_until:
                    continue                    # 케이블 뽑힘 — 요청이 PLC 에 닿지 않는다(쓰기도 반영 안 됨)
                resp = self._handle(body)
                if time.monotonic() < self.blackhole_until:
                    continue                    # 블랙홀 — 받기만 하고 답하지 않는다
                writer.write(SIM.struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------- PLC 쪽 기록: 하트비트 공백 · 받은 명령 ----------
    def _handle(self, pdu: bytes) -> bytes:
        resp = super()._handle(pdu)
        try:
            if pdu and pdu[0] in (6, 16) and not (resp and resp[0] & 0x80):
                addr = SIM.struct.unpack(">H", pdu[1:3])[0]
                count = 1 if pdu[0] == 6 else SIM.struct.unpack(">H", pdu[3:5])[0]
                hi = addr + count - 1
                now = time.monotonic()
                if addr <= A.D_PC_HB <= hi:
                    if self._hb_at is not None:
                        gap = (now - self._hb_at) * 1000.0
                        if gap >= self.hb_gap_ms:
                            self.log(f"PC 하트비트 공백 {gap:.0f} ms")
                    self._hb_at = now
                if addr <= A.D_CMD_NO <= hi:           # 명령은 번호를 쓸 때 한 번(코드 · 인자는 먼저 쓴다)
                    reg = self.sim.reg
                    code = reg[A.D_CMD_CODE]
                    self.log(f"명령 받음: 번호 {reg[A.D_CMD_NO]} · 코드 {code} "
                             f"({A.CMD_NAMES.get(code, '?')}) · 인자 {reg[A.D_CMD_ARG]}")
                elif addr >= A.D_PC_HB and hi <= SIM.PC_AREA_LAST and not (addr <= A.D_PC_HB <= hi):
                    self.log(f"PC 영역 쓰기 D{addr:05d}~D{hi:05d} ({count} 워드)")
        except Exception:  # noqa: BLE001
            pass
        return resp

    async def _loop(self):
        while True:
            try:
                self.sim.tick()
                self._watch()
            except Exception as e:  # noqa: BLE001
                self.log(f"tick 오류(계속): {type(e).__name__}: {e}")
            await asyncio.sleep(SIM.TICK_S)

    def _watch(self):
        """상태 변화 기록 — 장비 상태 · 시퀀서 · 알람 워드 · 결과 코드."""
        reg = self.sim.reg
        cur = {"state": reg[A.D_STATE], "seq": reg[A.D_SEQ_STATE],
               "alm0": reg[A.D_ALARM0], "alm1": reg[A.D_ALARM1]}
        for k, v in cur.items():
            old = self._last.get(k)
            if old is None or old == v:
                continue
            if k == "state":
                self.log(f"장비 상태 {A.STATE_NAMES.get(old, old)} → {A.STATE_NAMES.get(v, v)}")
            elif k == "seq":
                self.log(f"시퀀서 {A.SEQ_NAMES.get(old, old)} → {A.SEQ_NAMES.get(v, v)} "
                         f"(블록 {reg[A.D_SEQ_BLOCK]} · 스텝 {reg[A.D_SEQ_STEP]})")
            else:
                self.log(f"알람 워드 {k[-1]}: 0x{old:04X} → 0x{v:04X}")
        self._last = cur


class Control:
    """제어 포트(JSON 한 줄 요청 → JSON 한 줄 답) · 제어 파일."""

    def __init__(self, sim: PlantSim, srv: FakeServer, stop_event: asyncio.Event):
        self.sim, self.srv, self.stop_event = sim, srv, stop_event

    def run(self, req: dict) -> dict:
        op = req.get("op")
        sim, srv = self.sim, self.srv
        if op == "ping":
            return {"ok": True, "fake": True, "device": DEV.KEY, "port": srv.port, "pid": os.getpid()}
        if op == "faults":
            return {"ok": True, "faults": [{"key": f["key"], "name": f["name"], "on": sim.faults.get(f["key"], False)}
                                           for f in SIM.visible_faults()]}
        if op == "fault":
            key = req.get("key")
            if key not in sim.faults or key not in {f["key"] for f in SIM.visible_faults()}:
                return {"ok": False, "why": f"모르는 고장: {key}"}
            sim.set_fault(key, bool(req.get("on", True)))
            srv.log(f"고장 {key} {'켬' if req.get('on', True) else '끔'}")
            return {"ok": True}
        if op == "cut":
            srv.cut(req.get("mode", "blackhole"), float(req.get("s", 1.0)))
            return {"ok": True}
        if op == "id":
            sim.device_id = _pkg.parse_id(req.get("value"))          # 늘 16 진(h5045 표기와 같게)
            srv.log(f"장비 ID → 0x{sim.device_id:04X}")
            return {"ok": True, "device_id": sim.device_id}
        if op == "speed":
            sim.speed = max(0.1, float(req.get("value", 1.0)))
            srv.log(f"배속 → {sim.speed:g}")
            return {"ok": True}
        if op == "state":
            sim.set_state(req.get("name", "atm"))
            srv.log(f"물리 상태 → {req.get('name')}")
            return {"ok": True}
        if op == "status":
            now = time.monotonic()
            return {"ok": True, **sim.summary(), "cut": {"blackhole": max(0.0, srv.blackhole_until - now),
                                                         "unplug": max(0.0, srv.unplug_until - now),
                                                         "refusing": srv.refusing}}
        if op == "quit":
            self.stop_event.set()
            return {"ok": True}
        return {"ok": False, "why": f"모르는 명령: {op}"}

    async def client(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    out = self.run(json.loads(line.decode("utf-8")))
                except Exception as e:  # noqa: BLE001
                    out = {"ok": False, "why": f"{type(e).__name__}: {e}"}
                writer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def watch_file(self, path: str):
        pos = 0
        while True:
            try:
                if os.path.isfile(path):
                    with open(path, "rb") as f:
                        f.seek(pos)
                        data = f.read()
                    # 줄이 다 써진 데까지만(덧붙이는 중인 줄은 다음 번에)
                    cut = _utf16_cut(data) if _utf16(path) else data.rfind(b"\n") + 1
                    pos += cut
                    for line in _lines(data[:cut], path):
                        try:
                            out = self.run(json.loads(line))
                        except Exception as e:  # noqa: BLE001
                            out = {"ok": False, "why": f"{type(e).__name__}: {e}"}
                        with open(path + ".out", "a", encoding="utf-8") as o:
                            o.write(json.dumps({"req": line, **out}, ensure_ascii=False) + "\n")
            except OSError:
                pass
            await asyncio.sleep(0.2)


def _utf16(path: str) -> bool:
    """제어 파일이 UTF-16 인가(PowerShell 5 의 >> · Out-File 기본) — 첫 두 바이트(BOM)로."""
    try:
        with open(path, "rb") as f:
            return f.read(2) in (b"\xff\xfe", b"\xfe\xff")
    except OSError:
        return False


def _utf16_cut(data: bytes) -> int:
    """UTF-16 LE 조각에서 마지막 줄바꿈(0A 00) 바로 뒤 위치(짝수 자리만)."""
    for i in range(len(data) - 2 - (len(data) % 2), -1, -2):
        if data[i] == 0x0A and data[i + 1] == 0:
            return i + 2
    return 0


def _lines(chunk: bytes, path: str) -> list:
    """제어 파일 조각 → 빈 줄을 뺀 줄들. UTF-8(BOM 있어도) · UTF-16 LE 를 읽는다(C12)."""
    if not chunk:
        return []
    text = chunk.decode("utf-16-le" if _utf16(path) else "utf-8", "replace")
    out = []
    for ln in text.splitlines():
        ln = ln.strip().lstrip("﻿").strip()
        if ln:
            out.append(ln)
    return out


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=f"가짜 PLC ({DEV.KEY}) — 실제 PLC 자리에 두는 Modbus TCP 서버")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True, help="Modbus TCP 포트(프로그램 plc.port)")
    ap.add_argument("--ctl-port", type=int, default=0, help="제어 포트(기본 --port + 1)")
    ap.add_argument("--speed", type=float, default=1.0, help="물리 배속(1 = 실제 시간, 빨리 볼 때 2~20)")
    ap.add_argument("--device-id", default="", help=f"장비 ID — 16 진(기본 {DEV.DEVICE_ID:04X} — 틀린 ID 시험용)")
    ap.add_argument("--state", default="atm", help="시작 상태: atm · vac · hot 또는 JSON 파일")
    ap.add_argument("--config", default="", help="프로그램과 같은 설정 파일(아날로그 환산이 같아야 한다 — "
                                                 "없으면 프로그램 기본 config.json, 그것도 없으면 예시 설정)")
    ap.add_argument("--plant", default="", help="물리 값 파일(기본 test/fake_plant.json)")
    ap.add_argument("--log", default="", help="PLC 쪽 기록 파일(하트비트 공백 · 받은 명령 · 상태 변화)")
    ap.add_argument("--ctl-file", default="", help="제어 파일 — 한 줄씩 JSON 명령을 덧붙이면 실행")
    ap.add_argument("--seed", type=int, default=None, help="잡음 씨앗(같은 값이면 같은 잡음)")
    ap.add_argument("--exit-on-eof", action="store_true",
                    help="stdin 이 닫히면(EOF) 끝낸다 — 시험 틀이 stdin=PIPE 로 띄울 때(틀이 강제로 꺼져도 남지 않게)")
    return ap.parse_args(argv)


async def amain(args) -> int:
    # 설정만 읽는다 — 프로그램 데이터 폴더(data/)에는 아무것도 쓰지 않는다. --config 가 없으면 프로그램과 같은
    # 기본 설정(config.json, 없으면 예시) — 아날로그 환산이 프로그램과 같아야 한다(C5)
    cfg, cfg_used, cfg_what = _pkg.load_config(args.config)
    print(f"[info] 설정: {cfg_what} {cfg_used}", flush=True)
    plant = load_plant(args.plant)
    sim = PlantSim(cfg, plant, args.speed, args.seed)
    if args.device_id:
        sim.device_id = _pkg.parse_id(args.device_id)
    sim.set_state(args.state)
    logf = open(args.log, "a", encoding="utf-8") if args.log else None
    srv = FakeServer(sim, args.host, args.port, logf)
    if not await srv.start():
        print(f"[error] {srv.error}", flush=True)
        return 2
    stop = asyncio.Event()
    ctl = Control(sim, srv, stop)
    ctl_port = args.ctl_port or (args.port + 1)
    try:
        ctl_srv = await asyncio.start_server(ctl.client, "127.0.0.1", ctl_port)
    except OSError as e:
        print(f"[error] 제어 포트 {ctl_port} 를 열지 못했습니다 — {e}", flush=True)
        await srv.stop()
        return 2
    tasks = []
    if args.ctl_file:
        tasks.append(asyncio.create_task(ctl.watch_file(args.ctl_file)))
    srv.log(f"가짜 PLC 시작 — {DEV.KEY} · {args.host}:{args.port} · 제어 {ctl_port} · 배속 {sim.speed:g} · "
            f"ID 0x{sim.device_id:04X} · 상태 {args.state} · {cfg_what} {os.path.basename(cfg_used)}")
    print(f"[info] 가짜 PLC {DEV.KEY} — modbus {args.host}:{args.port} · 제어 127.0.0.1:{ctl_port} · "
          f"배속 {sim.speed:g}", flush=True)
    loop = asyncio.get_event_loop()
    if args.exit_on_eof:
        _pkg.exit_on_stdin_eof(lambda: loop.call_soon_threadsafe(stop.set))
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass                                    # Windows — Ctrl+C 는 KeyboardInterrupt 로
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        ctl_srv.close()
        await srv.stop()
        srv.log("가짜 PLC 끝")
        if logf:
            logf.close()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
