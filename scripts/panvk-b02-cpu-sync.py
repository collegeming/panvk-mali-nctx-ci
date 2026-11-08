"""Elide the seqno-cell cache invalidates that cannot observe new data.

Applied to a mesa checkout root (argument 1) by the panvk-mali-nctx-ci
workflow on branch perf/panvk-b02-cpu-sync. Route-b B0.2: the B0 device run
measured that kbase_kmod_flush_bo_map_syncs() is called only ~2 times per
frame, so merging inside it (B0) could not change anything. The 44.8
kbv_bo_sync round trips per frame instead come from kbase_cache_clean_range()
(one per emitted ring entry, panvk_vX_gpu_queue.c:731) and
kbase_cache_invalidate_range() in the completion-wait loop (:852).

This patch attacks the second one: the loop invalidates the seqno cell on
every iteration, and on the virtio transport each invalidate is a full host
round trip (mali_kbase_virtio.c rpc(), cmd 12). Only a wakeup from the KCPU
CQS wait or the CSF notification can mean the cell moved, so invalidate on
the first iteration, after a wakeup, and on a periodic recheck instead of
every iteration.

Safety argument: the seqno only grows and the CPU never writes the cell, so
a stale read is always lower than reality - it can delay a break, never fake
one. cell->error becomes visible at the next wakeup, and the recheck bounds
that otherwise.

Runtime knobs (all default OFF, so the A/B needs no rebuild):
  PANVK_KBASE_SEQNO_INVAL=1               enable the elision
  PANVK_KBASE_SEQNO_INVAL_RECHECK_MS=<n>  recheck period (default 50 ms)
"""
import pathlib
import sys

MARKER = "kbase_seqno_inval_elide"

PAIRS = [
    # 1. stdlib for getenv()/strtoull().
    ('''#include <inttypes.h>
#include <unistd.h>
''',
     '''#include <inttypes.h>
#include <stdlib.h>
#include <unistd.h>
'''),

    # 2. The knobs, declared next to the wait loop that uses them.
    ('''   kbase_kmod_csf_queue_kick(dev->kmod.dev, subq->kbase.ringbuf_dev);
}

static VkResult
kbase_subqueue_wait_seqno(struct panvk_gpu_queue *queue, uint32_t subqueue,
''',
     '''   kbase_kmod_csf_queue_kick(dev->kmod.dev, subq->kbase.ringbuf_dev);
}

/* Every read of the seqno cell needs a cache invalidate, and on the virtio
 * transport each invalidate is one host round trip (cmd 12, 2-4 ms measured).
 * The cell only changes when the GPU retires work, which is exactly what the
 * waits below report, so re-reading it on every iteration asks the same
 * question at full round-trip price.
 *
 * Default off: PANVK_KBASE_SEQNO_INVAL=1 enables the elision, everything else
 * keeps the baseline one-invalidate-per-iteration behaviour.
 */
static bool
kbase_seqno_inval_elide(void)
{
   static int elide = -1;

   if (elide < 0) {
      const char *env = getenv("PANVK_KBASE_SEQNO_INVAL");

      elide = env && strtoul(env, NULL, 0) != 0;
   }

   return elide != 0;
}

/* How long cell->error may stay unobserved while a wait keeps timing out.
 * Must stay well under the 500 ms re-kick period so a fault is still
 * reported by this loop rather than by the watchdog. */
static int64_t
kbase_seqno_inval_recheck_ns(void)
{
   static int64_t ns = -1;

   if (ns < 0) {
      const char *env = getenv("PANVK_KBASE_SEQNO_INVAL_RECHECK_MS");

      ns = env ? (int64_t)strtoull(env, NULL, 0) : 50;
      if (ns < 0)
         ns = 0;
      ns *= 1000000;
   }

   return ns;
}

static VkResult
kbase_subqueue_wait_seqno(struct panvk_gpu_queue *queue, uint32_t subqueue,
'''),

    # 3. Loop state.
    ('''   int64_t start = os_time_get_nano();
   int64_t last_kick = start;
''',
     '''   int64_t start = os_time_get_nano();
   int64_t last_kick = start;
   const bool elide_inval = kbase_seqno_inval_elide();
   const int64_t inval_recheck_ns = kbase_seqno_inval_recheck_ns();
   int64_t last_inval = start;
   bool stale = true;
'''),

    # 4. Timestamp before the read gate (was further down the loop body).
    ('''      uint32_t active = *(volatile uint32_t *)(output_page +
                                               CS_USER_IO_OUTPUT_CS_ACTIVE);
      if (prev_extract != extract || prev_seqno != cell->seqno) {
''',
     '''      uint32_t active = *(volatile uint32_t *)(output_page +
                                               CS_USER_IO_OUTPUT_CS_ACTIVE);
      int64_t now = os_time_get_nano();

      if (prev_extract != extract || prev_seqno != cell->seqno) {
'''),

    # 5. The gate itself.
    ('''      kbase_cache_invalidate_range((const void *)cell, kbase_seqno_stride());
      if (cell->seqno >= target_seqno ||
''',
     '''      /* A stale cell can only read low: the seqno is monotonic and the CPU
       * never writes it, so eliding an invalidate cannot fake completion, it
       * can only delay noticing it. The periodic recheck keeps cell->error
       * visible while the wait keeps timing out. */
      if (!elide_inval || stale || now - last_inval >= inval_recheck_ns) {
         kbase_cache_invalidate_range((const void *)cell,
                                      kbase_seqno_stride());
         last_inval = now;
         stale = false;
      }
      if (cell->seqno >= target_seqno ||
'''),

    # 6. Drop the now-duplicated timestamp.
    ('''      int64_t now = os_time_get_nano();

      /* Re-kick periodically: a kick can race with an in-flight group
''',
     '''      /* Re-kick periodically: a kick can race with an in-flight group
'''),

    # 7. A wakeup is what marks the cell stale again.
    ('''                       : -1;

      if (cqs_ret < 0) {
         int error_type = kbase_kmod_csf_wait_event(dev->kmod.dev, remaining);
''',
     '''                       : -1;

      /* Only a completed wait is evidence that the cell moved. A timeout
       * proves it did not reach the target, so the next read would repeat the
       * answer we already have. */
      stale = cqs_ret != 0;

      if (cqs_ret < 0) {
         int error_type = kbase_kmod_csf_wait_event(dev->kmod.dev, remaining);
'''),

    # 8. Same for the notification fallback.
    ('''         prev_error_type = error_type;
      }
   }
''',
     '''         prev_error_type = error_type;
         stale = stale || error_type != 0;
      }
   }
'''),
]


def main():
    root = pathlib.Path(sys.argv[1])
    path = root / "src/panfrost/vulkan/csf/panvk_vX_gpu_queue.c"
    with path.open("r", encoding="utf-8", newline="") as f:
        text = f.read()

    if MARKER in text:
        print("already patched:", path)
        return

    for old, new in PAIRS:
        assert text.count(old) == 1, (str(path), old.splitlines()[0][:70],
                                      text.count(old))
        text = text.replace(old, new, 1)

    assert text.count("PANVK_KBASE_SEQNO_INVAL") == 3, "unexpected knob count"
    assert text.count("kbase_seqno_inval_elide") == 2, "helper not wired"
    assert text.count("stale = ") == 4, "stale tracking incomplete"
    assert text.count("int64_t now = os_time_get_nano();") == 1, \
        "timestamp duplicated"

    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
    print("patched", path)
    print("verified: gate, knobs and stale tracking present")


if __name__ == "__main__":
    main()
