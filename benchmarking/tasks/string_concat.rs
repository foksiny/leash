fn main() {
    let mut s = String::new();
    for i in 0..20000 {
        s.push('x');
        s.push_str(&i.to_string());
        s.push('-');
    }
    println!("string_concat bsize={} size={}", s.len(), s.chars().count());
}
