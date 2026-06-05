# core/translator.py — 百炼 Gummy-Realtime-V1 WebSocket 流式翻译引擎
# 协议：run-task → task-started → 发送 PCM 音频 → result-generated（含识别+翻译）
#       → finish-task → task-finished
# Partial（sentence_end=false）：灰色中间字幕
# Final（sentence_end=true）：绿色闪烁修正后变白

from __future__ import annotations

import json
import threading
import time
import queue
import uuid
import enum
from typing import Callable, Optional

try:
    import websocket  # websocket-client
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("[translator] 警告：websocket-client 未安装，使用模拟翻译模式")

from utils.config import CONFIG


# ── 字幕状态常量 ──
STATUS_PARTIAL = "partial"   # sentence_end=false → 灰色中间结果
STATUS_FINAL = "final"       # sentence_end=true  → 最终结果


class TranslatorState(enum.Enum):
    """WebSocket 连接状态机"""
    IDLE = "idle"
    CONNECTING = "connecting"
    WAITING_STARTED = "waiting_started"   # 已发 run-task，等待 task-started
    STREAMING = "streaming"               # 可发送音频，接收结果
    FINISHING = "finishing"               # 已发 finish-task，等待 task-finished
    DONE = "done"


class TranslationResult:
    """单条翻译结果数据结构"""

    def __init__(
        self,
        text: str,          # 翻译后文本
        original: str,      # 原始识别文本（源语言）
        status: str,        # STATUS_PARTIAL 或 STATUS_FINAL
        start_ms: int,      # 字幕开始时间（毫秒）
        end_ms: int,        # 字幕结束时间（毫秒）
        direction: str = "",  # 翻译方向标签，如 "auto→zh" / "zh→en"
    ) -> None:
        self.text = text
        self.original = original
        self.status = status
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.direction = direction


