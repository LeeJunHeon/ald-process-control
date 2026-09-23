"""
providers/demo.py — 데모 공정 흉내 provider.

실장비 없이 화면 전체를 검증하기 위한 것이다. 값은 전부 여기서 만들어지고,
provider 경계 밖으로는 실장비와 똑같은 모양으로 나간다(base.Provider 계약).

★ 실장비로 착각하는 사고를 막으려고 is_demo=True 를 노출한다 — 화면 헤더에 '데모' 칩이
  항상 뜬다. 이 플래그를 끄거나 숨기지 마라.
★ 장비 이름·밸브 태그를 여기에 쓰지 않는다. 전부 config 에서 읽는다.
"""

import math
import time
import random

from config import (ROLE_ALD, KIND_N2, SIDE_PRECURSOR, SIDE_REACTANT,
                    line_by_id, enabled_lines)
from recipe_model import step_open_tags, block_cycle_seconds
from providers.base import Provider, OK

# ---- 시뮬레이션 상수 (전부 이 자리에 모은다) ----
PROC_BASE_TORR = 0.235        # 공정 중 베이스 압력
IDLE_BASE_TORR = 1.8e-2       # 대기(펌핑 완료) 베이스 압력
ATM_TORR = 760.0
SPIKE_AMPL = 0.075            # 펄스 스텝 진입 시 압력 상승폭
SPIKE_TAU_S = 0.9             # 스파이크 지수 감쇠 시정수
PRESSURE_NOISE = 0.0016
MFC_NOISE_PCT_FS = 0.3        # MFC PV 잡음 (% FS)
HEATER_NOISE_C = 0.25
AMBIENT_C = 24.5              # 끈 히터가 수렴하는 온도
FIRST_ALARM_AFTER_S = 30.0    # running 시나리오에서 경고 알람이 뜨는 시점
RUNNING_START_CYCLE = 128     # running 시나리오 기동 시 사이클 위치
PUMP_DOWN_S = 25.0            # 펌핑 목표 도달 시간(데모)
VENT_UP_S = 20.0              # 벤트 ATM 도달 시간(데모)


