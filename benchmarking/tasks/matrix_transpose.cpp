#include <cstdio>
#include <vector>

int main() {
    int n = 256;
    std::vector<int> a(n * n);
    for (int i = 0; i < n * n; i++) a[i] = (i % 23) + 1;
    std::vector<int> b(n * n);
    for (int j = 0; j < n; j++) {
        for (int i = 0; i < n; i++) {
            b[j * n + i] = a[i * n + j];
        }
    }
    long long total = 0;
    for (size_t k = 0; k < b.size(); k++) {
        total += (long long)b[k] * (long long)(k % 97 + 1);
    }
    printf("matrix_transpose n=%d total=%lld\n", n, total);
    return 0;
}
