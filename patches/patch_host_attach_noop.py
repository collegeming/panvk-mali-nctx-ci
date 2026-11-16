#!/usr/bin/env python3
"""Minimal host-side idempotent attach for classic typed resources.

The Windows KMD sends a full ATTACH_BACKING (2523 iovs) followed by a smaller
ATTACH_BACKING for the same resource.  virgl_resource_attach_iov() currently
rejects the second call with EINVAL once res->iov is set.  The second iov list
is a subset of the first mapping (same base, later offset), so keeping the
first backing is the safe minimal behavior.

This patch runs after patch_host_attach_diag.py.  It only changes the duplicate
branch for resources that have a pipe_resource (classic 3D allocations).  Blob
resources, including the KB_IMPORT_SHARED / C2 path, keep the original reject
behavior because their resources are fd/opaque-backed and have no
pipe_resource.
"""

from __future__ import annotations

import pathlib
import sys


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"patch_host_attach_noop: {message}")


def sub_idem(text: str, old: str, new: str, what: str) -> str:
    if text.count(old) != 1:
        fail(f"anchor not unique: {what}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: patch_host_attach_noop.py <virglrenderer-root>")
    path = pathlib.Path(sys.argv[1]) / "src/virgl_resource.c"
    text = path.read_text()

    text = sub_idem(
        text,
        """   if (res->iov) {
      fprintf(stderr,
              "ATTACH-DIAG: reject res=%u iov_count=%d existing=%d existing_count=%d ret=%d\\n",
              res->res_id, iov_count, res->iov != NULL, res->iov_count, EINVAL);
      return EINVAL;
   }""",
        """   if (res->iov) {
      if (res->pipe_resource) {
         fprintf(stderr,
                 "ATTACH-DIAG: noop res=%u iov_count=%d existing=%d existing_count=%d ret=0\\n",
                 res->res_id, iov_count, res->iov != NULL, res->iov_count);
         return 0;
      }
      fprintf(stderr,
              "ATTACH-DIAG: reject res=%u iov_count=%d existing=%d existing_count=%d ret=%d\\n",
              res->res_id, iov_count, res->iov != NULL, res->iov_count, EINVAL);
      return EINVAL;
   }""",
        "virgl_resource_attach_iov duplicate branch",
    )

    path.write_text(text)


if __name__ == "__main__":
    main()
