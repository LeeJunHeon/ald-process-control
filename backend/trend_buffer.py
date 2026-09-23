"""
trend_buffer.py — 트렌드용 링버퍼.

두 벌을 둔다.
  slow : 1 Hz · 1시간  — 온도·MFC·압력의 장시간 추세
  fast : 10 Hz · 10분  — 압력 전용. ALD 펄스는 0.1 s 라서 1 Hz로는 아예 보이지 않는다.

링버퍼로 두는 이유: 공정은 몇 시간씩 돈다. 배열을 계속 늘리면 메모리가 선형으로 늘고
직렬화 비용도 함께 커진다. 크기를 고정하면 최악의 경우가 처음부터 정해진다.
"""

import collections

SLOW_HZ = 1
SLOW_SEC = 3600
FAST_HZ = 10
FAST_SEC = 600


class _Ring:
    def __init__(self, maxlen: int):
        self.buf = collections.deque(maxlen=maxlen)

    def push(self, row: dict):
        self.buf.append(row)

    def since(self, sec: float, now: float) -> list:
        cut = now - sec
        return [r for r in self.buf if r["t"] >= cut]

    def clear(self):
        self.buf.clear()


class TrendBuffer:
    def __init__(self):
        self.slow = _Ring(SLOW_HZ * SLOW_SEC)
        self.fast = _Ring(FAST_HZ * FAST_SEC)
        self._next_slow = 0.0
        self.pulses = collections.deque(maxlen=2000)   # 펄스 표시 띠 (t, 라인id)

    def record(self, now: float, snap: dict, pulse_line: str = ""):
        """샘플링 루프가 매 tick 부른다. 압력은 매번(10 Hz), 나머지는 1 Hz로 남긴다."""
        g = snap.get("gauges") or {}
        self.fast.push({"t": now, "p": g.get("baratron")})
        if pulse_line:
            self.pulses.append({"t": now, "line": pulse_line})
        if now < self._next_slow:
            return
        self._next_slow = now + 1.0 / SLOW_HZ
        self.slow.push({
            "t": now,
            "p": g.get("baratron"),
            "c": g.get("convectron"),
            "h": {k: v.get("pv") for k, v in (snap.get("heaters") or {}).items()},
            "m": {k: v.get("pv") for k, v in (snap.get("mfc") or {}).items()},
        })

    def series(self, sec: float, now: float) -> dict:
        """GET /api/trend 응답. 이후 값은 telemetry 로 화면이 이어 붙인다."""
        return {
            "now": now,
            "sec": sec,
            "slow": self.slow.since(sec, now),
            # 압력 고해상도는 fast 버퍼가 덮는 구간(10분)까지만 의미가 있다.
            "fast": self.fast.since(min(sec, FAST_SEC), now),
            "pulses": [p for p in self.pulses if p["t"] >= now - sec],
        }


trend = TrendBuffer()
