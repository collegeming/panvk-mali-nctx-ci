from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[2]
p = root / 'cnf-docs/03-panvk-mali-nctx-route.md'
backup = root / 'route-b/evidence-panvk/20261106-native-input/main-document-before-update.md'
backup.parent.mkdir(parents=True, exist_ok=True)
if not backup.exists(): shutil.copyfile(p, backup)
s = p.read_text(encoding='utf-8')
def replace(old, new):
    global s
    assert s.count(old) == 1, old[:100]
    s = s.replace(old, new, 1)
replace('桌面（Arch Linux ARM + niri + Noctalia）在同一个 VM 内可运行，合成走 **virgl**；PanVK 只承担 Vulkan 负载，尚未接管合成。', 'Arch Linux ARM + niri + Noctalia 已在独立测试 VM 中通过 **Zink → PanVK** 完成 niri 的 DRM/GBM 合成，并在 DroidVM native 页面呈现可动态更新的桌面。Noctalia 自身沿用 Qt 软件绘制；该结果不代表所有客户端都由 GPU 绘制。')
replace('zink 已到 EGL 层可用的程度（`OpenGL renderer: zink Vulkan 1.4(Mali-G2-Ultra MC16 (MESA_PANVK))`），但 niri 的 DRM/GBM 后端受限于 virtio 传输层不传 fd，无法导入 dma-buf。', '固定 BAR 与直接控制页映射已通过 20 轮完整计算生命周期和 offset 复用。显示末端仍为 CPU 拷贝，原生输入、重复冷启动、Surface 重连和 60 分钟稳定性尚未验收。输入独立前端已通过 CI 编译，但设备安装被系统拒绝；不能据此宣称触摸/键鼠可用。验证依据见 [native 实测记录](../route-b/evidence-panvk/20261106-native/RESULTS.md)。')
replace('标识约定：**已有**＝设备或 CI 实测；**推断**＝有推理链但未直接证实；**待验证**＝需在设备上确认。', '本文面向实现与测试人员。标识约定：**已有**＝设备或 CI 实测，必须区分两者；**推断**＝有推理链但未直接证实；**待验证**＝需在设备上确认。历史试验与旧配置见 [原始文档存档](../route-b/evidence-panvk/20261106-native-input/main-document-before-update.md)，不得作为当前启动配置。')
replace('        cx["独立 crosvm 运行时<br/>/data/local/tmp/panvk-nctx/bin/crosvm"]', '        cx["独立 crosvm-native4<br/>panvk-stability-v1/bin"]')
replace('        niri -->|"EGL / GBM（virgl）"| vgpu', '        niri -->|"EGL / GBM → Zink"| icd')
replace('    cx -->|"virgl2:drm context 0x4b424153"| vr', '    cx -->|"virgl2:drm context 0x4b424153"| vr\n    vr --> shadow["真实 GPU 完成后读回共享影子内存"]\n    shadow --> cx\n    cx -->|"CPU 拷贝"| surface["Android native Surface"]\n    surface --> app')
replace('| 桌面合成 | niri → EGL/GBM → virgl → Mali 原生 DRM 上下文 | **已有**，出图正常 |', '| 桌面合成 | niri → EGL/GBM → Zink → PanVK → Mali GPU | **已有**，同一 niri PID 的 renderer 与动态 native 画面对应 |')
replace('| zink（GL→Vulkan） | 应用 → zink → PanVK → 同上 | **部分**，见 §7 |', '| Android 显示 | GPU BO → 共享影子内存 → crosvm → native Surface | **已有**，末端 CPU 拷贝；性能与零拷贝待验证 |')
replace('| CI 与补丁 | `collegeming/panvk-mali-nctx-ci` | 分支 `main` | 五条 workflow：crosvm、host virglrenderer、guest ICD、guest rootfs、DRM 探针 |', '| CI 与补丁 | `collegeming/panvk-mali-nctx-ci` | `panvk/stability-v1`、`panvk/host-stability-v1`、`panvk/native-input-v1` | guest ICD/rootfs、native crosvm、独立输入前端；源码修改与编译均在 Actions |')
replace('因此本路线可验证的是「枚举 → 分配 → 提交 → 回读」，不含 WSI 与 DRM 修饰符协商。', '上游实验边界不能直接当作当前补丁版本的能力结论。当前已验证计算与 niri DRM/GBM 合成，guest 通过真实 GPU 等待后导出有效 sync-file；宿主完整异步 fence timeline、通用跨 context 导入和 modifier 组合仍未验收。')
replace('八次提交，全部集中在 `devices/src/virtio/gpu/virtio_gpu.rs`，解决 virtio-gpu blob 的 BAR 映射生命周期：', '基础分支包含下列 blob 映射改造；当前实测版本另叠加 CI 补丁，不能只按基础分支提交重建：')
replace('- guest 的 stage-2 对同一 BAR offset 的映射不跟随重新注册，复用 offset 会让第二次映射读到上一轮的页。', '- 动态映射试验曾在 offset 复用后读到陈旧页；当前 KVM 固定 BAR 配置使用直接控制页映射，已通过复用测试，不能推广为所有后端都会发生同一问题。\n\n当前 CI 补丁还包括：PCI mapper 在注销成功后才删除跟踪项、保留注销失败的诊断状态；固定 BAR 下停用控制页镜像；native scanout 保留首次导出 fd 并复用映射；native-only 构建的 feature 条件修正。')
replace('### 4.2 `collegeming/panvk-mali-nctx-ci`（分支 `main`）', '### 4.2 `collegeming/panvk-mali-nctx-ci`')
replace('ICD 构建中的四处必要修改（都在 CI 内以脚本实施，不改上游仓库）：', 'ICD 的基础修复如下，均在 CI 内应用；用于桌面的版本还必须包含本节后续的导入与同步补丁：')
replace('### 4.3 guest 内核模块（`virtgpu_vram.c`）', '桌面 ICD 另按 virtio GEM 身份复用同设备 BO，并在通用导入层增加引用；BO 释放关闭导出 fd。sync-file 导出先等待真实 GPU 完成，再同步已导出的 BO 影子内存，最后创建已信号 guest DRM syncobj 并导出有效 fd。这是阻塞式正确性实现，尚未优化异步并行度。\n\n### 4.3 guest 内核模块（`virtgpu_vram.c`）')
replace('| 同一开机内多次创建设备 | 单调 BAR 游标配置曾连续四次 PASS；普通 offset 复用仍有失败记录，正常销毁路径尚待回归 | 见 §7.3 |', '| 同一开机内多次创建设备 | 固定 BAR、直接控制页映射：同进程 20 轮 create/submit/readback/destroy PASS，不依赖单调游标 | [生命周期记录](../route-b/evidence-panvk/20261106-stability-boot3/RESULTS.md) |')
replace('| 桌面运行 | niri active、Noctalia 渲染，1280×1024 截图正常 | `panvk-rebuilt-desktop.png`，sha256 `6b6753b4…` |', '| native 桌面 | 2272×1514，Android 截图显示 Noctalia；五轮面板开关可动态更新 | `20261106-native/android-panel-open.png`、`android-panel-closed.png` |')
replace('| niri 合成走 PanVK | 独立测试 VM 的 DRM/GBM 后端已确认 `GL Renderer: zink Vulkan 1.4(Mali-G2-Ultra MC16 (MESA_PANVK))`；同设备导入及真实 sync-file 修复后可生成 Noctalia 画面。DroidVM native 显示、输入和长期稳定性尚待验收 | [实测记录](../route-b/evidence-panvk/20261106-stability-boot3/RESULTS.md) |', '| niri 合成走 PanVK | PID 548：`zink Vulkan 1.4(Mali-G2-Ultra MC16 (MESA_PANVK))`，DRM card0、GBM；桌面运行时双次 compute PASS | [renderer](../route-b/evidence-panvk/20261106-native/renderer-native.txt)、[回归输出](../route-b/evidence-panvk/20261106-native/final-regression.txt) |\n| 原生输入 | 独立前端与 Binder 输入代理已通过 CI 编译；设备安装返回 `INSTALL_FAILED_USER_RESTRICTED`，触摸/键鼠未验收 | [输入实现与限制](../route-b/evidence-panvk/20261106-native-input/RESULTS.md) |')
a = s.index('## 6. 产物清单')
b = s.index('## 7. PanVK 路线处理情况')
s = s[:a] + '''## 6. 已验证产物与启动配置

以下为已出图配置；新输入代码和测量版本未经设备验收，不替换该基线。

| 产物 | CI / 设备位置 | SHA-256 |
| --- | --- | --- |
| native-only crosvm | [42190769607](https://github.com/collegeming/panvk-mali-nctx-ci/actions/runs/42190769607)，`panvk-stability-v1/bin/crosvm-native4` | `d12fbd132f0fcf964d7c1e7482c54a265d659b92f810e682c734b1096ce3caa8` |
| PanVK ICD | [42189809681](https://github.com/collegeming/panvk-mali-nctx-ci/actions/runs/42189809681)，guest `/opt/panvk/libvulkan_manet.so` | `45f0d22ee222ae9211436a41d97fddf6d6fc53eeb9725ef8152aea9396156a36` |
| host virglrenderer | `panvk-stability-v1/lib/libvirglrenderer.so` | `22e1c7df223edcd10b374f419ff50149fa24e846ae7bfc5be18c66634db7c51c` |
| 干净 qcow2 | [42170484538](https://github.com/collegeming/panvk-mali-nctx-ci/actions/runs/42170484538) | `a161a63f7d07d6b9e2f1b2a42f8bdb01cd74995f3b153a08fae7cd5cc275c7cd` |
| guest Image | `panvk-stability-v1/boot/Image` | `80af1fe7be83a08d5392b83383342cf32b1f278f6d8675d75dc4617b047a09b9` |
| guest initramfs | `panvk-stability-v1/boot/initramfs-linux.img` | `7d7aafd8a62066c090b4fcd84191c11f8eea72f4efdb8713328bfd4123b144d5` |

设备路径均相对 `/data/local/tmp/`。工作盘为 `panvk-stability-v1/boot/root-boot2.qcow2`，包含测试安装与最终 ICD，不能再用干净镜像哈希校验。`boot/root.qcow2` 是首轮故障盘，保留取证，不得复用。

宿主使用 KVM、2048 MB、4 vCPU、`fixed-blob-mapping=true`，native 显示 2272×1514、60 Hz、160 DPI；60 Hz 是模式配置，不是实测 FPS。GPU 后端仍为 `virglrenderer` 的 `virgl2:drm` native-context 传输，不能改为 gfxstream。

```sh
# guest niri 环境；PANVK_KBASE_VIRTIO 的值是设备路径
PAN_I_WANT_A_BROKEN_VULKAN_DRIVER=1
PANVK_KBASE_VIRTIO=/dev/dri/renderD128
VK_DRIVER_FILES=/opt/panvk/panvk_icd.json
MESA_LOADER_DRIVER_OVERRIDE=zink
```

宿主关键环境为 `VKR_TEST_KBASE_NATIVE=1`、`VKR_TEST_KBASE_MAX_BO=1024`、`VKR_TEST_KBASE_MAX_MB=1024`、`GPU_DISPLAY_COPY_MODE=cpu`。native Binder 服务固定为 `droidvm_disp_panvk_stability_native_gpu-0`。运行时需要从当前 APK 安装路径发现兼容库，不能硬编码旧 APK 目录。

''' + s[b:]
replace('| niri DRM/GBM 后端直接走 zink | 原先同设备 dma-buf 再导入误走 `KBASE_IOCTL_MEM_IMPORT`；现按 virtio GEM 身份复用已有 BO，并正确增加引用 | 已生成画面；native 输出及长时间验证待完成 |', '| niri DRM/GBM 后端直接走 zink | 按 virtio GEM 身份复用同设备 BO，正确增加引用 | native 输出已验证；长期稳定性待完成 |')
replace('### 7.2 zink 与合成的距离', '### 7.2 Zink/PanVK 合成与 native 输出')
replace('单调 BAR 游标不适合作为长期运行方案。`0x1200` 是通用错误响应，不能单独证明 share 未回收。源码已确认：crosvm 把 `remove_mapping` 失败折叠成 `ErrUnspec`；PCI mapper 又在 `unregister_memory` 成功前删除 offset 跟踪项，底层回收失败后重试会丢失该项。需要保留失败状态并记录底层错误，再验证 guest release、host reclaim 和 BAR 复用是否一致。该缺陷与既往 GPU 队列停滞的因果关系仍待设备回归。', '单调 BAR 游标不适合作为长期运行方案。当前 mapper 在 `unregister_memory` 成功后才删除 offset 跟踪项，并记录真实失败原因；固定 BAR 的复用回归已经通过。`0x1200` 是通用错误响应，不能单独证明 share 未回收，也不能把 KVM 实测外推为 Gunyah 回收已验收。')
replace('| guest 的 BAR offset 由 `drm_mm` 分配，跨进程会复用 | 复用 offset 命中旧 stage-2 页，表现为队列 io 页陈旧、CSF seqno 不推进 | 见 §7.3，当前依赖单调游标 |', '| 动态映射下 BAR offset 复用 | 曾出现队列 io 页陈旧与 CSF seqno 不推进 | 使用已验证的固定 BAR 与直接控制页配置，见 §7.3 |')
replace('| 独立 VM 盘 | 只使用 `/data/local/tmp/panvk-nctx/boot/` 下的盘与内核 |', '| 独立 VM 盘 | 当前只使用 `/data/local/tmp/panvk-stability-v1/boot/root-boot2.qcow2`，不触碰 `arch.img` |')
replace('| 独立 crosvm 运行时 | 使用 `/data/local/tmp/panvk-nctx/bin/crosvm` 与同目录 `lib/`，不经 app 的 dispatcher |', '| 独立 crosvm 运行时 | 使用 `/data/local/tmp/panvk-stability-v1/bin/` 与同目录 `lib/`，不经 app dispatcher |')
a = s.index('## 10. 当前阻塞')
s = s[:a] + '''## 10. 显示性能与零拷贝边界

native 原生显示描述的是 Android Surface 输出方式，不保证传输无 CPU 拷贝。

| 阶段 | 当前行为 | 优化条件 |
| --- | --- | --- |
| GPU 完成 | guest 等待真实执行完成 | 异步方案必须保持 fence 对源 BO 复用的顺序约束 |
| GPU BO → 共享影子内存 | 对已导出 BO 执行读回同步 | 需要更精确的 scanout/脏资源跟踪；不能无条件删掉，否则可能读到旧帧 |
| 共享影子内存 → crosvm | 保留首次导出 fd，映射复用 | 已避免重复导出；仍需测量每帧实际读写量 |
| crosvm → Android Surface | CPU 拷贝，必要时像素格式转换 | 分别测量 Surface 锁定、转换/拷贝与提交，再决定批量转换或 GPU blit |
| 真正零拷贝 | 未实现 | Android 可导入的真实 GPU allocation、匹配的格式/modifier、完整 acquire/release fence 与生命周期 |

`GPU_DISPLAY_COPY_MODE=zero` 只改变尝试的输出分支，不能把共享影子 memfd 变成 GPU dma-buf。宿主现有导入 API 也不证明 Mali/PanVK 这一 allocation 可导入。优化必须以相同分辨率和操作的基线测量为依据；不得以删同步、跳过重连首帧或未经说明的丢帧作为性能收益。

首轮影子同步 ICD 部署后仍黑屏，重启到有界像素诊断版后才出现画面。诊断并非有意改变数据路径，因此启动与 Surface 时序仍待复现，不能把全部黑屏归为一个已修复根因。

## 11. 输入接入与后续验收

### 11.1 独立输入路径

现有 DroidVM `NativeDisplayBinder.writeInput()` 查找 daemon 管理的 VMInstance；未知 standalone ID 返回 false。只把 Activity 的 `input_enabled` 改为 true 不会建立输入设备。已检查的 `CrosvmBackendInstance` 仍硬编码共享 prebuilt crosvm，不能假设已有 per-VM runtime 路径配置。

独立测试实现使用包名 `io.github.collegeming.panvk`，复用 DroidVM 的事件编码与 native Surface 协议。root 输入代理预监听 PanVK 专用 socket，crosvm 以客户端身份连接；前端使用 Binder 发送固定通道的 evdev 记录。代理按调用 UID 和会话令牌鉴权，不接受任意 VM、路径或服务名。该方案不替换已安装 `cn.classfun.droidvm`，也不代表现有 app 的 daemon 启动管理已经接通。

| 输入能力 | 状态与验收条件 |
| --- | --- |
| 触摸点击、拖动、多点触摸 | 编码路径已有；需 Android 事件、代理计数、guest evdev 与实际桌面行为相互对应 |
| 鼠标移动、按钮、滚轮 | 独立通道已有；实体鼠标与 Android 注入分别记录，不相互替代 |
| 键盘与软键盘 | 前台事件转发与 ASCII 发送已有；不抢占宿主全局键盘，不宣称系统快捷键全部可转发 |
| 失焦、断连释放 | 实现释放按键与触点；设备卡键、断连和重连测试待完成 |
| 设备部署 | CI 编译通过，安装返回 `INSTALL_FAILED_USER_RESTRICTED: Install canceled by user`；未绕过限制，输入保持未验收 |

### 11.2 后续待办与完成判据

| 优先级 | 事项 | 完成判据 |
| --- | --- | --- |
| 高 | 独立输入代理与 native 前端设备验收 | 安装获准后，触摸、键盘、鼠标各自取得 guest 事件与 UI 响应证据 |
| 高 | native 冷启动与 Surface 重连 | 有界多轮启动、离开/返回页面后均恢复动态画面，确认首帧与资源回收 |
| 高 | 60 分钟端到端稳定性 | 记录宿主/guest boot ID、进程、内存、输入与 GPU 错误；正常关闭后资源释放 |
| 高 | 显示性能测量 | 同场景比较 GPU 等待、读回、Surface 提交耗时与实际帧率；优化前后均保留正确性证据 |
| 中 | 配对 rootfs 固化 | 干净镜像直接包含最终 ICD、unit/config 和 manifest，无测试安装步骤遗漏 |
| 中 | app 管理的独立 PanVK VM | 独立 runtime 选择、daemon 输入与启动集成成立，且不写 gfxstream 共享入口 |
| 中 | 跨 context BO 导入 | 外来 BO 的身份、引用、同步与失败路径分别验证；不以同设备复用结果代替 |
| 中 | Noctalia Qt GPU 绘制 | 在明确切换 Qt 后端后单独验证，不混同 niri GPU 合成 |

### 11.3 每次设备回归的检查

- 启动前同时检查 `/data/local/tmp/vm-lock` 与实际 crosvm 进程；他方持锁或运行 GPU VM 时不启动。
- 保留已验证基线；新 runtime/ICD/APK 按独立文件名与哈希部署，不覆盖共享运行库。
- 关联同一 niri PID 的 renderer、GPU 执行、动态 native 画面；maps 或截图均不单独作为 GPU 合成证据。
- 停机先执行 guest `sync; systemctl poweroff`，等待 crosvm 正常退出；不得用 `pkill -f crosvm` 或 SIGKILL 清场。
- 收尾检查宿主 boot ID、共享文件哈希、独立 tap/路由/转发/代理，并释放本路线锁。

旧盘 switch-root exit 127 与 guest panic 有原始证据；新 CI 镜像已正常启动，旧盘失败原因仍未最终定位。旧 `run80.log` 的 guest reset 不构成宿主 panic 证据；pstore 读取被拒绝也不代表目录为空。相关原始分析保留于 [只读取证记录](../route-b/evidence-panvk/20261106-readonly/assessment.md)。
'''
p.write_text(s, encoding='utf-8')
print('Updated current facts; original preserved:', backup)
