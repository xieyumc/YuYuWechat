# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


SERVER_DIR = Path(SPECPATH).resolve()
REPO_DIR = SERVER_DIR.parent
PYWECHAT_DIR = REPO_DIR / "pywechat"
PYWEIXIN_DIR = PYWECHAT_DIR / "pyweixin"


def existing_data(path: Path, target: str):
    return [(str(path), target)] if path.exists() else []


datas = []
datas += existing_data(SERVER_DIR / "db.sqlite3", ".")
datas += existing_data(SERVER_DIR / "YuYuWechatV3", "YuYuWechatV3")
datas += existing_data(SERVER_DIR / "wechat_app", "wechat_app")
datas += existing_data(SERVER_DIR / "wechat_bridge", "wechat_bridge")
datas += existing_data(PYWEIXIN_DIR, "pywechat/pyweixin")
datas += collect_data_files("django")
datas += collect_data_files("drf_spectacular")
datas += collect_data_files("rest_framework")
for package in ("emoji", "pycaw"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass


hiddenimports = []
for package in (
    "YuYuWechatV3",
    "wechat_app",
    "wechat_app.migrations",
    "wechat_bridge",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "django",
):
    hiddenimports += collect_submodules(package)

for package in (
    "pyweixin",
    "pywechat",
    "pywinauto",
    "pyautogui",
    "pyperclip",
    "emoji",
    "pycaw",
    "pycaw.pycaw",
    "win32com",
    "win32com.client",
    "win32clipboard",
    "win32api",
    "win32con",
    "win32gui",
    "pythoncom",
    "comtypes",
    "psutil",
    "packaging",
    "PIL",
):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass


a = Analysis(
    ["packaging/pyinstaller_entry.py"],
    pathex=[str(SERVER_DIR), str(PYWECHAT_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YuYuWechatV3_Server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="YuYuWechatV3_Server",
)
