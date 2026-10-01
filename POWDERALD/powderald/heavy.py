"""
heavy.py — '무거운 조회' 하나의 문(데이터 로그 목록 · 그래프 · 표, 트렌드 이력 · 내보내기).

★ 작업 스레드에서 돌아도 CSV 풀기 · JSON 만들기는 GIL 을 잡는다. 여러 개가 동시에 돌면 이벤트 루프가
  차례를 못 받아 PC 하트비트가 멈춘다(44 MB × 18 동시 → 3.65 s → PC 통신 알람 → 안전 정지).
  그래서 서버 전체에서 동시에 TOTAL 개만, 그중 원격은 합쳐서 REMOTE 개만 돈다.
  - 원격 칸이 차 있으면 기다리지 않고 바로 거절(429) — 원격이 칸을 다 잡아 로컬을 줄 세우지 못하게.
  - 로컬은 줄을 선다.
"""

import json
import math
import asyncio
import threading
from collections import OrderedDict

TOTAL = 2               # 서버 전체 동시 무거운 조회
REMOTE = 1              # 그중 원격(합쳐서)
BUSY_TEXT = "다른 조회가 끝난 뒤 다시 시도하세요"


class Busy(Exception):
    """원격 칸이 차 있다(→ 429)."""


class Gate:
    def __init__(self):
        self._sem = None
        self._loop = None
        self._remote = 0

    def _semaphore(self):
        # 서버는 루프 하나에서 돈다. 루프가 바뀌면(시험 · 다시 시작) 새로 만든다 — 다른 루프에 묶인
        # 세마포어를 기다리면 RuntimeError
        loop = asyncio.get_running_loop()
        if self._sem is None or self._loop is not loop:
            self._sem = asyncio.Semaphore(TOTAL)
            self._loop = loop
            self._remote = 0
        return self._sem

    async def run(self, fn, *args, remote: bool = False):
        """fn(*args) 를 작업 스레드에서 — 문을 지나서. 원격 칸이 차 있으면 Busy."""
        sem = self._semaphore()
        if remote:
            if self._remote >= REMOTE:
                raise Busy(BUSY_TEXT)
            self._remote += 1
        try:
            async with sem:
                return await asyncio.to_thread(fn, *args)
        finally:
            if remote:
                self._remote -= 1

    @property
    def remote_busy(self) -> int:
        return self._remote


gate = Gate()


class ResultCache:
    """(경로, 크기, 수정 시각) → 결과 바이트. 최근 N 개만."""

    def __init__(self, size: int = 4):
        self.size = size
        self._d = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
                self.hits += 1
            return v

    def put(self, key, value):
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)

    def clear(self):
        with self._lock:
            self._d.clear()


SIG = 5                 # 그래프 · 이력 값 유효 숫자(화면 · 커서 표시에 충분 — JSON 크기를 줄인다)


def _sig(v):
    return v if v is None or v == 0 or not math.isfinite(v) else float(f"{v:.{SIG}g}")


def rows_json(res: dict) -> bytes:
    """rows 가 [[t, [최소, 최대, 평균] | None, …], …] 인 조회 결과(데이터 로그 그래프 · 트렌드 이력)의 JSON.
    ★ C 인코더는 한 번 부르는 동안 GIL 을 놓지 않는다(3.8 MB 에 약 60 ms). 값을 유효 숫자 5 자리로 줄이고, 줄을 200 개씩 나눠 인코딩한다(사이마다 루프가 차례를 받는다)."""
    rows = res["rows"]
    head = {k: v for k, v in res.items() if k != "rows"}
    parts = []
    for i in range(0, len(rows), 200):
        chunk = [[round(r[0], 3)] + [None if c is None else [_sig(c[0]), _sig(c[1]), _sig(c[2])] for c in r[1:]]
                 for r in rows[i:i + 200]]
        parts.append(json.dumps(chunk, ensure_ascii=False)[1:-1])
    # {...} — 끝의 } 앞에 rows 를 붙인다
    body = json.dumps(head, ensure_ascii=False, default=lambda o: None).encode("utf-8")
    return body[:-1] + b', "rows": [' + ", ".join(p for p in parts if p).encode("utf-8") + b"]}"
