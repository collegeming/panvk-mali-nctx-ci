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
  * import-aware lifetime (the origin unregisters, imported copies do not);
  * pinning of exported registry entries so a consumer can still import after
    the producer released its resource;
  * KB_CAP_IMPORT_SHARED in KB_INFO so the guest can negotiate the new ccmd.

The guest sends ccmd 16 from vdrm_wddm.c before it creates the reused WDDM
allocation.  Keep the two sides in lockstep.
"""

from __future__ import annotations

import pathlib
import sys

PATCH_VERSION = 2
VERSION_MARKER = f"KB_IMPORT_SHARED_PATCH_VERSION {PATCH_VERSION}"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"patch_host_kbase_import: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def find_function(text: str, signature: str) -> tuple[int, int]:
    require(text.count(signature) == 1, f"anchor not unique: {signature}")
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
    require(text.count(old) == 1, f"anchor not unique: {what}")
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
 *
 * A producer entry is pinned the first time its blob is exported.  Pinned
 * entries survive the producer context so a consumer can still import after
 * the producer released its resource; they are reclaimed at process exit.
 * Unpinned entries are owned by the context that created them and are removed
 * by free_bo()/destroy().
 */
#define MAX_SHARED 1024
#define KB_CAP_IMPORT_SHARED (1u << 0)
#define KB_IMPORT_SHARED_PATCH_VERSION 2
struct kb_shared_entry {
   uint32_t handle, size;
   int memfd;
   struct kb_ctx *owner;
   bool used, pinned;
};
static struct kb_shared_entry kb_shared[MAX_SHARED];
static pthread_mutex_t kb_shared_lock = PTHREAD_MUTEX_INITIALIZER;
static uint64_t kb_next_handle;

static uint32_t
kb_alloc_handle(void)
{
   uint64_t h = __atomic_add_fetch(&kb_next_handle, 1, __ATOMIC_RELAXED);
   if (h == 0 || h > UINT32_MAX)
      return 0;
   return (uint32_t)h;
}

static int
kb_shared_register(struct kb_ctx *owner, uint32_t handle, uint32_t size,
                   int memfd)
{
   int fd = fcntl(memfd, F_DUPFD_CLOEXEC, 3);
   if (fd < 0)
      return -errno;

   pthread_mutex_lock(&kb_shared_lock);
   for (unsigned i = 0; i < MAX_SHARED; i++) {
      if (kb_shared[i].used)
         continue;
      kb_shared[i] = (struct kb_shared_entry){
         .handle = handle, .size = size, .memfd = fd,
         .owner = owner, .used = true, .pinned = false};
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
      if (!kb_shared[i].used || kb_shared[i].pinned ||
          kb_shared[i].handle != handle)
         continue;
      close(kb_shared[i].memfd);
      memset(&kb_shared[i], 0, sizeof(kb_shared[i]));
      break;
   }
   pthread_mutex_unlock(&kb_shared_lock);
}

static void
kb_shared_unregister_owner(struct kb_ctx *owner)
{
   pthread_mutex_lock(&kb_shared_lock);
   for (unsigned i = 0; i < MAX_SHARED; i++) {
      if (!kb_shared[i].used || kb_shared[i].pinned ||
          kb_shared[i].owner != owner)
         continue;
      close(kb_shared[i].memfd);
      memset(&kb_shared[i], 0, sizeof(kb_shared[i]));
   }
   pthread_mutex_unlock(&kb_shared_lock);
}

static void
kb_shared_pin(uint32_t handle)
{
   pthread_mutex_lock(&kb_shared_lock);
   for (unsigned i = 0; i < MAX_SHARED; i++) {
      if (!kb_shared[i].used || kb_shared[i].handle != handle)
         continue;
      kb_shared[i].pinned = true;
      kb_shared[i].owner = NULL;
      break;
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
      if (!kb_shared[i].used || kb_shared[i].handle != handle)
         continue;
      fd = fcntl(kb_shared[i].memfd, F_DUPFD_CLOEXEC, 3);
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
   bool have_import = false, mem_same_va = false;
   void *cpu = MAP_FAILED, *gpu = MAP_FAILED;

   memfd = fcntl(src_fd, F_DUPFD_CLOEXEC, 3);
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
   have_import = true;
   mem_same_va = !!(imp.out.flags & BASE_MEM_SAME_VA);
   if (!(imp.out.flags & (BASE_MEM_SAME_VA | BASE_MEM_NEED_MMAP)) ||
       imp.out.va_pages != size / 4096) {
      errno = ENOTSUP;
      goto fail;
   }
   gpu = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, c->base.fd,
              imp.out.gpu_va);
   if (gpu == MAP_FAILED)
      goto fail;
   cpu = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, memfd, 0);
   if (cpu == MAP_FAILED)
      goto fail;
   struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_RW};
   if (ioctl(dma, DMA_BUF_IOCTL_SYNC, &sync))
      goto fail;
   *bo = (struct kb_bo){.ptr = cpu, .gpu_mapping = gpu, .size = size,
                        .memfd = memfd, .dmabuf = dma, .shared = true,
                        .same_va = mem_same_va,
                        .gpu_va = mem_same_va ? (uintptr_t)gpu : imp.out.gpu_va,
                        .imported = imported};
   return 0;

fail:
   ret = -errno;
   if (gpu != MAP_FAILED)
      munmap(gpu, size);
   if (cpu != MAP_FAILED)
      munmap(cpu, size);
   if (have_import && (!mem_same_va || gpu == MAP_FAILED)) {
      struct kbase_ioctl_mem_free f = {.gpu_addr = imp.out.gpu_va};
      ioctl(c->base.fd, KBASE_IOCTL_MEM_FREE, &f);
   }
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
       * will use for the reused WDDM allocation.
       *
       * Only an imported copy satisfies this request.  A producer-only entry
       * with the same handle must not be reused here: the guest's later cmd 4
       * for the consumer reference would then target the producer instead of
       * the imported copy. */
      for (unsigned i = 0; i < MAX_BO; i++) {
         struct kb_bo *bo = &c->bo[i];
         if (!bo->ptr || !bo->imported || bo->handle != q->handle)
            continue;
         if (q->size && q->size != bo->size) {
            r->status = -EINVAL;
            return 0;
         }
         r->handle = bo->handle;
         r->size = bo->size;
         r->value = bo->gpu_va;
         return 0;
      }
      uint32_t want = q->size, size = 0;
      int fd = kb_shared_dup(q->handle, &size);
      if (fd < 0) {
         r->status = fd;
         return 0;
      }
      if ((want && want != size) || !size || (size & 4095) ||
          size > c->limit || c->allocated > c->limit ||
          size > c->limit - c->allocated) {
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

    if VERSION_MARKER in text:
        print("already patched")
        return 0
    require("KB_IMPORT_SHARED" not in text,
            "an older KB_IMPORT_SHARED patch is present; regenerate from the original source")

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
        "   munmap(bo->ptr, bo->size);\n"
        "   if (bo->shared) {\n"
        "      munmap(bo->gpu_mapping, bo->size);\n"
        "      if (bo->dmabuf >= 0) close(bo->dmabuf);\n"
        "      close(bo->memfd);\n"
        "      if (bo->shadow && !bo->same_va) {\n"
        "         struct kbase_ioctl_mem_free f = {.gpu_addr = bo->gpu_va};\n"
        "         ioctl(fd, KBASE_IOCTL_MEM_FREE, &f);\n"
        "      }\n"
        "   }\n",
        "   munmap(bo->ptr, bo->size);\n"
        "   if (bo->shared) {\n"
        "      if (!bo->imported && !bo->exported)\n"
        "         kb_shared_unregister(bo->handle);\n"
        "      munmap(bo->gpu_mapping, bo->size);\n"
        "      if (bo->dmabuf >= 0) close(bo->dmabuf);\n"
        "      close(bo->memfd);\n"
        "      if (!bo->same_va) {\n"
        "         struct kbase_ioctl_mem_free f = {.gpu_addr = bo->gpu_va};\n"
        "         ioctl(fd, KBASE_IOCTL_MEM_FREE, &f);\n"
        "      }\n"
        "   }\n",
        "free_bo unregister")

    text = sub_idem(
        text,
        "   struct kb_bo *bo = NULL;\n"
        "   for (unsigned i = 0; i < MAX_BO; i++)\n"
        "      if (c->bo[i].ptr && c->bo[i].handle == q->handle) { bo = &c->bo[i]; break; }\n"
        "   if (!bo) { r->status = -ENOENT; return 0; }\n",
        "   struct kb_bo *bo = NULL, *imported = NULL;\n"
        "   /* A same-context producer and consumer import share one handle.\n"
        "    * Prefer the imported copy so cmd 4 retires the consumer reference\n"
        "    * and leaves the producer entry (and registry pin) alone. */\n"
        "   for (unsigned i = 0; i < MAX_BO; i++) {\n"
        "      if (!c->bo[i].ptr || c->bo[i].handle != q->handle)\n"
        "         continue;\n"
        "      if (!bo)\n"
        "         bo = &c->bo[i];\n"
        "      if (c->bo[i].imported) {\n"
        "         imported = &c->bo[i];\n"
        "         break;\n"
        "      }\n"
        "   }\n"
        "   if (imported)\n"
        "      bo = imported;\n"
        "   if (!bo) { r->status = -ENOENT; return 0; }\n",
        "prefer imported on handle lookup")

    text = sub_idem(
        text,
        "   if (hdr->cmd == 0) { r->value = c->gpu_id; r->size = c->allocated; return 0; }\n",
        "   if (hdr->cmd == 0) {\n"
        "      r->handle = KB_CAP_IMPORT_SHARED;\n"
        "      r->value = c->gpu_id;\n"
        "      r->size = c->allocated;\n"
        "      return 0;\n"
        "   }\n",
        "KB_INFO capability")

    text = sub_idem(
        text,
        "   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {\n",
        IMPORT_CMD + "\n   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {\n",
        "cmd 16")

    text = sub_idem(
        text,
        "      if (!q->size || q->size > c->limit || (q->size & 4095) ||\n"
        "          q->size > c->limit - c->allocated) { r->status = -EINVAL; return 0; }\n",
        "      if (!q->size || q->size > c->limit || c->allocated > c->limit ||\n"
        "          (q->size & 4095) || q->size > c->limit - c->allocated) { r->status = -EINVAL; return 0; }\n",
        "alloc budget")

    text = sub_idem(
        text,
        "      if (slot == c->bo_limit || c->next_handle == UINT32_MAX) { r->status = -ENOSPC; return 0; }\n",
        "      if (slot == c->bo_limit) { r->status = -ENOSPC; return 0; }\n"
        "      uint32_t new_handle = kb_alloc_handle();\n"
        "      if (!new_handle) { r->status = -ENOSPC; return 0; }\n",
        "global handle capacity")

    text = sub_idem(
        text,
        "         c->bo[slot].handle = ++c->next_handle;\n"
        "         c->allocated += q->size;\n"
        "         r->handle = c->next_handle; r->size = q->size;\n",
        "         c->bo[slot].handle = new_handle;\n"
        "         c->allocated += q->size;\n"
        "         if (hdr->cmd == 5 || (hdr->cmd == 11 && q->reserved)) {\n"
        "            int reg_ret = kb_shared_register(c, new_handle,\n"
        "                                             c->bo[slot].size,\n"
        "                                             c->bo[slot].memfd);\n"
        "            if (reg_ret) {\n"
        "               c->allocated -= q->size;\n"
        "               free_bo(base->fd, &c->bo[slot]);\n"
        "               r->status = reg_ret;\n"
        "               return 0;\n"
        "            }\n"
        "         }\n"
        "         r->handle = c->bo[slot].handle; r->size = q->size;\n",
        "shared alloc registry")

    text = sub_idem(
        text,
        "      c->bo[slot] = (struct kb_bo){.ptr = ptr, .size = q->size, .handle = ++c->next_handle};\n"
        "      c->allocated += q->size;\n"
        "      r->handle = c->next_handle; r->size = q->size;\n",
        "      c->bo[slot] = (struct kb_bo){.ptr = ptr, .size = q->size, .handle = new_handle};\n"
        "      c->allocated += q->size;\n"
        "      r->handle = c->bo[slot].handle; r->size = q->size;\n",
        "regular alloc global handle")

    text = sub_idem(
        text,
        "      for (unsigned i = 0; i < MAX_BO; i++) {\n"
        "         struct kb_bo *bo = &c->bo[i];\n"
        "         if (!bo->ptr || !bo->shared || bo->handle != id) continue;\n"
        "         if (bo->exported || size != bo->size || flags != VIRGL_RENDERER_BLOB_FLAG_USE_MAPPABLE)\n"
        "            return -EINVAL;\n"
        "         int fd = fcntl(bo->memfd, F_DUPFD_CLOEXEC, 3);\n"
        "         if (fd < 0) return -errno;\n"
        "         out->type = VIRGL_RESOURCE_FD_SHM; out->u.fd = fd;\n"
        "         out->map_info = VIRGL_RENDERER_MAP_CACHE_CACHED;\n"
        "         bo->exported = true; return 0;\n"
        "      }\n"
        "      return -ENOENT;\n",
        "      struct kb_bo *bo = NULL;\n"
        "      bool saw_match = false;\n"
        "      for (unsigned i = 0; i < MAX_BO; i++) {\n"
        "         struct kb_bo *candidate = &c->bo[i];\n"
        "         if (!candidate->ptr || !candidate->shared ||\n"
        "             candidate->handle != id)\n"
        "            continue;\n"
        "         saw_match = true;\n"
        "         if (size != candidate->size ||\n"
        "             flags != VIRGL_RENDERER_BLOB_FLAG_USE_MAPPABLE)\n"
        "            continue;\n"
        "         /* In a context that holds both the producer and a consumer\n"
        "          * import, the imported copy is the one this resource belongs to.\n"
        "          * Prefer it, and among equals prefer a not-yet-exported copy. */\n"
        "         if (!bo ||\n"
        "             (candidate->imported && !bo->imported) ||\n"
        "             (candidate->imported == bo->imported &&\n"
        "              !candidate->exported && bo->exported))\n"
        "            bo = candidate;\n"
        "      }\n"
        "      if (!bo)\n"
        "         return saw_match ? -EINVAL : -ENOENT;\n"
        "      int fd = fcntl(bo->memfd, F_DUPFD_CLOEXEC, 3);\n"
        "      if (fd < 0) return -errno;\n"
        "      out->type = VIRGL_RESOURCE_FD_SHM; out->u.fd = fd;\n"
        "      out->map_info = VIRGL_RENDERER_MAP_CACHE_CACHED;\n"
        "      kb_shared_pin(bo->handle);\n"
        "      bo->exported = true; return 0;\n",
        "blob multi-export prefer imported")

    text = sub_idem(
        text,
        "   for (unsigned i = 0; i < MAX_BO; i++)\n"
        "      if (c->bo[i].ptr) free_bo(c->base.fd, &c->bo[i]);\n"
        "   munmap(c->tracking, 4096);\n",
        "   for (unsigned i = 0; i < MAX_BO; i++)\n"
        "      if (c->bo[i].ptr) free_bo(c->base.fd, &c->bo[i]);\n"
        "   kb_shared_unregister_owner(c);\n"
        "   munmap(c->tracking, 4096);\n",
        "destroy owner cleanup")

    text = sub_idem(
        text,
        '   {"KB_ALIAS", command, sizeof(struct kb_req)},\n',
        '   {"KB_ALIAS", command, sizeof(struct kb_req)},\n'
        '   {"KB_IMPORT_SHARED", command, sizeof(struct kb_req)},\n',
        "dispatch entry")

    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
