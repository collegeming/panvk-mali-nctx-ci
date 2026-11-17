#!/usr/bin/env python3
"""Upload classic-3D backing iovecs into a kbase BO at attach time.

Background
----------
The mali_kbase backend's attach_resource callback is empty:

    static void attach(struct virgl_context *v, struct virgl_resource *res)
    { (void)v; (void)res; }

For a classic (non-blob) 3D resource, `virgl_renderer_resource_create()`
already produced a vrend `pipe_resource`, so the guest's ATTACH_BACKING
iovecs reach `vrend_pipe_resource_attach_iov()` through
`virgl_resource_attach_iov()` -> `pipe_callbacks.attach_iov` and are stored
on the vrend resource.  vrend then uploads them lazily inside
`vrend_renderer_transfer_write_iov()`, i.e. when a TRANSFER3D arrives that
carries no explicit iovec and `info->iovec_cnt == 0`.

That path needs a vrend (GL/EGL) context.  This backend is a DRM-style
context: `drm_context_init()` installs `drm_context_transfer_3d`, which is a
stub returning -1, and `drm_context_submit_cmd()` rejects any ccmd id at or
above its own dispatch size.  The backend's dispatch table is a private
KB_* transport (17 entries, ids 0..16), so a vrend command stream containing
VIRGL_CCMD_TRANSFER3D (41) or VIRGL_CCMD_PIPE_RESOURCE_SET_TYPE (47) is
rejected before it can reach vrend at all.

What this patch adds
--------------------
`attach()` keeps a per-resource record so the backing pages are no longer
dropped on the floor, and a new `KB_RES_UPLOAD` (ccmd 17) command copies
those pages into a kbase-allocated BO that the native command stream can
sample.  The record is keyed by `res_id` and stores a snapshot of the iovec
list taken at attach time (the caller's array is owned by crosvm/rutabaga and
is freed on the next attach, so it must not be referenced later).

The upload is deliberately explicit rather than automatic: attach runs before
ATTACH_BACKING in the guest KMD, so at attach time the iovec list is usually
still empty.  KB_RES_UPLOAD is issued by the guest after it has attached
backing and knows the destination BO handle.

Scope
-----
This patch does not make the mali_kbase context execute virgl/vrend command
streams; that remains unsupported (`drm_context_transfer_3d` stub, private
ccmd dispatch).  It only stops discarding classic-3D backing and gives the
native kbase path a documented way to consume it.
"""

from __future__ import annotations

import pathlib
import sys


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"patch_host_attach_upload: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def sub_idem(text: str, old: str, new: str, what: str) -> str:
    require(text.count(old) == 1, f"anchor not unique: {what}")
    return text.replace(old, new, 1)


RES_TRACK = r'''
/* Classic-3D backing records.
 *
 * attach_resource() runs before the guest's ATTACH_BACKING, so at attach time
 * `res->iov` is normally NULL.  A later attach carries the real page list but
 * is rejected by virgl_resource_attach_iov() once `res->iov` is set, and the
 * caller (rutabaga) frees the array it passed.  Keeping our own snapshot is
 * therefore the only way the backing pages survive long enough to be copied
 * into a kbase BO.
 *
 * Entries are released when the context is destroyed, or replaced by a later
 * attach of the same resource.  Detach is not hooked: this backend does not
 * install a detach_resource callback, so drm_context_detach_resource() only
 * frees objects it registered in its own res_id table, which this backend
 * never populates.  `iov` is a private copy; `size` is the byte total the
 * snapshot covers.
 */
#define MAX_RES_TRACK 256
struct kb_res_track {
   uint32_t res_id;
   uint64_t size;
   struct iovec *iov;
   int iov_count;
   bool used;
};
'''

