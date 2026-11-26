"""Python reference for benchmarking/tasks/ai_mlp.lsh.

Same arithmetic, same public output, no third-party packages. Used by the
AI-model benchmark test to compare numeric correctness, runtime and the
amount of code needed to express the model.
"""


def activate(x):
    if x > 12.0:
        return 1.0
    if x < -12.0:
        return -1.0
    ax = -x if x < 0.0 else x
    return x / (1.0 + ax * 0.25)


def predict(x, w0, w1, w2, b0, b1):
    h0 = activate(x * w0 + b0)
    h1 = activate(x * w1 + b0 * 0.5)
    return activate(h0 * w2 + h1 * (w2 * 0.75) + b1)


def main():
    w0, w1, w2, b0, b1 = 0.31, -0.17, 0.53, 0.07, -0.03
    n = 4000
    total = 0.0
    for i in range(n):
        x = (i % 32 - 16) / 16.0
        total += predict(x, w0, w1, w2, b0, b1)
    print("ai_mlp n=%d mean=%.6f" % (n, total / n))


if __name__ == "__main__":
    main()
