"""Collapse per-range kbase MEM_SYNC requests into one request per BO.

Applied to a mesa checkout root (argument 1) by the panvk-mali-nctx-ci
workflow on branch perf/panvk-b0-batching. Route-b judgement B0: the
PanVK submission cost is dominated by host round trips, and kbase's
virtio transport answers every MEM_SYNC with one rpc() (cmd 12).

Current behaviour (baseline 57e402a2): kbase_kmod_flush_bo_map_syncs()
emits one rpc per pending range. A submission dirties many neighbouring
slices of the same BO (descriptor pools, ring buffer, syncobj cells), so
a frame issues ~45 of these at 2-3 ms each, and pan_kmod.c:366 already
merges only ranges with a zero gap. This patch merges at flush time:
same BO, same sync type, gap <= PANVK_KBASE_SYNC_MERGE_GAP.

Merging is idempotent cache maintenance on a larger range, it does not
reorder anything relative to the submit (the flush still happens before
emission) and it never mixes MSYNC with CSYNC.

Runtime knobs (no rebuild needed):
  PANVK_KBASE_SYNC_MERGE_GAP=<bytes>   0 = baseline, one rpc per range
                                       default = 1048576 (1 MiB)
"""
import pathlib
import sys

OLD = '''static int
kbase_kmod_flush_bo_map_syncs(struct pan_kmod_dev *dev)
{
   util_dynarray_foreach(&dev->pending_bo_syncs.array,
                         struct pan_kmod_deferred_bo_sync, sync) {
      struct kbase_kmod_bo *kbase_bo =
         container_of(sync->bo, struct kbase_kmod_bo, base);

      struct kbase_ioctl_mem_sync req = {
         .handle = kbase_bo->gpu_va,
         .user_addr = (uintptr_t)kbase_bo->cpu_ptr + sync->start,
         .size = sync->size,
         .type = sync->type == PAN_KMOD_BO_SYNC_CPU_CACHE_FLUSH
                    ? BASE_SYNCSET_OP_MSYNC
                    : BASE_SYNCSET_OP_CSYNC,
      };

      if (kbase_bo->virtio_handle) {
         if (kbv_bo_sync(dev->fd, kbase_bo->virtio_handle, sync->start, sync->size, req.type))
            return -1;
         continue;
      }
      if (pan_kmod_ioctl(dev->fd, KBASE_IOCTL_MEM_SYNC, &req)) {
         mesa_loge("kbase: KBASE_IOCTL_MEM_SYNC failed: %s", strerror(errno));
         return -1;
      }
   }

   return 0;
}
'''

NEW = '''/* Every MEM_SYNC on the virtio transport is one host round trip (cmd 12,
 * ~2-3 ms measured on the Mali-G2 virtio-gpu path) while the cache
 * maintenance it asks for costs microseconds. A submission queues several
 * neighbouring slices of the same BO, and the zero-gap merge in
 * pan_kmod_queue_bo_map_sync() does not catch them because descriptor and
 * ring slices are separated by free space.
 *
 * Collapse same-BO, same-type ranges that are at most
 * PANVK_KBASE_SYNC_MERGE_GAP bytes apart into a single request. The extra
 * coverage is bounded by the gap and is idempotent cache maintenance, so
 * the only cost is work the host would otherwise skip; the saving is one
 * full round trip per merged range.
 *
 * A merged-away entry is marked by size = 0. That is already the empty
 * request for both the virtio and the ioctl path, and the whole array is
 * cleared by pan_kmod_flush_bo_map_syncs_locked() right after this
 * callback returns.
 *
 * PANVK_KBASE_SYNC_MERGE_GAP=0 keeps the baseline one-request-per-range
 * behaviour, which makes the runtime A/B a single environment variable.
 */
static uint64_t
kbase_sync_merge_gap(void)
{
   static int64_t gap = -1;

   if (gap < 0) {
      const char *env = getenv("PANVK_KBASE_SYNC_MERGE_GAP");

      gap = env ? (int64_t)strtoull(env, NULL, 0) : 1024 * 1024;
      if (gap < 0)
         gap = 0;
   }

   return (uint64_t)gap;
}

static int
kbase_kmod_flush_bo_map_syncs(struct pan_kmod_dev *dev)
{
   const uint64_t merge_gap = kbase_sync_merge_gap();

   util_dynarray_foreach(&dev->pending_bo_syncs.array,
                         struct pan_kmod_deferred_bo_sync, sync) {
      if (!sync->size)
         continue;

      struct kbase_kmod_bo *kbase_bo =
         container_of(sync->bo, struct kbase_kmod_bo, base);
      uint64_t start = sync->start;
      uint64_t end = sync->start + sync->size;

      if (kbase_bo->virtio_handle && merge_gap) {
         bool merged = true;

         /* Repeat until the union stops growing: merging A and B can bring
          * C within the gap of the new range. */
         while (merged) {
            merged = false;

            util_dynarray_foreach(&dev->pending_bo_syncs.array,
                                  struct pan_kmod_deferred_bo_sync, other) {
               if (other == sync || !other->size || other->bo != sync->bo ||
                   other->type != sync->type)
                  continue;

               uint64_t other_end = other->start + other->size;

               if (other->start > end + merge_gap ||
                   start > other_end + merge_gap)
                  continue;

               start = MIN2(start, other->start);
               end = MAX2(end, other_end);
               other->size = 0;
               merged = true;
            }
         }
      }

      struct kbase_ioctl_mem_sync req = {
         .handle = kbase_bo->gpu_va,
         .user_addr = (uintptr_t)kbase_bo->cpu_ptr + start,
         .size = end - start,
         .type = sync->type == PAN_KMOD_BO_SYNC_CPU_CACHE_FLUSH
                    ? BASE_SYNCSET_OP_MSYNC
                    : BASE_SYNCSET_OP_CSYNC,
      };

      if (kbase_bo->virtio_handle) {
         if (kbv_bo_sync(dev->fd, kbase_bo->virtio_handle, start, end - start,
                         req.type))
            return -1;
      } else if (pan_kmod_ioctl(dev->fd, KBASE_IOCTL_MEM_SYNC, &req)) {
         mesa_loge("kbase: KBASE_IOCTL_MEM_SYNC failed: %s", strerror(errno));
         return -1;
      }

      sync->size = 0;
   }

   return 0;
}
'''

MARKER = "kbase_sync_merge_gap"


def read_text(path):
    # newline="" keeps the byte-for-byte line endings (mesa is LF-only) and
    # open() works on Python 3.8+, unlike Path.read_text(newline=...).
    with path.open("r", encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path, text):
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)


def main():
    root = pathlib.Path(sys.argv[1])
    path = root / "src/panfrost/lib/kmod/kbase_kmod.c"
    text = read_text(path)

    if MARKER in text:
        print("already patched:", path)
        return

    assert text.count(OLD) == 1, (str(path), "flush anchor not found exactly once",
                                  text.count(OLD))
    write_text(path, text.replace(OLD, NEW, 1))
    print("patched", path)

    out = read_text(path)
    assert out.count(MARKER) == 2, "unexpected marker count"
    assert out.count("PANVK_KBASE_SYNC_MERGE_GAP") == 3, \
        "unexpected knob count"
    print("verified: merge helper + env knob present")


if __name__ == "__main__":
    main()
