# 视频溯源（video source tracing）

给定 B 站视频链接、短链或 BV 号，找出原视频、原作者线索和作者频道 ID。

本文件是给 AI agent 的工作指引，适用于任何支持 `AGENTS.md` 约定的工具（Codex、Cursor、Zed、Windsurf 等）。DSH 用户请直接使用 [SKILL.md](SKILL.md)（格式不同，内容等价）。

---

## 环境准备

只需要三样东西，不需要任何 agent 专有能力：

```bash
python -m pip install -r requirements.txt   # numpy + opencv-python-headless
# 另需系统安装 ffmpeg 和 yt-dlp 到 PATH
```

检查：

```bash
ffmpeg -version
yt-dlp --version
python scripts/trace.py --version
```

---

## 怎么用

**唯一入口：`python scripts/trace.py <BV号或链接>`**

```bash
python scripts/trace.py BV1rCxf6MERM
python scripts/trace.py "https://www.bilibili.com/video/BV1rCxf6MERM"
python scripts/trace.py BV1rCxf6MERM --json          # 完整证据 JSON
python scripts/trace.py BV1rCxf6MERM --budget 60     # 时间预算（秒）
```

默认输出 7 行中文摘要，例如：

```text
输入    : BV1rCxf6MERM  【ASMR】丝袜"脱了"放我耳朵上？_丝袜触发音
原视频  : BV1wGao65EnF  早 35.49 天（目前找到的最早副本）
原作者  : pomechan  （较强线索 / strong_lead，41 个账号印证）
作者 ID : UC1Y-RkDrol_nyXk7vx8wgUA
见证作品: 239 条（目录证据，归属待人工确认）
说明    : 归属为高可能性线索，需人工打开频道确认
```

需要结构化数据时加 `--json`。

---

## 输出怎么读

三个字段回答三个不同的问题，**绝不能互相替代**：

| 字段 | 回答 | 不回答 |
|---|---|---|
| `earliest_known` | 哪条上传最早、早多少天 | 谁拍的 |
| `creator_conclusion` | 谁被署名为作者、频道 ID | 署名是否属实 |
| `diagnosis` | 链路断在哪一步 | — |

`creator_conclusion.level` 四个等级：

| 等级 | 含义 | 你能做什么 |
|---|---|---|
| `strong_lead` | 显式署名被 ≥2 个不同上传者印证，或品牌跨平台复现 | 可以告诉用户「很可能是这个人」，仍需人工确认 |
| `weak_lead` | 单条署名，未经印证 | 只能说「有一条署名线索」 |
| `guess` | 只有评论里的猜测（带「应该是」等词） | 必须说明这是猜测 |
| `unconfirmed` | 未找到 | 如实说没找到 |

**引擎永不输出 `confirmed`。** 字符串证据（标题、页面、名字匹配）无法证明归属，所以归属永远是「线索」，由人确认。向用户汇报时不要把它说成「已确认」「已证实」。

---

## 向用户汇报时必须保留的边界

1. **「原视频」= 目前找到的最早副本**，不等于首次发布，更不等于原作者。
2. **频道 ID 是字符串证据线索**，不是已证实身份。带 `status=lead_from_string_evidence`。
3. **B 站搜索结果会波动**，同一链接不同时刻可能拿到不同副本。没找到不等于不存在。
4. **`coverage_incomplete=true` 时** 要告诉用户「部分通道被限流，结果可能不完整」，不要说成「确定没有」。
5. 遇到 B 站 412 风控应停止重试，不要反复请求。

---

## 什么时候它帮不上忙

| 场景 | 结果 |
|---|---|
| 搬运号保留了上游署名（标题写「作者：XXX」、简介有 youtu.be 链接） | ✅ 能找到 |
| 搬运号彻底洗稿（标题、简介、标签全抹掉署名） | ❌ 只能给 `guess` 或 `unconfirmed` |
| 原作者账号已删除 | ⚠️ 可能通过第三方存档站点找回目录，但不保证 |
| 非视频内容（图片、纯文字） | ❌ 不支持 |

---

## 真实案例

| 输入 | 最早副本 | 作者线索 | 诊断 |
|---|---|---|---|
| `BV1rCxf6MERM` | `BV1wGao65EnF`，早 35.49 天 | `POMEchan` / `strong_lead`，ID `UC1Y-RkDrol_nyXk7vx8wgUA` | `complete` |
| `BV1Xpxf6QEqk` | `BV1xpHq6BEDR`，早 32.57 天 | `Kirin Camp` / `weak_lead` | `unconfirmed_creator` |
| `BV1hSuV6UESm` | 未找到 | `桑塔阿普亚` / `guess`（仅评论猜测） | `name_is_a_guess` |

---

## 其他脚本（按需）

主流程由 `trace.py` 一次跑完。以下脚本用于特定的补充调查：

```bash
python scripts/resolve.py "https://b23.tv/kwoerN3"        # 短链还原
python scripts/probe.py BV1TrxQ64EXo                       # 单视频元数据
python scripts/find_earlier.py BV1TrxQ64EXo                # 同站更早副本
python scripts/verify.py pixels A.jpg B.jpg                # 封面/画面对比
python scripts/auprint.py --video ORIG.mp4 --video COPY.mp4 # 音频指纹比对
python scripts/channel.py "@candidate" --duration 631      # 频道目录核查
```

`distinctive` 查询、别名展开等高级用法见 [SKILL.md](SKILL.md)。

---

## 测试

```bash
python tests/run_all.py    # 11 套件 / 367 项断言，全离线
```

离线测试覆盖全部判定逻辑。真实网络请求不在回归测试内，发布前单独低频验证。
