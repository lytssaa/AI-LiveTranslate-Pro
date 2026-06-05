# main.py — AI LiveTranslate Pro 程序入口
# v1.0 — 纯 Gummy 云端实时翻译，精简版

from __future__ import annotations

import sys
import signal
import os
import threading
import traceback

# ── 全局未捕获异常处理（捕获崩溃日志）──
def _global_exception_handler(exc_type, exc_value, exc_tb):
    """捕获主线程中所有未处理的异常，打印完整 traceback"""
    print("\n" + "=" * 55, flush=True)
    print("[FATAL] 未捕获的异常导致程序退出：", flush=True)
    traceback.print_exception(exc_type, exc_value, exc_tb)
    print("=" * 55, flush=True)
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _global_exception_handler

# ── 全局线程异常处理 ──
def _thread_exception_handler(args):
    print("\n" + "=" * 55, flush=True)
    print(f"[FATAL] 后台线程未捕获异常：{args.thread.name}", flush=True)
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
    print("=" * 55, flush=True)
threading.excepthook = _thread_exception_handler

# 高分屏（HiDPI）适配
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

try:
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMessageBox
    from PyQt6.QtGui import QIcon
    from PyQt6.QtCore import Qt
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False

from core.audio_capture import AudioCapture
from core.translator import GummyTranslator, STATUS_FINAL, STATUS_PARTIAL, TranslatorState
from core.summarizer import Summarizer
from core.corrector import ContextCorrector
from ui.subtitle_window import SubtitleWindow
from ui.transcript_panel import TranscriptPanel
from ui.final_subtitle import FinalSubtitleWindow
from utils.config import CONFIG, save_config, load_config


