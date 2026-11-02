#!/bin/bash
set -euxo pipefail

ROOT=$(pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$ROOT/work
OUT=$ROOT/out
mkdir -p "$WORK" "$OUT"
cd "$WORK"

NDK_ZIP=android-ndk-r27c-linux.zip
if [ ! -f "$NDK_ZIP" ]; then
  curl -fsSL --retry 3 -o "$NDK_ZIP" https://dl.google.com/android/repository/android-ndk-r27c-linux.zip
  printf '%s  %s\n' 090e8083a715fdb1a3e402d0763c388abb03fb4e "$NDK_ZIP" | sha1sum -c -
fi
unzip -q -o "$NDK_ZIP" -d "$WORK/ndk"
NDK=$WORK/ndk/android-ndk-r27c
TOOL=$NDK/toolchains/llvm/prebuilt/linux-x86_64
CC=$TOOL/bin/aarch64-linux-android33-clang
AR=$TOOL/bin/llvm-ar
SYS=$TOOL/sysroot
API=33

curl -fsSL --retry 3 -o virgl.tgz https://github.com/MollySophia/virglrenderer/archive/e58441fc6fa6ce73e6545882b43c8c807fe22a11.tar.gz
tar xzf virgl.tgz
V=$WORK/virglrenderer-e58441fc6fa6ce73e6545882b43c8c807fe22a11
test -f "$V/src/drm/mali_kbase_renderer.c"

python3 - "$V/src/gallium/auxiliary/util/u_format.h" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
s = p.read_text()
s = s.replace('#include "pipe/p_compiler.h"',
              '#include <stdio.h>' + chr(10) + '#include "pipe/p_compiler.h"', 1)
old = """   const struct util_format_description *desc = util_format_description(format);
   assert(desc);
   return desc->is_depth || desc->is_stencil;"""
new = """   const struct util_format_description *desc = util_format_description(format);
   if (!desc) {
      fprintf(stderr, "virglrenderer: NULL format %d", (int)format);
      return false;
   }
   return desc->is_depth || desc->is_stencil;"""
assert old in s, 'format assert anchor not found'
s = s.replace(old, new, 1)
p.write_text(s)
print('patched util_format_is_depth_or_stencil')
PY

curl -fsSL --retry 3 -o epoxy.tgz https://github.com/anholt/libepoxy/archive/refs/tags/1.5.10.tar.gz
tar xzf epoxy.tgz
E=$WORK/libepoxy-1.5.10

mkdir -p "$WORK/pc"
for lib in egl glesv2; do
  case $lib in
    egl) LIBS='-lEGL' ;;
    glesv2) LIBS='-lGLESv2' ;;
  esac
  cat > "$WORK/pc/$lib.pc" <<EOF
prefix=$SYS/usr
libdir=$SYS/usr/lib/aarch64-linux-android/$API
includedir=$SYS/usr/include
Name: $lib
Description: $lib
Version: 1.5
Libs: -L\${libdir} $LIBS
Cflags: -I\${includedir}
EOF
done

