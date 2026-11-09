fn main() {
    let n: usize = 128;
    let mut a = vec![0i64; n * n];
    let mut b = vec![0i64; n * n];
    for i in 0..n * n {
        a[i] = (i % 17) as i64 + 1;
        b[i] = ((i * 7) % 13) as i64 + 1;
    }
    let mut c = vec![0i64; n * n];
    for i in 0..n {
        for j in 0..n {
            let mut sum: i64 = 0;
            for k in 0..n {
                sum += a[i * n + k] * b[k * n + j];
            }
            c[i * n + j] = sum;
        }
    }
    let mut total: i64 = 0;
    for i in 0..n * n {
        total += c[i] % 1009;
    }
    println!("matrix_mul n={} total={}", n, total);
}
