"""Read/write the front-end font atlas (tag=2000) of A.C.E.3.

The 262144-byte image is stored in a scrambled order.  Solved mapping:

    nibble index n (0..524287) -> atlas pixel (x, y)
      u = n >> 6, rem = n & 63, p = rem & 1, i = rem >> 1
      k = 4*u3 + 8*u4 + 16*u5 + u9 + 2*u10          (uN = bit N of u)
      y = 2p + 16*u0 + 32*u1 + 64*u2 + u6 + 4*u7 + 8*u8 + 128*u11 + 256*u12
      c = p ^ u7
      x = 32*k + BASE[i & 15] + 4*((i >> 4) ^ c)
    BASE = [0, 8, 16, 24, 1, 9, 17, 25, 2, 10, 18, 26, 3, 11, 19, 27]

Verified against a PCSX2 texture dump: 99.998% of 524288 pixels agree.
"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

W, H = 1024, 512
BASE = [0, 8, 16, 24, 1, 9, 17, 25, 2, 10, 18, 26, 3, 11, 19, 27]

_cache = {}


def tables_for(w, h):
    """Return (nibble index of each pixel, pixel index of each nibble).

    地址位与坐标位是一一对应的置换；贴图变高时多出来的那一位就是 y 的最高位
    （1024 高 = 512 高的那套排列 + 最高位接到 y9），所以前一半完全不变。
    """
    key = (w, h)
    if key in _cache:
        return _cache[key]
    n = np.arange(w * h, dtype=np.int64)
    u = n >> 6
    rem = n & 63
    p = rem & 1
    i = rem >> 1
    if (w, h) == (512, 512):
        # ★R194：1200002/1200003 的 512×512（128KB）小图集单独一套位序。
        #   推导：GS 4bpp 的地址位字段里"页列"少一页（1024 宽 3 位 → 512 宽 2 位）后，
        #   其后的字段整体下移一位。地面真值 = 原版 1200002 的图集 + 原版 FontMap：
        #   本式解出来是干净的假名/ASCII/符号/汉字图集，且
        #   "record 矩形内墨水 = 100.00% / 空 rect 2/698"，与已验证的大包同水准（100.00%/3/1393）。
        #   旧公式（k 五位、y 高取 u11/u12）**不是双射**（唯一值只 131584/262144，43% 往返不一致），
        #   会把图集 y≥256 的字形整片写丢 —— 这就是"512×512 y≥256 映射未解"的真身。
        y = (2 * p + 16 * ((u >> 0) & 1) + 32 * ((u >> 1) & 1) + 64 * ((u >> 2) & 1)
             + ((u >> 5) & 1) + 4 * ((u >> 6) & 1) + 8 * ((u >> 7) & 1)
             + 128 * ((u >> 10) & 1) + 256 * ((u >> 11) & 1))
        c = p ^ ((u >> 6) & 1)
        low = np.array(BASE, dtype=np.int64)[i & 15] + 4 * ((i >> 4) ^ c)
        x = (low + 32 * ((u >> 8) & 1) + 64 * ((u >> 9) & 1)
             + 128 * ((u >> 3) & 1) + 256 * ((u >> 4) & 1))
    else:
        y = (2 * p + 16 * ((u >> 0) & 1) + 32 * ((u >> 1) & 1) + 64 * ((u >> 2) & 1)
             + ((u >> 6) & 1) + 4 * ((u >> 7) & 1) + 8 * ((u >> 8) & 1)
             + 128 * ((u >> 11) & 1) + 256 * ((u >> 12) & 1))
        if h > 512:
            y = y + 512 * ((u >> 13) & 1)
        k = (4 * ((u >> 3) & 1) + 8 * ((u >> 4) & 1) + 16 * ((u >> 5) & 1)
             + ((u >> 9) & 1) + 2 * ((u >> 10) & 1))
        c = p ^ ((u >> 7) & 1)
        low = np.array(BASE, dtype=np.int64)[i & 15] + 4 * ((i >> 4) ^ c)
        x = 32 * k + low
    inv = np.zeros(w * h, dtype=np.int32)
    inv[y * w + x] = n
    pix = (y * w + x).astype(np.int32)
    # ★R194 自检：置换必须是**双射**。不是双射就会静默丢字形（本工程踩过），
    #   宁可当场报错，也不许产出"看起来能跑但丢半边图集"的盘。
    if inv.min() < 0 or len(np.unique(inv)) != w * h or len(np.unique(pix)) != w * h:
        raise ValueError(
            "front_atlas: %dx%d 的 file<->pixel 置换不是双射（唯一 inv=%d/%d, pix=%d/%d）"
            % (w, h, len(np.unique(inv)), w * h, len(np.unique(pix)), w * h))
    _cache[key] = (inv, pix)
    return inv, pix


def tables():
    """Return (nibble index of each pixel, pixel index of each nibble)."""
    return tables_for(W, H)


def decode(image: bytes, h=None, w=None) -> np.ndarray:
    """打乱的字节 -> h x w 的调色板索引数组（h 默认 512，w 默认 1024）。

    ★（R160）w 可指定：1200002/1200003 是 512×512（128KB）的小图集。
    """
    h = H if h is None else h
    w = W if w is None else w
    inv, _pix = tables_for(w, h)
    raw = np.frombuffer(image[:w * h // 2], dtype=np.uint8)
    nib = np.empty(w * h, dtype=np.uint8)
    nib[0::2] = raw & 15
    nib[1::2] = raw >> 4
    return nib[inv].reshape(h, w)


def encode(atlas: np.ndarray, h=None, w=None) -> bytes:
    """h x w 索引数组 -> 打乱的字节（h 默认 512，w 默认 1024）。★（R160）w 可指定。"""
    h = atlas.shape[0] if h is None else h
    w = atlas.shape[1] if w is None else w
    atlas = np.asarray(atlas)
    if (atlas.shape != (h, w) or atlas.dtype.kind not in "iu"
            or np.any(atlas < 0) or np.any(atlas > 15)):
        raise ValueError("front_atlas.encode requires exact dimensions and integer indices 0..15")
    inv, pix = tables_for(w, h)
    nib = atlas.reshape(-1)[pix]          # nibble index n -> value
    nib = nib.reshape(-1, 2)
    return (nib[:, 0] | (nib[:, 1] << 4)).astype(np.uint8).tobytes()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tex", "t2000.bin")
    blob = open(path, "rb").read()
    img = blob[0x20:0x20 + W * H // 2]
    at = decode(img)
    back = encode(at)
    print("round-trip identical:", back == img)
    print("atlas shape", at.shape, "unique values", sorted(set(at.reshape(-1).tolist())))


if __name__ == "__main__":
    main()
