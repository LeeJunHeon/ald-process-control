# -*- mode: python ; coding: utf-8 -*-
"""
build.spec — PEALD 제어 프로그램 (PyInstaller onedir).

빌드:  cd PEALD && pyinstaller build.spec --clean --noconfirm
결과:  dist/PEALD_Control/PEALD_Control.exe

★ 두 프로그램은 각자 빌드한다. 한 spec 에서 둘 다 만들지 않는다 —
  이름·아이콘·데이터 폴더가 섞이면 배포 패키지가 오염된다.
★ config.json 과 data/ 는 datas 에 넣지 않는다. 번들에 들어가면 읽기 전용 임시
  폴더로 가서 저장이 유실된다(paths.py 참고). 배포 시 exe 폴더에 동봉한다.
★ 빌드 검증은 3단계에서 한다. 이번에는 이름·아이콘·경로만 맞춘다.
"""

import os
import sys

block_cipher = None

sys.path.insert(0, SPECPATH)
_version_res = None
try:
    from peald import version as _v, device as _dev
    _vt = tuple(int(x) for x in _v.APP_VERSION.split('.'))[:3] + (0,)
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
        StringStruct, VarFileInfo, VarStruct)
    _version_res = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_vt, prodvers=_vt),
        kids=[StringFileInfo([StringTable('041204B0', [
            StringStruct('CompanyName', 'VANAM INC.'),
            StringStruct('FileDescription', _v.APP_NAME),
            StringStruct('FileVersion', _v.APP_VERSION),
            StringStruct('ProductName', _v.APP_NAME),
            StringStruct('ProductVersion', _v.APP_VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [1042, 1200])])],
    )
except Exception:
    _version_res = None   # 리눅스 빌드·API 차이 등 — 버전 리소스 없이 진행

a = Analysis(
    ['run.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        ('frontend', 'frontend'),       # 읽기 전용 화면 자원
        ('assets', 'assets'),           # 아이콘
        ('config', 'config'),           # config.example.json (없을 때 기동용)
    ],
    hiddenimports=[
        'webview.platforms.edgechromium',
        'uvicorn.logging',
        'uvicorn.loops.auto', 'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.lifespan.on', 'uvicorn.lifespan.off',
        'h11', 'httptools', 'websockets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows 는 pywebview 의 edgechromium(WebView2) 백엔드만 쓴다.
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi',
        'matplotlib', 'tkinter', 'IPython', 'jedi', 'zmq', 'PIL', 'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='PEALD_Control',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 압축은 백신 오탐의 흔한 원인 — 상용 배포이므로 끈다
    console=False,       # ★ 배포판. 디버깅 시 True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_res,
    icon=os.path.join(SPECPATH, 'assets', 'peald.ico'),
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='PEALD_Control',
)
