# core/corrector.py — 跨句上下文纠错引擎
# 利用后续句子的上下文自动修正前序翻译错误
# 策略：每积累 N 句 Final 翻译后，用 LLM 对前文做一次回顾修正

from __future__ import annotations

import json
import threading
from typing import Callable, Optional

from utils.config import CONFIG

try:
    import requests
    HTTP_AVAILABLE = True
except ImportError:
    HTTP_AVAILABLE = False


# ── 数据结构 ──

class SentenceRecord:
    """单句记录"""
    __slots__ = ("id", "text", "original", "start_ms", "end_ms")

    def __init__(self, sid: int, text: str, original: str, start_ms: int, end_ms: int) -> None:
        self.id = sid
        self.text = text
        self.original = original
        self.start_ms = start_ms
        self.end_ms = end_ms


class CorrectionResult:
    """单条修正结果"""
    __slots__ = ("sentence_id", "old_text", "new_text", "reason")

    def __init__(self, sentence_id: int, old_text: str, new_text: str, reason: str) -> None:
        self.sentence_id = sentence_id
        self.old_text = old_text
        self.new_text = new_text
        self.reason = reason


# ── 纠错引擎 ──

class ContextCorrector:
    """
    跨句上下文纠错引擎。
    - 维护最近 N 句的滑动窗口
    - 每积累 CHECK_INTERVAL 句新句子后，异步触发 LLM 回顾
    - 修正结果通过 on_correction 回调通知 UI
    """

    WINDOW_SIZE = 8         # 保留最近 8 句
    CHECK_INTERVAL = 3       # 每 3 句新句子触发一次检查

    def __init__(
        self,
        on_correction: Optional[Callable[[CorrectionResult], None]] = None,
    ) -> None:
        self._on_correction = on_correction or (lambda _: None)
        self._window: list[SentenceRecord] = []
        self._seq_counter = 0
        self._sentence_since_check = 0
        self._running = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        # LLM 配置
        self._llm_api_key = CONFIG.get("llm_api_key", "")
        self._llm_api_url = CONFIG.get("llm_api_url", "")
        self._llm_model = CONFIG.get("llm_model", "qwen-plus")

        # 简化：直接复用百炼 API Key
        if not self._llm_api_key:
            self._llm_api_key = CONFIG.get("api_key", "")

        self._llm_available = bool(HTTP_AVAILABLE and self._llm_api_key and self._llm_api_url)

    @property
    def available(self) -> bool:
        return self._llm_available

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        print(f"[corrector] 上下文纠错引擎启动（窗口={self.WINDOW_SIZE}句, 间隔={self.CHECK_INTERVAL}句）")

    def stop(self) -> None:
        self._running = False
        print("[corrector] 纠错引擎已停止")

    def reload_llm_config(self) -> None:
        """热重载 LLM 配置（无需重启引擎，改完 API 设置即生效）"""
        self._llm_api_key = CONFIG.get("llm_api_key", "")
        self._llm_api_url = CONFIG.get("llm_api_url", "")
        self._llm_model = CONFIG.get("llm_model", "qwen-plus")
        if not self._llm_api_key:
            self._llm_api_key = CONFIG.get("api_key", "")
        self._llm_available = bool(HTTP_AVAILABLE and self._llm_api_key and self._llm_api_url)
        print(f"[corrector] LLM 配置已热重载 (available={self._llm_available})")

    def push_sentence(
        self,
        text: str,
        original: str = "",
        start_ms: int = 0,
        end_ms: int = 0,
    ) -> None:
        """添加一句 Final 翻译"""
        if not text or len(text.strip()) < 2:
            return

        with self._lock:
            rec = SentenceRecord(
                sid=self._seq_counter,
                text=text.strip(),
                original=original.strip(),
                start_ms=start_ms,
                end_ms=end_ms,
            )
            self._seq_counter += 1
            self._window.append(rec)

            # 维护窗口大小
            while len(self._window) > self.WINDOW_SIZE:
                self._window.pop(0)

            self._sentence_since_check += 1

            # 是否触发检查
            if self._sentence_since_check >= self.CHECK_INTERVAL:
                self._sentence_since_check = 0
                window_copy = list(self._window)
                threading.Thread(
                    target=self._run_correction_check,
                    args=(window_copy,),
                    daemon=True,
                ).start()

    # ── 内部 ──

    def _run_correction_check(self, sentences: list[SentenceRecord]) -> None:
        """后台线程：调用 LLM 做跨句纠错"""
        if not self._running:
            return
        if not self._llm_available or len(sentences) < 2:
            return

        try:
            result = self._llm_correct(sentences)
            if result:
                self._on_correction(result)
        except Exception as e:
            print(f"[corrector] LLM 纠错失败：{e}")

    def _llm_correct(self, sentences: list[SentenceRecord]) -> Optional[CorrectionResult]:
        """调用 LLM 检查并修正翻译"""
        if len(sentences) < 2:
            return None

        # 构建上下文
        context_lines = []
        for s in sentences:
            context_lines.append(f"[句{s.id}] {s.text}")
        context = "\n".join(context_lines)

        prompt = f"""你是同声传译的质量审查员。以下是一段实时翻译的连续句子（从早到晚排列）：

{context}

请检查：后面的句子是否让前面的某句翻译不准确或可改进？
- 如果全部正确，回复 {{"ok": true}}
- 如果需要修正，回复 JSON：
  {{"ok": false, "sentence_id": 需要修正的句子ID, "new_text": "修正后的译文", "reason": "修正原因（一句话）"}}

只回复 JSON，不要其他内容。"""

        resp = requests.post(
            self._llm_api_url,
            headers={
                "Authorization": f"Bearer {self._llm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._llm_model,
                "messages": [
                    {"role": "system", "content": "你是翻译质量控制专家。只回复 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 300,
            },
            timeout=15,
        )

        if resp.status_code != 200:
            print(f"[corrector] LLM 返回 {resp.status_code}")
            return None

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        # 解析 JSON
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 JSON 片段
            import re
            m = re.search(r'\{[^{}]*\}', content)
            if m:
                try:
                    parsed = json.loads(m.group())
                except json.JSONDecodeError:
                    return None
            else:
                return None

        if parsed.get("ok", False):
            return None

        sentence_id = parsed.get("sentence_id", -1)
        new_text = parsed.get("new_text", "")
        reason = parsed.get("reason", "上下文修正")

        if sentence_id < 0 or not new_text:
            return None

        # 找到旧文本
        old_text = ""
        for s in sentences:
            if s.id == sentence_id:
                old_text = s.text
                break

        if not old_text or old_text == new_text:
            return None

        print(f"[corrector] ✓ 修正句{sentence_id}：「{old_text}」→「{new_text}」（{reason}）")
        return CorrectionResult(
            sentence_id=sentence_id,
            old_text=old_text,
            new_text=new_text,
            reason=reason,
        )
