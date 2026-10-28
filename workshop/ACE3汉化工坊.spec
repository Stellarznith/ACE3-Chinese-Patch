# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 构建描述（发布仓库版，全部相对路径）。

发布布局::

    publish/
        tools/            ← 工坊依赖的底层模块 + ace3_text_locator
        workshop/         ← 本 spec 所在目录（main.py / core / ui / assets）

PyInstaller 执行 spec 时会注入全局 ``SPECPATH``（本 spec 所在目录），
用它往上找 ``tools``，因此整包搬到任何盘符都能直接构建。
"""
import os

# SPECPATH 由 PyInstaller 注入；直接用 python 跑 spec 时退回当前目录。
SPEC_DIR = os.path.abspath(globals().get('SPECPATH') or os.getcwd())
ROOT_DIR = os.path.dirname(SPEC_DIR)              # publish/
TOOLS_DIR = os.path.join(ROOT_DIR, 'tools')
LOCATOR_DIR = os.path.join(TOOLS_DIR, 'ace3_text_locator')
ICON_FILE = os.path.join(SPEC_DIR, 'assets', 'ace3.ico')

a = Analysis(
    [os.path.join(SPEC_DIR, 'main.py')],
    pathex=[SPEC_DIR, TOOLS_DIR, LOCATOR_DIR],
    binaries=[],
    datas=[],
    hiddenimports=['core', 'core.env', 'core.codec', 'core.slots', 'core.fontcov', 'core.relayout', 'core.writer', 'core.search', 'core.editplan', 'core.pipeline', 'core.estimate', 'core.settings', 'core.paths', 'ui', 'ui.theme', 'ui.preview', 'ui.widgets', 'ui.workers', 'ui.results', 'ui.editor', 'ui.queue', 'ui.checkdesk', 'ui.indexmgr', 'ui.main_window', 'locator_core', 'query', 'build_index', '_r223_apply_plan', '_r104_glyphfix2', '_r104_audit2', '_r104_text_audit', '_r211_cmap', '_r211_override', '_r206_memfix', '_r213_lz', 'lz_encode', 'lz_decode_entry', 'ace3_pack', 'front_atlas'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia', 'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtNetwork', 'PySide6.QtSql', 'PySide6.QtTest', 'tkinter', 'matplotlib', 'scipy', 'pandas', 'IPython'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ACE3汉化工坊',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    icon=[ICON_FILE],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ACE3汉化工坊',
)
