import pathlib

def patch(path, pairs):
    p = pathlib.Path(path)
    s = p.read_text()
    for old, new in pairs:
        assert old in s, (path, old[:70])
        s = s.replace(old, new, 1)
    p.write_text(s)
    print("patched", path)

patch("mesa/src/panfrost/lib/kmod/kbase_kmod.c", [
    ('''   if (ioctl(fd, KBASE_IOCTL_MEM_JIT_INIT, &jit_init)) {
      mesa_logw("kbase: KBASE_IOCTL_MEM_JIT_INIT failed: %s "
                "(tiler heap creation will not work)", strerror(errno));
   }''',
     '''   {
      int jr = ioctl(fd, KBASE_IOCTL_MEM_JIT_INIT, &jit_init);
      fprintf(stderr, "pvk: jit_init ret=%d errno=%d\\n", jr, errno);
      if (jr) {
         mesa_logw("kbase: KBASE_IOCTL_MEM_JIT_INIT failed: %s "
                   "(tiler heap creation will not work)", strerror(errno));
      }
   }'''),
    ('''   if (ioctl(fd, KBASE_IOCTL_MEM_EXEC_INIT, &exec_init)) {
      mesa_logw("kbase: KBASE_IOCTL_MEM_EXEC_INIT failed: %s "
                "(executable BO allocation will not work)", strerror(errno));
   }''',
     '''   {
      int er = ioctl(fd, KBASE_IOCTL_MEM_EXEC_INIT, &exec_init);
      fprintf(stderr, "pvk: exec_init ret=%d errno=%d\\n", er, errno);
      if (er) {
         mesa_logw("kbase: KBASE_IOCTL_MEM_EXEC_INIT failed: %s "
                   "(executable BO allocation will not work)", strerror(errno));
      }
   }'''),
    ('''   kbase_dev_query_props(kbase_dev, props_buf, props_size);''',
     '''   kbase_dev_query_props(kbase_dev, props_buf, props_size);
   fprintf(stderr, "pvk: props gpu_id=0x%llx variant=0x%x shader_present=0x%llx core_count=%u csg_slots=%u\\n",
           (unsigned long long)kbase_dev->base.props.gpu_id,
           kbase_dev->base.props.gpu_variant,
           (unsigned long long)kbase_dev->base.props.shader_present,
           pan_query_core_count(&kbase_dev->base.props),
           kbase_dev->csif_info.csg_slot_count);'''),
])

patch("mesa/src/panfrost/vulkan/panvk_physical_device.c", [
    ('''   result = create_kmod_dev_kbase(device, instance, path);
   if (result != VK_SUCCESS)
      return result;''',
     '''   result = create_kmod_dev_kbase(device, instance, path);
   fprintf(stderr, "pvk: create_kmod_dev_kbase result=%d dev=%p\\n", result, (void *)device->kmod.dev);
   if (result != VK_SUCCESS)
      return result;'''),
    ('''   bool unknown_gpu;
   result = get_gpu_model(device, instance, &unknown_gpu);
   if (result != VK_SUCCESS)
      goto fail_kbase;''',
     '''   bool unknown_gpu;
   result = get_gpu_model(device, instance, &unknown_gpu);
   fprintf(stderr, "pvk: arch=%u get_gpu_model result=%d model=%s unknown=%d\\n", arch, result,
           device->model ? device->model->name : "(null)", (int)unknown_gpu);
   if (result != VK_SUCCESS)
      goto fail_kbase;'''),
    ('''   result = get_core_masks(device, instance);
   if (result != VK_SUCCESS)
      goto fail_kbase;

   result = get_device_heaps(device, instance);
   if (result != VK_SUCCESS)
      goto fail_kbase;

   result = get_device_sync_types_kbase(device, instance);
   if (result != VK_SUCCESS)
      goto fail_kbase;''',
     '''   result = get_core_masks(device, instance);
   fprintf(stderr, "pvk: get_core_masks result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto fail_kbase;

   result = get_device_heaps(device, instance);
   fprintf(stderr, "pvk: get_device_heaps result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto fail_kbase;

   result = get_device_sync_types_kbase(device, instance);
   fprintf(stderr, "pvk: get_device_sync_types_kbase result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto fail_kbase;'''),
    ('''   result =
      vk_physical_device_init(&device->vk, &instance->vk, &supported_extensions,
                              &supported_features, NULL, &dispatch_table);

   if (result != VK_SUCCESS)
      goto fail_kbase;''',
     '''   result =
      vk_physical_device_init(&device->vk, &instance->vk, &supported_extensions,
                              &supported_features, NULL, &dispatch_table);
   fprintf(stderr, "pvk: vk_physical_device_init result=%d\\n", result);

   if (result != VK_SUCCESS)
      goto fail_kbase;'''),
    ('''   result = panvk_wsi_init(device);
   if (result != VK_SUCCESS)
      goto fail_kbase;''',
     '''   result = panvk_wsi_init(device);
   fprintf(stderr, "pvk: panvk_wsi_init result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto fail_kbase;'''),
])
