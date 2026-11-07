# 视频溯源

本仓库是一个 Python 工具，用于从 B 站视频链接找出原视频、原作者线索和作者频道 ID。

完整工作指引见 [AGENTS.md](AGENTS.md)（环境准备、命令用法、输出含义、能力边界、汇报时必须保留的注意事项）。

## 快速开始

```bash
python -m pip install -r requirements.txt   # numpy + opencv-python-headless
# 另需系统安装 ffmpeg 和 yt-dlp 到 PATH

python scripts/trace.py BV1rCxf6MERM
```

默认输出 7 行中文摘要；加 `--json` 输出完整证据 JSON。

## 最重要的三条

1. **引擎永不输出 `confirmed`。** 归属等级最高是 `strong_lead`，永远是「线索」，需人工确认。向用户汇报时不要说成「已确认」「已证实」。

2. **`earliest_known` 和 `creator_conclusion` 回答不同问题。** 前者答「哪条视频最早」，后者答「谁被署名为作者」。不要互相替代 —— 找到更早的搬运副本不代表找到了原作者。

3. **没找到不等于不存在。** B 站搜索结果和风控会波动。`coverage_incomplete=true` 时要说「部分通道被限流，结果可能不完整」，不要说「确定没有」。

## 测试

```bash
python tests/run_all.py    # 11 套件 / 367 项断言，全离线
```
