"""
maintenance.py — 정비 카운터 (v0.7.0 A4).

data/maintenance.json(원자적 저장)에 남긴다:
  · 레시피 밸브(RECIPE_VALVES)별 열림 횟수 · 열린 시간 합 — 공정 끝 판정 때 센다(recipe.valve_usage 규칙 —
    PLC 와 같게). 정상 종료면 레시피 전체, 아니면 끝 스냅샷 위치(시퀀서 6/8 · 블록 ≠ 0)까지, 그것도 없으면 공정 중
    마지막으로 본 위치까지. 센 데이터 로그 이름을 기억해 두 번 세지 않는다. 수동 밸브 열기(명령 12 결과 0, 닫힘 →
    열림)도 1 회.
  · 켜진 시간 — 펌프 · (PEALD) RF · (Powder) O3 발생기 · 바이패스 펌프 출력(device.MAINT_HOURS). PLC 링크가 붙어
    있는 동안(멈춤 제외)만 센다 — 프로그램이 꺼져 있던 동안은 모른다. 60 s 마다 · 끌 때 저장.
한계(maintenance.valve_opens_warn · pump_hours_warn · rf_hours_warn / o3_hours_warn, 0 = 끔)를 넘으면 Program Notices 에
'정비 한계 — …'. [Reset] 은 관리자 PIN · 확인 창, 이벤트 로그에 이전 값.

★ 기록 실패가 공정 · 화면을 멈추지 않게 한다 — 실패는 로그로만 알린다.
"""

import os
import time

from . import addresses as A
from . import device as DEV
from . import logger
from . import paths
from . import recipe as R
from .storage import atomic_write_json, read_json

SAVE_S = 60.0
COUNTED_KEEP = 300          # 센 데이터 로그 이름을 이만큼 기억한다


def path() -> str:
    return os.path.join(paths.DATA_DIR, "maintenance.json")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


