#include <stdio.h>

int fib_rec(int n) {
    if (n <= 1) return n;
    return fib_rec(n - 1) + fib_rec(n - 2);
}

int fib_iter(int n) {
    if (n <= 1) return n;
    int a = 0, b = 1;
    for (int i = 2; i <= n; i++) {
        int temp = a + b;
        a = b;
        b = temp;
    }
    return b;
}

int main() {
    int n = 40;
    int result_rec = fib_rec(n);
    int result_iter = fib_iter(n);
    printf("fib_rec(%d): %d\n", n, result_rec);
    printf("fib_iter(%d): %d\n", n, result_iter);
    return 0;
}
