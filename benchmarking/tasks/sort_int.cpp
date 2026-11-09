#include <cstdio>
#include <cstdint>
#include <vector>

static void quicksort(std::vector<int>& v, int lo, int hi) {
    if (lo >= hi) return;
    int pivot = v[lo + (hi - lo) / 2];
    int i = lo, j = hi;
    while (i <= j) {
        while (v[i] < pivot) i++;
        while (v[j] > pivot) j--;
        if (i <= j) {
            int tmp = v[i]; v[i] = v[j]; v[j] = tmp;
            i++; j--;
        }
    }
    if (lo < j) quicksort(v, lo, j);
    if (i < hi) quicksort(v, i, hi);
}

int main() {
    int n = 50000;
    std::vector<int> v;
    v.reserve(n);
    for (int i = n - 1; i >= 0; --i) {
        v.push_back((int)(((long long)i * 131 + 12345 + (i % 97)) % 1000003));
    }
    quicksort(v, 0, (int)v.size() - 1);
    long long sum = 0;
    for (size_t i = 0; i < v.size(); ++i) sum += (long long)v[i] * (i + 1);
    printf("sort_int n=%d sum=%lld\n", n, sum);
    return 0;
}
