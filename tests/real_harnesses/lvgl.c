#include <stdint.h>
#include <string.h>
#include "lvgl/lvgl.h"

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0) return 0;

    static int initialized = 0;
    if (!initialized) {
        lv_init();
        initialized = 1;
    }

    lv_img_dsc_t img_dsc = {0};
    img_dsc.data = data;
    img_dsc.data_size = size;
    img_dsc.header.cf = LV_IMG_CF_RAW;

    lv_img_decoder_dsc_t dsc;
    if (lv_img_decoder_open(&dsc, &img_dsc, NULL) == LV_RES_OK) {
        lv_img_decoder_close(&dsc);
    }

    return 0;
}