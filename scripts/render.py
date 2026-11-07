#!/usr/bin/env python3
"""
把「分段数据 + 讲解内容」渲染成 HTML 页。

讲解内容由模型填入 contents JSON，本脚本只负责排版。
这样做的原因：排版是确定性工作，不该让模型每天重新发明一遍 CSS。

用法：
  python3 render.py <segments.json> <contents.json> <out.html>

contents.json 结构：
{
  "title": "...",
  "subtitle": "...",
  "mode": "s" | "k" | "f",
  "lead": {"intro": "...", "items": ["...", "..."], "onesent": "..."},
  "terms": [{"zh","en","at","def"}, ...],
  "segments": {
     "<段索引或时间码>": {
        "head": "小标题",
        "read": ["解读段落1", "解读段落2"],
        "flags": [{"kind":"ok|warn|err", "label":"...", "text":"..."}],
        "addon": "补充说明（可选）",
        "extra_table": {"headers":[...], "rows":[[...]]}  // 可选
     }
  },
  "notes": [{"title","paras":[...]}],       // 批判/说明区块
  "facts": [{"n","l"}],                      // 硬数字
  "recap": {"items":[...], "onesent":"..."},
  "xhs": {"title":"...", "body":"...", "tags":["..."]},   // 小红书文案
  "footer": "..."
}
"""
import json
import os
import sys
import html

