#!/usr/bin/env python3
"""
把逐句字幕合并为「语义段」，供三种讲解模式使用。

合并规则（按优先级）：
  1) 时间间隔 > gap 秒 → 断段（说话人停顿）
  2) 累积字符数 > max_chars → 断段（避免一段太长）
  3) 句末标点（。？！）→ 优先断段

用法：
  python3 segment.py <transcript.json> [--mode s|k|f] [--max-chars N] [--gap S]

  --mode s  时间表对照（默认）：粗分段，每段 60-120 秒语速量
  --mode k  关键节点：更粗，只挑信息密度高的，段落 100-200 秒
  --mode f  逐片讲解：保留全部，段落 45-90 秒

输出：<transcript.json 同目录>/segments.json
"""
import argparse
import json
import os
import re
import sys

END_PUNCT = "。？！.?!"


def tc(sec):
    """秒 → mm:ss"""
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def build_segments(lines, max_chars=260, gap=2.5):
    segs = []
    cur = []

    def flush():
        if not cur:
            return
        text = "".join(l["text"] for l in cur)
        segs.append({
            "start": cur[0]["start"],
            "end": cur[-1]["end"],
            "text": text,
            # 保留原始分句，供渲染时自然断行（OCR 字幕常无标点，不能靠标点断句）
            "lines": [{"t": round(x["start"], 2), "s": x["text"]} for x in cur],
            "n_lines": len(cur),
        })
        cur.clear()

    for i, l in enumerate(lines):
        cur.append(l)
        chars = sum(len(x["text"]) for x in cur)
        # 到下一句的间隔
        nxt_gap = (lines[i + 1]["start"] - l["end"]) if i + 1 < len(lines) else 999
        ends_punct = l["text"].rstrip()[-1:] in END_PUNCT
        enough = chars >= max_chars * 0.55

        # 优先在句末标点处断开；无标点时只在明显停顿且足够长才断
        if ends_punct and chars >= max_chars * 0.45:
            flush()
        elif nxt_gap > gap and chars >= max_chars * 0.6:
            flush()
        elif chars >= max_chars * 1.6:
            flush()

    flush()
    return merge_short_tail(segs, min_dur=8.0)


def merge_short_tail(segs, min_dur=8.0):
    """把过短的尾段并入前一段（常见于片尾口播、片头 logo）。"""
    if len(segs) < 2:
        return segs
    out = [segs[0]]
    for s in segs[1:]:
        if s["end"] - s["start"] < min_dur and len(out) >= 1:
            prev = out[-1]
            prev["end"] = s["end"]
            prev["text"] += s["text"]
            prev["lines"].extend(s["lines"])
            prev["n_lines"] += s["n_lines"]
        else:
            out.append(s)
    return out


MODE_PRESETS = {
    "s": {"max_chars": 240, "gap": 2.5},   # 时间表对照：细
    "k": {"max_chars": 420, "gap": 3.5},   # 关键节点：略粗
    "f": {"max_chars": 170, "gap": 2.0},   # 逐片讲解：最细
}


def score_segment(seg):
    """
    粗评信息密度，供「关键节点」模式筛选。
    信号：数字、引号、专有名词式大写、转折词、因果词、提问。
    """
    t = seg["text"]
    score = 0.0
    score += len(re.findall(r"\d", t)) * 0.35
    score += len(re.findall(r"[%％]", t)) * 1.2
    score += len(re.findall(r"(但是|然而|其实|本质上|关键|核心|为什么|原因|意味着|也就是说|反过来|注意)", t)) * 1.6
    score += len(re.findall(r"(？|\?)", t)) * 1.0
    score += len(re.findall(r"[A-Z]{2,}", t)) * 0.5
    score += len(re.findall(r"(第一|第二|第三|第四|第五|首先|其次|最后)", t)) * 1.2
    # 长度归一（太长不一定好）
    score /= max(1.0, (len(t) / 100.0) ** 0.55)
    return round(score, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--mode", default="s", choices=list(MODE_PRESETS))
    ap.add_argument("--max-chars", type=int, default=None)
    ap.add_argument("--gap", type=float, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    data = json.load(open(args.transcript))
    lines = data["lines"]

    pre = MODE_PRESETS[args.mode]
    mc = args.max_chars or pre["max_chars"]
    gp = args.gap if args.gap is not None else pre["gap"]

    segs = build_segments(lines, mc, gp)
    for s in segs:
        s["tc"] = tc(s["start"])
        s["dur"] = round(s["end"] - s["start"], 1)
        s["score"] = score_segment(s)

    out = args.out or os.path.join(os.path.dirname(args.transcript), "segments.json")
    dur = data.get("meta", {}).get("duration") or (lines[-1]["end"] if lines else 0)
    json.dump({
        "source": data.get("source"),
        "meta": data.get("meta"),
        "mode": args.mode,
        "count": len(segs),
        "duration": dur,
        "segments": segs,
    }, open(out, "w"), ensure_ascii=False, indent=1)

    print(f"mode={args.mode} segments={len(segs)} -> {out}", file=sys.stderr)
    for s in segs[:8]:
        print(f"  [{s['tc']}] {s['dur']}s score={s['score']} {s['text'][:56]}")
    if len(segs) > 8:
        print(f"  ... 共 {len(segs)} 段")


if __name__ == "__main__":
    main()
