"""自测：fontcov 三态判定 + 与索引库 / 码表的一致性。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import env, codec, fontcov  # noqa: E402


def main() -> int:
    t0 = time.time()
    fc = fontcov.coverage()
    print("覆盖库装载 %.2fs  码位数=%d  包数=%d"
          % (time.time() - t0, len(fc._by_code), len(fc.pkg_meta)))
    ct = codec.table()
    fails = []

    def check(label, ch, want, code=None):
        v = fc.diagnose(ch, code=code, codes_of=ct.codes_of)
        tag = "OK " if v.state == want else "FAIL"
        if v.state != want:
            fails.append(label)
        print("  [%s] %s %s  code=%s 包=%d/%d  %s"
              % (tag, label, v.short(),
                 "-" if v.code is None else "0x%04X" % v.code,
                 v.pkg_count, len(env.FONT_PKGS), v.reason))
        for n in v.notes:
            print("        · %s" % n)
        return v

    print("\n== 常用字（应 PASS）==")
    for ch in "什么何优莱科拉利安":
        check("常用", ch, fontcov.PASS)

    print("\n== 无码位字（应 BLOCK）==")
    for ch in "黄昏鑫":
        check("无码位", ch, fontcov.BLOCK)

    print("\n== ASCII / 半角假名（不得 BLOCK）==")
    for ch in "AQVxyz.?!-0":
        v = check("ascii", ch, None) if False else fc.diagnose(ch, codes_of=ct.codes_of)
        assert v.state != fontcov.BLOCK, "%r 被误判 BLOCK" % ch
        print("  [OK ] ascii %r -> %s  path=%s  %s"
              % (ch, v.state, v.path, v.reason))
    for ch in "ｱﾀﾞ":
        v = fc.diagnose(ch, codes_of=ct.codes_of)
        assert v.state == fontcov.WARN, "%r 应 WARN，实际 %s" % (ch, v.state)
        print("  [OK ] kana %r -> %s  path=%s  %s"
              % (ch, v.state, v.path, v.reason))

    print("\n== 空白（应 PASS）==")
    for ch in " \u3000":
        check("空白", ch, fontcov.PASS)

    print("\n== 覆盖率分布：全库 2172 字逐字判定 ==")
    t1 = time.time()
    dist = {fontcov.PASS: 0, fontcov.WARN: 0, fontcov.BLOCK: 0}
    warn_samples, block_samples = [], []
    for ch in sorted(ct.char2codes.keys()):
        if ch.isspace():
            dist[fontcov.PASS] += 1
            continue
        v = fc.diagnose(ch, codes_of=ct.codes_of)
        dist[v.state] += 1
        if v.state == fontcov.WARN and len(warn_samples) < 15:
            warn_samples.append(v)
        if v.state == fontcov.BLOCK and len(block_samples) < 15:
            block_samples.append(v)
    print("  用 %.2fs  PASS=%d WARN=%d BLOCK=%d  合计=%d"
          % (time.time() - t1, dist[fontcov.PASS], dist[fontcov.WARN],
             dist[fontcov.BLOCK], sum(dist.values())))
    print("  WARN 样本：")
    for v in warn_samples:
        print("     %s  包=%d 缺=%s" % (v.short(), v.pkg_count, v.missing))
    print("  BLOCK 样本：")
    for v in block_samples:
        print("     %s" % v.short())

    print("\n== 逐包「仅 N 个包有字形」统计 ==")
    hist = {}
    for ch in ct.char2codes:
        if ch.isspace():
            continue
        v = fc.diagnose(ch, codes_of=ct.codes_of)
        hist[v.pkg_count] = hist.get(v.pkg_count, 0) + 1
    for k in sorted(hist):
        print("   有字形包数=%2d -> %d 字" % (k, hist[k]))

    print("\n== 文本级判定 ==")
    for txt in ("什么？", "优莱卡是科拉利安", "黄色的黄昏", "甲　乙"):
        state, verdicts, notes = fc.diagnose_text(txt, codes_of=ct.codes_of)
        print("  %r -> %s  (%d 字)" % (txt, state, len(verdicts)))
        for n in notes:
            print("      · %s" % n)

    print("\n失败项:", fails if fails else "无")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
