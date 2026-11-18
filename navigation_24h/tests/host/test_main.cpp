#include "test_support.hpp"
#include <iostream>

int main(int argc, char** argv) {
    std::string filter;
    if (argc == 3 && std::string(argv[1]) == "--case") filter = argv[2];
    std::vector<test::Case> all;
    for (auto suite : {test::fix_cases(), test::lifecycle_cases(),
                       test::navigation_cases(), test::matcher_cases()})
        for (auto& item : suite) all.push_back(std::move(item));
    std::size_t ran = 0, failed = 0;
    for (const auto& item : all) {
        if (!filter.empty() && item.id != filter) continue;
        ++ran;
        try {
            item.run();
            std::cout << "{\"case_id\":\"" << item.id << "\",\"fixture\":\"" << item.name
                      << "\",\"scope\":\"host_core\",\"result\":\"passed\"}\n";
        } catch (const test::Failure& failure) {
            ++failed;
            std::cout << "{\"case_id\":\"" << item.id << "\",\"fixture\":\"" << item.name
                      << "\",\"scope\":\"host_core\",\"result\":\"failed\",\"detail\":\""
                      << failure.what() << "\"}\n";
        }
    }
    std::cout << "{\"summary\":true,\"executed\":" << ran << ",\"failed\":" << failed << "}\n";
    return ran > 0 && failed == 0 ? 0 : 1;
}