class Maintenance:
    def __init__(self):
        self.data = None
        self._last_t = None
        self._saved_at = 0.0
        self.dirty = False

    # ===================== 파일 =====================
    def load(self):
        d = read_json(path()) if os.path.isfile(path()) else None
        d = d if isinstance(d, dict) else {}
        valves = d.get("valves") if isinstance(d.get("valves"), dict) else {}
        hours = d.get("hours") if isinstance(d.get("hours"), dict) else {}
        out = {"valves": {}, "hours": {}, "counted": [str(x) for x in (d.get("counted") or [])
                                                     if isinstance(x, str)][-COUNTED_KEEP:]}
        for tag in DEV.RECIPE_VALVES:
            v = valves.get(tag) if isinstance(valves.get(tag), dict) else {}
            out["valves"][tag] = {"opens": _int(v.get("opens")), "open_ms": _int(v.get("open_ms")),
                                  "reset_at": str(v.get("reset_at") or "")}
        for h in DEV.MAINT_HOURS:
            v = hours.get(h["key"]) if isinstance(hours.get(h["key"]), dict) else {}
            out["hours"][h["key"]] = {"s": max(0.0, _float(v.get("s"))), "reset_at": str(v.get("reset_at") or "")}
        self.data = out
        return out

    def _d(self):
        return self.data if self.data is not None else self.load()

    def save(self, force: bool = False):
        if not (self.dirty or force):
            return
        try:
            os.makedirs(paths.DATA_DIR, exist_ok=True)
            atomic_write_json(path(), dict(self._d(), saved_at=time.strftime("%Y-%m-%d %H:%M:%S")))
            self.dirty = False
            self._saved_at = time.monotonic()
        except Exception as e:  # noqa: BLE001
            logger.write("warn", f"정비 카운터 저장 실패(계속 진행): {e}")

    # ===================== 세기 =====================
    def add_valves(self, usage: dict):
        d = self._d()
        for tag, (n, ms) in usage.items():
            v = d["valves"].get(tag)
            if v is None:
                continue
            v["opens"] += int(n)
            v["open_ms"] += int(ms)
        self.dirty = True

    def count_run(self, cfg, key: str, recipe, s, last_pos, normal: bool) -> str:
        """끝 판정 때 — 밸브 사용량을 더하고 이벤트 로그 글을 돌려준다(이미 센 것 · 표 없음이면 그 글)."""
        d = self._d()
        if key in d["counted"]:
            return ""
        if not (isinstance(recipe, dict) and recipe.get("blocks")):
            return "정비 카운터: 레시피(표) 없이 이어받은 공정이라 밸브 열림을 세지 않았습니다"
        if normal:
            upto, where = None, "레시피 전체"
        elif s is not None and s[A.D_SEQ_STATE] in (6, 8) and s[A.D_SEQ_BLOCK]:
            upto = {"block": s[A.D_SEQ_BLOCK], "step": s[A.D_SEQ_STEP],
                    "cycle": A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1]),
                    "group_pass": s[A.D_SEQ_GROUP_PASS]}
            where = f"끝 위치 블록 {upto['block']} · 스텝 {upto['step']} · 사이클 {upto['cycle']}까지"
        elif last_pos:
            upto = {"block": last_pos[0], "step": last_pos[1], "cycle": last_pos[2],
                    "group_pass": s[A.D_SEQ_GROUP_PASS] if s is not None else 1}
            where = f"마지막으로 본 위치 블록 {last_pos[0]} · 스텝 {last_pos[1]} · 사이클 {last_pos[2]}까지"
        else:
            return "정비 카운터: 공정 위치를 몰라 밸브 열림을 세지 않았습니다"
        usage = R.valve_usage(cfg, recipe, upto)
        self.add_valves(usage)
        d["counted"] = (d["counted"] + [key])[-COUNTED_KEEP:]
        self.save(force=True)
        n = sum(v[0] for v in usage.values())
        return f"정비 카운터: 밸브 열림 {n} 회 더함({where})"

    def manual_open(self, tag: str):
        """수동 밸브 열기(명령 12 결과 0, 닫힘 → 열림) — 1 회(열린 시간은 세지 않는다)."""
        d = self._d()
        if tag in d["valves"]:
            d["valves"][tag]["opens"] += 1
            self.dirty = True

    def tick(self, link):
        """샘플링 루프(10 Hz) — 링크가 붙어 있는 동안(멈춤 제외) 켜진 출력의 시간을 더한다. 60 s 마다 저장."""
        now = time.monotonic()
        last, self._last_t = self._last_t, now
        ok = bool(link and link.connected and not getattr(link, "plc_hb_stalled", False)
                  and not (hasattr(link, "id_block_text") and link.id_block_text()))
        if ok and last is not None:
            dt = min(1.0, max(0.0, now - last))
            aux = link.status[A.D_AUX_OUT]
            d = self._d()
            for h in DEV.MAINT_HOURS:
                if (aux >> h["aux"]) & 1:
                    d["hours"][h["key"]]["s"] += dt
                    self.dirty = True
        if self.dirty and now - self._saved_at >= SAVE_S:
            self.save()

    # ===================== 보기 · 리셋 =====================
    def rows(self, cfg) -> list:
        """설정 탭 표 — [{key, label, value, unit, limit, over, reset_at}]."""
        d = self._d()
        mt = (cfg or {}).get("maintenance") or {}
        out = []
        vlim = _float(mt.get("valve_opens_warn"))
        for tag in DEV.RECIPE_VALVES:
            v = d["valves"][tag]
            out.append({"key": f"valve:{tag}", "label": f"{tag} opens", "value": v["opens"], "unit": "",
                        "extra": f"열린 시간 {v['open_ms'] / 3600000.0:.2f} h",
                        "limit": vlim or None, "over": bool(vlim and v["opens"] >= vlim), "reset_at": v["reset_at"]})
        for h in DEV.MAINT_HOURS:
            v = d["hours"][h["key"]]
            lim = _float(mt.get(h["warn"])) if h.get("warn") else 0.0
            hrs = v["s"] / 3600.0
            out.append({"key": f"hours:{h['key']}", "label": f"{h['name']} hours", "value": round(hrs, 2),
                        "unit": "h", "extra": "", "limit": lim or None, "over": bool(lim and hrs >= lim),
                        "reset_at": v["reset_at"]})
        return out

    def notices(self, cfg) -> list:
        return [("warn", f"정비 한계 — {r['label']} {r['value']}{(' ' + r['unit']) if r['unit'] else ''} "
                         f"≥ 한계 {r['limit']:g}")
                for r in self.rows(cfg) if r["over"]]

    def reset(self, key: str) -> str:
        """하나를 0 으로. 이전 값 글(이벤트 로그용)을 돌려준다. 모르는 키면 ''."""
        d = self._d()
        kind, _, name = str(key).partition(":")
        if kind == "valve" and name in d["valves"]:
            v = d["valves"][name]
            before = f"{name} 열림 {v['opens']} 회 · 열린 시간 {v['open_ms'] / 3600000.0:.2f} h"
            d["valves"][name] = {"opens": 0, "open_ms": 0, "reset_at": _today()}
        elif kind == "hours" and name in d["hours"]:
            before = f"{name} 켜진 시간 {d['hours'][name]['s'] / 3600.0:.2f} h"
            d["hours"][name] = {"s": 0.0, "reset_at": _today()}
        else:
            return ""
        self.dirty = True
        self.save(force=True)
        return before


def _int(v) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else 0.0
    except (TypeError, ValueError):
        return 0.0


maint = Maintenance()


def count_run(cfg, key, recipe, s, last_pos, normal) -> str:
    return maint.count_run(cfg, key, recipe, s, last_pos, normal)
