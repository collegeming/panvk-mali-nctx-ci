#include <errno.h>
#include <sys/ioctl.h>
#include <xf86drm.h>

drmVersionPtr drmGetVersion(int fd)
{
   (void)fd;
   return NULL;
}

void drmFreeVersion(drmVersionPtr version)
{
   (void)version;
}

int drmOpenWithType(const char *name, const char *busid, int type)
{
   (void)name;
   (void)busid;
   (void)type;
   return -1;
}

int drmIoctl(int fd, unsigned long request, void *arg)
{
   return ioctl(fd, request, arg);
}

int drmCommandWriteRead(int fd, unsigned long drmCommandIndex, void *data, unsigned long size)
{
   (void)fd;
   (void)drmCommandIndex;
   (void)data;
   (void)size;
   return -ENOSYS;
}
