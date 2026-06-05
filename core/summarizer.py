# core/summarizer.py — 增量主题分析 + 专业术语增强（⛔ 禁止修改主循环逻辑）
# 每 60 秒触发一次，仅提取会议核心关键词（不追求长摘要，提升稳定性）
# 专业术语注释：自动识别 RAG、Agent、MCP、Workflow 等技术词汇

from __future__ import annotations

import threading
import time
import json
from datetime import datetime
from typing import Callable, List, Optional

try:
    import requests
    HTTP_AVAILABLE = True
except ImportError:
    HTTP_AVAILABLE = False
    print("[summarizer] 警告：requests 未安装，将使用模拟分析模式")

from utils.config import CONFIG

# 默认 LLM API 地址（当 llm_api_url 未配置时使用）
_DEFAULT_LLM_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 需要强制注释的专业术语表（术语 → 中文解释）
TERM_GLOSSARY: dict[str, str] = {
    "RAG": "检索增强生成",
    "Agent": "AI 智能体",
    "MCP": "模型上下文协议",
    "Workflow": "工作流",
    "LLM": "大语言模型",
    "Embedding": "向量嵌入",
    "Fine-tuning": "模型微调",
    "Prompt": "提示词",
    "RLHF": "基于人类反馈的强化学习",
    "CoT": "思维链推理",
    "LoRA": "低秩适配微调",
    "Vector DB": "向量数据库",
    "Transformer": "Transformer 架构",
    "Attention": "注意力机制",
}

# 增量主题分析间隔（秒），v0.7.2 从 30s 调整为 60s
SUMMARY_INTERVAL = int(CONFIG.get("summary_interval", "60"))


