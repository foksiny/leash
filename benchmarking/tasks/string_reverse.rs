fn main() {
    let n = 100000;
    let mut s = String::new();
    for i in 0..n {
        s.push((b'a' + ((i * 13 + 7) % 26) as u8) as char);
    }
    let mut rev = String::new();
    for j in (0..n).rev() {
        rev.push(s.as_bytes()[j] as char);
    }
    let total: i64 = rev.bytes().map(|b| b as i64).sum();
    let head: String = rev.chars().take(3).collect();
    let tail: String = rev.chars().skip(n - 3).collect();
    println!("{} {} {} {}", rev.len(), total, head, tail);
}
