#!/usr/bin/env python3
"""把仓库里的 Markdown 渲染成一个可离线浏览的预览页。

用法：
    python tools/build_preview.py
    python tools/build_preview.py --out .preview/preview.html

产物是单文件 HTML，不联网、不依赖第三方库，直接双击即可查看。
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOCS = [
    ("README.md", "项目首页"),
    ("README_EN.md", "English"),
    ("DISCLAIMER.md", "免责声明"),
    ("DISTRIBUTION.md", "转载政策"),
    ("LICENSE-SCOPE.md", "许可范围"),
    ("THIRD_PARTY_NOTICES.md", "第三方说明"),
    ("CONTRIBUTING.md", "参与与反馈"),
    ("CHANGELOG.md", "更新日志"),
    ("docs/INSTALL.md", "安装说明"),
    ("docs/FAQ.md", "常见问题"),
    ("docs/KNOWN_ISSUES.md", "已知问题"),
    ("docs/COMPATIBILITY.md", "兼容性"),
    ("docs/VERIFICATION.md", "验证记录"),
    ("docs/TEST_REPORT.md", "测试报告"),
    ("docs/CREDITS.md", "制作与致谢"),
    ("installer/README.md", "安装器"),
    ("tools/README.md", "工具"),
]

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
ISO_LINK = re.compile(r"\[([^\]]*)\]\((?!https?://)([^)]+\.md)(#[^)]*)?\)")


def inline(text: str, current_dir: Path | None = None) -> str:
    text = html.escape(text, quote=False)
    text = INLINE_CODE.sub(r"<code>\1</code>", text)
    text = BOLD.sub(r"<strong>\1</strong>", text)

    def repl(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://", "mailto:")):
            return f'<a href="{target}" target="_blank" rel="noreferrer">{label}</a>'
        return f'<span class="xlink" title="{target}">{label}</span>'

    return LINK.sub(repl, text)


def slug(path: str) -> str:
    return "doc-" + re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")


def render(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    in_list = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            continue

        if stripped.startswith("<!--"):
            while i < len(lines) and "-->" not in lines[i]:
                i += 1
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in header) + "</tr></thead><tbody>")
            for row in rows:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
            out.append("</tbody></table>")
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + "".join(f"<p>{inline(b)}</p>" for b in buf if b) + "</blockquote>")
            continue

        m = re.match(r"^[-*]\s+\[( |x|X)\]\s+(.*)$", stripped)
        if m:
            if not in_list:
                out.append('<ul class="check">')
                in_list = True
            mark = "checked" if m.group(1).lower() == "x" else ""
            out.append(f'<li><input type="checkbox" disabled {mark}> {inline(m.group(2))}</li>')
            i += 1
            continue

        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(m.group(1))}</li>")
            i += 1
            continue

        m = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m:
            if not in_list:
                out.append("<ol>")
                in_list = True
            out.append(f"<li>{inline(m.group(1))}</li>")
            i += 1
            continue

        if in_list:
            out.append("</ul>" if out and out[-1].startswith("<li>") else "</ol>")
            in_list = False

        if not stripped:
            i += 1
            continue

        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|>|[-*]\s|\d+\.\s|\||```)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + inline(" ".join(para)) + "</p>")

    if in_list:
        out.append("</ul>")
    return "\n".join(out)


CSS = """
:root{--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--bg:#f6f8fa;--accent:#0969da}
*{box-sizing:border-box}
body{margin:0;font:15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:var(--fg);background:#fff}
.layout{display:flex;min-height:100vh}
nav{width:250px;flex:none;padding:22px 16px;background:var(--bg);border-right:1px solid var(--line);position:sticky;top:0;height:100vh;overflow:auto}
nav .brand{font-weight:700;font-size:15px;margin-bottom:4px}
nav .sub{color:var(--muted);font-size:12px;margin-bottom:16px}
nav a{display:block;padding:5px 8px;border-radius:6px;color:var(--fg);text-decoration:none;font-size:13.5px}
nav a:hover{background:#e7edf3;color:var(--accent)}
main{flex:1;min-width:0;padding:28px 40px 80px;max-width:900px}
section{padding-bottom:36px;margin-bottom:36px;border-bottom:1px solid var(--line)}
h1{font-size:26px;border-bottom:1px solid var(--line);padding-bottom:8px}
h2{font-size:20px;margin-top:28px;border-bottom:1px solid var(--line);padding-bottom:6px}
h3{font-size:17px;margin-top:22px}
code{background:var(--bg);padding:1px 5px;border-radius:5px;font-size:13px;font-family:Consolas,monospace}
pre{background:var(--bg);padding:12px 14px;border-radius:8px;overflow:auto}
pre code{background:none;padding:0}
blockquote{margin:14px 0;padding:6px 16px;border-left:4px solid #c8d5e2;color:#3d4650;background:#fbfcfd}
blockquote p{margin:6px 0}
table{border-collapse:collapse;margin:14px 0;width:100%;font-size:14px}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--bg)}
ul,ol{padding-left:24px}
li{margin:4px 0}
ul.check{list-style:none;padding-left:4px}
.xlink{border-bottom:1px dashed #b9c4d0;cursor:help}
footer{color:var(--muted);font-size:12px;padding:0 40px 40px;max-width:900px}
"""


def build(out: Path) -> Path:
    nav, body = [], []
    for rel, title in DOCS:
        path = ROOT / rel
        if not path.exists():
            continue
        nav.append(f'<a href="#{slug(rel)}">{html.escape(title)}</a>')
        body.append(f'<section id="{slug(rel)}">{render(path.read_text(encoding="utf-8"))}</section>')
    page = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>ACE3 汉化 · 文档预览</title><style>"
        + CSS
        + "</style></head><body><div class=\"layout\"><nav>"
        + '<div class="brand">ACE3 汉化</div><div class="sub">本地预览 · 非发布版</div>'
        + "".join(nav)
        + "</nav><main>"
        + "\n".join(body)
        + "</main></div><footer>本页由 tools/build_preview.py 生成，用于检查文档排版；不代表已发布。</footer></body></html>"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=".preview/preview.html")
    args = parser.parse_args()
    result = build(ROOT / args.out)
    print(f"已生成 {result}  ({result.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
