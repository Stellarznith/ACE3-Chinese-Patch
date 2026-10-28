"""一次性自测：core.codec 的编解码往返与标签保护。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import codec  # noqa: E402


def show(seg: codec.Segment, label):
    print("--", label)
    print("   plain :", repr(seg.plain))
    print("   tags  :", [t.decode("latin-1") for t in seg.tags])
    print("   codes :", ["%04X" % c for c in seg.codes])
    print("   unk   :", ["%04X" % c for c in seg.unknown])


def main():
    tb = codec.table()
    print(tb.note)
    print("何 ->", ["%04X" % c for c in tb.codes_of("何")])
    print("黄 ->", ["%04X" % c for c in tb.codes_of("黄")])
    print("　 ->", ["%04X" % c for c in tb.codes_of("\u3000")])

    # 1) 真实槽：eid=4004643 (0,0) slot9 = 「什么？」
    raw = b"<operator(23010,1)><speed(2)>\x89|\x89W\x81H"
    seg = codec.Segment(raw)
    show(seg, "4004643 slot9 现盘")
    r = codec.encode_text("何？", seg.codes)
    print("   重新编码「何？」->", r.data.hex(), "missing:", r.missing)
    assert r.data == b"\x89\xa8\x81H", r.data.hex()

    # 2) 含 book/color 的句子：正文替换后标签必须字节不变
    raw2 = (b"<on()><sp(20)><book(22)>\x8b{\x8eS\x8b}\x8b\x96<endbook()>"
            b"\x8e\xea\x89j\x8cp\x91e\x8c\xb4\x81B\n\x8c\xba\x8a\xc8\x90\xab"
            b"\x89\xab\x8c\xb9\x89g\x89\x84\x89\x8b\x8e\xea\x89X\x8b\x8d\x81B")
    seg2 = codec.Segment(raw2)
    show(seg2, "6020 slot131 现盘")
    sig0 = codec.tag_signature(seg2.tags)
    data, res = codec.rebuild_with_plain(seg2, "测试一下标签保护")
    seg3 = codec.Segment(data)
    sig1 = codec.tag_signature(seg3.tags)
    print("   rebuilt hex:", data.hex()[:80], "...")
    print("   回读 plain:", repr(seg3.plain))
    print("   标签指纹一致:", sig0 == sig1)
    print("   missing:", res.missing)
    assert sig0 == sig1

    # 3) 缺字：黄
    r3 = codec.encode_text("黄昏", ())
    print("   「黄昏」missing:", r3.missing, "data:", r3.data.hex())
    assert "黄" in r3.missing
    assert codec.table().codes_of("黄") == ()

    # 4) 全角空格与换行
    r4 = codec.encode_text("甲\u3000乙\n丙", (0x816D,))
    print("   「甲　乙／丙」->", r4.data.hex(), "used:", r4.used_codes)

    # 5) 逐字符 diff
    d = codec.diff_chars("何？", "什么？")
    print("   diff:", d)

    print("\nALL OK")


if __name__ == "__main__":
    main()
