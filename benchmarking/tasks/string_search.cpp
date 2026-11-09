#include <cstdio>
#include <string>

int main() {
    std::string haystack;
    for (int i = 0; i < 2000; i++) haystack += "abcdefghijklmnopqrstuvwxyz0123456789";
    std::string needle = "mno";
    int found = 0;
    for (int iter = 0; iter < 100000; iter++) {
        if (haystack.find(needle) != std::string::npos) found++;
    }
    printf("string_search found=%d haystack_bsize=%zu\n", found, haystack.size());
    return 0;
}
