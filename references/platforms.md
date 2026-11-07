# 多平台支持

## 支持矩阵（实测结论，不是推测）

| 平台 | 链接形式 | 现成字幕 | 需要 Cookie | 实测状态 |
|---|---|---|---|---|
| **YouTube** | `watch?v=` / `youtu.be/` / `shorts/` | 常有（人工+自动） | 否（但字幕接口常 429） | ✅ 稳定 |
| **Bilibili** | `BV号` / `b23.tv` 短链 / `av号` | 多数有 CC，部分只有弹幕 | 高码率需，普通画质不需要 | ✅ 稳定，**最快** |
| **抖音** | `v.douyin.com/` 短链 / `douyin.com/video/` | 无 | **必须**（不必登录） | ⚠️ 需 cookie |
| **小红书** | `xiaohongshu.com/explore/` / `xhslink.com` | 无 | 通常需要 | ⚠️ 见下方时效性说明 |
| 快手 | `kuaishou.com` | 无 | 视情况 | 未充分测试 |
| 西瓜 | `ixigua.com` | 部分 | 否 | 未充分测试 |
| **本地文件** | 任意路径 | — | 否 | ✅ 永远可用 |

### 实测耗时记录（最小测试）

| 视频 | 时长 | 处理结果 | 耗时 |
|---|---|---|---|
| Bilibili 短视频 | 99s | 无口播，被内容检测拦下 | 16s |
| Bilibili 短视频 | 252s | 146 行，正常 | **26s** |
| Bilibili 中视频 | 602s | 300 行，正常 | 35s |
| YouTube | 1120s | 225 行（走代理） | 4-7min |

**B 站比 YouTube 快一个数量级**，主要差在代理和下载速度。

### 实测中必须知道的三个坑

#### 1. 小红书链接有很强时效性

带 `xsec_token` 的分享链接会过期。过期后报：

```
ERROR: [XiaoHongShu] xxx: No video formats found!
```

**这个报错有歧义**——它既可能是"图文笔记没有视频"，也可能是"token 过期"。

处理方式：
1. 让用户**重新从 App 复制一次分享链接**（拿新鲜 token）
2. 判断是 `type=video` 还是 `type=normal`（链接里就有这个参数）
3. 两者都不行 → 退到本地文件方案

#### 2. 无口播视频会产生识别幻觉

实测一条 99 秒纯画面视频，whisper 输出：

```
字幕志愿者 李宗盛
字幕志愿者 李宗盛   ← 重复 4 次
静静 木子咪
お詰み
```

这是 whisper 在**静音段**的典型幻觉。

脚本现在有 `check_speech_density()` 自动检测：
- 中文字数 / 时长 < 1.2 字/秒 → 警告（正常讲解 3-5 字/秒）
- 同一句重复 ≥3 次且占比 >30% → 警告

**命中警告时必须先问用户"这条视频有口播吗"**，不要硬着头皮写解读。

#### 3. 抖音必须走浏览器通道（已验证可行）

**结论：yt-dlp 拿不到抖音，但 Playwright 可以。**

实测 yt-dlp 的所有路径都失败：

| 方式 | 结果 |
|---|---|
| 无 cookie | `Fresh cookies are needed` |
| `--cookies-from-browser chrome` | `Failed to download web detail JSON: 403` |
| 导出的 cookies.txt（64 条，含 sessionid/UIFID/ttwid） | 同样 403 |
| 直接请求 web detail API | `Blocked by ArgusSecurityPlugin Uifid Not Found` |
| 补上 uifid 请求头 | → `Signature Not Found` |

原因：抖音的 web detail 接口要 **a_bogus 签名**，签名由页面 JS 现场算，
无法离线复现。cookie 再完整也没用。

**可行方案：`scripts/douyin_fetch.py`（Playwright 真浏览器）**

```
真实 Chrome 打开页面 → 页面 JS 自己算签名发请求 → 我们拦截媒体流 → 下载 mp4
```

这不是绕过风控，等同于用户手动打开页面。实测 14 分钟视频 12 秒拿到直链。

**两个必须知道的坑（都踩过）：**

1. **抖音用 DASH 分离流**：视频流和音频流是两个独立文件
   - 55MB 纯视频（无音轨）
   - 20MB 纯音频（无视频）
   
   如果按体积挑最大的，会拿到**无声视频**，后面 ASR 直接报
   `Output file #0 does not contain any stream`。
   
   脚本现在会探测每个候选的 `codec_type`，分出 video/audio 后用
   `ffmpeg -c copy -map` 合并。合并后 99MB，音视频齐全。

2. **静态资源干扰**：`douyinstatic.com` 下的 `uuu_265.mp4` 是页面宣传片，
   不是目标视频。必须按域名白名单过滤（只认 `douyinvod.com` / `video/tos`）。

**用法**：

```bash
# 脚本已自动接进主流程，直接传抖音链接即可
fetch_video.py "https://www.douyin.com/jingxuan?modal_id=xxx" ./work

# 如果页面没自动播放，用 headed 模式手动点一下
fetch_video.py "https://www.douyin.com/video/xxx" ./work --dy-headed
```

实测耗时：14 分钟视频 → 下载 12s + 转写 50s = **63 秒完成**。

## 代理策略：国内平台不能强制走境外代理

这是最容易踩的坑。**不能用一套网络配置打所有平台。**

实测数据（同一台机器，访问 bilibili 首页）：

| 方式 | 耗时 |
|---|---|
| 走系统代理 | 0.16s |
| 清空代理直连 | **SSL UNEXPECTED_EOF / 502 失败** |