CSS = """
:root{
  --paper:#FBFAF7; --card:#FFFFFF;
  --ink:#191714; --ink2:#413C36; --muted:#767067; --faint:#A8A29A;
  --line:#E4DFD6; --line2:#EFEAE1;
  --blue:#1D5FD0; --blue-bg:#EAF0FC;
  --warm:#B4530A; --warm-bg:#FDF2E6; --warm-line:#F0D9BF;
  --green:#1F6B4A; --green-bg:#EAF5EF;
  --red:#A32B2B; --red-bg:#FBEDED; --red-line:#F0CFCF;
}
*{box-sizing:border-box;}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Hiragino Sans GB",sans-serif;
  font-size:16px;line-height:1.75;-webkit-font-smoothing:antialiased;}
.wrap{max-width:1080px;margin:0 auto;padding:0 28px 120px;}
header{padding:60px 0 36px;border-bottom:1px solid var(--line);}
.kicker{font-size:11.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--faint);font-weight:650;margin-bottom:20px;}
h1{font-size:33px;line-height:1.3;margin:0 0 16px;font-weight:680;letter-spacing:-.01em;}
.sub{font-size:16.5px;color:var(--ink2);margin:0 0 24px;line-height:1.7;max-width:820px;}
.meta{display:flex;flex-wrap:wrap;gap:8px 22px;font-size:13px;color:var(--muted);}
.meta b{color:var(--ink2);font-weight:600;}
.meta a{color:var(--blue);text-decoration:none;border-bottom:1px solid #B9CDF3;}
section{padding:56px 0 0;}
h2{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);font-weight:700;
   margin:0 0 26px;display:flex;align-items:center;gap:14px;}
h2::after{content:"";flex:1;height:1px;background:var(--line2);}
.lead{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:30px 34px;}
.lead p{margin:0 0 16px;color:var(--ink2);font-size:16px;}
.lead ol{margin:0;padding-left:22px;}
.lead li{margin-bottom:16px;color:var(--ink2);}
.lead li:last-child{margin-bottom:0;}
.lead b{color:var(--ink);font-weight:640;}
.onesent{margin-top:24px;padding-top:22px;border-top:1px dashed var(--line);font-size:17px;font-weight:560;line-height:1.7;}
.terms{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;}
.term{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:15px 18px;}
.tt{display:flex;align-items:baseline;gap:8px;margin-bottom:6px;}
.tt .zh{font-size:15.5px;font-weight:660;}
.tt .en{font-size:11px;color:var(--faint);}
.tt .at{margin-left:auto;font-size:11px;color:var(--blue);font-weight:650;font-variant-numeric:tabular-nums;white-space:nowrap;}
.term .d{font-size:13.5px;color:var(--muted);line-height:1.62;}
.seg{background:var(--card);border:1px solid var(--line);border-radius:14px;margin-bottom:22px;overflow:hidden;}
.seg-head{padding:20px 26px 16px;background:linear-gradient(180deg,#FDFCFA,#fff);border-bottom:1px solid var(--line2);}
.tcode{display:inline-flex;align-items:center;gap:6px;font-family:ui-monospace,"SF Mono",Menlo,monospace;
  font-size:12.5px;font-weight:650;color:var(--blue);background:var(--blue-bg);
  border-radius:6px;padding:3px 9px;margin-bottom:11px;text-decoration:none;}
.tcode:hover{background:#DCE7FA;}
.tcode::after{content:"\\2197";font-size:10px;opacity:.55;}
.seg-head h3{margin:0;font-size:19px;font-weight:660;line-height:1.45;}
.duo{display:grid;grid-template-columns:minmax(0,42fr) minmax(0,58fr);}
.col-src{padding:20px 22px 22px;border-right:1px solid var(--line2);background:#FDFCFA;}
.col-read{padding:20px 26px 22px;}
.clabel{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;font-weight:700;color:var(--faint);margin-bottom:12px;}
.col-src .clabel{color:#9C958A;}
.src{font-size:14.5px;line-height:1.85;color:#514B44;}
.src .ln{display:flex;gap:9px;margin-bottom:5px;}
.src .ln .lt{flex:0 0 34px;font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#B3ACA1;
  text-align:right;padding-top:3px;font-variant-numeric:tabular-nums;}
.src .ln .ls{flex:1;}
.col-read p{margin:0 0 13px;font-size:15.5px;line-height:1.8;color:var(--ink2);}
.col-read p:last-child{margin-bottom:0;}
.col-read strong{color:var(--ink);font-weight:650;}
.col-read em{font-style:normal;background:#FFF3C4;padding:0 2px;border-radius:2px;}
.flag{margin:16px 0 0;border-radius:9px;padding:14px 16px;font-size:14px;line-height:1.72;}
.flag .ft{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;font-weight:700;margin-bottom:6px;}
.flag.warn{background:var(--warm-bg);border:1px solid var(--warm-line);color:#6B3A0E;}
.flag.warn .ft{color:#8A4A0F;}
.flag.err{background:var(--red-bg);border:1px solid var(--red-line);color:#6E2020;}
.flag.err .ft{color:#8E2A2A;}
.flag.ok{background:var(--green-bg);border:1px solid #CBE5D8;color:#154C35;}
.flag.ok .ft{color:#1A5B3F;}
.addon{margin-top:14px;padding-top:13px;border-top:1px dashed var(--line);font-size:13.5px;color:var(--muted);line-height:1.7;}
.addon b{color:var(--ink2);font-weight:620;}
table.xt{width:100%;border-collapse:collapse;margin:14px 0 0;font-size:13.5px;}
table.xt th{background:#F4F1EA;text-align:left;padding:8px 10px;font-weight:650;color:var(--ink2);
  border-bottom:1px solid var(--line);font-size:12.5px;}
table.xt td{padding:8px 10px;border-bottom:1px solid var(--line2);color:var(--ink2);vertical-align:top;}
table.xt tr:last-child td{border-bottom:none;}
.crit{background:#F6F4EF;border:1px solid var(--line);border-radius:14px;padding:30px 34px;}
.crit h4{margin:0 0 12px;font-size:16.5px;font-weight:660;}
.crit h4:not(:first-child){margin-top:26px;}
.crit p{margin:0 0 12px;font-size:15px;color:var(--ink2);line-height:1.78;}
.crit p:last-child{margin-bottom:0;}
.crit b{color:var(--ink);font-weight:650;}
.facts{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;}
.fact{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:16px 18px;}
.fact .n{font-size:22px;font-weight:690;letter-spacing:-.02em;color:var(--ink);font-variant-numeric:tabular-nums;}
.fact .l{font-size:12.5px;color:var(--muted);margin-top:4px;line-height:1.55;}
/* 小红书 */
.xhs{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;}
.xhs-top{padding:18px 24px;border-bottom:1px solid var(--line2);background:linear-gradient(180deg,#FFF9F5,#fff);
  display:flex;align-items:center;gap:10px;}
.xhs-badge{font-size:11px;font-weight:700;color:#D0342C;background:#FDEDEB;border-radius:5px;padding:3px 8px;letter-spacing:.04em;}
.xhs-title{font-size:16.5px;font-weight:670;color:var(--ink);flex:1;}
.copy-btn{font-size:12px;font-weight:620;color:var(--blue);background:var(--blue-bg);border:none;
  border-radius:6px;padding:6px 12px;cursor:pointer;font-family:inherit;}
.copy-btn:hover{background:#DCE7FA;}
.copy-btn.done{color:var(--green);background:var(--green-bg);}
.xhs-body{padding:22px 24px;font-size:15px;line-height:1.9;color:var(--ink2);white-space:pre-wrap;}
.xhs-body .xh{color:var(--ink);font-weight:650;}
.xhs-tags{padding:0 24px 22px;display:flex;flex-wrap:wrap;gap:7px;}
.tag{font-size:12.5px;color:var(--blue);background:var(--blue-bg);border-radius:5px;padding:3px 9px;}
footer{margin-top:64px;padding-top:26px;border-top:1px solid var(--line);font-size:12.5px;color:var(--faint);line-height:1.85;}
@media (max-width:820px){
  .wrap{padding:0 18px 80px;}
  h1{font-size:25px;}
  header{padding:40px 0 28px;}
  .terms,.facts{grid-template-columns:1fr;}
  .duo{grid-template-columns:1fr;}
  .col-src{border-right:none;border-bottom:1px solid var(--line2);}
  .lead,.crit{padding:22px;}
  .seg-head,.col-src,.col-read{padding-left:18px;padding-right:18px;}
}
"""


