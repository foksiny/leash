import sys

def fib_rec(n):
    if n <= 1:
        return n
    return fib_rec(n - 1) + fib_rec(n - 2)

def fib_iter(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b

def main():
    n = 40
    result_rec = fib_rec(n)
    result_iter = fib_iter(n)
    print(f"fib_rec({n}): {result_rec}")
    print(f"fib_iter({n}): {result_iter}")

if __name__ == "__main__":
    main()
