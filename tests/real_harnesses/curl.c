#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <curl/curl.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0) return 0;

    char *url = malloc(size + 1);
    if (!url) return 0;
    memcpy(url, data, size);
    url[size] = '\0';

    CURLU *h = curl_url();
    if (h) {
        curl_url_set(h, CURLUPART_URL, url, 0);
        curl_url_cleanup(h);
    }

    free(url);
    return 0;
}