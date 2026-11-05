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


q = root / "src/panfrost/vulkan/csf/panvk_vX_gpu_queue.c"
qs = q.read_text()


def repq(old, new):
    global qs
    assert old in qs, old[:90]
    qs = qs.replace(old, new, 1)


repq("""      result = kbase_create_group(queue, shader_core_count);
      if (result != VK_SUCCESS)
         goto err_destroy_syncobj;
      group_created = true;

      result = init_tiler(queue);
      if (result != VK_SUCCESS)
         goto err_cleanup_created;
      tiler_initialized = true;""",
     """      result = kbase_create_group(queue, shader_core_count);
      fprintf(stderr, "pvkq: kbase_create_group result=%d\\n", result);
      if (result != VK_SUCCESS)
         goto err_destroy_syncobj;
      group_created = true;

      result = init_tiler(queue);
      fprintf(stderr, "pvkq: init_tiler result=%d\\n", result);
      if (result != VK_SUCCESS)
         goto err_cleanup_created;
      tiler_initialized = true;""")

repq("""   result = init_queue(queue);
   if (result != VK_SUCCESS)
      goto err_cleanup_created;""",
     """   result = init_queue(queue);
   fprintf(stderr, "pvkq: init_queue result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto err_cleanup_created;""")


repq("""   result = init_render_desc_ringbuf(queue);
   if (result != VK_SUCCESS)
      goto err_cleanup_queue;

   result = init_utrace(queue);
   if (result != VK_SUCCESS)
      goto err_cleanup_queue;

   for (uint32_t i = 0; i < PANVK_SUBQUEUE_COUNT; i++) {
      result = init_subqueue(queue, i);
      if (result != VK_SUCCESS)
         goto err_cleanup_queue;
   }""",
     """   result = init_render_desc_ringbuf(queue);
   fprintf(stderr, "pvkq: render_desc_ringbuf result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto err_cleanup_queue;

   result = init_utrace(queue);
   fprintf(stderr, "pvkq: utrace result=%d\\n", result);
   if (result != VK_SUCCESS)
      goto err_cleanup_queue;

   for (uint32_t i = 0; i < PANVK_SUBQUEUE_COUNT; i++) {
      result = init_subqueue(queue, i);
      fprintf(stderr, "pvkq: init_subqueue %u result=%d\\n", i, result);
      if (result != VK_SUCCESS)
         goto err_cleanup_queue;
   }""")

repq("""      result = kbase_submit_init_subqueues(queue);
      if (result != VK_SUCCESS)
         goto err_cleanup_queue;""",
     """      result = kbase_submit_init_subqueues(queue);
      fprintf(stderr, "pvkq: submit_init_subqueues result=%d\\n", result);
      if (result != VK_SUCCESS)
         goto err_cleanup_queue;""")

repq("""      result = kbase_init_seqnos(queue);
      if (result != VK_SUCCESS)
         return result;""",
     """      result = kbase_init_seqnos(queue);
      fprintf(stderr, "pvkq: init_seqnos result=%d\\n", result);
      if (result != VK_SUCCESS)
         return result;""")

repq("""   if (*active) {
      *(volatile uint32_t *)subq->kbase.user_io.doorbell = 1;""",
     """   fprintf(stderr, "pvbq: publish sq%u active=%u insert=%llu\\n", subqueue,
           (unsigned)*active, (unsigned long long)subq->kbase.insert);
   if (*active) {
      *(volatile uint32_t *)subq->kbase.user_io.doorbell = 1;""")

repq("""      VkResult res =
         kbase_subqueue_emit_job(queue, subqueue, subq->kbase.init_stream_addr,""",
     """      VkResult res =
         kbase_subqueue_emit_job(queue, subqueue, subq->kbase.init_stream_addr,""")

repq("""      if (res != VK_SUCCESS)
         return panvk_errorf(dev->vk.physical, VK_ERROR_INITIALIZATION_FAILED,
                             "Failed to initialize subqueue");

      subq->kbase.init_pending = 0;""",
     """      fprintf(stderr, "pvbq: emit sq%u res=%d\\n", subqueue, res);
      if (res != VK_SUCCESS)
         return panvk_errorf(dev->vk.physical, VK_ERROR_INITIALIZATION_FAILED,
                             "Failed to initialize subqueue");

      subq->kbase.init_pending = 0;""")

repq("""      VkResult res = kbase_subqueue_wait_seqno(
         queue, i, queue->subqueues[i].kbase.emitted_jobs, touched, false,
         UINT64_MAX);
      if (res != VK_SUCCESS)""",
     """      VkResult res = kbase_subqueue_wait_seqno(
         queue, i, queue->subqueues[i].kbase.emitted_jobs, touched, false,
         UINT64_MAX);
      fprintf(stderr, "pvbq: wait sq%u res=%d emitted=%llu\\n", i, res,
              (unsigned long long)queue->subqueues[i].kbase.emitted_jobs);
      if (res != VK_SUCCESS)""")
q.write_text(qs)
p.write_text(s)
print("instrumented", p)
