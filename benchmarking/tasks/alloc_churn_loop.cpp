#include <cstdio>
#include <string>
#include <vector>

int main() {
    int total = 0;
    for (int i = 0; i < 100000; i++) {
        std::vector<int> tmp;
        for (int k = 0; k < 8; k++) {
            tmp.push_back(i + k);
        }
        int round = tmp[0] + tmp[7];
        std::string tag = "k" + std::to_string(i % 89);
        total += round % 1000 + (int)tag.size();
    }
    printf("alloc_churn total=%d\n", total);
    return 0;
}
