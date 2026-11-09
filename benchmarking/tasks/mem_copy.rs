fn main() {
    let src: Vec<i64> = (0..1_000_000).map(|i| i % 1009).collect();
    let mut dst: Vec<i64> = Vec::new();
    for _ in 0..10 {
        dst.clear();
        dst.extend_from_slice(&src);
    }
    let sum: i64 = dst.iter().sum();
    println!("mem_copy sum={} size={}", sum, dst.len());
}
