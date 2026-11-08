#!/usr/bin/env python3
"""E1 patch: env-gated host CSYNC experiment for the Mali kbase backend.

Applied to the extracted MollySophia/virglrenderer tree *after*
scripts/host-ndk/patch_host_probe.py. All behaviour is opt-in at run time; a
build with none of the variables set issues exactly the same ioctls as the
baseline library.

  PANVK_HOST_CSYNC_SKIP_SHADOW=1
      cmd 12 (KB_NATIVE_SYNC) shadow BO + CSYNC: keep the 64 B
      KBASE_IOCTL_MEM_SYNC and the memcpy back to the guest-visible page, skip
      the global shared_sync(DMA_BUF_SYNC_START) loop.
  PANVK_HOST_KICK_SYNC_OFF=1
      cmd 14/op2 (kick): skip shared_sync(DMA_BUF_SYNC_END); the ring memcpy,
      MEM_SYNC, CS_INSERT write and CS_QUEUE_KICK are unchanged.
  PANVK_HOST_CSYNC_DIAG=1
      Aggregate counters on stderr: command counts, skipped syncs, shared BO
      count/bytes, and the per-phase microseconds (MEM_SYNC, memcpy+return,
      shared_sync). One line per 128 events plus one at context destroy.

Any other value (including unset) leaves the gate off. Anchors are asserted so
an unexpected source tree fails the build instead of silently shipping a
library without the experiment.
"""

import sys
from pathlib import Path


def rep(text, old, new):
    assert text.count(old) == 1, (
        'anchor count %d: %s' % (text.count(old), old[:100]))
    return text.replace(old, new, 1)


