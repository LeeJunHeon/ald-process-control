"""
convert.py — 원시값(PLC 아날로그) ↔ 공학 단위 환산.

★ 모든 환산은 단조 증가여야 한다: 원시값이 커지면 공학값도 커진다.
  베이스 압력 같은 설정을 PLC 에 넣을 때 역함수를 쓰기 때문이다.
  단조가 깨지면 역함수가 여러 답을 갖게 되고, PLC 에 엉뚱한 기준값이 들어간다.

★ 확정되지 않은 환산(confirmed=false)은 값을 지어내지 않고 예시값으로 두되,
  화면에서 그 값 옆에 "환산 미확정"을 붙인다. 현장에서 실제 스케일을 확인하면
  설정만 고치면 된다 — 코드는 건드리지 않는다.
"""

import math

# 계산이 불가능할 때 돌려주는 값. None 은 화면에서 '—' 로 그려진다.
UNKNOWN = None


class Scale:
    """원시값 0~raw_max 를 공학 단위로 바꾸는 선형 환산 (MFC·RF·PCV·O3 공용)."""

    def __init__(self, raw_max: int, full: float, confirmed: bool = False, unit: str = ""):
        self.raw_max = max(1, int(raw_max or 1))
        self.full = float(full) if full is not None else None
        self.confirmed = bool(confirmed)
        self.unit = unit

    def to_eng(self, raw):
        if raw is None or self.full is None:
            return UNKNOWN
        return float(raw) / self.raw_max * self.full

    def to_raw(self, eng):
        if eng is None or self.full is None or self.full == 0:
            return 0
        raw = round(float(eng) / self.full * self.raw_max)
        return max(0, min(self.raw_max, int(raw)))


class Pressure:
    """압력 게이지 환산. mode 에 따라 세 가지.

    loglinear : 전압 1 V 마다 몇 decade 씩 올라가는 게이지(CVG 계열).
                p = torr_at_0v * 10 ^ (V * decades_per_volt)
    linear    : p = torr_at_0v + V * torr_per_volt   (커패시턴스 게이지 계열)
    table     : [[raw, torr], ...] 점 표. 사이는 로그 보간(압력은 자릿수가 정보다).

    전압 V 는 원시값에서 만든다: V = raw / raw_max * volt_max
    """

    def __init__(self, cfg: dict, raw_max: int):
        cfg = cfg or {}
        self.mode = cfg.get("mode", "loglinear")
        self.raw_max = max(1, int(raw_max or 1))
        self.volt_max = float(cfg.get("volt_max", 10.0)) or 10.0
        self.torr_at_0v = float(cfg.get("torr_at_0v", 1.0e-4))
        self.decades_per_volt = float(cfg.get("decades_per_volt", 1.0))
        self.torr_per_volt = float(cfg.get("torr_per_volt", 100.0))
        self.unit = cfg.get("unit", "Torr")
        self.confirmed = bool(cfg.get("confirmed", False))
        self.installed = bool(cfg.get("installed", True))
        pts = cfg.get("points") or []
        # 표는 원시값 기준 오름차순으로 정렬해 둔다(보간과 역함수가 같은 순서를 쓴다).
        self.points = sorted(((float(a), float(b)) for a, b in pts), key=lambda x: x[0])

    # ---------- 정방향 ----------
    def to_torr(self, raw):
        if raw is None or not self.installed:
            return UNKNOWN
        raw = max(0, min(self.raw_max, float(raw)))
        if self.mode == "table":
            return self._table_to_torr(raw)
        v = raw / self.raw_max * self.volt_max
        if self.mode == "linear":
            return self.torr_at_0v + v * self.torr_per_volt
        # loglinear
        if self.torr_at_0v <= 0:
            return UNKNOWN
        return self.torr_at_0v * (10.0 ** (v * self.decades_per_volt))

    # ---------- 역방향 ----------
    def to_raw(self, torr):
        """공학 단위 압력을 원시값으로. 베이스 압력 기준을 PLC 에 넣을 때 쓴다."""
        if torr is None or not self.installed:
            return 0
        if self.mode == "table":
            return self._table_to_raw(float(torr))
        if self.mode == "linear":
            if self.torr_per_volt == 0:
                return 0
            v = (float(torr) - self.torr_at_0v) / self.torr_per_volt
        else:
            if self.torr_at_0v <= 0 or float(torr) <= 0 or self.decades_per_volt == 0:
                return 0
            v = math.log10(float(torr) / self.torr_at_0v) / self.decades_per_volt
        raw = round(v / self.volt_max * self.raw_max)
        return max(0, min(self.raw_max, int(raw)))

    # ---------- 표 보간 ----------
    def _table_to_torr(self, raw):
        pts = self.points
        if len(pts) < 2:
            return UNKNOWN
        if raw <= pts[0][0]:
            return pts[0][1]
        if raw >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            if raw <= x1:
                if x1 == x0:
                    return y1
                t = (raw - x0) / (x1 - x0)
                # 압력은 자릿수가 정보라 로그 공간에서 보간한다(둘 다 양수일 때).
                if y0 > 0 and y1 > 0:
                    return 10.0 ** (math.log10(y0) + t * (math.log10(y1) - math.log10(y0)))
                return y0 + t * (y1 - y0)
        return pts[-1][1]

    def _table_to_raw(self, torr):
        pts = self.points
        if len(pts) < 2:
            return 0
        if torr <= pts[0][1]:
            return int(pts[0][0])
        if torr >= pts[-1][1]:
            return int(pts[-1][0])
        for i in range(1, len(pts)):
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            if torr <= y1:
                if y1 == y0:
                    return int(x1)
                if y0 > 0 and y1 > 0 and torr > 0:
                    t = (math.log10(torr) - math.log10(y0)) / (math.log10(y1) - math.log10(y0))
                else:
                    t = (torr - y0) / (y1 - y0)
                return max(0, min(self.raw_max, int(round(x0 + t * (x1 - x0)))))
        return int(pts[-1][0])

    def is_monotonic(self) -> bool:
        """단조 증가인지 확인한다. 설정 검증이 부른다."""
        if self.mode == "table":
            if len(self.points) < 2:
                return False
            ys = [y for _, y in self.points]
            return all(b > a for a, b in zip(ys, ys[1:]))
        if self.mode == "linear":
            return self.torr_per_volt > 0
        return self.torr_at_0v > 0 and self.decades_per_volt > 0