class GummyTranslator:
    """
    百炼 Gummy-Realtime-V1 实时语音翻译引擎。
    协议流程：
      1. WebSocket 连接 → 发送 run-task 指令
      2. 收到 task-started → 开始发送 PCM 音频流
      3. 收到 result-generated → 解析 transcription（原文）+ translations（译文）
      4. 收到 finish-task → 发送 finish-task 指令
      5. 收到 task-finished → 关闭连接
    """

    # ── 协议常量（实例级，在 __init__ 中从 CONFIG 动态读取）──

    def __init__(
        self,
        on_result: Callable[[TranslationResult], None],
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> None:
        self._on_result = on_result
        self._running = False
        self._state = TranslatorState.IDLE
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._state_lock = threading.Lock()
        self._task_id: str = ""
        self._session_offset_ms: int = 0  # 会话开始时的绝对时间戳偏移
        self._stopping = threading.Event()  # 关闭标志：阻断 WS 线程回调 Qt widget
        self._send_thread: Optional[threading.Thread] = None  # 音频发送线程引用

        # 协议参数（实例级，支持运行时更新）
        self._API_URL = CONFIG.get("api_url", "wss://dashscope.aliyuncs.com/api-ws/v1/inference")
        self._API_KEY = CONFIG.get("api_key", "")
        self._MODEL = CONFIG.get("gummy_model", "gummy-realtime-v1")
        self._SAMPLE_RATE = int(CONFIG.get("gummy_sample_rate", "16000"))
        self._FORMAT = CONFIG.get("gummy_format", "pcm")
        self._SOURCE_LANG = source_lang if source_lang else CONFIG.get("gummy_source_language", "auto")
        self._TARGET_LANG = target_lang if target_lang else CONFIG.get("gummy_target_language", "zh")
        self._MAX_END_SILENCE = int(CONFIG.get("gummy_max_end_silence", "800"))

        # 翻译方向标签（供 UI 区分英→中 / 中→英）
        self._direction = f"{self._SOURCE_LANG}→{self._TARGET_LANG}"

        # Partial 结果缓存：同一个 sentence_id 的中间结果会被后续 Final 覆盖
        self._partial_cache: dict[int, TranslationResult] = {}

    # ── 公开接口 ──

    def start(self) -> None:
        """启动 WebSocket 连接并进入 run-task 流程"""
        if self._running:
            return
        self._running = True
        self._session_offset_ms = int(time.time() * 1000)
        self._task_id = uuid.uuid4().hex  # 32位 hex，无横线
        self._set_state(TranslatorState.CONNECTING)

        self._ws_thread = threading.Thread(
            target=self._connect_and_run, daemon=True, name="Gummy-WS"
        )
        self._ws_thread.start()
        print(f"[translator] Gummy 翻译引擎启动，task_id={self._task_id}")

    def stop(self) -> None:
        """停止翻译引擎并等待所有线程完全退出"""
        if not self._running:
            return
        # ⚠️ 必须先设停止标志，阻止 WS 线程的回调继续调用 Qt widget 方法
        self._stopping.set()
        self._running = False

        # 发送 finish-task 通知服务端
        if self._state in (TranslatorState.STREAMING, TranslatorState.WAITING_STARTED):
            self._send_finish_task()
            self._set_state(TranslatorState.FINISHING)
            print("[translator] 已发送 finish-task，等待服务端确认...")

        # ⚠️ 关键：从主线程显式关闭 WebSocket，以打断 WS 线程中的 run_forever() 阻塞。
        # _on_close 回调会同步触发（在主线程），设置状态为 IDLE → 使 send 循环退出。
        self._close_ws()

        # ⚠️ 等待所有后台线程完全退出再返回。
        # 不 join 的话：这些线程在 stop() 返回后仍可能做 C 级清理（ws.close 等），
        # 与 Qt 事件循环线程产生竞态 → 进程静默崩溃。
        for t_name, t in [("Send", self._send_thread), ("WS", self._ws_thread)]:
            if t and t.is_alive():
                t.join(timeout=5.0)
                if t.is_alive():
                    print(f"[translator] ⚠ {t_name} 线程 5s 未退出，继续执行")

        print("[translator] 引擎线程已全部退出")

    def push_audio(self, pcm_data: bytes) -> None:
        """
        接收来自 audio_capture 的 PCM 音频数据。
        非阻塞；仅在 STREAMING 状态下实际发送，其他状态丢弃。
        """
        if self._state != TranslatorState.STREAMING:
            return
        try:
            self._audio_queue.put_nowait(pcm_data)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(pcm_data)
            except queue.Empty:
                pass

    # ── 状态管理 ──

    def _set_state(self, new_state: TranslatorState) -> None:
        with self._state_lock:
            old = self._state
            self._state = new_state
        if old != new_state:
            print(f"[translator] 状态变更：{old.value} → {new_state.value}")

    # ── WebSocket 连接与协议主循环 ──

    def _connect_and_run(self) -> None:
        """WebSocket 连接主循环（自动重连）"""
        if not WS_AVAILABLE or not self._API_KEY:
            print("[translator] WebSocket 或 API Key 不可用，使用模拟翻译模式")
            self._mock_translate()
            return

        while self._running:
            self._set_state(TranslatorState.CONNECTING)
            try:
                self._ws = websocket.WebSocketApp(
                    self._API_URL,
                    header={"Authorization": f"Bearer {self._API_KEY}"},
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                # 启动音频发送线程
                self._send_thread = threading.Thread(
                    target=self._send_audio_loop, daemon=True, name="Gummy-Send"
                )
                self._send_thread.start()
                # 阻塞运行 WebSocket 事件循环
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                print(f"[translator] WebSocket 异常：{e}")

            if self._running:
                print("[translator] 3 秒后重连...")
                time.sleep(3)

    # ── WebSocket 回调 ──

    def _on_open(self, ws) -> None:
        """连接建立 → 发送 run-task 指令"""
        print("[translator] WebSocket 已连接，发送 run-task...")
        self._send_run_task()

    def _on_message(self, ws, message: str) -> None:
        """处理服务端返回的 JSON 事件"""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            print(f"[translator] JSON 解析失败：{message[:200]}")
            return

        header = data.get("header", {})
        event = header.get("event", "")
        payload = data.get("payload", {})

        if event == "task-started":
            self._handle_task_started()
        elif event == "result-generated":
            self._handle_result(payload)
        elif event == "task-finished":
            self._handle_task_finished()
        elif event == "task-failed":
            self._handle_task_failed(header)
        else:
            print(f"[translator] 未知事件：{event}")

    def _on_error(self, ws, error) -> None:
        print(f"[translator] WebSocket 错误：{error}")

    def _on_close(self, ws, close_code, close_msg) -> None:
        print(f"[translator] WebSocket 已关闭 (code={close_code}, msg={close_msg})")
        self._set_state(TranslatorState.IDLE)

    # ── 协议指令 ──

    def _send_run_task(self) -> None:
        """发送 run-task 指令"""
        msg = {
            "header": {
                "streaming": "duplex",
                "task_id": self._task_id,
                "action": "run-task",
            },
            "payload": {
                "model": self._MODEL,
                "parameters": {
                    "sample_rate": self._SAMPLE_RATE,
                    "format": self._FORMAT,
                    "source_language": None if self._SOURCE_LANG == "auto" else self._SOURCE_LANG,
                    "transcription_enabled": True,
                    "translation_enabled": True,
                    "translation_target_languages": [self._TARGET_LANG],
                    "max_end_silence": self._MAX_END_SILENCE,
                },
                "input": {},
                "task": "asr",
                "task_group": "audio",
                "function": "recognition",
            },
        }
        self._send_json(msg)

    def _send_finish_task(self) -> None:
        """发送 finish-task 指令"""
        msg = {
            "header": {
                "action": "finish-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }
        self._send_json(msg)

    def _send_json(self, data: dict) -> None:
        """通过 WebSocket 发送 JSON 文本帧"""
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(json.dumps(data, ensure_ascii=False))
            except Exception as e:
                print(f"[translator] 发送 JSON 失败：{e}")

    # ── 事件处理 ──

    def _handle_task_started(self) -> None:
        """task-started：可以开始发送音频"""
        self._set_state(TranslatorState.STREAMING)
        print("[translator] ✓ 任务已启动，开始流式传输音频")

    def _handle_result(self, payload: dict) -> None:
        """
        解析 result-generated 事件。
        提取 transcription（原文）和 translations（译文）。
        """
        output = payload.get("output", {})

        # ── 语音识别结果（原文）──
        transcription = output.get("transcription", {})
        if not transcription:
            return

        original_text = transcription.get("text", "")
        is_final = transcription.get("sentence_end", False)
        sentence_id = transcription.get("sentence_id", 0)
        begin_ms = transcription.get("begin_time", 0)
        end_ms = transcription.get("end_time", begin_ms + 2000)

        # ── 翻译结果（译文）──
        translations = output.get("translations", [])
        translated_text = translations[0].get("text", "") if translations else ""

        status = STATUS_FINAL if is_final else STATUS_PARTIAL

        result = TranslationResult(
            text=translated_text,
            original=original_text,
            status=status,
            start_ms=begin_ms,
            end_ms=end_ms,
            direction=self._direction,
        )

        # Partial：缓存 + 立即推送给 UI
        if not is_final:
            self._partial_cache[sentence_id] = result
        else:
            # Final：清除缓存
            self._partial_cache.pop(sentence_id, None)

        # ⚠️ 关闭期间阻断回调：防止 WS 监听线程（非 Qt 线程）
        # 调用 Qt widget 方法导致内存损坏 → 进程静默崩溃
        if not self._stopping.is_set():
            self._on_result(result)

    def _handle_task_finished(self) -> None:
        """task-finished：任务正常结束"""
        print("[translator] ✓ 任务正常结束")
        self._set_state(TranslatorState.DONE)
        self._close_ws()

    def _handle_task_failed(self, header: dict) -> None:
        """task-failed：任务异常"""
        error_code = header.get("error_code", "UNKNOWN")
        error_msg = header.get("error_message", "")
        print(f"[translator] ✗ 任务失败 [{error_code}]：{error_msg}")
        self._set_state(TranslatorState.IDLE)
        self._close_ws()

    # ── 音频发送循环 ──

    def _send_audio_loop(self) -> None:
        """从队列取音频 PCM 数据，通过 WebSocket 二进制帧发送"""
        # 等待进入 STREAMING 状态
        while self._running and self._state != TranslatorState.STREAMING:
            if self._state == TranslatorState.IDLE:
                return
            time.sleep(0.05)

        print("[translator] 音频发送线程就绪")
        while self._running and self._state == TranslatorState.STREAMING:
            try:
                pcm = self._audio_queue.get(timeout=0.5)
                if self._ws and self._ws.sock and self._ws.sock.connected:
                    self._ws.send(pcm, opcode=websocket.ABNF.OPCODE_BINARY)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[translator] 发送音频异常：{e}")
                break

        print("[translator] 音频发送线程退出")

    # ── 辅助 ──

    def _close_ws(self) -> None:
        """安全关闭 WebSocket 连接（线程安全：swap-with-None 防重复关闭）"""
        ws = self._ws
        self._ws = None  # 先置空，防止主线程和 WS 回调线程同时 close 同一对象
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    # ── 模拟模式（开发/测试用）──

    def _mock_translate(self) -> None:
        """无 API Key 时的模拟翻译模式（每 2 秒一条）"""
        samples = [
            ("Hello, this is a live translation test.", "你好，这是实时翻译测试。"),
            ("The Gummy-Realtime-V1 model is working.", "Gummy-Realtime-V1 模型正在运行。"),
            ("Real-time speech recognition and translation.", "实时语音识别与翻译。"),
            ("AI LiveTranslate Pro is now connected to Bailian.", "AI LiveTranslate Pro 已接入百炼。"),
        ]
        idx = 0
        t = 0
        self._set_state(TranslatorState.STREAMING)
        while self._running:
            time.sleep(2)
            orig, trans = samples[idx % len(samples)]
            start_ms = t * 2000
            end_ms = start_ms + 1800

            # Partial（灰色）
            partial = TranslationResult(orig, orig, STATUS_PARTIAL, start_ms, end_ms, direction=self._direction)
            self._on_result(partial)
            time.sleep(0.5)

            # Final（修正译文）
            final = TranslationResult(trans, orig, STATUS_FINAL, start_ms, end_ms, direction=self._direction)
            self._on_result(final)

            idx += 1
            t += 1
