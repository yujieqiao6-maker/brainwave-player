"""Launch the private Windows runtime without relying on global Python or PATH."""
import os
from pathlib import Path
import runpy
import sys
import traceback

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parents[1]
QT = BASE / 'runtime' / 'Lib' / 'site-packages' / 'PyQt6' / 'Qt6'
_DLL_HANDLES = [os.add_dll_directory(str(QT / 'bin'))] if hasattr(os, 'add_dll_directory') else []
os.environ['QT_PLUGIN_PATH'] = str(QT / 'plugins')
LOG_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'BrainwavePlayer' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
if sys.stderr is None or sys.stdout is None:
    log = (LOG_DIR / 'startup.log').open('a', encoding='utf-8', buffering=1)
    sys.stderr = log
    sys.stdout = log
try:
    runpy.run_path(str(BASE / 'app' / 'main.py'), run_name='__main__')
except Exception:
    traceback.print_exc()
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, f'程序启动失败，请查看日志：\n{LOG_DIR / "startup.log"}', '脑波控制视频播放器', 0x10)
    raise SystemExit(1)
