#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include "virtgpu_drm.h"

int main(void)
{
   const char *path = "/dev/dri/renderD128";
   int fd = open(path, O_RDWR | O_CLOEXEC);
   printf("open %s fd=%d errno=%d (%s)\n", path, fd, errno, strerror(errno));
   if (fd < 0)
      return 1;

   uint32_t caps[6] = {0};
   struct drm_virtgpu_get_caps get = {
      .cap_set_id = 6,
      .addr = (uintptr_t)caps,
      .size = sizeof(caps),
   };
   errno = 0;
   int r = ioctl(fd, DRM_IOCTL_VIRTGPU_GET_CAPS, &get);
   printf("GET_CAPS r=%d errno=%d (%s) size=%u\n", r, errno, strerror(errno), get.size);
   printf("caps: %08x %08x %08x %08x %08x %08x\n",
          caps[0], caps[1], caps[2], caps[3], caps[4], caps[5]);
   if (r)
      return 1;

   struct drm_virtgpu_context_set_param params[2] = {
      { .param = 1, .value = 6 },
      { .param = 2, .value = 1 },
   };
   struct drm_virtgpu_context_init init = {
      .num_params = 2,
      .ctx_set_params = (uintptr_t)params,
   };
   errno = 0;
   r = ioctl(fd, DRM_IOCTL_VIRTGPU_CONTEXT_INIT, &init);
   printf("CONTEXT_INIT r=%d errno=%d (%s)\n", r, errno, strerror(errno));
   if (r)
      return 1;

   struct drm_virtgpu_resource_create_blob blob = {
      .blob_mem = VIRTGPU_BLOB_MEM_HOST3D,
      .blob_flags = VIRTGPU_BLOB_FLAG_USE_MAPPABLE,
      .size = 4096,
   };
   errno = 0;
   r = ioctl(fd, DRM_IOCTL_VIRTGPU_RESOURCE_CREATE_BLOB, &blob);
   printf("CREATE_BLOB r=%d errno=%d (%s) bo_handle=%u blob_id=%u\n",
          r, errno, strerror(errno), blob.bo_handle, blob.blob_id);
   if (r)
      return 1;

   struct drm_virtgpu_map map = { .handle = blob.bo_handle };
   errno = 0;
   r = ioctl(fd, DRM_IOCTL_VIRTGPU_MAP, &map);
   printf("MAP r=%d errno=%d (%s) offset=%llu\n",
          r, errno, strerror(errno), (unsigned long long)map.offset);
   if (r)
      return 1;

   errno = 0;
   void *p = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, map.offset);
   printf("mmap p=%p errno=%d (%s)\n", p, errno, strerror(errno));
   if (p == MAP_FAILED)
      return 1;

   return 0;
}
