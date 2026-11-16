#!/usr/bin/env python3
"""Minimal Mali-kbase host patch for cross-context shared blob import.

Unlike patch_host_kbase_import.py this deliberately leaves the producer's
shared_alloc(), free_bo(), blob() and per-context handle numbering untouched.
It only adds:

  * a process-global registry of shared producer memfds;
  * KB_IMPORT_SHARED (ccmd 16), which imports a registered memfd into the
    calling context and returns that context's kbase VA;
  * the KB_INFO capability bit used by the Windows ICD.

This is the bring-up variant: registry entries are retained for the life of
the renderer process.  Once C1/C2 is proven, the full patch can add ownership,
free-on-detach and multi-export refinements without changing the wire ABI.
"""

from __future__ import annotations

import pathlib
import sys


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"patch_host_min_import: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def sub_idem(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        return text
    require(old in text, f"missing anchor: {what}")
    return text.replace(old, new, 1)


REGISTRY = r'''
/* Minimal cross-context shared-blob registry.  Producer contexts register the
 * sealed memfd that backs a cmd5/cmd11 shared allocation.  KB_IMPORT_SHARED
 * duplicates it into the consumer's kbase context and returns the mmap VA
 * that this context's native commands must use.  Entries live until process
 * exit in this bring-up variant. */
#define MAX_SHARED 1024
struct kb_shared_entry {
   uint32_t handle, size;
   int memfd;
   bool used;
};
static struct kb_shared_entry kb_shared[MAX_SHARED];
static pthread_mutex_t kb_shared_lock = PTHREAD_MUTEX_INITIALIZER;

static int
kb_shared_register(uint32_t handle, uint32_t size, int memfd)
{
   int fd = fcntl(memfd, F_DUPFD_CLOEXEC, 3);
   if (fd < 0)
      return -errno;

   pthread_mutex_lock(&kb_shared_lock);
   for (unsigned i = 0; i < MAX_SHARED; i++) {
      if (kb_shared[i].used)
         continue;
      kb_shared[i] = (struct kb_shared_entry){
         .handle = handle, .size = size, .memfd = fd, .used = true};
      pthread_mutex_unlock(&kb_shared_lock);
      return 0;
   }
   pthread_mutex_unlock(&kb_shared_lock);
   close(fd);
   return -ENOSPC;
}

/* Returns a CLOEXEC dup of the producer's memfd, or a negative errno. */
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

static int
shared_import_fd(struct kb_ctx *c, struct kb_bo *bo, uint32_t size,
                 int src_fd, bool imported)
{
   int ret, memfd = -1, dma = -1, dev = -1, have_import = 0;
   void *cpu = MAP_FAILED, *gpu = MAP_FAILED;
   union kbase_ioctl_mem_import imp = {0};

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
   imp = (union kbase_ioctl_mem_import){.in = {
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
   have_import = 1;
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
                        .same_va = true, .gpu_va = (uintptr_t)gpu,
                        .imported = imported};
   return 0;

fail:
   ret = -errno;
   if (gpu != MAP_FAILED)
      munmap(gpu, size);
   if (cpu != MAP_FAILED)
      munmap(cpu, size);
   if (have_import && gpu == MAP_FAILED) {
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


IMPORT_CMD = r'''   if (hdr->cmd == 16) {
      /* Only an imported copy satisfies this request; a producer entry with
       * the same numeric handle must not be returned. */
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
        fail("usage: patch_host_min_import.py <virglrenderer-root>")
    path = pathlib.Path(sys.argv[1]) / "src/drm/mali_kbase_renderer.c"
    text = path.read_text()

    if "KB_IMPORT_SHARED_MIN" in text:
        print("already patched")
        return 0
    require("KB_IMPORT_SHARED" not in text,
            "another KB_IMPORT_SHARED patch is present; regenerate from upstream")

    text = sub_idem(text, "#include <poll.h>\n",
                    "#include <poll.h>\n#include <pthread.h>\n", "pthread include")

    text = sub_idem(
        text,
        "   int memfd, dmabuf;\n   bool shared, exported;\n   bool shadow, same_va;\n",
        "   int memfd, dmabuf;\n   bool shared, exported;\n   bool shadow, same_va;\n   bool imported;\n",
        "kb_bo imported flag")

    marker = "static int shared_alloc(struct kb_ctx *c, struct kb_bo *bo, uint32_t size)"
    pos = text.index(marker)
    text = text[:pos] + REGISTRY + "\n#define KB_IMPORT_SHARED_MIN 1\n\n" + text[pos:]

    text = sub_idem(
        text,
        "   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {\n",
        IMPORT_CMD + "\n   if (hdr->cmd == 1 || hdr->cmd == 5 || hdr->cmd == 11) {\n",
        "cmd 16")

    # Register every shared producer after its shared_alloc succeeds.
    text = sub_idem(
        text,
        "         c->bo[slot].handle = ++c->next_handle;\n"
        "         c->allocated += q->size;\n"
        "         r->handle = c->next_handle; r->size = q->size;\n",
        "         c->bo[slot].handle = ++c->next_handle;\n"
        "         c->allocated += q->size;\n"
        "         if (hdr->cmd == 5 || (hdr->cmd == 11 && q->reserved)) {\n"
        "            int reg_ret = kb_shared_register(c->bo[slot].handle,\n"
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
        "register shared producer")

    # 32-bit ARM variant: uintptr_t >> 32 is rejected by -Werror.
    text = sub_idem(
        text,
        "         if (((uintptr_t)ptr >> 32) == (((uintptr_t)ptr + total - 1) >> 32)) {\n",
        "         if (((uint64_t)(uintptr_t)ptr >> 32) ==\n"
        "             (((uint64_t)(uintptr_t)ptr + total - 1) >> 32)) {\n",
        "32-bit alias address check")

    text = sub_idem(
        text,
        "   if (hdr->cmd == 0) { r->value = c->gpu_id; r->size = c->allocated; return 0; }\n",
        "   if (hdr->cmd == 0) {\n"
        "      r->handle = 1; /* KB_CAP_IMPORT_SHARED */\n"
        "      r->value = c->gpu_id;\n"
        "      r->size = c->allocated;\n"
        "      return 0;\n"
        "   }\n",
        "KB_INFO capability")

    text = sub_idem(
        text,
        '   {"KB_ALIAS", command, sizeof(struct kb_req)},\n',
        '   {"KB_ALIAS", command, sizeof(struct kb_req)},\n'
        '   {"KB_IMPORT_SHARED", command, sizeof(struct kb_req)},\n',
        "dispatch entry")

    path.write_text(text, newline="\n")
    print(f"patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