def main() -> None:
    """程序入口：初始化所有模块并启动事件循环"""

    _print_banner()

    # ── 1. 初始化 PyQt 应用 ──
    if PYQT_AVAILABLE:
        app = QApplication(sys.argv)
        app.setApplicationName("AI LiveTranslate Pro")
        app.setQuitOnLastWindowClosed(False)
    else:
        app = None
        print("[main] PyQt6 不可用，以无界面模式运行")

    # ── 引擎状态容器（双向模式支持双实例）──
    engine = {
        "sys": {"gummy": None, "audio_capture": None},
        "mic": {"gummy": None, "audio_capture": None},
        "lock": threading.Lock(),
    }
    _bidirectional = CONFIG.get("bidirectional_enabled", "false").lower() in ("true", "1", "yes")

    # ── 2. 初始化 UI 组件 ──
    transcript = TranscriptPanel()
    final_sub = FinalSubtitleWindow()
    window = SubtitleWindow(transcript_panel=transcript, final_subtitle=final_sub)
    if _bidirectional:
        window.set_dual_mode(True)

    # ── 3. 语义分析 + 纠错 ──
    summarizer = Summarizer(
        on_summary_text=window.update_summary_text,
        on_summary=lambda s: print(f"[main] 会议纪要已生成，长度 {len(s)} 字"),
        on_log=window.update_summary_log,
    )

    corrector = ContextCorrector(
        on_correction=lambda c: window.show_correction(c.old_text, c.new_text, c.reason)
    )
    if corrector.available:
        print("[main] 上下文纠错引擎已启用")
    else:
        print("[main] 上下文纠错引擎不可用（需 LLM API Key）")

    # ── 4. 翻译结果分发回调 ──
    def _dispatch_result(result):
        """翻译结果五路分发（系统音频流）"""
        window.update_subtitle(result)
        transcript.push_result(result)
        final_sub.update_text(result)
        if result.status == STATUS_FINAL and result.text:
            summarizer.push_text(result.text)
            corrector.push_sentence(
                text=result.text,
                original=result.original,
                start_ms=result.start_ms,
                end_ms=result.end_ms,
            )

    def _dispatch_mic_result(result):
        """翻译结果分发（麦克风流）"""
        window.update_mic_subtitle(result)
        transcript.push_result(result)
        if result.status == STATUS_FINAL and result.text:
            summarizer.push_text(result.text)

    # ── 5. 引擎启停 ──

    def _start_one(slot: str, src_lang: str, tgt_lang: str, audio_src: str, dev_idx: int,
                   on_result, label: str):
        """启动单条 Gummy + AudioCapture 管线（线程安全）"""
        print(f"[main] 启动 {label}（{src_lang}→{tgt_lang}）...")
        tr = GummyTranslator(on_result=on_result, source_lang=src_lang, target_lang=tgt_lang)
        with engine["lock"]:
            engine[slot]["gummy"] = tr
        tr.start()

        def _push(pcm: bytes):
            g = engine[slot].get("gummy")
            if g:
                g.push_audio(pcm)

        ac = AudioCapture(
            on_audio_chunk=_push,
            audio_source=audio_src,
            input_device_index=dev_idx,
        )
        with engine["lock"]:
            engine[slot]["audio_capture"] = ac
        ac.start()
        print(f"[main] {label} 音频捕获已启动")

    def _stop_one(slot: str, label: str):
        """停止单条管线（线程安全 — 带锁保护 engine dict）"""
        with engine["lock"]:
            slot_data = engine.get(slot, {})
        try:
            ac = slot_data.get("audio_capture")
            if ac:
                ac.stop()
        except Exception as e:
            print(f"[main] 停止 {label} 音频捕获时出错: {e}")
        try:
            g = slot_data.get("gummy")
            if g:
                g.stop()
        except Exception as e:
            print(f"[main] 停止 {label} 翻译引擎时出错: {e}")
        with engine["lock"]:
            engine[slot]["audio_capture"] = None
            engine[slot]["gummy"] = None

    def start_engine():
        """启动 Gummy 翻译引擎 + 音频捕获"""
        import time
        bi = CONFIG.get("bidirectional_enabled", "false").lower() in ("true", "1", "yes")

        src = CONFIG.get("gummy_source_language", "auto")
        tgt = CONFIG.get("gummy_target_language", "zh")
        audio_src = CONFIG.get("audio_source", "system")
        dev_idx = int(CONFIG.get("audio_input_device", "-1"))

        print("[main] 启动引擎...")
        transcript.set_engine_status("正在连接百炼 Gummy…")

        # 系统音频流（双向模式下强制 system，单向模式跟配置走）
        sys_audio_src = "system" if bi else audio_src
        _start_one("sys", src, tgt, sys_audio_src, dev_idx, _dispatch_result, "系统音频")

        # 双向模式：额外启动麦克风 → 英文
        if bi:
            # 微小延迟避免两个 PyAudio 实例并发初始化 PortAudio（已知竞态）
            time.sleep(0.3)
            _start_one("mic", "zh", "en", "mic", dev_idx, _dispatch_mic_result, "麦克风")
            window.set_dual_mode(True)
            print("[main] 双向翻译模式已启用（系统音频→中文 + 麦克风→英文）")

        print("[main] 引擎启动完成")

    def stop_engine():
        """停止所有引擎管线"""
        _stop_one("sys", "系统音频")
        _stop_one("mic", "麦克风")
        print("[main] 引擎已停止")

    def _update_engine_status():
        """更新翻译面板引擎状态指示"""
        g_sys = engine["sys"].get("gummy")
        g_mic = engine["mic"].get("gummy")
        parts = []
        for g, label in [(g_sys, "系统"), (g_mic, "麦克风")]:
            if g is None:
                continue
            state = g._state
            if state == TranslatorState.STREAMING:
                parts.append(f"🟢 {label}")
            elif state in (TranslatorState.CONNECTING, TranslatorState.WAITING_STARTED):
                parts.append(f"⏳ {label}")
            else:
                parts.append(f"✓ {label}")
        transcript.set_engine_status("Gummy: " + " | ".join(parts) if parts else "Gummy 就绪")

    # ── 暂停 / 恢复引擎 ──
    _engine_paused = False

    def toggle_pause(paused: bool) -> None:
        """暂停或恢复识别翻译（不生成纪要，不重置状态）"""
        nonlocal _engine_paused
        _engine_paused = paused
        if paused:
            print("[main] ⏸ 暂停识别翻译")
            transcript.set_engine_status("⏸ 已暂停")
            # stop_engine 在事件循环中执行（不在 Qt 信号槽中），
            # 且 translator 的 _stopping 标志已阻断 WS 线程回调 → 安全。
            QTimer.singleShot(0, stop_engine)
        else:
            print("[main] ▶ 恢复识别翻译")
            transcript.set_engine_status("▶ 恢复中…")
            QTimer.singleShot(0, start_engine)

    def stop_session() -> None:
        """
        结束当前识别会话：
        - 停止引擎
        - 生成最终会议纪要
        - 重置摘要引擎以备下次会话
        - 程序保持打开
        """
        print("\n[main] ⏹ 停止识别，正在结束会话…")
        transcript.set_engine_status("⏹ 已停止 — 正在生成纪要…")

        def _finish_session():
            """引擎停止后的收尾工作"""
            # 生成最终纪要
            try:
                final_summary = summarizer.stop()
                if final_summary:
                    _save_final_summary(final_summary)
            except Exception as e:
                print(f"[main] 生成最终纪要时出错: {e}")
                traceback.print_exc()

            # 重置摘要引擎（准备下次会话）
            try:
                summarizer.reset()
                summarizer.start()
                print("[main] 摘要引擎已重置，可开始新会话")
            except Exception as e:
                print(f"[main] 重置摘要引擎时出错: {e}")

            transcript.set_engine_status("会话已结束 — 可重新开始")
            print("[main] 会话已结束\n")

        # 先停止引擎（延迟到事件循环），再执行收尾
        def _on_engine_stopped():
            stop_engine()
            QTimer.singleShot(300, _finish_session)

        QTimer.singleShot(0, _on_engine_stopped)

    def restart_engine():
        """热重启引擎（设置变更后调用）"""
        print("\n[main] ===== 引擎热重启 =====")

        def _do_restart():
            try:
                stop_engine()
            except Exception as e:
                print(f"[main] 停止旧引擎时出错: {e}")
                traceback.print_exc()

            # 重新加载配置
            new_cfg = load_config()
            for k, v in new_cfg.items():
                CONFIG[k] = v

            # 双向模式变更时更新窗口布局
            bi = CONFIG.get("bidirectional_enabled", "false").lower() in ("true", "1", "yes")
            window.set_dual_mode(bi)

            # 更新全局双向标志
            nonlocal _bidirectional
            _bidirectional = bi

            # 启动新引擎（给 WS/PyAudio 清理留一点时间）
            QTimer.singleShot(300, _start_new)

        def _start_new():
            try:
                start_engine()
                _update_engine_status()
            except Exception as e:
                print(f"[main] 启动新引擎时出错: {e}")
                traceback.print_exc()

        QTimer.singleShot(0, _do_restart)

    # ── 6. 设置窗口定位工具 ──
    def _calc_settings_rect(settings_win, anchor_window):
        """计算设置窗口的目标矩形（不执行移动）。
        返回 QRect，在 show() 之前用 setGeometry() 一步到位，消除闪现。"""
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QScreen
        win_size = settings_win.size()

        if anchor_window and anchor_window.isVisible():
            anchor_geo = anchor_window.geometry()
            x = anchor_geo.right() + 10
            y = anchor_geo.top()
        else:
            # 主窗口不可见时，放在当前屏幕中央偏上
            screen = QApplication.primaryScreen()
            if screen:
                sg = screen.availableGeometry()
                x = sg.x() + (sg.width() - win_size.width()) // 2
                y = sg.y() + (sg.height() - win_size.height()) // 3
            else:
                x, y = 200, 200

        # 确保不超出屏幕边界
        screen = QApplication.screenAt(anchor_window.geometry().center()) if anchor_window and anchor_window.isVisible() else QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            if x + win_size.width() > sg.right():
                x = sg.right() - win_size.width() - 10
            if y + win_size.height() > sg.bottom():
                y = sg.bottom() - win_size.height() - 10
            if y < sg.top():
                y = sg.top() + 10
            if x < sg.left():
                x = sg.left() + 10

        return QRect(x, y, win_size.width(), win_size.height())

    # ── 7. 设置窗口（非模态）──
    _settings_win = None  # 单例引用

    def open_settings():
        """打开设置对话框（非模态，可边用边改）"""
        nonlocal _settings_win
        # 检查是否已有窗口（WA_DeleteOnClose 关闭后 C++ 对象已销毁，需 try/except 兜底）
        try:
            if _settings_win is not None and _settings_win.isVisible():
                _settings_win.raise_()
                _settings_win.activateWindow()
                return
        except RuntimeError:
            _settings_win = None  # C++ 对象已销毁，重置引用

        from ui.settings_window import SettingsWindow
        _settings_win = SettingsWindow()
        _settings_win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        _settings_win.setWindowModality(Qt.WindowModality.NonModal)

        # LLM 配置变更 → 热重载（无需重启）
        def _on_llm_saved():
            print("[main] LLM 配置已更新，热重载中...")
            summarizer.reload_llm_config()
            corrector.reload_llm_config()
            print("[main] ✅ 摘要 & 纠错引擎配置已热重载")

        # 引擎配置变更 → 提示重启
        def _on_engine_restart_needed():
            print("[main] 引擎配置已变更，触发重启...")
            try:
                restart_engine()
            except Exception as e:
                traceback.print_exc()
                if PYQT_AVAILABLE:
                    QMessageBox.critical(
                        None, "引擎重启失败",
                        f"重启引擎时发生错误：\n{e}\n\n请查看终端控制台获取完整信息。"
                    )

        _settings_win.llm_saved.connect(_on_llm_saved)
        _settings_win.engine_restart_needed.connect(_on_engine_restart_needed)

        # 防闪现：Win32 SetWindowPos 兆底强制位置
        # Qt 层所有方法都无法完全阻止 Windows WM_SHOWWINDOW 的默认放置。
        # show() 后立即用 SetWindowPos 重设 HWND 位置，最小化闪现帧数。
        _settings_win.adjustSize()
        _settings_win.ensurePolished()
        _settings_win.layout().activate()
        _settings_win.resize(_settings_win.sizeHint())
        rect = _calc_settings_rect(_settings_win, window)
        _settings_win.setGeometry(rect)
        _settings_win.show()

        # Win32: show() 后立即纠正 HWND 位置（先计算好坐标）
        import ctypes
        try:
            hwnd = int(_settings_win.winId())
            SWP_NOACTIVATE = 0x0010
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0,
                rect.x(), rect.y(), 0, 0,
                SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER
            )
        except Exception:
            pass

    window.set_settings_callback(open_settings)
    window.set_pause_callback(toggle_pause)
    window.set_stop_session_callback(stop_session)
    window.set_exit_callback(lambda: shutdown())

    # ── 8. 系统托盘 ──
    tray_icon = None
    if PYQT_AVAILABLE:
        tray_icon = _setup_tray(app, window, transcript, final_sub, open_settings)

    # ── 9. 退出信号处理 ──
    def shutdown(signum=None, frame=None):
        print("\n[main] 正在关闭所有模块...")
        stop_engine()

        # 生成并保存最终会议纪要
        try:
            final_summary = summarizer.stop()
            if final_summary:
                _save_final_summary(final_summary)
        except Exception as e:
            print(f"[main] 生成最终纪要时出错: {e}")
            traceback.print_exc()

        corrector.stop()
        transcript.hide()
        final_sub.hide()
        window.hide()
        if app:
            app.quit()
        print("[main] 已安全退出")

    def _save_final_summary(markdown_text: str) -> None:
        """保存最终会议纪要到文件并弹窗通知"""
        if not markdown_text or not markdown_text.strip():
            print("[main] 会议纪要内容为空，跳过保存")
            return

        import os
        from datetime import datetime

        save_dir = os.path.dirname(os.path.abspath(__file__))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"会议纪要_{timestamp}.md"
        filepath = os.path.join(save_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(markdown_text)
            print(f"[main] ✅ 会议纪要已保存：{filepath}")
            if PYQT_AVAILABLE:
                QMessageBox.information(
                    None, "会议纪要已生成",
                    f"完整会议纪要已保存至：\n{filepath}\n\n共 {len(markdown_text)} 字"
                )
        except Exception as e:
            print(f"[main] ❌ 保存会议纪要失败：{e}")
            if PYQT_AVAILABLE:
                QMessageBox.warning(
                    None, "保存失败",
                    f"会议纪要生成成功但保存失败：\n{e}"
                )

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ── 10. 启动非引擎模块（先让窗口出来，再启引擎防崩）──
    print("[main] 启动语义分析...")
    summarizer.start()

    if corrector.available:
        print("[main] 启动上下文纠错...")
        corrector.start()

    # ── 11. 先显示窗口，引擎延迟启动 ──
    print("[main] 显示悬浮窗...")
    print("[main] 提示：点击悬浮窗「⚙」按钮可打开设置")
    sys.stdout.flush()
    if PYQT_AVAILABLE:
        from PyQt6.QtCore import QTimer

        try:
            window.show()
        except Exception as e:
            print(f"[main] 悬浮窗显示失败：{e}")
            traceback.print_exc()

        # 窗口出来后 500ms 再启动引擎，避免 PyAudio 线程在事件循环就绪前崩溃
        QTimer.singleShot(500, start_engine)
        QTimer.singleShot(3500, _update_engine_status)

        print("[main] 进入事件循环...", flush=True)
        exit_code = app.exec()
        print(f"[main] 事件循环退出（code={exit_code}），开始清理...")
        shutdown()
        sys.exit(exit_code)
    else:
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            shutdown()


def _print_banner() -> None:
    """打印启动横幅"""
    print("=" * 55)
    print("  AI LiveTranslate Pro  v1.4")
    print("  引擎：百炼 Gummy-Realtime-V1 × 双向翻译")
    print("=" * 55)


# ═══════════════════════════════════════════════════════════
# 系统托盘
# ═══════════════════════════════════════════════════════════

def _setup_tray(app, window, transcript, final_sub, open_settings_cb):
    """创建系统托盘图标及右键菜单"""
    try:
        tray = QSystemTrayIcon()
        tray.setIcon(QApplication.style().standardIcon(
            QApplication.style().StandardPixmap.SP_ComputerIcon
        ))
        tray.setToolTip("AI LiveTranslate Pro — 实时翻译中")

        menu = QMenu()

        def toggle_subtitle():
            if window.isVisible():
                window.hide()
            else:
                window.show()

        action_subtitle = menu.addAction("字幕悬浮窗")
        action_subtitle.setCheckable(True)
        action_subtitle.setChecked(True)
        action_subtitle.triggered.connect(toggle_subtitle)

        def toggle_transcript():
            if transcript.isVisible():
                transcript.hide()
                window.sync_transcript_button(False)
            else:
                transcript.show()
                window.sync_transcript_button(True)

        action_transcript = menu.addAction("翻译记录面板")
        action_transcript.setCheckable(True)
        action_transcript.setChecked(False)
        action_transcript.triggered.connect(toggle_transcript)

        def toggle_final_sub():
            if final_sub.isVisible():
                final_sub.hide()
                window.sync_final_button(False)
            else:
                final_sub.show()
                window.sync_final_button(True)

        action_final = menu.addAction("最终译文展示窗")
        action_final.setCheckable(True)
        action_final.setChecked(False)
        action_final.triggered.connect(toggle_final_sub)

        menu.addSeparator()

        action_settings = menu.addAction("⚙  设置…")
        action_settings.triggered.connect(open_settings_cb)

        menu.addSeparator()

        def quit_app():
            window.hide()
            transcript.hide()
            final_sub.hide()
            tray.hide()
            app.quit()

        action_quit = menu.addAction("退出")
        action_quit.triggered.connect(quit_app)

        tray.setContextMenu(menu)

        tray.activated.connect(
            lambda reason: open_settings_cb()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )

        tray.show()
        print("[main] 系统托盘已就绪")
        return tray

    except Exception as e:
        print(f"[main] 系统托盘初始化失败：{e}")
        return None


if __name__ == "__main__":
    main()
