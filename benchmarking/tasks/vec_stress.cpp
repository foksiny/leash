#include <cstdio>
#include <vector>

int main() {
    int n = 200000;
    std::vector<int> v;
    v.reserve(n);
    for (int i = 0; i < n; i++) {
        v.push_back((int)(((long long)i * 131 + 12345 + (i % 97)) % 1000003));
    }
    long long sum = 0;
    for (size_t i = 0; i < v.size(); i++) {
        sum += v[i];
    }
    int popped = v.back();
    v.pop_back();
    printf("vec_stress n=%d sum=%lld size=%zu popped=%d\n", n, sum, v.size(), popped);
    return 0;
}
