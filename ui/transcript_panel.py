# ui/transcript_panel.py — 竖屏翻译记录面板
# 功能：双语对照历史记录 · 工作进程状态 · 自动滚动 · 可自由拖动定位
# 设计：竖屏窄窗（320x600），暗色半透明，玻璃质感

from __future__ import annotations

from typing import List, Optional
from datetime import datetime

try:
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QScrollArea, QFrame, QPushButton, QSizePolicy, QSizeGrip,
    )
    from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QRect
    from PyQt6.QtGui import QFont, QCursor
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False

from core.translator import TranslationResult, STATUS_PARTIAL, STATUS_FINAL


# ── 样式常量 ──
PANEL_BG = "rgba(10, 12, 18, 0.92)"
BORDER_COLOR = "rgba(255, 255, 255, 0.08)"
TITLE_BAR_BG = "rgba(20, 24, 36, 0.95)"
ENTRY_BG_EVEN = "rgba(255, 255, 255, 0.03)"
ENTRY_BG_ODD = "rgba(255, 255, 255, 0.06)"
ENTRY_BORDER = "rgba(255, 255, 255, 0.05)"

# 英文译中文方向（系统音频 → auto→zh / en→zh）
TEXT_SRC_TO_ZH = "#A0A8C0"        # 原文色
TEXT_ZH_TGT = "#E8ECF4"          # 中文译文字色
ACCENT_TO_ZH = "#00FF88"         # 绿 — 英→中
BORDER_TO_ZH = "rgba(0, 255, 136, 0.3)"

# 中文译英文方向（麦克风 → zh→en）
TEXT_SRC_ZH = "#C8CCE0"          # 中文原文色
TEXT_EN_TGT = "#88C8FF"          # 英文译文字色
ACCENT_TO_EN = "#66BBFF"         # 蓝 — 中→英
BORDER_TO_EN = "rgba(102, 187, 255, 0.3)"

TEXT_TIME = "#5A6078"       # 时间戳色
TEXT_STATUS_IDLE = "#5A6078"
TEXT_STATUS_STREAMING = "#00FF88"
TEXT_STATUS_ERROR = "#FF6B6B"
ACCENT_GREEN = "#00FF88"


class _PanelSignalBridge(QObject):
    """线程安全的信号桥接器"""
    new_entry = pyqtSignal(str, str, str, str, str)  # (time, src, tgt, status, direction)
    status_update = pyqtSignal(str)              # engine status text
    partial_update = pyqtSignal(str, str)        # (en, zh) 实时流式中间结果


