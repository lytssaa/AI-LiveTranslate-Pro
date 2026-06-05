# ui/settings_window.py — 设置对话框
# v1.1 — Gummy 参数 + LLM API 设置（支持主流模型）

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QWidget,
    QLabel, QPushButton, QSlider, QComboBox, QLineEdit, QToolButton,
    QCheckBox, QFormLayout, QMessageBox,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal

from utils.config import CONFIG, save_config


# ── API 供应商预设 ──
API_PRESETS = [
    ("dashscope", "百炼 DashScope", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen-plus"),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1/chat/completions", "deepseek-chat"),
    ("openai", "OpenAI", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    ("custom", "自定义接口", "", ""),
]


class CollapsibleSection(QWidget):
    """可折叠分组控件：点击标题栏展开/折叠内容"""

    def __init__(self, title: str, parent=None, expanded: bool = True):
        super().__init__(parent)
        self._expanded = expanded
        self._title_text = title

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(0)

        # 标题栏按钮
        arrow = "▼" if expanded else "▶"
        self._btn = QToolButton()
        self._btn.setText(f"  {arrow}  {title}")
        self._btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._btn.setIconSize(QSize(10, 10))
        self._btn.setStyleSheet("""
            QToolButton {
                text-align: left; font-weight: bold; font-size: 12px;
                padding: 7px 10px; border: 1px solid #3d3d5c; border-radius: 6px;
                background: #232340; color: #ccc;
            }
            QToolButton:hover { background: #2e2e55; }
        """)
        self._btn.clicked.connect(self._toggle)
        outer.addWidget(self._btn)

        # 内容区
        self._content = QWidget()
        self._content.setVisible(expanded)
        self._content.setStyleSheet("border: 1px solid #3d3d5c; border-top: none; "
                                     "border-radius: 0 0 6px 6px; background: #1c1c30;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(10, 8, 10, 10)
        self._content_layout.setSpacing(6)
        outer.addWidget(self._content)

    def content_layout(self):
        return self._content_layout

    def _toggle(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        arrow = "▼" if self._expanded else "▶"
        self._btn.setText(f"  {arrow}  {self._title_text}")


class SettingsWindow(QDialog):
    """设置对话框 — Gummy 参数 + LLM API（非模态，可边用边改）"""

    # 信号：LLM 配置变更（无需重启，热重载即可）
    llm_saved = pyqtSignal()
    # 信号：翻译引擎配置变更（需重启生效）
    engine_restart_needed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._needs_restart = False
        self._original_silence = int(CONFIG.get("gummy_max_end_silence", "800"))

        # 记录原始配置值，用于保存时检测变更类型
        self._orig_gummy_key = CONFIG.get("api_key", "")
        self._orig_gummy_url = CONFIG.get("api_url", "wss://dashscope.aliyuncs.com/api-ws/v1/inference")
        self._orig_gummy_model = CONFIG.get("gummy_model", "gummy-realtime-v1")
        self._orig_src_lang = CONFIG.get("gummy_source_language", "auto")
        self._orig_tgt_lang = CONFIG.get("gummy_target_language", "zh")
        self._orig_audio_src = CONFIG.get("audio_source", "system")
        self._orig_audio_dev = CONFIG.get("audio_input_device", "-1")
        self._orig_bi = CONFIG.get("bidirectional_enabled", "false")
        self._orig_llm_key = CONFIG.get("llm_api_key", "")
        self._orig_llm_url = CONFIG.get("llm_api_url", "")
        self._orig_llm_model = CONFIG.get("llm_model", "qwen-plus")

        self.setWindowTitle("⚙️  设置 — AI LiveTranslate Pro")
        self.setMinimumWidth(440)
        self.setMaximumWidth(520)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )

        # 统一深色主题：与 CollapsibleSection 面板颜色一致
        self.setStyleSheet("""
            QDialog {
                background: #1a1a2e;
                color: #e0e0e0;
            }
            QLabel {
                color: #ddd;
            }
            QLabel[hint="true"] {
                color: #999;
                font-size: 11px;
            }
            QComboBox, QLineEdit {
                background: #2a2a3e;
                color: #e0e0e0;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background: #2a2a3e;
                color: #e0e0e0;
                selection-background-color: #3a3a5e;
            }
            QCheckBox {
                color: #ddd;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #555;
                border-radius: 3px;
                background: #2a2a3e;
            }
            QCheckBox::indicator:checked {
                background: #3a6fc5;
                border-color: #5a9eff;
            }
            QCheckBox::indicator:hover {
                border-color: #777;
            }
            QSlider::groove:horizontal {
                background: #333;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #5a9eff;
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #3a6fc5;
                border-radius: 3px;
            }
        """)

        self._build_ui()
        self._load_current_values()

    # ── UI 构建 ──

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(6)

        # ──── 🌐 翻译 API（百炼 Gummy）────
        gummy_sec = CollapsibleSection("🌐 翻译 API（百炼 Gummy）", expanded=True)
        gummy_form = QFormLayout()
        gummy_form.setSpacing(6)

        # Key 行（输入框 + 显示/隐藏按钮）
        key_row = QHBoxLayout()
        self._gummy_key_input = QLineEdit()
        self._gummy_key_input.setPlaceholderText("sk-xxxxxxxxxxxxxxxx")
        self._gummy_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._gummy_key_input.setMinimumWidth(300)
        key_row.addWidget(self._gummy_key_input, 1)

        self._gummy_key_eye = QPushButton("👁")
        self._gummy_key_eye.setFixedSize(32, 28)
        self._gummy_key_eye.setCheckable(True)
        self._gummy_key_eye.setToolTip("显示/隐藏 Key")
        self._gummy_key_eye.clicked.connect(
            lambda: self._gummy_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if self._gummy_key_eye.isChecked()
                else QLineEdit.EchoMode.Password
            )
        )
        self._gummy_key_eye.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.1); border: 1px solid #3d3d5c;"
            "  border-radius: 4px; font-size: 14px; padding: 0; }"
            "QPushButton:hover { background: rgba(255,255,255,0.2); }"
        )
        key_row.addWidget(self._gummy_key_eye)
        gummy_form.addRow("API Key：", key_row)

        # 地址
        gummy_addr_row = QHBoxLayout()
        self._gummy_url_input = QLineEdit()
        self._gummy_url_input.setPlaceholderText("wss://dashscope.aliyuncs.com/api-ws/v1/inference")
        self._gummy_url_input.setMinimumWidth(300)
        gummy_addr_row.addWidget(self._gummy_url_input, 1)

        self._gummy_url_reset = QPushButton("↺")
        self._gummy_url_reset.setFixedSize(32, 28)
        self._gummy_url_reset.setToolTip("重置为默认地址")
        self._gummy_url_reset.clicked.connect(
            lambda: self._gummy_url_input.setText("wss://dashscope.aliyuncs.com/api-ws/v1/inference")
        )
        self._gummy_url_reset.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.1); border: 1px solid #3d3d5c;"
            "  border-radius: 4px; font-size: 14px; padding: 0; }"
            "QPushButton:hover { background: rgba(255,255,255,0.2); }"
        )
        gummy_addr_row.addWidget(self._gummy_url_reset)
        gummy_form.addRow("API 地址：", gummy_addr_row)

        # 模型
        model_row = QHBoxLayout()
        self._gummy_model_combo = QComboBox()
        self._gummy_model_combo.addItem("gummy-realtime-v1（推荐）", "gummy-realtime-v1")
        self._gummy_model_combo.setMinimumWidth(250)
        model_row.addWidget(self._gummy_model_combo)
        model_row.addStretch()
        gummy_form.addRow("模型：", model_row)

        gummy_hint = QLabel(
            "🌐 此为 <b>翻译用 API</b>（百炼 Gummy WebSocket），负责实时语音翻译。<br>"
            "获取 Key：<a href='https://bailian.console.aliyun.com'>百炼控制台</a> → 模型广场 → Gummy-Realtime。<br>"
            "⚠ <b>与下方的 LLM API 分开配置</b>，两者互不影响。"
        )
        gummy_hint.setStyleSheet("color: #888; font-size: 11px;")
        gummy_hint.setWordWrap(True)
        gummy_hint.setOpenExternalLinks(True)
        gummy_form.addRow(gummy_hint)
        gummy_sec.content_layout().addLayout(gummy_form)
        main_layout.addWidget(gummy_sec)

        # ──── 断句延迟（默认展开）────
        delay_sec = CollapsibleSection("⏱️ 断句延迟", expanded=True)
        delay_form = QFormLayout()
        delay_form.setSpacing(6)

        delay_row = QHBoxLayout()
        self._gummy_silence_slider = QSlider(Qt.Orientation.Horizontal)
        self._gummy_silence_slider.setRange(100, 1500)
        self._gummy_silence_slider.setTickInterval(100)
        self._gummy_silence_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._gummy_silence_label = QLabel("800 ms")
        self._gummy_silence_label.setMinimumWidth(60)
        self._gummy_silence_slider.valueChanged.connect(
            lambda v: self._gummy_silence_label.setText(f"{v} ms")
        )
        delay_row.addWidget(self._gummy_silence_slider)
        delay_row.addWidget(self._gummy_silence_label)
        delay_form.addRow("静音阈值：", delay_row)

        delay_hint = QLabel(
            "说话停顿时长超过该值即断句输出翻译。<br>"
            "<b>值越小 → 翻得更快</b>，但可能在不该断的地方断句。"
        )
        delay_hint.setStyleSheet("color: #888; font-size: 11px;")
        delay_hint.setWordWrap(True)
        delay_form.addRow(delay_hint)
        delay_sec.content_layout().addLayout(delay_form)
        main_layout.addWidget(delay_sec)

        # ──── 翻译方向（默认折叠）────
        lang_sec = CollapsibleSection("🌐 翻译方向", expanded=False)
        lang_form = QFormLayout()
        lang_form.setSpacing(6)

        self._lang_preset_combo = QComboBox()
        self._lang_preset_combo.addItem("自动检测 → 中文", "auto_zh")
        self._lang_preset_combo.addItem("中文 → 英文", "zh_en")
        self._lang_preset_combo.addItem("英文 → 中文", "en_zh")
        self._lang_preset_combo.addItem("自动检测 → 英文", "auto_en")
        self._lang_preset_combo.addItem("自定义...", "custom")
        self._lang_preset_combo.setMinimumWidth(200)
        self._lang_preset_combo.currentIndexChanged.connect(self._on_lang_preset_changed)
        lang_form.addRow("翻译方向：", self._lang_preset_combo)

        self._src_lang_combo = QComboBox()
        _SRC = [("auto", "自动检测"), ("zh", "中文"), ("en", "英文"), ("ja", "日语"), ("ko", "韩语"), ("fr", "法语"), ("de", "德语"), ("es", "西班牙语"), ("ru", "俄语"), ("it", "意大利语"), ("pt", "葡萄牙语"), ("id", "印尼语"), ("ar", "阿拉伯语"), ("th", "泰语")]
        for code, label in _SRC:
            self._src_lang_combo.addItem(label, code)
        self._src_lang_combo.setMinimumWidth(150)
        self._src_lang_combo.setVisible(False)
        lang_form.addRow("源语言：", self._src_lang_combo)

        self._tgt_lang_combo = QComboBox()
        _TGT = [("zh", "中文"), ("en", "英文"), ("ja", "日语"), ("ko", "韩语"), ("yue", "粤语"), ("fr", "法语"), ("de", "德语"), ("es", "西班牙语"), ("ru", "俄语"), ("it", "意大利语"), ("pt", "葡萄牙语"), ("id", "印尼语"), ("ar", "阿拉伯语"), ("th", "泰语"), ("hi", "印地语"), ("da", "丹麦语"), ("ur", "乌尔都语"), ("tr", "土耳其语"), ("nl", "荷兰语"), ("ms", "马来语"), ("vi", "越南语")]
        for code, label in _TGT:
            self._tgt_lang_combo.addItem(label, code)
        self._tgt_lang_combo.setMinimumWidth(150)
        self._tgt_lang_combo.setVisible(False)
        lang_form.addRow("目标语言：", self._tgt_lang_combo)

        lang_hint = QLabel(
            "Gummy 实时翻译支持 14 种源语言、20 种目标语言。<br>"
            "选择「自定义...」可手动指定源/目标语言。"
        )
        lang_hint.setStyleSheet("color: #888; font-size: 11px;")
        lang_hint.setWordWrap(True)
        lang_form.addRow(lang_hint)
        lang_sec.content_layout().addLayout(lang_form)
        main_layout.addWidget(lang_sec)

        # ──── 音频输入源（默认折叠）────
        audio_sec = CollapsibleSection("🎙 音频输入源", expanded=False)
        audio_form = QFormLayout()
        audio_form.setSpacing(6)

        self._audio_source_combo = QComboBox()
        self._audio_source_combo.addItem("系统音频（扬声器回环）", "system")
        self._audio_source_combo.addItem("麦克风", "mic")
        self._audio_source_combo.setMinimumWidth(200)
        self._audio_source_combo.currentIndexChanged.connect(self._on_audio_source_changed)
        audio_form.addRow("输入源：", self._audio_source_combo)

        self._mic_device_combo = QComboBox()
        self._mic_device_combo.addItem("默认设备", -1)
        self._mic_device_combo.setMinimumWidth(200)
        self._mic_device_combo.setVisible(False)
        audio_form.addRow("麦克风设备：", self._mic_device_combo)

        audio_hint = QLabel(
            "系统音频：捕获电脑播放的声音（如会议音频）。<br>"
            "麦克风：直接录制麦克风输入。"
        )
        audio_hint.setStyleSheet("color: #888; font-size: 11px;")
        audio_hint.setWordWrap(True)
        audio_form.addRow(audio_hint)
        audio_sec.content_layout().addLayout(audio_form)
        main_layout.addWidget(audio_sec)

        # ──── 双向翻译模式（默认折叠）────
        bi_sec = CollapsibleSection("🔄 双向翻译模式（实验性）", expanded=False)
        bi_form = QFormLayout()
        bi_form.setSpacing(6)

        self._bidirectional_cb = QCheckBox("启用双向翻译")
        self._bidirectional_cb.setToolTip(
            "同时运行两条 Gummy 实例：系统音频→中文 + 麦克风→英文"
        )
        bi_form.addRow(self._bidirectional_cb)

        bi_hint = QLabel(
            "⚠ 启用后将同时启动两个 Gummy WebSocket 连接，<br>"
            "API 调用量翻倍。适用于国际会议场景。<br>"
            "系统音频 → 自动检测 → 中文翻译<br>"
            "麦克风 → 中文 → 英文翻译"
        )
        bi_hint.setStyleSheet("color: #FFB74D; font-size: 11px;")
        bi_hint.setWordWrap(True)
        bi_form.addRow(bi_hint)
        bi_sec.content_layout().addLayout(bi_form)
        main_layout.addWidget(bi_sec)

        # ──── 🤖 主题摘要 & 纠错 LLM API（默认展开）────
        api_sec = CollapsibleSection("🤖 主题摘要 & 纠错 LLM API", expanded=True)
        api_form = QFormLayout()
        api_form.setSpacing(6)

        supplier_row = QHBoxLayout()
        self._api_preset_combo = QComboBox()
        for pid, pname, purl, pmodel in API_PRESETS:
            self._api_preset_combo.addItem(pname, pid)
        self._api_preset_combo.setMinimumWidth(200)
        self._api_preset_combo.currentIndexChanged.connect(self._on_api_preset_changed)
        supplier_row.addWidget(self._api_preset_combo)
        supplier_row.addStretch()
        api_form.addRow("LLM 供应商：", supplier_row)

        self._api_url_input = QLineEdit()
        self._api_url_input.setPlaceholderText("https://api.xxx.com/v1/chat/completions")
        self._api_url_input.setMinimumWidth(350)
        api_form.addRow("API 地址：", self._api_url_input)

        key_row = QHBoxLayout()
        self._api_key_input = QLineEdit()
        self._api_key_input.setPlaceholderText("sk-xxxxxxxxxxxxxxxx")
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setMinimumWidth(250)
        key_row.addWidget(self._api_key_input, 1)

        self._show_key_btn = QPushButton("👁")
        self._show_key_btn.setFixedWidth(36)
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.setToolTip("显示/隐藏 API Key")
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)
        self._show_key_btn.setStyleSheet("""
            QPushButton { border: 1px solid #555; border-radius: 4px; background: transparent; color: #ccc; }
            QPushButton:hover { background: #333; }
            QPushButton:checked { background: #444; color: #fff; }
        """)
        key_row.addWidget(self._show_key_btn)
        api_form.addRow("API Key：", key_row)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setMinimumWidth(200)
        self._model_combo.addItems(["qwen-plus", "qwen-turbo", "qwen-max", "deepseek-chat", "deepseek-reasoner", "gpt-4o-mini", "gpt-4o"])
        api_form.addRow("模型名称：", self._model_combo)

        # 测试连接按钮 + 状态标签
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("🧪 测试连接")
        self._test_btn.setFixedWidth(120)
        self._test_btn.setToolTip("用当前 API 配置发送一条测试消息")
        self._test_btn.clicked.connect(self._test_llm_connection)
        self._test_btn.setStyleSheet("""
            QPushButton {
                padding: 6px 14px; border: 1px solid #5a9eff;
                border-radius: 5px; background: rgba(90,158,255,0.15);
                color: #5a9eff; font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(90,158,255,0.3); color: #fff;
            }
            QPushButton:disabled {
                border-color: #555; color: #666;
                background: rgba(255,255,255,0.05);
            }
        """)
        test_row.addWidget(self._test_btn)

        self._test_status = QLabel("")
        self._test_status.setStyleSheet("color: #888; font-size: 12px;")
        self._test_status.setWordWrap(True)
        test_row.addWidget(self._test_status, 1)
        api_form.addRow(test_row)

        api_hint = QLabel(
            "🧠 此 Key 专供 <b>主题摘要</b> 和 <b>上下文纠错</b> 使用。<br>"
            "推荐填入你的 <b>DeepSeek</b> Key（便宜又好用）。<br>"
            "⚠️ <b>不影响</b> Gummy 实时翻译（翻译用百炼 WebSocket Key）。"
        )
        api_hint.setStyleSheet("color: #888; font-size: 11px;")
        api_hint.setWordWrap(True)
        api_form.addRow(api_hint)
        api_sec.content_layout().addLayout(api_form)
        main_layout.addWidget(api_sec)

        # ──── 按钮 ────
        main_layout.addStretch()
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 20px; border: 1px solid #555;
                border-radius: 6px; background: transparent; color: #ccc;
            }
            QPushButton:hover { background: #333; }
        """)

        save_btn = QPushButton("💾 保存")
        save_btn.clicked.connect(self._save_settings)
        save_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 20px; border: none; border-radius: 6px;
                background: #2ecc71; color: #fff; font-weight: bold;
            }
            QPushButton:hover { background: #27ae60; }
        """)

        self._save_status = QLabel("")
        self._save_status.setStyleSheet("color: #888; font-size: 12px;")

        btn_layout.addWidget(self._save_status, 1)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        main_layout.addLayout(btn_layout)

    # ── 当前值加载 ──

    def _load_current_values(self) -> None:
        gummy_silence = int(CONFIG.get("gummy_max_end_silence", "800"))
        gummy_silence = max(100, min(1500, gummy_silence))
        self._gummy_silence_slider.setValue(gummy_silence)

        # Gummy 翻译 API 设置
        gummy_key = CONFIG.get("api_key", "")
        if gummy_key:
            self._gummy_key_input.setText(gummy_key)
        gummy_url = CONFIG.get("api_url", "wss://dashscope.aliyuncs.com/api-ws/v1/inference")
        self._gummy_url_input.setText(gummy_url)
        gummy_model = CONFIG.get("gummy_model", "gummy-realtime-v1")
        idx = self._gummy_model_combo.findData(gummy_model)
        if idx >= 0:
            self._gummy_model_combo.setCurrentIndex(idx)

        # LLM API 设置（摘要 & 纠错）
        api_url = CONFIG.get("llm_api_url", "")
        api_key = CONFIG.get("llm_api_key", "")

        # 匹配预设
        preset_found = False
        for i, (pid, _, purl, _) in enumerate(API_PRESETS):
            if purl and api_url == purl:
                self._api_preset_combo.setCurrentIndex(i)
                preset_found = True
                break
        if not preset_found:
            self._api_preset_combo.setCurrentIndex(3)  # custom
            if api_url:
                self._api_url_input.setText(api_url)
        if api_key:
            self._api_key_input.setText(api_key)

        model = CONFIG.get("llm_model", "qwen-plus")
        self._model_combo.setCurrentText(model)

        # 翻译方向
        src = CONFIG.get("gummy_source_language", "auto")
        tgt = CONFIG.get("gummy_target_language", "zh")
        _MAP = {
            ("auto", "zh"): "auto_zh",
            ("zh", "en"): "zh_en",
            ("en", "zh"): "en_zh",
            ("auto", "en"): "auto_en",
        }
        preset = _MAP.get((src, tgt), "custom")
        idx = self._lang_preset_combo.findData(preset)
        if idx < 0:
            idx = 4  # custom
        self._lang_preset_combo.setCurrentIndex(idx)
        self._src_lang_combo.setCurrentIndex(
            max(self._src_lang_combo.findData(src), 0)
        )
        self._tgt_lang_combo.setCurrentIndex(
            max(self._tgt_lang_combo.findData(tgt), 0)
        )
        self._src_lang_combo.setVisible(preset == "custom")
        self._tgt_lang_combo.setVisible(preset == "custom")

        # 音频输入源
        audio_src = CONFIG.get("audio_source", "system")
        idx = self._audio_source_combo.findData(audio_src)
        if idx < 0:
            idx = 0
        self._audio_source_combo.setCurrentIndex(idx)

        # 枚举麦克风设备
        try:
            from core.audio_capture import AudioCapture
            devices = AudioCapture.list_input_devices()
            self._mic_device_combo.clear()
            self._mic_device_combo.addItem("默认设备", -1)
            for dev in devices:
                self._mic_device_combo.addItem(dev["name"], dev["index"])
            saved_dev = int(CONFIG.get("audio_input_device", "-1"))
            di = self._mic_device_combo.findData(saved_dev)
            if di < 0:
                di = 0
            self._mic_device_combo.setCurrentIndex(di)
        except Exception as e:
            print(f"[settings] 枚举麦克风失败: {e}")

        self._mic_device_combo.setVisible(audio_src == "mic")

        # 双向翻译模式
        bi = CONFIG.get("bidirectional_enabled", "false")
        self._bidirectional_cb.setChecked(bi.lower() in ("true", "1", "yes"))

    # ── 属性 ──

    @property
    def needs_engine_restart(self) -> bool:
        return self._needs_restart

    # ── 事件处理 ──

    def _on_api_preset_changed(self, index: int) -> None:
        """选择 API 预设时自动填充地址和推荐模型"""
        pid = self._api_preset_combo.itemData(index)
        for p, _, purl, pmodel in API_PRESETS:
            if p == pid:
                if purl:
                    self._api_url_input.setText(purl)
                if pmodel:
                    self._model_combo.setCurrentText(pmodel)
                return

    def _toggle_key_visibility(self, show: bool) -> None:
        """切换 API Key 显示/隐藏"""
        if show:
            self._api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText("🔒")
        else:
            self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText("👁")

    def _on_lang_preset_changed(self, index: int) -> None:
        """翻译方向预设切换"""
        preset = self._lang_preset_combo.itemData(index)
        is_custom = (preset == "custom")
        self._src_lang_combo.setVisible(is_custom)
        self._tgt_lang_combo.setVisible(is_custom)
        _MAP = {
            "auto_zh": ("auto", "zh"),
            "zh_en": ("zh", "en"),
            "en_zh": ("en", "zh"),
            "auto_en": ("auto", "en"),
        }
        if preset in _MAP:
            src, tgt = _MAP[preset]
            self._src_lang_combo.setCurrentIndex(
                max(self._src_lang_combo.findData(src), 0)
            )
            self._tgt_lang_combo.setCurrentIndex(
                max(self._tgt_lang_combo.findData(tgt), 0)
            )

    def _on_audio_source_changed(self, index: int) -> None:
        """音频输入源切换：麦克风时显示设备下拉框"""
        src = self._audio_source_combo.itemData(index)
        self._mic_device_combo.setVisible(src == "mic")
        if src == "mic":
            try:
                from core.audio_capture import AudioCapture
                devices = AudioCapture.list_input_devices()
                self._mic_device_combo.clear()
                self._mic_device_combo.addItem("默认设备", -1)
                for dev in devices:
                    self._mic_device_combo.addItem(dev["name"], dev["index"])
            except Exception as e:
                print(f"[settings] 枚举麦克风失败: {e}")

    # ── 测试连接 ──

    def _test_llm_connection(self) -> None:
        """发送一条最小化测试消息到配置的 LLM API"""
        url = self._api_url_input.text().strip()
        key = self._api_key_input.text().strip()
        model = self._model_combo.currentText().strip()

        if not url:
            self._test_status.setText("❌ 请填写 API 地址")
            self._test_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")
            return
        if not key:
            self._test_status.setText("❌ 请填写 API Key")
            self._test_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")
            return

        self._test_btn.setEnabled(False)
        self._test_btn.setText("⏳ 测试中...")
        self._test_status.setText("")
        self._test_status.repaint()

        try:
            import requests
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": "ping"}
                    ],
                    "max_tokens": 5,
                },
                timeout=10,
            )

            if resp.status_code == 200:
                data = resp.json()
                if "choices" in data and len(data["choices"]) > 0:
                    reply = data["choices"][0].get("message", {}).get("content", "")
                    self._test_status.setText(f"✅ 连接成功！模型回复：\"{reply.strip()}\"")
                    self._test_status.setStyleSheet("color: #2ecc71; font-size: 12px;")
                else:
                    self._test_status.setText(f"✅ 连接成功（HTTP 200），但响应格式异常")
                    self._test_status.setStyleSheet("color: #f0a040; font-size: 12px;")
            elif resp.status_code == 401:
                self._test_status.setText("❌ 认证失败 — API Key 无效或已过期")
                self._test_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")
            elif resp.status_code == 403:
                self._test_status.setText("❌ 权限不足 — 请检查 API Key 是否有调用权限")
                self._test_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")
            elif resp.status_code == 404:
                self._test_status.setText("❌ 接口不存在 — 请检查 API 地址和模型名称")
                self._test_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")
            elif resp.status_code == 429:
                self._test_status.setText("⚠️ 请求过于频繁，请稍后重试")
                self._test_status.setStyleSheet("color: #f0a040; font-size: 12px;")
            else:
                err_msg = resp.text[:200] if resp.text else "未知错误"
                self._test_status.setText(f"❌ HTTP {resp.status_code}：{err_msg}")
                self._test_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")
        except Exception as e:
            msg = str(e)[:200]
            if "Connection" in msg or "resolve" in msg.lower():
                self._test_status.setText(f"❌ 网络不通 — 无法连接 {url.split('/')[2]}")
            elif "timeout" in msg.lower():
                self._test_status.setText("❌ 连接超时 — API 无响应（10s）")
            else:
                self._test_status.setText(f"❌ 错误：{msg}")
            self._test_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")
        finally:
            self._test_btn.setEnabled(True)
            self._test_btn.setText("🧪 测试连接")

    # ── 保存（智能检测变更类型）──

    def _save_settings(self) -> None:
        """保存配置，自动判断是否需要重启引擎"""
        new_silence = self._gummy_silence_slider.value()

        # Gummy 翻译 API
        gummy_key = self._gummy_key_input.text().strip()
        gummy_url = self._gummy_url_input.text().strip()
        gummy_model = self._gummy_model_combo.currentData() or "gummy-realtime-v1"

        # LLM API（摘要 & 纠错）
        llm_key = self._api_key_input.text().strip()
        llm_url = self._api_url_input.text().strip()
        llm_model = self._model_combo.currentText().strip()

        # 翻译方向
        src_lang = self._src_lang_combo.currentData() or "auto"
        tgt_lang = self._tgt_lang_combo.currentData() or "zh"

        # 音频输入源
        audio_src = self._audio_source_combo.currentData() or "system"
        audio_dev = self._mic_device_combo.currentData()
        if audio_dev is None:
            audio_dev = -1

        # 双向翻译模式
        bi = "true" if self._bidirectional_cb.isChecked() else "false"

        # ── 检测哪些配置发生了变化 ──
        engine_changed = (
            gummy_key != self._orig_gummy_key
            or gummy_url != self._orig_gummy_url
            or gummy_model != self._orig_gummy_model
            or str(new_silence) != str(self._orig_silence)
            or src_lang != self._orig_src_lang
            or tgt_lang != self._orig_tgt_lang
            or audio_src != self._orig_audio_src
            or str(audio_dev) != str(self._orig_audio_dev)
            or bi != self._orig_bi
        )
        llm_changed = (
            llm_key != self._orig_llm_key
            or llm_url != self._orig_llm_url
            or llm_model != self._orig_llm_model
        )

        try:
            save_config({
                "api_key": gummy_key,
                "api_url": gummy_url,
                "gummy_model": gummy_model,
                "gummy_max_end_silence": str(new_silence),
                "gummy_source_language": src_lang,
                "gummy_target_language": tgt_lang,
                "audio_source": audio_src,
                "audio_input_device": str(audio_dev),
                "bidirectional_enabled": bi,
                "llm_api_key": llm_key,
                "llm_api_url": llm_url,
                "llm_model": llm_model,
            })
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"无法写入配置文件：{e}")
            return

        # ── 刷新原始值（防止重复保存时误判）──
        self._orig_gummy_key = gummy_key
        self._orig_gummy_url = gummy_url
        self._orig_gummy_model = gummy_model
        self._orig_silence = new_silence
        self._orig_src_lang = src_lang
        self._orig_tgt_lang = tgt_lang
        self._orig_audio_src = audio_src
        self._orig_audio_dev = str(audio_dev)
        self._orig_bi = bi
        self._orig_llm_key = llm_key
        self._orig_llm_url = llm_url
        self._orig_llm_model = llm_model

        # ── 通知主程序 ──
        if llm_changed:
            self.llm_saved.emit()

        if engine_changed:
            self.engine_restart_needed.emit()
            self._save_status.setText("⚠ 翻译/音频设置已保存，需<b>重启引擎</b>生效")
            self._save_status.setStyleSheet("color: #f0a040; font-size: 12px;")
        elif llm_changed:
            self._save_status.setText("✅ LLM 配置已保存并即时生效")
            self._save_status.setStyleSheet("color: #2ecc71; font-size: 12px;")
        else:
            self._save_status.setText("✅ 已保存（无变更）")
            self._save_status.setStyleSheet("color: #888; font-size: 12px;")
