use std::collections::HashMap;

fn main() {
    let mut h: HashMap<String, i64> = HashMap::new();
    let n: i64 = 20000;
    for i in 0..n {
        h.insert(format!("key{}", i), i * 3 + 1);
    }
    let mut sum: i64 = 0;
    for i in (0..n).step_by(2) {
        sum += h[&format!("key{}", i)];
    }
    println!("hash_lookup n={} sum={} size={}", n, sum, h.len());
}
