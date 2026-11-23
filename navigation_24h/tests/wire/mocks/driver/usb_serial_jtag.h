#pragma once
#include "freertos/FreeRTOS.h"
#include <cstddef>
int usb_serial_jtag_write_bytes(const void* bytes, std::size_t length, TickType_t timeout);
