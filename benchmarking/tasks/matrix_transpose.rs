fn main() {
    let n: usize = 256;
    let mut a = vec![0i64; n * n];
    for i in 0..n * n {
        a[i] = (i % 23) as i64 + 1;
    }
    let mut b = vec![0i64; n * n];
    for j in 0..n {
        for i in 0..n {
            b[j * n + i] = a[i * n + j];
        }
    }
    let mut total: i64 = 0;
    for (k, &x) in b.iter().enumerate() {
        total += x * ((k % 97) as i64 + 1);
    }
    println!("matrix_transpose n={} total={}", n, total);
}
