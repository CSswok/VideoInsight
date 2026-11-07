---
name: video-deep-reading
display_name: 视频深度精读
display_name_en: Video Deep Reading
description: "把 YouTube / Bilibili / 抖音 / 小红书 等平台的视频做成中文逐段精读对照页：左栏原话（带秒级时间码、可点击跳转），右栏逐段解读，配套术语表、批判性注记和小红书文案。支持三种讲解粒度——时间表对照（默认）、关键节点讲解、逐片讲解。自动处理无字幕视频（内嵌硬字幕 OCR + 本地语音转写双通道）并交叉校验识别错误。触发词：视频讲解、精读这个视频、解读这个视频、拆解视频、视频逐段、YouTube 讲解、B站讲解、bilibili 讲解、抖音讲解、小红书视频、帮我看懂这个视频、视频文案、做视频笔记。"
description_zh: "把 YouTube/B站/抖音/小红书的视频转成左右对照的中文逐段精读页，含术语表、批判注记和小红书文案，支持三种粒度，自动处理无字幕视频。"
description_en: "Turn videos from YouTube, Bilibili, Douyin, Xiaohongshu into a side-by-side Chinese deep-reading page with timestamped transcript, commentary, glossary, critical notes and Xiaohongshu copy."
category: content
version: 1.0.0
author: WorkBuddy
agent_created: true
allowed-tools: "Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch, present_files, AskUserQuestion"
---

# 视频深度精读

## 这个 skill 解决什么问题

用户发来一个视频链接，说"帮我讲解"。但"讲解"有三层完全不同的深度：

1. **泛泛总结** —— 说这集讲了什么。这个不需要 skill，看简介就行。
2. **逐段翻译** —— 每句话都译成人话，但不知道哪些重要。
3. **精准解读** —— 术语逐个拆、原话逐句对、观点逐条判，还告诉你哪里站不住。

**本 skill 只做第 3 层。** 因为用户要的是"我完全不懂这个领域，里面全是我不认识的词"——
这种情况下，跳过任何一段都会造成概念断裂，而照抄原话又等于没讲。

## 支持哪些平台

| 平台 | 状态 | 备注 |
|---|---|---|
| YouTube | ✅ 稳定 | 字幕接口常 429，靠回退；需能出境的代理 |
| Bilibili（含 b23 短链） | ✅ 稳定且**最快** | 252 秒视频 26 秒处理完 |
| 抖音 | ✅ **已验证** | 走 Playwright 浏览器通道（yt-dlp 过不了签名）；14 分钟视频 63 秒完成 |
| 小红书 | ⚠️ 链接有时效性 | 分享链接会过期，需用户重新复制 |
| 本地视频文件 | ✅ | 兜底方案，永远可用 |

**无口播视频会被自动拦下。** 纯音乐/纯画面视频会让语音识别产生幻觉
（实测出现"字幕志愿者 李宗盛"重复多次）。脚本用字/秒密度检测，
命中时先问用户"这条有口播吗"，不要硬写解读。详见 `references/platforms.md`。

**代理策略按平台分流**，不是一刀切——细节和实测数据见 `references/platforms.md`。
一句话：**国内平台保留系统代理，海外平台必须有代理。**

## 三种模式（默认时间表对照）

用户在调用时选择，未指定则用 `s`。

| mode | 名称 | 分段粒度 | 适用 |
|---|---|---|---|
| `s` | **时间表对照**（默认） | 60-120 秒/段 | 一般场景。信息完整，可读性好。 |
| `k` | 关键节点讲解 | 100-200 秒/段，只选高信息密度段 | 视频很长（>40min），或用户只想要重点 |
| `f` | 逐片讲解 | 40-90 秒/段，不跳过任何内容 | 用户明确要求"精细""不漏""全片" |

模式只影响**分段密度**和**解读篇幅**，不影响质量要求。
三种模式的具体差异与选择判断见 `references/modes.md`。

## 执行流程

