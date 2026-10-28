r"""和游戏解码器（ELF 0x23E950）严格对应的 LZSS 编码器。

规则（和 tools\lz_patch.py 的 decode/rebuild 一致）：
  * 每 8 个 token 一个标志字节（低位先）：bit=0 明文 1 字节；bit=1 回溯 2 字节
  * 回溯：value = (b0<<4)|(b1>>4)（12 位），len = (b1&0xF)+1（1..16，nib=0 表示流结束）
  * 窗口 base：初始 0；**每个 token 之后**都走一遍 —— 未滑动时若
    (已输出 - base) >= 0x1000 则 base = 已输出 - 0x1000 并标记滑动；
    已滑动时 base += 本 token 的输出字节数（明文按 1 算）。
  编码时同样模拟这个 base，保证解出来一模一样。

用法（自检）：
    python tools\lz_encode.py --selftest
"""
import random
import sys
from array import array

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def decode(st, out_size):
    out = bytearray()
    p = 0
    n = len(st)
    mask = 0
    flags = 0
    base = 0
    sliding = False
    while p < n and len(out) < out_size:
        if mask == 0:
            flags = st[p]
            p += 1
            mask = 1
            if p >= n:
                break
        if (flags & mask) == 0:
            out.append(st[p])
            p += 1
            run = 1
        else:
            b0, b1 = st[p], st[p + 1]
            p += 2
            nib = b1 & 0xF
            if nib == 0:
                break
            value = (b0 << 4) | (b1 >> 4)
            run = nib + 1
            for k in range(run):
                idx = base + value + k
                if idx >= len(out) or idx < 0:
                    return bytes(out), False
                out.append(out[idx])
        if not sliding:
            if len(out) - base >= 4096:
                base = len(out) - 4096
                sliding = True
        else:
            base += run
        mask = (mask << 1) & 0xFF
    return bytes(out), len(out) >= out_size


def encode(data, max_len=16, window=4096, cap=64, lazy=True):
    """贪心 LZSS 编码（按上面那套窗口语义），返回压缩流 bytes。

    用 3 字节哈希链找匹配，速度够快（1MB 数据几秒）。
    """
    out = bytearray()          # 压缩流
    dec = bytearray()          # 已输出（解码视角）
    base = 0
    sliding = False
    mask = 0
    flags_at = 0
    chains = {}                # 3 字节前缀 -> [位置...]

    def new_group():
        nonlocal flags_at, mask
        flags_at = len(out)
        out.append(0)
        mask = 1

    def add_pos(pos):
        if pos + 3 <= len(dec):
            key = bytes(dec[pos:pos + 3])
            chains.setdefault(key, []).append(pos)

    def best_match(i, base):
        """在窗口里找 data[i:] 的最长匹配，返回 (len, value)。"""
        best_val, best_len = 0, 0
        limit = min(max_len, n - i)
        if limit < 3 or i + 3 > n:
            return 0, 0
        key = bytes(data[i:i + 3])
        lst = chains.get(key)
        if not lst:
            return 0, 0
        for cand in reversed(lst[-cap:]):
            if cand < base:
                continue
            room = len(dec) - cand
            if room < 3:
                continue
            lim = min(limit, room)
            L = 3
            while L < lim and dec[cand + L] == data[i + L]:
                L += 1
            if L > best_len:
                best_len, best_val = L, cand - base
            if best_len == limit:
                break
        return best_len, best_val

    i = 0
    n = len(data)
    while i < n:
        if mask == 0:
            new_group()
        best_len, best_val = best_match(i, base)
        # 惰性匹配：下一个位置能匹配更长就先出明文
        if lazy and 3 <= best_len < max_len and i + 1 < n:
            nxt_len, _nxt_val = best_match(i + 1, base)
            if nxt_len > best_len:
                best_len = 0
        if best_len >= 3:
            value = best_val
            nib = best_len - 1
            out.append((value >> 4) & 0xFF)
            out.append(((value & 0xF) << 4) | nib)
            out[flags_at] |= mask
            for k in range(best_len):
                dec.append(dec[base + value + k])
                add_pos(len(dec) - 3)
            run = best_len
            i += best_len
        else:
            out.append(data[i])
            dec.append(data[i])
            add_pos(len(dec) - 3)
            i += 1
            run = 1
        if not sliding:
            if len(dec) - base >= 4096:
                base = len(dec) - 4096
                sliding = True
        else:
            base += run
        mask = (mask << 1) & 0xFF
    if mask == 0:
        new_group()
    out.append(0x00)           # 结束标记：回溯 token，nib=0
    out.append(0x00)
    out[flags_at] |= mask
    return bytes(out)


