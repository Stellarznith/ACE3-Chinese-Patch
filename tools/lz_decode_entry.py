r"""解包 DATA.BIN 里的打包条目（magic 00 01 00 00 + BE32 解压长度 + LZSS）。

用法：
    python tools\lz_decode_entry.py <iso> <条目号> [--out 输出文件] [--hex 偏移 长度]
"""
import mmap
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SECTOR, DATA_LBA, COUNT = 2048, 2117, 10344


def lzss_decode(buf, start, out_size, hard_limit=1 << 26):
    out = bytearray()
    p = start
    n = len(buf)
    mask = 0
    flags = 0
    base = 0
    sliding = False
    ended = False
    while p < n and len(out) < out_size:
        if mask == 0:
            flags = buf[p]
            p += 1
            mask = 1
            if p >= n:
                break
        if (flags & mask) == 0:
            out.append(buf[p])
            p += 1
            run = 1
        else:
            if p + 1 >= n:
                break
            b0, b1 = buf[p], buf[p + 1]
            p += 2
            nib = b1 & 0xF
            if nib == 0:
                ended = True
                break
            value = (b0 << 4) | (b1 >> 4)
            length = nib + 1
            for k in range(length):
                idx = base + value + k
                if idx >= len(out) or idx < 0:
                    return bytes(out), False, p
                out.append(out[idx])
            run = length
        if not sliding:
            if len(out) - base >= 4096:
                base = len(out) - 4096
                sliding = True
        else:
            base += run
        mask = (mask << 1) & 0xFF
        if len(out) > hard_limit:
            break
    return bytes(out), ended, p


def entry_span(d, base, want):
    head = d[base + 0x20:base + 0x20 + COUNT * 16]
    for i in range(COUNT):
        a, sz, off, e = struct.unpack_from("<IIII", head, i * 16)
        if e == want:
            return off, sz
    return None


def main():
    iso = sys.argv[1]
    ent = int(sys.argv[2], 0)
    out_path = None
    args = sys.argv[3:]
    for i, a in enumerate(args):
        if a == "--out":
            out_path = args[i + 1]
    f = open(iso, "rb")
    d = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    base = DATA_LBA * SECTOR
    off, sz = entry_span(d, base, ent)
    k = base + off
    size = struct.unpack_from(">I", d, k + 4)[0]
    print("条目 %d @%#x 文件大小=%d 声明解压长度=%d 头=%s"
          % (ent, k, sz, size, d[k:k + 12].hex()))
    out, ended, p = lzss_decode(d, k + 12, size)
    print("解出 %d 字节，结束标记=%s，消费压缩 %d 字节 (文件里剩 %d)"
          % (len(out), ended, p - (k + 12), sz - 12 - (p - (k + 12))))
    for m in (b"VMD\x00", b"CLM\x00", b"FNM\x00", b"BND\x00", b"END\x00"):
        c = out.count(m)
        if c:
            print("  含 %s × %d" % (m.decode("latin-1"), c))
    jp = "イクスブラウ".encode("cp932")
    if jp in out:
        j = out.find(jp)
        print("  ★含机体名 @%#x" % j)
        print("     上下文: %r" % out[max(0, j - 32):j + 64])
    print("  开头 64B: %s" % out[:64].hex())
    if out_path:
        open(out_path, "wb").write(out)
        print("  写出 -> %s" % out_path)


if __name__ == "__main__":
    main()
