#!/usr/bin/env python3
import sys
from pathlib import Path


def rep(text, old, new):
    assert old in text, 'anchor not found: ' + old[:80]
    return text.replace(old, new, 1)


root = Path(sys.argv[1])

drm = root / 'src/drm/drm_renderer.c'
s = drm.read_text()
s = rep(s, '#include <errno.h>', '#include <errno.h>\n#include <stdio.h>')
s = rep(s, '''int
drm_renderer_init(int drm_fd)
{
   for (unsigned i = 0; i < ARRAY_SIZE(backends); i++) {''', '''int
drm_renderer_init(int drm_fd)
{
   fprintf(stderr, "drm-init: drm_fd=%d", drm_fd);
   for (unsigned i = 0; i < ARRAY_SIZE(backends); i++) {''')
s = rep(s, '''      if (b->context_type == MALI_KBASE_CONTEXT) {
         const char *opt = getenv("VKR_TEST_KBASE_NATIVE");
         if (!opt || strcmp(opt, "1")) continue;
      }''', '''      if (b->context_type == MALI_KBASE_CONTEXT) {
         const char *opt = getenv("VKR_TEST_KBASE_NATIVE");
         fprintf(stderr, "drm-init: mali opt=%s", opt ? opt : "(null)");
         if (!opt || strcmp(opt, "1")) continue;
      }''')
s = rep(s, '''         fd = open(b->node, O_RDWR | O_CLOEXEC);
         if (fd < 0)
            continue;

         capset.context_type = b->context_type;

         int ret = b->probe(fd, &capset);
         if (ret)
            memset(&capset, 0, sizeof(capset));

         close(fd);
         return ret;''', '''         fd = open(b->node, O_RDWR | O_CLOEXEC);
         fprintf(stderr, "drm-init: open %s fd=%d errno=%d", b->node, fd, errno);
         if (fd < 0)
            continue;

         capset.context_type = b->context_type;

         int ret = b->probe(fd, &capset);
         fprintf(stderr, "drm-init: probe %s ret=%d", b->name, ret);
         if (ret)
            memset(&capset, 0, sizeof(capset));

         close(fd);
         return ret;''')
drm.write_text(s)

mali = root / 'src/drm/mali_kbase_renderer.c'
s = mali.read_text()
s = rep(s, '#include <errno.h>', '#include <errno.h>\n#include <stdio.h>')
s = rep(s, '''static int handshake(int fd, struct kbase_ioctl_version_check *v)
{
   struct kbase_ioctl_set_flags flags = {0};
   if (ioctl(fd, KBASE_IOCTL_VERSION_CHECK_CSF, v) ||
       ioctl(fd, KBASE_IOCTL_SET_FLAGS, &flags)) return -errno;
   return 0;
}''', '''static int handshake(int fd, struct kbase_ioctl_version_check *v)
{
   struct kbase_ioctl_set_flags flags = {0};
   int r1 = ioctl(fd, KBASE_IOCTL_VERSION_CHECK_CSF, v);
   fprintf(stderr, "mali-probe: VC fd=%d r=%d errno=%d", fd, r1, errno);
   if (r1) return -errno;
   int r2 = ioctl(fd, KBASE_IOCTL_SET_FLAGS, &flags);
   fprintf(stderr, "mali-probe: SET r=%d errno=%d", r2, errno);
   if (r2) return -errno;
   return 0;
}''')
s = rep(s, '''   const char *opt = getenv("VKR_TEST_KBASE_NATIVE");
   if (!opt || strcmp(opt, "1")) return -ENODEV;
   struct kbase_ioctl_version_check v = {0};
   uint64_t id;
   int ret = handshake(fd, &v);
   if (!ret) ret = gpu_id(fd, &id);
   if (ret) return ret;''', '''   const char *opt = getenv("VKR_TEST_KBASE_NATIVE");
   fprintf(stderr, "mali-probe: enter fd=%d opt=%s", fd, opt ? opt : "(null)");
   if (!opt || strcmp(opt, "1")) return -ENODEV;
   struct kbase_ioctl_version_check v = {0};
   uint64_t id = 0;
   int ret = handshake(fd, &v);
   if (!ret) ret = gpu_id(fd, &id);
   fprintf(stderr, "mali-probe: ret=%d id=%llx", ret, (unsigned long long)id);
   if (ret) return ret;''')
