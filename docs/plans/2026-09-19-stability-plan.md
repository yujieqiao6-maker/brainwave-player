# V8 Stability Implementation Plan

**Goal:** Improve stability and responsiveness of the user's V8 serial player.

**Architecture:** Preserve the single-file entry point and its existing tabs. Separate serial acquisition from bounded GUI refresh, retain full CSV output with bounded in-memory previews, and make signal validity explicit.

**Tech Stack:** Python 3.14, PyQt6, pyqtgraph, pyserial, NumPy, unittest.

## Tasks
1. Back up `brainwave_player_v8(1).py` outside the working folder. Document scope.
2. Correct BrainwaveThread framing/checksum/TLV parsing and interruption handling. Add tests in `tests/test_serial_stream.py`.
3. Update DataRecorder and GUI state/refresh logic in the existing entry point. Add tests in `tests/test_player_runtime.py`.
4. Run `QT_QPA_PLATFORM=offscreen ./venv/bin/python -B -m unittest discover -s tests -v`; fix failures, then review boundary cases.
5. Close the previous instance, launch the updated entry point with `./venv/bin/python 'brainwave_player_v8(1).py'`, inspect the UI, and report validation limits.

At the start of this work, the folder had no Git repository. An external local source backup was kept for recovery and is not distributed with the project.
