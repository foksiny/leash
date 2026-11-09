#include <cstdio>
#include <vector>
#include <cmath>

int main() {
    std::vector<double> v(100000);
    for (size_t i = 0; i < v.size(); i++) v[i] = (i % 100) / 10.0;
    double sum = 0.0;
    for (size_t i = 0; i < v.size(); i++) {
        sum += v[i] * i / 100.0 + 0.123;
    }
    printf("fp_math sum=%f\n", sum);
    return 0;
}
