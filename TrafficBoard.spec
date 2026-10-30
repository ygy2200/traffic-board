# -*- mode: python ; coding: utf-8 -*-
# 路径一律相对 SPECPATH（本文件所在目录）：写死 C:/Users/y/... 换台机器/换盘符就打包失败。
import os

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(SPECPATH)

datas = [(os.path.join(ROOT, 'assets', 'icon.ico'), 'assets')]
datas += collect_data_files('qfluentwidgets')

# upx 保持 False：本机 PATH 与 python Scripts 里都没有 upx.exe，
# 写 True 只会静默不生效（PyInstaller 直接跳过），是假的优化声明。
a = Analysis(
    [os.path.join(ROOT, 'src', 'main.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[],
    datas=datas,
    hiddenimports=['qfluentwidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'cv2', 'scipy', 'imageio_ffmpeg', 'pandas', 'matplotlib', 'numpy', 'PIL', 'babel', 'tkinter', 'PyQt5'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TrafficBoard',
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
    icon=[os.path.join(ROOT, 'assets', 'icon.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='TrafficBoard',
)
