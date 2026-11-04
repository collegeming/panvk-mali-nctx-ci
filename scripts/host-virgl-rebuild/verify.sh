#!/bin/bash
# Verify the NDK-built libvirglrenderer.so is an aarch64 Android shared object
# that satisfies the exact symbol contract of DroidVM's crosvm (rutabaga virgl).
set -euxo pipefail

LIB=${1:-out/libvirglrenderer.so}
test -f "$LIB"

echo "== ELF header =="
readelf -h "$LIB" | tee /tmp/hdr.txt
grep -q 'Class:.*ELF64' /tmp/hdr.txt
grep -q 'Machine:.*AArch64' /tmp/hdr.txt

echo "== Dynamic section =="
readelf -d "$LIB" | tee /tmp/dyn.txt
grep -q 'SONAME.*\[libvirglrenderer.so\]' /tmp/dyn.txt

# Only Android/bionic libraries may be NEEDED. A glibc-linked build would show
# ld-linux-aarch64.so.1, libpthread.so.0, libgcc_s.so.1 etc.
NEEDED=$(sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p' /tmp/dyn.txt | sort -u)
echo "$NEEDED"
ALLOWED='^(libc|libm|libdl|liblog|libz|libEGL|libGLESv2|libandroid|libnativewindow|libnativehelper|libc\+\+|libstdc\+\+|libunwind|libmediandk|libsync|libcutils|libhardware|libion|libutils|libbase|libbinder_ndk)\.so$'
BAD=0
while IFS= read -r n; do
  [ -z "$n" ] && continue
  if ! printf '%s\n' "$n" | grep -Eq "$ALLOWED"; then
    echo "UNEXPECTED NEEDED: $n"
    BAD=1
  fi
done <<< "$NEEDED"
test "$BAD" -eq 0

echo "== Exported symbols required by DroidVM crosvm =="
cat > /tmp/required.txt <<'EOF'
virgl_renderer_cleanup
virgl_renderer_context_create
virgl_renderer_context_create_fence
virgl_renderer_context_create_with_flags
virgl_renderer_context_destroy
virgl_renderer_create_fence
virgl_renderer_ctx_attach_resource
virgl_renderer_ctx_detach_resource
virgl_renderer_execute
virgl_renderer_fill_caps
virgl_renderer_force_ctx_0
virgl_renderer_get_cap_set
virgl_renderer_get_poll_fd
virgl_renderer_init
virgl_renderer_poll
virgl_renderer_resource_attach_iov
virgl_renderer_resource_create
virgl_renderer_resource_create_blob
virgl_renderer_resource_detach_iov
virgl_renderer_resource_export_blob
virgl_renderer_resource_export_display_blob
virgl_renderer_resource_get_map_info
virgl_renderer_resource_get_map_ptr
virgl_renderer_resource_import_blob
virgl_renderer_resource_map
virgl_renderer_resource_set_guest_blob_fd
virgl_renderer_resource_unmap
virgl_renderer_resource_unref
virgl_renderer_submit_cmd
virgl_renderer_transfer_read_iov
virgl_renderer_transfer_write_iov
EOF
sort -u -o /tmp/required.txt /tmp/required.txt

readelf --dyn-syms -W "$LIB" \
  | awk '$4=="FUNC" && $5=="GLOBAL" && $6=="DEFAULT" && $7!="UND" {print $8}' \
  | sed 's/@.*//' | sort -u > /tmp/exported.txt

echo "-- required but missing --"
comm -23 /tmp/required.txt /tmp/exported.txt | tee /tmp/missing.txt
test ! -s /tmp/missing.txt

echo "== Mali kbase backend strings =="
strings -a "$LIB" | grep -iE 'mali_kbase|VKR_TEST_KBASE|mali-create|mali-blob|mali-props' | sort -u | tee /tmp/mali-strings.txt
grep -qi 'mali_kbase' /tmp/mali-strings.txt
grep -q 'VKR_TEST_KBASE_NATIVE' /tmp/mali-strings.txt

echo "== Vertex/context type marker =="
strings -a "$LIB" | grep -i 'KBAS' | sort -u | head -5 || true

echo "== SHA-256 =="
sha256sum "$LIB" | tee out/SHA256SUMS
echo "VERIFY OK"
