#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
export PATH=/usr/local/bin:/usr/bin:/bin
cd /build
mkdir -p downloads headers module out
exec > >(tee -a /build/out/build.log) 2>&1
fetch() { aria2c --console-log-level=warn --summary-interval=0 --max-tries=5 --retry-wait=3 --allow-overwrite=false --auto-file-renaming=false -x4 -s4 --dir=/build/downloads --out="$2" "$1"; }
mirror=https://mirrors.tuna.tsinghua.edu.cn/archlinuxarm
printf 'Server = %s/$arch/$repo\n' "$mirror" > /etc/pacman.d/mirrorlist
sed -i '/^\[options\]$/a XferCommand = /usr/local/bin/aria2c --console-log-level=warn --summary-interval=0 --allow-overwrite=true --auto-file-renaming=false --dir=/ --out %o %u' /etc/pacman.conf
pacman-key --init
pacman-key --populate archlinuxarm
test "$(df -Pk /build | awk 'NR==2 {print $4}')" -gt 4000000
sed -i 's/^CheckSpace$/# CheckSpace (host df checked above)/' /etc/pacman.conf
# Isolated CI tool root only: never mount or install anything in either guest VM.
pacman -Syu --noconfirm --needed --ignore 'linux-aarch64,linux-aarch64-headers,linux-firmware,linux-firmware-*' base-devel bc openssl libelf kmod python
CC=aarch64-unknown-linux-gnu-gcc
export ARCH=arm64
test "$("$CC" -dumpfullversion)" = 16.1.1
test "$("$CC" --version | head -1)" = 'aarch64-unknown-linux-gnu-gcc (GCC) 16.1.1 20260430'
ld --version | grep -q '2\.46'
"$CC" --version > out/compiler.txt
ld --version >> out/compiler.txt
fetch "$mirror/aarch64/core/linux-aarch64-headers-7.3.1-2-aarch64.pkg.tar.xz" headers.pkg.tar.xz
fetch https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.3.1.tar.xz linux-7.3.1.tar.xz
printf '%s  %s\n' 8e93db0c909f41a7b1b0f723a010f7f1c97b5faea01e65a26d8c38d168c4d53a downloads/headers.pkg.tar.xz fa595aad93734f829c963c2a90ef7c3673224fd7fdae7b49406e397cde2de231 downloads/linux-7.3.1.tar.xz | sha256sum -c -
# Extract only required official package build files and sound/virtio sources.
python -I - <<'PY'
from pathlib import Path, PurePosixPath
import tarfile

def extract(archive, prefix, dest):
    with tarfile.open(archive, 'r:xz') as tar:
        selected=[]
        for m in tar.getmembers():
            p=PurePosixPath(m.name)
            assert not p.is_absolute() and '..' not in p.parts and '\\' not in m.name and ':' not in m.name
            if m.name == prefix.rstrip('/') or m.name.startswith(prefix):
                assert m.isfile() or m.isdir(), ('Unexpected special file', m.name)
                selected.append(m)
        assert selected
        tar.extractall(dest, members=selected, filter='data')
extract('/build/downloads/headers.pkg.tar.xz', 'usr/lib/modules/7.3.1-2-aarch64-ARCH/build/', '/build/headers')
extract('/build/downloads/linux-7.3.1.tar.xz', 'linux-7.3.1/sound/virtio/', '/build/module')
PY
KDIR=/build/headers/usr/lib/modules/7.3.1-2-aarch64-ARCH/build
MDIR=/build/module/linux-7.3.1/sound/virtio
export KDIR MDIR
test -s "$KDIR/Module.symvers"
test -s "$KDIR/scripts/module.lds"
printf '%s  %s\n' 4e424f8c1a7372ccf349e04834a47ea83399bd40f1b7ad1b004980c6523d9c63 "$KDIR/Module.symvers" efc4c81ec61b553431569250e8b356e8fec3137c2757339dc820e323e2f3fbd4 "$KDIR/.config" | sha256sum -c -
python -I /build/control/verify-panvk-module.py pre
# Hash every prepared-header file before and after; Kbuild may not mutate configuration or release.
(cd "$KDIR"; find . -type f -print0 | sort -z | xargs -0 sha256sum) > out/headers-before.sha256
# No olddefconfig, modules_prepare, Image or vmlinux build and no vermagic editing.
# This command-line override selects an external module without changing actual .config.
make -C "$KDIR" M="$MDIR" CC="$CC" CONFIG_SND_VIRTIO=m -j"$(nproc)" modules
(cd "$KDIR"; sha256sum -c /build/out/headers-before.sha256 > /build/out/headers-unchanged.txt)
install -m644 "$MDIR/virtio_snd.ko" out/virtio_snd.ko
modinfo out/virtio_snd.ko | tee out/modinfo.txt
test "$(modinfo -F vermagic out/virtio_snd.ko)" = '7.3.1-2-aarch64-ARCH SMP preempt mod_unload aarch64'
test -z "$(modinfo -F depends out/virtio_snd.ko)"
readelf -h -S out/virtio_snd.ko > out/elf.txt
readelf -h out/virtio_snd.ko | grep -q 'Machine:.*AArch64'
nm -u out/virtio_snd.ko > out/undefined-symbols.txt
cp "$KDIR/.config" out/headers.config
cp /build/control/running.config out/running.config
cp "$KDIR/include/generated/utsrelease.h" "$KDIR/include/generated/compile.h" out/
cp "$KDIR/Module.symvers" out/Module.symvers
python -I /build/control/verify-panvk-module.py post
printf '%s\n' 'release=7.3.1-2-aarch64-ARCH' 'scope=external virtio-snd module only; no runtime/device/VM operation' 'source=kernel.org linux-7.3.1 sound/virtio, unmodified' 'headers=official Arch Linux ARM 7.3.1-2, key files byte-identical to running guest headers' 'config_override=CONFIG_SND_VIRTIO=m command-line only; actual config unchanged' > out/provenance.txt
sha256sum downloads/headers.pkg.tar.xz downloads/linux-7.3.1.tar.xz >> out/provenance.txt
(cd out; sha256sum virtio_snd.ko headers.config running.config Module.symvers utsrelease.h compile.h imports-verification.json > SHA256SUMS)
