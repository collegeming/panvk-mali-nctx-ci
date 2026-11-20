#!/usr/bin/env python3
"""Host-side Mali kbase event decode + shadow-BO live-mapping access.

Two behavior changes, both driven by the task hang evidence (dmesg
CS_FATAL.EXCEPTION_TYPE 0x48 / CS_BUS_FAULT, EXCEPTION_DATA 0x9c00010,
"queue not bound when handling an error event"):

* A1 (src/drm/kbase_transport_queue.h): the CS group-error notification is
  read as a 64-byte event and dropped with -EIO before anything inspects it,
  so the 63-byte payload -- the only record of which queue group died and
  why, delivered exactly once by the driver -- is lost.  Decode it per
  mali_kbase_ioctl.h and print it to stderr first: notification type, csg
  handle, error_type name (switch over the five base_gpu_queue_group_error_type
  values), status, sideband, csi_index, and a 16-word little-endian hex dump.

* A2 (src/drm/mali_kbase_renderer.c): copy-based guest accesses (KB_WRITE
  cmd2 / KB_READ cmd3) used bo->ptr, which for shadow BOs is a private memfd
  copy while the memory the GPU and the kbase driver actually see is
  bo->gpu_mapping.  Route shadow BOs through the live mapping so those
  accesses are never stale.  Imported mappings can SIGBUS on CPU access
  (see the in-code comment), and plain KB_ALLOC BOs have bo->ptr as the live
  mapping already, so only shadow BOs take the gpu_mapping path.

Idempotent: re-running on a patched tree is a no-op.  Anchor drift fails
loudly with a "patch_host_kbase_evt:" prefixed message.
"""

from __future__ import annotations

import pathlib
import sys

A1_ANCHOR = (
    "         if (n == 64 && event[0] == BASE_CSF_NOTIFICATION_GPU_QUEUE_GROUP_ERROR) return -EIO;\n"
)
A1_MARKER = "kbase_evt: type="
A1_NEW = """         if (n == 64 && event[0] == BASE_CSF_NOTIFICATION_GPU_QUEUE_GROUP_ERROR) {
            /* fix-kbase-evt: decode the 63-byte payload before discarding it.
             * The driver delivers this notification exactly once; without this
             * record the group-error cause is unrecoverable. */
            const struct base_csf_notification *note = (const void *)event;
            const struct base_gpu_queue_group_error *ge =
               &note->payload.csg_error.error;
            const char *err_name;
            switch (ge->error_type) {
            case BASE_GPU_QUEUE_GROUP_ERROR_FATAL:
               err_name = "BASE_GPU_QUEUE_GROUP_ERROR_FATAL"; break;
            case BASE_GPU_QUEUE_GROUP_QUEUE_ERROR_FATAL:
               err_name = "BASE_GPU_QUEUE_GROUP_QUEUE_ERROR_FATAL"; break;
            case BASE_GPU_QUEUE_GROUP_ERROR_TIMEOUT:
               err_name = "BASE_GPU_QUEUE_GROUP_ERROR_TIMEOUT"; break;
            case BASE_GPU_QUEUE_GROUP_ERROR_TILER_HEAP_OOM:
               err_name = "BASE_GPU_QUEUE_GROUP_ERROR_TILER_HEAP_OOM"; break;
            case BASE_GPU_QUEUE_GROUP_QUEUE_ERROR_FAULT:
               err_name = "BASE_GPU_QUEUE_GROUP_QUEUE_ERROR_FAULT"; break;
            default:
               err_name = "UNKNOWN"; break;
            }
            /* fatal_group/fatal_queue/fault_queue share the sideband/status
             * layout, so one fatal_queue read covers every error_type. */
            fprintf(stderr,
                    "kbase_evt: type=%u handle=%u error_type=%u err_name=%s status=%u sideband=%llu csi=%u\\n",
                    (unsigned)note->type, (unsigned)note->payload.csg_error.handle,
                    (unsigned)ge->error_type, err_name,
                    (unsigned)ge->payload.fatal_queue.status,
                    (unsigned long long)ge->payload.fatal_queue.sideband,
                    (unsigned)ge->payload.fatal_queue.csi_index);
            fprintf(stderr, "kbase_evt: dump");
            for (unsigned w = 0; w < 16; w++)
               fprintf(stderr, " %02x%02x%02x%02x",
                       event[4 * w + 3], event[4 * w + 2],
                       event[4 * w + 1], event[4 * w]);
            fprintf(stderr, "\\n");
            return -EIO;
         }
"""

A2_ANCHOR = (
    "   if (hdr->cmd == 2) memcpy((uint8_t *)bo->ptr + q->offset, q->data, q->size);\n"
    "   else if (hdr->cmd == 3) memcpy(r->data, (uint8_t *)bo->ptr + q->offset, q->size);\n"
)
A2_MARKER = "bo->shadow ? (uint8_t *)bo->gpu_mapping"
A2_NEW = """   /* fix-kbase-evt: for shadow BOs bo->ptr is a private memfd copy; the
    * memory the GPU and the kbase driver actually see is bo->gpu_mapping.
    * Route copy-based guest accesses through the live mapping so they are
    * never stale.  Imported mappings can SIGBUS on CPU access (see below),
    * so only shadow BOs take the gpu_mapping path. */
   uint8_t *live = bo->shadow ? (uint8_t *)bo->gpu_mapping : (uint8_t *)bo->ptr;
   if (hdr->cmd == 2) memcpy(live + q->offset, q->data, q->size);
   else if (hdr->cmd == 3) memcpy(r->data, live + q->offset, q->size);
"""


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"patch_host_kbase_evt: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def read_lf(path: pathlib.Path) -> str:
    return path.read_bytes().decode().replace("\r\n", "\n")


def write_lf(path: pathlib.Path, text: str) -> None:
    path.write_bytes(text.encode())


def apply_once(text: str, anchor: str, new: str, marker: str, what: str) -> str:
    if marker in text:
        require(anchor not in text, f"partially patched: {what}")
        print(f"patch_host_kbase_evt: already applied: {what}")
        return text
    require(text.count(anchor) == 1, f"anchor not unique or missing: {what}")
    return text.replace(anchor, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: patch_host_kbase_evt.py <virglrenderer-root>")
    root = pathlib.Path(sys.argv[1])
    queue = root / "src/drm/kbase_transport_queue.h"
    mali = root / "src/drm/mali_kbase_renderer.c"
    for p in (queue, mali):
        require(p.is_file(), f"missing file: {p}")

    mali_text = read_lf(mali)
    queue_text = read_lf(queue)

    # A1's fprintf needs stdio.h in mali_kbase_renderer.c (probe normally
    # adds it; add it idempotently so this patch also works standalone).
    if "#include <stdio.h>" not in mali_text:
        stdio_anchor = "#include <errno.h>\n"
        require(mali_text.count(stdio_anchor) == 1,
                "anchor not unique or missing: stdio include")
        mali_text = mali_text.replace(stdio_anchor,
                                      stdio_anchor + "#include <stdio.h>\n", 1)

    queue_text = apply_once(queue_text, A1_ANCHOR, A1_NEW, A1_MARKER,
                            "A1 group-error decode (kbase_transport_queue.h)")
    mali_text = apply_once(mali_text, A2_ANCHOR, A2_NEW, A2_MARKER,
                           "A2 shadow-BO live mapping (mali_kbase_renderer.c)")

    write_lf(queue, queue_text)
    write_lf(mali, mali_text)
    print(f"patched {mali}")
    print(f"patched {queue}")


main()
