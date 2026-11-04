#include <stdint.h>
#include <cpu-features.h>

AndroidCpuFamily android_getCpuFamily(void)
{
   return ANDROID_CPU_FAMILY_UNKNOWN;
}

uint64_t android_getCpuFeatures(void)
{
   return 0;
}
