import sys

def pi_leibniz(terms):
    pi = 0.0
    sign = 1
    for i in range(terms):
        term = 1.0 / (2 * i + 1)
        if sign == 1:
            pi += term
        else:
            pi -= term
        sign = -sign
    return pi * 4.0

def main():
    terms = 10000000
    pi = pi_leibniz(terms)
    print(f"pi ({terms} terms): {pi:.10f}")

if __name__ == "__main__":
    main()
