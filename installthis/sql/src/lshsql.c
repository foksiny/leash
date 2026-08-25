/*
 * lshsql.c — native SQLite binding shim for the Leash `sql` stdlib package.
 *
 * Thin wrapper around the SQLite amalgamation (sqlite3.c, vendored next to
 * this file) so Leash programs get an embedded, zero-configuration SQL
 * database through the @from FFI directive. The archive is fully
 * self-contained: SQLite needs no external libraries on any platform.
 *
 * Strings handed back to Leash are allocated with leash_gc_alloc_string()
 * so they are ordinary garbage-collected Leash strings — callers never free
 * anything. Pointers to sqlite3/sqlite3_stmt objects cross the boundary as
 * opaque pointer<void> handles managed by the Leash Db/Stmt classes.
 *
 * Build (see README.md in this directory):
 *   Linux/macOS:  sh build.sh            (produces ../<platform>/liblshsql.a)
 *   Windows:      build.bat              (produces ..\win\liblshsql.a)
 */

#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "sqlite3.h"

/* Strings handed back to Leash come from the Leash runtime allocator. */
extern void *leash_gc_alloc_string(long long len);

static char *lsql_gc_string(const char *data, long long len) {
    char *out = (char *)leash_gc_alloc_string(len);
    if (!out) return NULL;
    if (len > 0 && data) memcpy(out, data, (size_t)len);
    return out; /* alloc_string zeroes, so out[len] == '\0' */
}

/* ------------------------------------------------------------------ */
/* Database handles                                                   */
/* ------------------------------------------------------------------ */

void *lsql_open(const char *path) {
    sqlite3 *db = NULL;
    if (!path) return NULL;
    if (sqlite3_open_v2(path, &db,
                        SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, NULL) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return NULL;
    }
    /* WAL journal + normal sync: fast and crash-safe for typical apps. */
    sqlite3_exec(db, "PRAGMA journal_mode=WAL;", NULL, NULL, NULL);
    sqlite3_busy_timeout(db, 5000);
    return (void *)db;
}

void lsql_close(void *handle) {
    if (handle) sqlite3_close((sqlite3 *)handle);
}

/* Executes one or more statements that return no rows.
 * Returns SQLITE_OK (0) or an SQLite error code. */
int lsql_exec(void *handle, const char *sql) {
    if (!handle || !sql) return SQLITE_MISUSE;
    char *errmsg = NULL;
    int rc = sqlite3_exec((sqlite3 *)handle, sql, NULL, NULL, &errmsg);
    if (errmsg) sqlite3_free(errmsg);
    return rc;
}

/* Human-readable message for the most recent error on `handle`. */
char *lsql_errmsg(void *handle) {
    if (!handle) {
        static const char *closed_msg = "database is closed";
        return lsql_gc_string(closed_msg, (long long)strlen(closed_msg));
    }
    const char *msg = sqlite3_errmsg((sqlite3 *)handle);
    return msg ? lsql_gc_string(msg, (long long)strlen(msg))
               : lsql_gc_string("", 0);
}

long long lsql_changes(void *handle) {
    if (!handle) return -1;
    return (long long)sqlite3_changes64((sqlite3 *)handle);
}

long long lsql_last_insert_rowid(void *handle) {
    if (!handle) return -1;
    return (long long)sqlite3_last_insert_rowid((sqlite3 *)handle);
}

/* ------------------------------------------------------------------ */
/* Prepared statements                                                */
/* ------------------------------------------------------------------ */

void *lsql_prepare(void *handle, const char *sql) {
    if (!handle || !sql) return NULL;
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2((sqlite3 *)handle, sql, -1, &st, NULL) != SQLITE_OK) {
        return NULL;
    }
    return (void *)st;
}

int lsql_bind_int(void *stmt, int idx, long long v) {
    if (!stmt) return SQLITE_MISUSE;
    return sqlite3_bind_int64((sqlite3_stmt *)stmt, idx, (sqlite3_int64)v);
}

int lsql_bind_float(void *stmt, int idx, double v) {
    if (!stmt) return SQLITE_MISUSE;
    return sqlite3_bind_double((sqlite3_stmt *)stmt, idx, v);
}

int lsql_bind_text(void *stmt, int idx, const char *v) {
    if (!stmt) return SQLITE_MISUSE;
    if (!v) return sqlite3_bind_null((sqlite3_stmt *)stmt, idx);
    return sqlite3_bind_text((sqlite3_stmt *)stmt, idx, v, -1, SQLITE_TRANSIENT);
}

int lsql_bind_null(void *stmt, int idx) {
    if (!stmt) return SQLITE_MISUSE;
    return sqlite3_bind_null((sqlite3_stmt *)stmt, idx);
}

/* Returns SQLITE_ROW (100), SQLITE_DONE (101) or an error code. */
int lsql_step(void *stmt) {
    if (!stmt) return SQLITE_MISUSE;
    return sqlite3_step((sqlite3_stmt *)stmt);
}

int lsql_reset(void *stmt) {
    if (!stmt) return SQLITE_MISUSE;
    return sqlite3_reset((sqlite3_stmt *)stmt);
}

int lsql_finalize(void *stmt) {
    if (!stmt) return SQLITE_MISUSE;
    return sqlite3_finalize((sqlite3_stmt *)stmt);
}

/* ------------------------------------------------------------------ */
/* Column access (valid between step() returning ROW and the next call)*/
/* ------------------------------------------------------------------ */

int lsql_column_count(void *stmt) {
    if (!stmt) return 0;
    return sqlite3_column_count((sqlite3_stmt *)stmt);
}

char *lsql_column_name(void *stmt, int i) {
    if (!stmt) return lsql_gc_string("", 0);
    const char *n = sqlite3_column_name((sqlite3_stmt *)stmt, i);
    return n ? lsql_gc_string(n, (long long)strlen(n)) : lsql_gc_string("", 0);
}

/* SQLITE_INTEGER=1, SQLITE_FLOAT=2, SQLITE_TEXT=3, SQLITE_BLOB=4, NULL=5 */
int lsql_column_type(void *stmt, int i) {
    if (!stmt) return 0;
    int t = sqlite3_column_type((sqlite3_stmt *)stmt, i);
    if (t == SQLITE_NULL) return 5;
    if (t == SQLITE_FLOAT) return 2;
    if (t == SQLITE_TEXT) return 3;
    if (t == SQLITE_BLOB) return 4;
    return 1;
}

long long lsql_column_int(void *stmt, int i) {
    if (!stmt) return 0;
    return (long long)sqlite3_column_int64((sqlite3_stmt *)stmt, i);
}

double lsql_column_float(void *stmt, int i) {
    if (!stmt) return 0.0;
    return sqlite3_column_double((sqlite3_stmt *)stmt, i);
}

/* Copies the cell text into a fresh GC string; NULL cells yield "". */
char *lsql_column_text(void *stmt, int i) {
    if (!stmt) return lsql_gc_string("", 0);
    const unsigned char *t = sqlite3_column_text((sqlite3_stmt *)stmt, i);
    if (!t) return lsql_gc_string("", 0);
    int n = sqlite3_column_bytes((sqlite3_stmt *)stmt, i);
    return lsql_gc_string((const char *)t, (long long)n);
}
