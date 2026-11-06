import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

kdir = Path(os.environ['KDIR'])
out = Path('/build/out')
running = Path('/build/control/running.config')

def options(path):
    return {s.split('=', 1)[0]: s for s in path.read_text().splitlines() if s.startswith('CONFIG_') or s.startswith('# CONFIG_')}

actual = options(running)
headers = options(kdir / '.config')
assert actual == headers, 'Running kernel/header config mismatch'
assert hashlib.sha256(running.read_bytes()).hexdigest() == 'efc4c81ec61b553431569250e8b356e8fec3137c2757339dc820e323e2f3fbd4'
for option in ['SND', 'SND_PCM', 'SND_TIMER', 'VIRTIO', 'VIRTIO_PCI', 'MODULES']:
    assert actual['CONFIG_' + option] == 'CONFIG_' + option + '=y'
assert actual['# CONFIG_SND_VIRTIO is not set'] == '# CONFIG_SND_VIRTIO is not set'
assert actual['# CONFIG_MODVERSIONS is not set'] == '# CONFIG_MODVERSIONS is not set'
assert actual['# CONFIG_MODULE_SIG is not set'] == '# CONFIG_MODULE_SIG is not set'
assert (kdir / 'include/generated/utsrelease.h').read_text().strip() == '#define UTS_RELEASE "7.3.1-2-aarch64-ARCH"'
assert (kdir / 'include/config/kernel.release').read_text().strip() == '7.3.1-2-aarch64-ARCH'
assert 'aarch64-unknown-linux-gnu-gcc (GCC) 16.1.1 20260430, GNU ld (GNU Binutils) 2.46.0' in (kdir / 'include/generated/compile.h').read_text()
assert hashlib.sha256((kdir / 'Module.symvers').read_bytes()).hexdigest() == '4e424f8c1a7372ccf349e04834a47ea83399bd40f1b7ad1b004980c6523d9c63'
print('Exact running/header config, release, compiler identity, prerequisites and symbol-table hash verified')
if sys.argv[1] == 'post':
    exports = {}
    for line in (kdir / 'Module.symvers').read_text().splitlines():
        fields = line.split()
        assert len(fields) >= 4
        exports[fields[1]] = {'crc': fields[0], 'provider': fields[2], 'export': fields[3]}
    required = []
    for line in (out / 'undefined-symbols.txt').read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[-2] in ('U', 'w', 'v'):
            required.append(fields[-1])
    assert required
    missing = sorted(set(required) - exports.keys())
    external = {s: exports[s] for s in required if s in exports and exports[s]['provider'] != 'vmlinux'}
    assert not missing, ('Unknown imports', missing)
    assert not external, ('Imports require other kernel modules', external)
    result = {'imports': {s: exports[s] for s in sorted(required)}, 'missing': missing, 'non_builtin_providers': external, 'config_options_identical': True, 'release': '7.3.1-2-aarch64-ARCH', 'module_symvers_sha256': hashlib.sha256((kdir / 'Module.symvers').read_bytes()).hexdigest()}
    (out / 'imports-verification.json').write_text(json.dumps(result, indent=2))
    print('All', len(required), 'imports resolve to matching kernel builtins; no missing imports or external module dependencies')
