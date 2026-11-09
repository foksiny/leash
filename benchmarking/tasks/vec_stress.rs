fn main() {
    let n: i64 = 200000;
    let mut v: Vec<i64> = Vec::with_capacity(200000);
    for i in 0..n {
        v.push((i * 131 + 12345 + (i % 97)) % 1000003);
    }
    let mut sum: i64 = 0;
    for i in 0..v.len() {
        sum += v[i];
    }
    let popped = v.pop().unwrap_or(0);
    println!("vec_stress n={} sum={} size={} popped={}", n, sum, v.len(), popped);
}
