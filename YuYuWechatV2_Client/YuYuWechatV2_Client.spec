# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('client_app', 'client_app'),
        ('YuYuWechatV2_Client', 'YuYuWechatV2_Client'),
        ('db.sqlite3', '.'),
       ],
       hiddenimports=[
       'celery',
       'celery.schedules',
       'celery.result',
       'requests',
       'croniter',
       'kombu',
       'amqp',
       'billiard',
       'pytz',
       'celery.fixups',
       'celery.fixups.django',
       'celery.loaders.app',
       'redis',
       'sqlalchemy',
       'cryptography',
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
    name='YuYuWechatV2_Client',
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
