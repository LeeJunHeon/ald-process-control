"""원시값 ↔ 공학 단위 환산 — 역함수 왕복과 단조 증가.

★ 환산이 단조 증가가 아니면 역함수가 여러 답을 갖고, 베이스 압력 기준이 엉뚱한
  원시값으로 PLC 에 들어간다. 이 성질을 테스트로 고정한다.
"""
import math

import pytest

from peald import convert
from peald.convert import Converters, Pressure, Scale, heater_raw, heater_temp


# ===================== 압력 =====================
def cvg(**over):
    base = {"mode": "loglinear", "volt_max": 10.0, "torr_at_0v": 1.0e-4,
            "decades_per_volt": 1.0}
    base.update(over)
    return Pressure(base, 16000)


@pytest.mark.parametrize("torr", [1.0e-3, 5.0e-2, 0.5, 5.0, 100.0, 760.0])
def test_loglinear_roundtrip(torr):
    p = cvg()
    raw = p.to_raw(torr)
    back = p.to_torr(raw)
    # 원시값이 정수라 되돌린 값에 양자화 오차가 있다 — 0.1 % 안이면 충분하다.
    assert back == pytest.approx(torr, rel=0.01)


def test_loglinear_is_monotonic_increasing():
    p = cvg()
    vals = [p.to_torr(r) for r in range(0, 16001, 500)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
    assert p.is_monotonic()


def test_raw_is_clamped_to_range():
    p = cvg()
    assert p.to_raw(1e-9) == 0                 # 범위 아래는 0
    assert p.to_raw(1e9) == 16000              # 범위 위는 최대
    assert p.to_torr(-5) == p.to_torr(0)
    assert p.to_torr(99999) == p.to_torr(16000)


def test_linear_mode_roundtrip():
    p = Pressure({"mode": "linear", "volt_max": 10.0, "torr_at_0v": 0.0,
                  "torr_per_volt": 100.0}, 16000)
    assert p.is_monotonic()
    for torr in (0.0, 50.0, 500.0, 1000.0):
        assert p.to_torr(p.to_raw(torr)) == pytest.approx(torr, abs=0.2)


def test_table_mode_roundtrip_and_monotonic():
    p = Pressure({"mode": "table", "points": [[0, 1e-4], [4000, 1e-2], [8000, 1.0],
                                              [12000, 100.0], [16000, 1000.0]]}, 16000)
    assert p.is_monotonic()
    for torr in (1e-3, 0.1, 10.0, 500.0):
        assert p.to_torr(p.to_raw(torr)) == pytest.approx(torr, rel=0.05)


def test_non_monotonic_table_is_rejected():
    p = Pressure({"mode": "table", "points": [[0, 10.0], [8000, 1.0], [16000, 100.0]]}, 16000)
    assert not p.is_monotonic()


def test_not_installed_gauge_returns_none():
    p = Pressure({"installed": False, "mode": "linear"}, 16000)
    assert p.to_torr(1234) is None
    assert p.to_raw(1.0) == 0


# ===================== 선형 스케일 (MFC·RF·PCV·O3) =====================
def test_scale_roundtrip():
    s = Scale(16000, 1000.0)
    assert s.to_raw(0) == 0
    assert s.to_raw(1000) == 16000
    assert s.to_eng(8000) == pytest.approx(500.0)
    for v in (0, 12.5, 250, 999.9):
        assert s.to_eng(s.to_raw(v)) == pytest.approx(v, abs=0.1)


def test_scale_without_full_scale_is_unknown():
    """풀스케일 미정이면 값을 지어내지 않고 None(화면에서 '—')."""
    s = Scale(16000, None)
    assert s.to_eng(8000) is None
    assert s.to_raw(500) == 0


def test_scale_clamps():
    s = Scale(16000, 100.0)
    assert s.to_raw(-10) == 0
    assert s.to_raw(1e6) == 16000


# ===================== 히터 =====================
@pytest.mark.parametrize("c", [-40.0, 0.0, 25.5, 120.0, 300.0, 450.0])
def test_heater_roundtrip(c):
    assert heater_temp(heater_raw(c)) == pytest.approx(c, abs=0.05)


def test_heater_negative_is_signed():
    assert heater_raw(-20.0) == 0xFF38
    assert heater_temp(0xFF38) == pytest.approx(-20.0)


def test_heater_none_is_zero_raw():
    """max_c 가 null 인 채널은 PLC 한계에 0 (소프트 과온 감시 없음)."""
    assert heater_raw(None) == 0


# ===================== 묶음 =====================
def test_converters_lists_unconfirmed(cfg):
    c = Converters(cfg)
    names = c.unconfirmed()
    assert "CVG 압력" in names
    assert any("MFC" in n for n in names)


def test_unconfirmed_lists_only_devices_present(cfg):
    """이 장비에 없는 장치(PCV·RF·O3)의 '환산 미확정'은 내지 않는다."""
    from peald import device as DEV
    names = Converters(cfg).unconfirmed()
    assert ("PCV 개도" in names) == (DEV.HAS_PCV and not (cfg.get("pcv") or {}).get("confirmed"))
    if not DEV.HAS_RF:
        assert "RF 전력" not in names
    if not DEV.HAS_O3:
        assert "O3 출력" not in names


def test_converters_use_config_raw_max(cfg):
    cfg["analog"]["raw_max"] = 32000
    c = Converters(cfg)
    assert c.cvg.raw_max == 32000
    assert c.mfc[1].raw_max == 32000
