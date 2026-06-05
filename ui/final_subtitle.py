# ui/final_subtitle.py — 实时译文独立展示窗
# 实时显示翻译结果（PARTIAL 灰色 / FINAL 绿色→白色）
# 可拖动到视频字幕区域、可最小化/关闭
# v0.7.0 — 修复背景色选择器、添加窗口控制按钮

from __future__ import annotations

import json
import os
from typing import Optional

try:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
        QPushButton, QColorDialog, QMenu,
    )
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint
    from PyQt6.QtGui import QFont, QColor
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    print("[final_subtitle] 警告：PyQt6 未安装，UI 不可用")

from core.translator import TranslationResult, STATUS_FINAL, STATUS_PARTIAL

# 设置文件
_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "final_subtitle_settings.json")

_DEFAULT_BG_COLOR   = "#000000"
_DEFAULT_BG_OPACITY = 0.80
_DEFAULT_FONT_COLOR = "#FFFFFF"
_DEFAULT_FONT_SIZE  = 24
_DEFAULT_W = 640
_DEFAULT_H = 120


def _load_settings() -> dict:
    defaults = {
        "bg_color": "#000000",
        "bg_opacity": 0.80,
        "font_color": "#FFFFFF",
        "font_size": 24,
        "window_w": _DEFAULT_W,
        "window_h": _DEFAULT_H,
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
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[final_subtitle] 保存设置失败：{e}")


if PYQT_AVAILABLE:

    class _SignalBridge(QObject):
        text_updated = pyqtSignal(str, str)  # (text, status)


    class FinalSubtitleWindow(QWidget):
        """实时译文独立展示窗 — 可拖动、可最小化、可关闭"""

        def __init__(self) -> None:
            super().__init__()
            self._signal = _SignalBridge()
            self._current_text = ""
            self._current_status = ""
            self._clear_timer: Optional[QTimer] = None

            s = _load_settings()
            self._bg_color   = s.get("bg_color", _DEFAULT_BG_COLOR)
            self._bg_opacity = s.get("bg_opacity", _DEFAULT_BG_OPACITY)
            self._font_color = s.get("font_color", _DEFAULT_FONT_COLOR)
            self._font_size  = s.get("font_size", _DEFAULT_FONT_SIZE)
            self._init_w     = s.get("window_w", _DEFAULT_W)
            self._init_h     = s.get("window_h", _DEFAULT_H)

            self._drag_pos: Optional[QPoint] = None
            self._signal.text_updated.connect(self._on_text_updated)

            self._setup_window()
            self._setup_ui()

        # ── 窗口设置 ──

        def _setup_window(self) -> None:
            self.setWindowTitle("实时译文 — 可拖动到视频字幕区")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            # 允许最小化
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setMinimumSize(240, 60)
            self.resize(self._init_w, self._init_h)
            screen = QApplication.primaryScreen().geometry()
            self.move(
                (screen.width() - self.width()) // 2,
                screen.height() - self.height() - 120,
            )

        def _setup_ui(self) -> None:
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)

            # 主文本
            self._frame = QLabel("就绪 — 等待翻译")
            self._frame.setObjectName("finalFrame")
            self._frame.setFont(QFont("Microsoft YaHei", self._font_size))
            self._frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._frame.setWordWrap(True)
            self._apply_colors()
            root.addWidget(self._frame)

            # 底部栏
            bottom = QHBoxLayout()
            bottom.setContentsMargins(8, 2, 8, 4)

            btn_min = QPushButton("−")
            btn_min.setFixedSize(28, 24)
            btn_min.setToolTip("最小化")
            btn_min.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.15); color: #ccc;"
                "  border: 1px solid rgba(255,255,255,0.2); border-radius: 4px;"
                "  font-size: 16px; font-weight: bold; padding: 0; }"
                "QPushButton:hover { background: rgba(255,255,255,0.3); color: white; }"
            )
            btn_min.clicked.connect(self.showMinimized)
            bottom.addWidget(btn_min)

            bottom.addStretch()

            self._status_label = QLabel("")
            self._status_label.setFont(QFont("Microsoft YaHei", 9))
            self._status_label.setStyleSheet("color: rgba(255,255,255,0.3); background: transparent;")
            bottom.addWidget(self._status_label)

            bottom.addStretch()

            btn_close = QPushButton("✕")
            btn_close.setFixedSize(28, 24)
            btn_close.setToolTip("关闭窗口")
            btn_close.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.15); color: #ccc;"
                "  border: 1px solid rgba(255,255,255,0.2); border-radius: 4px;"
                "  font-size: 14px; padding: 0; }"
                "QPushButton:hover { background: rgba(255,80,80,0.5); color: white; }"
            )
            btn_close.clicked.connect(self.hide)
            bottom.addWidget(btn_close)

            root.addLayout(bottom)

        # ── 拖动 ──

        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
            else:
                super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:
            if self._drag_pos is not None:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()
            else:
                super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = None
                self._persist()
                event.accept()
            else:
                super().mouseReleaseEvent(event)

        # ── 右键菜单 ──

        def contextMenuEvent(self, event) -> None:
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu { background: #1a1d2e; color: #E8ECF4; border: 1px solid #333; padding: 4px; }
                QMenu::item { padding: 6px 24px; border-radius: 4px; }
                QMenu::item:selected { background: #2d3148; }
            """)

            action_bg     = menu.addAction("🎨 背景颜色…")
            action_opacity = menu.addAction("🔍 背景透明度…")
            action_font   = menu.addAction("🔤 字体颜色…")
            menu.addSeparator()
            action_s = menu.addAction("📏 字号：小 (18)")
            action_m = menu.addAction("📏 字号：中 (24)")
            action_l = menu.addAction("📏 字号：大 (32)")
            menu.addSeparator()
            action_reset = menu.addAction("🔄 恢复默认")

            action = menu.exec(event.globalPos())
            if action is None:
                return

            if action == action_bg:
                self._pick_bg_color()
            elif action == action_opacity:
                self._pick_opacity()
            elif action == action_font:
                self._pick_font_color()
            elif action == action_s:
                self._set_font_size(18)
            elif action == action_m:
                self._set_font_size(24)
            elif action == action_l:
                self._set_font_size(32)
            elif action == action_reset:
                self._reset_colors()

        def _pick_bg_color(self) -> None:
            """选背景色（纯色，不涉及 alpha）"""
            try:
                initial = QColor(self._bg_color)
            except Exception:
                initial = QColor("#000000")
            color = QColorDialog.getColor(initial, self, "选择背景颜色")
            if color.isValid():
                self._bg_color = color.name()
                self._apply_colors()
                self._persist()

        def _pick_opacity(self) -> None:
            """通过简单对话框选择透明度（0-100%）"""
            from PyQt6.QtWidgets import QInputDialog
            current = int(self._bg_opacity * 100)
            val, ok = QInputDialog.getInt(
                self, "背景透明度",
                "请输入透明度（0=全透明, 100=不透明）：",
                current, 0, 100, 5,
            )
            if ok:
                self._bg_opacity = val / 100.0
                self._apply_colors()
                self._persist()

        def _pick_font_color(self) -> None:
            try:
                initial = QColor(self._font_color)
            except Exception:
                initial = QColor("#FFFFFF")
            color = QColorDialog.getColor(initial, self, "选择字体颜色")
            if color.isValid():
                self._font_color = color.name()
                self._apply_colors()
                self._persist()

        def _set_font_size(self, size: int) -> None:
            self._font_size = size
            self._frame.setFont(QFont("Microsoft YaHei", size))
            self._persist()

        def _reset_colors(self) -> None:
            self._bg_color   = _DEFAULT_BG_COLOR
            self._bg_opacity = _DEFAULT_BG_OPACITY
            self._font_color = _DEFAULT_FONT_COLOR
            self._font_size  = _DEFAULT_FONT_SIZE
            self._apply_colors()
            self._persist()

        def _bg_rgba(self) -> str:
            """把 bg_color + bg_opacity 合成 rgba 字符串"""
            try:
                c = QColor(self._bg_color)
                r, g, b = c.red(), c.green(), c.blue()
            except Exception:
                r, g, b = 0, 0, 0
            return f"rgba({r},{g},{b},{self._bg_opacity:.2f})"

        def _apply_colors(self) -> None:
            bg = self._bg_rgba()
            self._frame.setStyleSheet(
                f"#finalFrame {{"
                f"  background: {bg};"
                f"  border-radius: 8px;"
                f"  color: {self._font_color};"
                f"  padding: 10px 20px;"
                f"}}"
            )
            self._frame.setFont(QFont("Microsoft YaHei", self._font_size))

        def _persist(self) -> None:
            _save_settings({
                "bg_color": self._bg_color,
                "bg_opacity": self._bg_opacity,
                "font_color": self._font_color,
                "font_size": self._font_size,
                "window_w": self.width(),
                "window_h": self.height(),
            })

        def _clear_text(self) -> None:
            self._current_text = ""
            self._current_status = ""
            self._frame.setText("")
            self._status_label.setText("")

        # ── 公共接口 ──

        def update_text(self, result: TranslationResult) -> None:
            """线程安全地推送翻译结果（含 PARTIAL + FINAL）"""
            if result.text:
                self._signal.text_updated.emit(result.text, result.status)

        def _on_text_updated(self, text: str, status: str) -> None:
            """UI 线程更新 — 字体色保持用户选择色，不再闪烁切换"""
            self._current_text = text
            self._current_status = status

            # 统一使用用户字体色，按状态区分底部提示标签
            if status == STATUS_FINAL:
                self._status_label.setText("✓")
                self._status_label.setStyleSheet("color: #00FF88; background: transparent;")
            else:
                self._status_label.setText("…")
                self._status_label.setStyleSheet("color: rgba(255,255,255,0.3); background: transparent;")

            self._frame.setText(text)

            # ── 自适应高度（仅 FINAL 结果，避免 PARTIAL 跳跃）──
            if status == STATUS_FINAL:
                self._frame.adjustSize()
                hint = self._frame.sizeHint()
                new_h = max(hint.height() + 50, self.minimumHeight())
                if abs(new_h - self.height()) > 10:  # 避免微小抖动
                    self.resize(self.width(), new_h)

            # 清除定时器
            if self._clear_timer:
                self._clear_timer.stop()
            self._clear_timer = QTimer()
            self._clear_timer.setSingleShot(True)
            self._clear_timer.timeout.connect(self._clear_text)
            self._clear_timer.start(30000)


else:
    class FinalSubtitleWindow:
        def __init__(self):
            print("[final_subtitle] PyQt6 未安装，窗口不可用")
        def update_text(self, result): pass
        def show(self): pass
        def hide(self): pass
        def showMinimized(self): pass
        def isVisible(self): return False