def main():
    root = Path(sys.argv[1])
    mali = root / 'src/drm/mali_kbase_renderer.c'
    queue = root / 'src/drm/kbase_transport_queue.h'

    s = mali.read_text()
    assert '#include <stdio.h>' in s, 'patch_host_probe.py must run first'

    # --- 1. per-context statistics record -----------------------------------
    s = rep(s, '''struct kb_ctx {
   struct drm_context base;''', '''/* PANVK host CSYNC experiment record (default off). */
struct kb_sync_stat {
   uint64_t events, csync, csync_skip, csync_us, csync_mem_us, csync_shared_us;
   uint64_t csync_max_shared_us, last_mem_us, last_shared_us;
   uint64_t kick, kick_skip, kick_us, kick_shared_us, kick_max_shared_us;
   uint64_t last_kick_us;
   uint32_t shared_bo, shared_bytes;
};
struct kb_ctx {
   struct drm_context base;''')

    # --- 2. gate flags and counters inside the context ----------------------
    s = rep(s, '''   uint8_t groups[16];
   uint32_t n_groups;
};''', '''   uint8_t groups[16];
   uint32_t n_groups;
   /* PANVK host CSYNC experiment (default disabled). */
   bool skip_shadow_csync, kick_sync_off, sync_diag;
   struct kb_sync_stat sync;
};''')

    # --- 3. helpers, after shared_sync() so the queue header can use them ---
    s = rep(s, '''static int shared_sync(struct kb_ctx *c, uint64_t operation)
{
   for (unsigned i = 0; i < MAX_BO; i++) {
      if (!c->bo[i].ptr || !c->bo[i].shared || c->bo[i].shadow) continue;
      struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_RW | operation};
      if (ioctl(c->bo[i].dmabuf, DMA_BUF_IOCTL_SYNC, &sync)) return -errno;
   }
   return 0;
}''', '''static int shared_sync(struct kb_ctx *c, uint64_t operation)
{
   for (unsigned i = 0; i < MAX_BO; i++) {
      if (!c->bo[i].ptr || !c->bo[i].shared || c->bo[i].shadow) continue;
      struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_RW | operation};
      if (ioctl(c->bo[i].dmabuf, DMA_BUF_IOCTL_SYNC, &sync)) return -errno;
   }
   return 0;
}
static uint64_t sync_now_us(void)
{
   struct timespec t;
   clock_gettime(CLOCK_MONOTONIC, &t);
   return (uint64_t)t.tv_sec * 1000000u + (uint64_t)t.tv_nsec / 1000u;
}
static void shared_scan(struct kb_ctx *c, uint32_t *nbo, uint64_t *nbytes)
{
   uint32_t n = 0;
   uint64_t b = 0;
   for (unsigned i = 0; i < MAX_BO; i++) {
      if (!c->bo[i].ptr || !c->bo[i].shared || c->bo[i].shadow) continue;
      n++; b += c->bo[i].size;
   }
   if (nbo) *nbo = n;
   if (nbytes) *nbytes = b;
}
static void sync_report(struct kb_ctx *c, const char *why)
{
   const struct kb_sync_stat *s = &c->sync;
   if (!c->sync_diag) return;
   fprintf(stderr, "panvk-host-csync: fd=%d why=%s skip_shadow=%d kick_off=%d "
           "shared_bo=%u shared_bytes=%u csync=%llu csync_skip=%llu csync_us=%llu "
           "csync_mem_us=%llu csync_shared_us=%llu csync_max_shared_us=%llu "
           "csync_last_mem_us=%llu csync_last_shared_us=%llu kick=%llu kick_skip=%llu "
           "kick_us=%llu kick_shared_us=%llu kick_max_shared_us=%llu kick_last_us=%llu\\n",
           c->base.fd, why, c->skip_shadow_csync, c->kick_sync_off,
           s->shared_bo, s->shared_bytes,
           (unsigned long long)s->csync, (unsigned long long)s->csync_skip,
           (unsigned long long)s->csync_us, (unsigned long long)s->csync_mem_us,
           (unsigned long long)s->csync_shared_us,
           (unsigned long long)s->csync_max_shared_us,
           (unsigned long long)s->last_mem_us, (unsigned long long)s->last_shared_us,
           (unsigned long long)s->kick, (unsigned long long)s->kick_skip,
           (unsigned long long)s->kick_us, (unsigned long long)s->kick_shared_us,
           (unsigned long long)s->kick_max_shared_us, (unsigned long long)s->last_kick_us);
}
static void sync_maybe_report(struct kb_ctx *c)
{
   c->sync.events++;
   if (c->sync_diag && !(c->sync.events & 127)) sync_report(c, "periodic");
}
static void sync_inventory(struct kb_ctx *c)
{
   if (!c->sync_diag) return;
   for (unsigned i = 0; i < c->bo_limit; i++) {
      if (!c->bo[i].ptr || !c->bo[i].shared) continue;
      fprintf(stderr, "panvk-host-csync: bo[%u] handle=%u size=%u shadow=%d exported=%d\\n",
              i, c->bo[i].handle, c->bo[i].size, c->bo[i].shadow, c->bo[i].exported);
   }
}''')

    # --- 4. cmd 12 shadow CSYNC: gate + per-phase timing --------------------
    s = rep(s, '''      if (ioctl(base->fd, KBASE_IOCTL_MEM_SYNC, &s)) r->status = -errno;
      else if (q->data[0] == BASE_SYNCSET_OP_CSYNC) memcpy(cpu, gpu, q->size);
      if (!r->status && q->data[0] == BASE_SYNCSET_OP_CSYNC)
         r->status = shared_sync(c, DMA_BUF_SYNC_START);
      return 0;''', '''      uint64_t t0 = c->sync_diag ? sync_now_us() : 0;
      int io_ret = ioctl(base->fd, KBASE_IOCTL_MEM_SYNC, &s);
      uint64_t t1 = c->sync_diag ? sync_now_us() : 0;
      if (io_ret) r->status = -errno;
      else if (q->data[0] == BASE_SYNCSET_OP_CSYNC) memcpy(cpu, gpu, q->size);
      if (q->data[0] == BASE_SYNCSET_OP_CSYNC) {
         bool skipped = false;
         uint64_t t2 = c->sync_diag ? sync_now_us() : 0;
         if (!r->status) {
            if (c->skip_shadow_csync) skipped = true;
            else r->status = shared_sync(c, DMA_BUF_SYNC_START);
         }
         uint64_t t3 = c->sync_diag ? sync_now_us() : 0;
         if (c->sync_diag) {
            uint32_t nbo = 0;
            uint64_t nbytes = 0;
            shared_scan(c, &nbo, &nbytes);
            c->sync.csync++;
            c->sync.csync_skip += skipped ? 1 : 0;
            c->sync.csync_us += t3 - t0;
            c->sync.csync_mem_us += t1 - t0;
            c->sync.csync_shared_us += t3 - t2;
            c->sync.last_mem_us = t1 - t0;
            c->sync.last_shared_us = t3 - t2;
            if (t3 - t2 > c->sync.csync_max_shared_us)
               c->sync.csync_max_shared_us = t3 - t2;
            c->sync.shared_bo = nbo;
            c->sync.shared_bytes = (uint32_t)(nbytes > 0xffffffffu ? 0xffffffffu : nbytes);
            sync_maybe_report(c);
         }
      }
      return 0;''')

    mali.write_text(s)

    # --- 5. kick: gate the shared_sync(END) --------------------------------
    q = queue.read_text()
    q = rep(q, '''      if (ioctl(c->base.fd, KBASE_IOCTL_MEM_SYNC, &s)) return -errno;
      int sync_ret = shared_sync(c, DMA_BUF_SYNC_END);
      if (sync_ret) return sync_ret;''', '''      if (ioctl(c->base.fd, KBASE_IOCTL_MEM_SYNC, &s)) return -errno;
      uint64_t kt0 = c->sync_diag ? sync_now_us() : 0;
      int sync_ret = 0;
      if (!c->kick_sync_off) sync_ret = shared_sync(c, DMA_BUF_SYNC_END);
      uint64_t kt1 = c->sync_diag ? sync_now_us() : 0;
      if (c->sync_diag) {
         uint32_t nbo = 0;
         uint64_t nbytes = 0;
         shared_scan(c, &nbo, &nbytes);
         c->sync.kick++;
         c->sync.kick_skip += c->kick_sync_off ? 1 : 0;
         c->sync.kick_us += kt1 - kt0;
         c->sync.kick_shared_us += kt1 - kt0;
         c->sync.last_kick_us = kt1 - kt0;
         if (kt1 - kt0 > c->sync.kick_max_shared_us)
            c->sync.kick_max_shared_us = kt1 - kt0;
         c->sync.shared_bo = nbo;
         c->sync.shared_bytes = (uint32_t)(nbytes > 0xffffffffu ? 0xffffffffu : nbytes);
         sync_maybe_report(c);
      }
      if (sync_ret) return sync_ret;''')
    queue.write_text(q)

    # --- 6. read the gates once, at context creation -----------------------
    s = mali.read_text()
    s = rep(s, '''   const char *budget = getenv("VKR_TEST_KBASE_MAX_MB");
   if (budget) {
      char *end;
      unsigned long mb = strtoul(budget, &end, 10);
      if (*end || mb < 64 || mb > 1024) goto fail;
      c->limit = mb * 1024u * 1024u;
   }''', '''   const char *budget = getenv("VKR_TEST_KBASE_MAX_MB");
   if (budget) {
      char *end;
      unsigned long mb = strtoul(budget, &end, 10);
      if (*end || mb < 64 || mb > 1024) goto fail;
      c->limit = mb * 1024u * 1024u;
   }
   /* PANVK host CSYNC experiment gates: default off, read once per context.
    * Only the exact string "1" enables a gate. */
   const char *csync_gate = getenv("PANVK_HOST_CSYNC_SKIP_SHADOW");
   c->skip_shadow_csync = csync_gate && csync_gate[0] == '1' && !csync_gate[1];
   const char *kick_gate = getenv("PANVK_HOST_KICK_SYNC_OFF");
   c->kick_sync_off = kick_gate && kick_gate[0] == '1' && !kick_gate[1];
   const char *diag_gate = getenv("PANVK_HOST_CSYNC_DIAG");
   c->sync_diag = diag_gate && diag_gate[0] == '1' && !diag_gate[1];
   if (c->skip_shadow_csync || c->kick_sync_off || c->sync_diag)
      fprintf(stderr, "panvk-host-csync: fd=%d gates skip_shadow_csync=%d "
              "kick_sync_off=%d diag=%d\\n",
              fd, c->skip_shadow_csync, c->kick_sync_off, c->sync_diag);''')

    # --- 7. final report and BO inventory at context destroy --------------
    s = rep(s, '''static void destroy(struct virgl_context *v)
{
   struct kb_ctx *c = (void *)v;
   for (unsigned i = 0; i < 32; i++)
      if (c->queue[i].io) queue_free(c, &c->queue[i]);''', '''static void destroy(struct virgl_context *v)
{
   struct kb_ctx *c = (void *)v;
   for (unsigned i = 0; i < 32; i++)
      if (c->queue[i].io) queue_free(c, &c->queue[i]);
   if (c->sync_diag) { sync_report(c, "destroy"); sync_inventory(c); }''')
    mali.write_text(s)

    print('host CSYNC experiment patched')


if __name__ == '__main__':
    main()
