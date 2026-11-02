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
mali.write_text(s)

print('host probe diagnostics patched')
