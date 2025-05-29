# -*- mode: python ; coding: utf-8 -*-

import os
import drf_spectacular

# 动态获取 drf-spectacular 的 templates 目录路径
drf_spectacular_templates_path = os.path.join(os.path.dirname(drf_spectacular.__file__), 'templates')

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('wechat_app', 'wechat_app'),
        ('YuYuWechatV2', 'YuYuWechatV2'),
        (drf_spectacular_templates_path, r'drf_spectacular/templates'), # 使用动态路径
        # ('templates', 'templates') # 移除这一行，如果项目根目录没有 templates 文件夹
       ],
       hiddenimports=[
        'comtypes',
        'comtypes.client',
        'comtypes.stream',
        'django.contrib.staticfiles',
        'django.contrib.admin',
        'rest_framework.authentication',
        'rest_framework.permissions',
        'rest_framework.parsers',
        'rest_framework.negotiation',
        'rest_framework.metadata',
        'wechat_app',
        'wechat_app.apps',
        'altgraph',
        'docopt',
        'easydict',
        'future',
        'json5',
        'numpy',
        'pefile',
        'pqi',
        'pyperclip',
        'pypiwin32',
        'PyQt5',
        'PyQt5.Qt5',
        'PyQt5.sip',
        'PySide2',
        'pywin32',
        'pywin32_ctypes',
        'shiboken2',
        'uiautomation',
        'pyautogui',
        'win32clipboard',
    ],
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
    a.binaries,
    a.datas,
    [],
    name='YuYuWechatV2_Server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
