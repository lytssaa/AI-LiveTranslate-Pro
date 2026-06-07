# PR 提交记录

> 每个 PR 只做一件事，标题 + 功能描述 + 实现思路 + 测试方式四要素齐全。

---

## PR #1: 项目脚手架初始化

**标题**: feat: 项目脚手架初始化

**功能描述**:
搭建 AI LiveTranslate Pro 项目基础架构，包含目录结构、依赖声明、配置模板和项目文档。

**实现思路**:
- `.gitignore`: 排除 venv、IDE 配置、运行时敏感文件（config.ini）、生成物
- `requirements.txt`: 声明四个核心依赖（PyQt6 GUI / websocket-client / pyaudiowpatch / requests）
- `config.example.ini`: 用户参考配置模板，列出所有可配置参数及说明
- `README.md`: 项目简介、技术栈、快速开始指南
- 初始化 core/ui/utils 三个子包的 `__init__.py`

**测试方式**:
```bash
git clone <repo> && cd AI_LiveTranslate_Pro
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt  # 确认依赖安装无报错
```

---

## PR #2: 配置管理模块

**标题**: feat: 配置管理模块

**功能描述**:
实现 configparser 三层优先级配置加载系统，支持运行时热更新。

**实现思路**:
- 优先级链：环境变量 > config.ini > 内置默认值
- `load_config()`: 合并三层配置返回 dict
- `save_config()`: 运行时写入 config.ini + 同步全局 CONFIG 对象
- 模块级 `CONFIG` 对象，其他模块直接 `from utils.config import CONFIG` 即可
- 环境变量映射表：DASHSCOPE_API_KEY → api_key 等

**测试方式**:
```python
from utils.config import CONFIG, load_config, save_config
assert CONFIG["api_url"] == "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
save_config({"gummy_target_language": "en"})
cfg = load_config()
assert cfg["gummy_target_language"] == "en"
```

---

## PR #3: GummyTranslator 实时翻译引擎

**标题**: feat: GummyTranslator 实时翻译引擎

**功能描述**:
百炼 Gummy-Realtime-V1 WebSocket 实时翻译核心模块，状态机驱动的双语翻译管线。

**实现思路**:
- **状态机**: 6 种状态（IDLE→CONNECTING→STREAMING→FINISHING→DONE），带 `_state_lock` 线程安全保护
- **WebSocket 协议**: 实现 run-task / finish-task 握手机制，发送 PCM 二进制帧
- **双线程模型**: WS 监听线程（run_forever）+ 音频发送线程（Queue 消费），互不阻塞
- **Partial/Final**: partial 结果缓存到 `_partial_cache`，final 时清除，防止字幕回退闪烁
- **会话时间戳**: `_session_offset_ms` 统一管理增量偏移
- **线程安全停止**: `_stopping` Event 阻断关闭期间回调 Qt widget；`_close_ws()` swap-with-None 防双 close

**测试方式**:
```python
from core.translator import GummyTranslator, TranslationResult

def on_result(r: TranslationResult):
    print(f"[{r.is_final}] {r.source_text} → {r.target_text}")

t = GummyTranslator(on_result=on_result, source_lang="auto", target_lang="zh")
t.start()
# 通过 AudioCapture 推送 PCM 数据 → t.push_audio(pcm)
# 观察控制台输出翻译结果
t.stop()
```

---

## PR #4: AudioCapture 音频捕获模块

**标题**: feat: AudioCapture 音频捕获模块

**功能描述**:
基于 pyaudiowpatch 的 WASAPI Loopback 音频捕获，支持系统音频和麦克风双模式。

**实现思路**:
- **设备发现**: 自动检测 Loopback 设备（系统音频）和默认麦克风设备
- **降采样**: 线性插值将 48000/44100Hz 降至 Gummy 要求的 16000Hz
- **捕获线程**: 独立线程运行 `_capture_loop()`，通过回调 `on_audio_chunk(pcm: bytes)` 推送
- **线程生命周期**: `stop()` 先 `join()` 捕获线程（等待 `stream.read()` 退出），再清理 stream/PyAudio

**测试方式**:
```python
from core.audio_capture import AudioCapture

def on_chunk(pcm):
    print(f"收到音频: {len(pcm)} bytes")

ac = AudioCapture(on_audio_chunk=on_chunk, audio_source="system")
ac.start()
# 播放系统音频，观察控制台输出
ac.stop()
```

---

## PR #5: 悬浮字幕窗口 + 最终译文窗口 UI

**标题**: feat: 悬浮字幕窗口 + 最终译文窗口 UI

**功能描述**:
PyQt6 双窗口字幕显示系统，含三按钮工具栏、双向翻译双栏布局、摘要展示。

**实现思路**:
- **SubtitleWindow**: 无边框始终置顶悬浮窗，三按钮（暂停/停止/退出）+ 状态指示 + 字幕滚动区 + 摘要区
- **TranscriptPanel**: 可滚动字幕面板，支持原文+译文双行显示，自动滚动
- **FinalSubtitleWindow**: 完整译文历史，独立拖拽，位置配置持久化到 JSON
- **双向布局**: `set_dual_mode(True)` 切换为左右分栏（系统音频译文 | 麦克风译文）
- 信号机制: `summary_updated` / `translation_received` 解耦数据流

