#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <qrencode.h>
#include <limits.h>

// Maximum size limit for payload to prevent slowdowns or heap overflows
#define MAX_PAYLOAD_SIZE 1024

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (!data || size < 6 || size > MAX_PAYLOAD_SIZE + 6) return 0;

    // Extract last 6 bytes as fuzzing parameters
    int version        = data[size - 6] % 41;                     // 0–40
    QRecLevel eclevel  = (QRecLevel)(data[size - 5] % 4);         // L, M, Q, H
    QRencodeMode mode  = (QRencodeMode)(data[size - 4] % 4);      // MODE_8, MODE_KANJI, etc.
    int case_sensitive = data[size - 3] % 2;
    int use_string     = data[size - 2] % 2;
    int pad_null       = data[size - 1] % 2;

    // Actual data for input buffer
    size_t payload_size = size - 6;
    if (payload_size == 0) return 0;

    char *buffer = (char *)malloc(payload_size + 1);
    if (!buffer) return 0;

    memcpy(buffer, data, payload_size);
    buffer[payload_size] = pad_null ? '\0' : 'X';  // Ensure null-termination if needed

    QRcode *qrcode = NULL;

    if (use_string) {
        // Use string-based encoding
        buffer[payload_size] = '\0';  // Just to be safe
        qrcode = QRcode_encodeString(buffer, version, eclevel, mode, case_sensitive);
    } else {
        // Use binary data encoding
        qrcode = QRcode_encodeData(payload_size, (unsigned char *)buffer, version, eclevel);
    }

    if (qrcode) {
        QRcode_free(qrcode);  // Free result to prevent memory leaks
    }

    free(buffer);  // Clean up input buffer
    return 0;
}
