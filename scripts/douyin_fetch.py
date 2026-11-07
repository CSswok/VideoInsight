#!/usr/bin/env python3
"""
用 Playwright 真实浏览器加载抖音页面，拿到视频直链。

核心难点（实测）：
  抖音 web detail 接口有 ArgusSecurityPlugin 签名（a_bogus），
  签名由页面内 JS 现场计算，无法离线复现。
  纯 HTTP（curl/yt-dlp）一律 403，cookie 再完整也没用。

  唯一稳定办法：真浏览器打开页面，让 JS 自己算签名并发出请求，
  我们只拦截它发出的媒体请求。这不是绕过风控，等同于用户手动打开。

踩过的坑：
  - douyinstatic.com 下的 uuu_265.mp4 是页面宣传片，不是目标视频
  - 视频可能延迟加载，要等 video 元素真正有 src
  - CDN 要求 Referer，直接 curl 会被拒

用法：
  python3 douyin_fetch.py <url_or_id> <out.mp4> [--headed] [--timeout 90]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

# 静态资源站，一律排除（宣传片、播放器皮肤都在这）
STATIC_HOSTS = ("douyinstatic.com", "douyinpic.com", "byteimg.com")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def log(m):
    print(f"[douyin] {m}", file=sys.stderr, flush=True)


def extract_id(u):
    for pat in (r"modal_id=(\d+)", r"/video/(\d+)", r"/share/video/(\d+)", r"(\d{15,})"):
        m = re.search(pat, u)
        if m:
            return m.group(1)
    return None


def is_real_video(url):
    """判断是否为目标视频流，排除静态资源。"""
    low = url.lower()
    if any(h in low for h in STATIC_HOSTS):
        return False
    return (".mp4" in low or "video/tos" in low
            or "aweme/v1/play" in low or "douyinvod.com" in low)


def fetch_urls(target, timeout=90, headless=True, profile=None):
    from playwright.sync_api import sync_playwright

    profile = profile or os.path.expanduser("~/.workbuddy/cache/dy-profile")
    os.makedirs(profile, exist_ok=True)

    media, page_title, page_desc = [], "", ""

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=headless,
            channel="chrome" if not headless else None,
            args=["--disable-blink-features=AutomationControlled",
                  "--autoplay-policy=no-user-gesture-required"],
            viewport={"width": 1280, "height": 900},
            user_agent=UA,
        )
        page = ctx.new_page()

        def on_resp(r):
            u = r.url
            if r.status in (200, 206) and is_real_video(u):
                media.append(u)

        page.on("response", on_resp)

        log(f"打开 {target}")
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=timeout * 1000)
        except Exception as e:
            log(f"goto 超时/异常，继续等待: {type(e).__name__}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                srcs = page.eval_on_selector_all(
                    "video", "els => els.map(e => e.src || e.currentSrc).filter(Boolean)")
                for s in srcs:
                    if is_real_video(s):
                        media.append(s)
                page.evaluate("""() => {
                    document.querySelectorAll('video').forEach(v => {
                        v.muted = true;
                        if (v.paused) v.play().catch(()=>{});
                    });
                }""")
            except Exception:
                pass

            if media:
                break
            time.sleep(2)

        try:
            page_title = page.title()
            d = page.query_selector("meta[name='description']")
            page_desc = d.get_attribute("content") if d else ""
        except Exception:
            pass

        ctx.close()

    uniq = list(dict.fromkeys(media))
    return uniq, page_title, page_desc


def probe_streams(path):
    """
    返回 (has_video, has_audio)。

    抖音用 DASH 分离流：视频流和音频流是两个独立文件。
    实测同一个视频会得到：
      - 55MB 的纯视频（无音轨）
      - 20MB 的纯音频（无视频）
    如果只按体积挑最大的，会拿到无声视频，后面 ASR 直接失败。
    """
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        types = r.stdout.split()
        return ("video" in types), ("audio" in types)
    except Exception:
        return False, False


def pick_best(candidates):
    """
    从候选文件里挑出可用组合，返回 (video_path, audio_path)。

    优先要"既有视频又有音频"的单文件；
    否则分别取视频流和音频流，后续由调用方合并。
    """
    both = [(p, s) for p, s, hv, ha in candidates if hv and ha]
    if both:
        best = max(both, key=lambda x: x[1])
        return best[0], None

    vids = [(p, s) for p, s, hv, ha in candidates if hv]
    auds = [(p, s) for p, s, hv, ha in candidates if ha]
    v = max(vids, key=lambda x: x[1])[0] if vids else None
    a = max(auds, key=lambda x: x[1])[0] if auds else None
    return v, a


def download(url, out_path):
    subprocess.run([
        "curl", "-L", "--max-time", "300", "-H", f"User-Agent: {UA}",
        "-H", "Referer: https://www.douyin.com/", "-o", out_path, url,
    ], capture_output=True, text=True)
    if not os.path.exists(out_path):
        return False, 0
    size = os.path.getsize(out_path)
    # 小于 500KB 的大概率是宣传片或错误页
    return size > 500_000, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--headed", dest="headless", action="store_false", default=True)
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    vid = extract_id(args.url)
    if not vid:
        log("无法提取视频 ID")
        sys.exit(2)

    target = f"https://www.douyin.com/video/{vid}"
    log(f"video_id={vid}")

    urls, title, desc = fetch_urls(target, args.timeout, args.headless)

    if title:
        log(f"标题: {title[:80]}")
    if not urls:
        log("未拿到视频直链。可试 --headed 手动点一下播放后重试。")
        print(json.dumps({"ok": False, "video_id": vid, "title": title}, ensure_ascii=False))
        sys.exit(1)

    log(f"候选 {len(urls)} 个")

    # 全部下载下来再做流探测（抖音 DASH 分离，不能只看体积）
    cands = []
    for i, u in enumerate(urls[:6]):
        tmp = args.out + f".part{i}"
        ok, size = download(u, tmp)
        if not ok:
            log(f"  [{i}] X {size//1024}KB 太小或失败")
            try:
                os.remove(tmp)
            except OSError:
                pass
            continue
        hv, ha = probe_streams(tmp)
        log(f"  [{i}] {size//1024}KB video={hv} audio={ha}")
        cands.append((tmp, size, hv, ha))

    if not cands:
        log("所有候选都下载失败")
        print(json.dumps({"ok": False, "video_id": vid, "title": title}, ensure_ascii=False))
        sys.exit(1)

    vpath, apath = pick_best(cands)
    if not vpath and not apath:
        log("候选里既没有视频也没有音频")
        print(json.dumps({"ok": False, "video_id": vid, "title": title}, ensure_ascii=False))
        sys.exit(1)

    if vpath and apath:
        # 分离流：ffmpeg 合并
        log("检测到 DASH 分离流，合并音视频…")
        merged = args.out + ".merged.mp4"
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", vpath, "-i", apath,
             "-c", "copy", "-map", "0:v:0", "-map", "1:a:0", merged],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and os.path.exists(merged):
            os.replace(merged, args.out)
            log("✓ 合并完成")
        else:
            # 合并失败时退而求其次：用视频流（至少能抽帧），并提示
            log(f"合并失败，使用视频流：{r.stderr[-200:]}")
            os.replace(vpath, args.out)
    elif vpath:
        os.replace(vpath, args.out)
    else:
        # 只有音频，也能做转写
        log("只有音频流，仅做转写")
        os.replace(apath, args.out)

    # 清理临时文件
    for p, *_ in cands:
        if os.path.exists(p) and os.path.abspath(p) != os.path.abspath(args.out):
            try:
                os.remove(p)
            except OSError:
                pass

    hv, ha = probe_streams(args.out)
    log(f"最终文件 video={hv} audio={ha} {os.path.getsize(args.out)//1024}KB")
    if not ha:
        log("⚠ 最终文件没有音轨，语音转写会失败")

    duration = None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", args.out],
                           capture_output=True, text=True, timeout=30)
        duration = round(float(r.stdout.strip()), 1)
    except Exception:
        pass

    meta = {"ok": True, "video_id": vid, "title": title, "desc": desc,
            "size": os.path.getsize(args.out), "duration": duration,
            "has_audio": ha, "url": args.url}
    print("META=" + json.dumps(meta, ensure_ascii=False))
    print(f"OUTPUT={args.out}")


if __name__ == "__main__":
    main()
