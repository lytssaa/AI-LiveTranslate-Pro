# AI LiveTranslate Pro

🎯 **国际会议实时双向翻译系统** — 系统音频 + 麦克风双通道并行翻译，基于百炼 Gummy-Realtime-V1 + LLM 语义增强。

## 创新亮点

> **双引擎独立管线**：系统音频（如 Zoom 外方发言）→ 中文，麦克风（己方发言）→ 英文，两条管线独立运行互不干扰。接入 TTS 后即可实现**听 + 说双向同传**，覆盖国际会议全场景。

| 创新点 | 说明 |
|---|---|
| 🔄 双向实时翻译 | 双 Gummy 实例并行，系统音频+麦克风同时翻译，业界少见 |
| 🧠 AI 语义增强 | 摘要引擎 + 上下文纠错，不只是翻译，而是"理解"对话 |
| 🎛️ 全可视化控制 | 悬浮窗拖拽/缩放、右键自定义字号颜色、设置面板热配置 |
| 🔌 扩展就绪 | 预留 TTS 接口，接入即可实现语音播报，完成听→译→说闭环 |

## 功能特性

- 系统音频 / 麦克风实时捕获与翻译
- **双向翻译模式**（系统音频→中文 + 麦克风→英文，双管线并行）
- 渐进累积式会议摘要（LLM 自动生成）
- 上下文纠错引擎（翻译后语义修正）
- 悬浮字幕窗口 + 最终译文窗口（右键自定义字号/颜色）
- 可视化设置面板（翻译 API / LLM / 音频参数一站式配置）

## 应用场景

- 🌍 国际线上会议实时翻译
- 🎓 跨语言在线课程字幕
- 📺 外语直播/视频实时字幕
- 🗣️ 接入 TTS 后：听译一体同传耳机

## 技术栈

| 类别 | 技术 | 用途 |
|---|---|---|
| 框架 | PyQt6 | GUI 界面 |
| 音频 | PyAudioWPatch | WASAPI Loopback 系统音频捕获 |
| 翻译 | websocket-client | 百炼 Gummy-Realtime-V1 WebSocket 实时翻译 |
| AI | requests | LLM API 调用（摘要 & 纠错） |

> 以上为第三方依赖，通过 `pip install -r requirements.txt` 安装。核心业务逻辑（双管线翻译架构、摘要引擎、纠错引擎、UI 组件）均为原创实现。

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/lytssaa/AI-LiveTranslate-Pro.git
cd AI_LiveTranslate_Pro

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API 密钥
cp config.example.ini config.ini
# 编辑 config.ini，填入你的百炼 API Key 和 LLM API Key

# 5. 运行
python main.py
```

## 配置说明

| 配置项 | 说明 |
|---|---|
| `api_key` | 百炼 Gummy-Realtime-V1 API 密钥 |
| `llm_api_key` | DeepSeek / 百炼兼容接口 API 密钥 |
| `bidirectional_enabled` | 是否启用双向翻译（true/false） |
| `audio_source` | 音频源：system（系统音频）/ mic（麦克风） |
| `summary_interval` | 摘要生成间隔（秒） |

## Demo 视频

[▶ 观看演示视频](demo.mp4)

## 许可证

MIT License