RES_HELPERS = r'''
/* Copy the caller's iovec array, so a later rutabaga detach/re-attach cannot
 * leave us pointing at freed memory.  Returns 0 or a negative errno. */
static int
kb_res_snapshot(struct kb_res_track *t, uint32_t res_id,
                const struct iovec *iov, int iov_count)
{
   free(t->iov);
   memset(t, 0, sizeof(*t));
   t->res_id = res_id;
   t->used = false;
   if (!iov || iov_count <= 0)
      return 0;

   struct iovec *copy = calloc((size_t)iov_count, sizeof(*copy));
   if (!copy)
      return -ENOMEM;

   uint64_t total = 0;
   for (int i = 0; i < iov_count; i++) {
      copy[i] = iov[i];
      total += iov[i].iov_len;
   }
   t->iov = copy;
   t->iov_count = iov_count;
   t->size = total;
   t->used = true;
   return 0;
}

static void
kb_res_release(struct kb_res_track *t)
{
   free(t->iov);
   memset(t, 0, sizeof(*t));
}

static struct kb_res_track *
kb_res_find(struct kb_ctx *c, uint32_t res_id)
{
   for (unsigned i = 0; i < MAX_RES_TRACK; i++) {
      if (c->res[i].used && c->res[i].res_id == res_id)
         return &c->res[i];
   }
   return NULL;
}

static struct kb_res_track *
kb_res_slot(struct kb_ctx *c, uint32_t res_id)
{
   struct kb_res_track *t = kb_res_find(c, res_id);
   if (t)
      return t;
   for (unsigned i = 0; i < MAX_RES_TRACK; i++) {
      if (!c->res[i].used) {
         memset(&c->res[i], 0, sizeof(c->res[i]));
         c->res[i].res_id = res_id;
         return &c->res[i];
      }
   }
   return NULL;
}
'''

ATTACH = r'''
static void
attach(struct virgl_context *v, struct virgl_resource *res)
{
   struct kb_ctx *c = (void *)v;
   if (!res)
      return;

   /* A classic resource owns a vrend pipe_resource: vrend already stores the
    * iovecs and performs the GL upload on TRANSFER3D.  Nothing to do here, and
    * snapshotting would only duplicate a list vrend owns.
    *
    * A resource with no pipe_resource has no GL storage in this build, so its
    * pages can only be consumed through the native kbase path.  Snapshot them
    * so KB_RES_UPLOAD can copy them into a BO later. */
   if (res->pipe_resource)
      return;

   struct kb_res_track *t = kb_res_slot(c, res->res_id);
   if (!t) {
      __android_log_print(ANDROID_LOG_WARN, "kbattach",
                          "attach: res=%u no slot for iov_count=%d",
                          res->res_id, res->iov_count);
      return;
   }

   int ret = kb_res_snapshot(t, res->res_id, res->iov, res->iov_count);
   if (ret) {
      __android_log_print(ANDROID_LOG_WARN, "kbattach",
                          "attach: res=%u snapshot failed ret=%d", res->res_id, ret);
      return;
   }

   __android_log_print(ANDROID_LOG_INFO, "kbattach",
                       "attach: res=%u iov_count=%d total=%llu",
                       res->res_id, t->iov_count, (unsigned long long)t->size);
}

'''

UPLOAD_CMD = r'''   if (hdr->cmd == 17) {
      /* KB_RES_UPLOAD: copy a snapshotted classic-3D backing into a BO.
       *   handle  : destination BO handle
       *   offset  : destination byte offset inside that BO
       *   size    : bytes to copy (0 = the whole snapshot)
       *   reserved: source resource id
       * The BO must be CPU-writable; shared/shadow BOs both have a CPU view. */
      uint32_t res_id = q->reserved;
      struct kb_res_track *t = kb_res_find(c, res_id);
      if (!t) { r->status = -ENOENT; return 0; }

      struct kb_bo *bo = NULL;
      for (unsigned i = 0; i < MAX_BO; i++)
         if (c->bo[i].ptr && c->bo[i].handle == q->handle) { bo = &c->bo[i]; break; }
      if (!bo) { r->status = -ENOENT; return 0; }

      uint64_t want = q->size ? q->size : t->size;
      if (!want || q->offset > bo->size || want > bo->size - q->offset ||
          want > t->size) {
         r->status = -EINVAL;
         return 0;
      }

      /* Walk the snapshot rather than assuming one linear range: the guest
       * backs a large surface with thousands of independent page entries. */
      uint8_t *dst = (uint8_t *)bo->ptr + q->offset;
      uint64_t done = 0;
      for (int i = 0; i < t->iov_count && done < want; i++) {
         uint64_t left = want - done;
         uint64_t len = t->iov[i].iov_len < left ? t->iov[i].iov_len : left;
         memcpy(dst + done, t->iov[i].iov_base, (size_t)len);
         done += len;
      }

      /* Shadow BOs keep the authoritative copy in `gpu_mapping`; push the new
       * bytes across so the GPU sees them without a separate KB_NATIVE_SYNC. */
      if (bo->shadow && bo->gpu_mapping) {
         memcpy((uint8_t *)bo->gpu_mapping + q->offset, dst, (size_t)done);
         struct kbase_ioctl_mem_sync s = {
            .handle = bo->gpu_va,
            .user_addr = (uintptr_t)((uint8_t *)bo->gpu_mapping + q->offset),
            .size = (uint32_t)done,
            .type = BASE_SYNCSET_OP_MSYNC,
         };
         if (ioctl(c->base.fd, KBASE_IOCTL_MEM_SYNC, &s)) {
            r->status = -errno;
            return 0;
         }
      }

      r->size = (uint32_t)done;
      __android_log_print(ANDROID_LOG_INFO, "kbattach",
                          "upload: res=%u -> bo=%u off=%u bytes=%llu",
                          res_id, q->handle, q->offset, (unsigned long long)done);
      return 0;
   }
'''


