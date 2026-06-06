# utils/config.py — API Key 与全局配置管理
# 使用 configparser 读取 config.ini，支持环境变量覆盖

import configparser
import os
from pathlib import Path

# 配置文件路径（项目根目录下）
CONFIG_FILE = Path(__file__).parent.parent / "config.ini"

# 默认配置值
_DEFAULTS = {
    # ── 百炼 Gummy-Realtime-V1 ──
    "api_key": "",
    "api_url": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
    "gummy_model": "gummy-realtime-v1",
    "gummy_source_language": "auto",          # auto/en/zh/ja/ko 等
    "gummy_target_language": "zh",             # 翻译目标语言
    "gummy_format": "pcm",                     # 音频格式
    "gummy_sample_rate": "16000",              # 音频采样率（Hz）
    "gummy_max_end_silence": "800",            # VAD 静音断句阈值（ms）

    # ── LLM 语义分析（预留）──
    "llm_api_key": "",
    "llm_api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "llm_model": "qwen-plus",

    # ── 行为参数 ──
    "summary_interval": "60",       # 增量主题分析间隔（秒）
    "audio_chunk_ms": "100",        # 音频分片大小（毫秒）
    "audio_sample_rate": "16000",   # 目标音频采样率（Hz，需与 gummy_sample_rate 一致）
    "audio_source": "system",        # 音频输入源：system=系统音频回环，mic=麦克风
    "audio_input_device": "-1",     # 麦克风设备索引，-1=默认设备
    "bidirectional_enabled": "false",  # 双向翻译：同时运行系统音频→中文 + 麦克风→英文
    "output_dir": "output",         # 导出文件默认目录
}


def load_config() -> dict:
    """加载配置，环境变量优先于配置文件，配置文件优先于默认值"""
    cfg = dict(_DEFAULTS)

    # 读取 config.ini（如果存在）
    parser = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        parser.read(CONFIG_FILE, encoding="utf-8")
        section = "DEFAULT"
        for key in cfg:
            if parser.has_option(section, key):
                val = parser.get(section, key)
                # 空字符串不覆盖默认值
                if val.strip():
                    cfg[key] = val

    # 环境变量覆盖（大写 KEY 优先）
    env_map = {
        "DASHSCOPE_API_KEY": "api_key",
        "DASHSCOPE_API_URL": "api_url",
        "GUMMY_SOURCE_LANG": "gummy_source_language",
        "GUMMY_TARGET_LANG": "gummy_target_language",
        "LLM_API_KEY": "llm_api_key",
        "LLM_API_URL": "llm_api_url",
        "LLM_MODEL": "llm_model",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = val

    return cfg


def save_config(updates: dict) -> None:
    """将配置变更写入 config.ini（仅更新传入的 key，保留其余不变）"""
    parser = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        parser.read(CONFIG_FILE, encoding="utf-8")
    # DEFAULT 是 configparser 保留 section，始终隐式存在，直接操作即可
    for key, value in updates.items():
        parser.set("DEFAULT", key, str(value))
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        parser.write(f)
    # 同步回全局 CONFIG
    CONFIG.update(updates)


# 模块级全局配置对象（直接 from utils.config import CONFIG 使用）
CONFIG = load_config()