def reencode(st, out_size, target):
    """保留原 token 结构重建流：明文字节取 target 的值；回溯若"源和目标的副本
    不一致"（冲突）就拆成明文。返回新流（解出来正好等于 target）。"""
    new = bytearray()
    dec = bytearray()
    p = 0
    n = len(st)
    smask = 0
    flags = 0
    omask = 0
    flags_at = -1
    conflicts = 0

    def put_literal(v):
        nonlocal omask, flags_at
        if omask == 0:
            flags_at = len(new)
            new.append(0)
            omask = 1
        new.append(v)
        new[flags_at] |= omask
        dec.append(v)
        omask = (omask << 1) & 0xFF

    def put_match(value, run):
        nonlocal omask, flags_at
        if omask == 0:
            flags_at = len(new)
            new.append(0)
            omask = 1
        new.append((value >> 4) & 0xFF)
        new.append(((value & 0xF) << 4) | (run - 1))
        new[flags_at] |= omask
        src = dec2base() + value
        for k in range(run):
            dec.append(dec[src + k])
        omask = (omask << 1) & 0xFF

    def dec2base():
        return max(0, len(dec) - 4096)

    while p < n and len(dec) < out_size:
        if smask == 0:
            flags = st[p]
            p += 1
            smask = 1
            if p >= n:
                break
        bit = flags & smask
        if bit == 0:
            i = len(dec)
            p += 1
            put_literal(target[i])
        else:
            b0, b1 = st[p], st[p + 1]
            p += 2
            nib = b1 & 0xF
            if nib == 0:
                break
            value = (b0 << 4) | (b1 >> 4)
            run = nib + 1
            base = dec2base()
            s = base + value
            dst = bytes(target[len(dec):len(dec) + run])
            seg = bytes(target[s:s + run]) if s + run <= len(dec) else None
            if seg is not None and seg == dst:
                put_match(value, run)
            else:
                conflicts += 1
                for k in range(run):
                    put_literal(target[len(dec)])
        smask = (smask << 1) & 0xFF
    return bytes(new), conflicts, bytes(dec)


def match_table(data, max_len=16, window=4096, cap=256, keep=8192):
    """预计算每个位置的最长匹配。

    返回 (ml, mv)：ml[i] = 最长匹配长度（0 或 3..max_len），
    mv[i] = 对应的 12 位 value（窗口相对偏移）。给定 value 对**任何**
    L <= ml[i] 都成立（匹配是从 3 开始逐字节比出来的）。

    窗口语义与 decode 完全一致：base = max(0, i - window)。
    keep 是哈希链每个前缀最多保留的候选数（防内存爆炸）。
    """
    n = len(data)
    ml = bytearray(n)
    mv = array("H", bytes(2 * n))
    chains = {}
    for i in range(n - 2):
        a = data[i]
        key = (a << 16) | (data[i + 1] << 8) | data[i + 2]
        lst = chains.get(key)
        if lst is None:
            chains[key] = [i]
            continue
        base = i - window
        if base < 0:
            base = 0
        limit = max_len
        if i + limit > n:
            limit = n - i
        best = 0
        bestv = 0
        lo = len(lst) - cap
        if lo < 0:
            lo = 0
        for t in range(len(lst) - 1, lo - 1, -1):
            cand = lst[t]
            if cand < base:            # 链是递增的，往前的更小
                break
            L = 3
            while L < limit and data[cand + L] == data[i + L]:
                L += 1
            if L > best:
                best = L
                bestv = cand - base
                if best == limit:
                    break
        if best >= 3:
            ml[i] = best
            mv[i] = bestv
        lst.append(i)
        if len(lst) > keep:
            del lst[:len(lst) - keep]
    return ml, mv


