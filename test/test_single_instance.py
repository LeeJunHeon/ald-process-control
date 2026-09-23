"""챔버별 단일 실행 뮤텍스 — 같은 챔버만 막고 다른 챔버는 허용해야 한다.

★ 이 규칙이 깨지면 둘 중 하나가 일어난다.
   - 너무 느슨하면: 같은 챔버에 두 프로그램이 붙어 서로 밸브 명령을 덮어쓴다.
   - 너무 빡빡하면: 챔버 2를 아예 띄울 수 없다(납품 불가).
"""
import sys

import pytest

import window

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="뮤텍스는 Windows 전용 (다른 OS에서는 항상 허용)")


@pytest.fixture(autouse=True)
def clean_mutex():
    """테스트마다 핸들을 비우고, 끝나면 닫아 다음 테스트에 새지 않게 한다."""
    import ctypes
    window._MUTEX_HANDLE = None
    yield
    if window._MUTEX_HANDLE:
        ctypes.windll.kernel32.CloseHandle(window._MUTEX_HANDLE)
    window._MUTEX_HANDLE = None


def test_same_chamber_second_instance_is_blocked():
    window.set_chamber("pytest_a", "A")
    assert window.acquire_single_instance() is True
    first = window._MUTEX_HANDLE
    # 다른 프로세스인 척: 재진입 가드를 비우고 같은 이름으로 다시 잡아 본다.
    window._MUTEX_HANDLE = None
    assert window.acquire_single_instance() is False
    window._MUTEX_HANDLE = first


def test_different_chamber_is_allowed():
    window.set_chamber("pytest_a", "A")
    assert window.acquire_single_instance() is True
    first = window._MUTEX_HANDLE
    window._MUTEX_HANDLE = None
    window.set_chamber("pytest_b", "B")
    assert window.acquire_single_instance() is True, "다른 챔버는 동시에 떠야 한다"
    import ctypes
    ctypes.windll.kernel32.CloseHandle(first)


def test_reentrant_call_is_safe():
    """창 경로와 lifespan 이 둘 다 부른다 — 두 번째 호출이 자기 자신에 걸리면 안 된다."""
    window.set_chamber("pytest_c", "C")
    assert window.acquire_single_instance() is True
    assert window.acquire_single_instance() is True


def test_window_title_includes_chamber_name():
    window.set_chamber("ald1", "ALD-1")
    assert window.TITLE == "ALD-1 · ALD Process Control"
