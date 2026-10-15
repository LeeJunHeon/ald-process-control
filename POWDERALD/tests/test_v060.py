"""v0.6.0 — 가짜 PLC(별도 프로세스 · 실제 같은 물리) · plc_ctl 시험 콘솔 · 실제 공정 시나리오 자동 시험.

★ 가짜 PLC · 프로그램을 별도 프로세스로 띄운다(빈 포트 · 임시 폴더 — 실행 중인 프로그램과 data/ 는 건드리지 않음).
  시나리오는 빠른 배속으로 1(기동) · 3(레시피 정상 완료) · 5(EMO 하나)만. 긴 시나리오 전부는 클라우드에서
  python test/e2e_process.py 로 돌린다.
"""
import os
import re
import sys
import json
import time
import socket
import subprocess

import pytest

from powderald import device as DEV
from powderald import version

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = os.path.join(ROOT, "test")
PY = sys.executable
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def _free_pair():
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", p + 1))
            return p
        except OSError:
            continue
    raise RuntimeError("빈 포트 없음")


def _e2e(*args, timeout=900):
    r = subprocess.run([PY, os.path.join(TEST, "e2e_process.py"), *args], cwd=ROOT, env=ENV,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def test_tools_exist_and_not_bundled():
    for n in ("fake_plc.py", "plc_ctl.py", "e2e_process.py", "fake_plant.json", "_pkg.py"):
        assert os.path.isfile(os.path.join(TEST, n)), n
    spec = open(os.path.join(ROOT, "build.spec"), encoding="utf-8").read()
    assert "'test'" not in spec and '"test"' not in spec and "test/" not in spec      # exe 에 넣지 않는다
    assert "('config/config.example.json', 'config')" in spec                          # config/ 는 예시 한 파일만
    plant = json.load(open(os.path.join(TEST, "fake_plant.json"), encoding="utf-8"))
    for k in ("pressure", "mfc", "heaters", "gauges", "rf", "pcv", "o3", "states"):
        assert k in plant, k
    assert set(plant["states"]) >= {"atm", "vac", "hot"}
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 6, 0)   # v0.6.1: 버전이 오를 때 깨지지 않게


def test_fake_plc_runs_ladder_from_simulator_and_physics_only():
    src = open(os.path.join(TEST, "fake_plc.py"), encoding="utf-8").read()
    assert "class PlantSim(SIM.PlcSim):" in src                     # 래더는 PlcSim 그대로
    # 물리 · 내보내기만 바꾼다(래더 프로그램 P25~P70 은 손대지 않음)
    for name in ("_p25", "_p45", "_p60", "_p70", "_interlocks", "_alarms", "_sequencer", "_execute"):
        assert f"def {name}(" not in src, name


@pytest.fixture
def fake_plc(tmp_path):
    port = _free_pair()
    log = tmp_path / "plc.log"
    p = subprocess.Popen([PY, os.path.join(TEST, "fake_plc.py"), "--port", str(port), "--speed", "20",
                          "--log", str(log)], cwd=ROOT, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            r = _ctl(port, "ping")
            if r[0] == 0:
                break
            time.sleep(0.1)
        yield port, log
    finally:
        try:
            with socket.create_connection(("127.0.0.1", port + 1), timeout=2) as s:
                s.sendall(b'{"op": "quit"}\n')
                s.recv(100)
        except OSError:
            pass
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(5)


def _ctl(port, *args):
    r = subprocess.run([PY, os.path.join(TEST, "plc_ctl.py"), "--port", str(port), *args], cwd=ROOT, env=ENV,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return r.returncode, r.stdout + r.stderr


def test_plc_ctl_status_and_fault(fake_plc):
    port, log = fake_plc
    code, out = _ctl(port, "status", "--count", "1")
    assert code == 0, out
    assert f"ID 0x{DEV.DEVICE_ID:04X}" in out and "장비 상태" in out and "압력 CVG" in out, out
    code, out = _ctl(port, "fault", "emo", "on")
    assert code == 0 and '"ok": true' in out, out
    code, out = _ctl(port, "faults")
    assert re.search(r"● emo", out), out
    time.sleep(0.5)
    code, out = _ctl(port, "status", "--count", "1")
    assert "EMO" not in out.split("입력0")[1].split("\n")[0], out       # EMO OK 입력이 꺼졌다
    code, out = _ctl(port, "fault", "emo", "off")
    assert code == 0, out
    code, out = _ctl(port, "id", "0x1234")
    assert code == 0, out
    code, out = _ctl(port, "status", "--count", "1")
    assert "ID 0x1234" in out and "아님" in out, out
    # v0.6.1(C1): 장비 ID 가 다르면 쓰기를 거절한다(--force 일 때만 — test_v061) — 쓰기 전에 ID 를 되돌린다
    code, out = _ctl(port, "id", f"{DEV.DEVICE_ID:04X}")
    assert code == 0, out
    # 공정 중이 아닐 때만 쓴다 · 가짜 PLC 는 --yes 로 확인 생략 · 하트비트 · 명령 영역은 WRITE 를 쳐야
    code, out = _ctl(port, "--yes", "write", "D01040", "0")
    assert code == 0 and "되읽은 값" in out, out
    r = subprocess.run([PY, os.path.join(TEST, "plc_ctl.py"), "--port", str(port), "--yes", "write", "D01000", "1"],
                       cwd=ROOT, env=ENV, input="no\n", capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 1 and "WRITE" in r.stdout and "취소" in r.stdout, r.stdout
    assert "고장 emo 켬" in log.read_text(encoding="utf-8")


def test_e2e_startup_recipe_emo_pass():
    code, out = _e2e("startup", "recipe", "faults", "--faults", "emo", "--speed", "20")
    assert code == 0, out[-4000:]
    assert re.search(r"PASS\s+startup", out) and re.search(r"PASS\s+recipe", out) and re.search(r"PASS\s+faults", out), out[-3000:]
    assert "PLC 연결(simulate = false · 가짜 PLC)" in out and "EMO → 공정 멈춤" in out
