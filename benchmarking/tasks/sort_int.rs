fn quicksort(v: &mut Vec<i64>, lo: isize, hi: isize) {
    if lo >= hi { return; }
    let pivot = v[(lo + (hi - lo) / 2) as usize];
    let mut i = lo;
    let mut j = hi;
    while i <= j {
        while v[i as usize] < pivot { i += 1; }
        while v[j as usize] > pivot { j -= 1; }
        if i <= j {
            let tmp = v[i as usize];
            v[i as usize] = v[j as usize];
            v[j as usize] = tmp;
            i += 1;
            j -= 1;
        }
    }
    if lo < j { quicksort(v, lo, j); }
    if i < hi { quicksort(v, i, hi); }
}

fn main() {
    let n = 50000_usize;
    let mut v: Vec<i64> = Vec::with_capacity(n);
    for i in (0..n).rev() {
        v.push(((i as i64 * 131 + 12345 + (i as i64 % 97)) % 1000003) as i64);
    }
    let len = v.len() as isize;
    quicksort(&mut v, 0, len - 1);
    let mut sum: i64 = 0;
    for (i, &x) in v.iter().enumerate() {
        sum += x * (i as i64 + 1);
    }
    println!("sort_int n={} sum={}", n, sum);
}
