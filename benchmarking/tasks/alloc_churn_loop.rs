fn main() {
    let mut total: i64 = 0;
    for i in 0..100000 {
        let mut tmp: Vec<i64> = Vec::new();
        for k in 0..8 {
            tmp.push(i + k);
        }
        let round = tmp[0] + tmp[7];
        let tag = format!("k{}", i % 89);
        total += round % 1000 + tag.len() as i64;
    }
    println!("alloc_churn total={}", total);
}
