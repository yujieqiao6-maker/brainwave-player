"""Copy installed third-party license files into a distributable directory."""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys
import sysconfig

NAMES = ['PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'pyqtgraph', 'numpy', 'pyserial', 'colorama']

def collect(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    index = []
    for name in NAMES:
        dist = distribution(name)
        index.append(f'{name} {dist.version}')
        for file in dist.files or []:
            if any(part.lower() in ('licenses', 'license', 'copying') for part in file.parts) or file.name.lower().startswith(('license', 'copying', 'notice')):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = destination / name / str(file).replace('../', '')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    (destination / 'VERSIONS.txt').write_text('\n'.join(index) + '\n', encoding='utf-8')
    candidates = [Path(sys.base_prefix) / 'LICENSE.txt',
                  Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt']
    python_license = next((path for path in candidates if path.is_file()), None)
    if python_license is None:
        raise RuntimeError('Python license was not found in this Python installation')
    shutil.copy2(python_license, destination / 'PYTHON-LICENSE.txt')

if __name__ == '__main__':
    collect(sys.argv[1])