# ===================== 히터 =====================
def heater_temp(raw) -> float:
    """히터 현재 온도. D00030~41 은 ×0.1 ℃ 이고 부호가 있다(영하도 읽힌다)."""
    if raw is None:
        return UNKNOWN
    v = int(raw) & 0xFFFF
    if v & 0x8000:
        v -= 0x10000
    return v / 10.0


def heater_raw(celsius) -> int:
    """설정 온도·과온 한계를 PLC 값(×0.1 ℃, 부호 있음)으로."""
    if celsius is None:
        return 0
    v = int(round(float(celsius) * 10))
    v = max(-32768, min(32767, v))
    return v & 0xFFFF


class Converters:
    """설정에서 만들어 두는 환산기 묶음. 서버·시뮬레이터·화면 응답이 함께 쓴다."""

    def __init__(self, cfg: dict):
        analog = cfg.get("analog") or {}
        self.raw_max = int(analog.get("raw_max") or 16000)
        self.analog_confirmed = bool(analog.get("confirmed", False))

        press = cfg.get("pressure") or {}
        self.cvg = Pressure(press.get("cvg") or {}, self.raw_max)
        cm_cfg = dict(press.get("cm") or {})
        cm_cfg.setdefault("installed", False)
        self.cm = Pressure(cm_cfg, self.raw_max)

        self.mfc = {}
        for m in cfg.get("mfc") or []:
            no = int(m.get("no") or 0)
            if no:
                self.mfc[no] = Scale(self.raw_max, m.get("full_scale_sccm"),
                                     m.get("confirmed", False), "sccm")

        rf = cfg.get("rf") or {}
        self.rf = Scale(self.raw_max, rf.get("max_w"), rf.get("confirmed", False), "W")
        pcv = cfg.get("pcv") or {}
        self.pcv = Scale(self.raw_max, pcv.get("full_pct", 100), pcv.get("confirmed", False), "%")
        o3 = cfg.get("o3") or {}
        self.o3 = Scale(self.raw_max, o3.get("full"), o3.get("confirmed", False),
                        o3.get("unit", ""))

    def unconfirmed(self) -> list:
        """아직 확정되지 않은 환산 목록. 화면이 "환산 미확정" 표시에 쓴다."""
        out = []
        if not self.analog_confirmed:
            out.append("아날로그 원시값 최대")
        if not self.cvg.confirmed:
            out.append("CVG 압력")
        if self.cm.installed and not self.cm.confirmed:
            out.append("커패시턴스 게이지")
        for no, s in sorted(self.mfc.items()):
            if not s.confirmed or s.full is None:
                out.append(f"MFC{no} 풀스케일")
        if self.rf.full is not None and not self.rf.confirmed:
            out.append("RF 전력")
        if not self.pcv.confirmed:
            out.append("PCV 개도")
        if self.o3.full is not None and not self.o3.confirmed:
            out.append("O3 출력")
        return out
