"""test/e2e_process.py — 실제 공정 시나리오 자동 시험(가짜 PLC + 프로그램 headless + 운전자처럼 WebSocket).

가짜 PLC(test/fake_plc.py)를 별도 프로세스로 띄우고, 프로그램을 plc.simulate = false 로 그 가짜 PLC 에 붙여
headless 로 띄운 뒤, 화면이 보내는 것과 같은 명령을 WebSocket 으로 보내 결과를 PASS / FAIL 로 판정한다.
끝나면 띄운 프로세스(가짜 PLC · 프로그램)를 모두 정리한다. 로그 · 데이터 로그는 임시 폴더에 쓰고 지운다(--keep 이면 남김).

실행:
    python test/e2e_process.py                       (전부, 배속 10)
    python test/e2e_process.py startup recipe        (이름으로 골라서)
    python test/e2e_process.py faults --speed 20 --keep
    python test/e2e_process.py --list
시나리오: startup · pumpdown · recipe · control · faults · comm · device_id · manual · long · dual

★ 래더 타이머 · 스텝 시간은 실제 시간이다(--speed 는 펌핑 · 벤트 · 승온 · O3 만 빠르게 — 펄스 감쇠 · PCV · MFC ·
  RF 램프 같은 빠른 기기 반응도 실제 시간).
★ 이 PC 의 프로그램 데이터(data/)와 실행 중인 프로그램은 건드리지 않는다 — 빈 포트 · 임시 폴더 · 뮤텍스 없이.
★ 자식(가짜 PLC · headless 프로그램)은 stdin 파이프가 닫히면 스스로 끝난다 — 이 시험 틀이 작업 관리자 · IDE 정지로
  강제로 꺼져도 남지 않게(v0.6.1 C9).
"""
import os
import re
import sys
import json
import time
import shutil
import socket
import asyncio
import argparse
import tempfile
import subprocess
import urllib.request

import _pkg

_pkg.utf8_console()
A = _pkg.mod("addresses")
DEV = _pkg.mod("device")

SCENARIOS = ["startup", "pumpdown", "recipe", "control", "faults", "comm", "device_id", "manual", "long", "dual"]
PY = sys.executable
OTHER = {"peald": "POWDERALD", "powderald": "PEALD"}[_pkg.PKG]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def free_pair() -> int:
    """port 와 port + 1(제어 포트)이 둘 다 빈 포트."""
    for _ in range(50):
        p = free_port()
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", p + 1))
            return p
        except OSError:
            continue
    raise RuntimeError("빈 포트를 찾지 못했습니다")


# ===================== 판정 기록 =====================
class Report:
    def __init__(self):
        self.rows = []          # (시나리오, 항목, 기대, 실제, 통과)
        self.skips = set()      # 건너뛴 항목 (시나리오, 항목)

    def check(self, scen, item, ok, expected, actual):
        self.rows.append((scen, item, str(expected), str(actual), bool(ok)))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {item} — 기대: {expected} / 실제: {actual}", flush=True)
        return bool(ok)

    def skip(self, scen, item, why):
        """건너뜀 — 실패가 아니다(예: 다른 프로그램 폴더가 없음)."""
        self.rows.append((scen, item, "-", str(why), True))
        self.skips.add((scen, item))
        print(f"  [SKIP] {item} — {why}", flush=True)
        return True

    def failed(self, scen=None):
        return [r for r in self.rows if not r[4] and (scen is None or r[0] == scen)]

    def summary(self) -> str:
        out = ["", "=" * 78, f"{DEV.NAME} e2e 요약", "-" * 78]
        by = {}
        for r in self.rows:
            by.setdefault(r[0], []).append(r)
        for scen, rows in by.items():
            bad = [r for r in rows if not r[4]]
            skipped = all((r[0], r[1]) in self.skips for r in rows)
            out.append(f"{'SKIP' if skipped else 'PASS' if not bad else 'FAIL':4}  {scen:<10} "
                       f"{len(rows) - len(bad)}/{len(rows)} 항목")
            for r in bad:
                out.append(f"        ✕ {r[1]} — 기대 {r[2]} / 실제 {r[3]}")
        out.append("=" * 78)
        return "\n".join(out)


