"""Build a Windows x64 offline installer on macOS/Linux/Windows with NSIS."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / 'build'
CACHE = BUILD / 'windows-wheels'
STAGE = BUILD / 'windows-payload'
VERSION = '8.1.0'
PYTHON_URL = 'https://www.python.org/ftp/python/3.14.7/python-3.14.7-embed-amd64.zip'
PYTHON_SHA256 = 'd297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nsis_quote(value):
    return value.replace('$', '$$').replace('"', '$\\"')


def main():
    BUILD.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--dest', str(CACHE),
                    '--only-binary=:all:', '--platform', 'win_amd64', '--implementation', 'cp',
                    '--python-version', '314', '--abi', 'cp314', '-r', str(ROOT / 'packaging/requirements.txt')], check=True)
    runtime_zip = BUILD / 'python-3.14.7-embed-amd64.zip'
    if not runtime_zip.exists():
        urllib.request.urlretrieve(PYTHON_URL, runtime_zip)
    if digest(runtime_zip) != PYTHON_SHA256:
        raise RuntimeError('Official Python archive checksum mismatch')
    if STAGE.exists():
        shutil.rmtree(STAGE)
    runtime = STAGE / 'runtime'
    site = runtime / 'Lib/site-packages'
    site.mkdir(parents=True)
    with zipfile.ZipFile(runtime_zip) as archive:
        archive.extractall(runtime)
    wheel_manifest = []
    for wheel in sorted(CACHE.glob('*.whl')):
        if not (wheel.name.endswith('-win_amd64.whl') or wheel.name.endswith('-any.whl')):
            raise RuntimeError(f'Wrong platform wheel: {wheel.name}')
        with zipfile.ZipFile(wheel) as archive:
            # These pinned wheels have no .data relocation paths.
            if any('.data/' in name for name in archive.namelist()):
                raise RuntimeError(f'Wheel needs relocation support: {wheel.name}')
            archive.extractall(site)
        wheel_manifest.append({'file': wheel.name, 'sha256': digest(wheel)})
    (runtime / 'python314._pth').write_text('python314.zip\n.\nLib/site-packages\n../app\nimport site\n', encoding='utf-8')
    app = STAGE / 'app'
    app.mkdir()
    shutil.copy2(ROOT / 'brainwave_player_v8(1).py', app / 'main.py')
    shutil.copy2(ROOT / 'packaging/windows/launcher.py', app / 'launcher.py')
    # Python ships newer VC runtime DLLs. Preserve them, and use the same
    # versions in Qt's DLL directory so load order cannot select an older copy.
    qt_bin = site / 'PyQt6/Qt6/bin'
    for pattern in ('vcruntime*.dll', 'msvcp*.dll', 'concrt*.dll'):
        for dll in qt_bin.glob(pattern):
            root_dll = runtime / dll.name
            if root_dll.exists():
                shutil.copy2(root_dll, dll)
            else:
                shutil.copy2(dll, root_dll)
    for required in ('python.exe', 'pythonw.exe', 'python314.dll', 'msvcp140.dll', 'vcruntime140_1.dll'):
        if not (runtime / required).is_file():
            raise RuntimeError(f'Missing runtime file: {required}')
    for relative in ('platforms/qwindows.dll', 'multimedia/ffmpegmediaplugin.dll'):
        if not (site / 'PyQt6/Qt6/plugins' / relative).is_file():
            raise RuntimeError(f'Missing Qt plugin: {relative}')
    notices = ('BrainwavePlayer 8.1.0\nWindows 10/11 x64\n\n'
               'Python and dependencies are included; a separate Python install is not needed.\n'
               'Use the Start menu shortcut after installation.\n'
               'Recordings are stored in Documents/BrainwavePlayer by default.\n'
               'Third-party license files are included with each package under runtime/Lib/site-packages.\n'
               'This unsigned installer was assembled on macOS; Windows execution has not yet been verified.\n')
    (STAGE / 'README.txt').write_text(notices, encoding='utf-8')
    manifest = {'version': VERSION, 'python_url': PYTHON_URL, 'python_sha256': PYTHON_SHA256,
                'wheels': wheel_manifest, 'source_sha256': digest(app / 'main.py')}
    (STAGE / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    # Delete only files installed by this build; never recurse over an arbitrary installation folder.
    files = sorted(path.relative_to(STAGE) for path in STAGE.rglob('*') if path.is_file())
    directories = sorted((path.relative_to(STAGE) for path in STAGE.rglob('*') if path.is_dir()), key=lambda p: len(p.parts), reverse=True)
    lines = [f'  Delete "$INSTDIR\\{nsis_quote(str(path).replace(chr(47), chr(92)))}"' for path in files]
    lines += [f'  RMDir "$INSTDIR\\{nsis_quote(str(path).replace(chr(47), chr(92)))}"' for path in directories]
    removal = BUILD / 'windows-uninstall-manifest.nsh'
    removal.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    output = ROOT / 'dist' / f'BrainwavePlayer-{VERSION}-windows-x64-setup.exe'
    output.parent.mkdir(exist_ok=True)
    compiler = shutil.which('makensis')
    if compiler is None:
        raise RuntimeError('Install NSIS (macOS: brew install makensis), then rerun.')
    define = '/D' if sys.platform == 'win32' else '-D'
    compiler_env = os.environ.copy()
    if sys.platform == 'darwin':
        compiler_env['LC_ALL'] = 'en_US.UTF-8'
    subprocess.run([compiler, f'{define}PAYLOAD={STAGE}', f'{define}OUTPUT={output}',
                    f'{define}REMOVE_MANIFEST={removal}', str(ROOT / 'packaging/windows/installer.nsi')],
                   check=True, env=compiler_env)
    print(output)
    print('SHA256:', digest(output))

if __name__ == '__main__':
    main()
