fn main() {
    let mut hay = String::new();
    for _ in 0..2000 {
        hay.push_str("abcdefghijklmnopqrstuvwxyz0123456789");
    }
    let needle = "mno";
    let mut found = 0;
    for _ in 0..100000 {
        if hay.contains(needle) { found += 1; }
    }
    println!("string_search found={} haystack_bsize={}", found, hay.len());
}