class Summarizer:
    """
    增量主题分析器。
    在后台线程中每隔 SUMMARY_INTERVAL 秒，对累积的字幕文本进行主题关键词提取。
    不影响音频捕获线程和 UI 渲染线程。
    """

    def __init__(
        self,
        on_summary_text: Callable[[str], None],
        on_summary: Callable[[str], None],
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Args:
            on_summary_text: 收到新内容摘要时的回调（用于更新 UI 右侧摘要栏）
            on_summary:      收到完整会议纪要时的回调（会议结束时调用）
            on_log:          状态日志回调（用于 UI 显示工作进度）
        """
        self._on_summary_text = on_summary_text
        self._on_summary = on_summary
        self._on_log = on_log
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._text_buffer: List[str] = []   # 累积字幕文本缓冲区
        self._buffer_lock = threading.Lock()
        self._last_processed_idx = 0        # 上次分析处理到的位置（增量）

        # ── 渐进累积摘要状态 ──
        self._last_summary = ""             # 不断生长的累计摘要

        # ── 状态计数器 ──
        self._sentence_count = 0            # 已记录句子总数
        self._analysis_count = 0            # 已执行分析次数
        self._last_analysis_time = ""       # 上次分析时间

        # ── 解析有效 LLM Key（llm_api_key 为空时 fallback 到 api_key）──
        raw_key = CONFIG.get("llm_api_key", "")
        if not raw_key:
            raw_key = CONFIG.get("api_key", "")
        self._llm_key = raw_key

        # ── 解析有效 LLM URL（llm_api_url 为空时 fallback 到默认 DashScope 地址）──
        raw_url = CONFIG.get("llm_api_url", "")
        if not raw_url:
            # api_url 是 Gummy WebSocket 地址，不能用；用 llm_api_url 的默认值
            raw_url = _DEFAULT_LLM_URL
        self._llm_url = raw_url

        self._llm_model = CONFIG.get("llm_model", "qwen-plus")
        self._llm_available = bool(HTTP_AVAILABLE and self._llm_key and self._llm_url)

    def start(self) -> None:
        """启动后台分析线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._analysis_loop, daemon=True, name="Summarizer"
        )
        self._thread.start()
        print(f"[summarizer] 主题分析线程已启动，间隔 {SUMMARY_INTERVAL}s")

    def stop(self) -> str:
        """
        停止后台分析线程，并生成最终会议总结报告。

        Returns:
            Markdown 格式的完整会议纪要，如果无内容则返回空字符串
        """
        self._running = False
        # 等待线程退出
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        print("[summarizer] 主题分析线程已停止")

        # 生成最终总结
        self._emit_log("📋 正在生成最终会议纪要…")
        final_summary = self._generate_final_summary()
        if final_summary:
            print(f"[summarizer] 最终会议纪要已生成，{len(final_summary)} 字")
            self._emit_log("✅ 会议纪要已生成")
            self._on_summary(final_summary)
        return final_summary

    def reset(self) -> None:
        """
        重置所有状态，准备新一轮会话。
        不清除 LLM 配置，只重置文本缓冲区和摘要累积状态。
        """
        with self._buffer_lock:
            self._text_buffer.clear()
            self._last_processed_idx = 0
        self._last_summary = ""
        self._sentence_count = 0
        self._analysis_count = 0
        self._last_analysis_time = ""
        self._emit_log("🔄 摘要引擎已重置，等待新会话")
        print("[summarizer] 状态已重置")

    def reload_llm_config(self) -> None:
        """热重载 LLM 配置（无需重启引擎，改完 API 设置即生效）"""
        raw_key = CONFIG.get("llm_api_key", "")
        if not raw_key:
            raw_key = CONFIG.get("api_key", "")
        self._llm_key = raw_key
        self._llm_url = CONFIG.get("llm_api_url", "") or _DEFAULT_LLM_URL
        self._llm_model = CONFIG.get("llm_model", "qwen-plus")
        self._llm_available = bool(HTTP_AVAILABLE and self._llm_key and self._llm_url)
        print(f"[summarizer] LLM 配置已热重载 (available={self._llm_available})")
        if self._llm_available:
            self._emit_log("🔑 LLM 已配置就绪，分析功能可用")
        else:
            self._emit_log("⚠ LLM 未配置，将使用本地词典匹配")

    def push_text(self, text: str) -> None:
        """
        向缓冲区追加新字幕文本（由翻译引擎的 Final 结果触发）。

        Args:
            text: 已确认的字幕文本（仅 Final 结果）
        """
        with self._buffer_lock:
            self._sentence_count += 1
            self._text_buffer.append(text)
            total = len(self._text_buffer)

        preview = text[:50] + ("…" if len(text) > 50 else "")
        log_msg = f"📝 第 {self._sentence_count} 句 | 已累积 {total} 句 | {preview}"
        print(f"[summarizer] {log_msg}")
        self._emit_log(log_msg)

    def get_status(self) -> str:
        """返回当前摘要引擎状态（供 UI 轮询/查询）"""
        with self._buffer_lock:
            total = len(self._text_buffer)
            pending = total - self._last_processed_idx
        if not self._running:
            return "⏸ 已停止"
        if self._analysis_count == 0:
            if total == 0:
                return "⏳ 等待翻译内容..."
            return f"⏳ 已记录 {total} 句，等待首次分析 (间隔{SUMMARY_INTERVAL}s)"
        if pending > 0:
            return f"🔄 已分析 {self._analysis_count} 次 | {total} 句 | 待处理 {pending} 句"
        return f"✅ 已分析 {self._analysis_count} 次 | {total} 句 | {self._last_analysis_time}"

    def get_full_transcript(self) -> str:
        """返回完整字幕文本（用于生成会议纪要）"""
        with self._buffer_lock:
            return "\n".join(self._text_buffer)

    def annotate_terms(self, text: str) -> str:
        """
        对文本中的专业术语进行注释（术语 → 术语[解释]）。

        Args:
            text: 原始翻译文本

        Returns:
            注释后的文本
        """
        for term, explanation in TERM_GLOSSARY.items():
            if term in text:
                text = text.replace(term, f"{term}[{explanation}]")
        return text

    # ──────────────────────────────────────────
    # 内部分析逻辑
    # ──────────────────────────────────────────

    def _emit_log(self, msg: str) -> None:
        """线程安全地发送日志到 UI"""
        try:
            if self._on_log:
                self._on_log(msg)
        except Exception:
            pass

    def _analysis_loop(self) -> None:
        """主题分析主循环（每 SUMMARY_INTERVAL 秒触发一次增量分析）"""
        while self._running:
            time.sleep(SUMMARY_INTERVAL)
            if not self._running:
                break

            with self._buffer_lock:
                new_texts = self._text_buffer[self._last_processed_idx:]
                self._last_processed_idx = len(self._text_buffer)
                total = len(self._text_buffer)

            if not new_texts:
                self._emit_log(f"⏭ 第 {self._analysis_count + 1} 次跳过 | 无新内容 (总计 {total} 句)")
                continue

            self._analysis_count += 1
            incremental_text = "\n".join(new_texts)
            cnt = len(new_texts)
            now = datetime.now().strftime("%H:%M:%S")
            self._last_analysis_time = now

            self._emit_log(f"🔍 第 {self._analysis_count} 次分析 | {now} | 增量 {cnt} 句 | 总计 {total} 句")
            print(f"[summarizer] 🔍 第 {self._analysis_count} 次分析 ({now})，增量 {cnt} 句")

            if self._llm_available:
                has_prev = bool(self._last_summary)
                self._emit_log(f"🤖 正在调用 LLM {'更新' if has_prev else '生成'}摘要...")

            summary = self._generate_summary(incremental_text)
            if summary:
                self._last_summary = summary
                print(f"[summarizer] ✅ 摘要：{summary[:80]}…")
                self._emit_log(f"✅ {summary}")
                self._on_summary_text(summary)
            else:
                self._emit_log("⚠ 未生成摘要（内容过短或 LLM 不可用）")

    def _generate_summary(self, text: str) -> str:
        """
        对增量文本生成/更新渐进累积摘要。
        如果有上次摘要，将其作为上下文让 LLM 更新而非重写。

        Args:
            text: 本轮新增的文本

        Returns:
            更新后的累积摘要（1-3 句话），或空字符串
        """
        if len(text.strip()) < 10:
            return ""

        if self._llm_available:
            return self._call_llm_for_summary(text, self._last_summary)
        else:
            # 降级：拼接历史摘要 + 本轮前 3 句
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            new_part = "、".join(lines[:3])
            if len(new_part) > 60:
                new_part = new_part[:57] + "…"
            if self._last_summary:
                return f"{self._last_summary}；{new_part}"
            return f"📝 {new_part}" if new_part else ""

    def _call_llm_for_summary(self, text: str, previous_summary: str = "") -> str:
        """调用 LLM API 生成/更新渐进累积摘要"""
        try:
            if previous_summary:
                system_prompt = (
                    "你是一个精炼的会议摘要助手。以下是之前已总结的内容摘要，"
                    "现在收到了新的会议内容，请将新旧内容融合，"
                    "用 2-3 句简洁的中文更新整体摘要。"
                    "只返回更新后的摘要文本本身，不要加任何前缀、编号或格式。"
                )
                user_content = (
                    f"【之前摘要】\n{previous_summary}\n\n"
                    f"【新内容】\n{text}\n\n"
                    f"请融合以上信息，输出更新后的整体摘要："
                )
            else:
                system_prompt = (
                    "你是一个精炼的会议摘要助手。"
                    "请用 1-2 句简洁的中文总结以下内容的要点。"
                    "只返回总结文本本身，不要加任何前缀、编号或格式。"
                    "如果内容太短或无意义，直接返回空字符串。"
                )
                user_content = text

            resp = requests.post(
                self._llm_url,
                headers={
                    "Authorization": f"Bearer {self._llm_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._llm_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": 200,
                },
                timeout=15,
            )

            if resp.status_code != 200:
                print(f"[summarizer] 摘要 API HTTP {resp.status_code}: {resp.text[:200]}")
                self._emit_log(f"⚠ LLM API 返回 {resp.status_code}")
                return ""

            result = resp.json()
            choices = result.get("choices", [])
            if not choices:
                return ""

            content = choices[0].get("message", {}).get("content", "").strip()
            return content if content else ""

        except requests.exceptions.Timeout:
            print("[summarizer] 摘要 API 调用超时")
            self._emit_log("⏱ 摘要生成超时，跳过本轮")
            return ""
        except Exception as e:
            print(f"[summarizer] 摘要 API 调用失败：{e}")
            return ""

    def _generate_final_summary(self) -> str:
        """
        生成最终完整会议纪要。
        将所有字幕 + 渐进摘要一并传给 LLM，生成结构化 Markdown 报告。

        Returns:
            Markdown 格式的完整会议纪要，无内容时返回空字符串
        """
        transcript = self.get_full_transcript()
        if not transcript.strip():
            self._emit_log("⚠ 无字幕数据，跳过最终总结")
            return ""

        # 如果 LLM 可用，生成完整报告
        if self._llm_available:
            self._emit_log("🤖 正在调用 LLM 生成完整会议纪要…")
            return self._call_llm_for_final_notes(transcript, self._last_summary)
        else:
            # 降级：用渐进摘要 + 字幕拼一个简单版本
            parts = []
            if self._last_summary:
                return f"# 会议摘要\n\n{self._last_summary}"
            return "\n".join(parts)

    def _call_llm_for_final_notes(self, transcript: str, progressive_summary: str) -> str:
        """调用 LLM 生成结构化完整会议纪要"""
        fallback = f"# 会议摘要\n\n{progressive_summary}\n"
        try:
            user_content = f"## 实时摘要\n{progressive_summary}\n\n## 完整字幕\n{transcript}"
            resp = requests.post(
                self._llm_url,
                headers={
                    "Authorization": f"Bearer {self._llm_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._llm_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是一个会议内容总结助手。请根据提供的实时摘要和完整字幕，"
                                "生成一份简洁的会议内容摘要。\n\n"
                                "要求：\n"
                                "- 用 Markdown 格式输出\n"
                                "- 提炼主要讨论内容和关键结论，不需要固定章节模板\n"
                                "- 如果内容不够充实，宁可简短也不要编造\n"
                                "- 控制在 500 字以内\n\n"
                                "只输出摘要内容本身，不加任何前缀。"
                            ),
                        },
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": 1500,
                },
                timeout=45,
            )

            if resp.status_code != 200:
                print(f"[summarizer] 最终纪要 API HTTP {resp.status_code}")
                self._emit_log(f"⚠ LLM API 返回 {resp.status_code}，降级输出")
                return fallback

            result = resp.json()
            choices = result.get("choices", [])
            if not choices:
                return fallback

            content = choices[0].get("message", {}).get("content", "").strip()
            return content if content else fallback

        except requests.exceptions.Timeout:
            print("[summarizer] 最终纪要 API 调用超时")
            self._emit_log("⏱ 最终纪要生成超时，降级输出")
            return fallback
        except Exception as e:
            print(f"[summarizer] 最终纪要生成失败：{e}")
            return fallback

