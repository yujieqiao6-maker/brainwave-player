from pathlib import Path
root = Path(SPECPATH).resolve().parents[1]
a = Analysis(
    [str(root / 'brainwave_player_v8(1).py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'build' / 'third_party_licenses'), 'third_party_licenses')],
    hiddenimports=['PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets', 'serial.tools.list_ports_osx'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6', 'PySide2', 'PyQt5', 'tkinter', 'matplotlib', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BrainwavePlayer',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, target_arch='arm64', codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BrainwavePlayer')
app = BUNDLE(coll, name='BrainwavePlayer.app', icon=None,
             bundle_identifier='com.brainwave.player',
             info_plist={
                 'CFBundleDisplayName': '脑波控制视频播放器',
                 'CFBundleShortVersionString': '8.1.0',
                 'CFBundleVersion': '8.1.0',
                 'LSMinimumSystemVersion': '14.0',
                 'NSHighResolutionCapable': True,
                 'NSBluetoothAlwaysUsageDescription': '连接脑波设备并接收串口数据。',
             })
