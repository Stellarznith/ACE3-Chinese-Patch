"""自测：安全写盘引擎（内存构建 + 独立回读 + 整盘白名单差分）。

**不使用**任何写盘函数的自证结论：每个 PASS 都靠
  ① 重新打开 dst、重新解析 TOC/资源/块 的独立回读，以及
  ② 与父盘逐字节整盘差分（白名单外必须 0 字节变化）
两项外部证据共同支撑。

用例：
  1. 非压缩单槽（4004643 slot9）变短：什么？→何？
  2. 非压缩单槽加长（同上，转紧凑重排）
  3. 同资源多槽批量（4004643 slot9/10/12）
  4. 含 book/color 标签槽（6020 slot131）
  5. 压缩资源（701009）单槽改写 -> 整条重压 -> 独立解压回读
  6. 压缩资源多槽批量
  7. 冲突检测：同槽重复修改、块内空间不足
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import env, slots, codec, relayout, writer  # noqa: E402

SRC = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
OUT = os.path.join(env.WORK_DIR, "iso_out")
os.makedirs(OUT, exist_ok=True)
TOC = slots.cached_toc(SRC)


def mk(eid, tag, k, slot, new_plain, ignore_old=False):
    mem = slots.load_resource(SRC, eid, TOC).member(tag)
    blk = mem.block(k)
    old = slots.slot_payload(mem.data, blk, slot)
    seg = codec.Segment(old)
    data, er = codec.rebuild_with_plain(seg, new_plain)
    assert not er.missing, "缺字：%s" % sorted(er.missing)
    return writer.Edit(eid=eid, tag=tag, k=k, slot=slot, new_payload=data,
                       old_payload=(b"" if ignore_old else old),
                       label=codec.Segment(old).plain[:20])


def run(name, edits, expect_block=False):
    print("=" * 78)
    print("用例：", name)
    verdicts = writer.check(SRC, edits)
    for v in verdicts:
        print("  [%s] eid=%d tag=%d slot=%d  %dB->%dB  comp=%s  cap=%d  %s"
              % (v.state, v.edit.eid, v.edit.tag, v.edit.slot,
                 v.detail.get("old_len", 0), len(v.edit.new_payload),
                 v.compressed, v.capacity, v.reason))
    blocked = [v for v in verdicts if v.state == writer.BLOCK]
    if expect_block:
        assert blocked, "本应 BLOCK 却全部放行"
        print("  ✓ 按预期阻断：%s" % blocked[0].reason)
        return None
    assert not blocked, "意外阻断：%s" % blocked[0].reason

    builds = writer.build(SRC, edits, TOC)
    for eid, b in builds.items():
        print("  build eid=%d  %d 字节  edits=%d  %s"
              % (eid, len(b.new_raw), len(b.edits), b.notes))
    dst = os.path.join(OUT, "t_%05d.iso" % (abs(hash(name)) % 100000))
    if os.path.exists(dst):
        os.remove(dst)
    info = writer.write(SRC, builds, dst, progress=lambda m: print("    ·", m))
    print("  写盘完成 %s  %.1fs  dst_md5=%s"
          % (env.human_size(info["size"]), info["elapsed"], info["dst_md5"]))

    rv = writer.verify(dst, edits, TOC)
    print("  独立回读：%s（%d 项）" % ("PASS" if rv["ok"] else "FAIL", len(rv["items"])))
    for it in rv["items"]:
        if not it["ok"]:
            print("    ✗ eid=%d slot=%d %s" % (it["eid"], it["slot"], it["error"]))
    assert rv["ok"], "独立回读失败"

    df = writer.diff_whitelist(SRC, dst, builds)
    print("  整盘差分：白名单外变化 %d 字节（%s）"
          % (df["outside"], "PASS" if df["ok"] else "FAIL"))
    if df["bad"]:
        print("    越界区间：", df["bad"][:5])
    assert df["ok"], "白名单外有改动"

    # 保留产物供人工核对，删除以省空间
    os.remove(dst)
    return info


def main():
    run("1 非压缩单槽变短 什么？→何？", [mk(4004643, 0, 0, 9, "何？")])
    run("2 非压缩单槽加长", [mk(4004643, 0, 0, 9, "什么？什么？什么？")])
    run("3 同资源多槽批量",
        [mk(4004643, 0, 0, 9, "何？"), mk(4004643, 0, 0, 10, "什么？"),
         mk(4004643, 0, 0, 12, "怎么了？")])
    run("4 含 book 标签槽", [mk(6020, -1, 13, 131, "甲甲甲甲甲甲甲甲甲甲")])
    run("5 压缩资源单槽", [mk(701009, -1, 1, 0, "甲")])
    run("6 压缩资源多槽",
        [mk(701009, -1, 1, 0, "甲甲"), mk(701009, -1, 1, 1, "乙乙")])
    run("7 空间不足必须 BLOCK",
        [mk(4004643, 0, 0, 9, "什" * 200)], expect_block=True)
    # 串位防护
    bad = mk(4004643, 0, 0, 9, "何？")
    bad.old_payload = b"\x00wrong"
    run("8 原字节指纹不符必须 BLOCK", [bad], expect_block=True)
    # 重复槽
    a = mk(4004643, 0, 0, 9, "何？")
    b = mk(4004643, 0, 0, 9, "什么？")
    run("9 同槽重复修改（后者覆盖前者）", [a, b])
    print("\n全部用例通过")


if __name__ == "__main__":
    sys.exit(main())
