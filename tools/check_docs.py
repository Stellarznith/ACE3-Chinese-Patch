#!/usr/bin/env python3
"""发布前自检：链接完整性、编码、本地路径泄漏、二进制混入。

用法：
    python tools/check_docs.py            # 在仓库根目录执行
    python tools/check_docs.py --strict   # 把"待定"占位也视为失败

退出码：0 全通过；1 有失败项。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "build", "dist", "out", "tmp", "__pycache__", ".preview"}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
ABS_PATH_RE = re.compile(r"[A-Za-z]:\\\\(?:CODEX|Users|Windows)|/mnt/[a-z]/")
BINARY_SUFFIXES = {".iso", ".bin", ".xdelta", ".exe", ".dll", ".zip", ".7z"}
# 随源码一并发布的第三方命令行工具：体积小、经核验、许可允许再分发，属仓库内容而非构建产物
BINARY_ALLOWLIST = {
    Path("installer/third_party/xdelta3-3.2.2/xdelta3.exe"),
    Path("installer/third_party/xdelta3-3.2.2/xdelta3decode.exe"),
}


def iter_files() -> list[Path]:
    out = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        out.append(path)
    return sorted(out)


def check_links(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        if path.suffix.lower() not in {".md", ".yml", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                problems.append(f"{path.relative_to(ROOT)}: 失效链接 -> {target}")
    return problems


def check_encoding(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        if path.suffix.lower() not in {".md", ".txt", ".yml", ".yaml", ".py"}:
            continue
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{path.relative_to(ROOT)}: 含 UTF-8 BOM")
        if b"\r\n" in raw:
            problems.append(f"{path.relative_to(ROOT)}: 含 CRLF 换行")
    return problems


def check_leaks(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        if path.suffix.lower() not in {".md", ".txt", ".yml", ".yaml", ".py", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for match in ABS_PATH_RE.finditer(text):
            problems.append(f"{path.relative_to(ROOT)}: 疑似本地绝对路径 -> {match.group(0)}")
    return problems


def check_binaries(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        if path.suffix.lower() not in BINARY_SUFFIXES:
            continue
        rel = path.relative_to(ROOT)
        if rel in BINARY_ALLOWLIST:
            continue
        problems.append(f"{rel}: 不应提交的二进制")
    return problems


def count_pending(files: list[Path]) -> tuple[int, list[str]]:
    counts: list[tuple[str, int]] = []
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        n = text.count("待定")
        if n:
            counts.append((str(path.relative_to(ROOT)), n))
    hits = [f"{name}: {n} 处待定" for name, n in counts]
    return sum(n for _, n in counts), hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="把待定占位也算失败")
    args = parser.parse_args()

    files = iter_files()
    checks = {
        "链接": check_links(files),
        "编码": check_encoding(files),
        "本地路径": check_leaks(files),
        "二进制": check_binaries(files),
    }

    failed = False
    for name, problems in checks.items():
        if problems:
            failed = True
            print(f"[失败] {name}（{len(problems)} 项）")
            for item in problems:
                print(f"  - {item}")
        else:
            print(f"[通过] {name}")

    pending, hits = count_pending(files)
    print(f"[信息] 待定占位共 {pending} 处")
    for line in hits:
        print(f"  - {line}")
    if args.strict and pending:
        failed = True
        print("[失败] --strict：仍有待定占位")

    print(f"[信息] 共检查 {len(files)} 个文件")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
