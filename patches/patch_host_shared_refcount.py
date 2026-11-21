#!/usr/bin/env python3
"""Patch the vendored host virglrenderer for long-session stability.

Two single-variable fixes, both in src/drm/mali_kbase_renderer.c:

1. kb_shared registry reclamation.  The bring-up registry keeps every shared
   blob entry until process exit ("Entries live until process exit in this
   bring-up variant", kb_shared_register at MAX_SHARED=1024), and cmd4
   deliberately does not reclaim.  A long session therefore exhausts the table
   and every later allocation fails with -ENOSPC, which the guest surfaces as
   `kbase virtio: BO size=... failed: No space left on device` and then as
   D3D11 device removal.  Fix: refcount each entry by producer context plus
   live importers, drop it when the count reaches zero.

2. Per-context BO slot leak.  free_bo() clears the slot but cmd4 also has to
   release the table entry when the producer is the last user.
"""
import sys
from pathlib import Path

MARK = 'hostkb-shared-refcount'


def rep(text, old, new):
    assert old in text, 'anchor not found: ' + old[:90]
    return text.replace(old, new, 1)


root = Path(sys.argv[1])
f = root / 'src/drm/mali_kbase_renderer.c'
s = f.read_text()

if MARK in s:
    print('already patched')
    sys.exit(0)

# --- registry entry carries a reference count -------------------------------
s = rep(s, '''struct kb_shared_entry {
   uint32_t handle, size;
   int memfd;
   bool used;
};''', '''struct kb_shared_entry {
   uint32_t handle, size;
   int memfd;
   int refs;   /* producer context + one per live importer */
   bool used;
};''')

s = rep(s, '''      kb_shared[i] = (struct kb_shared_entry){
         .handle = handle, .size = size, .memfd = fd, .used = true};''',
        '''      kb_shared[i] = (struct kb_shared_entry){
         .handle = handle, .size = size, .memfd = fd, .refs = 1, .used = true};''')

# --- register returns the slot so cmd4 can drop exactly one reference -------
s = rep(s, '''static int
kb_shared_register(uint32_t handle, uint32_t size, int memfd)
{''', '''static int
kb_shared_register(uint32_t handle, uint32_t size, int memfd, unsigned *slot_out)
{''')

s = rep(s, '''      kb_shared[i] = (struct kb_shared_entry){
         .handle = handle, .size = size, .memfd = fd, .refs = 1, .used = true};
      pthread_mutex_unlock(&kb_shared_lock);
      return 0;''', '''      kb_shared[i] = (struct kb_shared_entry){
         .handle = handle, .size = size, .memfd = fd, .refs = 1, .used = true};
      if (slot_out)
         *slot_out = i;
      pthread_mutex_unlock(&kb_shared_lock);
      return 0;''')

# --- a helper that drops one reference and frees the entry at zero ----------
s = rep(s, '''/* Returns a CLOEXEC dup of the producer's memfd, or a negative errno.''',
        '''/* Drops one reference to a registered shared blob and reclaims the entry
 * when the last producer/importer is gone.  Called from the producer's cmd4
 * and from each importer's context destroy. */
static void
kb_shared_unref(uint32_t handle)
{
   pthread_mutex_lock(&kb_shared_lock);
   for (unsigned i = 0; i < MAX_SHARED; i++) {
      if (!kb_shared[i].used || kb_shared[i].handle != handle)
         continue;
      if (--kb_shared[i].refs > 0)
         break;
      close(kb_shared[i].memfd);
      memset(&kb_shared[i], 0, sizeof(kb_shared[i]));
      break;
   }
   pthread_mutex_unlock(&kb_shared_lock);
}

/* Returns a CLOEXEC dup of the producer's memfd, or a negative errno.''')

# --- registering in cmd5/cmd11 keeps the new slot index --------------------
s = rep(s, '''         if (hdr->cmd == 5 || (hdr->cmd == 11 && q->reserved)) {
            int reg_ret = kb_shared_register(c->bo[slot].handle,
                                             c->bo[slot].size,
                                             c->bo[slot].memfd);''',
        '''         if (hdr->cmd == 5 || (hdr->cmd == 11 && q->reserved)) {
            int reg_ret = kb_shared_register(c->bo[slot].handle,
                                             c->bo[slot].size,
                                             c->bo[slot].memfd, NULL);''')

# --- importing bumps the reference count -----------------------------------
s = rep(s, '''      int import_ret = shared_import_fd(c, &c->bo[slot], size, fd, true);''',
        '''      pthread_mutex_lock(&kb_shared_lock);
      for (unsigned i = 0; i < MAX_SHARED; i++)
         if (kb_shared[i].used && kb_shared[i].handle == q->handle)
            kb_shared[i].refs++;
      pthread_mutex_unlock(&kb_shared_lock);
      int import_ret = shared_import_fd(c, &c->bo[slot], size, fd, true);''')

# --- cmd4 drops the producer reference ------------------------------------
s = rep(s, '''      c->allocated -= bo->size; free_bo(base->fd, bo); return 0;''',
        '''      uint32_t shared_handle = bo->shared ? bo->handle : 0;
      c->allocated -= bo->size; free_bo(base->fd, bo);
      if (shared_handle)
         kb_shared_unref(shared_handle);
      return 0;''')

# --- context destroy releases every shared reference it still holds --------
s = rep(s, '''   for (unsigned i = 0; i < MAX_BO; i++)
      if (c->bo[i].ptr) free_bo(c->base.fd, &c->bo[i]);''',
        '''   for (unsigned i = 0; i < MAX_BO; i++) {
      if (!c->bo[i].ptr)
         continue;
      uint32_t shared_handle = c->bo[i].shared ? c->bo[i].handle : 0;
      free_bo(c->base.fd, &c->bo[i]);
      if (shared_handle)
         kb_shared_unref(shared_handle);
   }''')

f.write_text(s)
print('patched', f)
