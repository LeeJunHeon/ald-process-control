# -*- mode: python ; coding: utf-8 -*-
"""
build.spec — PyInstaller onedir 빌드 설정 (납품용).

빌드:  pyinstaller build.spec --clean --noconfirm
결과:  dist/ALDControl/ALDControl.exe

★ 버전은 backend/version.py 에서 관리한다. 배포 전 APP_VERSION·BUILD_DATE 를 갱신할 것.

onedir 을 쓰는 이유:
  - 시작이 빠르다(매 실행 압축 해제 없음)
  - config.json 이 exe 옆에 자연스럽게 놓인다(paths.DATA_ROOT = exe 폴더)
  - 문제 파일 하나만 교체할 수 있다

★ 2챔버 납품: 같은 exe 를 챔버별 폴더 두 곳에 두고, 폴더마다 config.json 을 둔다.
  데이터 폴더(data/<챔버id>/)는 실행하면서 자동으로 만들어진다.

★ config.json 과 data/ 는 datas 에 넣지 않는다. 번들에 들어가면 읽기 전용 임시 폴더로
  가서 저장이 유실된다(paths.py 참고). 배포 시 dist/ALDControl/ 안에 수동으로 동봉한다.
"""

import os
import sys

block_cipher = None

# exe 파일 속성(회사·버전)을 채운다. 백신 신뢰도와 고객 인상에 영향이 있고,
# 버전이 없으면 납품 후 "어느 빌드인지" 특정할 수 없다.
# ★ 리눅스 빌드나 PyInstaller API 차이로 실패해도 빌드는 계속돼야 하므로 None 폴백.
sys.path.insert(0, os.path.join(SPECPATH, 'backend'))
_version_res = None
try:
    import version as _appver
    _vt = tuple(int(x) for x in _appver.APP_VERSION.split('.'))[:3] + (0,)
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
        StringStruct, VarFileInfo, VarStruct)
    _version_res = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_vt, prodvers=_vt),
        kids=[StringFileInfo([StringTable('041204B0', [
            StringStruct('CompanyName', 'VANAM INC.'),
            StringStruct('FileDescription', 'ALD Process Control'),
            StringStruct('FileVersion', _appver.APP_VERSION),
            StringStruct('ProductName', 'ALD Process Control'),
            StringStruct('ProductVersion', _appver.APP_VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [1042, 1200])])],
    )
except Exception:
    _version_res = None   # 리눅스 빌드·API 차이 등 — 버전 리소스 없이 진행

a = Analysis(
    ['backend/server.py'],
    pathex=['backend'],          # backend/ 안의 모듈을 최상위 이름으로 import 하므로 필요
    binaries=[],
    datas=[
        ('frontend', 'frontend'),   # 읽기 전용 화면 자원 → BUNDLE_ROOT/frontend
    ],
    hiddenimports=[
        # pywebview: Windows 백엔드(WebView2)
        'webview.platforms.edgechromium',
        # uvicorn: 런타임에 문자열로 골라 import 하는 구현체들
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        # auto 계열이 실제로 고르는 구현체들. 프리즈 환경에서 정적 분석에 안 잡혀
        # exe 에서만 서버 스레드가 죽는 것을 막는다.
        'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.lifespan.off',
        'h11', 'httptools', 'websockets',
        # pymodbus 는 다음 단계(plc provider)에서 추가한다.
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows 는 pywebview 의 edgechromium(WebView2) 백엔드만 쓴다.
        # 개발 PC 에 PyQt5·PyQt6 가 함께 설치돼 있으면 PyInstaller 가
        # "multiple Qt bindings" 오류로 빌드를 중단하므로 Qt/GTK 백엔드는 제외한다.
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi',
        # 이 프로그램이 쓰지 않는 무거운 개발용 패키지(설치돼 있으면 딸려 들어간다)
        'matplotlib', 'tkinter', 'IPython', 'jedi', 'zmq', 'PIL', 'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ALDControl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 압축은 백신 오탐의 흔한 원인 — 상용 납품이므로 끈다
    console=False,       # ★ 배포판. 디버깅 시 True 로 바꾼다
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_res,
    # icon='assets/app.ico'  # .ico 파일이 준비되면 추가
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ALDControl',
)