def time_jump(url, platform, sec):
    """
    按平台生成带时间码的跳转链接。

    各平台的时间码格式完全不同，不能统一拼 &t=Ns：
      YouTube      ?v=ID&t=123s
      Bilibili     ?t=123          （秒，无 s 后缀）
      抖音/小红书   不支持精确时间戳，回落到原链接
      本地文件     无链接
    """
    sec = int(sec)
    if not url:
        return "#"
    if platform == "youtube":
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={sec}s"
    if platform == "bilibili":
        base = url.split("?")[0]
        return f"{base}?t={sec}"
    # 抖音、小红书等：无标准时间戳参数，回原链接
    return url


def esc(s):
    return html.escape(str(s if s is not None else ""))


def inline(s):
    """把 **粗体** 和 `代码` 和 ==高亮== 转成 HTML。"""
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"==(.+?)==", r"<em>\1</em>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


import re


def render(segdata, content):
    meta = segdata.get("meta", {})
    segs = segdata["segments"]
    c_segs = content.get("segments", {})
    mode = content.get("mode", segdata.get("mode", "s"))
    mode_label = {"s": "时间表对照", "k": "关键节点讲解", "f": "逐片讲解"}.get(mode, "时间表对照")

    dur = meta.get("duration") or segdata.get("duration") or 0
    dur_s = f"{int(dur // 60)}:{int(dur % 60):02d}"
    url = meta.get("url", "")

    P = []
    P.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    P.append(f"<title>{esc(content.get('title','视频精读'))}</title>")
    P.append(f"<style>{CSS}</style></head><body><div class='wrap'>")

    # header
    P.append("<header>")
    P.append(f"<div class='kicker'>{esc(mode_label)} · 左右对照 · 零基础</div>")
    P.append(f"<h1>{esc(content.get('title',''))}</h1>")
    if content.get("subtitle"):
        P.append(f"<p class='sub'>{inline(content['subtitle'])}</p>")
    P.append("<div class='meta'>")
    plat = meta.get("platform", "")
    plat_label = {
        "youtube": "YouTube", "bilibili": "Bilibili",
        "douyin": "抖音", "xiaohongshu": "小红书",
        "local": "本地文件", "kuaishou": "快手", "ixigua": "西瓜视频",
    }.get(plat, "")
    if plat_label:
        P.append(f"<span>来源 <b>{esc(plat_label)}</b></span>")
    if meta.get("uploader"):
        P.append(f"<span>频道：<b>{esc(meta['uploader'])}</b></span>")
    P.append(f"<span>时长 <b>{dur_s}</b></span>")
    if meta.get("upload_date"):
        d = str(meta["upload_date"])
        if len(d) == 8:
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        P.append(f"<span>发布 <b>{esc(d)}</b></span>")
    if url:
        P.append(f"<span><a href='{esc(url)}' target='_blank'>原片链接 &nearr;</a></span>")
    if content.get("segcount_note"):
        P.append(f"<span>分段 <b>{len(segs)}</b> 段</span>")
    P.append("</div></header>")

    # lead
    lead = content.get("lead")
    if lead:
        P.append("<section><h2>先给你一张地图</h2><div class='lead'>")
        if lead.get("intro"):
            P.append(f"<p>{inline(lead['intro'])}</p>")
        if lead.get("items"):
            P.append("<ol>")
            for it in lead["items"]:
                P.append(f"<li>{inline(it)}</li>")
            P.append("</ol>")
        if lead.get("onesent"):
            P.append(f"<div class='onesent'>{inline(lead['onesent'])}</div>")
        P.append("</div></section>")

    # terms
    terms = content.get("terms")
    if terms:
        P.append("<section><h2>听之前，先拿下这些词</h2><div class='terms'>")
        for t in terms:
            P.append("<div class='term'><div class='tt'>")
            P.append(f"<span class='zh'>{esc(t.get('zh',''))}</span>")
            if t.get("en"):
                P.append(f"<span class='en'>{esc(t['en'])}</span>")
            if t.get("at"):
                P.append(f"<span class='at'>{esc(t['at'])}</span>")
            P.append(f"</div><div class='d'>{inline(t.get('def',''))}</div></div>")
        P.append("</div></section>")

    # segments
    P.append(f"<section><h2>全片逐段对照</h2>")
    for idx, sg in enumerate(segs):
        c = c_segs.get(str(idx)) or c_segs.get(sg.get("tc", "")) or {}
        tc = sg.get("tc", "")
        jump = time_jump(url, meta.get("platform", ""), sg["start"])
        P.append("<article class='seg'><div class='seg-head'>")
        P.append(f"<a class='tcode' href='{esc(jump)}' target='_blank'>{esc(tc)} – "
                 f"{int(sg['end'])//60}:{int(sg['end'])%60:02d}</a>")
        head = c.get("head") or "（未填写小标题）"
        P.append(f"<h3>{esc(head)}</h3></div>")

        P.append("<div class='duo'><div class='col-src'><div class='clabel'>原话</div><div class='src'>")
        for ln in sg.get("lines", []):
            t = ln["t"]
            lt = f"{int(t)//60}:{int(t)%60:02d}"
            P.append(f"<div class='ln'><span class='lt'>{lt}</span>"
                     f"<span class='ls'>{esc(ln['s'])}</span></div>")
        P.append("</div></div>")

        P.append("<div class='col-read'><div class='clabel'>解读</div>")
        for para in c.get("read", []):
            P.append(f"<p>{inline(para)}</p>")
        for f in c.get("flags", []):
            kind = f.get("kind", "warn")
            P.append(f"<div class='flag {kind}'><div class='ft'>{esc(f.get('label',''))}</div>"
                     f"{inline(f.get('text',''))}</div>")
        xt = c.get("extra_table")
        if xt:
            P.append("<table class='xt'><thead><tr>")
            for h in xt.get("headers", []):
                P.append(f"<th>{esc(h)}</th>")
            P.append("</tr></thead><tbody>")
            for row in xt.get("rows", []):
                P.append("<tr>" + "".join(f"<td>{inline(x)}</td>" for x in row) + "</tr>")
            P.append("</tbody></table>")
        if c.get("addon"):
            P.append(f"<div class='addon'>{inline(c['addon'])}</div>")
        P.append("</div></div></article>")
    P.append("</section>")

    # notes
    notes = content.get("notes")
    if notes:
        P.append("<section><h2>读完之后</h2><div class='crit'>")
        for n in notes:
            P.append(f"<h4>{esc(n.get('title',''))}</h4>")
            for p in n.get("paras", []):
                P.append(f"<p>{inline(p)}</p>")
        P.append("</div></section>")

    # facts
    facts = content.get("facts")
    if facts:
        P.append("<section><h2>值得记住的硬数字</h2><div class='facts'>")
        for f in facts:
            P.append(f"<div class='fact'><div class='n'>{esc(f.get('n',''))}</div>"
                     f"<div class='l'>{inline(f.get('l',''))}</div></div>")
        P.append("</div></section>")

    # recap
    recap = content.get("recap")
    if recap:
        P.append("<section><h2>所以这条片子该怎么用</h2><div class='lead'>")
        if recap.get("items"):
            P.append("<ol>")
            for it in recap["items"]:
                P.append(f"<li>{inline(it)}</li>")
            P.append("</ol>")
        if recap.get("onesent"):
            P.append(f"<div class='onesent'>{inline(recap['onesent'])}</div>")
        P.append("</div></section>")

    # xhs
    xhs = content.get("xhs")
    if xhs:
        P.append("<section><h2>小红书文案</h2><div class='xhs'>")
        P.append("<div class='xhs-top'><span class='xhs-badge'>小红书</span>")
        P.append(f"<span class='xhs-title'>{esc(xhs.get('title',''))}</span>")
        P.append("<button class='copy-btn' onclick=\"copyXhs(this)\">复制正文</button></div>")
        body = esc(xhs.get("body", ""))
        P.append(f"<div class='xhs-body' id='xhsbody'>{body}</div>")
        if xhs.get("tags"):
            P.append("<div class='xhs-tags'>")
            for tg in xhs["tags"]:
                P.append(f"<span class='tag'>#{esc(tg)}</span>")
            P.append("</div>")
        P.append("</div></section>")

    if content.get("footer"):
        P.append(f"<footer>{inline(content['footer'])}</footer>")

    P.append("""<script>
function copyXhs(btn){
  var t=document.getElementById('xhsbody').innerText;
  var tags=Array.from(document.querySelectorAll('.tag')).map(function(e){return e.innerText}).join(' ');
  navigator.clipboard.writeText(t+'\\n\\n'+tags).then(function(){
    btn.textContent='已复制';btn.classList.add('done');
    setTimeout(function(){btn.textContent='复制正文';btn.classList.remove('done')},1600);
  });
}
</script>""")
    P.append("</div></body></html>")
    return "\n".join(P)


def main():
    if len(sys.argv) < 4:
        print("usage: render.py <segments.json> <contents.json> <out.html>", file=sys.stderr)
        sys.exit(2)
    segdata = json.load(open(sys.argv[1]))
    content = json.load(open(sys.argv[2]))
    htmlstr = render(segdata, content)
    open(sys.argv[3], "w", encoding="utf-8").write(htmlstr)
    print(f"wrote {sys.argv[3]} ({len(htmlstr)//1024}KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
