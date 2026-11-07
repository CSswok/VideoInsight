# VideoInsight

把一条你完全看不懂的视频，变成一份逐句对照的精读页。

不是"AI 帮你总结三句话"。是**左边原话带秒级时间码、右边大白话逐段解读**，配术语表，并且**明确指出哪里讲得站不住**。

支持 **YouTube / Bilibili / 抖音**，无字幕也能处理。

---

## 它解决什么问题

刷到一条"看起来很厉害但一句没听懂"的视频，收藏了，然后永远没打开过。

不是不想看，是术语密度太高——**一个词没懂，后面全崩**。视频是线性的，它不会等你。

这个工具把过程反过来：先把整条视频拆成一句一句，再逐句解释。哪里不懂就停在哪一句。

## 输出长什么样

一份 HTML 页面，四块内容：

| 区块 | 内容 |
|---|---|
| **术语表** | 8-15 个外行必不懂的词，每个标首次出现的时间码 |
| **逐段对照** | 左栏原话（逐句带时间码，可点击跳转），右栏大白话解读 |
| **判断标注** | 每段标 `可接受` / `要打折` / `有问题`，共三档 |
| **小红书文案** | 附带一份可直接发的文案，可一键复制 |

外加：硬数字卡片、批判性说明、来源限制声明。

## 三种粒度

| 模式 | 分段 | 适用 |
|---|---|---|
| `s` 时间表对照（默认） | 60-120 秒/段 | 一般场景，信息不丢 |
| `k` 关键节点 | 100-200 秒/段，只挑高密度段 | 视频 >40 分钟 |
| `f` 逐片讲解 | 40-90 秒/段，不跳过任何内容 | 要求"精细""不漏" |

## 安装

```bash
git clone https://github.com/CSswok/VideoInsight.git
cd VideoInsight
bash scripts/setup.sh
```

`setup.sh` 会自动检查依赖、编译 OCR 工具、创建转写环境（约 1.2G，装在 `~/.workbuddy/cache/`，不污染仓库）。

### 依赖

| 依赖 | 用途 | 必需性 |
|---|---|---|
| `ffmpeg` / `ffprobe` | 音视频处理 | 必需 |
| `yt-dlp` | YouTube / Bilibili 下载 | 必需 |
| `mlx-whisper` | 本地语音转写 | 必需（macOS Apple Silicon） |
| Playwright + Chrome | 抖音通道 | 仅抖音需要 |
| Swift（macOS） | 编译内嵌字幕 OCR | 可选 |

```bash
# 一次性装齐
brew install ffmpeg yt-dlp
pip install playwright mlx-whisper
python3 -m playwright install chromium
```

## 用法

```bash
# YouTube
python3 scripts/fetch_video.py "https://www.youtube.com/watch?v=xxx" ./work

# Bilibili（含 b23.tv 短链）
python3 scripts/fetch_video.py "https://www.bilibili.com/video/BV1xx" ./work

# 抖音（自动走浏览器通道，无需 cookie）
python3 scripts/fetch_video.py "https://www.douyin.com/video/xxx" ./work

# 本地文件（最终兜底，永远可用）
python3 scripts/fetch_video.py "/path/video.mp4" ./work --lang zh
```

拿到文字稿后：

```bash
# 分段
python3 scripts/segment.py ./work/transcript.json --mode s

# 填写解读内容（格式见 templates/contents-schema.md），然后渲染
python3 scripts/render.py ./work/segments.json ./work/contents.json ./out.html
```

## 三个平台都是实测过的

| 平台 | 实测结果 | 耗时 |
|---|---|---|
| YouTube | 1120 秒视频 → 225 行 | 4-7 分钟 |
| Bilibili | 252 秒视频 → 146 行 | **26 秒** |
| 抖音 | 840 秒视频 → 431 行 | **63 秒** |

## 一些踩过的坑（写在这里省你时间）

### 抖音：yt-dlp 拿不到，必须走浏览器

所有常规路径都会失败：

