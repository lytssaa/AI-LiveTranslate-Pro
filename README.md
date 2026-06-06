# AI LiveTranslate Pro

实时语音识别与双向翻译桌面应用，基于百炼 Gummy-Realtime-V1 翻译引擎 + DeepSeek LLM 语义分析。

## 功能特性

- 系统音频 / 麦克风实时捕获与翻译
- 双向翻译模式（系统音频→中文 + 麦克风→英文）
- 渐进累积式会议摘要
- 上下文纠错引擎
- 悬浮字幕窗口 + 最终译文窗口（支持右键自定义字号/颜色）
- 可视化设置面板（翻译 API / LLM / 音频参数一站式配置）

## 技术栈

| 类别 | 技术 | 用途 |
|---|---|---|
| 框架 | PyQt6 | GUI 界面 |
| 音频 | PyAudioWPatch | WASAPI Loopback 系统音频捕获 |
| 翻译 | websocket-client | 百炼 Gummy-Realtime-V1 WebSocket 实时翻译 |
| AI | requests | LLM API 调用（摘要 & 纠错） |

> 以上为第三方依赖，通过 `pip install -r requirements.txt` 安装。核心业务逻辑（翻译管线、摘要引擎、纠错引擎、UI 组件）均为原创实现。

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
