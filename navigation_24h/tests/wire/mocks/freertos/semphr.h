#pragma once
#include "FreeRTOS.h"
#include <mutex>
using SemaphoreHandle_t = std::mutex*;
inline SemaphoreHandle_t xSemaphoreCreateMutex() { static std::mutex value; return &value; }
inline BaseType_t xSemaphoreTake(SemaphoreHandle_t value, TickType_t) { value->lock(); return pdTRUE; }
inline BaseType_t xSemaphoreGive(SemaphoreHandle_t value) { value->unlock(); return pdTRUE; }
