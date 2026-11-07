#!/usr/bin/env python3
"""
获取 YouTube 视频：元信息 + 音频 + 视频 + 字幕文本（三级回退）。

字幕获取优先级（这是本脚本的核心价值）：
  1) 人工字幕 / 自动字幕（yt-dlp 直取）—— 最快最准，但经常 429 或无字幕
  2) 视频内嵌硬字幕 OCR（macOS Vision）—— 中文财经类视频常见，比 ASR 准
  3) 本地语音转写（mlx-whisper）—— 兜底

用法：
  python3 fetch_video.py <url> <workdir>
输出：
  <workdir>/meta.json       视频元信息
  <workdir>/audio.m4a       音频
  <workdir>/video.mp4       视频（可选，--no-video 跳过）
  <workdir>/transcript.json 统一格式：{"source": "...", "lines": [{"start","end","text"}]}
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# ---------- 环境 ----------

# 常见本地代理端口，按顺序探测
PROXY_CANDIDATES = [7897, 7890, 1087, 1080, 6152, 8888]

# ---------- 平台策略 ----------
#
# 关键结论（实测得出，不要随意改）：
#   国内平台（bilibili / douyin / xiaohongshu）在境内网络下，走系统代理反而更快更稳，
#   强制"清空代理直连"会导致 SSL UNEXPECTED_EOF / 502。
#   海外平台（youtube / vimeo）在境内必须走代理，否则完全不通。
#
#   所以策略是：**保留系统代理，youtube 类才额外要求代理存在**。
#   不要用 clean_env() 清代理——这是之前踩过的坑。

DOMESTIC_HOSTS = (
    "bilibili.com", "b23.tv",
    "douyin.com", "iesdouyin.com",
    "xiaohongshu.com", "xhslink.com",
    "kuaishou.com", "ixigua.com",
    "weibo.com", "zhihu.com",
)

# 这些平台需要 cookies 才能拉到完整内容
COOKIE_REQUIRED = {
    "douyin.com": "抖音需要新鲜 cookies（不必登录），否则会报 'Fresh cookies are needed'",
    "xiaohongshu.com": "小红书通常需要 cookies；图文笔记没有视频格式，会报 'No video formats found'",
}


def precheck_browser_cookies(browser, target_domain):
    """
    读 cookie 之前先探一下：浏览器里到底有没有这个域名的 cookie。

    为什么需要这一步：
      yt-dlp 读不到 cookie 时会报 "Fresh cookies are needed"，
      但这个错误**掩盖了真实原因**——可能根本不是权限问题，
      而是浏览器里从来没访问过该站点，压根没有它的 cookie。

    实测踩过：Chrome 里 200 条 cookie，douyin.com 一条都没有，
    反复重试、反复授权都没用，因为数据本身不存在。

    返回 (has_cookies, total, message)
    """
    tmp = tempfile.mkdtemp(prefix="ckchk_")
    jar = os.path.join(tmp, "c.txt")
    try:
        # 用一个能触发 cookie 导出的 URL
        r = subprocess.run(
            ["yt-dlp", "--cookies-from-browser", browser, "--cookies", jar,
             "--skip-download", "--no-warnings", "https://example.com"],
            capture_output=True, text=True, timeout=120,
        )
        if not os.path.exists(jar):
            return False, 0, f"无法导出 {browser} 的 cookie（{r.stderr[-160:].strip()}）"

        domains = set()
        with open(jar, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                domains.add(line.split("\t")[0].lstrip(".").lower())

        total = len(domains)
        hit = [d for d in domains if target_domain in d]
        if hit:
            return True, total, f"{browser} 中有 {target_domain} cookie（{len(hit)} 个域名）"
        return False, total, (
            f"{browser} 里没有任何 {target_domain} 的 cookie"
            f"（共 {total} 个域名）。这不是权限问题——该浏览器从未访问过该站点。"
        )
    except subprocess.TimeoutExpired:
        return False, 0, f"导出 {browser} cookie 超时（可能卡在钥匙串授权）"
    except Exception as e:
        return False, 0, f"cookie 预检失败：{e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def log(msg):
    print(f"[fetch] {msg}", file=sys.stderr, flush=True)


def run(cmd, **kw):
    """执行命令，失败抛异常。"""
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd[:4])}...\n{r.stderr[-800:]}")
    return r


def platform_of(url):
    """识别 URL 属于哪个平台。"""
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    for h in DOMESTIC_HOSTS:
        if h in u:
            return h.split(".")[0]
    return "other"


def is_domestic(url):
    u = (url or "").lower()
    return any(h in u for h in DOMESTIC_HOSTS)


def detect_proxy():
    """探测本机可用代理端口（不含环境变量里的）。"""
    import socket
    for port in PROXY_CANDIDATES:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return None


def probe_proxy(proxy, url="https://www.google.com", timeout=6):
    """检测某个代理能否访问境外站点。返回 True/False。"""
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", str(timeout), "-x", proxy, url],
            capture_output=True, text=True,
        )
        code = (r.stdout or "").strip()
        return code not in ("", "000")
    except Exception:
        return False


def resolve_network(url):
    """
    决定这次该用什么网络配置。

    关键区分（实测得出）：
      - 境内平台（bilibili/douyin/xiaohongshu）用系统代理最快，且不能清空代理
      - 境外平台（youtube）需要**能出境的代理**，系统代理往往出不了境

    所以海外平台要主动探测：系统代理能不能访问 Google？
    不能的话，去本地常见端口找 Clash/V2Ray 之类能出境的代理。

    返回 (proxy, env, note)
    """
    env = os.environ.copy()
    env_proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or env.get("HTTP_PROXY")

    # ---------- 境内平台 ----------
    if is_domestic(url):
        if env_proxy:
            return None, env, f"domestic + system proxy ({env_proxy})"
        return None, env, "domestic + direct"

    # ---------- 境外平台 ----------
    # 1) 先看系统代理能不能出境
    if env_proxy and probe_proxy(env_proxy):
        return None, env, f"overseas + system proxy works ({env_proxy})"

    # 2) 系统代理出不了境，去找本地能出境的代理
    for port in PROXY_CANDIDATES:
        p = f"http://127.0.0.1:{port}"
        try:
            import socket
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                pass
        except OSError:
            continue
        if probe_proxy(p):
            # 清掉环境代理，避免与 --proxy 冲突
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env.pop(k, None)
            return p, env, f"overseas + switched to {p} (system proxy can't reach abroad)"

    # 3) 都不行
    note = "overseas + NO WORKING PROXY (will likely fail; start your VPN)"
    return None, env, note


def ytdlp_base(proxy, cookies_from=None, cookies_file=None):
    """构造 yt-dlp 基础命令。"""
    cmd = ["yt-dlp"]
    if proxy:
        cmd += ["--proxy", proxy]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    elif cookies_from:
        cmd += ["--cookies-from-browser", cookies_from]
    return cmd


def build_env(env):
    return env


# ---------- 元信息 ----------

def get_meta(url, proxy, env, cookies_from=None, cookies_file=None):
    cmd = ytdlp_base(proxy, cookies_from, cookies_file) + [
        "--skip-download",
        "--print", "%(id)s\t%(title)s\t%(uploader)s\t%(duration)s\t%(upload_date)s",
        "--no-warnings",
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    line = [l for l in r.stdout.strip().splitlines() if "\t" in l]
    if not line:
        raise RuntimeError(f"无法获取视频信息：{r.stderr[-500:]}")
    vid, title, uploader, dur, date = line[-1].split("\t")
    return {
        "id": vid,
        "title": title,
        "uploader": uploader,
        "duration": float(dur) if dur not in ("NA", "") else None,
        "upload_date": date,
        "url": url,
    }


# ---------- 字幕：路线 1 ----------

def try_ytdlp_subs(url, workdir, proxy, env, cookies_from=None, cookies_file=None):
    """尝试 yt-dlp 直接下载字幕。返回 lines 或 None。"""
    tmp = tempfile.mkdtemp(prefix="subs_", dir=workdir)
    try:
        cmd = ytdlp_base(proxy, cookies_from, cookies_file) + [
            "--skip-download",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", "zh-Hans,zh-CN,zh,zh-TW,en,en-US",
            "--sub-format", "vtt",
            "--no-warnings",
            "-o", os.path.join(tmp, "%(id)s.%(ext)s"),
            url,
        ]
        subprocess.run(cmd, capture_output=True, text=True, env=env)
        vtts = [f for f in os.listdir(tmp) if f.endswith(".vtt")]
        if not vtts:
            return None
        # 优先中文
        vtts.sort(key=lambda f: (0 if ".zh" in f else 1, f))
        lines = parse_vtt(os.path.join(tmp, vtts[0]))
        return lines if lines else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_vtt(path):
    """解析 VTT，去重去空，返回 [{start,end,text}]。"""
    with open(path, encoding="utf-8", errors="ignore") as f:
        raw = f.read()

    def ts_to_sec(t):
        parts = t.replace(",", ".").split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
        except ValueError:
            pass
        return 0.0

    blocks = re.split(r"\n\s*\n", raw)
    seen = []
    for b in blocks:
        lines = [l.strip() for l in b.strip().splitlines() if l.strip()]
        if not lines:
            continue
        tm = None
        text_lines = []
        for l in lines:
            m = re.match(r"([\d:.]+)\s*-->\s*([\d:.]+)", l)
            if m:
                tm = (ts_to_sec(m.group(1)), ts_to_sec(m.group(2)))
            elif l.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or re.match(r"^\d+$", l):
                continue
            else:
                text_lines.append(re.sub(r"<[^>]+>", "", l))
        if tm and text_lines:
            txt = " ".join(text_lines).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and (not seen or seen[-1]["text"] != txt):
                seen.append({"start": round(tm[0], 2), "end": round(tm[1], 2), "text": txt})
    return seen


# ---------- 下载 ----------

def download_audio(url, workdir, proxy, env, cookies_from=None, cookies_file=None):
    out = os.path.join(workdir, "audio.%(ext)s")
    cmd = ytdlp_base(proxy, cookies_from, cookies_file) + [
        "-f", "bestaudio", "-x", "--audio-format", "m4a",
        "--no-warnings", "-o", out, url,
    ]
    run(cmd, env=env)
    cand = [f for f in os.listdir(workdir) if f.startswith("audio.") and not f.endswith(".part")]
    if not cand:
        raise RuntimeError("音频下载失败")
    return os.path.join(workdir, cand[0])


def download_video(url, workdir, proxy, env, cookies_from=None, cookies_file=None):
    """下载 h264 版本（AV1 在部分机器上无法解码）。"""
    out = os.path.join(workdir, "video.%(ext)s")
    cmd = ytdlp_base(proxy, cookies_from, cookies_file) + [
        "-f", "bv*[vcodec^=avc1][height<=480]+ba/b[height<=480]/b",
        "--merge-output-format", "mp4",
        "--no-warnings", "-o", out, url,
    ]
    try:
        run(cmd, env=env)
    except RuntimeError as e:
        log(f"视频下载失败，跳过抽帧：{e}")
        return None
    for f in os.listdir(workdir):
        if f.startswith("video.") and f.endswith(".mp4"):
            return os.path.join(workdir, f)
    return None


# ---------- 字幕：路线 2（内嵌硬字幕 OCR） ----------

def try_ocr_subs(video, workdir):
    """OCR 视频底部字幕区。需要 macOS + Vision（通过 swift 编译的 ocrbin）。"""
    if not video or sys.platform != "darwin":
        return None
    ocrbin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocrbin")
    if not os.path.exists(ocrbin):
        ocrbin = build_ocr_bin()
    if not ocrbin:
        return None

    frames = os.path.join(workdir, "subframes")
    shutil.rmtree(frames, ignore_errors=True)
    os.makedirs(frames, exist_ok=True)
    # 底部 10.5%，位置 82.8% 起 —— 经验值，覆盖绝大多数中文视频字幕条
    vf = "fps=2,crop=iw:ih*0.105:0:ih*0.828,scale=iw*2.2:-1"
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", video, "-vf", vf,
         os.path.join(frames, "s_%05d.png")],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    n = len([f for f in os.listdir(frames) if f.endswith(".png")])
    if n < 20:
        return None

    raw_json = os.path.join(workdir, "subs_raw.json")
    r = subprocess.run([ocrbin, frames, raw_json, "2"], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(raw_json):
        return None

    rows = json.load(open(raw_json))
    lines = merge_ocr_rows(rows)
    # 质量闸门：非空行太少说明裁错区域了
    if len(lines) < n / 12:
        return None
    return lines


def build_ocr_bin():
    """编译 Swift OCR 工具（只需一次）。"""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr.swift")
    if not os.path.exists(src):
        return None
    out = os.path.join(os.path.dirname(src), "ocrbin")
    r = subprocess.run(["swiftc", "-O", src, "-o", out], capture_output=True, text=True)
    return out if r.returncode == 0 and os.path.exists(out) else None


def merge_ocr_rows(rows):
    """逐帧 OCR 结果 → 去重字幕行。"""
    import difflib

    def clean(s):
        s = "".join(ch for ch in s.strip() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
                    or ch in "，。、；：？！%“”‘’（）《》—…·")
        return s

    seq = [(r["t"], clean(r.get("text", ""))) for r in rows if clean(r.get("text", ""))]

    def sim(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio()

    lines, cur, cs, cl = [], None, None, None
    for t, txt in seq:
        if cur is None:
            cur, cs, cl = txt, t, t
            continue
        if sim(cur, txt) > 0.72 or txt.startswith(cur) or cur.startswith(txt):
            if len(txt) > len(cur):
                cur = txt
            cl = t
        else:
            lines.append({"start": cs, "end": cl, "text": cur})
            cur, cs, cl = txt, t, t
    if cur:
        lines.append({"start": cs, "end": cl, "text": cur})

    return [l for l in lines if sum(1 for c in l["text"] if "\u4e00" <= c <= "\u9fff") >= 2]


# ---------- 字幕：路线 3（ASR） ----------

def skill_python():
    """
    返回能 import mlx_whisper 的 python 解释器。

    查找顺序（venv 约 1.2G，放 skill 目录不合适，默认放用户缓存区）：
      1) ~/.workbuddy/cache/video-deep-reading-venv   ← 默认安装位置
      2) <SKILL_DIR>/.venv                            ← 兼容手动装在 skill 内
      3) 当前解释器
    """
    candidates = [
        os.path.expanduser("~/.workbuddy/cache/video-deep-reading-venv/bin/python"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     ".venv", "bin", "python"),
    ]
    for py in candidates:
        if os.path.exists(py):
            r = subprocess.run([py, "-c", "import mlx_whisper"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return py
    r = subprocess.run([sys.executable, "-c", "import mlx_whisper"],
                       capture_output=True, text=True)
    return sys.executable if r.returncode == 0 else None


ASR_RUNNER = r"""
import json, sys, os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
import mlx_whisper
wav, out, lang = sys.argv[1], sys.argv[2], sys.argv[3]
kw = {"path_or_hf_repo": "mlx-community/whisper-large-v3-turbo", "verbose": False}
if lang and lang != "-":
    kw["language"] = lang
