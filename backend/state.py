"""
state.py — 서버가 주인인 상태 스냅샷.

화면은 상태를 스스로 만들지 않는다. 서버가 provider에게 읽은 값을 여기에 모으고,
connection.py 가 state / telemetry 로 내보낸다. 이렇게 두는 이유는 두 가지다.
  1. 브라우저와 앱 창이 동시에 붙어도 같은 화면을 본다.
  2. 연결이 끊기면 화면은 '—' 를 보여줄 뿐 값을 지어내지 않는다.

state(전체 스냅샷)는 접속 시와 구조가 바뀔 때만, telemetry(자주 변하는 값)는 5 Hz로 보낸다.
"""

import time

import version
import storage
from config import enabled_lines, line_modes


class State:
    def __init__(self):
        self.cfg = {}
        self.provider = None
        self.snap = {}                 # provider 가 마지막으로 돌려준 값
        self.startup_notices = []      # 기동 진단(접속할 때 화면에 재생)
        self.logs = []                 # 공정 로그(최근 500줄)
        self.config_path = ""

    # ===================== 로그 =====================
    def add_log(self, level: str, msg: str):
        self.logs.append({"ts": time.strftime("%H:%M:%S"), "level": level, "msg": msg})
        del self.logs[:-500]

    # ===================== 정적 구조 =====================
    def chamber_info(self) -> dict:
        ch = self.cfg.get("chamber") or {}
        ui = self.cfg.get("ui") or {}
        plc = self.cfg.get("plc") or {}
        return {
            "id": ch.get("id", ""),
            "name": ch.get("name", ""),
            "subtitle": ch.get("subtitle", ""),
            "theme": ui.get("theme", "light"),
            "accent": ui.get("accent", "#1f3b73"),
            "app_name": version.APP_NAME,
            "app_version": version.APP_VERSION,
            "plc_addr": f"{plc.get('ip', '')}:{plc.get('port', '')}" if plc.get("ip") else "",
            "config_file": self.cfg.get("_source", ""),
        }

    def lines_info(self) -> list:
        """배관도·표가 쓰는 라인 구조. 지원 mode 목록까지 여기서 계산해 화면에 넘긴다 —
        화면이 밸브 조합 규칙을 따로 알 필요가 없게 한다."""
        out = []
        for ln in self.cfg.get("lines") or []:
            out.append({
                "id": ln.get("id"),
                "kind": ln.get("kind"),
                "side": ln.get("side"),
                "label": ln.get("label", ""),
                "material": ln.get("material", ""),
                "heated": bool(ln.get("heated")),
                "enabled": bool(ln.get("enabled", True)),
                "generator": ln.get("generator", ""),
                "mfc": dict(ln.get("mfc") or {}),
                "valves": dict(ln.get("valves") or {}),
                "modes": line_modes(self.cfg, ln),
            })
        return out

    # ===================== 스냅샷 =====================
    def snapshot(self, access_local: bool = True) -> dict:
        s = self.snap or {}
        proc = s.get("process") or {}
        return {
            "type": "state",
            "chamber": self.chamber_info(),
            "conn": {"ok": True, "ts": time.strftime("%H:%M:%S")},
            "demo": {"enabled": bool(getattr(self.provider, "is_demo", False)),
                     "scenario": (self.cfg.get("demo") or {}).get("scenario", "")},
            "process": proc,
            "lines": self.lines_info(),
            "modes": self.cfg.get("modes") or {},
            "always_open": (self.cfg.get("process") or {}).get("always_open") or [],
            "chamber_io": self.cfg.get("chamber_io") or {},
            "gauges": self.cfg.get("gauges") or {},
            "heaters": self.cfg.get("heaters") or [],
            "interlocks": self.cfg.get("interlocks") or [],
            "alarm_cfg": self.cfg.get("alarms") or {},
            "vacuum": self.cfg.get("vacuum") or {},
            "plc_cfg": self.cfg.get("plc") or {},
            "log_cfg": self.cfg.get("log") or {},
            "server_cfg": {"host": (self.cfg.get("server") or {}).get("host", ""),
                           "port": (self.cfg.get("server") or {}).get("port", 0)},
            "alarms": s.get("alarms") or [],
            "alarm_history": self.provider.alarm_history() if self.provider else [],
            "recipes": storage.list_recipes(),
            "logs": list(self.logs),
            "access": {"local": bool(access_local),
                       "mode": "이 PC" if access_local else "보기 전용"},
            "live": self.telemetry_payload(),
        }

    def telemetry_payload(self) -> dict:
        """5 Hz로 나가는 '자주 변하는 값'만. 구조(라인 목록·히터 정의)는 넣지 않는다."""
        s = self.snap or {}
        return {
            "type": "telemetry",
            "ts": time.time(),
            "clock": time.strftime("%H:%M:%S"),
            "date": time.strftime("%Y-%m-%d"),
            "plc": s.get("plc") or {},
            "valves": s.get("valves") or {},
            "mfc": s.get("mfc") or {},
            "heaters": s.get("heaters") or {},
            "gauges": s.get("gauges") or {},
            "io": s.get("io") or {},
            "interlocks": s.get("interlocks") or {},
            "process": s.get("process") or {},
            "alarms": s.get("alarms") or [],
        }


state = State()