**测试方式**:
```bash
python main.py
# 观察悬浮窗是否正确显示
# 点击 ⚙ 打开设置，修改参数后保存
# 验证暂停/继续/停止按钮功能
```

---

## PR #6a: 摘要引擎

**标题**: feat: 会议摘要引擎

**功能描述**:
基于 LLM 的渐进累积式会议摘要模块，实时生成并持续更新会议纪要。

**实现思路**:
- 定时（默认 60s）调用 LLM 生成中文摘要
- 渐进累积模式：每次输入「上次摘要 + 新字幕」，产出更新后的整体摘要
- 会话结束时自动生成结构化 Markdown 纪要并保存到文件

**测试方式**:
```python
from core.summarizer import Summarizer

s = Summarizer(on_summary_text=lambda t: print(f"摘要: {t}"))
s.start()
s.push("Hello, today we will discuss the quarterly results.")
s.push("Revenue grew 15% year-over-year.")
# 等待 summary_interval 秒后观察输出
summary = s.stop()
print(summary)
```

---

## PR #6b: 上下文纠错引擎

**标题**: feat: 上下文纠错引擎

**功能描述**:
基于 LLM 的滑动窗口上下文纠错模块，利用对话上下文修正翻译结果中的歧义和错误。

**实现思路**:
- 滑动窗口缓存最近 8 句翻译，间隔 3 句触发一次 LLM 修正
- 将上下文注入 prompt 提升修正准确率，避免孤立翻译的语义偏差
- 异步回调不阻塞翻译管线，确保实时性不受影响

**测试方式**:
```python
from core.corrector import ContextCorrector

c = ContextCorrector(on_correction=lambda r: print(f"修正: {r.old_text} → {r.new_text}"))
c.start()
c.push_sentence(text="收入增长了15%", original="Revenue grew 15%", start_ms=0, end_ms=1000)
c.push_sentence(text="利润下降了", original="Profit declined", start_ms=2000, end_ms=3000)
# 观察 LLM 修正输出
c.stop()
```

---

## PR #7: 设置窗口 + 主程序管线集成

**标题**: feat: 设置窗口 + 主程序管线集成

**功能描述**:
完整的主程序入口，串联所有模块形成可运行的桌面应用。

**实现思路**:
- **main.py**: start_engine / stop_engine 管理整条管线生命周期；双向翻译模式同时启动 sys + mic 两条管线；暂停/恢复/停止会话/退出四种操作面板
- **热重启**: 设置保存 → QTimer 延迟停止旧引擎 → 等待线程退出 → 启动新引擎
- **SettingsWindow**: 表单式设置界面，四大参数区域，保存触发热重启
- **会议纪要**: stop_session 自动调用 summarizer.stop() 生成 Markdown 保存为 `会议纪要_YYYYMMDD_HHMMSS.md`
- **系统托盘**: 最小化到托盘，右键菜单恢复/退出

**测试方式**:
```bash
# 1. 配置 API Key
cp config.example.ini config.ini
# 编辑填入真实 API Key

# 2. 启动
python main.py

# 3. 验证
# - 悬浮窗出现
# - 系统音频翻译正常运行
# - 点击暂停/继续正常
# - 点击设置修改参数 → 热重启生效
# - 点击停止 → 生成会议纪要
```

---

## PR #8: 文档完善与发布准备

**标题**: docs: PR 描述文档 + README 完善

**功能描述**:
补齐比赛要求的文档，确保仓库可评审。

**改动**:
- 新增 `PR_DESCRIPTIONS.md`（本文件）：每笔 PR 的四要素完整记录
- 完善 `README.md`：补全配置说明表格、Demo 视频链接占位

**测试方式**:
评审者可直接阅读 PR_DESCRIPTIONS.md 了解开发全过程。

---

## PR #9: Bug修复 + UI优化 + 演示视频

**标题**: fix: 设置窗口闪现修复 + UI重构 + 字号自定义 + Demo视频

**功能描述**:
修复多个用户体验问题，并补齐比赛要求的演示视频。

**实现思路**:

1. **变量拼写修复** — `_orig_silence` → `_original_silence`，修复保存设置时 AttributeError 崩溃
2. **热重启移除** — 引擎配置变更后改为弹窗提示 + 自动退出，避免 WebSocket/PyAudio 清理时的静默崩溃
3. **设置面板双列布局** — 左右分栏（翻译API/断句/双向 + 翻译方向/音频/LLM），窗口扩至 700-900px，无需滚动
4. **字幕字号扩展** — 实时字幕新增 10 号（极小），最终译文新增 12 号（极小），右键菜单即可切换
5. **提示框深色主题** — QMessageBox 使用自定义深色样式，文字清晰可见
6. **README 完善** — 补全第三方依赖清单（websocket-client、requests）、声明原创范围、修正仓库 URL
7. **Demo 视频** — 上传 181MB 演示视频（Git LFS），README 添加可点击链接

**测试方式**:
```bash
# 1. 打开设置 → 展开所有面板 → 确认两列并排 + 保存按钮可见
# 2. 右键字幕窗口 → 选择"极小"字号 → 确认 10/12 号生效
# 3. 修改翻译引擎参数 → 保存 → 确认弹窗提示需重启
# 4. git clone 后 pip install -r requirements.txt → 确认无缺依赖
# 5. 点击 README 中 Demo 视频链接 → 确认可播放
```
