#include <string.h>

#include "cutils/properties.h"

int property_get(const char *key, char *value, const char *default_value)
{
   size_t len;

   (void)key;
   if (!value)
      return 0;

   if (default_value) {
      strncpy(value, default_value, PROPERTY_VALUE_MAX - 1);
      value[PROPERTY_VALUE_MAX - 1] = '\0';
   } else {
      value[0] = '\0';
   }

   len = strlen(value);
   return (int)len;
}
