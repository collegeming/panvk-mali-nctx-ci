#!/usr/bin/env python3
"""Give the mali_kbase context a way to consume classic-3D backing pages.

The problem
-----------
`attach()` in this backend is empty:

    static void attach(struct virgl_context *v, struct virgl_resource *res)
    { (void)v; (void)res; }

The guest Windows KMD orders the two calls the other way round from what the
name suggests:

  1. `DeviceSpecificAllocation::new()` -> `CTX_ATTACH_RESOURCE` -> host
     `virgl_renderer_ctx_attach_resource()` -> `attach()`.
  2. Later, on `DXGK_OPERATION_UPDATE_PAGE_TABLE` -> `RESOURCE_ATTACH_BACKING`
     -> host `virgl_renderer_resource_attach_iov()` -> `virgl_resource_attach_iov()`.

So at attach time `res->iov` is still NULL, and a snapshot taken there is
always empty.  The authoritative page list only becomes readable at step 2,
stored on the *global* `virgl_resource`, not on the context.

Why vrend is not the answer here
--------------------------------
For a classic (non-blob) 3D resource `virgl_renderer_resource_create()` builds
a vrend `pipe_resource`, so `virgl_resource_attach_iov()` forwards the iovecs
to `vrend_pipe_resource_attach_iov()` and vrend uploads them lazily when a
TRANSFER3D with `iovec_cnt == 0` arrives.  That works, but only for a vrend
context.  This backend is a DRM-style context:

  * `drm_context_init()` installs `drm_context_transfer_3d`, a stub that
    returns -1;
  * `drm_context_submit_cmd()` rejects any ccmd id >= `dispatch_size`;
  * the dispatch table here is the private KB_* transport (17 entries, ids
    0..16), while `VIRGL_CCMD_TRANSFER3D` is 41 and
    `VIRGL_CCMD_PIPE_RESOURCE_SET_TYPE` is 47.

A vrend command stream submitted to this context is therefore rejected before
vrend can see it.  The guest's real GL path runs on its separate virgl2
context; the mali_kbase context only carries the KB_* transport and native
kbase work.

What this patch adds
--------------------
`KB_RES_UPLOAD` (a new ccmd appended to the KB_* table) copies a resource's
current backing iovecs into a caller-named kbase BO.  It resolves the
resource through `virgl_resource_lookup()` at call time, so it sees the page
list that ATTACH_BACKING installed, instead of a stale snapshot taken before
that list existed.  The BO then holds guest pixels in host-visible memory and
the native command stream can sample it.

The command is explicit, not automatic: only the guest knows which BO should
receive a given surface, and an automatic copy at attach/backing time would
have to guess.  Nothing changes for paths that already work, because the
handler runs only when the guest sends ccmd 17.

Fields
------
    handle  : destination BO handle (KB_ALLOC/KB_SHARED_ALLOC/KB_NATIVE_ALLOC)
    offset  : destination byte offset inside that BO
    size    : bytes to copy; 0 means "all attached bytes"
    reserved: source virgl resource id (the guest's ATTACH_BACKING id)
"""

from __future__ import annotations

import pathlib
import sys


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"patch_host_res_upload: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def sub_idem(text: str, old: str, new: str, what: str) -> str:
    require(text.count(old) == 1, f"anchor not unique: {what}")
    return text.replace(old, new, 1)


HELPER = r'''
/* Copy a resource's attached backing into `dst`.
 *
 * `res` is looked up from the global resource table at call time: attach runs
 * before RESOURCE_ATTACH_BACKING on this stack, so nothing cached earlier can
 * be authoritative.  Returns bytes copied or a negative errno.
 */
static int
kb_res_upload(struct kb_ctx *c, struct virgl_resource *res, struct kb_bo *bo,
              uint32_t offset, uint64_t want)
{
   if (!res || !res->iov || res->iov_count <= 0) return -ENOENT;
   if (!bo || !bo->ptr) return -ENOENT;

   uint64_t iov_total = 0;
   for (int i = 0; i < res->iov_count; i++)
      iov_total += res->iov[i].iov_len;
   if (!want) want = iov_total;
   if (!want || want > iov_total) return -EINVAL;
   if (offset > bo->size || want > bo->size - offset) return -EINVAL;

   uint8_t *dst = (uint8_t *)bo->ptr + offset;
   uint64_t done = 0;
   for (int i = 0; i < res->iov_count && done < want; i++) {
      uint64_t left = want - done;
      uint64_t len = res->iov[i].iov_len < left ? res->iov[i].iov_len : left;
      memcpy(dst + done, res->iov[i].iov_base, (size_t)len);
      done += len;
   }

   /* Shadow BOs keep the GPU-visible copy in gpu_mapping; a shared BO aliases
    * the same memfd, so its CPU view is already what the GPU reads. */
   if (bo->shadow && bo->gpu_mapping) {
      memcpy((uint8_t *)bo->gpu_mapping + offset, dst, (size_t)done);
      struct kbase_ioctl_mem_sync s = {
         .handle = bo->gpu_va,
         .user_addr = (uintptr_t)((uint8_t *)bo->gpu_mapping + offset),
         .size = (uint32_t)done,
         .type = BASE_SYNCSET_OP_MSYNC,
      };
      if (ioctl(c->base.fd, KBASE_IOCTL_MEM_SYNC, &s)) return -errno;
   }

   return (int)done;
}
'''

