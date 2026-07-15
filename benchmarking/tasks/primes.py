import sys

def sieve_count(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    i = 2
    while i * i <= n:
        if is_prime[i]:
            for j in range(i * i, n + 1, i):
                is_prime[j] = False
        i += 1
    return sum(is_prime)

def main():
    n = 1000000
    result = sieve_count(n)
    print(f"primes up to {n}: {result}")

if __name__ == "__main__":
    main()
