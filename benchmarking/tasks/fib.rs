fn fib_rec(n: u64) -> u64 {
    if n <= 1 {
        return n;
    }
    fib_rec(n - 1) + fib_rec(n - 2)
}

fn fib_iter(n: u64) -> u64 {
    if n <= 1 {
        return n;
    }
    let (mut a, mut b) = (0, 1);
    for _ in 2..=n {
        let temp = a + b;
        a = b;
        b = temp;
    }
    b
}

fn main() {
    let n: u64 = 40;
    let result_rec = fib_rec(n);
    let result_iter = fib_iter(n);
    println!("fib_rec({}): {}", n, result_rec);
    println!("fib_iter({}): {}", n, result_iter);
}