class DemoProvider(Provider):
    is_demo = True

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._rng = random.Random(hash(cfg.get("chamber", {}).get("id", "")) & 0xFFFF)
        self._logs = []
        self._alarms = []        # 현재 알람
        self._history = []       # 알람 이력(최근이 앞)
        self._t0 = time.monotonic()
        self._now = self._t0

        # --- 밸브/IO ---
        self.valves = {}
        for ln in cfg.get("lines") or []:
            for tag in (ln.get("valves") or {}).values():
                self.valves[tag] = False
        io = cfg.get("chamber_io") or {}
        self._vent_tag = (io.get("vent") or {}).get("tag") or "VENT"
        self._rv_tag = (io.get("rv") or {}).get("tag") or "RV"
        self.valves[self._vent_tag] = False
        self.valves[self._rv_tag] = False

        self.dry_pump = False
        self.tv_pct = 100.0
        self._pressure = ATM_TORR
        self._base = ATM_TORR       # 스파이크를 뺀 베이스 압력
        self._target_p = ATM_TORR
        self._spike = 0.0

        # --- MFC / 히터 ---
        self.mfc_sv = {ln["id"]: 0.0 for ln in cfg.get("lines") or []}
        self.heater_sv = {}
        for h in cfg.get("heaters") or []:
            self.heater_sv[h["id"]] = float(h.get("default_sv") or 0.0) if h.get("enabled", True) else 0.0

        # --- 공정 ---
        self.recipe = None
        self.mode = "idle"        # idle | running | paused | stopping
        self._seq = []            # 반복 그룹을 펼친 블록 인덱스 순서
        self._pos = 0             # _seq 위치
        self._cycle = 1           # 현재 블록 안의 사이클 번호
        self._step = 0
        self._step_t = 0.0        # 현재 스텝 경과
        self._elapsed = 0.0
        self._plan_total = 0.0
        self._stop_req = False
        self._alarm_fired = False

        self._scenario = ((cfg.get("demo") or {}).get("scenario") or "idle").lower()
        self._apply_idle_vacuum()

    # ===================== 준비 =====================
    def prime(self, recipe: dict):
        """기동 시나리오 적용. 서버가 샘플 레시피를 확보한 뒤 한 번 부른다.
        running 이면 공정 중간(사이클 RUNNING_START_CYCLE)부터 시작해 화면을 바로 검증할 수 있다."""
        if self._scenario != "running" or not recipe:
            self._log("info", "대기 상태로 기동 — 시작 조건 충족")
            return
        ok, why = self.start_process(recipe)
        if not ok:
            self._log("warn", f"데모 시나리오를 시작하지 못했습니다: {why}")
            return
        # 사이클이 가장 많은 블록(= 본 증착 블록)으로 건너뛴다.
        target = max(range(len(self._seq)),
                     key=lambda i: self._blk(self._seq[i]).get("repeat", 1), default=0)
        self._pos = target
        blk = self._blk(self._seq[self._pos])
        self._cycle = min(RUNNING_START_CYCLE, max(1, int(blk.get("repeat") or 1)))
        self._step = 0
        self._step_t = 0.0
        self._elapsed = self._elapsed_upto(self._pos, self._cycle, 0, 0.0)
        self._apply_step()
        self._log("info", f"사이클 {self._cycle} / {blk.get('repeat')} 진행 중")

    # ===================== 주기 =====================
    def tick(self, now: float):
        dt = max(0.0, min(0.5, now - self._now))   # 큰 점프(디버거 정지 등)는 잘라낸다
        self._now = now
        if dt <= 0:
            return
        if self.mode == "running":
            self._advance(dt)
        self._update_pressure(dt)
        self._maybe_first_alarm()

    def _advance(self, dt: float):
        self._elapsed += dt
        self._step_t += dt
        steps = self._steps()
        if not steps:
            self._finish("레시피에 스텝이 없어 공정을 종료합니다")
            return
        cur = steps[self._step]
        dur = max(0.001, float(cur.get("time_s") or 0))
        while self._step_t >= dur:
            self._step_t -= dur
            self._step += 1
            if self._step >= len(steps):
                self._step = 0
                if not self._next_cycle():
                    return
                steps = self._steps()
                if not steps:
                    self._finish("레시피에 스텝이 없어 공정을 종료합니다")
                    return
            self._apply_step()
            cur = steps[self._step]
            dur = max(0.001, float(cur.get("time_s") or 0))

    def _next_cycle(self) -> bool:
        """사이클을 하나 넘긴다. 공정이 끝나면 False."""
        blk = self._blk(self._seq[self._pos])
        rep = max(1, int(blk.get("repeat") or 1))
        if self._cycle < rep:
            self._cycle += 1
            if self._cycle % 50 == 0:
                self._log("info", f"사이클 {self._cycle} / {rep} 완료")
            return True
        # 블록 끝. '사이클 후 정지' 요청이 있었으면 여기서 멈춘다.
        if self._stop_req:
            self._finish("사이클 후 정지 — 공정을 마쳤습니다")
            return False
        self._log("info", f"블록 완료: {blk.get('name', '')}")
        self._pos += 1
        self._cycle = 1
        if self._pos >= len(self._seq):
            self._finish("공정이 정상 종료되었습니다")
            return False
        self._log("info", f"블록 시작: {self._blk(self._seq[self._pos]).get('name', '')}")
        self._apply_block_mfc()
        return True

    def _finish(self, msg: str):
        self.mode = "idle"
        self._stop_req = False
        self._close_all_line_valves()
        self._open_always()          # 종료 후에도 N2 퍼지는 유지한다
        self.tv_pct = 100.0
        self._log("ok", msg)

    # ===================== 공정 명령 =====================
    def start_process(self, recipe: dict):
        if self.mode in ("running", "stopping"):
            return False, "이미 공정이 진행 중입니다"
        if not recipe or not (recipe.get("blocks") or []):
            return False, "실행할 블록이 없는 레시피입니다"
        self.recipe = recipe
        self._seq = self._expand(recipe)
        if not self._seq:
            return False, "실행할 블록이 없는 레시피입니다"
        self._pos = 0
        self._cycle = 1
        self._step = 0
        self._step_t = 0.0
        self._elapsed = 0.0
        self._stop_req = False
        self._plan_total = self._plan_seconds()
        self.mode = "running"
        self.dry_pump = True
        self.valves[self._vent_tag] = False
        self.valves[self._rv_tag] = True
        cond = recipe.get("conditions") or {}
        self.tv_pct = float(cond.get("throttle_pct") or 100)
        self._target_p = PROC_BASE_TORR
        self._apply_block_mfc()
        self._apply_step()
        self._log("ok", f"공정 시작 — {recipe.get('name', '')}")
        return OK

    def pause(self):
        if self.mode == "running":
            self.mode = "paused"
            self._log("warn", "일시정지 — 밸브 상태를 유지합니다")
            return OK
        if self.mode == "paused":
            self.mode = "running"
            self._log("ok", "공정 재개")
            return OK
        return False, "진행 중인 공정이 없습니다"

    def stop_after_cycle(self):
        if self.mode not in ("running", "paused"):
            return False, "진행 중인 공정이 없습니다"
        self._stop_req = not self._stop_req
        self._log("info", "사이클 후 정지 예약" if self._stop_req else "사이클 후 정지 예약 해제")
        return OK

    def abort(self):
        if self.mode == "idle":
            return False, "진행 중인 공정이 없습니다"
        self.mode = "idle"
        self._stop_req = False
        # 즉시 중단: ALD 밸브를 전부 닫고 N2 퍼지 상태로 둔다.
        # 전구체가 챔버에 남으면 리드를 열 때 대기와 반응한다.
        self._close_all_line_valves()
        self._open_always()
        self.tv_pct = 100.0
        self._target_p = IDLE_BASE_TORR
        self._alarm("OP-ABORT", "info", f"운전자 즉시 중단 — {(self.recipe or {}).get('name', '')}")
        self._log("warn", "즉시 중단 — ALD 밸브 닫음 · N2 퍼지 유지")
        return OK

    # ===================== 수동 조작 =====================
    def set_valve(self, tag: str, open_: bool):
        if tag not in self.valves:
            return False, f"알 수 없는 밸브입니다: {tag}"
        self.valves[tag] = bool(open_)
        self._log("info", f"밸브 {tag} {'열림' if open_ else '닫힘'}")
        return OK

    def pump(self):
        self.dry_pump = True
        self.valves[self._vent_tag] = False
        self.valves[self._rv_tag] = True
        self.tv_pct = 100.0
        self._target_p = IDLE_BASE_TORR
        self._log("ok", "펌핑 시작 — 러핑 밸브 열림")
        return OK

    def vent(self):
        self.valves[self._rv_tag] = False
        self.valves[self._vent_tag] = True
        self._target_p = ATM_TORR
        self._log("warn", "벤트 시작 — 러핑 밸브 닫힘")
        return OK

    def all_close(self):
        for tag in self.valves:
            self.valves[tag] = False
        self._log("warn", "전체 밸브 닫기")
        return OK

    def set_heater_sv(self, updates: dict):
        n = 0
        for hid, sv in (updates or {}).items():
            if hid in self.heater_sv:
                try:
                    self.heater_sv[hid] = float(sv)
                    n += 1
                except (TypeError, ValueError):
                    continue
        if not n:
            return False, "적용할 히터 SV 가 없습니다"
        self._log("ok", f"히터 SV 적용 — {n}개 채널")
        return OK

    # ===================== 알람 =====================
    def ack_alarm(self, code: str = ""):
        hit = 0
        for a in self._alarms:
            if not code or a["code"] == code:
                if not a["ack"]:
                    a["ack"] = True
                    hit += 1
        if not hit:
            return False, "확인할 알람이 없습니다"
        self._log("info", f"알람 확인 — {code or '전체'}")
        return OK

    def reset_alarms(self):
        if not self._alarms:
            return False, "해제할 알람이 없습니다"
        ts = time.strftime("%H:%M:%S")
        for a in self._alarms:
            for h in self._history:
                if h["code"] == a["code"] and not h["cleared"]:
                    h["cleared"] = ts
        self._alarms.clear()
        self._log("ok", "알람 리셋")
        return OK

    def alarm_history(self):
        return list(self._history)

    def drain_logs(self):
        out = list(self._logs)
        self._logs.clear()
        return out

    # ===================== 스냅샷 =====================
    def read_snapshot(self) -> dict:
        return {
            "plc": {"connected": True, "hb_ok": True, "rtt_ms": 12},
            "valves": dict(self.valves),
            "mfc": self._mfc_values(),
            "heaters": self._heater_values(),
            "gauges": {
                "baratron": round(self._pressure, 4),
                "convectron": round(self._pressure, 5),
            },
            "io": {
                "dry_pump": self.dry_pump,
                "rv": self.valves.get(self._rv_tag, False),
                "vent": self.valves.get(self._vent_tag, False),
                "tv_pct": round(self.tv_pct, 1),
            },
            "interlocks": {k["id"]: True for k in self.cfg.get("interlocks") or []},
            "process": self._process_values(),
            "alarms": [dict(a) for a in self._alarms],
        }

    # ===================== 내부 계산 =====================
    def _mfc_values(self):
        out = {}
        for ln in self.cfg.get("lines") or []:
            lid = ln["id"]
            if not ln.get("enabled", True):
                out[lid] = {"sv": None, "pv": None}
                continue
            fs = float((ln.get("mfc") or {}).get("full_scale") or 100)
            sv = float(self.mfc_sv.get(lid) or 0.0)
            pv = sv + self._rng.uniform(-1, 1) * fs * MFC_NOISE_PCT_FS / 100.0 if sv > 0 else 0.0
            out[lid] = {"sv": round(sv, 1), "pv": round(max(0.0, pv), 1)}
        return out

    def _heater_values(self):
        out = {}
        for h in self.cfg.get("heaters") or []:
            hid = h["id"]
            on = bool(h.get("enabled", True))
            sv = float(self.heater_sv.get(hid) or 0.0)
            if not on or sv <= 0:
                out[hid] = {"sv": None, "pv": round(AMBIENT_C + self._rng.uniform(-.3, .3), 1),
                            "out_pct": None, "on": False}
                continue
            dev = self._rng.uniform(-HEATER_NOISE_C, HEATER_NOISE_C)
            # 알람이 걸린 히터는 실제로 편차를 보여야 화면과 알람 내용이 맞다.
            for a in self._alarms:
                if a.get("heater") == hid:
                    dev = -(float(h.get("dev_warn") or 2.0) + 0.1)
            pv = sv + dev
            out[hid] = {"sv": round(sv, 1), "pv": round(pv, 1),
                        "out_pct": int(20 + (sv / max(1.0, float(h.get("max") or 200))) * 60),
                        "on": True}
        return out

    def _process_values(self):
        steps = self._steps()
        blk = self._blk(self._seq[self._pos]) if self._seq and self._pos < len(self._seq) else {}
        cur = steps[self._step] if steps and self._step < len(steps) else {}
        remaining = max(0.0, self._plan_total - self._elapsed) if self.mode != "idle" else 0.0
        return {
            "mode": self.mode,
            "stop_after_cycle": self._stop_req,
            "recipe": (self.recipe or {}).get("name", ""),
            "block": self._pos,
            "block_count": len(self._seq),
            "block_name": blk.get("name", ""),
            "cycle": self._cycle if self.mode != "idle" else 0,
            "cycles": int(blk.get("repeat") or 0),
            "step": self._step,
            "step_count": len(steps),
            "step_name": cur.get("name", ""),
            "step_elapsed_s": round(self._step_t, 2),
            "step_total_s": round(float(cur.get("time_s") or 0), 2),
            "steps": [{"name": s.get("name", ""), "time_s": float(s.get("time_s") or 0)}
                      for s in steps],
            "elapsed_s": round(self._elapsed, 1),
            "remaining_s": round(remaining, 1),
            "eta": time.strftime("%H:%M", time.localtime(time.time() + remaining))
            if self.mode != "idle" else "",
        }

    def _update_pressure(self, dt: float):
        """베이스 압력(느리게 수렴)과 펄스 스파이크(빠르게 감쇠)를 따로 계산해서 더한다.

        ★ 스파이크를 베이스에 직접 더하면 다음 tick 의 수렴 계산이 그 값을 출발점으로 삼아
          펄스마다 베이스가 조금씩 올라간다(계단식 상승). 반드시 분리해서 보관한다."""
        self._spike *= math.exp(-dt / SPIKE_TAU_S)
        if self.mode in ("running", "paused"):
            target = PROC_BASE_TORR
        elif self.valves.get(self._vent_tag):
            target = ATM_TORR
        elif self.dry_pump and self.valves.get(self._rv_tag):
            target = IDLE_BASE_TORR
        else:
            target = self._target_p
        self._target_p = target
        # 1차 지연으로 목표에 수렴. 펌핑/벤트 속도는 로그 스케일이라 비율로 움직인다.
        tau = PUMP_DOWN_S / 4 if target < self._base else VENT_UP_S / 4
        self._base = target + (self._base - target) * math.exp(-dt / max(0.5, tau))
        noise = self._rng.uniform(-1, 1) * PRESSURE_NOISE * (1 if self._base < 1 else 0)
        self._pressure = max(1e-4, self._base + self._spike + noise)

    def _maybe_first_alarm(self):
        """기동 후 일정 시간에 경고 알람 1건. 알람 화면과 칩이 실제로 동작하는지 보여준다."""
        if self._alarm_fired or self._scenario != "running":
            return
        if self._now - self._t0 < FIRST_ALARM_AFTER_S:
            return
        self._alarm_fired = True
        h = self._alarm_heater()
        if not h:
            return
        sv = float(self.heater_sv.get(h["id"]) or 0.0)
        dev = float(h.get("dev_warn") or 2.0)
        self._alarm(f"HTR-DEV-{h['id'].upper()}", "warn",
                    f"{h.get('label', h['id'])} 온도 편차 — PV {sv - dev - 0.1:.1f} °C / "
                    f"SV {sv:.1f} °C (허용 ±{dev:.1f} °C)", heater=h["id"])
        self._log("warn", f"경고 발생 — {h.get('label', h['id'])} 온도 편차 · 공정 계속")

    def _alarm_heater(self):
        """편차 알람을 낼 히터를 고른다 — 가열 캐니스터가 있으면 그것, 없으면 첫 히터.
        ★ 특정 라인 이름을 코드에 박지 않기 위해 config 에서 골라낸다."""
        heaters = [h for h in self.cfg.get("heaters") or [] if h.get("enabled", True)]
        for h in heaters:
            ln = line_by_id(self.cfg, h.get("line") or "")
            if ln is not None and ln.get("heated"):
                return h
        return heaters[0] if heaters else None

    def _alarm(self, code, level, msg, heater=None):
        ts = time.strftime("%H:%M:%S")
        rec = {"code": code, "level": level, "msg": msg, "ts": ts,
               "date": time.strftime("%m-%d"), "ack": False, "heater": heater}
        if level != "info":
            self._alarms.append(rec)
        self._history.insert(0, {**rec, "cleared": ts if level == "info" else ""})
        del self._history[200:]

    # ---------- 레시피 진행 보조 ----------
    def _expand(self, recipe) -> list:
        """반복 그룹을 펼쳐 '실행할 블록 인덱스'의 순서로 만든다.
        그룹 안의 블록 구간이 repeat 만큼 되풀이된다(라미네이트)."""
        blocks = recipe.get("blocks") or []
        groups = sorted((recipe.get("groups") or []),
                        key=lambda g: int(g.get("from_block") or 0))
        seq, i = [], 0
        while i < len(blocks):
            g = next((g for g in groups if int(g.get("from_block") or -1) == i), None)
            if g:
                a, b = int(g.get("from_block")), int(g.get("to_block"))
                b = min(max(a, b), len(blocks) - 1)
                seq.extend(list(range(a, b + 1)) * max(1, int(g.get("repeat") or 1)))
                i = b + 1
            else:
                seq.append(i)
                i += 1
        return seq

    def _blk(self, idx):
        blocks = (self.recipe or {}).get("blocks") or []
        return blocks[idx] if 0 <= idx < len(blocks) else {}

    def _steps(self):
        if not self._seq or self._pos >= len(self._seq):
            return []
        return self._blk(self._seq[self._pos]).get("steps") or []

    def _plan_seconds(self):
        return round(sum(block_cycle_seconds(self._blk(i)) * max(1, int(self._blk(i).get("repeat") or 1))
                         for i in self._seq), 3)

    def _elapsed_upto(self, pos, cycle, step, step_t):
        t = sum(block_cycle_seconds(self._blk(i)) * max(1, int(self._blk(i).get("repeat") or 1))
                for i in self._seq[:pos])
        blk = self._blk(self._seq[pos]) if pos < len(self._seq) else {}
        t += block_cycle_seconds(blk) * (cycle - 1)
        t += sum(float(s.get("time_s") or 0) for s in (blk.get("steps") or [])[:step]) + step_t
        return round(t, 3)

    def _apply_block_mfc(self):
        """블록 시작 시 MFC 를 한 번 설정한다(스텝마다 바꾸지 않는다)."""
        blk = self._blk(self._seq[self._pos]) if self._seq else {}
        for lid in self.mfc_sv:
            self.mfc_sv[lid] = 0.0
        for lid, sccm in (blk.get("mfc") or {}).items():
            if lid in self.mfc_sv:
                try:
                    self.mfc_sv[lid] = float(sccm)
                except (TypeError, ValueError):
                    pass

    def _apply_step(self):
        """현재 스텝이 요구하는 밸브 상태로 바꾸고, 펄스면 압력 스파이크를 만든다."""
        steps = self._steps()
        if not steps or self._step >= len(steps):
            return
        step = steps[self._step]
        want = set(step_open_tags(self.cfg, step))
        for tag in self.valves:
            if tag in (self._vent_tag, self._rv_tag):
                continue
            self.valves[tag] = tag in want
        if self._is_pulse(step):
            self._spike += SPIKE_AMPL

    def _is_pulse(self, step) -> bool:
        """전구체·반응물(비 N2) 라인이 챔버로 열리는 스텝 = 펄스. 압력 스파이크가 난다."""
        modes = self.cfg.get("modes") or {}
        for sel in step.get("lines") or []:
            ln = line_by_id(self.cfg, (sel or {}).get("line"))
            if not ln or ln.get("kind") == KIND_N2:
                continue
            if ROLE_ALD in ((modes.get((sel or {}).get("mode")) or {}).get("open") or []):
                return True
        return False

    def _close_all_line_valves(self):
        for ln in self.cfg.get("lines") or []:
            for tag in (ln.get("valves") or {}).values():
                self.valves[tag] = False
        for lid in self.mfc_sv:
            self.mfc_sv[lid] = 0.0

    def _open_always(self):
        """N2 퍼지 유지 — process.always_open 밸브와 그 라인의 MFC 를 살려 둔다."""
        for tag in (self.cfg.get("process") or {}).get("always_open") or []:
            if tag in self.valves:
                self.valves[tag] = True
            lid = tag.rsplit("-", 1)[0]
            ln = line_by_id(self.cfg, lid)
            if ln and ln.get("kind") == KIND_N2:
                self.mfc_sv[lid] = float((ln.get("mfc") or {}).get("purge_sccm") or 100.0)

    def _apply_idle_vacuum(self):
        """대기 시나리오의 기동 상태: ALD 밸브 전부 닫힘, 펌프 ON, 러핑 열림, 베이스 압력 근처.

        ★ 여기서는 N2 퍼지를 켜지 않는다. 기동 직후의 '대기'는 아무것도 흘리지 않는
          상태여야 베이스 압력 판정이 의미가 있다. 반면 공정을 마치거나 즉시 중단한
          뒤(_finish/abort)에는 챔버에 남은 전구체를 밀어내야 하므로 N2 퍼지를 유지한다.
          같은 '대기'로 보이지만 경위가 달라 해야 할 일이 다르다.
        """
        if self._scenario == "running":
            self.dry_pump = True
            self.valves[self._rv_tag] = True
            self._pressure = self._base = PROC_BASE_TORR
            self._target_p = PROC_BASE_TORR
            return
        self.dry_pump = True
        self.valves[self._rv_tag] = True
        self.tv_pct = 100.0
        self._pressure = self._base = IDLE_BASE_TORR
        self._target_p = IDLE_BASE_TORR

    def _log(self, level, msg):
        self._logs.append((level, msg))
        del self._logs[:-200]


