#include <cstdio>
#include <vector>
#include <algorithm>

static void quicksort(std::vector<double>& v, int lo, int hi) {
    if (lo >= hi) return;
    double pivot = v[lo + (hi - lo) / 2];
    int i = lo, j = hi;
    while (i <= j) {
        while (v[i] < pivot) i++;
        while (v[j] > pivot) j--;
        if (i <= j) {
            double tmp = v[i]; v[i] = v[j]; v[j] = tmp;
            i++; j--;
        }
    }
    if (lo < j) quicksort(v, lo, j);
    if (i < hi) quicksort(v, i, hi);
}

int main() {
    int n = 20000;
    std::vector<double> v;
    for (int i = 0; i < n; i++) {
        v.push_back((i % 997) / 7.0 + (double)((i / 997) % 3) * 0.5);
    }
    quicksort(v, 0, (int)v.size() - 1);
    long long checksum = 0;
    for (size_t i = 0; i < v.size(); i++) {
        long long k = (long long)(v[i] * 1000) + 1;
        checksum += (k % 10007) * (long long)(i + 1);
    }
    printf("%d %lld\n", n, checksum);
    return 0;
}