class TranscriptEntry(QFrame):
    """单条翻译记录卡片 — 根据翻译方向（英→中 / 中→英）自动调整标签和颜色"""

    def __init__(self, timestamp: str, src_text: str, tgt_text: str, status: str,
                 direction: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)

        # 判断方向：含 "→en" 即为中→英，否则英→中
        is_to_en = direction.endswith("→en") or direction.endswith("->en")

        if is_to_en:
            src_label_text = "🇨🇳 中文"
            tgt_label_text = "🇬🇧 English"
            src_color = TEXT_SRC_ZH
            tgt_color = TEXT_EN_TGT
            accent = ACCENT_TO_EN
            border_color = BORDER_TO_EN
        else:
            src_label_text = "🌐 原文"
            tgt_label_text = "🇨🇳 中文"
            src_color = TEXT_SRC_TO_ZH
            tgt_color = TEXT_ZH_TGT
            accent = ACCENT_TO_ZH
            border_color = BORDER_TO_ZH

        bg = ENTRY_BG_EVEN
        self.setStyleSheet(f"""
            TranscriptEntry {{
                background: {bg};
                border-left: 3px solid {border_color};
                border-bottom: 1px solid {ENTRY_BORDER};
                border-radius: 6px;
                margin: 2px 4px;
                padding: 6px 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        # 时间戳 + 方向徽章
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        time_label = QLabel(timestamp)
        time_label.setFont(QFont("Consolas", 9))
        time_label.setStyleSheet(f"color: {TEXT_TIME};")
        header.addWidget(time_label)

        header.addStretch()

        # 方向徽章
        badge = QLabel(f" {direction} ")
        badge.setFont(QFont("Consolas", 8))
        badge.setStyleSheet(
            f"color: {accent}; background: rgba(255,255,255,0.05);"
            f"border-radius: 3px; padding: 1px 4px;"
        )
        header.addWidget(badge)

        layout.addLayout(header)

        # 源语言原文
        src_label = QLabel(f"{src_label_text}：{src_text}")
        src_label.setFont(QFont("Segoe UI", 11))
        src_label.setStyleSheet(f"color: {src_color};")
        src_label.setWordWrap(True)
        layout.addWidget(src_label)

        # 译文（加粗）
        tgt_label = QLabel(f"{tgt_label_text}：{tgt_text}")
        tgt_label.setFont(QFont("Microsoft YaHei", 12))
        final_color = accent if status == STATUS_FINAL else tgt_color
        tgt_label.setStyleSheet(f"color: {final_color}; font-weight: bold;")
        tgt_label.setWordWrap(True)
        layout.addWidget(tgt_label)


class TranscriptPanel(QWidget):
    """
    竖屏翻译记录面板。
    ┌──────────────┐
    │ 翻译记录  ●🟢 │  ← 标题栏 + 状态指示灯
    │──────────────│
    │ 00:05        │
    │ EN: Hello... │  ← 滚动记录区
    │ ZH: 你好...  │
    │──────────────│
    │ 00:12        │
    │ EN: This is..│
    │ ZH: 这是...  │
    │    ...       │
    │──────────────│
    │ ⬆ 自动滚动  │  ← 底部控制栏
    └──────────────┘
    """

    _MAX_ENTRIES = 200  # 最多保存 200 条记录

    def __init__(self) -> None:
        if not PYQT_AVAILABLE:
            print("[transcript_panel] PyQt6 不可用")
            super().__init__()
            return

        super().__init__()
        self._signal = _PanelSignalBridge()
        self._entries: List[dict] = []
        self._auto_scroll = True
        self._status_text = "就绪"

        # ── 边缘拖拽缩放 ──
        self._resize_margin = 8           # 边缘检测像素
        self._resizing = False            # 是否正在缩放
        self._resize_edge = None          # 当前拖拽边缘
        self._resize_start_pos = None     # 缩放起始鼠标全局坐标
        self._resize_start_geo = None     # 缩放起始窗口几何

        # 连接信号
        self._signal.new_entry.connect(self._on_new_entry)
        self._signal.status_update.connect(self._on_status_update)
        self._signal.partial_update.connect(self._on_partial_update)

        self._setup_window()
        self._setup_ui()

    # ── 窗口属性 ──

    def _setup_window(self) -> None:
        """配置竖屏面板窗口"""
        self.setWindowTitle("翻译记录 — AI LiveTranslate Pro")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(300, 400)
        self.resize(320, 620)
        # 初始位置：屏幕右侧
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().geometry()
        self.move(
            screen.width() - self.width() - 20,
            (screen.height() - self.height()) // 2,
        )

    # ── UI 构建 ──

    def _setup_ui(self) -> None:
        """构建竖屏面板布局"""
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # ── 标题栏 ──
        title_bar = QFrame()
        title_bar.setStyleSheet(f"background: {TITLE_BAR_BG}; border-radius: 8px;")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(12, 6, 12, 6)

        # 拖动提示 + 标题
        title_label = QLabel("📋 翻译记录")
        title_label.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #E8ECF4;")
        title_layout.addWidget(title_label)

        title_layout.addStretch()

        # 状态指示灯
        self._status_dot = QLabel("●")
        self._status_dot.setFont(QFont("Segoe UI", 10))
        self._status_dot.setStyleSheet(f"color: {TEXT_STATUS_IDLE};")
        title_layout.addWidget(self._status_dot)

        self._status_label = QLabel("就绪")
        self._status_label.setFont(QFont("Microsoft YaHei", 9))
        self._status_label.setStyleSheet(f"color: {TEXT_STATUS_IDLE};")
        title_layout.addWidget(self._status_label)

        # 最小化按钮
        btn_min = QPushButton("−")
        btn_min.setFixedSize(24, 24)
        btn_min.setStyleSheet(
            "QPushButton { background: transparent; color: #A0A8C0; border: none; font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { color: #FFD700; }"
        )
        btn_min.clicked.connect(self.showMinimized)
        title_layout.addWidget(btn_min)

        # 隐藏按钮
        btn_hide = QPushButton("×")
        btn_hide.setFixedSize(24, 24)
        btn_hide.setStyleSheet(
            "QPushButton { background: transparent; color: #A0A8C0; border: none; font-size: 16px; }"
            "QPushButton:hover { color: #FF6B6B; }"
        )
        btn_hide.clicked.connect(self.hide)
        title_layout.addWidget(btn_hide)

        root.addWidget(title_bar)

        # ── 滚动内容区 ──
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet(f"""
            QScrollArea {{ background: {PANEL_BG}; border: 1px solid {BORDER_COLOR}; border-radius: 8px; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.15); border-radius: 3px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: rgba(255, 255, 255, 0.25); }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        # 内容容器
        self._content_widget = QWidget()
        self._content_widget.setStyleSheet(f"background: {PANEL_BG};")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(2, 4, 2, 4)
        self._content_layout.setSpacing(0)
        self._content_layout.addStretch()  # 底部弹簧，让内容从顶部排列

        self._scroll_area.setWidget(self._content_widget)
        root.addWidget(self._scroll_area, stretch=1)

        # ── 底部控制栏 ──
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet(f"background: {TITLE_BAR_BG}; border-radius: 8px;")
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(12, 6, 12, 6)

        # 记录计数
        self._count_label = QLabel("0 条记录")
        self._count_label.setFont(QFont("Microsoft YaHei", 9))
        self._count_label.setStyleSheet(f"color: {TEXT_TIME};")
        bottom_layout.addWidget(self._count_label)

        bottom_layout.addStretch()

        # 自动滚动切换
        self._btn_autoscroll = QPushButton("⬇ 自动滚动")
        self._btn_autoscroll.setCheckable(True)
        self._btn_autoscroll.setChecked(True)
        self._btn_autoscroll.setFont(QFont("Microsoft YaHei", 9))
        self._btn_autoscroll.setFixedHeight(26)
        self._btn_autoscroll.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {ACCENT_GREEN}; border: 1px solid {ACCENT_GREEN}; border-radius: 4px; padding: 2px 10px; }}
            QPushButton:checked {{ background: {ACCENT_GREEN}; color: #000; }}
            QPushButton:hover {{ background: rgba(0, 255, 136, 0.1); }}
        """)
        self._btn_autoscroll.clicked.connect(self._toggle_autoscroll)
        bottom_layout.addWidget(self._btn_autoscroll)

        # 清空按钮
        btn_clear = QPushButton("清空")
        btn_clear.setFixedHeight(26)
        btn_clear.setFont(QFont("Microsoft YaHei", 9))
        btn_clear.setStyleSheet(
            "QPushButton { background: transparent; color: #FF6B6B; border: 1px solid #FF6B6B; border-radius: 4px; padding: 2px 10px; }"
            "QPushButton:hover { background: rgba(255, 107, 107, 0.1); }"
        )
        btn_clear.clicked.connect(self._clear_all)
        bottom_layout.addWidget(btn_clear)

        # 右下角缩放手柄
        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        grip.setStyleSheet(
            "QSizeGrip { background: rgba(255,255,255,0.1); border-radius: 2px; }"
            "QSizeGrip:hover { background: rgba(255,255,255,0.3); }"
        )
        bottom_layout.addWidget(grip)

        root.addWidget(bottom_bar)

    # ── 鼠标拖动 + 边缘缩放 ──

    def _get_resize_edge(self, pos) -> Optional[str]:
        """根据鼠标位置判断是否在窗口边缘（用于缩放）"""
        rect = self.rect()
        m = self._resize_margin
        x, y = pos.x(), pos.y()
        r, b = rect.width(), rect.height()

        left = x <= m
        right = x >= r - m
        top = y <= m
        bottom = y >= b - m

        if left and top:       return "topleft"
        if right and top:      return "topright"
        if left and bottom:    return "bottomleft"
        if right and bottom:   return "bottomright"
        if left:               return "left"
        if right:              return "right"
        if top:                return "top"
        if bottom:             return "bottom"
        return None

    def _cursor_for_edge(self, edge: Optional[str]) -> Qt.CursorShape:
        """根据边缘返回对应光标形状"""
        if edge in ("left", "right"):
            return Qt.CursorShape.SizeHorCursor
        if edge in ("top", "bottom"):
            return Qt.CursorShape.SizeVerCursor
        if edge in ("topleft", "bottomright"):
            return Qt.CursorShape.SizeFDiagCursor
        if edge in ("topright", "bottomleft"):
            return Qt.CursorShape.SizeBDiagCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event):
        """记录拖动/缩放起始位置"""
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        pos = event.position().toPoint()
        edge = self._get_resize_edge(pos)

        if edge and not self.isMaximized():
            # 边缘拖拽：开始缩放
            self._resizing = True
            self._resize_edge = edge
            self._resize_start_pos = event.globalPosition().toPoint()
            self._resize_start_geo = self.geometry()
            self.setCursor(self._cursor_for_edge(edge))
            event.accept()
        else:
            # 标题栏区域拖动移动窗口
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._resizing = False
            self._resize_edge = None
            event.accept()

    def mouseMoveEvent(self, event):
        """拖动窗口 / 缩放窗口"""
        if self._resizing and self._resize_edge:
            # 执行缩放
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            geo = QRect(self._resize_start_geo)
            edge = self._resize_edge

            if "left" in edge:
                geo.setLeft(min(geo.left() + delta.x(), geo.right() - self.minimumWidth()))
            if "right" in edge:
                geo.setRight(max(geo.right() + delta.x(), geo.left() + self.minimumWidth()))
            if "top" in edge:
                geo.setTop(min(geo.top() + delta.y(), geo.bottom() - self.minimumHeight()))
            if "bottom" in edge:
                geo.setBottom(max(geo.bottom() + delta.y(), geo.top() + self.minimumHeight()))

            self.setGeometry(geo)
            event.accept()
        elif hasattr(self, '_drag_pos') and event.buttons() == Qt.MouseButton.LeftButton:
            # 移动窗口
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            # 仅悬停时更新光标形状
            pos = event.position().toPoint()
            edge = self._get_resize_edge(pos)
            if edge and not self.isMaximized():
                self.setCursor(self._cursor_for_edge(edge))
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """结束缩放"""
        self._resizing = False
        self._resize_edge = None
        self._resize_start_pos = None
        self._resize_start_geo = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    # ── 公共接口（线程安全）──

    def push_result(self, result: TranslationResult) -> None:
        """
        推送翻译结果到记录面板。
        仅 FINAL 结果写入历史；PARTIAL 结果只更新底部实时预览。
        """
        if not PYQT_AVAILABLE:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        if result.status == STATUS_FINAL:
            direction = getattr(result, "direction", "")
            self._signal.new_entry.emit(ts, result.original, result.text, result.status, direction)
        else:
            self._signal.partial_update.emit(result.original, result.text)

    def set_engine_status(self, status_text: str, is_active: bool = True) -> None:
        """更新工作进程状态"""
        if not PYQT_AVAILABLE:
            return
        self._signal.status_update.emit(status_text)
        # 颜色切换在主线程完成

    # ── 信号处理槽 ──

    def _on_new_entry(self, ts: str, src: str, tgt: str, status: str, direction: str = "") -> None:
        """添加一条新记录到列表"""
        entry_data = {"time": ts, "src": src, "tgt": tgt, "status": status, "direction": direction}
        self._entries.append(entry_data)

        # 限制最大条目数
        if len(self._entries) > self._MAX_ENTRIES:
            self._entries = self._entries[-self._MAX_ENTRIES:]
            # 移除最旧的 widget
            self._trim_old_entries()

        # 插入到 stretch 之前（stretch 在最底部）
        card = TranscriptEntry(ts, src, tgt, status, direction)
        insert_pos = self._content_layout.count() - 1  # stretch 前面
        self._content_layout.insertWidget(insert_pos, card)

        # 更新计数
        self._count_label.setText(f"{len(self._entries)} 条记录")

        # 自动滚动到底部
        if self._auto_scroll:
            QTimer.singleShot(50, self._scroll_to_bottom)

    def _on_partial_update(self, en: str, zh: str) -> None:
        """流式中间结果：显示在最后一张卡片的底部（预览区）"""
        # 简单实现：更新 engine 状态栏为当前流式文本
        preview = f"{en} → {zh}" if en and zh else en or zh
        self._status_label.setText(preview[:40] + ("…" if len(preview) > 40 else ""))

    def _on_status_update(self, status_text: str) -> None:
        """更新标题栏状态"""
        self._status_text = status_text
        self._status_label.setText(status_text)

        # 根据状态切换指示灯颜色
        if "连接" in status_text or "connecting" in status_text.lower():
            color = "#FFB74D"  # 橙色
        elif "流式" in status_text or "streaming" in status_text.lower() or "工作中" in status_text:
            color = ACCENT_GREEN
        elif "错误" in status_text or "失败" in status_text or "error" in status_text.lower():
            color = TEXT_STATUS_ERROR
        else:
            color = TEXT_STATUS_IDLE

        self._status_dot.setStyleSheet(f"color: {color};")
        self._status_label.setStyleSheet(f"color: {color};")

    # ── UI 交互 ──

    def _toggle_autoscroll(self) -> None:
        """切换自动滚动"""
        self._auto_scroll = self._btn_autoscroll.isChecked()
        if self._auto_scroll:
            self._btn_autoscroll.setText("⬇ 自动滚动")
            self._scroll_to_bottom()
        else:
            self._btn_autoscroll.setText("⬆ 手动滚动")

    def _scroll_to_bottom(self) -> None:
        """滚动到底部"""
        scrollbar = self._scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_all(self) -> None:
        """清空所有记录"""
        # 移除所有 entry widget（保留 stretch）
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._entries.clear()
        self._count_label.setText("0 条记录")

    def _trim_old_entries(self) -> None:
        """移除超出 MAX_ENTRIES 的旧 widget"""
        excess = self._content_layout.count() - 1 - self._MAX_ENTRIES  # -1 for stretch
        while excess > 0 and self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            excess -= 1


# ── PyQt6 不可用时的空壳 ──
if not PYQT_AVAILABLE:
    class TranscriptPanel:
        def __init__(self):
            print("[transcript_panel] PyQt6 未安装，记录面板不可用")
        def push_result(self, result): pass
        def set_engine_status(self, status_text, is_active=True): pass
        def show(self): pass
        def hide(self): pass
