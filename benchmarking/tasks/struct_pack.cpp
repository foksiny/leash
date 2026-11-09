#include <cstdio>
#include <vector>

struct Node {
    int key;
    int val;
};

static void quicksort(std::vector<Node>& v, int lo, int hi) {
    if (lo >= hi) return;
    Node pivot = v[lo + (hi - lo) / 2];
    int i = lo, j = hi;
    while (i <= j) {
        while (v[i].key < pivot.key) i++;
        while (v[j].key > pivot.key) j--;
        if (i <= j) {
            Node tmp = v[i]; v[i] = v[j]; v[j] = tmp;
            i++; j--;
        }
    }
    if (lo < j) quicksort(v, lo, j);
    if (i < hi) quicksort(v, i, hi);
}

int main() {
    int n = 5000;
    std::vector<Node> v;
    for (int i = 0; i < n; i++) {
        v.push_back(Node{i % 97, (i * 31) % 1009});
    }
    quicksort(v, 0, (int)v.size() - 1);
    long long total = 0;
    for (size_t i = 0; i < v.size(); i++) {
        total += (long long)v[i].val * (long long)(i + 1);
    }
    printf("struct_pack n=%d total=%lld\n", n, total);
    return 0;
}
