fn main() {
    let v: Vec<f64> = (0..100000).map(|i| (i % 100) as f64 / 10.0).collect();
    let mut sum = 0.0f64;
    for (i, &x) in v.iter().enumerate() {
        sum += x * (i as f64) / 100.0 + 0.123;
    }
    println!("fp_math sum={}", sum);
}
