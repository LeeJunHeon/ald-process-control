"""주소표·비트 해석·DWORD·부호 — PLC 래더와 맺은 약속을 고정한다.

★ 여기 숫자가 바뀌면 래더도 함께 바꿔야 한다. 이 테스트는 "말없이 바뀌는 것"을 막는다.
"""
import pytest

from peald import addresses as A
from peald import device as DEV


def test_status_block_covers_documented_range():
    assert A.STATUS_BASE == 0
    assert A.STATUS_COUNT == 81                 # D00000 ~ D00080
    assert A.D_SCAN_MAX == A.STATUS_BASE + A.STATUS_COUNT - 1


def test_key_addresses():
    assert (A.D_PLC_HB, A.D_STATE, A.D_ACK_NO, A.D_ACK_RESULT) == (0, 1, 2, 3)
    assert (A.D_INTERLOCK, A.D_ALARM0, A.D_ALARM1, A.D_ALARM_NEW) == (4, 5, 6, 7)
    assert (A.D_INPUT0, A.D_INPUT1, A.D_VALVE_OUT, A.D_AUX_OUT) == (8, 9, 10, 14)
    assert (A.D_PC_HB, A.D_CMD_NO, A.D_CMD_CODE) == (1000, 1001, 1002)
    assert A.D_HEATER_SV == 1012 and A.D_MFC_SV == 1030
    assert A.D_PRM_PC_WDT_MS == 1100 and A.D_PRM_HEATER_MAX == 1110
    assert A.DISPLAY_BASE == 4120 and A.DISPLAY_COUNT == 12


def test_recipe_area_layout():
    assert A.D_RCP_STEP_BASE == 2100 and A.RCP_STEP_STRIDE == 8
    assert A.D_RCP_BLOCK_BASE == 2900 and A.RCP_BLOCK_STRIDE == 16
    assert A.D_RCP_GROUP_BASE == 3100 and A.RCP_GROUP_STRIDE == 4
    # 스텝 100개는 블록 영역과 겹치지 않아야 한다.
    assert A.D_RCP_STEP_BASE + A.RCP_STEP_MAX * A.RCP_STEP_STRIDE <= A.D_RCP_BLOCK_BASE
    assert A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_MAX * A.RCP_BLOCK_STRIDE <= A.D_RCP_GROUP_BASE
    assert A.D_RCP_GROUP_BASE + A.RCP_GROUP_MAX * A.RCP_GROUP_STRIDE - 1 <= A.RCP_SUM_END
    assert A.RCP_AREA_COUNT == 1120


@pytest.mark.parametrize("lo,hi,want", [
    (0, 0, 0), (1, 0, 1), (0, 1, 65536), (0xFFFF, 0xFFFF, 0xFFFFFFFF),
    (0x3456, 0x0012, 0x00123456),
])
def test_dword_low_word_is_lower_address(lo, hi, want):
    """★ 아래 주소가 하위 워드다(D00024 하위, D00025 상위). 뒤집으면 시간이 65536배 틀어진다."""
    assert A.dword(lo, hi) == want
    assert A.split_dword(want) == (lo, hi)


@pytest.mark.parametrize("raw,want", [
    (0, 0), (1, 1), (32767, 32767), (32768, -32768), (65535, -1), (0xFFEC, -20),
])
def test_signed16(raw, want):
    """히터 온도만 부호가 있다. 영하를 양수로 읽으면 과온 판정이 뒤집힌다."""
    assert A.to_signed16(raw) == want


def test_bit_helper():
    assert A.bit(0b1010, 1) and A.bit(0b1010, 3)
    assert not A.bit(0b1010, 0) and not A.bit(0b1010, 2)


def test_device_masks_match_valve_definitions():
    """수동 마스크는 실제로 정의된 밸브 비트만 담아야 한다."""
    manual_bits = [v["bit"] for v in DEV.VALVES if not v.get("auto")]
    for b in range(16):
        in_mask = bool((DEV.MANUAL_VALVE_MASK >> b) & 1)
        assert in_mask == (b in manual_bits), f"b{b} 마스크와 밸브 정의가 어긋남"


def test_auto_valve_not_in_manual_mask():
    """PLC 가 자동으로 여는 밸브(Powder PV-B)는 수동 요청 대상이 아니다."""
    for v in DEV.VALVES:
        if v.get("auto"):
            assert not (DEV.MANUAL_VALVE_MASK >> v["bit"]) & 1


def test_aux_cmd_mask_excludes_pump_and_vent():
    """VV·IV-E·펌프는 명령 8~11 이 다룬다 — 수동 적용(12) 마스크에 들어가면 안 된다."""
    for bit in (A.AUX_VV, A.AUX_IVE, A.AUX_PUMP):
        assert not (DEV.AUX_CMD_MASK >> bit) & 1


def test_alarm_definitions_are_unique_and_known():
    for defs in (DEV.ALARMS0, DEV.ALARMS1):
        bits = [a["bit"] for a in defs]
        assert len(bits) == len(set(bits))
        assert all(0 <= b < 16 for b in bits)


def test_critical_mask_marks_the_same_bits_as_definitions():
    """중대 마스크(래더가 쓰는 값)와 화면 문구의 crit 표시가 어긋나면
    운전자가 '경고'로 본 것이 실제로는 장비를 세운다."""
    for a in DEV.ALARMS0:
        in_mask = bool((DEV.CRITICAL_MASK0 >> a["bit"]) & 1)
        assert in_mask == a["crit"], f"알람0 b{a['bit']} ({a['name']}) 등급 불일치"


def test_state_and_result_names_cover_codes():
    for c in range(7):
        assert c in A.STATE_NAMES
    for c in range(5):
        assert c in A.RESULT_NAMES
    for c in range(1, 15):
        assert c in A.CMD_NAMES
