#!/usr/bin/env python3
"""Print the defined dynamic symbols of an ELF64 little-endian shared object.

Used by the host-csync workflow to prove that the experiment does not change
the library's exported ABI: the output must match
scripts/host-csync/baseline-dynsym.txt (taken from the deployed library,
sha256 22e1c7df...), byte for byte. Runs on the CI runner with plain python3,
no pyelftools and no binutils layout assumptions.
"""

import struct
import sys


def sections(data):
    assert data[:4] == b'\x7fELF', 'not an ELF file'
    assert data[4] == 2 and data[5] == 1, 'expected ELF64 little-endian'
    e_shoff = struct.unpack_from('<Q', data, 0x28)[0]
    e_shentsize = struct.unpack_from('<H', data, 0x3a)[0]
    e_shnum = struct.unpack_from('<H', data, 0x3c)[0]
    e_shstrndx = struct.unpack_from('<H', data, 0x3e)[0]
    out = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name, stype, _flags, _addr, offset, size = struct.unpack_from('<IIQQQQ', data, off)
        link, _info, _align, entsize = struct.unpack_from('<IIQQ', data, off + 40)
        out.append(dict(name=name, type=stype, offset=offset, size=size,
                        link=link, entsize=entsize))
    shstr = out[e_shstrndx]
    base = shstr['offset']
    for s in out:
        end = data.index(b'\0', base + s['name'])
        s['sname'] = data[base + s['name']:end].decode()
    return out


def main():
    data = open(sys.argv[1], 'rb').read()
    secs = sections(data)
    dynsym = next((s for s in secs if s['sname'] == '.dynsym'), None)
    if dynsym is None:
        raise SystemExit('no .dynsym section')
    dynstr = secs[dynsym['link']]
    strs = data[dynstr['offset']:dynstr['offset'] + dynstr['size']]

    def cstr(off):
        return strs[off:strs.index(b'\0', off)].decode('utf-8', 'replace')

    names = set()
    for off in range(dynsym['offset'], dynsym['offset'] + dynsym['size'],
                     dynsym['entsize']):
        name, info, _other, shndx, _value, _size = struct.unpack_from(
            '<IBBHQQ', data, off)
        if not name or shndx == 0:
            continue
        if (info >> 4) not in (1, 2):  # GLOBAL or WEAK only
            continue
        names.add(cstr(name))
    for n in sorted(names):
        print(n)


if __name__ == '__main__':
    main()