严格按顺序执行。每一步都要真跑，不要跳步或凭猜测填内容。

### Step 0 首次使用：环境自检

第一次在本机运行时，先跑一次：

```bash
bash <SKILL_DIR>/scripts/setup.sh
```

它会检查 `ffmpeg`/`yt-dlp`、编译 OCR 工具、创建转写环境并安装 `mlx-whisper`。
**幂等，重复跑安全。** 已经装好的环境再跑只花几秒。

转写环境装在 `~/.workbuddy/cache/video-deep-reading-venv`（约 1.2G），
**不放在 skill 目录里**，这样 skill 本身保持轻量（<200K），便于分发。

若 `fetch_video.py` 报"未找到可用的 mlx-whisper"，就是这步没做。

### Step 1 摸底：拿到视频并对齐预期

```bash
python3 <SKILL_DIR>/scripts/fetch_video.py "<URL 或本地文件>" "<WORKDIR>"
```

按平台可能需要补参数：

```bash
# YouTube / Bilibili：直接跑
fetch_video.py "https://www.bilibili.com/video/BV1xx" ./work

# 抖音：自动走浏览器通道，无需 cookie
fetch_video.py "https://www.douyin.com/jingxuan?modal_id=xxx" ./work
# 若页面没自动播放：加 --dy-headed 看窗口手动点一下

# 小红书：先加 cookie 试；若报 No video formats 说明是图文笔记
fetch_video.py "https://www.xiaohongshu.com/explore/xxx" ./work --cookies-from-browser chrome

# 本地文件（最终兜底，永远可用）
fetch_video.py "/path/to/video.mp4" ./work --lang zh
```

脚本自动完成：按平台选择代理策略、取元信息、下载音频与 h264 视频，
并按三级回退获取文本：

1. **yt-dlp 现成字幕** —— 最快最准，但常遇 429 或无字幕
2. **内嵌硬字幕 OCR** —— 用 macOS Vision 逐帧识别视频底部字幕条，中文财经类视频常见，
   **通常比语音识别准**
3. **mlx-whisper 本地转写** —— 兜底

输出 `<WORKDIR>/transcript.json`，含 `source` 字段标明走的是哪条路。

**如果同时拿到了字幕和音频，务必做交叉校验：**

```bash
python3 <SKILL_DIR>/scripts/crosscheck.py \
  "<OCR字幕.json>" "<WORKDIR>/transcript.json" --out "<WORKDIR>/merged.json"
```

原因：实测两者**错法不同、可以互补**。

- 语音识别错在**同音字和数字**：`康波→康姆`、`三分之二→2%`、`珍妮机→装漆机`
- 字幕 OCR 错在**形近字**：`繁荣→繁菜`、`算力→算例`

单用任何一路，错误都会进成品。交叉校验会把不一致处列出来，
让你知道**该盯哪几个位置**，而不是全篇重读。

比较完后，以 `merged.json` 作为后续分段的输入。

拿到后先看 `meta`（时长）和 `source`（文本来源），然后**问用户选模式**（若未指定）。
时长 >40 分钟时主动提一句：要不要用 `k` 模式，否则页面会非常长。

### Step 2 分段

```bash
python3 <SKILL_DIR>/scripts/segment.py "<WORKDIR>/transcript.json" --mode s
```

产出 `segments.json`，每段含 `start/end/tc/lines/score`。
`lines` 是原始分句（带秒级时间码），渲染时会逐行显示，**这是左栏"原话"的来源，不要改动**。

`score` 是信息密度的粗评，`k` 模式下可参考它挑段。

### Step 3 通读全文（关键步骤，不可省）

读完整的 `segments.json`。这一步不能靠"看前几段猜后面"，因为：

- 术语在第一次出现时最需要解释，后面出现的可以省
- 论证链条要连贯，不能每段独立发挥
- 有些内容是前后呼应的（先埋钩子后回收）

