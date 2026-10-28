#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R211（只读）：把「逐字码覆盖（--override）」**从权威登记表推导出来**，并做交叉校验。

背景（R210 §10 的断链）：R210 总交接里那串 --override 只写了尾巴，全串从没落过盘。
本工具把三类权威来源合并，推导出**完整**的 char->code 覆盖，并输出：
  * override 串（可直接复制给 _r210_worklist / _r210_rewrite_check / _r208_untrans）
  * 「按本 override + load_cur，目标包**仍然画不出**的字」= 必须改写/腾字的字
  * 与前任记录串（R209 §10 / _r208_untrans 内置默认）的差集

权威来源：
  1) tools/_r206_memfix.load_cur()  —— tmp_r203/map_r203.tsv + tmp_r205/newcode_map{,2}.tsv
  2) tmp_r205/newcode_map.tsv       —— R205「零位移补 45 字」
  3) tmp_r205/newcode_map2.tsv      —— R208/R209/R210 新增码（92B1..92ED 等）
  4) R206 腾字（**不在任何 tsv 里**，只记在 docs 全史 R206 章 / R206 总交接）：
       8ADB<-呜  8EDD<-邪  8FA4<-默  8FD3<-叔  8A95<-娃  8764<-肃  878E<-嘲
     （R206 原文：把"任何文本都没用到"的 7 个码 8764 878E 8A95(厢) 8ADB(咱)
       8EDD(畏) 8FA4(绪) 8FD3(胎) 改画为 呜 邪 默 叔 娃 肃 嘲；不动 FontMap，只重画该格。）

用法：python -X utf8 tools/_r211_override.py --iso <iso> [--pkg 1200000]
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ACE3_ROOT") or os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from _r104_glyphfix2 import Pkg          # noqa: E402
from _r206_memfix import load_cur        # noqa: E402

# ---------------------------------------------------------------------------
# 腾字改画表：这些码的**字形含义已被改写**，旧字必须判"画不出"。
# 来源：docs 全史 R206 章（原文见 docstring 第 4 条）+ R210 章（腾字一/二轮）。
#   R206：8ADB<-呜 8EDD<-邪 8FA4<-默 8FD3<-叔 8A95<-娃 8764<-肃 878E<-嘲
#   R210 一轮：8199/9175/878D 的区间被重指走（这三个码在 f125 已不在 FontMap）
#   R210 二轮：92BA/92C0 的区间被重指走（同上）
# ⇒ 只有 R206 那 7 个是"同码换字"，必须显式纠正；其余是"码已消失"，天然判不出。
REPAINTED = {0x8ADB: "呜", 0x8EDD: "邪", 0x8FA4: "默", 0x8FD3: "叔",
             0x8A95: "娃", 0x8764: "肃", 0x878E: "嘲"}

REG_FILES = ["newcode_map.tsv", "newcode_map2.tsv"]