def encode_dp(data, max_len=16, window=4096, cap=256, keep=8192,
              cost_lit=9, cost_match=17):
    """最优解析（DP）编码，压缩率比贪心好，代价是慢。

    代价模型（1/8 字节为单位）：明文 8+1 bit、回溯 16+1 bit。近似"总长度 =
    数据字节 + 标志字节"，误差最多 1 字节。放宽 cap 时能拿到更深窗口内的匹配。
    结果与游戏的解码器**严格**一致（用 decode 回解验证）。
    """
    n = len(data)
    ml, mv = match_table(data, max_len, window, cap, keep)
    INF = 1 << 30
    cost = [INF] * (n + 1)
    cost[0] = 0
    for i in range(n):
        c = cost[i]
        if c == INF:
            continue
        c1 = c + cost_lit
        if c1 < cost[i + 1]:
            cost[i + 1] = c1
        Lmax = ml[i]
        if Lmax >= 3:
            room = n - i
            if Lmax > room:
                Lmax = room
            cm = c + cost_match
            for L in range(3, Lmax + 1):
                if cm < cost[i + L]:
                    cost[i + L] = cm
    # 从末尾回溯出 token 序列（1 = 明文，3..16 = 回溯长度）
    steps = []
    i = n
    while i > 0:
        if cost[i - 1] + cost_lit == cost[i]:
            steps.append(1)
            i -= 1
            continue
        top = max_len if i >= max_len else i
        for L in range(top, 2, -1):
            j = i - L
            if ml[j] >= L and cost[j] + cost_match == cost[i]:
                steps.append(L)
                i = j
                break
        else:
            raise RuntimeError("DP 回溯失败 @%d" % i)
    steps.reverse()
    # 按 token 序列生成压缩流
    out = bytearray()
    mask = 0
    flags_at = 0
    pos = 0
    for s in steps:
        if mask == 0:
            flags_at = len(out)
            out.append(0)
            mask = 1
        if s == 1:
            out.append(data[pos])
            pos += 1
        else:
            v = mv[pos]
            out.append((v >> 4) & 0xFF)
            out.append(((v & 0xF) << 4) | (s - 1))
            out[flags_at] |= mask
            pos += s
        mask = (mask << 1) & 0xFF
    if mask == 0:
        flags_at = len(out)
        out.append(0)
        mask = 1
    out.append(0x00)               # 结束标记
    out.append(0x00)
    out[flags_at] |= mask
    return bytes(out)


def selftest():
    rnd = random.Random(7)
    # 先测 DP 编码器（小样本，跑得快）
    for trial in range(3):
        if trial == 0:
            data = (b"ABCDEFGH" * 900)[:4000]
        elif trial == 1:
            data = bytes(range(256)) * 12
        else:
            data = bytes(rnd.randrange(0x83, 0x9F) for _ in range(2500))
        st_g = encode(data, cap=64)
        st_d = encode_dp(data, cap=64)
        gd, okd = decode(st_d, len(data))
        gg, okg = decode(st_g, len(data))
        print("DP 试 %d: 原 %d  贪心 %d  DP %d  省 %d  回解一致=%s/%s"
              % (trial, len(data), len(st_g), len(st_d),
                 len(st_g) - len(st_d), gg == data, gd == data))
        assert gd == data and gg == data, "DP 自检失败"
    for trial in range(6):
        if trial == 0:
            data = (b"ABCDEFGH" * 900)[:4000]
        elif trial == 1:
            data = bytes(range(256)) * 20
        else:
            data = bytes(rnd.randrange(0x83, 0x9F) for _ in range(3000))
        st = encode(data)
        got, ok = decode(st, len(data))
        print("试 %d: 原 %d -> 压 %d  解码一致=%s"
              % (trial, len(data), len(st), got == data))
        assert got == data, "自检失败"


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
