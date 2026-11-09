#include <cstdio>
#include <string>

int main() {
    int n = 100000;
    std::string s;
    for (int i = 0; i < n; i++) {
        s.push_back((char)((i * 13 + 7) % 26 + 97));
    }
    std::string rev;
    for (int j = n - 1; j >= 0; j--) {
        rev.push_back(s[j]);
    }
    long long total = 0;
    for (size_t k = 0; k < rev.size(); k++) {
        total += (unsigned char)rev[k];
    }
    char head[4] = {rev[0], rev[1], rev[2], 0};
    char tail[4] = {rev[n - 3], rev[n - 2], rev[n - 1], 0};
    printf("%zu %lld %s %s\n", rev.size(), total, head, tail);
    return 0;
}
