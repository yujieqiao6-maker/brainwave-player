#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
脑波控制视频播放器 V8 - 最终稳定版
串口校验、断线保护、有界缓存与限频界面刷新。
直接运行本文件，使用串口连接设备。
"""

import sys
import time
import csv
from pathlib import Path
from threading import Event, Lock
import serial
import serial.tools.list_ports
from collections import deque
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QSlider, QGroupBox,
    QComboBox, QMessageBox, QProgressBar, QTabWidget, QTextEdit,
    QLineEdit, QCheckBox
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QSettings, QUrl, QStandardPaths
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
import pyqtgraph as pg


APP_VERSION = "8.1.0"


def recording_path(filename):
    """相对文件名写入用户文档目录，避免安装目录不可写。"""
    path = Path(filename).expanduser()
    if path.is_absolute():
        return path
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    directory = (Path(documents) if documents else Path.home() / 'Documents') / 'BrainwavePlayer'
    return directory / path


class SmootherBase:
    """平滑器基类"""
    def update(self, value):
        raise NotImplementedError
    def reset(self):
        pass


class ExponentialSmoother(SmootherBase):
    """指数移动平均 (EMA) - 对最近的数据给予更高权重"""
    
    def __init__(self, alpha=0.3):
        self.alpha = alpha  # 平滑系数，0-1之间，越小越平滑
        self.smoothed = None
    
    def update(self, value):
        if self.smoothed is None:
            self.smoothed = value
        else:
            self.smoothed = self.alpha * value + (1 - self.alpha) * self.smoothed
        return self.smoothed
    
    def reset(self):
        self.smoothed = None


class SimpleMovingAverageSmoother(SmootherBase):
    """简单移动平均 (SMA) - 对窗口内数据求平均"""
    
    def __init__(self, window_size=5):
        self.window_size = window_size
        self.values = []
    
    def update(self, value):
        self.values.append(value)
        if len(self.values) > self.window_size:
            self.values.pop(0)
        return sum(self.values) / len(self.values)
    
    def reset(self):
        self.values = []


class WeightedMovingAverageSmoother(SmootherBase):
    """加权移动平均 (WMA) - 越近的数据权重越大"""
    
    def __init__(self, window_size=5):
        self.window_size = window_size
        self.values = []
    
    def update(self, value):
        self.values.append(value)
        if len(self.values) > self.window_size:
            self.values.pop(0)
        
        n = len(self.values)
        # 权重: 1, 2, 3, ..., n
        weights = list(range(1, n + 1))
        total_weight = sum(weights)
        return sum(w * v for w, v in zip(weights, self.values)) / total_weight
    
    def reset(self):
        self.values = []


class MedianFilterSmoother(SmootherBase):
    """中值滤波 - 对异常值有很好的抑制作用"""
    
    def __init__(self, window_size=5):
        self.window_size = window_size if window_size % 2 == 1 else window_size + 1  # 确保是奇数
        self.values = []
    
    def update(self, value):
        self.values.append(value)
        if len(self.values) > self.window_size:
            self.values.pop(0)
        sorted_vals = sorted(self.values)
        n = len(sorted_vals)
        return sorted_vals[n // 2]  # 返回中值
    
    def reset(self):
        self.values = []


class KalmanFilterSmoother(SmootherBase):
    """卡尔曼滤波器 - 最优估计"""
    
    def __init__(self, process_variance=0.1, measurement_variance=1.0):
        self.process_variance = process_variance  # 过程噪声
        self.measurement_variance = measurement_variance  # 测量噪声
        self.estimate = None
        self.error_covariance = None
    
    def update(self, measurement):
        if self.estimate is None:
            self.estimate = measurement
            self.error_covariance = self.measurement_variance
        else:
            # 预测步骤
            predicted_estimate = self.estimate
            predicted_error_covariance = self.error_covariance + self.process_variance
            
            # 更新步骤
            kalman_gain = predicted_error_covariance / (predicted_error_covariance + self.measurement_variance)
            self.estimate = predicted_estimate + kalman_gain * (measurement - predicted_estimate)
            self.error_covariance = (1 - kalman_gain) * predicted_error_covariance
        
        return self.estimate
    
    def reset(self):
        self.estimate = None
        self.error_covariance = None


class DoubleExponentialSmoother(SmootherBase):
    """双指数平滑 - 适合有趋势的数据"""
    
    def __init__(self, alpha=0.3, beta=0.1):
        self.alpha = alpha
        self.beta = beta
        self.level = None
        self.trend = None
    
    def update(self, value):
        if self.level is None:
            self.level = value
            self.trend = 0
        else:
            last_level = self.level
            self.level = self.alpha * value + (1 - self.alpha) * (self.level + self.trend)
            self.trend = self.beta * (self.level - last_level) + (1 - self.beta) * self.trend
        return self.level
    
    def reset(self):
        self.level = None
        self.trend = None


# 平滑算法注册表
SMOOTHERS = {
    '指数移动平均 (EMA)': ExponentialSmoother,
    '简单移动平均 (SMA)': SimpleMovingAverageSmoother,
    '加权移动平均 (WMA)': WeightedMovingAverageSmoother,
    '中值滤波': MedianFilterSmoother,
    '卡尔曼滤波': KalmanFilterSmoother,
    '双指数平滑': DoubleExponentialSmoother,
}


class SpeedSmoother:
    """速度平滑器 - 使用指数移动平均"""
    
    def __init__(self, alpha=0.3):
        self.alpha = alpha  # 平滑系数，越小越平滑
        self.smoothed = None
    
    def update(self, target):
        """更新平滑值"""
        if self.smoothed is None:
            self.smoothed = target
        else:
            self.smoothed = self.alpha * target + (1 - self.alpha) * self.smoothed
        return self.smoothed
    
    def reset(self):
        """重置平滑器"""
        self.smoothed = None


class AudioFeedback:
    """音频反馈 - 使用系统蜂鸣声"""
    
    def __init__(self):
        self.enabled = False  # 默认禁用
    
    def set_enabled(self, enabled):
        """启用/禁用音频反馈"""
        self.enabled = enabled
    
    def play_threshold_reached(self):
        """播放达到阈值的提示音"""
        if self.enabled:
            QApplication.beep()
    
    def play_low_attention(self):
        """播放注意力低的提示音"""
        if self.enabled:
            QApplication.beep()


class WaveformWidget(QWidget):
    """实时波形图显示 - 双Y轴"""
    
    def __init__(self, title="注意力与速度实时波形"):
        super().__init__()
        self.layout = QVBoxLayout(self)
        
        # 创建图表
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        self.plot_widget.setTitle(title, color='k', size='12pt')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        
        # 添加图例
        self.plot_widget.addLegend()
        
        # 左侧Y轴（注意力）
        self.plot_widget.setLabel('left', '注意力', color='g', units='')
        self.plot_widget.setYRange(0, 100)
        
        # 右侧Y轴（速度）
        self.plot_widget.showAxis('right')
        right_axis = self.plot_widget.getAxis('right')
        right_axis.setLabel('速度', color='b', units='x')
        # 设置右侧轴的颜色（使用 setTextPen 方法）
        right_axis.setTextPen(pg.mkPen('b', width=2))
        right_axis.setTickPen(pg.mkPen('b', width=1))
        right_axis.setStyle(showValues=True, autoExpandTextSpace=True)
        
        # 阈值线（在左轴）
        self.threshold_line = pg.InfiniteLine(
            pos=60, 
            angle=0, 
            pen=pg.mkPen('r', width=2, style=Qt.PenStyle.DashLine),
            label='阈值'
        )
        self.plot_widget.addItem(self.threshold_line)
        
        # 注意力曲线（左轴）
        self.attention_curve = self.plot_widget.plot(
            pen=pg.mkPen('g', width=2),
            name='注意力'
        )
        
        # 速度曲线（右轴）
        self.speed_view = pg.ViewBox()
        self.plot_widget.scene().addItem(self.speed_view)
        right_axis.linkToView(self.speed_view)
        self.speed_view.setXLink(self.plot_widget.getViewBox())
        self.speed_view.setYRange(0, 2.2, padding=0)
        self.curve = pg.PlotCurveItem(pen=pg.mkPen('b', width=2))
        self.speed_view.addItem(self.curve)
        self.plot_widget.plotItem.legend.addItem(self.curve, '速度')
        self.plot_widget.getViewBox().sigResized.connect(self.sync_speed_axis)
        self.sync_speed_axis()
        self.dirty = False
        
        # 数据
        self.speed_data = deque(maxlen=200)
        self.attention_data = deque(maxlen=200)
        
        self.layout.addWidget(self.plot_widget)
    
    def sync_speed_axis(self):
        main_view = self.plot_widget.getViewBox()
        self.speed_view.setGeometry(main_view.sceneBoundingRect())
        self.speed_view.linkedViewChanged(main_view, self.speed_view.XAxis)

    def update_data(self, speed, attention, threshold):
        self.speed_data.append(speed)
        self.attention_data.append(attention)
        self.threshold_line.setPos(threshold)
        self.dirty = True

    def render_pending(self):
        if self.dirty and self.isVisible():
            self.curve.setData(list(self.speed_data))
            self.attention_curve.setData(list(self.attention_data))
            self.dirty = False


class BrainwaveThread(QThread):
    """增量读取 ThinkGear 数据；旧版 raw 数值映射仅在显式兼容模式启用。"""

    data_updated = pyqtSignal(dict)
    debug_message = pyqtSignal(str)
    connection_changed = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    EMIT_INTERVAL = 0.05
    ATTENTION_TIMEOUT = 3.0
    MAX_PAYLOAD = 169
    MAX_RAW_PACKETS = 2000

    def __init__(self, port=None, baudrate=9600, legacy_raw_mapping=False):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.legacy_raw_mapping = bool(legacy_raw_mapping)
        self.serial_conn = None
        self.running = False
        self.paused = False
        self._stop_requested = Event()
        self._serial_lock = Lock()
        self._buffer = bytearray()
        self.attention = 0
        self.meditation = 0
        self.poor_signal = 200
        self.device_worn = False
        self.legacy_mapped = False
        self._standard_attention_seen = False
        self._poor_signal_seen = False
        self.attention_history = deque(maxlen=100)
        self.debug_enabled = False
        self.raw_packets = deque(maxlen=self.MAX_RAW_PACKETS)
        self._raw_lock = Lock()
        self._pending_data = None
        self._last_emit_at = float('-inf')
        self._last_debug_at = float('-inf')
        self._last_valid_attention_at = None
        self._no_attention_warning = False
        self.total_packets = 0
        self.attention_packets = 0
        self.poor_signal_packets = 0
        self.sichiray_packets = 0
        self.invalid_packets = 0

    def set_debug_mode(self, enabled):
        with self._raw_lock:
            self.debug_enabled = bool(enabled)
            if not enabled:
                self.raw_packets.clear()

    def raw_packets_snapshot(self):
        """GUI 导出使用快照，避免遍历时读取线程修改 deque。"""
        with self._raw_lock:
            return list(self.raw_packets)

    def clear_raw_packets(self):
        with self._raw_lock:
            self.raw_packets.clear()

    def _debug_packet(self, packet):
        if not self.debug_enabled:
            return
        hex_string = packet.hex(' ').upper()
        with self._raw_lock:
            if not self.debug_enabled:
                return
            self.raw_packets.append(hex_string)
        now = time.monotonic()
        if now - self._last_debug_at >= 0.5:
            self._last_debug_at = now
            self.debug_message.emit(
                f"[ThinkGear] {hex_string} -> 注意力: {self.attention}, "
                f"PoorSignal: {self.poor_signal}（日志最多每秒 2 条）"
            )

    def _queue_data(self, immediate=False, now=None):
        self._pending_data = {
            'attention': self.attention,
            'meditation': self.meditation,
            'poor_signal': self.poor_signal,
            'device_worn': self.device_worn,
            'attention_valid': self.device_worn,
            'legacy_mapped': self.legacy_mapped,
        }
        self.flush_data(force=immediate, now=now)

    def flush_data(self, force=False, now=None):
        """合并高频更新；信号刚失效时允许立即发送停止控制的状态。"""
        now = time.monotonic() if now is None else now
        if self._pending_data is not None and (
            force or now - self._last_emit_at >= self.EMIT_INTERVAL
        ):
            data, self._pending_data = self._pending_data, None
            self._last_emit_at = now
            self.data_updated.emit(data)

    def _invalidate_attention(self):
        self.attention = 0
        self.device_worn = False
        self.legacy_mapped = False
        self._last_valid_attention_at = None
        self.attention_history.clear()

    def check_timeout(self, now=None):
        """只有新的有效注意力能续期，raw/冥想/信号质量包不能续期。"""
        now = time.monotonic() if now is None else now
        if (self._last_valid_attention_at is not None
                and now - self._last_valid_attention_at >= self.ATTENTION_TIMEOUT):
            self._invalidate_attention()
            self._queue_data(immediate=True, now=now)

    def feed_data(self, data):
        """接收任意拆包/粘包字节流；损坏包逐字节重同步。"""
        self._buffer.extend(data)
        while True:
            start = self._buffer.find(b'\xaa\xaa')
            if start < 0:
                # 保留可能属于下一次同步头的末尾 AA。
                if self._buffer[-1:] == b'\xaa':
                    self._buffer[:] = b'\xaa'
                else:
                    self._buffer.clear()
                return
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 3:
                return
            length = self._buffer[2]
            if length > self.MAX_PAYLOAD:
                self.invalid_packets += 1
                del self._buffer[0]
                continue
            packet_size = length + 4
            if len(self._buffer) < packet_size:
                return
            packet = bytes(self._buffer[:packet_size])
            if ((sum(packet[3:-1]) + packet[-1]) & 0xFF) != 0xFF:
                self.invalid_packets += 1
                del self._buffer[0]
                continue
            del self._buffer[:packet_size]
            self.parse_thinkgear_packet(packet)

    def parse_thinkgear_packet(self, packet):
        """先完整验证校验和及每个数据行，再一次性提交状态。"""
        if (len(packet) < 4 or packet[:2] != b'\xaa\xaa'
                or packet[2] > self.MAX_PAYLOAD
                or len(packet) != packet[2] + 4
                or ((sum(packet[3:-1]) + packet[-1]) & 0xFF) != 0xFF):
            self.invalid_packets += 1
            return False
        payload = packet[3:-1]
        fields = {}
        raw_value = None
        i = 0
        while i < len(payload):
            extended = False
            while i < len(payload) and payload[i] == 0x55:
                extended = True
                i += 1
            if i >= len(payload):
                self.invalid_packets += 1
                return False
            code = payload[i]
            i += 1
            if code >= 0x80:
                if i >= len(payload):
                    self.invalid_packets += 1
                    return False
                size = payload[i]
                i += 1
            else:
                size = 1
            if i + size > len(payload):
                self.invalid_packets += 1
                return False
            value = payload[i:i + size]
            i += size
            if extended:
                continue
            if code in (0x02, 0x04, 0x05):
                if code in (0x04, 0x05) and value[0] > 100:
                    self.invalid_packets += 1
                    return False
                fields[code] = value[0]
            elif code == 0x80:
                if size != 2:
                    self.invalid_packets += 1
                    return False
                raw_value = int.from_bytes(value, 'big', signed=False)

        self.total_packets += 1
        was_valid = self.device_worn
        now = time.monotonic()
        if 0x05 in fields:
            self.meditation = fields[0x05]
        if 0x02 in fields:
            self._poor_signal_seen = True
            self.poor_signal = fields[0x02]
            self.poor_signal_packets += 1
        if 0x04 in fields:
            self._standard_attention_seen = True
            self.legacy_mapped = False
            self.attention_packets += 1
            if self.poor_signal < 50 and fields[0x04] > 0:
                self.attention = fields[0x04]
                self.device_worn = True
                self._last_valid_attention_at = now
                self.attention_history.append(self.attention)
            else:
                self._invalidate_attention()
        elif 0x02 in fields and self.poor_signal >= 50:
            self._invalidate_attention()

        relevant = 0x02 in fields or 0x04 in fields
        if (self.legacy_raw_mapping and not self._standard_attention_seen
                and raw_value is not None and not relevant):
            # 保留旧版启发式仅作兼容；该值不是 ThinkGear eSense 注意力。
            attention = min(100, max(0, int(raw_value * 100 / 65535)))
            self.legacy_mapped = True
            self.attention_history.append(attention)
            self.sichiray_packets += 1
            self.attention_packets += 1
            values = self.attention_history
            if len(values) >= 10:
                mean = sum(values) / len(values)
                variance = sum((value - mean) ** 2 for value in values) / len(values)
                self.device_worn = (
                    variance > 9 and max(values) - min(values) > 10
                    and len(set(values)) > 5
                    and (not self._poor_signal_seen or self.poor_signal < 50)
                )
            else:
                self.device_worn = False
            self.attention = attention if self.device_worn else 0
            self._last_valid_attention_at = now if self.device_worn else None
            relevant = True

        self._debug_packet(packet)
        if relevant:
            self._queue_data(immediate=was_valid and not self.device_worn, now=now)
        if (not self._no_attention_warning and self.total_packets >= 50
                and self.attention_packets == 0):
            self._no_attention_warning = True
            self.debug_message.emit(
                "已收到 50 个有效包，但没有 eSense 注意力数据；请检查设备佩戴和输出配置。"
            )
        return True

    def parse_sichiray_packet(self, packet):
        """保留调用接口；映射仍受 legacy_raw_mapping 显式开关控制。"""
        return self.parse_thinkgear_packet(packet)

    def run(self):
        connection = None
        try:
            if self._stop_requested.is_set() or self.isInterruptionRequested():
                return
            connection = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=0.1)
            with self._serial_lock:
                self.serial_conn = connection
            if self._stop_requested.is_set() or self.isInterruptionRequested():
                return
            self.running = True
            self.connection_changed.emit(True)
            self.debug_message.emit(f"✓ 已连接到 {self.port}，波特率: {self.baudrate}")
            while not self._stop_requested.is_set() and not self.isInterruptionRequested():
                data = connection.read(min(max(connection.in_waiting, 1), 4096))
                if self._stop_requested.is_set() or self.isInterruptionRequested():
                    break
                if data and not self.paused:
                    self.feed_data(data)
                self.check_timeout()
                self.flush_data()
        except (serial.SerialException, OSError) as exc:
            if not self._stop_requested.is_set() and not self.isInterruptionRequested():
                self.error_occurred.emit(f"串口连接或读取失败: {exc}")
        except Exception as exc:
            if not self._stop_requested.is_set() and not self.isInterruptionRequested():
                self.error_occurred.emit(f"脑波读取失败: {exc}")
        finally:
            self.running = False
            # 先撤销可取消读取的引用，避免 GUI cancel_read 与 close 同时使用文件描述符。
            with self._serial_lock:
                self.serial_conn = None
            if connection is not None:
                try:
                    connection.close()
                except (serial.SerialException, OSError):
                    pass
            self._invalidate_attention()
            self._queue_data(immediate=True)
            self.connection_changed.emit(False)
            self.debug_message.emit("✗ 已断开连接")

    def stop(self):
        """GUI 仅请求停止/取消阻塞读取；串口由工作线程 finally 关闭。"""
        self.running = False
        self.requestInterruption()
        with self._serial_lock:
            if self._stop_requested.is_set():
                return
            self._stop_requested.set()
            connection = self.serial_conn
            if connection is not None:
                try:
                    cancel_read = getattr(connection, 'cancel_read', None)
                    if cancel_read is not None:
                        cancel_read()
                except (serial.SerialException, OSError):
                    pass

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

class DataRecorder:
    """完整数据写入 CSV，内存仅保留最近的预览记录。"""

    def __init__(self):
        self.recording = False
        self.filename = None
        self.file = None
        self.writer = None
        self.data = deque(maxlen=200)
        self.record_count = 0
        self.worn_count = 0
        self.attention_sum = 0.0
        self.last_flush = time.monotonic()

    def start(self, filename):
        if self.recording:
            raise RuntimeError("请先停止当前记录")
        output = open(filename, 'w', newline='', encoding='utf-8')
        try:
            writer = csv.writer(output)
            writer.writerow(['时间戳', '注意力', 'Poor Signal', '佩戴状态', '播放速度'])
            output.flush()
        except Exception:
            output.close()
            raise
        self.filename = str(Path(filename).resolve())
        self.file = output
        self.writer = writer
        self.data.clear()
        self.record_count = 0
        self.worn_count = 0
        self.attention_sum = 0.0
        self.last_flush = time.monotonic()
        self.recording = True
        return True

    def record(self, attention, poor_signal, worn, speed):
        if not self.recording:
            return False
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        row = [timestamp, attention, poor_signal, bool(worn), speed]
        self.writer.writerow(row)
        self.data.append(row)
        self.record_count += 1
        self.worn_count += bool(worn)
        self.attention_sum += float(attention)
        self.flush()
        return True

    def flush(self):
        now = time.monotonic()
        if self.file is not None and now - self.last_flush >= 1.0:
            self.file.flush()
            self.last_flush = now

    def stop(self):
        self.recording = False
        output, self.file = self.file, None
        self.writer = None
        if output is not None:
            output.close()

    def get_statistics(self):
        count = self.record_count
        return {
            'count': count,
            'avg_attention': self.attention_sum / count if count else 0.0,
            'worn_ratio': self.worn_count * 100 / count if count else 0.0,
        }

    def get_preview(self, lines=10):
        return list(self.data)[-lines:] if lines > 0 else []


class BrainwaveVideoPlayer(QMainWindow):
    """脑波控制视频播放器 V8 - 最终稳定版"""
    
    def __init__(self):
        super().__init__()
        self.attention_threshold = 60
        self.current_attention = 0
        self.smoothed_attention = 0
        self.current_poor_signal = 200
        self.target_speed = 1.0
        self.current_speed = 1.0
        self.is_playing = False
        self.video_loaded = False
        self.manual_paused = False
        self.device_worn = False
        self._closing = False
        self._disconnecting = False
        self._feedback_state = None
        self._signal_color = None
        self._pending_logs = deque(maxlen=150)
        
        # 注意力平滑器（默认使用指数移动平均）
        self.attention_smoother = ExponentialSmoother(alpha=0.3)
        self.current_smoother_name = '指数移动平均 (EMA)'
        
        # 速度平滑器
        self.speed_smoother = SpeedSmoother(alpha=0.3)
        
        # 音频反馈
        self.audio_feedback = AudioFeedback()
        
        # 脑波线程
        self.brainwave_thread = None
        
        # 数据记录器
        self.recorder = DataRecorder()
        
        # 定时器
        self.speed_adjust_timer = QTimer()
        self.speed_adjust_timer.timeout.connect(self.adjust_playback_speed)
        self.speed_adjust_timer.setInterval(200)
        
        self.init_ui()
        self.init_player()
        self.refresh_ports(show_warning=False)
        self.load_settings()
        self.suggest_filename()
        self.waveform_timer = QTimer(self)
        self.waveform_timer.timeout.connect(self.waveform_widget.render_pending)
        self.waveform_timer.start(100)
        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.timeout.connect(self.refresh_background_views)
        self.maintenance_timer.start(500)
        
    def init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("脑波控制视频播放器 V8 - 最终稳定版")
        self.setMinimumSize(1000, 720)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # 创建 Tab Widget
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)
        
        # Tab 1: 主界面
        self.create_main_tab()
        
        # Tab 2: 调试
        self.create_debug_tab()
        
        # Tab 3: 数据记录
        self.create_recording_tab()
        
        # Tab 4: 波形分析
        self.create_waveform_tab()
    
    def create_main_tab(self):
        """创建主界面标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 视频播放区域
        video_group = QGroupBox("📺 视频播放区域")
        video_layout = QVBoxLayout()
        
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(240)
        video_layout.addWidget(self.video_widget)
        
        file_layout = QHBoxLayout()
        self.open_btn = QPushButton("📂 打开视频文件")
        self.open_btn.clicked.connect(self.open_video_file)
        file_layout.addWidget(self.open_btn)
        file_layout.addStretch()
        
        self.video_label = QLabel("未选择视频")
        file_layout.addWidget(self.video_label)
        video_layout.addLayout(file_layout)
        video_group.setLayout(video_layout)
        layout.addWidget(video_group, 2)
        
        # 信号质量显示
        signal_group = QGroupBox("📶 信号质量")
        signal_layout = QHBoxLayout()
        
        self.signal_label = QLabel("信号: --")
        self.signal_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        signal_layout.addWidget(self.signal_label)
        
        self.worn_label = QLabel("佩戴状态: --")
        self.worn_label.setStyleSheet("font-size: 14px;")
        signal_layout.addWidget(self.worn_label)
        
        signal_layout.addStretch()
        signal_group.setLayout(signal_layout)
        layout.addWidget(signal_group)
        
        # 注意力控制区域
        attention_group = QGroupBox("🧠 注意力控制")
        attention_layout = QVBoxLayout()
        
        info_layout = QHBoxLayout()
        self.attention_label = QLabel(f"当前注意力: {self.current_attention}")
        self.attention_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        info_layout.addWidget(self.attention_label)
        
        self.speed_label = QLabel("播放速度: 1.0x")
        info_layout.addWidget(self.speed_label)
        info_layout.addStretch()
        
        # 平滑算法选择
        smoother_layout = QHBoxLayout()
        smoother_layout.addWidget(QLabel("平滑算法:"))
        self.smoother_combo = QComboBox()
        self.smoother_combo.addItems(SMOOTHERS.keys())
        self.smoother_combo.setCurrentText(self.current_smoother_name)
        self.smoother_combo.currentTextChanged.connect(self.change_smoother)
        smoother_layout.addWidget(self.smoother_combo)
        
        self.smoother_params_label = QLabel("(α=0.3)")
        smoother_layout.addWidget(self.smoother_params_label)
        smoother_layout.addStretch()
        attention_layout.addLayout(smoother_layout)
        
        self.threshold_label = QLabel(f"播放阈值: {self.attention_threshold}")
        attention_layout.addLayout(info_layout)
        
        self.attention_bar = QProgressBar()
        self.attention_bar.setRange(0, 100)
        self.attention_bar.setValue(0)
        attention_layout.addWidget(self.attention_bar)
        
        self.status_label = QLabel("状态: 等待选择视频...")
        self.status_label.setStyleSheet("padding: 10px; background-color: #f5f5f5; border-radius: 5px;")
        attention_layout.addWidget(self.status_label)
        
        threshold_layout = QHBoxLayout()
        threshold_layout.addWidget(self.threshold_label)
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(0, 100)
        self.threshold_slider.setValue(self.attention_threshold)
        self.threshold_slider.valueChanged.connect(self.update_threshold)
        threshold_layout.addWidget(self.threshold_slider)
        attention_layout.addLayout(threshold_layout)
        
        self.play_btn = QPushButton("▶️ 播放")
        self.play_btn.clicked.connect(self.toggle_playback)
        self.play_btn.setEnabled(False)
        attention_layout.addWidget(self.play_btn)
        
        attention_group.setLayout(attention_layout)
        layout.addWidget(attention_group, 1)
        
        self.tab_widget.addTab(tab, "📺 主界面")
    
    def create_debug_tab(self):
        """创建调试标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 连接控制
        connection_group = QGroupBox("🔌 设备连接")
        connection_layout = QVBoxLayout()
        
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("串口:"))
        self.port_combo = QComboBox()
        port_layout.addWidget(self.port_combo)
        
        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.clicked.connect(lambda: self.refresh_ports(show_warning=True))
        port_layout.addWidget(self.refresh_btn)
        connection_layout.addLayout(port_layout)
        
        baud_layout = QHBoxLayout()
        baud_layout.addWidget(QLabel("波特率:"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(['9600', '57600', '115200'])
        self.baud_combo.setCurrentText('9600')
        baud_layout.addWidget(self.baud_combo)
        connection_layout.addLayout(baud_layout)
        self.legacy_raw_checkbox = QCheckBox("旧版原始值映射（兼容模式，非设备注意力值）")
        self.legacy_raw_checkbox.setToolTip("仅用于复现旧程序的原始数据映射；标准设备请保持关闭。")
        connection_layout.addWidget(self.legacy_raw_checkbox)
        
        self.connect_btn = QPushButton("🔗 连接设备")
        self.connect_btn.clicked.connect(self.connect_device)
        connection_layout.addWidget(self.connect_btn)
        
        connection_group.setLayout(connection_layout)
        layout.addWidget(connection_group)
        
        # 调试选项
        debug_options_group = QGroupBox("⚙️  调试选项")
        debug_options_layout = QHBoxLayout()
        
        self.debug_checkbox = QCheckBox("启用调试模式")
        self.debug_checkbox.toggled.connect(self.toggle_debug_mode)
        debug_options_layout.addWidget(self.debug_checkbox)
        
        self.export_raw_btn = QPushButton("📥 导出原始数据")
        self.export_raw_btn.clicked.connect(self.export_raw_data)
        self.export_raw_btn.setEnabled(False)
        debug_options_layout.addWidget(self.export_raw_btn)
        
        debug_options_layout.addStretch()
        debug_options_group.setLayout(debug_options_layout)
        layout.addWidget(debug_options_group)
        
        # 统计信息
        stats_group = QGroupBox("📊 统计信息")
        stats_layout = QVBoxLayout()
        self.stats_label = QLabel("未连接")
        stats_layout.addWidget(self.stats_label)
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)
        
        # 调试日志
        log_group = QGroupBox("📝 调试日志")
        log_layout = QVBoxLayout()
        self.debug_log = QTextEdit()
        self.debug_log.setReadOnly(True)
        self.debug_log.document().setMaximumBlockCount(1000)
        self.debug_log.setMaximumHeight(200)
        log_layout.addWidget(self.debug_log)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group, 1)
        
        self.tab_widget.addTab(tab, "🔧 调试")
    
    def create_recording_tab(self):
        """创建数据记录标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 记录控制
        record_control_group = QGroupBox("🎛️  记录控制")
        record_control_layout = QVBoxLayout()
        
        filename_layout = QHBoxLayout()
        filename_layout.addWidget(QLabel("文件名:"))
        self.filename_edit = QLineEdit()
        filename_layout.addWidget(self.filename_edit)
        record_control_layout.addLayout(filename_layout)
        
        self.suggest_name_btn = QPushButton("💡 建议文件名")
        self.suggest_name_btn.clicked.connect(self.suggest_filename)
        record_control_layout.addWidget(self.suggest_name_btn)
        
        record_buttons_layout = QHBoxLayout()
        self.start_record_btn = QPushButton("🔴 开始记录")
        self.start_record_btn.clicked.connect(self.start_recording)
        record_buttons_layout.addWidget(self.start_record_btn)
        
        self.stop_record_btn = QPushButton("⏹️ 停止记录")
        self.stop_record_btn.clicked.connect(self.stop_recording)
        self.stop_record_btn.setEnabled(False)
        record_buttons_layout.addWidget(self.stop_record_btn)
        
        self.open_record_btn = QPushButton("📂 打开记录文件")
        self.open_record_btn.clicked.connect(self.open_record_file)
        self.open_record_btn.setEnabled(False)
        record_buttons_layout.addWidget(self.open_record_btn)
        
        record_control_layout.addLayout(record_buttons_layout)
        record_control_group.setLayout(record_control_layout)
        layout.addWidget(record_control_group)
        
        # 实时统计
        realtime_stats_group = QGroupBox("📈 实时统计")
        realtime_stats_layout = QVBoxLayout()
        self.realtime_stats_label = QLabel("未开始记录")
        realtime_stats_layout.addWidget(self.realtime_stats_label)
        realtime_stats_group.setLayout(realtime_stats_layout)
        layout.addWidget(realtime_stats_group)
        
        # 数据预览
        preview_group = QGroupBox("👁️  数据预览")
        preview_layout = QVBoxLayout()
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(300)
        preview_layout.addWidget(self.preview_text)
        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group, 1)
        
        self.tab_widget.addTab(tab, "📊 数据记录")
    
    def create_waveform_tab(self):
        """创建波形分析标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 音频反馈选项
        audio_group = QGroupBox("🔊 音频反馈")
        audio_layout = QHBoxLayout()
        
        self.audio_enabled_checkbox = QCheckBox("启用音频反馈")
        self.audio_enabled_checkbox.toggled.connect(self.toggle_audio_feedback)
        audio_layout.addWidget(self.audio_enabled_checkbox)
        
        audio_layout.addStretch()
        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)
        
        # 实时波形图
        waveform_group = QGroupBox("📊 实时波形分析")
        waveform_layout = QVBoxLayout()
        
        self.waveform_widget = WaveformWidget("注意力与速度实时波形")
        waveform_layout.addWidget(self.waveform_widget)
        
        waveform_group.setLayout(waveform_layout)
        layout.addWidget(waveform_group, 1)
        
        # 说明
        info_label = QLabel(
            "💡 说明:\n"
            "  - 绿色曲线: 注意力值 (0-100, 左轴)\n"
            "  - 蓝色曲线: 播放速度 (0.5-2.0x, 右轴)\n"
            "  - 红色虚线: 阈值线"
        )
        info_label.setStyleSheet("color: #666; padding: 5px;")
        layout.addWidget(info_label)
        
        self.tab_widget.addTab(tab, "📊 波形分析")
    
    def init_player(self):
        """初始化播放器"""
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        
        # 播放状态变化监听
        self.media_player.playbackStateChanged.connect(self.on_playback_state_changed)
        self.media_player.errorOccurred.connect(self.on_media_error)
    
    def load_settings(self):
        """加载设置"""
        settings = QSettings("BrainwavePlayer", "Settings")
        self.attention_threshold = max(0, min(100, settings.value("threshold", 60, type=int)))
        previous_block = self.threshold_slider.blockSignals(True)
        self.threshold_slider.setValue(self.attention_threshold)
        self.threshold_slider.blockSignals(previous_block)
        self.threshold_label.setText(f"播放阈值: {self.attention_threshold}")
        
        # 更新波形图阈值线
        self.waveform_widget.threshold_line.setPos(self.attention_threshold)
        
        self.baud_combo.setCurrentText(settings.value("baudrate", "9600"))
        smoother = settings.value("smoother", self.current_smoother_name)
        if smoother in SMOOTHERS:
            self.smoother_combo.setCurrentText(smoother)
        self.legacy_raw_checkbox.setChecked(settings.value("legacy_raw_mapping", False, type=bool))
        last_port = settings.value("last_port", "")
        if last_port:
            QTimer.singleShot(100, lambda: self.set_last_port(last_port))
    
    def set_last_port(self, port_name):
        """设置上次使用的串口"""
        index = self.port_combo.findText(port_name)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)
    
    def save_settings(self):
        """保存设置"""
        settings = QSettings("BrainwavePlayer", "Settings")
        settings.setValue("threshold", self.attention_threshold)
        settings.setValue("baudrate", self.baud_combo.currentText())
        settings.setValue("smoother", self.current_smoother_name)
        settings.setValue("legacy_raw_mapping", self.legacy_raw_checkbox.isChecked())
        if self.port_combo.currentText():
            settings.setValue("last_port", self.port_combo.currentText())
    
    def refresh_ports(self, show_warning=True):
        previous = self.port_combo.currentText()
        self.port_combo.clear()
        try:
            ports = list(serial.tools.list_ports.comports())
        except (OSError, serial.SerialException) as error:
            self.on_debug_message(f"扫描串口失败: {error}")
            ports = []
        self.port_combo.addItems([port.device for port in ports])
        self.set_last_port(previous)
        if not ports and show_warning:
            QMessageBox.warning(self, "未找到设备", "未找到可用的串口设备，请检查蓝牙连接或USB串口。")

    def set_connection_controls(self, enabled):
        for widget in (self.port_combo, self.baud_combo, self.refresh_btn, self.legacy_raw_checkbox):
            widget.setEnabled(enabled)

    def connect_device(self):
        if self.brainwave_thread and self.brainwave_thread.isRunning():
            self._disconnecting = True
            self.connect_btn.setEnabled(False)
            self.connect_btn.setText("正在断开…")
            self.reset_signal_state("状态: 正在断开设备…")
            self.brainwave_thread.stop()
            return
        port = self.port_combo.currentText()
        if not port:
            QMessageBox.warning(self, "提示", "请先选择串口！")
            return
        if self.brainwave_thread is not None:
            self.brainwave_thread.deleteLater()
        self._disconnecting = False
        self.reset_signal_state("状态: 正在连接设备…")
        self.connect_btn.setEnabled(False)
        self.set_connection_controls(False)
        self.brainwave_thread = BrainwaveThread(
            port=port, baudrate=int(self.baud_combo.currentText()),
            legacy_raw_mapping=self.legacy_raw_checkbox.isChecked())
        thread = self.brainwave_thread
        thread.data_updated.connect(self.on_brainwave_data)
        thread.debug_message.connect(self.on_debug_message)
        thread.connection_changed.connect(self.on_connection_changed)
        thread.error_occurred.connect(self.on_error_occurred)
        thread.finished.connect(self.on_thread_finished)
        thread.set_debug_mode(self.debug_checkbox.isChecked())
        thread.start()
        self.save_settings()

    def on_thread_finished(self):
        self.connect_btn.setText("🔗 连接设备")
        self.connect_btn.setEnabled(True)
        self.set_connection_controls(True)
        if self._closing:
            QTimer.singleShot(0, self.close)

    def reset_signal_state(self, reason="状态: 等待有效脑波数据…"):
        self.device_worn = False
        self.attention_smoother.reset()
        self.speed_smoother.reset()
        self.smoothed_attention = 0
        self.current_attention = 0
        self.target_speed = self.current_speed = 1.0
        self.speed_adjust_timer.stop()
        self.media_player.pause()
        self.is_playing = False
        self.play_btn.setText("▶️ 播放")
        self.media_player.setPlaybackRate(1.0)
        self.attention_label.setText("当前注意力: 0")
        self.attention_bar.setValue(0)
        self.worn_label.setText("佩戴状态: ❌ 数据无效或未佩戴")
        self.speed_label.setText("播放速度: 已暂停")
        self.status_label.setText(reason)
        self._feedback_state = None

    def on_brainwave_data(self, data):
        """脑波数据更新回调"""
        if self._closing or self._disconnecting:
            return
        raw_attention = data.get('attention', 0)
        self.current_poor_signal = data.get('poor_signal', 200)
        device_worn = bool(data.get('device_worn', False))
        self.device_worn = device_worn
        if device_worn:
            raw_attention = max(0.0, min(100.0, float(raw_attention)))
            self.smoothed_attention = self.attention_smoother.update(raw_attention)
            self.current_attention = round(max(0.0, min(100.0, self.smoothed_attention)), 1)
        else:
            self.reset_signal_state("状态: 信号无效，已暂停")
        
        # 更新信号显示
        if self.current_poor_signal == 0:
            signal_text = "良好"
            signal_color = "#10b981"
            worn_text = "✅ 已佩戴"
        elif self.current_poor_signal < 200:
            signal_text = f"较差 ({self.current_poor_signal})"
            signal_color = "#f59e0b"
            worn_text = "⚠️ 信号弱"
        else:
            signal_text = "无信号"
            signal_color = "#ef4444"
            worn_text = "❌ 未佩戴"
        if data.get('legacy_mapped', False):
            signal_text = "兼容模式：信号质量未确认"
            signal_color = "#f59e0b"
        
        self.signal_label.setText(f"信号: {signal_text}")
        if signal_color != self._signal_color:
            self.signal_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {signal_color};")
            self._signal_color = signal_color
        worn_text = "✅ 已佩戴" if device_worn else "❌ 数据无效或未佩戴"
        if data.get('legacy_mapped', False):
            worn_text = "⚠️ 旧版推测状态" if device_worn else "❌ 未确认佩戴"
        self.worn_label.setText(f"佩戴状态: {worn_text}")
        
        # 更新注意力显示
        self.attention_label.setText(f"当前注意力: {self.current_attention}")
        self.attention_bar.setValue(round(self.current_attention))
        
        # 只在状态变化时换样式和提示，避免连续蜂鸣与样式重排。
        if not device_worn:
            feedback_state = 'invalid'
        elif self.current_attention >= self.attention_threshold:
            feedback_state = 'high'
        elif self.current_attention < 30:
            feedback_state = 'low'
        else:
            feedback_state = 'normal'
        if feedback_state != self._feedback_state:
            color = '#10b981' if feedback_state == 'high' else '#3b82f6'
            self.attention_bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {color}; border-radius: 3px; }}"
                f"QProgressBar {{ border: 2px solid {color}; border-radius: 5px; text-align: center; }}")
            if device_worn and feedback_state == 'high':
                self.audio_feedback.play_threshold_reached()
            elif device_worn and self.current_attention < 30:
                self.audio_feedback.play_low_attention()
            self._feedback_state = feedback_state
        if self.recorder.recording:
            try:
                self.recorder.record(self.current_attention, self.current_poor_signal,
                                     device_worn, self.current_speed)
            except (OSError, csv.Error, ValueError) as error:
                self.handle_recording_error(error)
                # 对话框会处理新事件；返回后本次样本可能已被断线或新数据取代。
                return

        # 更新波形图
        self.waveform_widget.update_data(
            self.current_speed,
            self.current_attention,
            self.attention_threshold
        )
        
        # 播放控制逻辑
        if self.video_loaded:
            if self.manual_paused:
                self.speed_adjust_timer.stop()
                self.media_player.pause()
                self.status_label.setText("状态: 已手动暂停，点击播放继续")
                return
            if device_worn and self.current_attention >= self.attention_threshold:
                # 注意力足够时，播放并根据注意力调节速度
                if self.media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                    self.media_player.play()
                    self.is_playing = True
                    self.play_btn.setText("⏸️ 暂停")
                
                # 根据注意力计算目标速度
                self.target_speed = self.calculate_speed(self.current_attention)
                self.speed_label.setText(f'播放速度: {self.target_speed:.1f}x')
                self.status_label.setText(f"状态: ▶ 播放中 (注意力: {self.current_attention})")
                self.status_label.setStyleSheet("background-color: #d1fae5; color: #065f46; border-radius: 5px;")
                
                if not self.speed_adjust_timer.isActive():
                    self.speed_adjust_timer.start()
            else:
                # 注意力不足或未佩戴时，暂停
                if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self.media_player.pause()
                    self.is_playing = False
                    self.play_btn.setText("▶️ 播放")
                
                self.speed_label.setText('播放速度: 已暂停')
                if device_worn:
                    self.status_label.setText(f"状态: ⏸ 已暂停 (注意力: {self.current_attention} < 阈值 {self.attention_threshold})")
                else:
                    self.status_label.setText("状态: 信号无效，已暂停，等待有效数据…")
                self.status_label.setStyleSheet("background-color: #fee2e2; color: #991b1b; border-radius: 5px;")
                
                if self.speed_adjust_timer.isActive():
                    self.speed_adjust_timer.stop()
        elif device_worn:
            if self.recorder.recording:
                self.status_label.setText("状态: 正在记录数据...")
            else:
                self.status_label.setText("状态: 设备信号正常，请打开视频")
    
    def calculate_speed(self, attention):
        """根据注意力计算播放速度"""
        if attention >= 90:
            return 2.0
        elif attention >= 75:
            return 1.5
        elif attention >= 60:
            return 1.0
        elif attention >= 40:
            return 0.75
        else:
            return 0.5
    
    def adjust_playback_speed(self):
        """改进的速度调整 - 使用指数移动平均"""
        smoothed_speed = self.speed_smoother.update(self.target_speed)
        self.current_speed = smoothed_speed
        if abs(self.media_player.playbackRate() - self.current_speed) >= 0.01:
            self.media_player.setPlaybackRate(self.current_speed)
        if abs(self.current_speed - self.target_speed) < 0.01:
            self.speed_adjust_timer.stop()
    
    def open_video_file(self):
        """打开视频文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "视频文件 (*.mp4 *.avi *.mkv *.mov);;所有文件 (*)"
        )
        if file_path:
            self.speed_adjust_timer.stop()
            self.speed_smoother.reset()
            self.current_speed = self.target_speed = 1.0
            self.media_player.setPlaybackRate(1.0)
            self.manual_paused = False
            self.media_player.setSource(QUrl.fromLocalFile(file_path))
            self.video_loaded = True
            self.video_label.setText(f"✓ {Path(file_path).name}")
            self.play_btn.setEnabled(True)
            self.status_label.setText("状态: 视频已加载，点击播放按钮开始")
    
    def toggle_playback(self):
        """切换播放/暂停"""
        if not self.video_loaded:
            return
        
        if self.is_playing:
            self.manual_paused = True
            self.speed_adjust_timer.stop()
            self.media_player.pause()
            self.is_playing = False
            self.play_btn.setText("▶️ 播放")
            self.speed_label.setText("播放速度: 已暂停")
            self.status_label.setText("状态: 已手动暂停，点击播放继续")
        else:
            self.manual_paused = False
            if (self.brainwave_thread and self.brainwave_thread.isRunning()
                    and (not self.device_worn or self.current_attention < self.attention_threshold)):
                self.status_label.setText("状态: 等待有效注意力达到播放阈值…")
                return
            self.media_player.play()
            self.is_playing = True
            self.play_btn.setText("⏸️ 暂停")
            self.speed_label.setText(f"播放速度: {self.current_speed:.1f}x")
            self.status_label.setText("状态: 正在播放")
    
    def on_playback_state_changed(self, state):
        """播放状态变化回调"""
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.is_playing = True
            self.play_btn.setText("⏸️ 暂停")
        else:
            self.is_playing = False
            self.play_btn.setText("▶️ 播放")
    
    def on_connection_changed(self, connected):
        if connected:
            self.connect_btn.setText("❌ 断开设备")
            self.connect_btn.setEnabled(True)
            self.export_raw_btn.setEnabled(True)
            self.status_label.setText("状态: 设备已连接，等待有效数据…")
        else:
            self.current_poor_signal = 200
            self.signal_label.setText("信号: 未连接")
            self.reset_signal_state("状态: 设备已断开，播放已暂停")

    def on_debug_message(self, message):
        self._pending_logs.append(str(message))

    def refresh_background_views(self):
        if self._pending_logs:
            messages = '\n'.join(self._pending_logs)
            self._pending_logs.clear()
            self.debug_log.append(messages)
        if self.brainwave_thread:
            self.update_stats()
        if self.recorder.recording:
            try:
                self.recorder.flush()
            except OSError as error:
                self.handle_recording_error(error)
            self.update_realtime_stats()

    def on_media_error(self, error, message):
        self.video_loaded = False
        self.manual_paused = True
        self.speed_adjust_timer.stop()
        self.play_btn.setEnabled(False)
        self.status_label.setText(f"状态: 视频无法播放：{message}")
        self.on_debug_message(f"视频播放错误: {message}")

    def on_error_occurred(self, error):
        self.on_debug_message(error)
        self.status_label.setText(f"状态: {error}")
        if not self._closing:
            QMessageBox.critical(self, "连接错误", f"{error}\n请检查设备连接与串口是否被占用。")

    def toggle_debug_mode(self, enabled):
        """切换调试模式"""
        if self.brainwave_thread:
            self.brainwave_thread.set_debug_mode(enabled)
        
        if not enabled:
            self.debug_log.clear()
            self._pending_logs.clear()
    
    def toggle_audio_feedback(self, enabled):
        """切换音频反馈"""
        self.audio_feedback.set_enabled(enabled)
    
    def update_stats(self):
        """更新统计信息"""
        if not self.brainwave_thread:
            return
        
        thread = self.brainwave_thread
        total = thread.total_packets
        att = thread.attention_packets
        poor = thread.poor_signal_packets
        sichiray = thread.sichiray_packets
        
        att_percent = att * 100 / total if total > 0 else 0
        poor_percent = poor * 100 / total if total > 0 else 0
        sichiray_percent = sichiray * 100 / total if total > 0 else 0
        
        stats_text = (
            f"总数据包: {total}\n"
            f"包含 Attention: {att} ({att_percent:.1f}%)\n"
            f"包含 Poor Signal: {poor} ({poor_percent:.1f}%)\n"
            f"Sichiray 格式: {sichiray} ({sichiray_percent:.1f}%)\n"
            f"当前 Attention: {thread.attention}\n"
            f"当前 Poor Signal: {thread.poor_signal}"
        )
        self.stats_label.setText(stats_text)
    
    def export_raw_data(self):
        """导出原始数据包"""
        packets = self.brainwave_thread.raw_packets_snapshot() if self.brainwave_thread else []
        if not packets:
            QMessageBox.warning(self, "提示", "没有可导出的原始数据！")
            return
        
        filename = recording_path(f"raw_packets_{int(time.time())}.txt")
        try:
            filename.parent.mkdir(parents=True, exist_ok=True)
            with open(filename, 'w', encoding='utf-8') as f:
                f.write("# 脑波原始数据包\n")
                f.write(f"# 导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 数据包数量: {len(packets)}（有界调试缓存）\n\n")
                for packet in packets:
                    f.write(f"{packet}\n")
            
            QMessageBox.information(self, "导出成功", f"已保存到 {filename}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出失败: {str(e)}")
    
    def suggest_filename(self):
        """建议文件名"""
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        self.filename_edit.setText(str(recording_path(f"brainwave_data_{timestamp}.csv")))
    
    def start_recording(self):
        """开始记录"""
        filename = self.filename_edit.text().strip()
        if not filename:
            QMessageBox.warning(self, "提示", "请输入文件名！")
            return
        
        if not filename.endswith('.csv'):
            filename += '.csv'
        
        try:
            filename = recording_path(filename)
            filename.parent.mkdir(parents=True, exist_ok=True)
            self.recorder.start(filename)
            self.filename_edit.setEnabled(False)
            self.suggest_name_btn.setEnabled(False)
            self.start_record_btn.setEnabled(False)
            self.stop_record_btn.setEnabled(True)
            self.open_record_btn.setEnabled(False)
            self.status_label.setText("状态: 正在记录数据...")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"开始记录失败: {str(e)}")
    
    def reset_recording_controls(self):
        self.start_record_btn.setEnabled(True)
        self.stop_record_btn.setEnabled(False)
        self.filename_edit.setEnabled(True)
        self.suggest_name_btn.setEnabled(True)
        self.open_record_btn.setEnabled(bool(self.recorder.filename))

    def handle_recording_error(self, error):
        try:
            self.recorder.stop()
        except OSError:
            pass
        self.reset_recording_controls()
        self.status_label.setText(f"状态: 记录失败：{error}")
        QMessageBox.critical(self, "记录失败", f"记录已停止，请检查磁盘和文件权限。\n{error}")

    def stop_recording(self):
        try:
            self.recorder.stop()
        except OSError as error:
            self.handle_recording_error(error)
            return
        self.reset_recording_controls()
        self.status_label.setText("状态: 记录已停止")
        self.update_realtime_stats()

    def update_realtime_stats(self):
        """更新实时统计"""
        stats = self.recorder.get_statistics()
        stats_text = (
            f"记录条数: {stats['count']}\n"
            f"平均注意力: {stats['avg_attention']:.1f}\n"
            f"佩戴时间比例: {stats['worn_ratio']:.1f}%"
        )
        self.realtime_stats_label.setText(stats_text)
        
        preview = self.recorder.get_preview(20)
        preview_text = "时间戳\t注意力\tPoor Signal\t佩戴状态\t播放速度\n"
        preview_text += "-" * 70 + "\n"
        for row in preview:
            preview_text += f"{row[0]}\t{row[1]}\t{row[2]}\t{row[3]}\t{row[4]:.2f}\n"
        self.preview_text.setText(preview_text)
    
    def open_record_file(self):
        filename = self.recorder.filename
        if not filename or not Path(filename).is_file():
            QMessageBox.warning(self, "提示", "记录文件不存在，请先记录数据。")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(filename)):
            QMessageBox.warning(self, "打开失败", f"请手动打开文件：{filename}")

    def change_smoother(self, smoother_name):
        """切换平滑算法"""
        self.current_smoother_name = smoother_name
        
        # 根据不同算法设置不同参数
        if smoother_name == '指数移动平均 (EMA)':
            self.attention_smoother = ExponentialSmoother(alpha=0.3)
            self.smoother_params_label.setText("(α=0.3)")
        elif smoother_name == '简单移动平均 (SMA)':
            self.attention_smoother = SimpleMovingAverageSmoother(window_size=5)
            self.smoother_params_label.setText("(窗口=5)")
        elif smoother_name == '加权移动平均 (WMA)':
            self.attention_smoother = WeightedMovingAverageSmoother(window_size=5)
            self.smoother_params_label.setText("(窗口=5)")
        elif smoother_name == '中值滤波':
            self.attention_smoother = MedianFilterSmoother(window_size=5)
            self.smoother_params_label.setText("(窗口=5)")
        elif smoother_name == '卡尔曼滤波':
            self.attention_smoother = KalmanFilterSmoother()
            self.smoother_params_label.setText("(最优估计)")
        elif smoother_name == '双指数平滑':
            self.attention_smoother = DoubleExponentialSmoother(alpha=0.3, beta=0.1)
            self.smoother_params_label.setText("(α=0.3, β=0.1)")
        else:
            self.attention_smoother = ExponentialSmoother(alpha=0.3)
            self.smoother_params_label.setText("(α=0.3)")
        
        self.status_label.setText(f"状态: 已切换到 {smoother_name}")
    
    def update_threshold(self, value):
        """更新阈值"""
        self.attention_threshold = value
        self.threshold_label.setText(f"播放阈值: {value}")
        
        # 更新波形图的阈值线
        self.waveform_widget.threshold_line.setPos(value)
        
        self.save_settings()
    
    def closeEvent(self, event):
        if self.brainwave_thread and self.brainwave_thread.isRunning():
            self._closing = True
            self.connect_btn.setEnabled(False)
            self.reset_signal_state("状态: 正在关闭设备…")
            self.brainwave_thread.stop()
            event.ignore()
            return
        try:
            self.recorder.stop()
        except OSError as error:
            QMessageBox.warning(self, "保存失败", f"关闭记录文件时出错：{error}")
        self.waveform_timer.stop()
        self.maintenance_timer.stop()
        self.speed_adjust_timer.stop()
        self.media_player.stop()
        self.save_settings()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("脑波控制视频播放器")
    app.setApplicationVersion(APP_VERSION)

    if len(sys.argv) >= 3 and sys.argv[1] == '--smoke-test':
        # 安装包自检不连接设备、不读写用户设置；结果仅写到指定测试文件。
        import json
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        from PyQt6.QtMultimedia import QMediaFormat
        media = QMediaPlayer()
        video = QVideoWidget()
        media.setVideoOutput(video)
        waveform = WaveformWidget()
        waveform.update_data(1.0, 50, 60)
        supported = QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode)
        result = {
            'version': APP_VERSION,
            'platform': sys.platform,
            'qt': QT_VERSION_STR,
            'pyqt': PYQT_VERSION_STR,
            'video_formats': [value.name for value in supported],
            'serial_import': True,
            'waveform_created': True,
            'recording_directory': str(recording_path('probe.csv').parent),
        }
        if len(sys.argv) >= 4:
            result['frames_decoded'] = 0
            def count_frame(frame):
                if frame.isValid():
                    result['frames_decoded'] += 1
            video.videoSink().videoFrameChanged.connect(count_frame)
            media.setSource(QUrl.fromLocalFile(str(Path(sys.argv[3]).resolve())))
            media.play()
            QTimer.singleShot(2500, app.quit)
            app.exec()
            media.stop()
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    
    player = BrainwaveVideoPlayer()
    player.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
