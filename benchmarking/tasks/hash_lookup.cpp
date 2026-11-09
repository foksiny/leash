#include <cstdio>
#include <string>
#include <unordered_map>

int main() {
    std::unordered_map<std::string, int> h;
    int n = 20000;
    for (int i = 0; i < n; i++) {
        h["key" + std::to_string(i)] = i * 3 + 1;
    }
    long long sum = 0;
    for (int i = 0; i < n; i += 2) {
        sum += h["key" + std::to_string(i)];
    }
    printf("hash_lookup n=%d sum=%lld size=%zu\n", n, sum, h.size());
    return 0;
}
