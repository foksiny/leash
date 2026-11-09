use std::string::String;
fn main() {
    let n = 5000;
    let mut v: Vec<String> = Vec::new();
    let mut state: i64 = 123456789;
    for _ in 0..n {
        state = state * 1103515245 + 12345;
        let raw = (state >> 8) % 11;
        let len = if 5 + raw <= 0 { 0 } else { (5 + raw) as usize };
        let mut s = String::new();
        for _ in 0..len {
            state = state * 1103515245 + 12345;
            let c = (state >> 10) % 26;
            s.push((97u8.wrapping_add(c as u8)) as char);
        }
        v.push(s);
    }
    v.sort();
    let mut total: i64 = 0;
    let mut dups = 0;
    for (i, s) in v.iter().enumerate() {
        for b in s.bytes() {
            total += b as i64 * (i as i64 + 1);
        }
        if i > 0 && v[i] == v[i - 1] {
            dups += 1;
        }
    }
    println!("{} {} {}", n, total, dups);
}
