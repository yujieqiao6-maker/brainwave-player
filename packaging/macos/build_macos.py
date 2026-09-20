"""Build the Apple Silicon .app and drag-to-install DMG."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
VERSION = '8.1.0'

def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)

def main():
    if sys.platform != 'darwin':
        raise SystemExit('The macOS application must be built on macOS.')
    run(sys.executable, ROOT / 'packaging/collect_licenses.py', ROOT / 'build/third_party_licenses')
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', ROOT / 'build/macos-dist', '--workpath', ROOT / 'build/pyinstaller-macos',
        ROOT / 'packaging/macos/BrainwavePlayer.spec')
    app = ROOT / 'build/macos-dist/BrainwavePlayer.app'
    run('codesign', '--verify', '--deep', '--strict', app)
    stage = ROOT / 'build/dmg-stage'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    run('ditto', app, stage / app.name)
    os.symlink('/Applications', stage / 'Applications')
    (stage / '安装说明.txt').write_text(
        '脑波控制视频播放器 8.1.0\n\n'
        '适用于 M 系列芯片 Mac，macOS 14 或更高版本。\n'
        '将 BrainwavePlayer.app 拖入 Applications 文件夹，然后从应用程序中打开。\n'
        '已内置 Python、Qt 和视频解码依赖，不需要另行安装 Python。\n'
        '连接设备后，在“调试”页选择串口和波特率，再打开本地视频。\n'
        '录制文件默认保存到“文稿/BrainwavePlayer”。\n\n'
        '此包使用本地 ad-hoc 签名，未做 Apple 开发者身份签名及公证。\n'
        '在其他电脑上首次打开时，macOS 可能要求确认开发者来源。\n'
        '第三方许可证位于应用包 Contents/Resources/third_party_licenses。\n', encoding='utf-8')
    destination = ROOT / 'dist' / f'BrainwavePlayer-{VERSION}-macos-arm64.dmg'
    destination.parent.mkdir(exist_ok=True)
    run('hdiutil', 'create', '-volname', 'BrainwavePlayer', '-srcfolder', stage,
        '-ov', '-format', 'UDZO', destination)
    run('hdiutil', 'verify', destination)
    print(destination)

if __name__ == '__main__':
    main()
