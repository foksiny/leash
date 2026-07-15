#include <stdio.h>

double pi_leibniz(int terms) {
    double pi = 0.0;
    int sign = 1;
    for (int i = 0; i < terms; i++) {
        double term = 1.0 / (double)(2 * i + 1);
        if (sign == 1)
            pi += term;
        else
            pi -= term;
        sign = -sign;
    }
    return pi * 4.0;
}

int main() {
    int terms = 10000000;
    double pi = pi_leibniz(terms);
    printf("pi (%d terms): %.10f\n", terms, pi);
    return 0;
}