通读时记下三件事：
1. **术语清单**：哪些词外行一定不懂，各自在什么时间点第一次出现
2. **论证结构**：这条片子是"讲事实"还是"讲观点"？有没有把两者混在一起？
3. **可疑处**：数字、因果、绝对化表述（"100%""必然""一定"）

### Step 4 核查（决定这份精读是否可信）

**这一步是专业与业余的分界线。** 不做核查的"解读"，只是把原话换个说法。

必查三类：

- **数字与年份**：用 WebSearch 交叉验证。财经、历史、科学类视频尤其容易记错。
- **超出常识的论断**：搜"XX 理论 争议 / 批评"，看是否有主流反驳。
- **理论地位**：是学界共识、有争议、还是非主流？这直接决定解读语气。

> 如果视频是**观点类**（而非事实类），核查重点转向：他的论据成立吗？
> 有没有可证伪性？哪些是分析、哪些是立场？

### Step 5 撰写 contents.json

按 `templates/contents-schema.md` 的结构写。核心原则：

- `read` 段落用**大白话**，不要复述原文。用户要的是"这句话什么意思"。
- 每段配 `flags`：`ok`（可接受）/ `warn`（要打折）/ `err`（有问题）。
  **不要全给 ok**，那说明没认真读。
- `terms` 的 `at` 字段填**该词首次出现的时间码**。
- 术语表控制 8-15 个。少于 8 个说明低估了用户的无知程度；
  多于 15 个说明把普通词也当术语了（"GDP""通胀"这种要收，
  "经济""价格"这种不用收）。

### Step 6 渲染

```bash
python3 <SKILL_DIR>/scripts/render.py "<WORKDIR>/segments.json" "<WORKDIR>/contents.json" "<OUT.html>"
```

然后 `present_files` 打开。**必须打开给用户看**，不要只报路径。

### Step 7 小红书文案

在 `contents.json` 的 `xhs` 字段里。要求见 `references/xhs-copy.md`。

小红书文案的关键不是"把长文压缩"，而是**换一个叙述立场**：
长文是"我帮你读懂"，小红书是"我看完发现了什么"。

## 硬性质量要求

这些是底线，缺任何一条都应该重做：

1. **时间码必须真实**，且与原文对得上。不能凭印象标。
2. **左栏原话不得改写**。识别错误可以修正（并在页脚说明），但不能意译后当原文。
3. **每段都要有解读**，不能只填标题让用户自己看原文。
4. **必须有 flags**，且至少有一个 `warn` 或 `err`——除非内容确实是纯事实陈述。
5. **核查过的事实要能在页脚列出参考来源**。
6. **画面必须真看过**。若视频有图表、演示、代码，要抽帧确认并在解读里提到；
   若是纯口播（无画面信息），在页脚注明"全片为固定机位，无图表"——
   这也是一种有价值的判断。

## 常见坑

| 坑 | 后果 | 对策 |
|---|---|---|
| 语音识别把数字听错（"三分之二"→"2%"） | 直接歪曲原意 | 关键数字单独切片重跑验证；优先用内嵌字幕 OCR |
| 内嵌字幕 OCR 裁错区域 | 提取率 <5% | 脚本内置质量闸门；先抽一帧确认字幕位置 |
| AV1 编码视频无法解码 | 抽帧失败 | 脚本强制下 h264（`vcodec^=avc1`） |
| 视频无字幕也没代理 | 完全拿不到内容 | 先探测代理；直连不通就明确告知用户 |
| 不核查就写解读 | 把 UP 主的错误当事实传播 | Step 4 不可省 |
| 术语表过短 | 用户依然读不懂 | 按"外行视角"而非"专家视角"选词 |

## 交付物

- `<OUT.html>`：主交付物，左右对照精读页
- `<WORKDIR>/transcript.json`：统一样式的逐句文本（可复用）
- `<WORKDIR>/segments.json`：分段数据
- `<WORKDIR>/contents.json`：讲解内容（可迭代修改后重新渲染）

保留中间文件的原因：用户常会说"第三段解读不对"，改 json 重渲染比重跑全流程快得多。