def start_conditions(cfg: dict, snap: dict, recipe: dict) -> list:
    """'공정 준비' 패널의 시작 조건 체크리스트. provider 와 무관한 순수 판정이라
    여기(데모 모듈)가 아니라 계산만 하고, 실장비에서도 같은 함수를 쓴다."""
    out = []
    vac = cfg.get("vacuum") or {}
    base = float(vac.get("start_base_torr") or 5.0e-2)
    p = float((snap.get("gauges") or {}).get("baratron") or 0)
    out.append({"label": f"베이스 압력 {_torr(p)} Torr (기준 {_torr(base)})", "ok": p <= base})

    cond = (recipe or {}).get("conditions") or {}
    heaters = snap.get("heaters") or {}
    for h in cfg.get("heaters") or []:
        if not h.get("enabled", True) or not h.get("stable_band"):
            continue
        hv = heaters.get(h["id"]) or {}
        sv, pv = hv.get("sv"), hv.get("pv")
        if sv is None or pv is None:
            continue
        band = float(h.get("stable_band") or 1.0)
        sec = int(h.get("stable_sec") or 60)
        label = h.get("label", h["id"])
        if h.get("id") == (cfg.get("stage_heater") or "stage"):
            sv = float(cond.get("stage_sv") or sv)
        out.append({"label": f"{label} {sv:.1f} °C 안정 (±{band:.1f} °C, {sec} s)",
                    "ok": abs(float(pv) - float(sv)) <= band})

    ilk = snap.get("interlocks") or {}
    names = [k.get("label", k.get("id")) for k in cfg.get("interlocks") or []
             if k.get("id") not in ("plc_hb",)]
    out.append({"label": "인터락 정상 (" + " · ".join(names) + ")",
                "ok": all(v for k, v in ilk.items() if k != "plc_hb")})
    plc = snap.get("plc") or {}
    out.append({"label": "PLC 연결 · 하트비트 정상",
                "ok": bool(plc.get("connected")) and bool(plc.get("hb_ok"))})
    return out


def _torr(v: float) -> str:
    if v is None:
        return "—"
    return f"{v:.3f}" if v >= 0.1 else f"{v:.1E}"
