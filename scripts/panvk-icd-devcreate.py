"""Instrument panvk's device creation so a silent -3 names its step.

Applied to a mesa checkout root (argument 1) by the ICD build workflow.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
p = root / "src/panfrost/vulkan/panvk_vX_device.c"
s = p.read_text()


def rep(old, new):
    global s
    assert old in s, old[:90]
    s = s.replace(old, new, 1)


rep('''   device->kmod.vm =
      pan_kmod_vm_create(device->kmod.dev, vm_flags,
                         user_va_start, user_va_end - user_va_start);

   if (!device->kmod.vm) {''',
    '''   device->kmod.vm =
      pan_kmod_vm_create(device->kmod.dev, vm_flags,
                         user_va_start, user_va_end - user_va_start);
   fprintf(stderr, "pvkx: kmod dev=%p vm=%p\\n", (void *)device->kmod.dev,
           (void *)device->kmod.vm);

   if (!device->kmod.vm) {''')

rep('''   result = panvk_priv_bo_create(
      device, pan_sample_positions_buffer_size(),
      panvk_device_adjust_bo_flags(device, PAN_KMOD_BO_FLAG_WB_MMAP),
      VK_SYSTEM_ALLOCATION_SCOPE_DEVICE, &device->sample_positions);
   if (result != VK_SUCCESS)
      goto err_free_priv_bos;''',
    '''   result = panvk_priv_bo_create(
      device, pan_sample_positions_buffer_size(),
      panvk_device_adjust_bo_flags(device, PAN_KMOD_BO_FLAG_WB_MMAP),
      VK_SYSTEM_ALLOCATION_SCOPE_DEVICE, &device->sample_positions);
   fprintf(stderr, "pvkx: sample_positions result=%d bo=%p\\n", result,
           (void *)device->sample_positions);
   if (result != VK_SUCCESS)
      goto err_free_priv_bos;''')

rep('''   result = panvk_per_arch(init_tiler_oom)(device);
   if (result != VK_SUCCESS)
      goto err_free_priv_bos;''',
    '''   result = panvk_per_arch(init_tiler_oom)(device);
   fprintf(stderr, "pvkx: tiler_oom result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto err_free_priv_bos;''')

rep('''   result = panvk_priv_bo_create(device, PAN_PRINTF_BUFFER_SIZE, 0,
                                 VK_SYSTEM_ALLOCATION_SCOPE_DEVICE,
                                 &device->printf.bo);
   if (result != VK_SUCCESS)
      goto err_free_priv_bos;''',
    '''   result = panvk_priv_bo_create(device, PAN_PRINTF_BUFFER_SIZE, 0,
                                 VK_SYSTEM_ALLOCATION_SCOPE_DEVICE,
                                 &device->printf.bo);
   fprintf(stderr, "pvkx: printf_bo result=%d bo=%p\\n", result,
           (void *)device->printf.bo);
   if (result != VK_SUCCESS)
      goto err_free_priv_bos;''')

rep('''   result = panvk_precomp_init(device);
   if (result != VK_SUCCESS)
      goto err_free_priv_bos;''',
    '''   result = panvk_precomp_init(device);
   fprintf(stderr, "pvkx: precomp result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto err_free_priv_bos;''')

rep('''   device->vk.mem_cache = vk_pipeline_cache_create(&device->vk, &cache_info, NULL);
   if (device->vk.mem_cache == NULL) {''',
    '''   device->vk.mem_cache = vk_pipeline_cache_create(&device->vk, &cache_info, NULL);
   fprintf(stderr, "pvkx: mem_cache=%p\\n", (void *)device->vk.mem_cache);
   if (device->vk.mem_cache == NULL) {''')

rep('''   result = panvk_meta_init(device);
   if (result != VK_SUCCESS)
      goto err_free_draw_ctx;''',
    '''   result = panvk_meta_init(device);
   fprintf(stderr, "pvkx: meta result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto err_free_draw_ctx;''')

rep('''   for (unsigned i = 0; i < pCreateInfo->queueCreateInfoCount; i++) {
      const VkDeviceQueueCreateInfo *queue_create =
         &pCreateInfo->pQueueCreateInfos[i];

      result = check_global_priority(physical_device, queue_create);''',
    '''   fprintf(stderr, "pvkx: queues start count=%u\\n", pCreateInfo->queueCreateInfoCount);
   for (unsigned i = 0; i < pCreateInfo->queueCreateInfoCount; i++) {
      const VkDeviceQueueCreateInfo *queue_create =
         &pCreateInfo->pQueueCreateInfos[i];

      result = check_global_priority(physical_device, queue_create);''')

p.write_text(s)
print("instrumented", p)
