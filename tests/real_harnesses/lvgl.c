#include <stddef.h>
#include <stdint.h>
#include "lvgl/lvgl.h"

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if(size == 0) return 0;

    static int initialized;
    if(!initialized) {
        lv_init();
        initialized = 1;
    }

    lv_image_dsc_t image = {0};
    image.data = data;
    image.data_size = (uint32_t)size;
    image.header.cf = LV_COLOR_FORMAT_RAW;

    lv_image_header_t header = {0};
    (void)lv_image_decoder_get_info(&image, &header);

    return 0;
}