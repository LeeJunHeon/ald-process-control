"""
modbus.py — Modbus TCP 클라이언트 (asyncio, 직접 구현).

PLC 가 서버, PC 가 클라이언트다. 새 런타임 의존성을 만들지 않으려고 직접 구현한다 —
필요한 기능이 FC03/06/16 세 개뿐이라 라이브러리를 들일 이유가 없다.

프레임: MBAP 헤더(트랜잭션 2 + 프로토콜 2 + 길이 2 + 유닛 1) + PDU
★ 트랜잭션 번호를 반드시 확인한다. 시간 초과가 난 뒤 늦게 도착한 응답을 다음 요청의
  응답으로 잘못 읽으면, 엉뚱한 주소의 값을 그 주소의 값으로 믿게 된다.
★ 요청은 한 번에 하나만 보낸다(잠금). PLC 쪽 큐가 얕아 동시에 보내면 섞인다.
"""

import struct
import asyncio

# 기능 코드
FC_READ_HOLDING = 3
FC_WRITE_SINGLE = 6
FC_WRITE_MULTIPLE = 16

# 한 번에 다룰 수 있는 개수 (Modbus 규격)
MAX_READ = 125
MAX_WRITE = 120          # 규격은 123이지만 여유를 둔다

EXCEPTION_NAMES = {
    1: "지원하지 않는 기능", 2: "잘못된 주소", 3: "잘못된 값",
    4: "PLC 내부 오류", 5: "처리 중", 6: "장치 사용 중",
}


class ModbusError(Exception):
    """프로토콜 오류(예외 응답·프레임 불일치). 연결 자체는 살아 있을 수 있다."""


class ModbusTimeout(Exception):
    """응답 시간 초과. 재연결 대상이다."""


class ModbusClient:
    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout_ms: int = 1000):
        self.host = host
        self.port = int(port)
        self.unit_id = int(unit_id)
        self.timeout = max(0.05, float(timeout_ms) / 1000.0)
        self._reader = None
        self._writer = None
        self._tid = 0
        self._lock = asyncio.Lock()

    # ===================== 연결 =====================
    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self):
        self.close_sync()
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout)
        self._tid = 0

    def close_sync(self):
        """예외를 던지지 않는 정리. 재연결 경로에서 부른다."""
        w = self._writer
        self._reader = self._writer = None
        if w is not None:
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass

    async def close(self):
        w = self._writer
        self.close_sync()
        if w is not None:
            try:
                await w.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    # ===================== 프레임 =====================
    def _next_tid(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid

    async def _request(self, pdu: bytes) -> bytes:
        """요청 한 번. 응답 PDU(기능 코드 포함)를 돌려준다."""
        if not self.connected:
            raise ModbusError("연결되어 있지 않습니다")
        async with self._lock:
            tid = self._next_tid()
            frame = struct.pack(">HHHB", tid, 0, len(pdu) + 1, self.unit_id) + pdu
            try:
                self._writer.write(frame)
                await self._writer.drain()
                head = await asyncio.wait_for(self._reader.readexactly(7), self.timeout)
                rtid, proto, length, unit = struct.unpack(">HHHB", head)
                if length < 1:
                    raise ModbusError("응답 길이가 올바르지 않습니다")
                body = await asyncio.wait_for(self._reader.readexactly(length - 1), self.timeout)
            except asyncio.IncompleteReadError as e:
                raise ModbusTimeout("연결이 끊겼습니다") from e
            except (asyncio.TimeoutError, TimeoutError) as e:
                # ★ 여기서 소켓을 버린다. 늦게 도착할 응답이 다음 요청과 섞이면
                #   엉뚱한 주소의 값을 읽게 된다 — 재연결이 훨씬 안전하다.
                self.close_sync()
                raise ModbusTimeout("응답 시간 초과") from e
            except (OSError, ConnectionError) as e:
                raise ModbusTimeout(f"소켓 오류: {e}") from e

            if proto != 0:
                raise ModbusError(f"프로토콜 식별자가 0이 아닙니다: {proto}")
            if rtid != tid:
                self.close_sync()
                raise ModbusTimeout(f"트랜잭션 번호 불일치 (보냄 {tid} / 받음 {rtid})")
            if unit != self.unit_id:
                raise ModbusError(f"유닛 번호 불일치 ({unit})")

            fc = body[0]
            if fc & 0x80:
                code = body[1] if len(body) > 1 else 0
                raise ModbusError(f"PLC 예외 응답: {EXCEPTION_NAMES.get(code, code)} (코드 {code})")
            return body

    # ===================== 기능 =====================
    async def read_holding(self, addr: int, count: int) -> list:
        """FC03. count 가 125 를 넘으면 나눠 읽는다."""
        if count <= 0:
            return []
        out = []
        left, cur = int(count), int(addr)
        while left > 0:
            n = min(left, MAX_READ)
            pdu = struct.pack(">BHH", FC_READ_HOLDING, cur, n)
            body = await self._request(pdu)
            nbytes = body[1]
            if nbytes != n * 2 or len(body) < 2 + nbytes:
                raise ModbusError(f"읽기 응답 길이 불일치 (기대 {n * 2}, 받음 {nbytes})")
            out.extend(struct.unpack(">" + "H" * n, body[2:2 + nbytes]))
            cur += n
            left -= n
        return out

    async def write_single(self, addr: int, value: int) -> None:
        """FC06."""
        v = int(value) & 0xFFFF
        pdu = struct.pack(">BHH", FC_WRITE_SINGLE, int(addr), v)
        body = await self._request(pdu)
        if len(body) >= 5:
            raddr, rval = struct.unpack(">HH", body[1:5])
            if raddr != int(addr) or rval != v:
                raise ModbusError(f"쓰기 응답 불일치 (주소 {raddr}, 값 {rval})")

    async def write_multiple(self, addr: int, values) -> None:
        """FC16. 120 개씩 나눠 쓴다(레시피 표 1120 워드가 여기로 간다)."""
        vals = [int(v) & 0xFFFF for v in values]
        if not vals:
            return
        cur = int(addr)
        i = 0
        while i < len(vals):
            chunk = vals[i:i + MAX_WRITE]
            n = len(chunk)
            pdu = (struct.pack(">BHHB", FC_WRITE_MULTIPLE, cur, n, n * 2)
                   + struct.pack(">" + "H" * n, *chunk))
            body = await self._request(pdu)
            if len(body) >= 5:
                raddr, rn = struct.unpack(">HH", body[1:5])
                if raddr != cur or rn != n:
                    raise ModbusError(f"다중 쓰기 응답 불일치 (주소 {raddr}, 개수 {rn})")
            cur += n
            i += n