def load_registry():
    """返回 (code->char 全部登记, char->[code,...])，只取 1 个字符的条目。"""
    c2h = {}
    for nm in REG_FILES:
        p = None
        for cand in (os.path.join(_HERE, "data", nm), os.path.join(ROOT, "tmp_r205", nm)):
            if os.path.exists(cand):
                p = cand
                break
        if p is None:
            continue
        for line in open(p, encoding="utf-8"):
            if line.startswith("#") or line.startswith("code"):
                continue
            q = line.rstrip("\r\n").split("\t")
            if len(q) >= 2 and len(q[1]) == 1:
                try:
                    c = int(q[0], 16)
                except ValueError:
                    continue
                c2h.setdefault(c, q[1])
    return c2h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True)
    ap.add_argument("--pkg", type=int, default=1200000)
    ap.add_argument("--out", default=os.path.join(ROOT, "tmp_r211", "override_1200000.txt"))
    a = ap.parse_args()

    c2h_cur, h2c_cur = load_cur()
    reg = load_registry()

    P = Pkg(a.iso, a.pkg)
    # 「可画码」：FontMap 里有记录且 ink>0；再加三个"本来就是空白"的码
    #   0x8140 全角空格（项目口径：必须用它，不许转半角）
    #   0x816D 空白码、0x20 半角空格（无墨是正常表现）
    draw = set(c for c, i in P.code2idx.items() if P.ink(i) > 0) | {0x8140, 0x816D, 0x20}

    # ---- 1) 统一的 code -> char 表（load_cur ∪ 登记表），再用"腾字改画表"纠正 ----
    code2char = dict(c2h_cur)
    for c, ch in reg.items():
        code2char.setdefault(c, ch)
    forced = {c: ch for c, ch in REPAINTED.items()}
    for c, ch in forced.items():
        code2char[c] = ch          # 覆盖旧字（同一码在旧表里是别的字）
    # 反向表
    ch2codes = {}
    for c, ch in code2char.items():
        ch2codes.setdefault(ch, set()).add(c)

    # ---- 2) 推导 override：每个字取一个"本包真画得出"的码（多个取最大码，确定可复现） ----
    override, conflict = {}, []
    for ch, codes in ch2codes.items():
        ok = sorted(c for c in codes if c in draw)
        if not ok:
            continue
        if len(ok) > 1:
            conflict.append((ch, ["%04X" % x for x in ok]))
        override[ch] = max(ok)

    # ---- 3) 判"仍然画不出"：字在统一表里有登记，但没有任何可画码 ----
    all_chars = set(ch2codes.keys()) | set(h2c_cur.keys())
    undraw = sorted(ch for ch in all_chars if ch not in override and ord(ch) >= 0x80)
    undraw_ascii = sorted(ch for ch in all_chars if ch not in override and ord(ch) < 0x80)
    override["\u3000"] = 0x8140     # 项目口径：全角空格固定 0x8140（不许用 0x816D 顶替）

    # ★override 串里**只放全角(ord>=0x80)字**：
    #   写入器 enc() 对 <0x80 直接走单字节、从不查 override；而且 '=' 自己作为键会把
    #   `k, v = kv.split("=")` 解析打爆（实测 ValueError）。ASCII 可画性另行单独核查。
    ov_wide = {ch: c for ch, c in override.items() if ord(ch) >= 0x80}
    s = ",".join("%s=%04X" % (ch, ov_wide[ch]) for ch in sorted(ov_wide))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(s + "\n")
    outdir = os.path.dirname(a.out) or "."
    with open(os.path.join(outdir, "undrawable_%d.txt" % a.pkg), "w", encoding="utf-8") as f:
        f.write("".join(undraw) + "\n")
    with open(os.path.join(outdir, "undrawable_ascii_%d.txt" % a.pkg), "w", encoding="utf-8") as f:
        f.write("".join(undraw_ascii) + "\n")

    print("== R211 override 推导（目标包 %d）==" % a.pkg)
    print("该包可画码          : %d" % len(draw))
    print("登记表条目          : %d（其中可画的 %d）" % (len(reg), len([c for c in reg if c in draw])))
    print("推导出 override 条目 : %d" % len(override))
    print("仍画不出的汉字/全角 : %d  ->  %s" % (len(undraw), "".join(undraw)[:400]))
    print("仍画不出的半角字符 : %d  ->  %s" % (len(undraw_ascii), "".join(undraw_ascii)))
    if conflict:
        print("一个字有多个可画码（取最大）: %d 例: %s" % (len(conflict), conflict[:10]))
    print("写入: %s（%d 字节）" % (a.out, len(s)))

    # 与前任记录串对比
    prev_dir = os.path.join(ROOT, "tmp_r211")
    # R209 §10 记录串
    r209 = ("呜=8ADB,邪=8EDD,默=8FA4,叔=8FD3,娃=8A95,肃=8764,嘲=878E,"
            "琪=92B7,鳞=92B8,埃=92B9,六=92BA,哨=92BB,遁=92BC,镀=92BD,仓=92BE,"
            "逊=92BF,汀=92C0,邮=92C1,吒=92C2,枚=92C3,涩=92C4,谷=92C5,蛇=92C6,"
            "蝗=92C7,岑=92C8,堡=92C9,洪=92CA,毒=92CB,钟=92CC,箱=92CD,闸=92CE,"
            "诶=92CF,舷=92D0,搅=92D1,袋=92D2,秩=92D3,骄=92D4,傲=92D5,健=92D6,"
            "伪=92D7,朋=92D8,沌=92D9,娩=92DA,腐=92DB,朽=92DC,透=92DD,爬=92DE,"
            "晚=92DF,耻=92E0,辱=92E1,赝=92E2,橘=92E3,九=92B1,疫=92B2,苗=92B3,"
            "啥=92B4,拳=92B5,宏=92B6,浦=92E4")
    pv = {}
    for kv in r209.split(","):
        if "=" in kv:
            k, v = kv.split("=")
            pv[k] = int(v, 16)
    only_prev = {k: hex(v) for k, v in pv.items() if k not in override}
    only_new = {k: hex(v) for k, v in override.items() if k not in pv}
    diff_val = {k: (hex(pv[k]), hex(override[k])) for k in pv if k in override and pv[k] != override[k]}
    print("-- 与 R209 §10 串对比 --")
    print("  只在 R209 串里 : %s" % only_prev)
    print("  只在本推导里   : %s" % only_new)
    print("  同字不同码     : %s" % diff_val)
    return 0


if __name__ == "__main__":
    sys.exit(main())
