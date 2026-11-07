# video-source-tracer

给定 B 站视频链接或 BV 号，整理更早副本、原作者及频道 ID 的高可能性线索和证据。

Trace a Bilibili video to earlier copies and possible creator identities, with evidence for human review.

## 发布状态与验证范围

当前定位为**公开测试版**。CLI 的 `1.0.0` 是程序版本标识，不代表所有平台均已通过验证。

| 范围 | 状态 |
| --- | --- |
| Windows 本机 | 已在原目录及临时复制目录的全新虚拟环境安装依赖并通过离线回归；此前验证过少量 B 站真实案例 |
| Linux / macOS | 已配置 GitHub Actions 离线测试；以仓库实际运行记录为准，尚不宣称已通过 |
| DSH | 本机已加载此 Skill 并调用脚本 |
| 其他 agent | 提供 Skill 及仓库指引；尚未逐一安装、加载和端到端验证 |

主 CLI 当前接受 **BV 号或包含 BV 号的 B 站链接**。短链先使用 `scripts/resolve.py` 还原；其他平台链接、截图、标题属于 Skill 的调查工作流，依赖 agent 可用的浏览、搜索和媒体工具，不是主 CLI 的通用输入。

## 用法 A：独立 Python 工具

不依赖 DSH 或 agent。`scripts/` 可以独立运行，`_common.py` 是项目自带模块。

### 三步上手

1. 下载或克隆本仓库，进入 `video-source-tracer` 目录，准备 Python 3.10+ 和虚拟环境：

   ```bash
   python -m venv .venv
   ```

   Linux/macOS 用 `source .venv/bin/activate` 激活；Windows PowerShell 用 `.\.venv\Scripts\Activate.ps1`。

