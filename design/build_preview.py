"""把 design/source 下的界面设计稿（.dc.html）转换成可以直接在浏览器打开的网页。

用法：python design/build_preview.py
输出：design/html/*.html
"""
import html
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "source"
OUT = ROOT / "html"

# 设计稿文件名 -> （输出文件名, 标题, 是否桌面窗口）
PAGES = {
    "Main.dc.html": ("device-wall.html", "管理网站 · 设备墙", False),
    "Playback.dc.html": ("playback.html", "管理网站 · 录像回放", False),
    "Settings.dc.html": ("settings.html", "管理网站 · 设置", False),
    "Client.dc.html": ("client.html", "桌面端 · 录像客户端", True),
    "RepairTool.dc.html": ("repair-tool.html", "桌面端 · 修复工具", True),
    "Tray.dc.html": ("tray.html", "桌面端 · 托盘状态窗", True),
}


def convert(src_name: str) -> str:
    out_name, title, is_window = PAGES[src_name]
    text = (SRC / src_name).read_text(encoding="utf-8")

    body = re.search(r"<x-dc>(.*)</x-dc>", text, re.S).group(1)
    helmet = re.search(r"<helmet>(.*?)</helmet>", body, re.S)
    head_extra = helmet.group(1) if helmet else ""
    markup = body.replace(helmet.group(0), "") if helmet else body

    # 循环和条件改成 <template>，表格里也能正确解析
    markup = re.sub(r'<sc-for\s+list="([^"]*)"\s+as="([^"]*)"', r'<template data-for="\1" data-as="\2"', markup)
    markup = markup.replace("</sc-for>", "</template>")
    markup = re.sub(r'<sc-if\s+value="([^"]*)"', r'<template data-if="\1"', markup)
    markup = markup.replace("</sc-if>", "</template>")

    # 设计稿之间的链接改成输出文件名
    for src, (dst, _, _) in PAGES.items():
        markup = markup.replace(f'href="{src}"', f'href="{dst}"')

    script_tag = re.search(r"<script type=\"text/x-dc\" data-dc-script data-props='([^']*)'>(.*?)</script>", text, re.S)
    props_raw = html.unescape(script_tag.group(1))
    props = {k: v.get("default") for k, v in json.loads(props_raw).items() if not k.startswith("$") and isinstance(v, dict)}
    logic = script_tag.group(2)

    wrap_style = (
        "min-height:100vh;display:flex;align-items:flex-start;justify-content:center;padding:48px 24px;box-sizing:border-box;background:#E8E8E8"
        if is_window else ""
    )
    nav_links = " · ".join(f'<a href="{dst}">{t.split(" · ")[1]}</a>' for _, (dst, t, _) in PAGES.items())

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EverRec · {title}</title>
{head_extra}
<style>
.preview-bar{{font:13px -apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;padding:8px 16px;background:#1F1F1F;color:#D4D4D4;display:flex;gap:16px;flex-wrap:wrap;align-items:center}}
.preview-bar a{{color:#FFFFFF}}
</style>
</head>
<body>
<div class="preview-bar"><strong>EverRec 界面设计稿</strong><span>{title}</span><span style="margin-left:auto"><a href="index.html">目录</a> · {nav_links}</span></div>
<div style="{wrap_style}"><div id="dc-root"></div></div>
<template id="dc-template">{markup}</template>
<script src="runtime.js"></script>
<script>
(function () {{
{logic}
window.__dcBoot(Component, {json.dumps(props, ensure_ascii=False)});
}})();
</script>
</body>
</html>
"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    items = []
    for src_name, (out_name, title, _) in PAGES.items():
        (OUT / out_name).write_text(convert(src_name), encoding="utf-8")
        items.append(f'<li><a href="{out_name}">{title}</a></li>')
        print("built", out_name)
    (OUT / "index.html").write_text(
        """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EverRec 界面设计稿</title>
<style>body{font:15px/1.7 -apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#383838;max-width:720px;margin:48px auto;padding:0 24px}a{color:#383838}h1{font-size:22px}</style>
</head><body>
<h1>EverRec（恒录）界面设计稿</h1>
<p>管理网站 3 页，桌面端 3 个窗口。页面上的按钮和筛选可以点击；数据均为示例。</p>
<ul>
""" + "\n".join(items) + """
</ul>
<p>客户端在网址后加 <code>?mode=离线</code> 可查看离线状态，例如 <a href="client.html?mode=离线">client.html?mode=离线</a>。</p>
</body></html>
""",
        encoding="utf-8",
    )
    print("built index.html")


if __name__ == "__main__":
    main()
