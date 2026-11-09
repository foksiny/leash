#include <cstdio>
#include <string>

int main() {
    std::string s;
    for (int i = 0; i < 20000; i++) {
        s += "x";
        s += std::to_string(i);
        s += "-";
    }
    printf("string_concat bsize=%zu size=%zu\n", s.size(), s.size());
    return 0;
}
