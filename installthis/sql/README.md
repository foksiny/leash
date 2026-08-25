# sql — embedded SQL (SQLite) for Leash

Zero-configuration embedded SQL database powered by the SQLite amalgamation,
bound through Leash's `@from` FFI directive. The native archive is fully
self-contained on every platform — SQLite is vendored in `src/sqlite3.c` and
compiled in, so no external packages are ever needed:

| Platform | Archive |
|----------|---------|
| Linux    | `linux/liblshsql.a` |
| Windows  | `win/liblshsql.a` |
| macOS    | build once with `src/build.sh` |

## Usage

```leash
use sql::sql::*;

fnc main() : void {
    db: Db = Db.open("app.db");
    db.exec("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, score REAL)");

    // Prepared statement with typed bindings
    ins: Stmt = db.prepare("INSERT INTO users (name, score) VALUES (?, ?)");
    ins.bind_text(1, "alice");
    ins.bind_float(2, 91.5);
    while ins.step() { empty; }
    ins.finalize();

    // Queries return typed cells
    res: QueryResult = db.query("SELECT id, name FROM users");
    foreach _, row in<vector> res.rows {
        show(row.get(0).as_int(), row.get(1).as_text());
    }
    db.close();
}
```

Run with the stdlib on the import path:

```bash
leash run main.lsh --other-imports installthis
```

## API

### `Db`

| Member | Description |
|--------|-------------|
| `open(path)` / `open_memory()` | Open or create a database file / transient in-memory DB; check `.ok` after opening |
| `exec(sql) : bool` | Run DDL/DML returning no rows; false + `last_error()` on failure |
| `prepare(sql) : Stmt` | Compile a statement for binding and manual stepping |
| `exec_args(sql, args vec<string>)` | Convenience exec with all parameters bound as text |
| `query(sql) : QueryResult` | Fetch all rows of a query |
| `query_args(sql, args) : QueryResult` | Same with text-bound parameters |
| `transaction(stmts vec<string>) : bool` | BEGIN/.../COMMIT with automatic ROLLBACK on failure |
| `changes() : int<64>` / `last_insert_rowid()` | Statement effect metadata |
| `close()` | Release the database handle |

WAL journaling and a 5 s busy timeout are enabled automatically.

### `Stmt`

| Member | Description |
|--------|-------------|
| `bind_int / bind_float / bind_text / bind_null(idx, v)` | Bind 1-based parameters |
| `step() : bool` | True while a row is available |
| `int_at / float_at / text_at(i)` / `column_type(i)` | Typed cell access on the current row |
| `row() : vec<SqlValue>` / `query() : QueryResult` | Whole-row / whole-result fetch |
| `reset()` | Reuse the statement with new bindings |
| `finalize()` | Release the statement (idempotent) |

### `QueryResult` & `SqlValue`

`QueryResult` holds `cols: vec<string>`, `rows: vec<vec<SqlValue>>`, plus
`ok`/`err`. Cells are typed (`SINT`, `SFLOAT`, `STEXT`, `SBLOB`, `SNULL`) and
convert via `as_int()`, `as_float()`, `as_text()`; look them up by index with
`at(r, c)` or by name with `get(r, name)`.

## Rebuilding the native library

Prebuilt archives ship for Linux and Windows. After modifying the shim or
upgrading SQLite:

```bash
# Linux / macOS
cd src && sh build.sh

# Windows (MinGW-w64)
cd src && build.bat
```
