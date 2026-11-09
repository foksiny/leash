#include <cstdio>
#include <vector>

int main() {
    std::vector<int> src(1000000);
    for (size_t i = 0; i < src.size(); i++) src[i] = (int)(i % 1009);
    std::vector<int> dst;
    dst.reserve(src.size());
    for (int iter = 0; iter < 10; iter++) {
        dst.clear();
        dst.insert(dst.end(), src.begin(), src.end());
    }
    long long sum = 0;
    for (int x : dst) sum += x;
    printf("mem_copy sum=%lld size=%zu\n", sum, dst.size());
    return 0;
}
