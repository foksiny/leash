#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int sieve_count(int n) {
    char *is_prime = (char *)malloc((n + 1) * sizeof(char));
    if (!is_prime) return -1;
    memset(is_prime, 1, n + 1);
    is_prime[0] = is_prime[1] = 0;
    for (int i = 2; i * i <= n; i++) {
        if (is_prime[i]) {
            for (int j = i * i; j <= n; j += i) {
                is_prime[j] = 0;
            }
        }
    }
    int count = 0;
    for (int i = 2; i <= n; i++) {
        if (is_prime[i]) count++;
    }
    free(is_prime);
    return count;
}

int main() {
    int n = 1000000;
    int result = sieve_count(n);
    printf("primes up to %d: %d\n", n, result);
    return 0;
}
