# ui/subtitle_window.py — PyQt6 悬浮字幕窗口
# 实现"灰-白-绿"动态字幕特效 + 右侧主题摘要栏
# v0.5.0 — 窗口可拖动/缩放 + 右键菜单自定义背景/字体颜色

from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

try:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
        QPushButton, QFrame, QColorDialog, QMenu, QSizeGrip,
        QDialog, QSlider, QFormLayout, QDialogButtonBox,
    )
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint, QRect, QSettings, QEvent
    from PyQt6.QtGui import QFont, QAction, QCursor, QColor
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    print("[subtitle_window] 警告：PyQt6 未安装，UI 不可用")

from core.translator import TranslationResult, STATUS_PARTIAL, STATUS_FINAL


# 语义颜色常量（不可自定义 — 标志翻译状态）
COLOR_PARTIAL = "#BBBBBB"   # 中间结果：浅灰（AI 正在思考）
COLOR_FINAL = "#00FF88"     # 最终结果：绿色（修正完成瞬间）
COLOR_KEYWORD = "#FFD700"   # 主题关键词：金黄色

# 设置文件路径
_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "subtitle_settings.json")

# 默认颜色
_DEFAULT_BG = "rgba(0,0,0,0.88)"
_DEFAULT_FONT_COLOR = "#FFFFFF"
_DEFAULT_FONT_SIZE = 22
_DEFAULT_WINDOW_W = 1200
_DEFAULT_WINDOW_H = 200


def _load_settings() -> dict:
    """加载用户颜色偏好"""
    defaults = {
        "bg_color": _DEFAULT_BG,
        "font_color": _DEFAULT_FONT_COLOR,
        "font_size": _DEFAULT_FONT_SIZE,
        "window_w": _DEFAULT_WINDOW_W,
        "window_h": _DEFAULT_WINDOW_H,
    }
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            defaults.update(saved)
    except Exception:
        pass
    return defaults


def _save_settings(data: dict) -> None:
    """保存用户颜色偏好"""
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[subtitle_window] 保存设置失败：{e}")


# ── 边缘检测常量 ──
_EDGE_MARGIN = 8  # 边缘触发缩放的像素宽度

# 边缘区域枚举
_EDGE_NONE = 0
_EDGE_LEFT = 1
_EDGE_RIGHT = 2
_EDGE_TOP = 4
_EDGE_BOTTOM = 8
_EDGE_TOPLEFT = _EDGE_TOP | _EDGE_LEFT
_EDGE_TOPRIGHT = _EDGE_TOP | _EDGE_RIGHT
_EDGE_BOTTOMLEFT = _EDGE_BOTTOM | _EDGE_LEFT
_EDGE_BOTTOMRIGHT = _EDGE_BOTTOM | _EDGE_RIGHT