2. 安装 Python 依赖和外部命令：

   ```bash
   python -m pip install -r requirements.txt
   python -m pip install "yt-dlp[default]"
   ```

   `numpy` 和 `opencv-python-headless` 用于媒体分析。`yt-dlp` 是外部下载命令，需在运行环境的 `PATH` 中；也可以按 [yt-dlp 官方安装说明](https://github.com/yt-dlp/yt-dlp#installation)安装独立可执行文件。

   另行安装 **FFmpeg 可执行程序**，不是同名 Python 包。Linux 可通过发行版包管理器安装 `ffmpeg`；macOS 可用 `brew install ffmpeg`；Windows 可从 [FFmpeg 下载页](https://ffmpeg.org/download.html)选择构建包，解压并将包含 `ffmpeg.exe` 的目录加入 `PATH`。安装完成后检查：

   ```bash
   ffmpeg -version
   yt-dlp --version
   ```

3. 运行溯源：

   ```bash
   python scripts/trace.py BV1rCxf6MERM
   python scripts/trace.py "https://www.bilibili.com/video/BV1rCxf6MERM"
   ```

### 参数与输出

默认输出不超过 10 行的中文摘要；远端标题中的换行会合并。摘要里的“原视频”指目前找到的最早副本，不能据此认定首次发布者就是原作者。

```bash
python scripts/trace.py BV1rCxf6MERM --json
python scripts/trace.py BV1rCxf6MERM --budget 60 --max-requests 80
python scripts/trace.py --help
python scripts/trace.py --version
```

| 参数 | 含义 |
| --- | --- |
| `视频` | BV 号或包含 BV 号的完整 B 站链接 |
| `--json` | 输出完整 JSON，包括所有原始候选、证据、诊断、受限请求等 |
| `--budget 秒` | 搜索阶段预算，默认 90；必须大于 0。不含初始视频读取，在途请求和收尾可使总耗时超过该值，并非严格的程序超时 |
| `--max-requests 次数` | 搜索阶段计数请求预算，默认 120；必须为正整数，不含初始视频读取 |
| `--version` | 显示版本号 |

JSON 保留两个独立字段：`earliest_known` 回答哪条视频更早，`creator_conclusion` 回答谁被署名为作者。`creator_conclusion.corroborating_accounts` 是摘要用的账号计数（`independent_evidence_groups` 在部分分支统计的是作品数，两者语义不同）。缺少上传者身份时，账号数只计已知身份。

`creator_conclusion.level` 最高为 `strong_lead`，永不输出 `confirmed`：

| 等级 | 阅读方式 |
| --- | --- |
| `strong_lead` | 较强线索，仍需人工打开频道确认 |
| `weak_lead` | 较弱线索，署名尚未充分印证 |
| `guess` | 仅评论猜测，不能据此认定作者 |
| `unconfirmed` | 未找到可用作者线索 |

见证作品数来自检索到的频道或第三方存档目录，不代表逐条完成媒体核验，也不保证频道当前仍可访问。`earliest_known.original_links` 非空时，摘要显示对应的原始 YouTube 链接。`coverage_incomplete` 为真时，摘要末尾会提示“部分通道被限流，结果可能不完整”。初始视频读取失败时程序以非零状态退出，不会伪造结果；诊断消息写入 stderr。

## 用法 B：作为 agent skill

先完成上面的依赖安装，并确保 agent 使用的运行环境能找到 Python、FFmpeg 和 yt-dlp。

### Skill 安装与仓库指引是两回事

`SKILL.md` 并非 DSH 专有格式。支持 Agent Skills 的宿主（例如具备相应功能的 Claude Code、Codex）也可以读取这种「YAML 元数据 + Markdown 正文」结构。请按所用产品及版本的安装说明，将**整个目录**安装到其技能目录；保留 `scripts/`、`references/` 等资源。不是只复制一个 Markdown 文件。

`AGENTS.md`、`CLAUDE.md` 等则是**在本仓库中工作时的指引**，不等于在任意对话中安装了可自动触发的 Skill。是否加载、何时触发以及命令权限由宿主版本、设置和工作目录决定。

| 文件 | 用途与验证状态 |
|---|---|
| [`SKILL.md`](SKILL.md) | Skill 操作流程；本机 DSH 已加载，其他宿主待验证 |
| [`AGENTS.md`](AGENTS.md) | 支持该约定的 agent 的仓库指引；各宿主加载情况待验证 |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code 仓库指引；待验证 |
| [`.cursor/rules/video-trace.mdc`](.cursor/rules/video-trace.mdc) | Cursor 条件规则，当前并非始终加载；待验证 |
| [`.github/copilot-instructions.md`](.github/copilot-instructions.md) | 支持仓库自定义指引的 Copilot 环境；待验证 |

核心 Python 脚本不导入 DSH 运行时。基础路径需要读文件、运行 Python 和网络访问能力；完整 Skill 的截图、跨平台补查等路径可能还需要宿主提供搜索、浏览或媒体工具。仅能聊天、没有执行环境的模型不会因为读取 Skill 就获得这些能力。

所有示例命令均从本仓库（或已安装 Skill 的根目录）执行；agent 不应假设自己的当前目录就是技能目录。读取结构化证据时使用 `python scripts/trace.py <BV号> --json`，默认文字摘要不适合 JSON 解析。

DSH 用户从仓库的上级目录复制整个目录：

```bash
mkdir -p ~/.dsh/skills
cp -r video-source-tracer ~/.dsh/skills/
```

Windows PowerShell 对应：

```powershell
New-Item -ItemType Directory -Force "$HOME/.dsh/skills"
Copy-Item -Recurse ./video-source-tracer "$HOME/.dsh/skills/"
```

然后对支持该 skill 机制的 agent 说：“帮我看这个视频的原作者：BV1rCxf6MERM”。[SKILL.md](SKILL.md) 是给 agent 读取的说明，`scripts/` 是独立 Python 工具，两者可分开使用。需要结构化证据的调用方应使用 `--json`；默认输出已从 JSON 改为摘要。

## 真实案例与示例输出

以下案例来自 2026-11-07 发布交接记录。第一例还在发布整理时执行了真实网络请求，下面保留其实际输出；第二、第三例按新版摘要格式重排，仅摘录交接记录能够支持的行，不补造标题或账号数。网络变化后不保证逐字复现。

`BV1rCxf6MERM`：

```text
输入    : BV1rCxf6MERM  【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音
原视频  : BV1wGao65EnF  早 35.49 天（目前找到的最早副本）
          【ASMR】丝袜“脱了”放我耳朵上？/丝袜触发音
原作者  : pomechan  （较强线索 / strong_lead，41 个账号印证）
作者 ID : UC1Y-RkDrol_nyXk7vx8wgUA
见证作品: 239 条（目录证据，归属待人工确认）
说明    : 归属为高可能性线索，需人工打开频道确认
```

`BV1Xpxf6QEqk`（节选）：

```text
原视频  : BV1xpHq6BEDR  早 32.57 天（目前找到的最早副本）
作者 ID : 未找到频道 ID 线索
说明    : 署名证据不足，需更多独立来源并人工打开频道核对
```

该次作者线索为 `Kirin Camp / weak_lead`。

`BV1hSuV6UESm`（节选）：

```text
原视频  : 未找到更早副本
原作者  : 桑塔阿普亚  （仅评论猜测 / guess，0 个账号印证）
作者 ID : 未找到频道 ID 线索
说明    : 名字仅来自猜测，不能据此认定作者，需人工核实
```

| 输入 BV 号 | 最早副本 | 作者线索 / 强度 | 作者 ID | 诊断或结果 |
| --- | --- | --- | --- | --- |
| `BV1rCxf6MERM` | `BV1wGao65EnF`，早 35.49 天 | POMEchan / `strong_lead` | `UC1Y-RkDrol_nyXk7vx8wgUA` | `complete` |
| `BV1Xpxf6QEqk` | `BV1xpHq6BEDR`，早 32.57 天 | Kirin Camp / `weak_lead` | — | `unconfirmed_creator` |
| `BV1hSuV6UESm` | 未找到 | 桑塔阿普亚 / `guess` | — | `name_is_a_guess` |
| `BV1p4KhzoEck` | 未找到 | 抖音号已改号失效 | — | 未得到可用归属结果 |

`complete` 仅表示「更早副本和署名线索均有结果」，不表示作者归属已证实。第三个案例的作者线索强度为 `guess`，因为它只来自一条带「应该是」的评论，没有任何标题或简介署名。

## 能力边界

这不是“已证实原作者鉴定器”，是**高可能性线索 + 证据整理**工具：

- 字符串证据（标题、页面、名字匹配）无法证明归属。归属证明留给人；频道 ID 同样只是字符串证据线索。
- B 站搜索结果和风控会波动，同一链接不同时刻可能拿到不同副本。
- 引擎如实报告 `coverage_incomplete` / `blocked_requests`，不编造答案。遇到 B 站 412 风控时应停止频繁重试；缺失结果不能证明原视频不存在。
- 较有利的场景：搬运号保留了上游署名，例如标题写“作者：Kirin Camp”；仍取决于检索结果和访问情况，不保证成功。
- 较困难的场景：标题、简介、标签均抹掉署名；自动路径可能只有猜测或空结果，agent 可根据可用工具进一步调查。

## 验证与许可证

```bash
python tests/run_all.py
```

测试入口执行模块编译、UTF-8 编码扫描和离线回归套件，包含 CLI 摘要、JSON 完整性及参数处理测试。通过测试表示已有用例通过，不等于全部逻辑覆盖或真实平台始终可用。

[GitHub Actions](.github/workflows/ci.yml) 配置了 Windows、Ubuntu、macOS 与 Python 3.10、3.12 的矩阵。每个任务在独立 runner 安装依赖，检查 CLI 帮助、版本、错误退出码，并运行离线测试；不请求 B 站或 YouTube。工作流上传后还需查看 Actions 实际结果，配置文件本身不是通过证明。该矩阵也不验证各 agent 的 Skill 加载、真实媒体下载或系统 FFmpeg 集成。

真实网络请求不属于离线回归测试，发布前应单独低频验证。

[MIT License](LICENSE)，Copyright (c) 2026 YG。
