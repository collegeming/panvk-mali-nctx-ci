#!/usr/bin/env python3
"""Patch the virglrenderer Mali-kbase host backend for cross-context shared
blob import.

Target: MollySophia/virglrenderer@e58441fc6fa6ce73e6545882b43c8c807fe22a11
File:   src/drm/mali_kbase_renderer.c

The guest D3D UMD creates a shared blob through one virgl context (the
producer).  A different process/context later opens the same D3D resource and
the KMD queues RESOURCE_CREATE_BLOB for it.  The host backend's blob table is
per-context, so that lookup fails and the consumer samples zeros.  This patch
adds:

  * a process-global registry keyed by the (now globally unique) blob handle;
  * KB_IMPORT_SHARED (ccmd 16), which imports the producer's sealed memfd into
    the calling context and returns that context's kbase GPU VA;
  * import-aware lifetime (the origin unregisters, imported copies do not).

The guest sends ccmd 16 from vdrm_wddm.c before it creates the reused WDDM
allocation.  Keep the two sides in lockstep.
"""

from __future__ import annotations

import pathlib
import sys


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"patch_host_kbase_import: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def find_function(text: str, signature: str) -> tuple[int, int]:
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    fail(f"unterminated function: {signature}")


def replace_function(text: str, signature: str, replacement: str) -> str:
    start, end = find_function(text, signature)
    return text[:start] + replacement + text[end:]


