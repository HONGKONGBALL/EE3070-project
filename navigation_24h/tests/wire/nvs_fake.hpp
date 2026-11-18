#pragma once
#include <string>

namespace nvs_test {
void reset();
void seed(const std::string& name, const std::string& key, const std::string& value);
std::string stored(const std::string& name, const std::string& key);
}