| 方式 | 结果 |
|---|---|
| 无 cookie | `Fresh cookies are needed` |
| 64 条完整 cookie | `web detail JSON: 403` |
| 直接请求 API | `Blocked by ArgusSecurityPlugin: Uifid Not Found` |
| 补上 uifid 头 | → `Signature Not Found` |

根因是 **a_bogus 签名**，由页面 JS 现场计算，无法离线复现。

解法：`scripts/douyin_fetch.py` 用 Playwright 启动真实 Chrome，让页面自己算签名发请求，我们拦截媒体流。**这不是绕过风控，等同于用户手动打开页面。**

### 抖音用 DASH 分离流

视频流和音频流是两个独立文件：

- 55MB → 纯视频，**无音轨**
- 20MB → 纯音频，无画面

如果按体积挑最大的，会拿到无声视频，转写时报 `Output file #0 does not contain any stream`。

脚本现在会探测 `codec_type`，分离后 `ffmpeg -c copy -map` 合并。

### 代理要按平台分流

境内平台（B站/抖音）**不能**清空代理直连，也不能统一走境外代理：

| 方式 | 访问 B站 |
|---|---|
| 系统代理 | 200，0.16s |
| 清空代理直连 | **502 / SSL 报错** |
| 走境外代理 | 200，1.7s（慢 10 倍） |

海外平台（YouTube）反过来，必须走能出境的代理。

脚本的 `resolve_network()` 会探测系统代理能否访问境外，不能就自动切换到本地能出境的端口。

### 语音识别会产生幻觉

无口播的视频（纯音乐/纯画面）会让 whisper 输出重复的固定短语。实测见过：

```
字幕志愿者 李宗盛
字幕志愿者 李宗盛   ← 重复 4 次
静静 木子咪
お詰み
```

99 秒视频只识别出 40 个字。如果不检测，后面整篇解读都是空中楼阁。

脚本用字/秒密度检测（正常讲解 3-5 字/秒），低于 1.2 就警告。

### 内嵌字幕比语音识别准

很多视频画面底部有硬字幕。用 macOS Vision 逐帧 OCR 提取，比听写准确得多。

实测一条视频：OCR 得到 402 行，语音识别 301 行，而且**两者错法不同**：

- 语音识别错在**同音字**：`康波→康姆`、`珍妮机→装漆机`
- 字幕 OCR 错在**形近字**：`繁荣→繁菜`、`算力→算例`

`scripts/crosscheck.py` 会把两路不一致处列出来，让你知道该盯哪几个位置。

## 设计原则

**不做"总结"，做"解读"。**

如果一句解读把原文的术语换掉之后信息量没有增加，那就是废话。

**必须指出问题。**

每段至少一个判断标注，且不会全是"可接受"——那说明没认真读。内容如果是纯事实陈述可以例外。

**时间码必须真实。**

不能凭印象标。左栏原话不得改写。

**画面必须真看过。**

有图表、演示、代码就抽帧确认；纯口播也要在页脚注明"全片为固定机位，无图表"——这也是一种有价值的判断。

**不替用户做决定。**

内容和事实归用户，画法和手感归工具。涉及立场的内容标注出来，但不删改——删了就失真。

## 目录结构

```
SKILL.md                    主流程与质量红线
scripts/
  fetch_video.py            统一入口：三级回退取文本
  douyin_fetch.py           抖音专用：浏览器拦截
  crosscheck.py             字幕 vs 语音 交叉校验
  segment.py                三种粒度分段
  render.py                 排版渲染
  ocr.swift                 内嵌字幕 OCR（Swift 编译）
  setup.sh                  一键环境自检
references/
  platforms.md              各平台实测与踩坑记录
  fact-check.md             核查方法论 + 8 种逻辑谬误
  modes.md                  三种模式差异
  xhs-copy.md               小红书文案规范
templates/
  contents-schema.md        讲解内容 JSON 规范
```

## 作为 Claude Skill 使用

这个仓库本身就是一个 Skill 包。把目录放到 skills 路径下即可：

```bash
cp -R VideoInsight ~/.workbuddy/skills/video-deep-reading
```

然后直接说"用视频深度精读拆这个链接"。

## License

MIT
