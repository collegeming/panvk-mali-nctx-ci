#!/usr/bin/env python3
"""Log-only diagnostics for the virglrenderer resource attach path.

The Windows KMD replaces a standard allocation's backing by sending
DETACH_BACKING followed by ATTACH_BACKING.  virgl_resource_attach_iov()
rejects any second attach for the same resource with EINVAL, which surfaces
in crosvm as:

    error processing gpu command ResourceAttachBacking: ErrRutabaga(ComponentError(22))

This patch adds fprintf(stderr) probes only.  It does not change the attach,
detach or create semantics.  Fields are chosen to answer three questions:

  * was the resource already backed when ATTACH arrived, and with how many
    iovs (create_blob records its iovs before any attach);
  * how many iovs does each attach/detach carry;
  * which entry point returned EINVAL (lookup failure, duplicate backing).

virglrenderer logs to stderr, which crosvm forwards to logcat.  Grep for
"ATTACH-DIAG:".
"""

from __future__ import annotations

import pathlib
import sys


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"patch_host_attach_diag: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def sub_idem(text: str, old: str, new: str, what: str) -> str:
    require(text.count(old) == 1, f"anchor not unique: {what}")
    return text.replace(old, new, 1)


def patch_resource(path: pathlib.Path) -> None:
    text = path.read_text()

    text = sub_idem(
        text,
        "#include <assert.h>\n#include <errno.h>",
        "#include <assert.h>\n#include <errno.h>\n#include <stdio.h>",
        "virgl_resource.c includes",
    )

    text = sub_idem(
        text,
        """virgl_resource_attach_iov(struct virgl_resource *res,
                          const struct iovec *iov,
                          int iov_count)
{
   if (res->iov)
      return EINVAL;

   res->iov = iov;
   res->iov_count = iov_count;

   if (res->pipe_resource) {
      pipe_callbacks.attach_iov(res->pipe_resource,
                                iov,
                                iov_count,
                                pipe_callbacks.data);
   }

   return 0;
}""",
        """virgl_resource_attach_iov(struct virgl_resource *res,
                          const struct iovec *iov,
                          int iov_count)
{
   fprintf(stderr,
           "ATTACH-DIAG: enter res=%u iov_count=%d iov0=%p+0x%zx existing=%d existing_count=%d pipe=%p\\n",
           res->res_id, iov_count,
           (iov && iov_count > 0) ? iov[0].iov_base : NULL,
           (iov && iov_count > 0) ? iov[0].iov_len : (size_t)0,
           res->iov != NULL, res->iov_count, (void *)res->pipe_resource);

   if (res->iov) {
      fprintf(stderr,
              "ATTACH-DIAG: reject res=%u iov_count=%d existing=%d existing_count=%d ret=%d\\n",
              res->res_id, iov_count, res->iov != NULL, res->iov_count, EINVAL);
      return EINVAL;
   }

   res->iov = iov;
   res->iov_count = iov_count;

   if (res->pipe_resource) {
      pipe_callbacks.attach_iov(res->pipe_resource,
                                iov,
                                iov_count,
                                pipe_callbacks.data);
   }

   fprintf(stderr, "ATTACH-DIAG: ok res=%u iov_count=%d\\n", res->res_id, iov_count);
   return 0;
}""",
        "virgl_resource_attach_iov body",
    )

    text = sub_idem(
        text,
        """virgl_resource_detach_iov(struct virgl_resource *res)
{
   if (!res->iov)
      return;

   if (res->pipe_resource)
      pipe_callbacks.detach_iov(res->pipe_resource, pipe_callbacks.data);

   res->iov = NULL;
   res->iov_count = 0;
}""",
        """virgl_resource_detach_iov(struct virgl_resource *res)
{
   fprintf(stderr,
           "ATTACH-DIAG: detach res=%u existing=%d existing_count=%d pipe=%p\\n",
           res->res_id, res->iov != NULL, res->iov_count, (void *)res->pipe_resource);

   if (!res->iov)
      return;

   if (res->pipe_resource)
      pipe_callbacks.detach_iov(res->pipe_resource, pipe_callbacks.data);

   res->iov = NULL;
   res->iov_count = 0;
}""",
        "virgl_resource_detach_iov body",
    )

    path.write_text(text)


def patch_renderer(path: pathlib.Path) -> None:
    text = path.read_text()

    text = sub_idem(
        text,
        """int virgl_renderer_resource_attach_iov(int res_handle, struct iovec *iov,
                                       int num_iovs)
{
   TRACE_FUNC();
   struct virgl_resource *res = virgl_resource_lookup(res_handle);
   if (!res)
      return EINVAL;

   return virgl_resource_attach_iov(res, iov, num_iovs);
}""",
        """int virgl_renderer_resource_attach_iov(int res_handle, struct iovec *iov,
                                       int num_iovs)
{
   TRACE_FUNC();
   struct virgl_resource *res = virgl_resource_lookup(res_handle);
   if (!res) {
      fprintf(stderr, "ATTACH-DIAG: missing res=%d iov_count=%d ret=%d\\n",
              res_handle, num_iovs, EINVAL);
      return EINVAL;
   }

   return virgl_resource_attach_iov(res, iov, num_iovs);
}""",
        "virgl_renderer_resource_attach_iov body",
    )

    text = sub_idem(
        text,
        """int virgl_renderer_resource_create_blob(const struct virgl_renderer_resource_create_blob_args *args)
{
   TRACE_FUNC();
   struct virgl_resource *res;""",
        """int virgl_renderer_resource_create_blob(const struct virgl_renderer_resource_create_blob_args *args)
{
   TRACE_FUNC();
   fprintf(stderr,
           "ATTACH-DIAG: create_blob res=%u size=%llu blob_mem=%u num_iovs=%d flags=0x%x\\n",
           args->res_handle, (unsigned long long)args->size, args->blob_mem,
           args->num_iovs, args->blob_flags);
   struct virgl_resource *res;""",
        "virgl_renderer_resource_create_blob entry",
    )

    path.write_text(text)


def main() -> None:
    require(len(sys.argv) == 2, "usage: patch_host_attach_diag.py <virglrenderer-root>")
    root = pathlib.Path(sys.argv[1])
    resource = root / "src/virgl_resource.c"
    renderer = root / "src/virglrenderer.c"
    require(resource.is_file(), f"missing {resource}")
    require(renderer.is_file(), f"missing {renderer}")

    patch_resource(resource)
    patch_renderer(renderer)

    joined = resource.read_text() + renderer.read_text()
    require(joined.count("ATTACH-DIAG:") >= 5, "diagnostic probes were not installed")


if __name__ == "__main__":
    main()