res = mlx_whisper.transcribe(wav, **kw)
rows = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
        for s in res["segments"] if s["text"].strip()]
json.dump(rows, open(out, "w"), ensure_ascii=False)
"""


def try_asr(audio, workdir, lang=None):
    """mlx-whisper 转写。用子进程跑，这样能指定 skill 自带的 venv。"""
    py = skill_python()
    if not py:
        log("未找到可用的 mlx-whisper（skill .venv 或当前环境均无），跳过 ASR")
        log("  修复：cd <SKILL_DIR> && python3 -m venv .venv && "
            "./.venv/bin/pip install mlx-whisper")
        return None

    wav = os.path.join(workdir, "asr.wav")
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", audio, "-ar", "16000", "-ac", "1", wav],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log(f"ffmpeg 转 wav 失败：{r.stderr[-300:]}")
        return None

    out = os.path.join(workdir, "asr_raw.json")
    r = subprocess.run([py, "-c", ASR_RUNNER, wav, out, lang or "-"],
                       capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not os.path.exists(out):
        log(f"ASR 失败：{r.stderr[-400:]}")
        return None
    return json.load(open(out))



# ---------- 内容体检 ----------

def _probe_duration(path):
    """用 ffprobe 读时长。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(r.stdout.strip()), 1)
    except Exception:
        return None


