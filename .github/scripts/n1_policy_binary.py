"""Bounded reader for kernel-exported AppArmor policy (not a policy compiler).

Wire/hash/DFA definitions: Linux security/apparmor/{policy_unpack,crypto,match}.c
and include/{policy_unpack,policy_compat,match}.h. Unknown representations fail
closed. Attachment acceptance is conservative: any packed permission is a
possible conflict, irrespective of xattrs or another profile's specificity.
"""
import hashlib
import struct

MAX_BLOB = 8 * 1024 * 1024


def need(value):
    if not value:
        raise ValueError('unsupported or inconsistent AppArmor binary evidence')


class Wire:
    def __init__(self, data):
        need(0 < len(data) <= MAX_BLOB)
        self.data, self.pos, self.nodes = data, 0, 0

    def take(self, size):
        need(0 <= size <= len(self.data) - self.pos)
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def number(self, size):
        return int.from_bytes(self.take(size), 'little')

    def string(self):
        value = self.take(self.number(2))
        need(value.endswith(b'\0') and b'\0' not in value[:-1])
        return value[:-1].decode('utf-8')

    def node(self, depth=0):
        self.nodes += 1
        need(depth <= 32 and self.nodes <= 200000)
        start = self.pos
        tag, name = self.number(1), None
        if tag == 4:
            name, tag = self.string(), self.number(1)
        if tag in (0, 1, 2, 3):
            value = self.number(1 << tag)
        elif tag == 5:
            value = self.string()
        elif tag == 6:
            value = self.take(self.number(4))
        elif tag in (7, 9, 11):
            # ARRAY's count describes logical entries, which may each consist
            # of multiple wire nodes (e.g. permission records). Parse to END.
            if tag == 11:
                self.number(2)
            value = []
            while True:
                child = self.node(depth + 1)
                if child[1] == tag + 1:
                    need(child[0] is None)
                    break
                need(child[1] not in (8, 10, 12))
                value.append(child)
        else:
            need(tag in (8, 10, 12) and name is None)
            value = None
        return name, tag, value, start, self.pos


def attachment_matches(blob, target):
    # unpack_dfa aligns within the blob; padding is its length modulo 8.
    padding = len(blob) % 8
    need(blob[:padding] == bytes(padding))
    data = blob[padding:]
    need(len(data) >= 16)
    magic, header, size, flags = struct.unpack_from('>IIIH', data)
    # Differential xmatch transitions need different leftmatch handling;
    # refuse that representation instead of guessing. OOB does not affect
    # matching these NUL-free executable paths.
    need(magic == 0x1B5E783D and 16 <= header <= len(data)
         and size == len(data) and flags in (0, 2))
    tables, pos = {}, header
    widths = {1: 4, 2: 4, 3: 2, 4: 2, 5: 1, 7: 4, 8: 2}
    while pos < len(data):
        need(pos + 12 <= len(data))
        ident, width, high, count = struct.unpack_from('>HHII', data, pos)
        need(ident in widths and ident not in tables and width == widths[ident]
             and high == 0 and 0 < count <= 1000000)
        end = pos + ((12 + count * width + 7) & ~7)
        need(end <= len(data))
        tables[ident] = struct.unpack_from('>' + {1: 'B', 2: 'H', 4: 'I'}[width] * count,
                                          data, pos + 12)
        pos = end
    need({1, 2, 3, 4, 7, 8} <= tables.keys())
    accept, base, check, default, nxt = (tables[i] for i in (1, 2, 3, 4, 8))
    need(len(base) >= 2 and len(accept) == len(default) == len(base)
         and len(tables[7]) == len(base) and len(check) == len(nxt))
    need(all(value & 0xDF000000 == 0 for value in base))
    need(all(state < len(base) for state in (*default, *nxt, *check)))
    equiv = tables.get(5, tuple(range(256)))
    need(len(equiv) == 256)
    state = 1
    for char in target.encode('utf-8'):
        pos = (base[state] & 0xFFFFFF) + equiv[char]
        need(pos < len(check))
        state = nxt[pos] if check[pos] == state else default[state]
    return state != 0 and accept[state] != 0


def parse(data):
    """Parse the COMPLETE load blob; hash each exact profile segment like kernel.

    Raw data can contain multiple profiles, including profiles since replaced.
    The caller must bind a named segment's hash to its current kernel entry.
    No source lookup or locally precomputed binary digest is involved.
    """
    wire, version, profiles = Wire(data), None, {}
    while wire.pos < len(data):
        node = wire.node()
        if node[:2] == ('version', 2):
            version = node[2]
            need(5 <= (version & 0x3FF) <= 9)
            node = wire.node()
        # Namespace-qualified blobs are deliberately unresolved in this job's
        # root namespace; never silently strip namespace or rename metadata.
        need(version is not None and node[:2] == ('profile', 7))
        children = node[2]
        need(children and children[0][:2] == (None, 5))
        name = children[0][2]
        need(name and ':' not in name and name not in profiles and len(profiles) < 4096)
        flags = [i for i, field in enumerate(children) if field[:2] == ('flags', 7)]
        need(len(flags) == 1)
        pre = children[1:flags[0]]
        packed = children[flags[0]][2]
        need(len(packed) == 3 and all(field[:2] == (None, 2) for field in packed))
        mode = packed[1][2]
        need(mode in range(5))
        mode = 'complain' if version & 0x800 else ('enforce', 'complain', 'kill', 'unconfined', 'user')[mode]
        attachment = [field[2] for field in pre if field[:2] == ('attach', 5)]
        dfas = [field[2] for field in pre if field[:2] == ('aadfa', 6)]
        # Packed xmatch permissions (ABI 5-9) are supported. An explicit perms
        # table, renamed profile or new field needs review, never permission.
        need(all(field[:2] in (('attach', 5), ('aadfa', 6), (None, 2),
                               ('disconnected', 5)) for field in pre))
        need(len(attachment) <= 1 and len(dfas) <= 1)
        if dfas:
            need(sum(field[:2] == (None, 2) for field in pre) == 1)
        matches = [path for path in ('/usr/bin/bwrap', '/bin/bwrap')
                   if dfas and attachment_matches(dfas[0], path)]
        exported = attachment[0] if attachment else '<unknown>' if dfas else name.split('//')[-1]
        profiles[name] = {
            'attach': exported, 'mode': mode, 'matches': matches,
            'sha256': hashlib.sha256(struct.pack('<I', version) + data[node[3]:node[4]]).hexdigest(),
            'raw_sha256': hashlib.sha256(data).hexdigest(), 'abi': version & 0x3FF,
        }
    need(bool(profiles))
    return profiles
