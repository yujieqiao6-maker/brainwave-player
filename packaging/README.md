# 脑波控制视频播放器 8.1.0 打包

程序入口为 `brainwave_player_v8(1).py`，安装包已内置 Python、PyQt6、NumPy、串口及视频播放依赖。

## 安装包

| 文件 | 适用平台 | 安装方式 |
| --- | --- | --- |
| `BrainwavePlayer-8.1.0-macos-arm64.dmg` | M 系列 Mac，macOS 14 或以上 | 打开镜像，将应用拖入 Applications |
| `BrainwavePlayer-8.1.0-windows-x64-setup.exe` | Windows 10/11 x64 | 双击运行安装向导 |

macOS 使用本地 ad-hoc 签名，尚未进行 Apple Developer ID 签名和公证。Windows 安装器尚未做发布者签名。系统可能显示来源确认提示。

macOS 已验证应用签名完整性、镜像完整性，并从只读镜像启动及解码测试视频。Windows 已完成安装器编译、x64 文件和所需 DLL 依赖检查，尚未在 Windows 系统上执行安装、播放或设备连接实测。

默认录制和导出位置为用户的「文稿 / Documents」下的 `BrainwavePlayer` 目录。Windows 安装到当前用户的 `%LOCALAPPDATA%\Programs\BrainwavePlayer`，无需管理员权限；启动日志在 `%LOCALAPPDATA%\BrainwavePlayer\logs\startup.log`。卸载保留用户录制数据。

## 构建 macOS 安装包

在 Apple Silicon Mac 上使用原生 arm64 Python 3.14；本次构建使用 Python 3.14.5、macOS 14.4。运行目录为项目根目录：

```sh
python3.14 -m venv venv
venv/bin/python -m pip install -r packaging/build-requirements.txt
venv/bin/python packaging/macos/build_macos.py
```

输出在 `dist/`。应用先生成到 `build/macos-dist/BrainwavePlayer.app`，随后创建 DMG 并验证。构建需要系统的 `codesign`、`ditto` 和 `hdiutil`。

## 构建 Windows 安装包

Windows 程序使用官方 Python 3.14.7 x64 嵌入式运行时及固定版本的 Windows wheels，NSIS 负责生成安装向导。可在 macOS、Linux 或 Windows 上组装，但 Windows 运行验证必须在 Windows 系统进行。

安装 Python 3.14、pip 和 NSIS，并确保 `makensis` 位于 PATH。macOS 可使用：

```sh
brew install makensis
venv/bin/python packaging/windows/build_windows.py
```

Windows 可使用：

```powershell
py -3.14 -m venv venv
.\venv\Scripts\python.exe packaging\windows\build_windows.py
```

脚本校验官方 Python 压缩包 SHA-256，下载所需 wheels，保留较新的 Python VC 运行库，然后编译安装器。构建缓存位于 `build/windows-wheels/`；更改依赖版本前应清理此专用缓存。每次构建使用的新程序文件和 wheel 哈希记录在安装目录的 `build-manifest.json`。

## 验证

```sh
QT_QPA_PLATFORM=offscreen venv/bin/python -m unittest discover -s tests -v
```

应用提供不连接设备、不更改用户设置的自检入口。最后一个参数为可选的本地视频路径：

```sh
build/macos-dist/BrainwavePlayer.app/Contents/MacOS/BrainwavePlayer --smoke-test /tmp/brainwave-smoke.json /absolute/path/video.mp4
```

在 Windows 安装后的 PowerShell 中可运行：

```powershell
& "$env:LOCALAPPDATA\Programs\BrainwavePlayer\runtime\python.exe" "$env:LOCALAPPDATA\Programs\BrainwavePlayer\app\launcher.py" --smoke-test "$env:TEMP\brainwave-smoke.json" "C:\path\video.mp4"
```

提供视频时应检查输出 JSON 中 `frames_decoded` 大于 0。该入口检查导入、波形组件及解码；完整 GUI、安装/卸载和硬件串口连接仍需人工验证。

本次发布通过 38 项自动化测试。源码包包含程序入口、测试、打包脚本及设计文档，不含虚拟环境、设备记录、测试视频和构建缓存。第三方许可证随各运行包提供。