# ===================== 프로세스 =====================
class Harness:
    """가짜 PLC · 프로그램 프로세스와 임시 폴더. 끝나면 close() 로 모두 정리한다."""

    def __init__(self, speed: float, keep: bool, verbose: bool):
        self.speed = float(speed)
        self.keep = keep
        self.verbose = verbose
        self.tmp = tempfile.mkdtemp(prefix=f"e2e_{_pkg.PKG}_")
        self.plc_port = free_pair()
        self.ctl_port = self.plc_port + 1
        self.web_port = self._other_port()
        self.plc = None
        self.app = None
        self._plc_out = None        # 자식 출력 파일 — 멈출 때 닫는다(Windows 에서 임시 폴더가 지워지게, C7)
        self._app_out = None
        self.cfg_path = os.path.join(self.tmp, "config.json")
        self.cfg = self._write_config()
        self.plc_log = os.path.join(self.tmp, "fake_plc.log")

    def _other_port(self) -> int:
        """가짜 PLC 포트 · 제어 포트와 겹치지 않는 빈 포트(제어 포트는 아직 열리기 전이라 비어 보인다)."""
        used = {self.plc_port, self.ctl_port} | set(getattr(self, "_taken", set()))
        while True:
            p = free_port()
            if p not in used:
                self._taken = used | {p}
                return p

    def _write_config(self) -> dict:
        with open(os.path.join(_pkg.ROOT, "config", "config.example.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["server"] = {"host": "127.0.0.1", "port": self.web_port}
        plc = cfg.setdefault("plc", {})
        plc.update({"host": "127.0.0.1", "port": self.plc_port, "simulate": False, "unit_id": 1,
                    "sim_port": self._other_port()})
        cfg.setdefault("analog", {})["confirmed"] = True
        for g in (cfg.get("pressure") or {}).values():
            if isinstance(g, dict):
                g["confirmed"] = True
        for m in cfg.get("mfc") or []:
            if not m.get("full_scale_sccm"):
                m["full_scale_sccm"] = 1000
            m["confirmed"] = True
        for h in cfg.get("heaters") or []:
            if h.get("enabled") and h.get("max_c") is None:
                h["max_c"] = 300
        for k in ("rf", "o3", "pcv"):
            if isinstance(cfg.get(k), dict):
                cfg[k]["confirmed"] = True
        cfg.setdefault("params", {})["valve_min_ms"] = 20
        cfg.setdefault("process", {})["base_stable_s"] = 1
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return cfg

    # ---------- 가짜 PLC ----------
    def start_plc(self, state="atm", device_id=""):
        args = [PY, os.path.join(_pkg.TEST_DIR, "fake_plc.py"), "--port", str(self.plc_port),
                "--ctl-port", str(self.ctl_port), "--speed", str(self.speed), "--state", state,
                "--config", self.cfg_path, "--log", self.plc_log, "--seed", "1", "--exit-on-eof"]
        if device_id:
            args += ["--device-id", device_id]
        self._plc_out = open(os.path.join(self.tmp, "fake_plc.out"), "a", encoding="utf-8")
        # stdin=PIPE — 이 틀이 강제로 꺼지면 파이프가 닫혀(EOF) 가짜 PLC 가 스스로 끝난다(C9)
        self.plc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=self._plc_out, stderr=subprocess.STDOUT,
                                    cwd=_pkg.ROOT)
        for _ in range(100):
            if self.ctl({"op": "ping"}, quiet=True):
                return True
            if self.plc.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("가짜 PLC 가 뜨지 않았습니다 — " + self._tail("fake_plc.out"))

    def ctl(self, req: dict, quiet=False):
        try:
            with socket.create_connection(("127.0.0.1", self.ctl_port), timeout=2.0) as s:
                s.sendall((json.dumps(req) + "\n").encode("utf-8"))
                line = s.makefile("r", encoding="utf-8").readline()
                return json.loads(line) if line else None
        except (OSError, ValueError):
            if not quiet:
                print(f"  (가짜 PLC 제어 실패: {req})", flush=True)
            return None

    def stop_plc(self):
        if _pkg.alive(self.plc):
            self.ctl({"op": "quit"}, quiet=True)
            try:
                self.plc.wait(5)
            except subprocess.TimeoutExpired:
                self.plc.kill()
                self.plc.wait(5)
        _close_child(self.plc)
        self.plc = None
        self._plc_out = _close(self._plc_out)

    # ---------- 프로그램 ----------
    def start_app(self):
        args = [PY, os.path.abspath(__file__), "--serve-app", self.cfg_path, os.path.join(self.tmp, "app")]
        self._app_out = open(os.path.join(self.tmp, "app.out"), "a", encoding="utf-8")
        self.app = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=self._app_out, stderr=subprocess.STDOUT,
                                    cwd=_pkg.ROOT)
        for _ in range(200):
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        f"http://127.0.0.1:{self.web_port}/health", timeout=1) as r:
                    if json.loads(r.read().decode("utf-8")).get("ok"):
                        return True
            except Exception:  # noqa: BLE001
                pass
            if self.app.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("프로그램이 뜨지 않았습니다 — " + self._tail("app.out"))

    def stop_app(self):
        # ★ terminate 그대로 — comm 의 '프로그램 재시작 → 이어받기'는 갑자기 꺼진 프로그램을 흉내 낸다
        if _pkg.alive(self.app):
            self.app.terminate()
            try:
                self.app.wait(10)
            except subprocess.TimeoutExpired:
                self.app.kill()
                self.app.wait(5)
        _close_child(self.app)
        self.app = None
        self._app_out = _close(self._app_out)

    def get(self, path: str):
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{self.web_port}{path}", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def _tail(self, name, n=1500):
        try:
            with open(os.path.join(self.tmp, name), encoding="utf-8", errors="replace") as f:
                return f.read()[-n:]
        except OSError:
            return "(출력 없음)"

    def plc_log_text(self) -> str:
        try:
            with open(self.plc_log, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def close(self):
        self.stop_app()
        self.stop_plc()
        if self.keep:
            print(f"(임시 폴더를 남깁니다: {self.tmp})")
        else:
            shutil.rmtree(self.tmp, ignore_errors=True)


def _close(f):
    """열어 둔 파일을 닫는다(없으면 그대로). 늘 None."""
    if f is not None:
        try:
            f.close()
        except OSError:
            pass
    return None


def _close_child(p):
    """멈춘 자식의 stdin 파이프를 닫는다(핸들을 남기지 않게)."""
    if p is not None and p.stdin:
        try:
            p.stdin.close()
        except OSError:
            pass


def serve_app(cfg_path: str, data_root: str):
    """--serve-app — 프로그램을 headless 로(임시 데이터 폴더 · 뮤텍스 없이). e2e 가 띄우는 자식 프로세스."""
    paths = _pkg.mod("paths")
    os.makedirs(data_root, exist_ok=True)
    paths.set_data_root(data_root)
    paths.ensure_dirs()
    server = _pkg.mod("server")
    import uvicorn
    app = server.create_app(cfg_path, single_instance=False)
    state = _pkg.mod("state").state
    srv = state.cfg.get("server") or {}
    us = uvicorn.Server(server.uvicorn_config(app, "127.0.0.1", int(srv.get("port"))))

    def on_eof():                   # 시험 틀이 강제로 꺼짐(stdin EOF) — 스스로 끝낸다(C9)
        us.should_exit = True
        import threading
        threading.Timer(15.0, lambda: os._exit(0)).start()
    if not sys.stdin.isatty():
        _pkg.exit_on_stdin_eof(on_eof)
    us.run()


# ===================== 운전자(WebSocket) =====================
class Operator:
    def __init__(self, h: Harness):
        self.h = h
        self.ws = None
        self.live = {}
        self.snap = {}
        self.notices = []
        self.logs = []
        self.msgs = {}
        self._task = None

    async def connect(self, timeout=20.0):
        import websockets
        end = time.monotonic() + timeout
        while True:
            url = f"ws://127.0.0.1:{self.h.web_port}/ws"
            try:
                # ★ v0.6.1(C2): websockets 15 이상은 proxy=True 가 기본 — 시스템 프록시가 있으면 127.0.0.1 도 프록시로 간다
                try:
                    self.ws = await websockets.connect(url, max_size=None, open_timeout=5, proxy=None)
                except TypeError:           # proxy 인자가 없는 옛 websockets
                    self.ws = await websockets.connect(url, max_size=None, open_timeout=5)
                break
            except Exception:  # noqa: BLE001
                if time.monotonic() > end:
                    raise
                await asyncio.sleep(0.3)
        self._task = asyncio.create_task(self._reader())
        await self.wait(lambda: self.live.get("plc") is not None, 10)

    async def close(self):
        if self._task:
            self._task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        self.ws = None

    async def _reader(self):
        try:
            async for raw in self.ws:
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                t = m.get("type")
                if t == "live":
                    self.live = m
                elif t == "state":
                    self.snap = m
                    if isinstance(m.get("live"), dict):
                        self.live = m["live"]
                elif t == "notice":
                    self.notices.append((time.monotonic(), m.get("level"), m.get("msg", "")))
                    if self.h.verbose:
                        print(f"    · 알림[{m.get('level')}] {m.get('msg')}", flush=True)
                elif t == "log":
                    self.logs.append((time.monotonic(), m.get("level"), m.get("msg", "")))
                self.msgs[t] = m
        except Exception:  # noqa: BLE001
            pass

    async def send(self, cmd: str, **kw):
        await self.ws.send(json.dumps({"cmd": cmd, **kw}))

    async def wait(self, pred, timeout: float, step: float = 0.1) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if pred():
                    return True
            except (KeyError, TypeError, IndexError, AttributeError):
                pass
            await asyncio.sleep(step)
        try:
            return bool(pred())
        except (KeyError, TypeError, IndexError, AttributeError):
            return False

    async def reply(self, typ: str, cmd: str, timeout=10.0, **kw):
        self.msgs.pop(typ, None)
        await self.send(cmd, **kw)
        await self.wait(lambda: typ in self.msgs, timeout)
        return self.msgs.get(typ)

    def notices_since(self, t0):
        return [n for n in self.notices if n[0] >= t0]

    # ---------- 값 ----------
    @property
    def proc(self):
        return self.live.get("process") or {}

    def cvg(self):
        return ((self.live.get("pressure") or {}).get("cvg"))

    def ilk(self, bit) -> bool:
        return bool(((self.live.get("interlock") or 0) >> bit) & 1)

    def valve_on(self, tag) -> bool:
        v = next(x for x in DEV.VALVES if x["tag"] == tag)
        return bool(((self.live.get("valves") or 0) >> v["bit"]) & 1)

    def aux_on(self, tag) -> bool:
        v = next(x for x in DEV.AUX if x["tag"] == tag)
        return bool(((self.live.get("aux") or 0) >> v["bit"]) & 1)


# ===================== 레시피 =====================
def _block(name, mfc, steps, repeat, rf_w=0.0, o3=0.0):
    R = _pkg.mod("recipe")
    b = R.empty_block(name)
    b["repeat"] = repeat
    b["mfc_sccm"] = (list(mfc) + [0.0] * DEV.MFC_COUNT)[:DEV.MFC_COUNT]
    b["steps"] = []
    for (sname, ms, valves, extra) in steps:
        s = {"name": sname, "time_ms": ms, "valves": list(valves), "pause_ok": not valves}
        if DEV.HAS_RF:
            s["rf"] = bool(extra.get("rf"))
        b["steps"].append(s)
    if DEV.HAS_RF:
        b["rf_w"] = rf_w
    if DEV.HAS_O3:
        b["o3"] = o3
    return b


def recipes(cycles: int = 4):
    """시험 레시피 {이름: 레시피}. PEALD: TMA–H2O 열 ALD · TMA–O2 플라즈마, Powder: TMA–O3."""
    R = _pkg.mod("recipe")
    out = {}
    if DEV.HAS_RF:
        thermal = _block("TMA-H2O", [100, 0, 100, 0], [
            ("TMA", 60, ["PV-1"], {}), ("Purge 1", 250, [], {}),
            ("H2O", 60, ["PV-R", "PV-R2"], {}), ("Purge 2", 250, [], {})], cycles)
        plasma = _block("TMA-O2 Plasma", [100, 0, 100, 50], [
            ("TMA", 60, ["PV-1"], {}), ("Purge 1", 250, [], {}),
            ("O2 Plasma", 300, ["PV-R", "PV-R3"], {"rf": True}), ("Purge 2", 250, [], {})], cycles, rf_w=100.0)
        r = R.empty_recipe("E2E TMA-H2O")
        r["blocks"] = [thermal]
        out["E2E TMA-H2O"] = r
        r = R.empty_recipe("E2E TMA-O2 Plasma")
        r["blocks"] = [plasma]
        out["E2E TMA-O2 Plasma"] = r
        r = R.empty_recipe("E2E Group")
        r["blocks"] = [dict(thermal, name="Thermal"), dict(plasma, name="Plasma")]
        r["groups"] = [{"from_block": 1, "to_block": 2, "repeat": 2}]
        out["E2E Group"] = r
    else:
        o3 = _block("TMA-O3", [100, 0], [
            ("TMA", 60, ["PV-1"], {}), ("Purge 1", 250, [], {}),
            ("O3", 200, ["PV-R"], {}), ("Purge 2", 250, [], {})], cycles, o3=100.0)
        r = R.empty_recipe("E2E TMA-O3")
        r["blocks"] = [o3]
        out["E2E TMA-O3"] = r
        r = R.empty_recipe("E2E Group")
        r["blocks"] = [dict(o3, name="O3 A"), dict(o3, name="O3 B")]
        r["groups"] = [{"from_block": 1, "to_block": 2, "repeat": 2}]
        out["E2E Group"] = r
    return out


def long_recipe(name, cycles, step_ms=30):
    R = _pkg.mod("recipe")
    if DEV.HAS_RF:
        b = _block("Long", [100, 0, 100, 0], [("TMA", step_ms, ["PV-1"], {}), ("P1", step_ms, [], {}),
                                              ("H2O", step_ms, ["PV-R", "PV-R2"], {}), ("P2", step_ms, [], {})], cycles)
    else:
        b = _block("Long", [100, 0], [("TMA", step_ms, ["PV-1"], {}), ("P1", step_ms, [], {}),
                                      ("O3", step_ms, ["PV-R"], {}), ("P2", step_ms, [], {})], cycles, o3=100.0)
    r = R.empty_recipe(name)
    r["blocks"] = [b]
    return r


# ===================== 시나리오 =====================
class Run:
    def __init__(self, h: Harness, op: Operator, rep: Report):
        self.h, self.op, self.rep = h, op, rep
        self.scen = ""
        self.saved = set()
        self.only_faults = []          # --faults emo,leak — 고장 시나리오를 이것만

    def check(self, item, ok, expected, actual):
        return self.rep.check(self.scen, item, ok, expected, actual)

    def t(self, sim_s: float, floor: float = 5.0) -> float:
        """물리 시간(초) → 기다릴 실제 시간."""
        return max(floor, sim_s / self.h.speed)

    # ---------- 공통 준비 ----------
    async def clear_alarms(self):
        for k in [f["key"] for f in (self.h.ctl({"op": "faults"}) or {}).get("faults", []) if f["on"]]:
            self.h.ctl({"op": "fault", "key": k, "on": False})
        await self.op.send("alarm_ack")
        await asyncio.sleep(0.3)
        await self.op.send("alarm_reset")
        await self.op.wait(lambda: not self.op.live.get("alarms"), 8)

    async def pumped(self, label="베이스 압력 도달") -> bool:
        """펌핑 시작 → 베이스 압력(인터락 진공 비트). 이미 진공이면 그대로(판정 항목은 늘 남긴다 — 실행마다 항목 수가 같게).
        ★ v0.6.1(C3): 펌핑 시작이 2 s 안에 받아졌는지(PMP 켜짐 또는 '처리되었습니다') 보고, 안 되면 한 번 더 보낸다 —
          거절을 못 보고 42 s 를 기다린 뒤 '베이스 압력 FAIL' 로 끝나지 않게."""
        op = self.op
        base = float((self.h.cfg.get("params") or {}).get("base_press_torr") or 0.05)
        expect = f"CVG ≤ {base} Torr (인터락 진공)"

        def done():                     # 진공 · 펌프 운전 · 밸브 허가 · IV-E 열림(압력만 낮아서는 시작 조건이 안 된다)
            return (op.ilk(A.ILK_VACUUM) and op.ilk(A.ILK_PUMP) and op.ilk(A.ILK_VALVE_OK)
                    and op.aux_on("IV-E"))
        if done():
            return self.check(label, True, expect, f"이미 진공 · CVG {op.cvg()}")
        t0 = time.monotonic()
        took = False
        for _ in range(2):
            t1 = time.monotonic()
            await op.send("pump_start")
            took = await op.wait(lambda: op.aux_on("PMP") or any(
                "펌핑 시작" in n[2] and "처리되었습니다" in n[2] for n in op.notices_since(t1)), 2.0)
            if took:
                break
        ok = took and await op.wait(done, self.t(420, 30))
        actual = f"CVG {op.cvg()}" + ("" if took else " · 펌핑 시작이 두 번 다 받아지지 않음")
        if not ok:
            recent = [f"[{n[1]}] {n[2]}" for n in op.notices_since(t0)][-3:]
            actual += " · 최근 알림: " + (" | ".join(recent) if recent else "(없음)")
        return self.check(label, ok, expect, actual)

    async def plc_ready(self, timeout=30.0) -> bool:
        """PLC 연결 · PLC 하트비트 정상 · 멈춤 아님 · 장비 ID 맞음 — 명령을 보내도 거절되지 않는 상태.
        ★ v0.6.1(C3): PLC STOP 을 풀고 바로 알람 확인 · 펌핑 시작을 보내면 프로그램은 아직 'PLC 하트비트 멈춤' 이라 거절한다."""
        def ok():
            p = self.op.live.get("plc") or {}
            return p.get("connected") and p.get("hb_ok") and not p.get("hb_stalled") and p.get("id_state") == "ok"
        return await self.op.wait(ok, timeout)

    async def ready(self):
        await self.plc_ready()
        await self.clear_alarms()
        if self.op.proc.get("running"):
            await self.op.send("process_abort")
            await self.op.wait(lambda: not self.op.proc.get("running"), 20)
        await self.pumped("준비: 베이스 압력")
        if DEV.HAS_O3:
            await self.o3_line(True)

    async def o3_line(self, on: bool):
        if on and (self.op.live.get("extra") or {}).get("o3_on"):
            return True
        await self.op.send("manual_unlock", on=True)
        if on:
            await self.op.send("manual_o3", action="on", value=100)
            return await self.op.wait(lambda: self.op.ilk(A.ILK_O3_OK), 20)
        await self.op.send("manual_o3", action="off")
        return await self.op.wait(lambda: not (self.op.live.get("extra") or {}).get("o3_on"), 20)

    async def save(self, name, rec) -> bool:
        if name in self.saved:
            return True
        m = await self.op.reply("recipe_saved", "recipe_save", name=name, recipe=rec)
        ok = bool(m and m.get("ok"))
        if not ok:
            self.check(f"레시피 저장 {name}", False, "저장됨", m and m.get("why"))
        else:
            self.saved.add(name)
        return ok

    async def run_recipe(self, name, rec, label="", wait_end=True, end_timeout=None):
        """저장 → 올리기 → 시작 → (끝까지). (시작됐나, 끝 결과)"""
        m = await self.op.reply("recipe_check", "recipe_validate", recipe=rec)
        chk = (m or {}).get("check") or {}
        if chk.get("errors"):
            self.check(f"{label or name} 레시피 검증", False, "오류 없음", chk["errors"][:3])
            return False, ""
        if not await self.save(name, rec):
            return False, ""
        await self.op.send("recipe_select", name=name)
        await asyncio.sleep(0.3)
        await self.op.send("recipe_upload", name=name)
        await self.op.wait(lambda: (self.op.snap.get("plc_recipe") or {}).get("plc_ok"), 10)
        t0 = time.monotonic()
        await self.op.send("process_start", name=name)
        started = await self.op.wait(lambda: self.op.proc.get("running"), self.t(400, 40))
        if not started:
            why = [n[2] for n in self.op.notices_since(t0)][-3:]
            self.check(f"{label or name} 시작", False, "공정 중", f"{self.op.proc.get('message')} {why}")
            return False, ""
        if not wait_end:
            return True, ""
        est = R_est(rec)
        await self.op.wait(lambda: not self.op.proc.get("running") and self.op.proc.get("last_result"),
                           end_timeout or (est * 2 + 60))
        return True, self.op.proc.get("last_result") or ""

    def datalog_names(self) -> set:
        return {i.get("name") for i in (self.h.get("/api/datalog/list").get("items") or [])}

    async def new_datalog(self, before: set, timeout=20.0):
        """이번 공정의 데이터 로그 — 시작 전 목록에 없던 것이 닫힐 때까지(공정 끝 뒤 꼬리 몇 초를 더 쓴다)."""
        end = time.monotonic() + timeout
        while True:
            items = [i for i in (self.h.get("/api/datalog/list").get("items") or []) if i.get("name") not in before]
            done = [i for i in items if not i.get("writing")]
            if done or time.monotonic() > end:
                return max(done or items, key=lambda i: i.get("name") or "") if (done or items) else None
            await asyncio.sleep(0.5)

    # ---------- 1) 기동 ----------
    async def startup(self):
        op = self.op
        ok = await op.wait(lambda: op.live["plc"]["connected"], 15)
        self.check("PLC 연결(simulate = false · 가짜 PLC)", ok, f"127.0.0.1:{self.h.plc_port} 연결",
                   op.live.get("plc", {}).get("addr"))
        ok = await op.wait(lambda: op.live["plc"]["id_state"] == "ok", 10)
        self.check("장비 ID", ok, f"ok (0x{DEV.DEVICE_ID:04X})",
                   f"{op.live['plc'].get('id_state')} {op.live['plc'].get('device_id')}")
        ok = await op.wait(lambda: op.live["plc"]["hb_ok"], 10)
        self.check("PLC 하트비트", ok, "정상", op.live["plc"].get("hb_ok"))
        ok = await op.wait(lambda: op.live["plc"]["prm"] and all(r["match"] for r in op.live["plc"]["prm"])
                           and not op.live["plc"]["prm_mismatch"], 15)
        prm = op.live["plc"].get("prm") or []
        self.check("PRM 쓰기 · 되읽기 일치", ok, "모두 일치", f"{sum(r['match'] for r in prm)}/{len(prm)} 일치")
        await asyncio.sleep(3)
        gap = op.live["plc"].get("hb_gap_max_ms") or 0
        hb = int(self.h.cfg["plc"].get("heartbeat_ms") or 250)
        self.check("PC 하트비트 간격(프로그램)", gap < 1000, f"< 1000 ms (주기 {hb})", f"최대 {gap} ms")
        log = self.h.plc_log_text()
        self.check("PLC 쪽 기록 — PC 하트비트 공백 없음", "하트비트 공백" not in log, "공백 0 건",
                   f"{log.count('하트비트 공백')} 건")

    # ---------- 2) 펌프다운 · 승온 ----------
    async def pumpdown(self):
        op = self.op
        await self.clear_alarms()
        if op.aux_on("IV-E"):
            await op.send("vent")
            await op.wait(lambda: (op.cvg() or 0) >= 700, self.t(120, 20))
        p0 = op.cvg()
        t0 = time.monotonic()
        await op.send("pump_start")
        ok = await op.wait(lambda: op.live["state"] and op.aux_on("PMP"), 10)
        self.check("펌프 기동", ok, "PMP 출력", op.aux_on("PMP"))
        ok = await op.wait(lambda: ((op.live.get("inputs0") or 0) >> A.IN0_PUMP_RUN) & 1, 10)
        self.check("펌프 운전 피드백", ok, "입력0 펌프 운전", bool(ok))
        samples = []
        base = float(self.h.cfg["params"].get("base_press_torr") or 0.05)

        def reached():
            samples.append(op.cvg())
            return op.ilk(A.ILK_VACUUM)
        ok = await op.wait(reached, self.t(420, 30), 0.25)
        took = (time.monotonic() - t0) * self.h.speed
        self.check("베이스 압력 도달", ok, f"≤ {base} Torr", f"{op.cvg()} Torr · 물리 시간 {took:.0f} s")
        good = [s for s in samples if s]
        mono = all(b <= a * 1.15 for a, b in zip(good, good[1:]))
        self.check("펌프다운 곡선(로그 곡선 · 몇 분)", ok and mono and 60 <= took <= 600,
                   "단조 감소 · 물리 1~10 분", f"{p0} → {op.cvg()} · {took:.0f} s · 단조 {mono}")
        # 히터 승온(채널 2 — 전구체 라인) → 안정
        ch = 2
        await op.send("manual_unlock", on=True)
        await op.send("manual_heater", sv={str(ch): 60})
        await asyncio.sleep(0.5)
        await op.send("manual_heater", power={str(ch): True})
        peak = [0.0]

        def heat():
            pv = (op.live.get("heaters") or [{}] * 12)[ch - 1].get("pv") or 0
            peak[0] = max(peak[0], pv)
            return abs(pv - 60) <= 1.0
        t0 = time.monotonic()
        ok = await op.wait(heat, self.t(1800, 30), 0.25)
        took = (time.monotonic() - t0) * self.h.speed
        self.check(f"CH{ch} 승온 25 → 60 ℃", ok, "±1 ℃ 안 · 램프 수 분", f"{took / 60:.1f} 분(물리)")
        # 닿은 뒤 오버슈트를 지켜보고(물리 2 분), 끝에 ±1.5 ℃ 로 정착했는지
        def watch():
            heat()
            return False
        await op.wait(watch, self.t(120, 3), 0.1)
        stable = abs(((op.live.get("heaters") or [{}] * 12)[ch - 1].get("pv") or 0) - 60) <= 1.5
        self.check(f"CH{ch} 안정 · 오버슈트", stable and 60.3 <= peak[0] <= 66, "±1.5 ℃ 로 정착 · 오버슈트 0.3~6 ℃",
                   f"지금 {(op.live.get('heaters') or [{}] * 12)[ch - 1].get('pv')} · 최고 {peak[0]:.1f}")
        await op.send("manual_heater", power={str(ch): False})

    # ---------- 3) 레시피 정상 완료 ----------
    async def recipe(self):
        await self.ready()
        rs = recipes(cycles=3)
        for name, rec in rs.items():
            interval = float((self.h.cfg.get("log") or {}).get("datalog_interval_s") or 1)
            before = self.datalog_names()
            t0 = time.monotonic()
            started, res = await self.run_recipe(name, rec)
            if not started:
                continue
            took = time.monotonic() - t0
            self.check(f"{name} — 끝 결과", res.startswith("정상 종료"), "정상 종료", res)
            dl = await self.new_datalog(before)
            rows = (dl or {}).get("rows") or 0
            need = max(3, int((dl or {}).get("took_s") or took) / interval * 0.5)
            self.check(f"{name} — 데이터 로그", dl is not None and rows >= need and (dl.get("result") or "").startswith("정상"),
                       f"행 ≥ {need:.0f} · 메타 결과 정상", f"{(dl or {}).get('name')} 행 {rows} · {(dl or {}).get('result')}")
            if DEV.HAS_O3:
                await self.o3_line(True)

    # ---------- 4) 일시정지 · 재개 · 사이클 후 정지 · 즉시 중단 ----------
    async def control(self):
        op = self.op
        await self.ready()
        name = "E2E Control"
        rec = long_recipe(name, 120, 100)
        started, _ = await self.run_recipe(name, rec, wait_end=False)
        if not started:
            return
        await asyncio.sleep(3)
        await op.send("process_pause")
        ok = await op.wait(lambda: op.proc.get("paused"), 15)
        self.check("일시정지", ok, "Paused", op.live.get("state", {}).get("name"))
        await asyncio.sleep(1)
        await op.send("process_resume")
        ok = await op.wait(lambda: op.proc.get("running") and not op.proc.get("paused"), 10)
        self.check("재개", ok, "Running", op.live.get("state", {}).get("name"))
        await op.send("process_stop_after_cycle")
        ok = await op.wait(lambda: not op.proc.get("running") and op.proc.get("last_result"), 30)
        self.check("사이클 후 정지", ok and op.proc.get("last_result", "").startswith("사이클 후 정지"),
                   "사이클 후 정지 (…)", op.proc.get("last_result"))
        if DEV.HAS_O3:
            await self.o3_line(True)
        started, _ = await self.run_recipe(name, rec, wait_end=False)
        if not started:
            return
        await asyncio.sleep(2)
        await op.send("process_abort")
        ok = await op.wait(lambda: not op.proc.get("running") and op.proc.get("last_result"), 15)
        self.check("즉시 중단", ok and "운전자 중단" in (op.proc.get("last_result") or ""),
                   "중단 (운전자 중단)", op.proc.get("last_result"))

    # ---------- 5) 공정 중 고장 ----------
    FAULT_CASES = [("emo", "EMO"), ("pump_alm", "펌프 알람"), ("leak", "가스 누출"), ("ot", "히터 과온")] + (
        [("rf_ref", "RF 반사")] if DEV.HAS_RF else [("o3_room", "실내 O3 감지")]) + [("mfc1_stuck", "MFC 막힘")]
    # ★ v0.6.1(C11): 고장마다 기대 알람(워드, 비트) — 이름은 장비 정의(DEV.ALARMS0/1)에서
    FAULT_ALARM = {"emo": (0, A.ALM0_EMO), "pump_alm": (0, A.ALM0_PUMP), "leak": (0, A.ALM0_LEAK),
                   "ot": (0, A.ALM0_OT), "mfc1_stuck": (0, A.ALM0_MFC),
                   "rf_ref": (1, A.ALM1_RF_REF), "o3_room": (1, A.ALM1_O3_ROOM)}
    RECIPE_VALVES = ("PV-1", "PV-R", "PV-R2", "PV-R3")      # 시험 레시피가 여는 밸브(정지 뒤 출력 0 이어야)

    @staticmethod
    def alarm_name(word: int, bit: int) -> str:
        return next((d["name"] for d in (DEV.ALARMS0 if word == 0 else DEV.ALARMS1) if d["bit"] == bit), f"알람 {word}.{bit}")

    def recipe_valves_on(self) -> list:
        return [v["tag"] for v in DEV.VALVES if v["tag"] in self.RECIPE_VALVES and self.op.valve_on(v["tag"])]

    async def faults(self):
        op = self.op
        only = self.only_faults
        for key, label in self.FAULT_CASES:
            if only and key not in only:
                continue
            await self.ready()
            name = "E2E Fault"
            rec = long_recipe(name, 400, 100)
            if key == "rf_ref" and DEV.HAS_RF:
                # 반사 트립은 PRM 반사 초과 시간(rf_ref_ms) 동안 이어져야 한다 — RF 가 켜진 긴 스텝으로
                name = "E2E RF Long"
                rec = _pkg.mod("recipe").empty_recipe(name)
                rec["blocks"] = [_block("RF Long", [100, 0, 100, 50], [
                    ("TMA", 60, ["PV-1"], {}), ("Purge 1", 250, [], {}),
                    ("O2 Plasma", 5000, ["PV-R", "PV-R3"], {"rf": True}), ("Purge 2", 250, [], {})], 20, rf_w=100.0)]
            hist0 = await self._hist_len()
            started, _ = await self.run_recipe(name, rec, wait_end=False)
            if not started:
                continue
            await asyncio.sleep(3 if key != "rf_ref" else 2)
            self.h.ctl({"op": "fault", "key": key, "on": True})
            timeout = 40 if key in ("mfc1_stuck", "rf_ref") else 15
            ok = await op.wait(lambda: not op.proc.get("running") and op.proc.get("last_result"), timeout)
            res = op.proc.get("last_result") or ""
            want = "중단" if key == "mfc1_stuck" else "안전 정지"
            self.check(f"{label} → 공정 멈춤 · 끝 결과", ok and want in res and not res.startswith("정상"),
                       f"'{want}' 이 든 끝 결과", res or op.live.get("state", {}).get("name"))
            if key != "mfc1_stuck":
                st = (op.live.get("state") or {}).get("code")
                self.check(f"{label} → 안전 정지 상태", st == A.STATE_SAFE_STOP, "Safe Stop",
                           (op.live.get("state") or {}).get("name"))
            # 정지 뒤 출력 — 레시피 밸브 0 · RF 꺼짐(공정이 멈췄다고 출력이 남으면 안 된다)
            outs_off = await op.wait(lambda: not self.recipe_valves_on() and not (
                DEV.HAS_RF and (op.live.get("extra") or {}).get("rf_on")), 5)
            self.check(f"{label} → 밸브 출력 0" + (" · RF 꺼짐" if DEV.HAS_RF else ""), outs_off,
                       "레시피 밸브 꺼짐" + (" · RF 꺼짐" if DEV.HAS_RF else ""),
                       f"켜진 밸브 {self.recipe_valves_on() or '없음'}"
                       + (f" · RF {(op.live.get('extra') or {}).get('rf_on')}" if DEV.HAS_RF else ""))
            hist = await self._hist(hist0)
            want_alarm = self.alarm_name(*self.FAULT_ALARM[key])
            names = [h["name"] for h in hist] + [a.get("name") for a in op.live.get("alarms") or []]
            self.check(f"{label} → 알람 '{want_alarm}'", len(hist) >= 1 and want_alarm in names,
                       f"새 알람 줄 ≥ 1 · '{want_alarm}'", " · ".join(h["name"] for h in hist[:4]) or "(없음)")
            self.h.ctl({"op": "fault", "key": key, "on": False})
            await self.clear_alarms()

    async def _hist_len(self):
        m = await self.op.reply("alarm_history", "alarm_history")
        return len((m or {}).get("items") or [])

    async def _hist(self, before):
        m = await self.op.reply("alarm_history", "alarm_history")
        items = (m or {}).get("items") or []
        return items[:max(0, len(items) - before)]

    # ---------- 6) 통신 ----------
    async def comm(self):
        op = self.op
        for secs, survive in ((1.2, True), (2.0, True), (3.6, False)):
            await self.ready()
            name = "E2E Comm"
            rec = long_recipe(name, 60 if survive else 400, 100)
            started, _ = await self.run_recipe(name, rec, wait_end=False)
            if not started:
                continue
            await asyncio.sleep(2)
            self.h.ctl({"op": "cut", "mode": "blackhole", "s": secs})
            await asyncio.sleep(secs + 3)
            if survive:
                ok = await op.wait(lambda: not op.proc.get("running") and op.proc.get("last_result"), 120)
                res = op.proc.get("last_result") or ""
                self.check(f"블랙홀 {secs} s → 공정 계속 · 정상 종료", ok and res.startswith("정상 종료"), "정상 종료", res)
            else:
                ok = await op.wait(lambda: not op.proc.get("running") and op.proc.get("last_result"), 20)
                res = op.proc.get("last_result") or ""
                names = " · ".join(a["name"] for a in op.live.get("alarms") or [])
                self.check(f"블랙홀 {secs} s → PC 통신 끊김 트립", ok and "안전 정지" in res, "중단 (안전 정지 — …)",
                           f"{res} / 알람 {names}")
        # 통신이 끊긴 사이 보낸 명령 — 블랙홀(요청은 받고 답 없음) · 케이블 뽑힘(요청도 버림). 프로그램은 오류로 알리고
        # 다시 보내지 않는다(PLC 가 받은 횟수 ≤ 1), 끊김이 끝나면 링크가 돌아온다(C12)
        for mode in ("blackhole", "unplug"):
            await self.ready()
            n0 = len(self.h.plc_log_text())
            self.h.ctl({"op": "cut", "mode": mode, "s": 2.0})
            await asyncio.sleep(0.05)
            t0 = time.monotonic()
            await op.send("alarm_ack")
            await asyncio.sleep(2.0 + 3)
            back = await self.plc_ready(15)
            ns = [n for n in op.notices_since(t0) if "알람 확인" in n[2]]
            bad = [n for n in ns if n[1] in ("warn", "err")]
            done = [n for n in ns if "처리되었습니다" in n[2]]
            got = len(re.findall(r"명령 받음: 번호 \d+ · 코드 %d " % A.CMD_ALARM_ACK, self.h.plc_log_text()[n0:]))
            self.check(f"{'블랙홀' if mode == 'blackhole' else '케이블 뽑힘'} 2 s 중 보낸 명령 → 오류 알림 · 다시 보내지 않음",
                       bool(bad) and not done and got <= 1 and back,
                       "경고/오류 알림 · '처리되었습니다' 없음 · PLC 받은 횟수 ≤ 1 · 링크 회복",
                       f"{(bad or done or [(0, '', '(알림 없음)')])[-1][2]} · PLC 받음 {got} 회 · 회복 {back}")
        # PLC STOP → 하트비트 멈춤 → RUN(P00 첫 스캔) → PLC 재시작
        await self.ready()
        started, _ = await self.run_recipe("E2E Comm", long_recipe("E2E Comm", 400, 100), wait_end=False)
        if started:
            await asyncio.sleep(2)
            self.h.ctl({"op": "fault", "key": "plc_stop", "on": True})
            ok = await op.wait(lambda: op.live["plc"].get("hb_stalled"), 15)
            self.check("PLC STOP → 하트비트 멈춤 표시", ok, "hb_stalled", op.live["plc"].get("hb_stalled"))
            self.h.ctl({"op": "fault", "key": "plc_stop", "on": False})
            ok = await op.wait(lambda: not op.proc.get("running") and op.proc.get("last_result"), 20)
            res = op.proc.get("last_result") or ""
            self.check("PLC STOP → RUN → 끝 결과", ok and "PLC 재시작" in res, "중단 (PLC 재시작 — …)", res)
        # 프로그램 재시작 → 이어받기
        await self.ready()
        name = "E2E Adopt"
        started, _ = await self.run_recipe(name, long_recipe(name, 120, 100), wait_end=False)
        if started:
            await asyncio.sleep(2)
            n0 = len(self.h.plc_log_text())
            await op.close()
            self.h.stop_app()
            self.h.start_app()
            await op.connect()
            ok = await op.wait(lambda: op.proc.get("running") and (op.proc.get("adopted") or op.proc.get("adopting")
                                                                   or op.proc.get("recipe") == name), 20)
            self.check("프로그램 재시작 → 공정 이어받기", ok, "공정 중 · 이어받음",
                       f"running {op.proc.get('running')} · {op.proc.get('recipe')} · {self._hb_gaps(n0)}")
            ok = await op.wait(lambda: not op.proc.get("running") and op.proc.get("last_result"), 120)
            self.check("이어받은 공정 끝 결과", ok and (op.proc.get("last_result") or "").startswith("정상 종료"),
                       "정상 종료", f"{op.proc.get('last_result')} · {self._hb_gaps(n0)}")

    def _hb_gaps(self, n0: int) -> str:
        """가짜 PLC 기록의 PC 하트비트 공백(재시작 동안) — 느린 PC 에서 3 s 를 넘으면 실제 장비처럼 트립한다(C6).
        판정은 그대로 두고 원인이 보이게 판정 문구에 붙인다."""
        gaps = [int(g) for g in re.findall(r"PC 하트비트 공백 (\d+) ms", self.h.plc_log_text()[n0:])]
        return f"PLC 쪽 하트비트 공백 최대 {max(gaps)} ms({len(gaps)} 건)" if gaps else "PLC 쪽 하트비트 공백 없음"

    # ---------- 7) 장비 ID 틀림 ----------
    async def device_id(self):
        op = self.op
        await self.ready()
        self.h.ctl({"op": "id", "value": "0x1234"})
        ok = await op.wait(lambda: op.live["plc"]["id_state"] == "wrong", 10)
        self.check("다른 장비 ID → 막힘", ok, "id_state wrong", op.live["plc"].get("id_state"))
        t0 = time.monotonic()
        await op.send("pump_stop")
        await asyncio.sleep(1.0)
        refused = [n for n in op.notices_since(t0) if n[1] in ("warn", "err")]
        self.check("다른 장비 ID → 명령 거절", bool(refused) and op.aux_on("PMP"), "알림으로 거절 · 펌프 그대로",
                   (refused[-1][2] if refused else "(알림 없음)") + f" · PMP {op.aux_on('PMP')}")
        self.h.ctl({"op": "id", "value": f"{DEV.DEVICE_ID:04X}"})        # 장비 ID 는 16 진
        ok = await op.wait(lambda: op.live["plc"]["id_state"] == "ok", 10)
        self.check("장비 ID 되돌림 → 풀림", ok, "id_state ok", op.live["plc"].get("id_state"))

    # ---------- 8) 수동 조작 · 벤트 · 공정 중 잠금 ----------
    async def manual(self):
        op = self.op
        await self.ready()
        await op.send("manual_unlock", on=True)
        await op.send("manual_valve", tag="PV-1", on=True)
        ok = await op.wait(lambda: op.valve_on("PV-1"), 5)
        self.check("밸브 PV-1 열기", ok, "출력 켜짐", op.valve_on("PV-1"))
        await op.send("manual_valve", tag="PV-1", on=False)
        ok = await op.wait(lambda: not op.valve_on("PV-1"), 5)
        self.check("밸브 PV-1 닫기", ok, "출력 꺼짐", op.valve_on("PV-1"))
        await op.send("manual_mfc", sccm={"1": 50})
        ok = await op.wait(lambda: abs(((op.live.get("mfc") or [{}])[0].get("pv") or 0) - 50) < 5, self.t(20, 8))
        self.check("MFC1 50 sccm", ok, "50 ± 5", (op.live.get("mfc") or [{}])[0].get("pv"))
        await op.send("manual_mfc", sccm={"1": 0})
        if DEV.HAS_PCV:
            await op.send("manual_pcv", pct=40)
            ok = await op.wait(lambda: abs(((op.live.get("extra") or {}).get("pcv_pv") or 0) - 40) < 3, self.t(30, 8))
            self.check("PCV 40 %", ok, "40 ± 3", (op.live.get("extra") or {}).get("pcv_pv"))
            await op.send("manual_pcv", pct=0)
        if DEV.HAS_RF:
            # RF 허가는 '베이스 < 압력 ≤ RF 최대 압력' — 운전자처럼 가스를 먼저 흘려 공정 압력을 만든다
            base = float(self.h.cfg["params"].get("base_press_torr") or 0.05)
            await op.send("manual_mfc", sccm={"1": 300})
            ok = await op.wait(lambda: op.ilk(A.ILK_RF_OK), self.t(30, 10))
            self.check("가스 흘림 → RF 허가", ok, f"압력 > 베이스 {base} Torr · RF Permit", f"{op.cvg()} Torr")
            await op.send("manual_rf", on=True, watt=50)
            ok = await op.wait(lambda: abs(((op.live.get("extra") or {}).get("rf_fwd") or 0) - 50) < 5, self.t(10, 6))
            ex = op.live.get("extra") or {}
            self.check("RF 50 W 켜기", ok, "순방향 50 ± 5 W · 반사 작게", f"{ex.get('rf_fwd')} / {ex.get('rf_ref')}")
            await op.send("manual_rf", on=False)
            ok = await op.wait(lambda: not (op.live.get("extra") or {}).get("rf_on"), 6)
            self.check("RF 끄기", ok, "꺼짐", (op.live.get("extra") or {}).get("rf_on"))
            await op.send("manual_mfc", sccm={"1": 0})
        if DEV.HAS_O3:
            ok = await self.o3_line(True)
            ok = ok and await op.wait(lambda: abs(((op.live.get("extra") or {}).get("o3_pv") or 0) - 100) < 10, self.t(90, 10))
            self.check("O3 라인 켜기 · 농도", ok, "O3 허가 · 100 ± 10", (op.live.get("extra") or {}).get("o3_pv"))
        # 공정 중 잠금
        name = "E2E Lock"
        started, _ = await self.run_recipe(name, long_recipe(name, 80, 100), wait_end=False)
        if started:
            t0 = time.monotonic()
            await op.send("manual_valve", tag="PV-1", on=True)
            await asyncio.sleep(1.0)
            n = [x[2] for x in op.notices_since(t0)]
            self.check("공정 중 수동 조작 거절", any("공정 중" in x for x in n), "'공정 중…' 거절 알림", n[-1:] or "(알림 없음)")
            await op.send("process_abort")
            await op.wait(lambda: not op.proc.get("running"), 15)
        # 벤트(공정 뒤)
        if DEV.HAS_O3:
            await self.o3_line(False)
        await op.send("vent")
        ok = await op.wait(lambda: ((op.live.get("inputs0") or 0) >> A.IN0_ATM) & 1, self.t(150, 15))
        self.check("벤트 → 대기압", ok, "대기압 입력(700 Torr↑)", f"{op.cvg()} Torr")

    # ---------- 9) 긴 공정 · 두 프로그램 ----------
    async def long(self, cycles=500):
        await self.ready()
        name = f"E2E Long {cycles}"
        rec = long_recipe(name, cycles, 30)
        before = self.datalog_names()
        t0 = time.monotonic()
        started, res = await self.run_recipe(name, rec, end_timeout=cycles * 0.2 + 120)
        if not started:
            return
        took = time.monotonic() - t0
        self.check(f"{cycles} 사이클 정상 완료", res.startswith("정상 종료"), "정상 종료", f"{res} · {took:.0f} s")
        cyc = (self.op.live.get("seq") or {}).get("block_pass")
        dl = await self.new_datalog(before)
        self.check(f"{cycles} 사이클 데이터 로그", dl is not None and (dl.get("rows") or 0) >= took * 0.4,
                   f"행 ≥ {took * 0.4:.0f}", f"{(dl or {}).get('rows')} 행 · 마지막 회차 {cyc}")

    async def dual(self):
        other = os.path.join(os.path.dirname(_pkg.ROOT), OTHER, "test", "e2e_process.py")
        if not os.path.isfile(other):
            # 이 프로그램만 받은 PC — 실패가 아니라 건너뜀(C8)
            self.rep.skip(self.scen, "두 프로그램 동시 실행", f"{OTHER}/test/e2e_process.py 없음")
            return
        # 자식 출력은 파일로 — PIPE 는 읽지 않는 동안 가득 차면 자식이 쓰다 막힌다(C8)
        log_path = os.path.join(self.h.tmp, "dual_other.out")
        with open(log_path, "w", encoding="utf-8") as logf:
            p = subprocess.Popen([PY, other, "startup", "recipe", "--speed", str(self.h.speed)], stdin=subprocess.DEVNULL,
                                 stdout=logf, stderr=subprocess.STDOUT, cwd=os.path.dirname(os.path.dirname(other)))
            try:
                await self.long(cycles=150)
                await asyncio.to_thread(p.wait, 900)
            except Exception:  # noqa: BLE001
                p.kill()
                p.wait(10)
        with open(log_path, encoding="utf-8", errors="replace") as f:
            tail = f.read().strip().splitlines()[-6:]
        self.check(f"두 프로그램 동시 실행 — {OTHER} e2e", p.returncode == 0, "종료 코드 0", f"{p.returncode} {' | '.join(tail)}")


def R_est(rec) -> float:
    try:
        s = _pkg.mod("recipe").summarize({}, rec)
        return float(s.get("total_ms") or 0) / 1000.0 or 60.0
    except Exception:  # noqa: BLE001
        return 60.0


async def amain(args) -> int:
    rep = Report()
    h = Harness(args.speed, args.keep, args.verbose)
    op = None
    try:
        print(f"{DEV.NAME} e2e — 가짜 PLC 127.0.0.1:{h.plc_port} · 프로그램 :{h.web_port} · 배속 {h.speed:g}"
              f" · 임시 폴더 {h.tmp}", flush=True)
        run = None
        try:
            h.start_plc(args.state)
            h.start_app()
            op = Operator(h)
            await op.connect()
        except Exception as e:  # noqa: BLE001
            rep.check("준비", "가짜 PLC · 프로그램 띄우기", False, "뜸", f"{type(e).__name__}: {e}")
            return 1
        run = Run(h, op, rep)
        run.only_faults = [k for k in (args.faults or "").split(",") if k]
        for name in args.scenarios:
            run.scen = name
            print(f"\n▶ {name}", flush=True)
            try:
                await getattr(run, name)()
            except Exception as e:  # noqa: BLE001
                run.check("시나리오 실행", False, "예외 없음", f"{type(e).__name__}: {e}")
        return 0 if not rep.failed() else 1
    finally:
        if op:
            await op.close()
        print(rep.summary(), flush=True)
        if rep.failed() or args.keep:
            print("가짜 PLC 기록(끝 40 줄):")
            print("\n".join(h.plc_log_text().splitlines()[-40:]))
        h.close()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=f"{DEV.NAME} 실제 공정 시나리오 자동 시험")
    ap.add_argument("scenarios", nargs="*", help="시나리오 이름(없으면 전부)")
    ap.add_argument("--speed", type=float, default=10.0, help="물리 배속(펌핑 · 벤트 · 승온 · O3 만)")
    ap.add_argument("--state", default="atm", help="가짜 PLC 시작 상태(atm · vac · hot)")
    ap.add_argument("--keep", action="store_true", help="임시 폴더(로그 · 데이터 로그 · 가짜 PLC 기록)를 남긴다")
    ap.add_argument("--verbose", action="store_true", help="프로그램 알림을 모두 찍는다")
    ap.add_argument("--faults", default="", help="faults 시나리오에서 넣을 고장만(쉼표로, 예: emo,leak)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    bad = [s for s in a.scenarios if s not in SCENARIOS and s != "all"]
    if bad:
        ap.error(f"모르는 시나리오: {bad} — {SCENARIOS}")
    if not a.scenarios or "all" in a.scenarios:
        a.scenarios = list(SCENARIOS)
    return a


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--serve-app"]:
        serve_app(argv[1], argv[2])
        return 0
    args = parse_args(argv)
    if args.list:
        print(" ".join(SCENARIOS))
        return 0
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
