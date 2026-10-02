"""
trend_buffer.py — 트렌드용 링버퍼 (압력 · MFC · 히터).

두 벌을 둔다.
  slow : 1 Hz · 1시간  — 온도·MFC·압력의 장시간 추세
  fast : 10 Hz · 10분  — 압력 전용. 밸브 펄스가 짧아 1 Hz로는 아예 보이지 않는다.

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
        """★ 작업 스레드에서도 불린다(/api/trend) — 샘플링 루프가 그사이 deque 에 넣으면 파이썬 반복은
        'deque mutated during iteration' 이 난다. C 수준으로 한 번 복사(list)한 뒤 거른다(행 dict 는 넣은 뒤 바뀌지 않는다)."""
        cut = now - sec
        return [r for r in list(self.buf) if r["t"] >= cut]

    def clear(self):
        self.buf.clear()


class TrendBuffer:
    def __init__(self):
        self.slow = _Ring(SLOW_HZ * SLOW_SEC)
        self.fast = _Ring(FAST_HZ * FAST_SEC)
        self._next_slow = 0.0

    def record(self, now: float, live: dict):
        """샘플링 루프가 매 tick 부른다. PLC 가 끊겼으면 아무것도 남기지 않는다 —
        끊긴 구간에 0을 채우면 나중에 그래프가 '압력이 0이었다'고 거짓말을 한다."""
        press = live.get("pressure")
        if not press:
            return
        self.fast.push({"t": now, "p": press.get("cvg")})
        if now < self._next_slow:
            return
        self._next_slow = now + 1.0 / SLOW_HZ
        self.slow.push({
            "t": now,
            "p": press.get("cvg"),
            "c": press.get("cm"),
            "h": {h["ch"]: h.get("pv") for h in (live.get("heaters") or [])},
            "m": {m["no"]: m.get("pv") for m in (live.get("mfc") or [])},
        })

    def series(self, sec: float, now: float) -> dict:
        return {
            "now": now,
            "sec": sec,
            "slow": self.slow.since(sec, now),
            # 압력 고해상도는 fast 버퍼가 덮는 구간(10분)까지만 의미가 있다.
            "fast": self.fast.since(min(sec, FAST_SEC), now),
        }


trend = TrendBuffer()
