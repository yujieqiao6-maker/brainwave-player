# Brainwave Player

基于 PyQt6 的脑波控制视频播放器，通过串口读取设备的注意力和信号质量数据，控制本地视频播放与速度。当前版本为 **8.1.0**，主程序为 `brainwave_player_v8(1).py`。

## 功能

- 串口连接与调试：支持 9600、57600、115200 波特率，校验数据包并处理分片、粘包和断线。
- 注意力阈值控制播放，提供六种数据平滑算法；信号失效或断开后暂停视频。
- 注意力与播放速度实时曲线，以及 CSV 数据记录和原始包导出。
- 有界日志与预览缓存、限频刷新，减少长时间运行时的内存增长。

## 安装

可在仓库的 **Releases** 中下载安装包，均已内置 Python 和所需依赖：

| 平台 | 安装包 | 使用方式 |
| --- | --- | --- |
| M 系列 Mac，macOS 14+ | `BrainwavePlayer-8.1.0-macos-arm64.dmg` | 将应用拖入 Applications |
| Windows 10/11 x64 | `BrainwavePlayer-8.1.0-windows-x64-setup.exe` | 双击并按向导安装 |

macOS 包已验证启动和视频解码。Windows 安装器已编译并检查依赖，尚未在 Windows 系统上完成安装和设备连接实测。两个安装包均未做正式发布者签名，macOS 包使用本地 ad-hoc 签名且未公证，首次打开时系统可能提示确认来源。

## 从源码运行

使用 Python 3.14，首次运行先安装依赖。

macOS：

```sh
python3.14 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python 'brainwave_player_v8(1).py'
```

Windows PowerShell：

```powershell
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe 'brainwave_player_v8(1).py'
```

## 使用

1. 连接脑波设备，在「调试」页刷新并选择串口与波特率。Windows 端口通常为 `COM` 编号，macOS 通常为 `/dev/cu.*`。
2. 点击连接，确认设备持续提供有效注意力及信号质量数据。
3. 打开自己的本地视频，设置播放阈值和平滑算法。
4. 在「数据记录」页开始记录，或在「波形分析」页观察变化。

默认使用设备提供的标准注意力字段。旧版原始数据映射需要显式开启，仅供兼容旧设备。数据录制和导出默认保存到当前用户的「文稿 / Documents」下的 `BrainwavePlayer` 文件夹。

仓库不包含视频素材、设备采集记录、虚拟环境和构建缓存。

## 测试与打包

在 macOS 上运行离屏回归测试：

```sh
QT_QPA_PLATFORM=offscreen venv/bin/python -m unittest discover -s tests -v
```

当前 38 项自动化测试覆盖串口解析、线程退出、播放状态、录制错误、缓存边界及安装后的保存路径。测试不要求连接实体设备。

安装包构建命令和独立运行验证方式见 [打包说明](packaging/README.md)。自动化测试集中在 `tests/`。

## 文件结构

```text
brainwave_player_v8(1).py  主程序
requirements.txt         运行依赖入口
tests/                   自动化回归测试
packaging/               macOS 和 Windows 打包脚本
docs/                    稳定性设计和实施记录
```

第三方组件按其各自许可证提供，打包脚本保留随依赖分发的许可证文件。
