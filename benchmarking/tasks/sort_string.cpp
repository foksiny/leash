#include <cstdio>
#include <cstdint>
#include <string>
#include <vector>
#include <algorithm>

int main() {
    int n = 5000;
    std::vector<std::string> v;
    int64_t state = 123456789;
    for (int r = 0; r < n; r++) {
        state = state * 1103515245 + 12345;
        int len = 5 + (int)((state >> 8) % 11);
        std::string s;
        for (int c = 0; c < len; c++) {
            state = state * 1103515245 + 12345;
            s.push_back((char)(97 + ((state >> 10) % 26)));
        }
        v.push_back(s);
    }
    std::sort(v.begin(), v.end());
    long long total = 0;
    int dups = 0;
    for (size_t i = 0; i < v.size(); i++) {
        for (char ch : v[i]) total += (unsigned char)ch * (long long)(i + 1);
        if (i > 0 && v[i] == v[i - 1]) dups++;
    }
    printf("%d %lld %d\n", n, total, dups);
    return 0;
}
