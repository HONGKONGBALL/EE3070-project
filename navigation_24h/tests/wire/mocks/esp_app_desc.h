#pragma once
struct esp_app_desc_t { const char* version; };
const esp_app_desc_t* esp_app_get_description();
