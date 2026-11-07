# 视频溯源

本仓库是一个 Python 工具，用于从 B 站视频链接找出原视频、原作者线索和作者频道 ID。

完整工作指引见 [AGENTS.md](AGENTS.md)（环境准备、命令用法、输出含义、能力边界、汇报时必须保留的注意事项）。

这是仓库级指引，不是 Skill 安装步骤；具体加载行为取决于 Claude Code 版本和设置，尚未在该宿主完成端到端验证。

## 快速开始

先定位仓库根目录并从该目录运行命令：

```bash
python -m pip install -r requirements.txt   # numpy + opencv-python-headless
# 另需系统安装 ffmpeg 和 yt-dlp 到 PATH

python scripts/trace.py BV1rCxf6MERM --json
```

agent 读取证据时使用 `--json`；省略时是最多 10 行的文字摘要。搜索预算不含初始读取，在途请求和收尾可能增加总耗时，详见 AGENTS.md。

## 最重要的三条

1. **引擎永不输出 `confirmed`。** 归属等级最高是 `strong_lead`，永远是「线索」，需人工确认。向用户汇报时不要说成「已确认」「已证实」。

2. **`earliest_known` 和 `creator_conclusion` 回答不同问题。** 前者答「哪条视频最早」，后者答「谁被署名为作者」。不要互相替代 —— 找到更早的搬运副本不代表找到了原作者。

3. **没找到不等于不存在。** B 站搜索结果和风控会波动。`coverage_incomplete=true` 时要说「部分通道被限流，结果可能不完整」，不要说「确定没有」。

## 测试

```bash
python tests/run_all.py    # 离线回归；以实际报告为准
```