CMD = r'''   if (hdr->cmd == 17) {
      /* KB_RES_UPLOAD: copy attached backing pages into a BO.
       *   handle=BO handle, offset=dst byte offset, size=bytes (0=all),
       *   reserved=source virgl resource id. */
      struct virgl_resource *res = virgl_resource_lookup(q->reserved);
      struct kb_bo *bo = NULL;
      for (unsigned i = 0; i < MAX_BO; i++)
         if (c->bo[i].ptr && c->bo[i].handle == q->handle) { bo = &c->bo[i]; break; }
      if (!bo) { r->status = -ENOENT; return 0; }

      int n = kb_res_upload(c, res, bo, q->offset, q->size);
      if (n < 0) { r->status = n; return 0; }
      r->size = (uint32_t)n;
      fprintf(stderr, "kbattach: upload res=%u -> bo=%u off=%u bytes=%d\n",
              q->reserved, q->handle, q->offset, n);
      return 0;
   }
'''


def main() -> int:
    require(len(sys.argv) == 2, "usage: patch_host_res_upload.py <virglrenderer-root>")
    path = pathlib.Path(sys.argv[1]) / "src/drm/mali_kbase_renderer.c"
    require(path.is_file(), f"missing {path}")
    text = path.read_text()

    require("KB_RES_UPLOAD" not in text, "already patched")

    # The handler uses virgl_resource_lookup(); drm_context.h pulls in
    # virgl_context.h -> virgl_resource.h, which declares it.
    text = sub_idem(
        text,
        """#include "drm_context.h"
#include "mali_kbase_renderer.h\"""",
        """#include "drm_context.h"
#include "mali_kbase_renderer.h"
#include "virgl_resource.h\"""",
        "virgl_resource.h include",
    )

    # Helper before the command handler.
    text = sub_idem(
        text,
        "static int command(struct drm_context *base, struct vdrm_ccmd_req *hdr)\n{",
        HELPER + "\nstatic int command(struct drm_context *base, struct vdrm_ccmd_req *hdr)\n{",
        "kb_res_upload helper",
    )

    # Handler before the allocation-command group.
    text = sub_idem(
        text,
        "   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {",
        CMD + "   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {",
        "KB_RES_UPLOAD handler",
    )

    # Append to the KB_* dispatch table.  Anchor on the array's closing brace
    # plus the first line after it: the entry before `}` differs between trees
    # (KB_ALIAS on main, KB_IMPORT_SHARED on the shared-import branch), but the
    # terminator does not.
    text = sub_idem(
        text,
        """   {"KB_ALIAS", command, sizeof(struct kb_req)},
};""",
        """   {"KB_ALIAS", command, sizeof(struct kb_req)},
   {"KB_RES_UPLOAD", command, sizeof(struct kb_req)},
};""",
        "dispatch KB_RES_UPLOAD entry (main)",
    ) if text.count("""   {"KB_ALIAS", command, sizeof(struct kb_req)},
};""") == 1 else sub_idem(
        text,
        """   {"KB_IMPORT_SHARED", command, sizeof(struct kb_req)},
};""",
        """   {"KB_IMPORT_SHARED", command, sizeof(struct kb_req)},
   {"KB_RES_UPLOAD", command, sizeof(struct kb_req)},
};""",
        "dispatch KB_RES_UPLOAD entry (shared-import)",
    )

    path.write_text(text)

    joined = path.read_text()
    for marker in ("KB_RES_UPLOAD", "kb_res_upload", "virgl_resource_lookup(q->reserved)"):
        require(marker in joined, f"patch marker missing: {marker}")
    print(f"patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
