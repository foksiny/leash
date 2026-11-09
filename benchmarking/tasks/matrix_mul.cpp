#include <cstdio>
#include <vector>

int main() {
    int n = 128;
    std::vector<int> a(n * n), b(n * n);
    for (int i = 0; i < n * n; i++) {
        a[i] = (i % 17) + 1;
        b[i] = ((i * 7) % 13) + 1;
    }
    std::vector<int> c(n * n);
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            int sum = 0;
            for (int k = 0; k < n; k++) {
                sum += a[i * n + k] * b[k * n + j];
            }
            c[i * n + j] = sum;
        }
    }
    long long total = 0;
    for (int i = 0; i < n * n; i++) {
        total += c[i] % 1009;
    }
    printf("matrix_mul n=%d total=%lld\n", n, total);
    return 0;
}
