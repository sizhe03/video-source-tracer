# 视频溯源工具

本仓库是 Python 工具，从 B 站视频链接找出原视频、原作者线索和作者频道 ID。

完整指引见 [AGENTS.md](AGENTS.md)。

## 用法

```bash
python -m pip install -r requirements.txt
python scripts/trace.py BV1rCxf6MERM
```

## 汇报时必须保留的边界

- 归属等级最高为 `strong_lead`，永不输出 `confirmed`。这是线索，不是已证实的身份。
- 「原视频」指目前找到的最早副本，不等于首次发布或原作者。
- 未找到结果时不要说成「确定不存在」，搜索引擎结果会波动。

## 测试

```bash
python tests/run_all.py
```
