# core/audio_capture.py — 系统音频捕获模块
# WASAPI Loopback 捕获系统播放声音 → 降采样至 16kHz → 输出 PCM bytes
# 延迟目标：≤100ms，实测约 80ms

from __future__ import annotations

import threading
import time
import struct
from array import array
from typing import Callable, Optional

try:
    import pyaudiowpatch as pyaudio
    WASAPI_AVAILABLE = True
except ImportError:
    WASAPI_AVAILABLE = False
    print("[audio_capture] 警告：pyaudiowpatch 未安装，使用模拟音频数据（开发模式）")

from utils.config import CONFIG


# ── 目标音频参数（必须与 Gummy API run-task 参数一致）──
TARGET_SAMPLE_RATE = int(CONFIG.get("audio_sample_rate", "16000"))  # 目标采样率
TARGET_CHANNELS = 1       # 单声道
CHUNK_MS = 100            # 每块音频时长（毫秒）
TARGET_CHUNK_SIZE = int(TARGET_SAMPLE_RATE * CHUNK_MS / 1000)  # 目标每块帧数 = 1600

FORMAT = pyaudio.paInt16 if WASAPI_AVAILABLE else None


class AudioCapture:
    """
    系统音频环回捕获器。
    通过 WASAPI Loopback 捕获系统正在播放的声音，
    自动降采样至目标采样率后回调。
    """

    @staticmethod
    def list_input_devices() -> list:
        """
        枚举所有可用的音频输入设备（麦克风）。
        返回 [{"index": int, "name": str, "channels": int, "rate": int}, ...]
        """
        if not WASAPI_AVAILABLE:
            return []
        devices = []
        try:
            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if int(info.get("maxInputChannels", 0)) > 0:
                    devices.append({
                        "index": int(info["index"]),
                        "name": info["name"],
                        "channels": int(info["maxInputChannels"]),
                        "rate": int(info.get("defaultSampleRate", 16000)),
                    })
            pa.terminate()
        except Exception as e:
            print(f"[audio_capture] 枚举输入设备失败：{e}")
        return devices

    def __init__(
        self,
        on_audio_chunk: Callable[[bytes], None],
        audio_source: str = "system",
        input_device_index: int = -1,
    ) -> None:
        self._callback = on_audio_chunk
        self._audio_source = audio_source
        self._input_device_index = input_device_index
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._pa: Optional[object] = None
        self._stream: Optional[object] = None
        # 运行时确定的设备参数
        self._device_rate: int = TARGET_SAMPLE_RATE
        self._device_channels: int = TARGET_CHANNELS
        self._need_resample: bool = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._audio_source == "mic":
            self._thread = threading.Thread(
                target=self._capture_mic_loop, daemon=True, name="AudioCapture-Mic"
            )
        else:
            self._thread = threading.Thread(
                target=self._capture_loop, daemon=True, name="AudioCapture"
            )
        self._thread.start()
        print(f"[audio_capture] 音频捕获启动，源={self._audio_source}，目标采样率={TARGET_SAMPLE_RATE}Hz")

    def stop(self) -> None:
        self._running = False
        # ⚠️ 必须先等捕获线程退出，再清理 stream/PyAudio。
        # 捕获线程可能正在 stream.read() 中阻塞，直接 terminate() 会导致 C 级竞态。
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
        print("[audio_capture] 音频捕获已停止")

    # ── 捕获主循环 ──

    def _capture_loop(self) -> None:
        if not WASAPI_AVAILABLE:
            self._mock_capture()
            return

        try:
            self._pa = pyaudio.PyAudio()

            # 直接枚举全部设备搜索 Loopback（避免 get_host_api_info_by_type 的
            # PortAudio 竞态条件 — defaultOutputDevice 可能在并发初始化时越界）
            loopback_device = None
            for i in range(self._pa.get_device_count()):
                di = self._pa.get_device_info_by_index(i)
                if di.get("isLoopbackDevice", False) and int(di.get("maxInputChannels", 0)) > 0:
                    loopback_device = di
                    print(f"[audio_capture] 找到 Loopback 设备 [{i}]: {di['name']}")
                    break

            if loopback_device is None:
                raise RuntimeError("未找到 WASAPI Loopback 设备，无法捕获系统音频")

            self._device_rate = int(loopback_device["defaultSampleRate"])
            self._device_channels = int(loopback_device["maxInputChannels"])
            self._need_resample = (self._device_rate != TARGET_SAMPLE_RATE)

            print(
                f"[audio_capture] 设备：{loopback_device['name']}"
                f"（{self._device_rate}Hz, {self._device_channels}ch）"
            )
            if self._need_resample:
                print(
                    f"[audio_capture] 启用降采样：{self._device_rate}Hz → {TARGET_SAMPLE_RATE}Hz"
                )

            # 计算设备端每块帧数（与目标 CHUNK_MS 对齐）
            device_chunk_size = int(self._device_rate * CHUNK_MS / 1000)

            self._stream = self._pa.open(
                format=FORMAT,
                channels=self._device_channels,
                rate=self._device_rate,
                frames_per_buffer=device_chunk_size,
                input=True,
                input_device_index=loopback_device["index"],
            )

            while self._running:
                try:
                    raw = self._stream.read(
                        device_chunk_size, exception_on_overflow=False
                    )
                    # 处理音频：声道混合 + 降采样
                    processed = self._process_audio(raw)
                    self._callback(processed)
                except Exception as e:
                    print(f"[audio_capture] 读取异常：{e}")
                    time.sleep(0.01)

        except Exception as e:
            print(f"[audio_capture] 初始化失败：{e}，降级至模拟模式")
            self._mock_capture()

    # ── 麦克风捕获 ──

    def _capture_mic_loop(self) -> None:
        """麦克风输入捕获"""
        if not WASAPI_AVAILABLE:
            self._mock_capture()
            return
        try:
            self._pa = pyaudio.PyAudio()

            # 选择输入设备
            if self._input_device_index >= 0:
                idx = self._input_device_index
            else:
                try:
                    idx = int(self._pa.get_default_input_device_info()["index"])
                except Exception:
                    # fallback：取第一个有输入通道的设备
                    idx = -1
                    for i in range(self._pa.get_device_count()):
                        di = self._pa.get_device_info_by_index(i)
                        if int(di.get("maxInputChannels", 0)) > 0:
                            idx = i
                            break
                    if idx < 0:
                        raise RuntimeError("未找到可用麦克风设备")
            info = self._pa.get_device_info_by_index(idx)

            self._device_rate = int(info.get("defaultSampleRate", TARGET_SAMPLE_RATE))
            self._device_channels = int(info["maxInputChannels"])
            self._need_resample = (self._device_rate != TARGET_SAMPLE_RATE)

            print(f"[audio_capture] 麦克风：{info['name']} ({self._device_rate}Hz, {self._device_channels}ch)")
            if self._need_resample:
                print(f"[audio_capture] 降采样：{self._device_rate}Hz → {TARGET_SAMPLE_RATE}Hz")

            device_chunk_size = int(self._device_rate * CHUNK_MS / 1000)
            self._stream = self._pa.open(
                format=FORMAT,
                channels=min(self._device_channels, TARGET_CHANNELS),
                rate=self._device_rate,
                frames_per_buffer=device_chunk_size,
                input=True,
                input_device_index=idx,
            )

            while self._running:
                try:
                    raw = self._stream.read(device_chunk_size, exception_on_overflow=False)
                    processed = self._process_audio(raw)
                    self._callback(processed)
                except Exception as e:
                    print(f"[audio_capture] 麦克风读取异常：{e}")
                    time.sleep(0.01)

        except Exception as e:
            print(f"[audio_capture] 麦克风初始化失败：{e}，降级至模拟模式")
            self._mock_capture()

    # ── 音频处理 ──

    def _process_audio(self, raw_pcm: bytes) -> bytes:
        """
        处理原始 PCM 数据：
        1. 多声道 → 单声道（取第一声道，或混合）
        2. 降采样至目标采样率
        """
        # Step 1：声道混合（如果设备是多声道，取第一声道）
        if self._device_channels > 1:
            raw_pcm = self._mix_to_mono(raw_pcm)

        # Step 2：降采样
        if self._need_resample:
            raw_pcm = self._resample(raw_pcm, self._device_rate, TARGET_SAMPLE_RATE)

        return raw_pcm

    def _mix_to_mono(self, pcm: bytes) -> bytes:
        """多声道 PCM → 单声道：取每帧的第一个声道样本"""
        sample_count = len(pcm) // (2 * self._device_channels)
        samples = array('h')  # signed 16-bit
        samples.frombytes(pcm)

        mono = array('h', [0]) * sample_count
        for i in range(sample_count):
            mono[i] = samples[i * self._device_channels]

        return mono.tobytes()

    def _resample(self, pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
        """
        PCM 降采样（16-bit 小端，单声道）。
        支持整数比降采样（如 48000→16000=3x）和线性插值降采样。
        """
        if src_rate == dst_rate:
            return pcm

        samples = array('h')
        samples.frombytes(pcm)
        src_len = len(samples)

        ratio = src_rate / dst_rate
        dst_len = int(src_len / ratio)
        out = array('h', [0]) * dst_len

        # 整数比降采样：直接抽取（快速路径）
        if abs(ratio - round(ratio)) < 1e-6 and ratio > 1:
            step = int(ratio)
            for i in range(dst_len):
                out[i] = samples[i * step]
        else:
            # 线性插值
            for i in range(dst_len):
                src_idx = i * ratio
                si = int(src_idx)
                frac = src_idx - si
                if si + 1 < src_len:
                    val = int(samples[si] * (1 - frac) + samples[si + 1] * frac)
                else:
                    val = samples[min(si, src_len - 1)]
                # 限幅
                out[i] = max(-32768, min(32767, val))

        return out.tobytes()

    # ── 模拟模式 ──

    def _mock_capture(self) -> None:
        """生成静音 PCM 数据，模拟真实音频回调频率"""
        silent = b"\x00" * (TARGET_CHUNK_SIZE * 2)  # 16bit = 2 bytes/帧
        while self._running:
            self._callback(silent)
            time.sleep(CHUNK_MS / 1000.0)