def sub_idem(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        return text
    require(old in text, f"missing anchor: {what}")
    return text.replace(old, new, 1)


REGISTRY = r'''
/* Cross-context shared-blob registry.
 *
 * A D3D11 shared resource can be created in one process and opened in another.
 * The KMD forwards the producer's blob id to the consumer, but the renderer's
 * kb_bo table is per virgl context.  Keep the producer's sealed memfd in a
 * process-global table so KB_IMPORT_SHARED can import it into the consumer
 * context and hand back a GPU VA valid for that context's native submits.
 * Handles are allocated from one global counter: the id is part of the KMD's
 * blob metadata, so per-context counters could alias unrelated resources.
 */
#define MAX_SHARED 1024
struct kb_shared_entry {
   uint32_t handle, size;
   int memfd;
};
static struct kb_shared_entry kb_shared[MAX_SHARED];
static pthread_mutex_t kb_shared_lock = PTHREAD_MUTEX_INITIALIZER;
static uint32_t kb_next_handle;

static uint32_t
kb_alloc_handle(void)
{
   return __atomic_add_fetch(&kb_next_handle, 1, __ATOMIC_RELAXED);
}

static int
kb_shared_register(uint32_t handle, uint32_t size, int memfd)
{
   int fd = dup(memfd);
   if (fd < 0)
      return -errno;

   pthread_mutex_lock(&kb_shared_lock);
   for (unsigned i = 0; i < MAX_SHARED; i++) {
      if (kb_shared[i].memfd)
         continue;
      kb_shared[i] = (struct kb_shared_entry){
         .handle = handle, .size = size, .memfd = fd};
      pthread_mutex_unlock(&kb_shared_lock);
      return 0;
   }
   pthread_mutex_unlock(&kb_shared_lock);
   close(fd);
   return -ENOSPC;
}

static void
kb_shared_unregister(uint32_t handle)
{
   pthread_mutex_lock(&kb_shared_lock);
   for (unsigned i = 0; i < MAX_SHARED; i++) {
      if (kb_shared[i].memfd && kb_shared[i].handle == handle) {
         close(kb_shared[i].memfd);
         memset(&kb_shared[i], 0, sizeof(kb_shared[i]));
         break;
      }
   }
   pthread_mutex_unlock(&kb_shared_lock);
}

/* Returns a dup of the producer's memfd, or a negative errno. */
static int
kb_shared_dup(uint32_t handle, uint32_t *size)
{
   int fd = -ENOENT;

   pthread_mutex_lock(&kb_shared_lock);
   for (unsigned i = 0; i < MAX_SHARED; i++) {
      if (!kb_shared[i].memfd || kb_shared[i].handle != handle)
         continue;
      fd = dup(kb_shared[i].memfd);
      if (fd >= 0 && size)
         *size = kb_shared[i].size;
      else if (fd < 0)
         fd = -errno;
      break;
   }
   pthread_mutex_unlock(&kb_shared_lock);
   return fd;
}

/* Import an existing sealed memfd into this context.  Producer allocations
 * pass imported=false so their free_bo() retires the registry entry; consumers
 * pass imported=true and keep their private dup alive independently. */
static int
shared_import_fd(struct kb_ctx *c, struct kb_bo *bo, uint32_t size,
                 int src_fd, bool imported)
{
   int ret, memfd = -1, dma = -1, dev = -1;
   void *cpu = MAP_FAILED, *gpu = MAP_FAILED;

   memfd = dup(src_fd);
   if (memfd < 0)
      return -errno;
   dev = open("/dev/udmabuf", O_RDWR | O_CLOEXEC);
   if (dev < 0)
      goto fail;
   struct udmabuf_create u = {.memfd = memfd, .flags = UDMABUF_FLAGS_CLOEXEC,
                              .size = size};
   dma = ioctl(dev, UDMABUF_CREATE, &u);
   if (dma < 0)
      goto fail;
   close(dev);
   dev = -1;
   union kbase_ioctl_mem_import imp = {.in = {
      .flags = BASE_MEM_PROT_CPU_RD | BASE_MEM_PROT_CPU_WR |
               BASE_MEM_PROT_GPU_RD | BASE_MEM_PROT_GPU_WR |
               BASE_MEM_IMPORT_SHARED | BASE_MEM_COHERENT_LOCAL |
               BASE_MEM_CACHED_CPU,
      .phandle = (uintptr_t)&dma, .type = BASE_MEM_IMPORT_TYPE_UMM,
   }};
   ret = ioctl(c->base.fd, KBASE_IOCTL_MEM_IMPORT, &imp);
   if (ret && errno == ENOTTY)
      ret = ioctl(c->base.fd, KBASE_IOCTL_MEM_IMPORT_LEGACY, &imp);
   if (ret)
      goto fail;
   if (!(imp.out.flags & (BASE_MEM_SAME_VA | BASE_MEM_NEED_MMAP)) ||
       imp.out.va_pages != size / 4096) {
      struct kbase_ioctl_mem_free f = {.gpu_addr = imp.out.gpu_va};
      ioctl(c->base.fd, KBASE_IOCTL_MEM_FREE, &f);
      errno = ENOTSUP;
      goto fail;
   }
   gpu = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, c->base.fd,
              imp.out.gpu_va);
   if (gpu == MAP_FAILED) {
      int saved = errno;
      struct kbase_ioctl_mem_free f = {.gpu_addr = imp.out.gpu_va};
      ioctl(c->base.fd, KBASE_IOCTL_MEM_FREE, &f);
      errno = saved;
      goto fail;
   }
   cpu = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, memfd, 0);
   if (cpu == MAP_FAILED)
      goto fail;
   struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_RW};
   if (ioctl(dma, DMA_BUF_IOCTL_SYNC, &sync))
      goto fail;
   *bo = (struct kb_bo){.ptr = cpu, .gpu_mapping = gpu, .size = size,
                        .memfd = memfd, .dmabuf = dma, .shared = true,
                        .same_va = true, .gpu_va = (uintptr_t)gpu,
                        .imported = imported};
   return 0;

fail:
   ret = -errno;
   if (gpu != MAP_FAILED)
      munmap(gpu, size);
   if (cpu != MAP_FAILED)
      munmap(cpu, size);
   if (dma >= 0)
      close(dma);
   if (dev >= 0)
      close(dev);
   close(memfd);
   return ret;
}
'''


SHARED_ALLOC = r'''static int
shared_alloc(struct kb_ctx *c, struct kb_bo *bo, uint32_t size)
{
   int memfd = memfd_create("kbase-shared", MFD_CLOEXEC | MFD_ALLOW_SEALING);
   if (memfd < 0)
      return -errno;
   if (ftruncate(memfd, size) ||
       fcntl(memfd, F_ADD_SEALS, F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW)) {
      int saved = errno;
      close(memfd);
      return -saved;
   }
   int ret = shared_import_fd(c, bo, size, memfd, false);
   close(memfd);
   return ret;
}
'''


IMPORT_CMD = r'''   if (hdr->cmd == 16) {
      /* KB_IMPORT_SHARED: make another context's shared blob usable here.
       * The registry entry is keyed by the producer's blob id; the returned
       * value is this context's kbase GPU VA, which the guest's native submit
       * will use for the reused WDDM allocation. */
      uint32_t want = q->size, size = 0;
      int fd = kb_shared_dup(q->handle, &size);
      if (fd < 0) {
         r->status = fd;
         return 0;
      }
      if ((want && want != size) || !size || (size & 4095) ||
          size > c->limit || size > c->limit - c->allocated) {
         close(fd);
         r->status = -EINVAL;
         return 0;
      }
      unsigned slot;
      for (slot = 0; slot < c->bo_limit && c->bo[slot].ptr; slot++);
      if (slot == c->bo_limit) {
         close(fd);
         r->status = -ENOSPC;
         return 0;
      }
      int import_ret = shared_import_fd(c, &c->bo[slot], size, fd, true);
      close(fd);
      if (import_ret) {
         r->status = import_ret;
         return 0;
      }
      c->bo[slot].handle = q->handle;
      c->allocated += size;
      r->handle = q->handle;
      r->size = size;
      r->value = c->bo[slot].gpu_va;
      return 0;
   }
'''


def main() -> int:
    if len(sys.argv) != 2:
        fail("usage: patch_host_kbase_import.py <virglrenderer-root>")
    path = pathlib.Path(sys.argv[1]) / "src/drm/mali_kbase_renderer.c"
    text = path.read_text()

    if "KB_IMPORT_SHARED" in text:
        print("already patched")
        return 0

    text = sub_idem(text, "#include <poll.h>\n", "#include <poll.h>\n#include <pthread.h>\n", "pthread include")

    text = sub_idem(
        text,
        "   int memfd, dmabuf;\n   bool shared, exported;\n   bool shadow, same_va;\n",
        "   int memfd, dmabuf;\n   bool shared, exported;\n   bool shadow, same_va;\n   bool imported;\n",
        "kb_bo imported flag")

    start, end = find_function(text, "static int shared_alloc(struct kb_ctx *c, struct kb_bo *bo, uint32_t size)")
    text = text[:start] + REGISTRY + "\n" + SHARED_ALLOC + text[end:]

    text = sub_idem(
        text,
        "   munmap(bo->ptr, bo->size);\n   if (bo->shared) {\n",
        "   munmap(bo->ptr, bo->size);\n   if (bo->shared) {\n      if (!bo->imported)\n         kb_shared_unregister(bo->handle);\n",
        "free_bo unregister")

    text = sub_idem(
        text,
        "   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {\n",
        IMPORT_CMD + "\n   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {\n",
        "cmd 16")

    text = sub_idem(
        text,
        "      if (slot == c->bo_limit || c->next_handle == UINT32_MAX) { r->status = -ENOSPC; return 0; }\n",
        "      if (slot == c->bo_limit) { r->status = -ENOSPC; return 0; }\n",
        "global handle capacity")

    text = sub_idem(
        text,
        "         c->bo[slot].handle = ++c->next_handle;\n         c->allocated += q->size;\n         r->handle = c->next_handle; r->size = q->size;\n",
        "         c->bo[slot].handle = kb_alloc_handle();\n         c->allocated += q->size;\n         if (hdr->cmd == 5 || (hdr->cmd == 11 && q->reserved)) {\n            int reg_ret = kb_shared_register(c->bo[slot].handle,\n                                             c->bo[slot].size,\n                                             c->bo[slot].memfd);\n            if (reg_ret) {\n               c->allocated -= q->size;\n               free_bo(base->fd, &c->bo[slot]);\n               r->status = reg_ret;\n               return 0;\n            }\n         }\n         r->handle = c->bo[slot].handle; r->size = q->size;\n",
        "shared alloc registry")

    text = sub_idem(
        text,
        "      c->bo[slot] = (struct kb_bo){.ptr = ptr, .size = q->size, .handle = ++c->next_handle};\n      c->allocated += q->size;\n      r->handle = c->next_handle; r->size = q->size;\n",
        "      c->bo[slot] = (struct kb_bo){.ptr = ptr, .size = q->size, .handle = kb_alloc_handle()};\n      c->allocated += q->size;\n      r->handle = c->bo[slot].handle; r->size = q->size;\n",
        "regular alloc global handle")

    text = sub_idem(
        text,
        "      for (unsigned i = 0; i < MAX_BO; i++) {\n         struct kb_bo *bo = &c->bo[i];\n         if (!bo->ptr || !bo->shared || bo->handle != id) continue;\n         if (bo->exported || size != bo->size || flags != VIRGL_RENDERER_BLOB_FLAG_USE_MAPPABLE)\n",
        "      for (unsigned i = 0; i < MAX_BO; i++) {\n         struct kb_bo *bo = &c->bo[i];\n         if (!bo->ptr || !bo->shared || bo->handle != id || bo->exported) continue;\n         if (size != bo->size || flags != VIRGL_RENDERER_BLOB_FLAG_USE_MAPPABLE)\n",
        "blob prefer unexported")

    text = sub_idem(
        text,
        '   {"KB_ALIAS", command, sizeof(struct kb_req)},\n',
        '   {"KB_ALIAS", command, sizeof(struct kb_req)},\n   {"KB_IMPORT_SHARED", command, sizeof(struct kb_req)},\n',
        "dispatch entry")

    path.write_text(text)
    print(f"patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
