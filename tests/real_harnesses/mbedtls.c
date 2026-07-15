#include <stdint.h>
#include "mbedtls/pkcs7.h"

int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size)
{
    mbedtls_pkcs7 pkcs7;

    mbedtls_pkcs7_init(&pkcs7);

    mbedtls_pkcs7_parse_der(&pkcs7, Data, Size);

    mbedtls_pkcs7_free(&pkcs7);

    return 0;
}