if PYQT_AVAILABLE:

    class _SignalBridge(QObject):
        """线程安全的信号桥接器（子线程更新 UI 必须通过信号）"""
        subtitle_updated = pyqtSignal(str, str)     # (text, color) — 系统音频流
        subtitle2_updated = pyqtSignal(str, str)    # (text, color) — 麦克风流
        keywords_updated = pyqtSignal(list)          # [str, ...]  # 已废弃→保留兼容
        summary_updated = pyqtSignal(str)            # 内容摘要文本
        flash_done = pyqtSignal()                    # 绿色闪烁结束，切换为白色
        flash2_done = pyqtSignal()                   # 流2 绿色闪烁结束
        correction_shown = pyqtSignal(str, str, str)  # (old, new, reason) 上下文修正
        summary_log_updated = pyqtSignal(str)        # 摘要引擎工作日志


    class SubtitleWindow(QWidget):
        """
        PyQt6 悬浮字幕窗口主类。
        布局：[左侧：字幕显示区] [右侧：主题关键词摘要栏]
        
        交互：
        - 左键拖动 → 移动窗口
        - 边缘拖拽 → 缩放窗口
        - 右键菜单 → 自定义背景/字体颜色
        """

        def __init__(self, transcript_panel=None, final_subtitle=None) -> None:
            super().__init__()
            self._transcript_panel = transcript_panel  # 竖屏记录面板引用
            self._final_subtitle = final_subtitle      # 最终译文展示窗引用
            self._on_settings_clicked = None           # 设置按钮回调（由 main.py 注入）
            self._on_pause_clicked = None             # 暂停/继续按钮回调
            self._on_stop_session_clicked = None      # 停止识别按钮回调
            self._on_exit_clicked = None              # 退出程序按钮回调
            self._paused = False                      # 当前是否暂停中
            self._signal = _SignalBridge()
            self._current_text = ""
            self._current_text2 = ""
            self._dual_mode = False
            self._flash_timer: Optional[QTimer] = None
            self._flash_timer2: Optional[QTimer] = None
            self._correction_timer: Optional[QTimer] = None
            self._clear_timer: Optional[QTimer] = None   # 超时清除字幕定时器
            self._clear_timer2: Optional[QTimer] = None

            # ── 加载用户偏好 ──
            settings = _load_settings()
            self._bg_color = settings["bg_color"]
            self._font_color = settings["font_color"]
            self._font_size = settings["font_size"]
            self._init_w = settings["window_w"]
            self._init_h = settings["window_h"]

            # ── 拖动 / 缩放状态 ──
            self._drag_pos: Optional[QPoint] = None
            self._resize_edge = _EDGE_NONE
            self._resize_start_geom: Optional[QRect] = None
            self._resize_start_pos: Optional[QPoint] = None

            # 连接信号
            self._signal.subtitle_updated.connect(self._on_subtitle_updated)
            self._signal.subtitle2_updated.connect(self._on_subtitle2_updated)
            self._signal.summary_updated.connect(self._on_summary_updated)
            self._signal.flash_done.connect(self._on_flash_done)
            self._signal.flash2_done.connect(self._on_flash2_done)
            self._signal.correction_shown.connect(self._on_correction_shown)
            self._signal.summary_log_updated.connect(self._on_summary_log_updated)

            self._setup_window()
            self._setup_ui()
            self._apply_colors()

        # ──────────────────────────────────────────
        # 窗口与 UI 初始化
        # ──────────────────────────────────────────

        def _setup_window(self) -> None:
            """配置悬浮窗 — 无边框但可拖动+缩放"""
            self.setWindowTitle("AI LiveTranslate Pro — 右键可调色")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Window
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setMinimumSize(400, 80)   # 缩小最小尺寸
            self.resize(self._init_w, self._init_h)

            # 启用鼠标追踪（用于边缘检测光标变化）
            self.setMouseTracking(True)

            # 初始位置：屏幕底部居中
            screen = QApplication.primaryScreen().geometry()
            self.move(
                (screen.width() - self.width()) // 2,
                screen.height() - self.height() - 60,
            )

        def _setup_ui(self) -> None:
            """构建 UI 布局"""
            root = QHBoxLayout(self)
            root.setContentsMargins(12, 8, 12, 8)
            root.setSpacing(12)

            # ── 左侧：字幕区 ──
            self._subtitle_frame = QFrame()
            self._subtitle_frame.setObjectName("subtitleFrame")
            self._subtitle_frame.setAutoFillBackground(True)
            self._subtitle_frame.setStyleSheet(
                f"#subtitleFrame {{ background: {self._bg_color}; border-radius: 10px; }}"
            )

            # 用 QPalette 兜底：确保 Qt 原生背景色也一致
            pal = self._subtitle_frame.palette()
            pal.setColor(self._subtitle_frame.backgroundRole(), QColor(0, 0, 0, 224))
            self._subtitle_frame.setPalette(pal)
            subtitle_layout = QVBoxLayout(self._subtitle_frame)
            subtitle_layout.setContentsMargins(16, 10, 16, 10)

            # 字幕文本标签
            self._subtitle_label = QLabel("AI LiveTranslate Pro 就绪")
            self._subtitle_label.setFont(QFont("Microsoft YaHei", self._font_size))
            self._subtitle_label.setStyleSheet(
                f"color: {self._font_color};"
                f"background: transparent;"
                f"padding: 8px 16px;"
            )
            self._subtitle_label.setWordWrap(True)
            self._subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            subtitle_layout.addWidget(self._subtitle_label)

            # 第二字幕标签（麦克风流，默认隐藏）
            self._subtitle_label2 = QLabel("")
            self._subtitle_label2.setFont(QFont("Microsoft YaHei", self._font_size - 2))
            self._subtitle_label2.setStyleSheet(
                "color: #66BBFF;"
                "background: transparent;"
                "padding: 4px 12px;"
            )
            self._subtitle_label2.setWordWrap(True)
            self._subtitle_label2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._subtitle_label2.setVisible(False)
            subtitle_layout.addWidget(self._subtitle_label2)

            # 修正提示标签（初始隐藏，修正发生时显示）
            self._correction_label = QLabel("")
            self._correction_label.setFont(QFont("Microsoft YaHei", 12))
            self._correction_label.setStyleSheet("color: #FFB74D;")
            self._correction_label.setWordWrap(True)
            self._correction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._correction_label.setVisible(False)
            subtitle_layout.addWidget(self._correction_label)

            # ── 底部按钮栏 ──
            self._toolbar_frame = QFrame()
            self._toolbar_frame.setObjectName("toolbarFrame")
            self._toolbar_frame.setStyleSheet(
                "#toolbarFrame { background: transparent; border: none; }"
            )
            self._toolbar_visible = True
            btn_layout = QHBoxLayout(self._toolbar_frame)
            btn_layout.setContentsMargins(0, 4, 0, 0)

            # ⏸ 暂停 / ▶ 继续 — 暂停识别和翻译
            self._btn_pause = QPushButton("⏸ 暂停")
            self._btn_pause.setFixedSize(80, 30)
            self._btn_pause.setCheckable(True)
            self._btn_pause.setChecked(False)
            self._btn_pause.setStyleSheet(
                "QPushButton { background: #006064; color: white; border: none;"
                "  border-radius: 5px; font-size: 12px; padding: 0;"
                "  text-align: center; }"
                "QPushButton:hover { background: #00838F; }"
                "QPushButton:checked { background: #F57F17; color: #212121;"
                "  font-weight: bold; }"
            )
            self._btn_pause.setToolTip("暂停识别和翻译（再次点击继续）")
            self._btn_pause.clicked.connect(self._on_pause_btn)
            btn_layout.addWidget(self._btn_pause)

            self._btn_transcript = QPushButton("▶ 记录")
            self._btn_transcript.setFixedSize(70, 30)
            self._btn_transcript.setCheckable(True)
            # 初始状态：未勾选（面板默认不显示）
            self._btn_transcript.setChecked(False)
            self._btn_transcript.setStyleSheet(
                "QPushButton { background: #2a4a6a; color: white; border: none;"
                "  border-radius: 5px; font-size: 12px; padding: 0;"
                "  text-align: center; }"
                "QPushButton:hover { background: #1E88E5; }"
                "QPushButton:checked { background: #0D47A1; border-left: 3px solid #64B5F6; }"
            )
            self._btn_transcript.setToolTip("点击展开/收起翻译记录面板")
            self._btn_transcript.clicked.connect(self._toggle_transcript_panel)
            btn_layout.addWidget(self._btn_transcript)

            # 最终译文展示窗切换按钮
            self._btn_final = QPushButton("📺 输出")
            self._btn_final.setFixedSize(70, 30)
            self._btn_final.setCheckable(True)
            # 初始状态：未勾选（窗口默认不显示）
            self._btn_final.setChecked(False)
            self._btn_final.setStyleSheet(
                "QPushButton { background: #2a5a3a; color: white; border: none;"
                "  border-radius: 5px; font-size: 12px; padding: 0;"
                "  text-align: center; }"
                "QPushButton:hover { background: #43A047; }"
                "QPushButton:checked { background: #1B5E20; border-left: 3px solid #81C784; }"
            )
            self._btn_final.setToolTip("点击展开/收起最终译文窗口")
            self._btn_final.clicked.connect(self._toggle_final_subtitle)
            btn_layout.addWidget(self._btn_final)

            btn_layout.addStretch()

            # 设置按钮
            self._btn_settings = QPushButton("⚙")
            self._btn_settings.setFixedSize(30, 30)
            self._btn_settings.setToolTip("打开设置")
            self._btn_settings.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.1); color: #ccc;"
                "  border: 1px solid rgba(255,255,255,0.2); border-radius: 5px;"
                "  font-size: 16px; padding: 0; text-align: center; }"
                "QPushButton:hover { background: rgba(255,255,255,0.25); }"
            )
            btn_layout.addWidget(self._btn_settings)
            self._btn_settings.clicked.connect(self._on_settings_btn)

            # ⏹ 停止识别 — 结束当前会话（生成纪要），不关闭程序
            self._btn_stop_session = QPushButton("⏹ 停止")
            self._btn_stop_session.setFixedSize(70, 30)
            self._btn_stop_session.setStyleSheet(
                "QPushButton { background: #E65100; color: white; border: none;"
                "  border-radius: 5px; font-size: 12px; padding: 0;"
                "  text-align: center; }"
                "QPushButton:hover { background: #EF6C00; }"
            )
            self._btn_stop_session.setToolTip("停止识别和翻译，生成会议纪要（程序不关闭）")
            self._btn_stop_session.clicked.connect(self._on_stop_session_btn)
            btn_layout.addWidget(self._btn_stop_session)

            # ✕ 退出 — 关闭程序
            btn_exit = QPushButton("✕ 退出")
            btn_exit.setFixedSize(70, 30)
            btn_exit.setStyleSheet(
                "QPushButton { background: #C62828; color: white; border: none;"
                "  border-radius: 5px; font-size: 12px; padding: 0;"
                "  text-align: center; }"
                "QPushButton:hover { background: #D32F2F; }"
            )
            btn_exit.clicked.connect(self._on_exit_btn)
            btn_layout.addWidget(btn_exit)

            # 最小化按钮
            btn_min = QPushButton("−")
            btn_min.setFixedSize(30, 30)
            btn_min.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.1); color: #ccc;"
                "  border: 1px solid rgba(255,255,255,0.2); border-radius: 5px;"
                "  font-size: 16px; font-weight: bold; padding: 0;"
                "  text-align: center; }"
                "QPushButton:hover { background: rgba(255,255,255,0.25); }"
            )
            btn_min.clicked.connect(self.showMinimized)
            btn_layout.addWidget(btn_min)

            subtitle_layout.addWidget(self._toolbar_frame)

            # 工具栏隐藏时的恢复提示（轻触展开）
            self._restore_hint = QLabel("⏷")
            self._restore_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._restore_hint.setFont(QFont("Microsoft YaHei", 8))
            self._restore_hint.setStyleSheet(
                "color: rgba(255,255,255,0.2); background: transparent; padding: 0;"
            )
            self._restore_hint.setToolTip("双击字幕文字可展开工具栏")
            self._restore_hint.setVisible(False)
            subtitle_layout.addWidget(self._restore_hint)

            # 双击字幕文字 → 切换工具栏显隐
            self._subtitle_label.installEventFilter(self)
            self._subtitle_label2.installEventFilter(self)

            root.addWidget(self._subtitle_frame, stretch=3)

            # ── 右侧：主题关键词摘要栏 ──
            self._summary_frame = QFrame()
            self._summary_frame.setFixedWidth(220)
            self._summary_frame.setObjectName("summaryFrame")
            self._summary_frame.setAutoFillBackground(True)
            self._summary_frame.setStyleSheet(
                f"#summaryFrame {{ background: {self._bg_color}; border-radius: 10px; }}"
            )

            pal2 = self._summary_frame.palette()
            pal2.setColor(self._summary_frame.backgroundRole(), QColor(0, 0, 0, 224))
            self._summary_frame.setPalette(pal2)
            summary_layout = QVBoxLayout(self._summary_frame)
            summary_layout.setContentsMargins(12, 8, 12, 8)

            # 标题行：折叠按钮 + 标题
            header_row = QHBoxLayout()
            self._summary_collapse_btn = QPushButton("◀")
            self._summary_collapse_btn.setFixedSize(24, 24)
            self._summary_collapse_btn.setToolTip("折叠/展开主题摘要栏")
            self._summary_collapse_btn.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.08); color: #FFD700;"
                "  border: 1px solid rgba(255,255,255,0.15); border-radius: 4px;"
                "  font-size: 12px; padding: 0; }"
                "QPushButton:hover { background: rgba(255,255,255,0.2); }"
            )
            self._summary_collapse_btn.clicked.connect(self._toggle_summary)
            header_row.addWidget(self._summary_collapse_btn)

            summary_title = QLabel("主题摘要")
            summary_title.setFont(QFont("Microsoft YaHei", 11))
            summary_title.setStyleSheet("color: #FFD700; font-weight: bold;")
            header_row.addWidget(summary_title)
            header_row.addStretch()
            summary_layout.addLayout(header_row)

            self._summary_text_label = QLabel("等待内容…")
            self._summary_text_label.setFont(QFont("Microsoft YaHei", 10))
            self._summary_text_label.setStyleSheet(
                "color: #e0e0e0; padding: 4px 0; line-height: 1.5;"
            )
            self._summary_text_label.setWordWrap(True)
            self._summary_text_label.setAlignment(Qt.AlignmentFlag.AlignTop)
            summary_layout.addWidget(self._summary_text_label, 1)

            # 工作日志标签（显示摘要引擎实时状态）
            self._summary_log_label = QLabel("⏳ 等待翻译内容...")
            self._summary_log_label.setFont(QFont("Microsoft YaHei", 8))
            self._summary_log_label.setStyleSheet(
                "color: rgba(255,255,255,0.45); padding: 4px 0 0 0;"
            )
            self._summary_log_label.setWordWrap(True)
            summary_layout.addWidget(self._summary_log_label)

            summary_layout.addStretch()

            self._summary_collapsed = False
            self._summary_full_width = 220

            root.addWidget(self._summary_frame, stretch=1)

            # ── 右下角缩放手柄 ──
            self._grip = QSizeGrip(self)
            self._grip.setFixedSize(16, 16)
            self._grip.setStyleSheet(
                "QSizeGrip { background: rgba(255,255,255,0.15); border-radius: 2px; }"
                "QSizeGrip:hover { background: rgba(255,255,255,0.35); }"
            )
            # 放到右下角
            self._grip.move(self.width() - 20, self.height() - 20)

        # ──────────────────────────────────────────
        # 颜色应用
        # ──────────────────────────────────────────

        def _apply_colors(self) -> None:
            """将所有可配置颜色应用到 UI 组件"""
            self._subtitle_frame.setStyleSheet(
                f"#subtitleFrame {{ background: {self._bg_color}; border-radius: 10px; }}"
            )
            self._summary_frame.setStyleSheet(
                f"#summaryFrame {{ background: {self._bg_color}; border-radius: 10px; }}"
            )
            # 用 QPalette 兜底：防止样式表在某些系统上不渲染
            alpha = int(self._get_bg_alpha() * 255)
            dark_bg = QColor(0, 0, 0, alpha)
            pal = self._subtitle_frame.palette()
            pal.setColor(self._subtitle_frame.backgroundRole(), dark_bg)
            self._subtitle_frame.setPalette(pal)
            pal2 = self._summary_frame.palette()
            pal2.setColor(self._summary_frame.backgroundRole(), dark_bg)
            self._summary_frame.setPalette(pal2)

            self._subtitle_label.setFont(QFont("Microsoft YaHei", self._font_size))
            self._subtitle_label.setStyleSheet(
                f"color: {self._font_color};"
                f"background: transparent;"
                f"padding: 8px 16px;"
            )

        def _apply_stable_color(self) -> None:
            """应用稳定态字体颜色（白色阶段使用用户自定义色）"""
            self._subtitle_label.setStyleSheet(
                f"color: {self._font_color};"
                f"background: transparent;"
                f"padding: 8px 16px;"
            )

        # ──────────────────────────────────────────
        # 鼠标事件 — 窗口拖动 + 边缘缩放
        # ──────────────────────────────────────────

        def _detect_edge(self, pos: QPoint) -> int:
            """检测鼠标所在的边缘区域"""
            x, y = pos.x(), pos.y()
            w, h = self.width(), self.height()
            edge = _EDGE_NONE
            if x <= _EDGE_MARGIN:
                edge |= _EDGE_LEFT
            elif x >= w - _EDGE_MARGIN:
                edge |= _EDGE_RIGHT
            if y <= _EDGE_MARGIN:
                edge |= _EDGE_TOP
            elif y >= h - _EDGE_MARGIN:
                edge |= _EDGE_BOTTOM
            return edge

        def _edge_cursor(self, edge: int) -> Qt.CursorShape:
            """根据边缘区域返回对应光标形状"""
            if edge in (_EDGE_LEFT, _EDGE_RIGHT):
                return Qt.CursorShape.SizeHorCursor
            if edge in (_EDGE_TOP, _EDGE_BOTTOM):
                return Qt.CursorShape.SizeVerCursor
            if edge in (_EDGE_TOPLEFT, _EDGE_BOTTOMRIGHT):
                return Qt.CursorShape.SizeFDiagCursor
            if edge in (_EDGE_TOPRIGHT, _EDGE_BOTTOMLEFT):
                return Qt.CursorShape.SizeBDiagCursor
            return Qt.CursorShape.ArrowCursor

        def mousePressEvent(self, event) -> None:
            """左键按下：判断拖动还是缩放"""
            if event.button() == Qt.MouseButton.LeftButton:
                edge = self._detect_edge(event.pos())
                if edge != _EDGE_NONE:
                    # 边缘缩放
                    self._resize_edge = edge
                    self._resize_start_geom = self.geometry()
                    self._resize_start_pos = event.globalPosition().toPoint()
                else:
                    # 窗口拖动
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
            else:
                super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:
            """鼠标移动：拖动窗口 / 调整大小 / 更新光标"""
            if self._drag_pos is not None:
                # 拖动窗口
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()
                return

            if self._resize_edge != _EDGE_NONE and self._resize_start_geom is not None:
                # 边缘缩放
                delta = event.globalPosition().toPoint() - self._resize_start_pos
                geom = QRect(self._resize_start_geom)
                edge = self._resize_edge

                if edge & _EDGE_LEFT:
                    geom.setLeft(min(geom.left() + delta.x(), geom.right() - self.minimumWidth()))
                if edge & _EDGE_RIGHT:
                    geom.setRight(max(geom.right() + delta.x(), geom.left() + self.minimumWidth()))
                if edge & _EDGE_TOP:
                    geom.setTop(min(geom.top() + delta.y(), geom.bottom() - self.minimumHeight()))
                if edge & _EDGE_BOTTOM:
                    geom.setBottom(max(geom.bottom() + delta.y(), geom.top() + self.minimumHeight()))

                self.setGeometry(geom)
                # 更新 grip 位置
                self._grip.move(self.width() - 20, self.height() - 20)
                event.accept()
                return

            # 没有操作中 — 更新光标提示用户可缩放边缘
            edge = self._detect_edge(event.pos())
            self.setCursor(self._edge_cursor(edge))
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            """鼠标释放：结束拖动 / 缩放"""
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = None
                self._resize_edge = _EDGE_NONE
                self._resize_start_geom = None
                self._resize_start_pos = None
                self.setCursor(Qt.CursorShape.ArrowCursor)
                # 保存窗口大小
                self._save_window_size()
                event.accept()
            else:
                super().mouseReleaseEvent(event)

        def resizeEvent(self, event) -> None:
            """窗口大小变化时更新 grip 位置"""
            super().resizeEvent(event)
            if hasattr(self, '_grip'):
                self._grip.move(self.width() - 20, self.height() - 20)

        def leaveEvent(self, event) -> None:
            """鼠标离开窗口时恢复默认光标"""
            self.setCursor(Qt.CursorShape.ArrowCursor)
            super().leaveEvent(event)

        def _save_window_size(self) -> None:
            """保存当前窗口大小"""
            try:
                settings = _load_settings()
                settings["window_w"] = self.width()
                settings["window_h"] = self.height()
                _save_settings(settings)
            except Exception:
                pass

        # ──────────────────────────────────────────
        # 右键菜单 — 自定义颜色
        # ──────────────────────────────────────────

        def contextMenuEvent(self, event) -> None:
            """右键菜单：背景色 / 字体色 / 字号 / 重置"""
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu { background: #1a1d2e; color: #E8ECF4; border: 1px solid #333; padding: 4px; }
                QMenu::item { padding: 6px 24px; border-radius: 4px; }
                QMenu::item:selected { background: #2d3148; }
            """)

            action_bg = menu.addAction("🎨 背景颜色…")
            action_font = menu.addAction("🔤 字体颜色…")
            menu.addSeparator()
            action_size_xs = menu.addAction("📏 字号：极小 (10)")
            action_size_s = menu.addAction("📏 字号：小 (14)")
            action_size_m = menu.addAction("📏 字号：中 (22)")
            action_size_l = menu.addAction("📏 字号：大 (32)")
            menu.addSeparator()
            action_reset = menu.addAction("🔄 恢复默认")

            action = menu.exec(event.globalPos())
            if action is None:
                return

            if action == action_bg:
                self._pick_bg_color()
            elif action == action_font:
                self._pick_font_color()
            elif action == action_size_xs:
                self._set_font_size(10)
            elif action == action_size_s:
                self._set_font_size(14)
            elif action == action_size_m:
                self._set_font_size(22)
            elif action == action_size_l:
                self._set_font_size(32)
            elif action == action_reset:
                self._reset_colors()

        def _pick_bg_color(self) -> None:
            """打开颜色选择器选背景色"""
            initial = QColor(self._parse_rgba_to_hex(self._bg_color))
            initial.setAlpha(int(self._get_bg_alpha() * 255))
            color = QColorDialog.getColor(
                initial, self,
                "选择字幕背景色（可调节透明度）",
                QColorDialog.ColorDialogOption.ShowAlphaChannel,
            )
            if color.isValid():
                r, g, b = color.red(), color.green(), color.blue()
                a = self._get_bg_alpha()
                self._bg_color = f"rgba({r},{g},{b},{a})"
                self._apply_colors()
                self._persist_colors()

        def _pick_font_color(self) -> None:
            """打开颜色选择器选字体颜色"""
            current = self._parse_hex(self._font_color)
            color = QColorDialog.getColor(current, self, "选择字幕字体颜色")
            if color.isValid():
                self._font_color = color.name()
                self._apply_stable_color()
                self._persist_colors()

        def _set_font_size(self, size: int) -> None:
            """设置字号并应用到 UI"""
            self._font_size = size
            self._subtitle_label.setFont(QFont("Microsoft YaHei", size))
            self._persist_colors()

        def _reset_colors(self) -> None:
            """恢复默认颜色"""
            self._bg_color = _DEFAULT_BG
            self._font_color = _DEFAULT_FONT_COLOR
            self._font_size = _DEFAULT_FONT_SIZE
            self._apply_colors()
            self._apply_stable_color()
            self._persist_colors()

        def _persist_colors(self) -> None:
            """保存颜色/字号设置到文件"""
            _save_settings({
                "bg_color": self._bg_color,
                "font_color": self._font_color,
                "font_size": self._font_size,
                "window_w": self.width(),
                "window_h": self.height(),
            })

        @staticmethod
        def _parse_rgba_to_hex(rgba: str) -> str:
            """rgba(r,g,b,a) → #RRGGBB"""
            try:
                parts = rgba.replace("rgba(", "").replace(")", "").split(",")
                r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
                return f"#{r:02x}{g:02x}{b:02x}"
            except Exception:
                return "#000000"

        @staticmethod
        def _parse_hex(hex_color: str) -> str:
            """确保是合法的 #RRGGBB"""
            if hex_color.startswith("#") and len(hex_color) >= 7:
                return hex_color[:7]
            return "#FFFFFF"

        def _get_bg_alpha(self) -> float:
            """从 bg_color 中提取 alpha 值"""
            try:
                parts = self._bg_color.replace("rgba(", "").replace(")", "").split(",")
                return float(parts[3])
            except Exception:
                return 0.75

        # ──────────────────────────────────────────
        # 工具栏显隐
        # ──────────────────────────────────────────

        def eventFilter(self, obj, event) -> bool:
            """捕获字幕文字双击 → 切换工具栏"""
            if event.type() == QEvent.Type.MouseButtonDblClick:
                if obj in (self._subtitle_label, self._subtitle_label2):
                    self._toggle_toolbar()
                    return True
            return super().eventFilter(obj, event)

        def _toggle_toolbar(self) -> None:
            """显示/隐藏底部工具栏"""
            self._toolbar_visible = not self._toolbar_visible
            if self._toolbar_visible:
                self._toolbar_frame.show()
                self._restore_hint.hide()
            else:
                self._toolbar_frame.hide()
                self._restore_hint.show()

        # ──────────────────────────────────────────
        # 记录面板切换
        # ──────────────────────────────────────────

        def _toggle_transcript_panel(self) -> None:
            """切换竖屏翻译记录面板的显示/隐藏"""
            if self._transcript_panel is None:
                return
            if self._btn_transcript.isChecked():
                self._transcript_panel.show()
                self._btn_transcript.setText("▼ 记录")   # ▼ 表示已展开
            else:
                self._transcript_panel.hide()
                self._btn_transcript.setText("▶ 记录")   # ▶ 表示已收起

        def _toggle_final_subtitle(self) -> None:
            """切换最终译文展示窗的显示/隐藏"""
            if self._final_subtitle is None:
                return
            if self._btn_final.isChecked():
                self._final_subtitle.show()
                self._btn_final.setText("▼ 输出")
            else:
                self._final_subtitle.hide()
                self._btn_final.setText("▶ 输出")

        def _toggle_summary(self) -> None:
            """折叠/展开右侧主题摘要栏（带动画过渡）"""
            from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

            if self._summary_collapsed:
                # 展开
                target_w = self._summary_full_width
                self._summary_collapse_btn.setText("◀")
                self._summary_collapse_btn.setToolTip("折叠主题摘要栏")
            else:
                # 折叠：只保留按钮宽度
                target_w = 40
                self._summary_collapse_btn.setText("▶")
                self._summary_collapse_btn.setToolTip("展开主题摘要栏")

            self._summary_collapsed = not self._summary_collapsed

            # 动画过渡
            self._anim = QPropertyAnimation(self._summary_frame, b"minimumWidth")
            self._anim.setDuration(250)
            self._anim.setStartValue(self._summary_frame.width())
            self._anim.setEndValue(target_w)
            self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._anim.start()

            # 动画结束后固定宽度
            self._anim.finished.connect(lambda: self._summary_frame.setFixedWidth(target_w))

            # 折叠时清空摘要文本
            if self._summary_collapsed:
                self._summary_text_label.setText("")

        def sync_transcript_button(self, visible: bool) -> None:
            """外部同步按钮状态（如托盘点关闭面板时）"""
            self._btn_transcript.setChecked(visible)
            self._btn_transcript.setText("▼ 记录" if visible else "▶ 记录")

        def sync_final_button(self, visible: bool) -> None:
            """外部同步最终译文按钮状态"""
            self._btn_final.setChecked(visible)
            self._btn_final.setText("▼ 输出" if visible else "▶ 输出")

        def set_settings_callback(self, callback) -> None:
            """注入设置按钮回调（由 main.py 在初始化后设置）"""
            self._on_settings_clicked = callback

        def set_pause_callback(self, callback) -> None:
            """注入暂停/继续按钮回调"""
            self._on_pause_clicked = callback

        def set_stop_session_callback(self, callback) -> None:
            """注入停止识别按钮回调"""
            self._on_stop_session_clicked = callback

        def set_exit_callback(self, callback) -> None:
            """注入退出程序按钮回调"""
            self._on_exit_clicked = callback

        def _on_settings_btn(self) -> None:
            """设置按钮被点击"""
            if self._on_settings_clicked:
                self._on_settings_clicked()

        def _on_pause_btn(self) -> None:
            """暂停/继续按钮被点击"""
            self._paused = self._btn_pause.isChecked()
            self._btn_pause.setText("▶ 继续" if self._paused else "⏸ 暂停")
            if self._on_pause_clicked:
                self._on_pause_clicked(self._paused)

        def _on_stop_session_btn(self) -> None:
            """停止识别按钮被点击"""
            # 重置暂停按钮
            self._paused = False
            self._btn_pause.setChecked(False)
            self._btn_pause.setText("⏸ 暂停")
            if self._on_stop_session_clicked:
                self._on_stop_session_clicked()

        def _on_exit_btn(self) -> None:
            """退出按钮被点击"""
            if self._on_exit_clicked:
                self._on_exit_clicked()

        # ──────────────────────────────────────────
        # 公共接口（供翻译引擎 / 摘要模块回调调用）
        # ──────────────────────────────────────────

        def update_subtitle(self, result: TranslationResult) -> None:
            """
            线程安全地更新字幕显示（系统音频流）。
            从翻译引擎回调，可能在非 UI 线程中调用。
            """
            color = COLOR_PARTIAL if result.status == STATUS_PARTIAL else COLOR_FINAL
            self._signal.subtitle_updated.emit(result.text, color)

        def update_mic_subtitle(self, result: TranslationResult) -> None:
            """
            线程安全地更新第二行字幕（麦克风流 — 中→英）。
            从翻译引擎回调，可能在非 UI 线程中调用。
            """
            color = COLOR_PARTIAL if result.status == STATUS_PARTIAL else COLOR_FINAL
            self._signal.subtitle2_updated.emit(result.text, color)

        def set_dual_mode(self, enabled: bool) -> None:
            """切换双向模式布局"""
            self._dual_mode = enabled
            self._subtitle_label2.setVisible(enabled)
            if not enabled:
                self._subtitle_label2.setText("")

        def update_summary_text(self, summary: str) -> None:
            """
            线程安全地更新右侧内容摘要栏。
            从 Summarizer 回调，可能在非 UI 线程中调用。
            """
            self._signal.summary_updated.emit(summary)

        def update_summary_log(self, msg: str) -> None:
            """线程安全地更新摘要栏工作日志"""
            self._signal.summary_log_updated.emit(msg)

        def show_correction(self, old_text: str, new_text: str, reason: str) -> None:
            """
            线程安全地显示上下文修正提示。
            从 ContextCorrector 回调，可能在非 UI 线程中调用。
            """
            self._signal.correction_shown.emit(old_text, new_text, reason)

        # ──────────────────────────────────────────
        # 信号处理槽（在 UI 主线程中执行）
        # ──────────────────────────────────────────

        def _on_subtitle_updated(self, text: str, color: str) -> None:
            """更新字幕文本和颜色"""
            self._current_text = text
            self._subtitle_label.setText(text)
            self._subtitle_label.setStyleSheet(
                f"color: {color};"
                f"background: transparent;"
                f"padding: 8px 16px;"
            )

            # ── 重置清除定时器：3秒无新结果自动清除字幕 ──
            if self._clear_timer:
                self._clear_timer.stop()
            self._clear_timer = QTimer()
            self._clear_timer.setSingleShot(True)
            self._clear_timer.timeout.connect(self._clear_subtitle)
            self._clear_timer.start(3000)  # 3秒

            # Final 结果：绿色闪烁 1 秒后切换为用户字体色
            if color == COLOR_FINAL:
                if self._flash_timer:
                    self._flash_timer.stop()
                self._flash_timer = QTimer()
                self._flash_timer.setSingleShot(True)
                self._flash_timer.timeout.connect(self._signal.flash_done.emit)
                self._flash_timer.start(1000)  # 1 秒

        def _on_flash_done(self) -> None:
            """绿色闪烁结束，切换为用户自定义字体色"""
            self._apply_stable_color()

        def _on_subtitle2_updated(self, text: str, color: str) -> None:
            """更新流2（麦克风）字幕文本和颜色"""
            self._current_text2 = text
            prefix = "🎙 EN: " if text else ""
            self._subtitle_label2.setText(f"{prefix}{text}")
            self._subtitle_label2.setStyleSheet(
                f"color: {color};"
                f"background: transparent;"
                f"padding: 4px 12px;"
            )

            # 重置清除定时器
            if self._clear_timer2:
                self._clear_timer2.stop()
            self._clear_timer2 = QTimer()
            self._clear_timer2.setSingleShot(True)
            self._clear_timer2.timeout.connect(self._clear_subtitle2)
            self._clear_timer2.start(4000)  # 4秒

            # Final 结果：蓝色闪烁 → 稳定
            if color == COLOR_FINAL:
                if self._flash_timer2:
                    self._flash_timer2.stop()
                self._flash_timer2 = QTimer()
                self._flash_timer2.setSingleShot(True)
                self._flash_timer2.timeout.connect(self._signal.flash2_done.emit)
                self._flash_timer2.start(1000)

        def _on_flash2_done(self) -> None:
            """流2 闪烁结束"""
            if self._current_text2:
                self._subtitle_label2.setStyleSheet(
                    "color: #66BBFF;"
                    "background: transparent;"
                    "padding: 4px 12px;"
                )

        def _clear_subtitle2(self) -> None:
            """超时无新结果，清除流2字幕"""
            self._current_text2 = ""
            if self._dual_mode:
                self._subtitle_label2.setText("🎙 EN: ▏ 等待发言…")
                self._subtitle_label2.setStyleSheet(
                    f"color: {COLOR_PARTIAL};"
                    f"background: transparent;"
                    f"padding: 4px 12px;"
                )

        def _clear_subtitle(self) -> None:
            """超时无新结果，清除字幕（显示等待提示）"""
            self._current_text = ""
            self._subtitle_label.setText("▌ 等待翻译…")
            self._subtitle_label.setStyleSheet(
                f"color: {COLOR_PARTIAL};"
                f"background: transparent;"
                f"padding: 8px 16px;"
            )

        def _on_correction_shown(self, old_text: str, new_text: str, reason: str) -> None:
            """显示上下文修正提示（橙色，5 秒后自动消失）"""
            display = f"🔄 修正：{old_text} → {new_text}（{reason}）"
            self._correction_label.setText(display)
            self._correction_label.setVisible(True)

            if self._correction_timer:
                self._correction_timer.stop()
            self._correction_timer = QTimer()
            self._correction_timer.setSingleShot(True)
            self._correction_timer.timeout.connect(
                lambda: self._correction_label.setVisible(False)
            )
            self._correction_timer.start(5000)

        def _on_summary_updated(self, summary: str) -> None:
            """更新右侧内容摘要文本"""
            if self._summary_collapsed:
                return
            self._summary_text_label.setText(summary)

        def _on_summary_log_updated(self, msg: str) -> None:
            """更新摘要栏工作日志"""
            self._summary_log_label.setText(msg)


else:
    # PyQt6 不可用时提供空壳类
    class SubtitleWindow:
        def __init__(self, transcript_panel=None, final_subtitle=None):
            print("[subtitle_window] PyQt6 未安装，UI 窗口不可用")
        def update_subtitle(self, result): pass
        def update_summary_text(self, summary): pass
        def update_summary_log(self, msg): pass
        def show_correction(self, old_text, new_text, reason): pass
        def show(self): pass
        def sync_transcript_button(self, visible): pass
        def sync_final_button(self, visible): pass
        def set_settings_callback(self, callback): pass
        def set_pause_callback(self, callback): pass
        def set_stop_session_callback(self, callback): pass
        def set_exit_callback(self, callback): pass
