fn pi_leibniz(terms: i64) -> f64 {
    let mut pi = 0.0;
    let mut sign = 1;
    for i in 0..terms {
        let term = 1.0 / (2 * i + 1) as f64;
        if sign == 1 {
            pi += term;
        } else {
            pi -= term;
        }
        sign = -sign;
    }
    pi * 4.0
}

fn main() {
    let terms = 10_000_000i64;
    let pi = pi_leibniz(terms);
    println!("pi ({} terms): {:.10}", terms, pi);
}
