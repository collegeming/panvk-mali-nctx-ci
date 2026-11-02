#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#define PROPERTY_VALUE_MAX 92

int property_get(const char *key, char *value, const char *default_value);

#ifdef __cplusplus
}
#endif
