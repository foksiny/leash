fn quicksort(v: &mut Vec<f64>, lo: isize, hi: isize) {
    if lo >= hi { return; }
    let pivot = v[(lo + (hi - lo) / 2) as usize];
    let mut i = lo;
    let mut j = hi;
    while i <= j {
        while v[i as usize] < pivot { i += 1; }
        while v[j as usize] > pivot { j -= 1; }
        if i <= j {
            v.swap(i as usize, j as usize);
            i += 1;
            j -= 1;
        }
    }
    if lo < j { quicksort(v, lo, j); }
    if i < hi { quicksort(v, i, hi); }
}

fn main() {
    let n = 20000usize;
    let mut v: Vec<f64> = Vec::with_capacity(n);
    for i in 0..n {
        v.push((i % 997) as f64 / 7.0 + ((i / 997) % 3) as f64 * 0.5);
    }
    quicksort(&mut v, 0, (n - 1) as isize);
    let mut checksum: i64 = 0;
    for (i, &x) in v.iter().enumerate() {
        let k = (x * 1000.0) as i64 + 1;
        checksum += (k % 10007) * (i as i64 + 1);
    }
    println!("{} {}", n, checksum);
}
