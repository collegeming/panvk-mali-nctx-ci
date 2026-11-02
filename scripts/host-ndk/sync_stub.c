#include <poll.h>

#include "util/libsync.h"

int sync_wait(int fd, int timeout)
{
   struct pollfd p = { .fd = fd, .events = POLLIN };
   return poll(&p, 1, timeout);
}