s = rep(s, '''static int blob(struct virgl_context *v, uint32_t res, uint64_t id, uint64_t size,
                uint32_t flags, struct virgl_context_blob *out)
{
   (void)res;
   if (id) {''', '''static int blob(struct virgl_context *v, uint32_t res, uint64_t id, uint64_t size,
                uint32_t flags, struct virgl_context_blob *out)
{
   fprintf(stderr, "mali-blob: res=%u id=%llu size=%llu flags=0x%x", res,
           (unsigned long long)id, (unsigned long long)size, flags);
   (void)res;
   if (id) {''')
s = rep(s, '''   if (size < 4096 || size > 65536) return -EINVAL;
   return drm_context_get_shmem_blob((void *)v, "mali_kbase", 8, size, flags, out);''', '''   if (size < 4096 || size > 65536) {
      fprintf(stderr, "mali-blob: bad size");
      return -EINVAL;
   }
   int ret = drm_context_get_shmem_blob((void *)v, "mali_kbase", 8, size, flags, out);
   fprintf(stderr, "mali-blob: shmem ret=%d", ret);
   return ret;''')
# Diagnose the GPU-properties RPC path: it is the first command a guest
# context sends, and it fails for every PanVK process after the first one.
s = rep(s, '''   if (hdr->cmd == 9) {
      struct kbase_ioctl_get_gpuprops p = {0};
      int n = ioctl(base->fd, KBASE_IOCTL_GET_GPUPROPS, &p);
      if (n <= 0 || n > 65536) { r->status = -EIO; return 0; }''', '''   if (hdr->cmd == 9) {
      struct kbase_ioctl_get_gpuprops p = {0};
      int n = ioctl(base->fd, KBASE_IOCTL_GET_GPUPROPS, &p);
      fprintf(stderr, "mali-props: fd=%d n=%d errno=%d req_off=%u req_len=%u size_out=%u\\n",
              base->fd, n, errno, q->offset, q->size, p.size);
      if (n <= 0 || n > 65536) { r->status = -EIO; return 0; }''')
s = rep(s, '''   if (!base->shmem) return -EINVAL;
   struct kb_rsp *r = drm_context_rsp(base, hdr, sizeof(*r));
   if (!r) return -EINVAL;''', '''   if (!base->shmem) {
      fprintf(stderr, "mali-cmd: fd=%d cmd=%u no shmem\\n", base->fd, hdr->cmd);
      return -EINVAL;
   }
   struct kb_rsp *r = drm_context_rsp(base, hdr, sizeof(*r));
   if (!r) {
      fprintf(stderr, "mali-cmd: fd=%d cmd=%u no rsp (rsp_off=%u len=%u)\\n",
              base->fd, hdr->cmd, hdr->rsp_off, hdr->len);
      return -EINVAL;
   }''')
s = rep(s, '''   if (handshake(fd, &ver) || gpu_id(fd, &c->gpu_id)) goto fail;''', '''   int hs_ret = handshake(fd, &ver);
   int id_ret = hs_ret ? hs_ret : gpu_id(fd, &c->gpu_id);
   fprintf(stderr, "mali-create: fd=%d handshake=%d gpu_id=%d gpu=0x%llx errno=%d\\n",
           fd, hs_ret, id_ret, (unsigned long long)c->gpu_id, errno);
   if (hs_ret || id_ret) goto fail;''')
mali.write_text(s)

print('host probe diagnostics patched')
