#!/usr/bin/env python3
"""
交叉校验两路文本（OCR 字幕 vs ASR），取更可靠的版本并标出差异。

为什么需要这一步：
  实测中两者各有错字，且错法不同：
    - ASR 常错在：同音字（康波→康姆）、数字（三分之二→2%）、专有名词（珍妮机→装漆机）
    - OCR 常错在：形近字（繁荣→繁菜）、标点缺失、字幕滚动导致的半句

  单用任何一路都会把错误写进最终成品。两路比对能定位到具体位置，
  让修正有的放矢，而不是全篇重读。

用法：
  python3 crosscheck.py <ocr.json> <asr.json> [--out merged.json]

输入格式：{"lines": [{"start","end","text"}]} 或 纯 lines 数组
输出：merged.json，含
  - lines: 择优后的文本
  - conflicts: [{t, ocr, asr}]  两路明显不一致处，供人工/模型判定
  - stats: 覆盖率对比
"""
import argparse
import json
import re
import sys
from difflib import SequenceMatcher


def load(path):
    d = json.load(open(path))
    return d["lines"] if isinstance(d, dict) else d


def norm(s):
    """归一化：去空格标点，便于比对。"""
    return re.sub(r"[\s，。、；：？！,.;:?!\"'（）()《》〈〉…—\-]", "", s or "")


def han_only(s):
    return "".join(c for c in s if "\u4e00" <= c <= "\u9fff")


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def asr_chars(lines):
    return sum(len(han_only(l["text"])) for l in lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ocr")
    ap.add_argument("asr")
    ap.add_argument("--out", default="merged.json")
    args = ap.parse_args()

    A = load(args.ocr)   # 假设 A 是内嵌字幕（更可信）
    B = load(args.asr)

    print(f"[cross] ocr={len(A)}行 {asr_chars(A)}字", file=sys.stderr)
    print(f"[cross] asr={len(B)}行 {asr_chars(B)}字", file=sys.stderr)

    # 以 A（字幕）为骨架。字幕是画面上的确切文字，通常比听写可靠。
    merged, conflicts = [], []

    for a in A:
        t_mid = (a["start"] + a["end"]) / 2
        # 找时间上最接近的 B 行。ASR 一行常覆盖字幕多行，
        # 所以同时看"前后各一行拼起来"的窗口，避免误判为冲突。
        idx = min(range(len(B)), key=lambda i: abs((B[i]["start"] + B[i]["end"]) / 2 - t_mid))
        cand = B[idx]
        window = "".join(B[max(0, i)]["text"] for i in range(max(0, idx - 1), min(len(B), idx + 2)))
        s = max(sim(a["text"], cand["text"]), sim(a["text"], window))
        # 只有当这一行汉字数足够、且两种读法都非空时才算真冲突
        if s < 0.45 and len(han_only(a["text"])) >= 3 and han_only(cand["text"]):
            conflicts.append({
                "t": round(a["start"], 1),
                "subtitle": a["text"],
                "asr": cand["text"],
                "sim": round(s, 2),
            })
        merged.append({
            "start": a["start"],
            "end": a["end"],
            "text": a["text"],
            "alt": cand["text"] if s >= 0.45 else None,  # 供参考的另一种读法
        })

    out = {
        "source": "subtitle (OCR) + ASR crosscheck",
        "lines": merged,
        "conflicts": conflicts,
        "stats": {
            "subtitle_lines": len(A),
            "asr_lines": len(B),
            "subtitle_chars": asr_chars(A),
            "asr_chars": asr_chars(B),
            "conflict_count": len(conflicts),
        },
    }
    json.dump(out, open(args.out, "w"), ensure_ascii=False, indent=1)
    print(f"[cross] conflicts={len(conflicts)} -> {args.out}", file=sys.stderr)

    if conflicts:
        print("\n=== 两路不一致处（前 15 条，需人工判定） ===", file=sys.stderr)
        for c in conflicts[:15]:
            print(f"  [{c['t']}s] 字幕: {c['subtitle']}", file=sys.stderr)
            print(f"          ASR : {c['asr']}  (sim={c['sim']})", file=sys.stderr)


if __name__ == "__main__":
    main()