def _extract_uploader(desc):
    """从抖音 meta description 里抠作者名。格式：…于20261031发布在抖音，已经收获了…"""
    m = re.search(r"于\d{8}发布在抖音", desc or "")
    if not m:
        return ""
    head = desc[:m.start()]
    # 作者名通常在最后一段（前面是正文）
    for sep in (" - ", "。"):
        if sep in head:
            part = head.rsplit(sep, 1)[-1].strip()
            if 0 < len(part) <= 20:
                return part
    return head[-20:].strip()


def check_speech_density(lines, duration):
    """
    检测"这条视频到底有没有讲解内容"。

    背景：whisper 在**没有语音**的音频上会产生幻觉，输出重复的固定短语
    （实测见过：'字幕志愿者 李宗盛' 重复多次、'静静 木子咪'、'お詰み'）。

    如果不检测，这些幻觉会被当成讲解内容，后面全部解读都是建立在空气上。

    判据（命中任一即警告）：
      1. 字符密度过低：中文字数 / 时长 < 1.2 字/秒（正常讲解约 3-5 字/秒）
      2. 单句高度重复：同一句出现 ≥3 次且占全部内容 30% 以上
    """
    if not lines:
        return "没有识别到任何文本"

    han = "".join(c for c in "".join(l["text"] for l in lines)
                  if "\u4e00" <= c <= "\u9fff")
    n = len(han)

    if duration and duration > 30:
        rate = n / duration
        if rate < 1.2:
            return (f"疑似无讲解内容：{int(duration)} 秒视频只识别到 {n} 个中文字"
                    f"（{rate:.2f} 字/秒，正常讲解约 3-5 字/秒）。"
                    f"可能是纯音乐/纯画面，或语音识别产生幻觉。请先确认这条视频是否有口播。")

    from collections import Counter
    c = Counter(l["text"].strip() for l in lines)
    top_text, top_n = c.most_common(1)[0]
    if top_n >= 3 and n and (len(top_text) * top_n / max(n, 1)) > 0.3:
        return (f"疑似语音识别幻觉：同一句「{top_text}」重复 {top_n} 次。"
                f"这类视频通常没有实质口播，不建议做精读。")

    return None


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("workdir")
    ap.add_argument("--no-video", action="store_true", help="跳过视频下载（更快，但无法 OCR/抽帧）")
    ap.add_argument("--lang", default=None, help="ASR 语言，如 zh / en；留空自动检测")
    ap.add_argument("--force", default=None, choices=["subs", "ocr", "asr"], help="强制指定字幕来源")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="从浏览器读 cookie，如 chrome / safari / edge。抖音必需")
    ap.add_argument("--cookies", default=None, help="cookies.txt 文件路径，替代 --cookies-from-browser")
    ap.add_argument("--dy-timeout", type=int, default=90, help="抖音浏览器等待秒数")
    ap.add_argument("--dy-headed", action="store_true",
                    help="抖音通道显示浏览器窗口（需手动点播放时用）")
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)

    # 本地文件：直接走 ASR，绕开所有平台限制（最终兜底）
    local = args.url if os.path.isfile(args.url) else None
    if local:
        log(f"本地文件模式：{os.path.basename(local)}")
        lines = try_asr(local, args.workdir, args.lang)
        if not lines:
            log("❌ 本地文件转写失败")
            sys.exit(1)
        meta = {
            "id": os.path.splitext(os.path.basename(local))[0],
            "title": os.path.basename(local),
            "uploader": "local",
            "duration": None,
            "upload_date": "",
            "url": "",
            "platform": "local",
        }
        out = {"source": "whisper ASR (local file)", "meta": meta,
               "video": local, "lines": lines}
        p = os.path.join(args.workdir, "transcript.json")
        json.dump(out, open(p, "w"), ensure_ascii=False, indent=1)
        log(f"✓ {len(lines)} 行 -> {p}")
        print(f"SOURCE=asr-local LINES={len(lines)}")
        return

    plat = platform_of(args.url)
    proxy, env, note = resolve_network(args.url)
    log(f"platform={plat} network={note}")

    # ---------- 抖音专用通道 ----------
    # yt-dlp 的 Douyin 提取器过不了 ArgusSecurityPlugin 签名，必然 403。
    # 走 Playwright 真浏览器拦截媒体流，拿到 mp4 后转本地处理。
    if plat == "douyin":
        log("抖音走浏览器通道（yt-dlp 无法通过签名校验）")
        dy_out = os.path.join(args.workdir, "douyin.mp4")
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "douyin_fetch.py")
        dy_cmd = [sys.executable, script, args.url, dy_out,
                  "--timeout", str(args.dy_timeout)]
        if args.dy_headed:
            dy_cmd.append("--headed")
        r = subprocess.run(dy_cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(dy_out):
            log("❌ 抖音视频获取失败")
            tail = (r.stderr or r.stdout or "")[-600:]
            log(tail)
            log("提示：加 --dy-headed 打开浏览器窗口，手动点一下播放再重试")
            sys.exit(1)

        # 从 META 行取标题等信息
        meta = {"id": "", "title": "", "uploader": "", "duration": None,
                "upload_date": "", "url": args.url, "platform": "douyin"}
        for line in r.stdout.splitlines():
            if line.startswith("META="):
                try:
                    m = json.loads(line[5:])
                    meta.update({
                        "id": m.get("video_id", ""),
                        "title": m.get("title", ""),
                        "desc": m.get("desc", ""),
                        "uploader": _extract_uploader(m.get("desc", "")),
                    })
                except Exception:
                    pass
        meta["duration"] = _probe_duration(dy_out)
        json.dump(meta, open(os.path.join(args.workdir, "meta.json"), "w"),
                  ensure_ascii=False, indent=1)
        log(f"title={meta['title'][:50]} dur={meta['duration']}s")

        lines = try_asr(dy_out, args.workdir, args.lang)
        if not lines:
            log("❌ 抖音视频转写失败")
            sys.exit(1)
        warn = check_speech_density(lines, meta.get("duration") or 0)
        out = {"source": "whisper ASR (douyin via browser)",
               "meta": meta, "video": dy_out, "lines": lines,
               "warnings": [warn] if warn else []}
        p = os.path.join(args.workdir, "transcript.json")
        json.dump(out, open(p, "w"), ensure_ascii=False, indent=1)
        log(f"✓ {len(lines)} 行 -> {p}")
        if warn:
            log(f"⚠ {warn}")
        print(f"SOURCE=asr-douyin LINES={len(lines)}")
        return

    if plat == "xiaohongshu" and not (args.cookies_from_browser or args.cookies):
        for host, tip in COOKIE_REQUIRED.items():
            if host in args.url:
                log(f"⚠ {tip}")
                log(f"  建议加：--cookies-from-browser chrome")

    ck = dict(cookies_from=args.cookies_from_browser, cookies_file=args.cookies)

    meta = get_meta(args.url, proxy, env, **ck)
    meta["platform"] = plat
    json.dump(meta, open(os.path.join(args.workdir, "meta.json"), "w"),
              ensure_ascii=False, indent=1)
    log(f"title={meta['title']} dur={meta['duration']}s")

    lines, source = None, None

    # 路线 1：现成字幕
    if args.force in (None, "subs"):
        log("尝试 yt-dlp 字幕…")
        lines = try_ytdlp_subs(args.url, args.workdir, proxy, env, **ck)
        if lines:
            source = "yt-dlp subtitles"
            log(f"✓ 取到现成字幕 {len(lines)} 行")

    video = None
    # 只有需要 OCR 且用户未禁用视频时才下载视频
    need_video = (not args.no_video) and (args.force in (None, "ocr"))

    # 下载媒体
    audio = download_audio(args.url, args.workdir, proxy, env, **ck)
    log(f"音频 ok {os.path.getsize(audio)//1024}KB")
    if need_video:
        video = download_video(args.url, args.workdir, proxy, env, **ck)
        log(f"视频 {'ok' if video else 'skip'}")

    # 路线 2：内嵌字幕 OCR
    if not lines and args.force in (None, "ocr"):
        log("尝试内嵌硬字幕 OCR…")
        lines = try_ocr_subs(video, args.workdir)
        if lines:
            source = "embedded subtitle OCR"
            log(f"✓ OCR 字幕 {len(lines)} 行")

    # 路线 3：ASR
    if not lines and args.force in (None, "asr"):
        log("回退到本地语音转写…")
        lines = try_asr(audio, args.workdir, args.lang)
        if lines:
            source = "whisper ASR"
            log(f"✓ ASR {len(lines)} 行")

    if not lines:
        log("❌ 三种方式均失败")
        sys.exit(1)

    # 语音内容体检：识别"没有讲解内容"的视频（纯音乐、纯画面、只有字幕条）
    dur = meta.get("duration") or 0
    warn = check_speech_density(lines, dur)

    out = {
        "source": source,
        "meta": meta,
        "video": video,
        "lines": lines,
        "warnings": [warn] if warn else [],
    }
    path = os.path.join(args.workdir, "transcript.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    log(f"✓ 写入 {path}")
    if warn:
        log(f"⚠ {warn}")
    print(f"SOURCE={source} LINES={len(lines)}")


if __name__ == "__main__":
    main()
