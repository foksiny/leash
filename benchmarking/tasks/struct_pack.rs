#[derive(Clone)]
struct Node {
    key: i64,
    val: i64,
}

fn quicksort(v: &mut Vec<Node>, lo: isize, hi: isize) {
    if lo >= hi { return; }
    let pivot = v[(lo + (hi - lo) / 2) as usize].clone();
    let mut i = lo;
    let mut j = hi;
    while i <= j {
        while v[i as usize].key < pivot.key { i += 1; }
        while v[j as usize].key > pivot.key { j -= 1; }
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
    let n = 5000usize;
    let mut v: Vec<Node> = Vec::with_capacity(n);
    for i in 0..n {
        v.push(Node {
            key: (i % 97) as i64,
            val: ((i * 31) % 1009) as i64,
        });
    }
    quicksort(&mut v, 0, (n - 1) as isize);
    let mut total: i64 = 0;
    for (i, nd) in v.iter().enumerate() {
        total += nd.val * (i as i64 + 1);
    }
    println!("struct_pack n={} total={}", n, total);
}