cat > "$WORK/cross.txt" <<EOF
[binaries]
c = '$CC'
ar = '$AR'
strip = '$TOOL/bin/llvm-strip'
pkgconfig = '/usr/bin/pkg-config'
[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'aarch64'
endian = 'little'
[built-in options]
c_args = ['-fPIC']
c_link_args = ['-fPIC']
EOF

PKG_CONFIG_LIBDIR=$WORK/pc meson setup "$E/build" "$E" --cross-file "$WORK/cross.txt" \
  --prefix="$WORK/epoxy-prefix" -Ddefault_library=static -Dglx=no -Degl=yes -Dx11=false -Dtests=false -Ddocs=false
ninja -C "$E/build"
ninja -C "$E/build" install
EPOXY_LIB=$(find "$WORK/epoxy-prefix" -name 'libepoxy.a' | head -1)
test -n "$EPOXY_LIB"
test -f "$WORK/epoxy-prefix/include/epoxy/egl.h"

cp "$V/prebuilt-intermediates/config.h" "$WORK/config.h"
python3 - "$WORK/config.h" <<'PY'
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text()
s = s.replace('#define ENABLE_VENUS 1', '/* #define ENABLE_VENUS 1 */')
s += '\n#define HAVE_MEMFD_CREATE 1\n#define HAVE_LINUX_UDMABUF_H 1\n'
p.write_text(s)
PY

python3 - "$V/Android.bp" "$WORK/sources.txt" <<'PY'
import re
import sys

srcs = []
in_srcs = False
for line in open(sys.argv[1]):
    t = line.strip()
    if t.startswith('srcs: ['):
        in_srcs = True
        continue
    if in_srcs and t == '],':
        break
    if in_srcs:
        m = re.search(r'"([^"]+)"', line)
        if m:
            srcs.append(m.group(1))
srcs = [s for s in srcs if not s.startswith('src/venus/')]
open(sys.argv[2], 'w').write('\n'.join(srcs) + '\n')
print(len(srcs), 'sources')
PY

curl -fsSL --retry 3 -o "$WORK/xf86drm.h.b64" 'https://android.googlesource.com/platform/external/libdrm/+/refs/heads/main/xf86drm.h?format=TEXT' || true
if [ -s "$WORK/xf86drm.h.b64" ]; then
  base64 -d "$WORK/xf86drm.h.b64" > "$WORK/xf86drm.h" || true
fi
if [ ! -s "$WORK/xf86drm.h" ]; then
  curl -fsSL --retry 3 -o "$WORK/xf86drm.h" https://gitlab.freedesktop.org/mesa/drm/-/raw/main/xf86drm.h
fi
test -s "$WORK/xf86drm.h"

cp "$HERE/drm_stub.c" "$HERE/cutils_stub.c" "$HERE/cpu_stub.c" "$WORK/"
mkdir -p "$WORK/cutils" "$WORK/log"
cp "$HERE/properties.h" "$WORK/cutils/properties.h"
cat > "$WORK/log/log.h" <<'EOF'
#pragma once
#include <android/log.h>
#define LOG_PRI(priority, tag, ...) __android_log_print(priority, tag, __VA_ARGS__)
EOF

cd "$V"
$CC -shared -fPIC -O2 -g0 \
  -DHAVE_CONFIG_H -D__USE_GNU -DHAVE_MEMFD_CREATE=1 -DHAVE_LINUX_UDMABUF_H=1 \
  -Wno-unused-parameter -Wno-pointer-sign -Wno-implicit-function-declaration \
  -include "$WORK/config.h" \
  -I"$WORK" -I"$V/src" -I"$V/src/drm" -I"$V/src/drm/drm-uapi" \
  -I"$V/src/mesa" -I"$V/src/mesa/compat" -I"$V/src/mesa/pipe" -I"$V/src/mesa/util" \
  -I"$V/src/gallium/auxiliary" -I"$V/src/gallium/auxiliary/util" -I"$V/src/gallium/include" \
  -I"$V/prebuilt-intermediates" -I"$V/src/venus" -I"$NDK/sources/android/cpufeatures" \
  -I"$WORK/epoxy-prefix/include" \
  -o "$OUT/libvirglrenderer.so" \
  $(cat "$WORK/sources.txt") \
  "$WORK/drm_stub.c" "$WORK/cutils_stub.c" "$WORK/cpu_stub.c" \
  "$EPOXY_LIB" -lEGL -lGLESv2 -llog -ldl -lm -lz \
  -Wl,-soname,libvirglrenderer.so

readelf -h "$OUT/libvirglrenderer.so" | grep -E 'Machine|Class'
readelf -d "$OUT/libvirglrenderer.so" | grep -E 'SONAME|NEEDED'
strings "$OUT/libvirglrenderer.so" | grep -E 'mali_kbase|VKR_TEST_KBASE|drm_renderer' | sort -u > "$OUT/mali-strings.txt" || true
cat "$OUT/mali-strings.txt"
sha256sum "$OUT/libvirglrenderer.so" > "$OUT/SHA256SUMS"
