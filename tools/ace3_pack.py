"""Rebuild A.C.E.3 text resources and the containers that hold them.

Two pieces:

  rebuild_resource(blob, res_off, edits)   - text resource (version 0x00010000)
  Bnd2.rebuild(new_blobs)                  - 'BND\\0' archive

How a text resource is rebuilt
------------------------------
The string area is one contiguous run of NUL separated strings.  We split it
into tokens, swap the text of the edited slots, then glue the tokens back
together.  Every slot that is not edited keeps its original bytes, and so do
tokens nothing points at (some files contain such leftovers).  Afterwards the
offset table is rewritten from the new token positions.
"""
import struct

VER = b"\x00\x00\x01\x00"


class Bnd2:
    """'BND\\0' archive: u32 size, u32 count, count x (u32 tag, u32 offset)."""

    def __init__(self, data):
        if data[:4] != b"BND\x00":
            raise ValueError("not a BND archive")
        self.data = data
        self.size = struct.unpack_from("<I", data, 4)[0]
        self.count = struct.unpack_from("<I", data, 8)[0]
        self.table = [struct.unpack_from("<II", data, 0x10 + i * 8)
                      for i in range(self.count)]
        self.order = sorted(range(self.count), key=lambda i: self.table[i][1])
        self.rank = {i: r for r, i in enumerate(self.order)}
        self.first_off = self.table[self.order[0]][1]

    def blob(self, i):
        off = self.table[i][1]
        r = self.rank[i]
        end = (self.table[self.order[r + 1]][1]
               if r + 1 < len(self.order) else self.size)
        return self.data[off:end]

    def tag_index(self, tag):
        for i, (t, _) in enumerate(self.table):
            if t == tag:
                return i
        raise KeyError(tag)

    ALIGN = 0x20

    def rebuild(self, blobs, align=None, keep_offsets=False):
        """blobs[i] is the new content of table entry i (logical order).

        The original archives place every sub file on a 0x20 boundary and pad
        the gaps with zeros.  The PS2 GS needs that alignment for texture DMA,
        so a rebuild must keep it.  `align=None` uses the archive's own
        alignment (0x20); `keep_offsets=True` additionally pads every sub file
        back to its original length so that no offset moves at all.
        """
        align = self.ALIGN if align is None else align
        header = bytearray(self.data[:self.first_off])
        cur = self.first_off
        body = bytearray()
        newoff = {}
        for i in self.order:
            newoff[i] = cur
            blob = blobs[i]
            if keep_offsets:
                orig = len(self.blob(i))
                if len(blob) > orig:
                    raise ValueError(f"sub file {i} grew past its original slot")
                blob = blob + b"\x00" * (orig - len(blob))
            body += blob
            cur += len(blob)
            pad = (-cur) % align
            if pad:
                body += b"\x00" * pad
                cur += pad
        size = cur
        struct.pack_into("<I", header, 4, size)
        for i in self.order:
            struct.pack_into("<I", header, 0x10 + i * 8 + 4, newoff[i])
        out = bytes(header) + bytes(body)
        if len(out) < size:
            out += b"\x00" * (size - len(out))
        return out


def resource_size(blob, res_off):
    """Declared size of the resource (field +0x04), or None if implausible."""
    if res_off + 8 > len(blob):
        return None
    size = struct.unpack_from("<I", blob, res_off + 4)[0]
    if size < 0x20 or res_off + size > len(blob):
        return None
    return size


def parse_offsets(blob, res_off):
    count = struct.unpack_from("<I", blob, res_off + 0x10)[0]
    table_off = struct.unpack_from("<I", blob, res_off + 0x14)[0]
    tstart = res_off + table_off
    return count, tstart, list(struct.unpack_from(f"<{count}I", blob, tstart))


def _tokens(blob, lo, hi):
    """[(start, end)] of NUL separated tokens in blob[lo:hi]."""
    out = []
    i = lo
    while i <= hi:
        j = blob.find(b"\x00", i, hi)
        if j < 0:
            out.append((i, hi))
            break
        out.append((i, j))
        i = j + 1
        if i == hi:
            break
    return out


def rebuild_resource(blob, res_off, edits, verbose=False):
    """edits: {slot: "new text"}. Returns (new_blob, report dict)."""
    count, tstart, offsets = parse_offsets(blob, res_off)
    used = [v for v in offsets if v]
    if not used:
        raise ValueError("resource has no strings")
    lo = res_off + min(used)
    size = resource_size(blob, res_off)
    hi = res_off + size if size else len(blob)
    toks = _tokens(blob, lo, hi)
    starts = [t[0] for t in toks]

    # slot -> token index
    import bisect
    slot_tok = {}
    for slot, v in enumerate(offsets):
        if not v:
            continue
        p = res_off + v
        k = bisect.bisect_left(starts, p)
        if k >= len(starts) or starts[k] != p:
            raise ValueError(f"slot {slot} offset {v:#x} is not a token start")
        slot_tok[slot] = k

    newtoks = [blob[a:b] for a, b in toks]
    applied = []
    for slot, text in edits.items():
        if slot not in slot_tok:
            raise ValueError(f"slot {slot} is empty, nothing to replace")
        newtoks[slot_tok[slot]] = text if isinstance(text, bytes) else text.encode("cp932")
        applied.append((slot, blob[toks[slot_tok[slot]][0]:toks[slot_tok[slot]][1]]
                        .decode("cp932", "replace"),
                        text if isinstance(text, str) else "<bytes>"))

    region = b"\x00".join(newtoks)
    # new offsets
    pos = {}
    cur = 0
    for k, t in enumerate(newtoks):
        pos[k] = cur
        cur += len(t) + 1
    newoffsets = []
    for slot, v in enumerate(offsets):
        if not v:
            newoffsets.append(0)
        else:
            newoffsets.append(lo - res_off + pos[slot_tok[slot]])

    out = bytearray(blob[:lo])
    struct.pack_into(f"<{count}I", out, tstart, *newoffsets)
    out += region
    new_size = len(out) - res_off
    struct.pack_into("<I", out, res_off + 4, new_size)
    report = {
        "count": count,
        "tokens": len(toks),
        "old_size": len(blob),
        "new_resource_size": new_size,
        "before": hi - lo,
        "after": len(region),
        "applied": applied,
    }
    if verbose:
        for slot, old, new in applied:
            print(f"    slot {slot:<4} {old!r} -> {new!r}")
    return bytes(out), report


def find_resource(blob, res_off=0):
    """Return the resource offset inside blob (0x00010000 tag), or None."""
    if blob[:4] == VER:
        return 0
    i = blob.find(VER)
    return i if i >= 0 else None