原因：机器上的 `HTTP_PROXY` 环境变量指向本地一个转发代理（不是 Clash），
它同时能访问境内境外。**清掉它之后，直连反而被 TLS 层干扰。**

而 YouTube 恰恰相反：必须走代理，否则完全不通。

### 所以脚本里的策略是

```
国内平台（bilibili/douyin/xiaohongshu/kuaishou/ixigua/weibo/zhihu）
    → 保留系统代理，不额外指定 --proxy
海外平台（youtube/vimeo/...）
    → 先探测系统代理能否访问境外
       能 → 直接用
       不能 → 自动切到本地能出境的代理（7897/7890/1080...）
```

代码位置：`fetch_video.py` 的 `resolve_network()` 和 `probe_proxy()`。

**不要**改成"统一清空代理直连"——那会让所有国内平台瘫掉。
**不要**改成"统一强制走某个代理端口"——那会让国内平台变慢 10 倍以上。

### 为什么海外平台要多一步探测

很多开发环境会注入一个**只对境内可用**的 `HTTP_PROXY`（比如沙箱、企业网关）。
这种代理访问 bilibili 是 200，访问 google 是 000。

如果直接拿它去访问 YouTube，就会报：

```
SSL: UNEXPECTED_EOF_WHILE_READING
```

**这个报错看着像网络问题，实际上是"代理选错了"。** 脚本现在会先用
`curl -x <proxy> https://www.google.com` 探一下，通了才用，
不通就自动去本地端口找 Clash/V2Ray。

实测记录：

| 代理 | 访问 bilibili | 访问 google |
|---|---|---|
| 系统注入的 61876 | 200 ✅ | 000 ❌ |
| Clash 的 7897 | 200 ✅ | 302 ✅ |

所以脚本对 YouTube 会自动切到 7897，对 bilibili 会保留 61876。

## 抖音：必须给 Cookie

抖音的 extractor 会直接报：

```
ERROR: [Douyin] xxx: Fresh cookies (not necessarily logged in) are needed
```

**"not necessarily logged in" 很关键**——不需要登录账号，只需要"新鲜的" cookie。

### 方案 A：从浏览器读（推荐）

```bash
--cookies-from-browser chrome
```

第一次执行时 macOS 会弹出**钥匙串授权**，需要用户点"允许"。
如果卡住不动，就是这个弹窗在等确认。

支持的浏览器值：`chrome` / `safari` / `edge` / `firefox` / `brave` / `chromium`

### 方案 B：手动导出 cookies.txt

用浏览器插件（如 Get cookies.txt LOCALLY）导出抖音的 cookies，
然后：

```bash
--cookies /path/to/douyin_cookies.txt
```

适合：不想授权钥匙串、或用的是不支持的浏览器。

### 如果两种都拿不到

退回**用户手动下载**：

1. 让用户把视频文件放到工作目录
2. 直接对本地文件做 ASR 和分段：

```bash
ffmpeg -i "<本地视频>" -ar 16000 -ac 1 audio.wav
# 然后手动构造 transcript.json，或直接跑 ASR
```

这条路径永远可用，是最终兜底。

## 小红书：先确认是不是视频笔记

小红书大量笔记是**图文**，没有视频。yt-dlp 会报：

```
ERROR: [XiaoHongShu] xxx: No video formats found!
```

**这不一定是失败**——可能它本来就是图文。

处理方式：

1. 先问用户：这条是视频笔记还是图文？
2. 如果是图文：告知本 skill 只处理音视频，图文建议直接截图+OCR
3. 如果是视频但仍报错：加 cookie 重试

```bash
--cookies-from-browser chrome
```

## Bilibili：优先用 CC 字幕

b 站视频常有 UP 主上传的 CC 字幕，质量远高于语音识别。

脚本的路线 1（yt-dlp 字幕）会自动尝试。但要注意：

- 字幕语言代码是 `zh-Hans` / `zh-CN`，脚本已包含
- 只有 `danmaku`（弹幕 XML）时**不要用**——弹幕是观众评论，不是台词
- 高码率格式需要登录 cookie，但**基础画质不需要**，做 ASR 足够了

短链 `b23.tv` 会自动重定向，脚本能处理。

## 各平台的额外注意

### YouTube
- 自动字幕经常 429（限流），所以三级回退很必要
- 部分视频只有自动字幕，准确率低于人工字幕
- 视频常是 AV1 编码，抽帧会失败；脚本强制下 h264

### Bilibili
- 一个 BV 号可能是多 P，默认取第一 P；需要指定时用 `?p=2`
- 弹幕（danmaku）不是字幕，别混用

### 抖音
- 短链 `v.douyin.com/xxx/` 需要跟随重定向
- 视频通常很短（15s-3min），ASR 成本极低
- 画面上常有大量字幕贴纸，OCR 会干扰，**建议用 `--force asr`**

### 小红书
- 视频笔记比例低，先确认类型
- 竖屏 9:16，字幕位置和横屏不同，OCR 裁剪参数需调整

## 新增平台怎么加

1. 在 `fetch_video.py` 的 `DOMESTIC_HOSTS` 加域名（境外的不要加）
2. 在 `COOKIE_REQUIRED` 加提示（如果该平台需要 cookie）
3. 实测一次 `yt-dlp --list-subs` 和下载，确认可行
4. 在本文件的支持矩阵里补一行**实测结果**

**不要只是相信 yt-dlp 的 `--list-extractors` 里有名字就写"支持"**——
名字存在和真能拿到内容，是两件不同的事。抖音就是典型例子。