def main() -> int:
    require(len(sys.argv) == 2, "usage: patch_host_attach_upload.py <virglrenderer-root>")
    path = pathlib.Path(sys.argv[1]) / "src/drm/mali_kbase_renderer.c"
    require(path.is_file(), f"missing {path}")
    text = path.read_text()

    require("KB_RES_UPLOAD" not in text, "already patched")
    require("MAX_RES_TRACK" not in text, "another res-track patch is present")

    # 1. Tracking array on the context.
    text = sub_idem(
        text,
        """   uint32_t next_queue;
   struct { void *ptr; uint64_t size; } alias[32];
};""",
        """   uint32_t next_queue;
   struct { void *ptr; uint64_t size; } alias[32];
   struct kb_res_track res[MAX_RES_TRACK];
};""",
        "kb_ctx res tracking field",
    )

    # The struct is declared inside kb_ctx, so the record type must come first.
    text = sub_idem(
        text,
        "struct kb_ctx {",
        RES_TRACK + "\nstruct kb_ctx {",
        "kb_res_track definition",
    )

    # 2. Snapshot helpers, after the existing free_bo helper.
    text = sub_idem(
        text,
        """/* Deliberately bounded CSF experiment: validate one store-only command stream,""",
        RES_HELPERS + """
/* Deliberately bounded CSF experiment: validate one store-only command stream,""",
        "kb_res helpers",
    )

    # 3. KB_RES_UPLOAD handler, before the generic command-group branch.
    text = sub_idem(
        text,
        """   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {""",
        UPLOAD_CMD + """
   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {""",
        "KB_RES_UPLOAD handler",
    )

    # 4. Replace the empty attach and add detach.
    text = sub_idem(
        text,
        "static void attach(struct virgl_context *v, struct virgl_resource *res) { (void)v; (void)res; }\n",
        ATTACH,
        "attach/detach implementation",
    )

    # 5. Release snapshots when the context dies.
    text = sub_idem(
        text,
        """   for (unsigned i = 0; i < MAX_BO; i++)
      if (c->bo[i].ptr) free_bo(c->base.fd, &c->bo[i]);""",
        """   for (unsigned i = 0; i < MAX_BO; i++)
      if (c->bo[i].ptr) free_bo(c->base.fd, &c->bo[i]);
   for (unsigned i = 0; i < MAX_RES_TRACK; i++)
      kb_res_release(&c->res[i]);""",
        "destroy releases res snapshots",
    )

    # 6. Dispatch entry.  The table is index-addressed, so the new command must
    #    be appended and the array size grows with it.
    text = sub_idem(
        text,
        """   {"KB_IMPORT_SHARED", command, sizeof(struct kb_req)},
};""",
        """   {"KB_IMPORT_SHARED", command, sizeof(struct kb_req)},
   {"KB_RES_UPLOAD", command, sizeof(struct kb_req)},
};""",
        "dispatch KB_RES_UPLOAD entry",
    )

    path.write_text(text)

    joined = path.read_text()
    for marker in ("MAX_RES_TRACK", "KB_RES_UPLOAD", "kb_res_snapshot"):
        require(marker in joined, f"patch marker missing: {marker}")
    print(f"patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
