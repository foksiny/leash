// Leash JVM sql shim: implements the @from("liblshsql.a") surface for the
// --target jvm backend over a small SQL interpreter (no native code).
//
// Contract parity with installthis/sql/src/lshsql.c (which wraps SQLite):
//   * same lsql_* function names, argument shapes and return codes —
//     sqlite3 codes: OK=0, ROW=100, DONE=101, ERROR=1, RANGE=25, MISUSE=21,
//   * same column type numbering (1 INTEGER, 2 FLOAT, 3 TEXT, 5 NULL),
//   * prepare-time column metadata: lsql_column_count/name are answerable
//     before the first step, exactly like sqlite3_stmt,
//   * sqlite3_reset keeps its bindings; sqlite3_changes reports the last
//     DML row count; INTEGER PRIMARY KEY is the rowid alias with
//     auto-incrementing ids and last_insert_rowid(),
//   * SQLite's type affinity rules when storing into typed columns, and
//     affinity-aware comparisons in WHERE/ORDER BY,
//   * ":memory:" databases; file paths persist eagerly (not on close).
//
// Honest divergences from SQLite (documented in docs/limitations.html and
// installthis/sql/README.md): this is a small SQL interpreter, not SQLite.
// The supported surface is CREATE TABLE / INSERT / SELECT / UPDATE /
// DELETE / BEGIN / COMMIT / ROLLBACK with expressions (literals, ? binds,
// column refs, comparisons, AND/OR/NOT, arithmetic, COUNT). Database FILES
// use a simple line format of our own ("LEASHSQL1" magic) — they are NOT
// sqlite3 files, and an existing sqlite3 file fails to open.

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class LeashSql {

    // sqlite3 result codes.
    private static final int OK = 0;
    private static final int ERROR = 1;
    private static final int ROW = 100;
    private static final int DONE = 101;
    private static final int MISUSE = 21;
    private static final int RANGE = 25;

    // sqlite3_column_type codes.
    private static final int T_INT = 1;
    private static final int T_FLOAT = 2;
    private static final int T_TEXT = 3;
    private static final int T_BLOB = 4;
    private static final int T_NULL = 5;

    private LeashSql() { }

    // ------------------------------------------------------------------
    // Engine values, tables, databases, statements
    // ------------------------------------------------------------------

    static final class Value {
        final int type;            // T_INT / T_FLOAT / T_TEXT / T_NULL
        final long ival;
        final double fval;
        final String sval;

        static final Value NULL = new Value(T_NULL, 0, 0.0, "");

        Value(int type, long ival, double fval, String sval) {
            this.type = type;
            this.ival = ival;
            this.fval = fval;
            this.sval = sval;
        }

        static Value of(long v) { return new Value(T_INT, v, 0.0, ""); }
        static Value of(double v) { return new Value(T_FLOAT, 0, v, ""); }
        static Value of(String v) { return new Value(T_TEXT, 0, 0.0, v); }

        boolean isNull() { return type == T_NULL; }

        // As a double for numeric comparison.
        double asDouble() {
            if (type == T_INT) { return (double) ival; }
            if (type == T_FLOAT) { return fval; }
            if (type == T_TEXT) {
                try { return Double.parseDouble(sval.trim()); }
                catch (NumberFormatException e) { return 0.0; }
            }
            return 0.0;
        }

        // sqlite3_column_int64 semantics: floats truncate, text parses.
        long asLong() {
            if (type == T_INT) { return ival; }
            if (type == T_FLOAT) { return (long) fval; }
            if (type == T_TEXT) {
                try { return (long) Double.parseDouble(sval.trim()); }
                catch (NumberFormatException e) { return 0L; }
            }
            return 0L;
        }

        // sqlite3_column_text semantics.
        String asText() {
            if (type == T_INT) { return Long.toString(ival); }
            if (type == T_FLOAT) { return Double.toString(fval); }
            if (type == T_TEXT) { return sval; }
            return "";
        }

        // Whether the text looks like a number (for affinity conversions).
        boolean isNumericText() {
            if (type != T_TEXT) { return false; }
            String s = sval.trim();
            if (s.length() == 0) { return false; }
            try {
                Double.parseDouble(s);
                return true;
            } catch (NumberFormatException e) {
                return false;
            }
        }

        // Number that looks like an integer (affinity REAL->INTEGER etc.).
        boolean isIntegral() {
            if (type == T_INT) { return true; }
            if (type == T_FLOAT) { return fval == Math.rint(fval) && !Double.isInfinite(fval); }
            return false;
        }
    }

    static final class Column {
        final String name;
        final String declType;     // raw declared type text, e.g. "INTEGER PRIMARY KEY" trimmed
        final int affinity;        // 1 INTEGER, 2 TEXT, 3 REAL, 4 NONE, 5 NUMERIC
        final boolean integerPk;

        Column(String name, String declType, int affinity, boolean integerPk) {
            this.name = name;
            this.declType = declType;
            this.affinity = affinity;
            this.integerPk = integerPk;
        }
    }

    static final class Table {
        String name;
        List<Column> cols = new ArrayList<Column>();
        List<Value[]> rows = new ArrayList<Value[]>();
        List<Long> rowids = new ArrayList<Long>();
        int pkIndex = -1;          // INTEGER PRIMARY KEY column, or -1
        long nextRowid = 1;
    }

    static final class Dbh {
        String path;               // null = in-memory
        String errmsg = "not an error";
        long changes = 0;
        long lastRowid = 0;
        boolean inTx = false;
        LinkedHashMap<String, Table> txSnapshot = null;   // BEGIN's deep copy
        LinkedHashMap<String, Table> tables = new LinkedHashMap<String, Table>();
        boolean closed = false;
    }

    static final class Sth {
        Dbh db;
        StmtAst ast;
        Object[] binds;            // 0-based slot for 1-based params
        boolean executed = false;
        List<Value[]> resultRows = new ArrayList<Value[]>();
        List<String> resultCols = new ArrayList<String>();
        int cursor = 0;
    }

    // ------------------------------------------------------------------
    // AST
    // ------------------------------------------------------------------

    static final class StmtAst {
        int kind;                              // 0 select, 1 insert, 2 update, 3 delete, 4 create, 5 tx, 6 drop
        String table;
        List<Column> cols = new ArrayList<Column>();      // CREATE TABLE columns
        int pkIndex = -1;                                 // INTEGER PRIMARY KEY index
        List<String> selCols = new ArrayList<String>();   // result names (select)
        List<Expr> selExprs = new ArrayList<Expr>();      // nulls mark "all remaining" for *
        boolean star = false;
        Expr where = null;
        String orderBy = null;
        boolean orderDesc = false;
        List<String> insCols = new ArrayList<String>();   // empty = all columns
        List<List<Expr>> insRows = new ArrayList<List<Expr>>();
        List<String> setCols = new ArrayList<String>();
        List<Expr> setExprs = new ArrayList<Expr>();
        int txKind = 0;                        // 1 begin, 2 commit, 3 rollback
        int paramCount = 0;
        Object[] binds;                        // 0-based slot for 1-based params
    }

    static final class Expr {
        int kind;      // 0 num-int, 1 num-float, 2 text, 3 null, 4 param, 5 col, 6 binop, 7 unary, 8 count-star, 9 count-col
        long ival;
        double fval;
        String sval;
        String op;
        Expr left;
        Expr right;
        int paramIndex = 0;    // explicit ?N, else 0
        int pos0;              // source span (for expression naming)
        int pos1;
        Object[] bindsRef;     // the owning statement's bind slots
    }

    // ------------------------------------------------------------------
    // Tokenizer + parser
    // ------------------------------------------------------------------

    static final class Tok {
        static final int IDENT = 1;
        static final int NUM = 2;
        static final int STR = 3;
        static final int OP = 4;
        static final int END = 5;
        int kind;
        String text;
        boolean isInt;
        long ival;
        double fval;
        int pos;                   // char offset in the source (for naming)
    }

    static List<Tok> tokenize(String sql) {
        List<Tok> out = new ArrayList<Tok>();
        int i = 0;
        int n = sql.length();
        while (i < n) {
            char c = sql.charAt(i);
            if (c == ' ' || c == '\t' || c == '\r' || c == '\n') { i++; continue; }
            if (c == '-' && i + 1 < n && sql.charAt(i + 1) == '-') {
                while (i < n && sql.charAt(i) != '\n') { i++; }
                continue;
            }
            if (c == '\'') {
                StringBuilder sb = new StringBuilder();
                i++;
                while (i < n) {
                    if (sql.charAt(i) == '\'') {
                        if (i + 1 < n && sql.charAt(i + 1) == '\'') {
                            sb.append('\'');
                            i += 2;
                            continue;
                        }
                        break;
                    }
                    sb.append(sql.charAt(i));
                    i++;
                }
                if (i >= n) { return null; }   // unterminated string
                i++;
                Tok t = new Tok();
                t.kind = Tok.STR;
                t.text = sb.toString();
                t.pos = i;
                out.add(t);
                continue;
            }
            if (Character.isLetter(c) || c == '_') {
                int s = i;
                while (i < n && (Character.isLetterOrDigit(sql.charAt(i)) || sql.charAt(i) == '_')) { i++; }
                Tok t = new Tok();
                t.kind = Tok.IDENT;
                t.text = sql.substring(s, i);
                t.pos = s;
                out.add(t);
                continue;
            }
            if (Character.isDigit(c) || (c == '.' && i + 1 < n && Character.isDigit(sql.charAt(i + 1)))) {
                int s = i;
                boolean isInt = true;
                while (i < n && Character.isDigit(sql.charAt(i))) { i++; }
                if (i < n && sql.charAt(i) == '.') {
                    isInt = false;
                    i++;
                    while (i < n && Character.isDigit(sql.charAt(i))) { i++; }
                }
                if (i < n && (sql.charAt(i) == 'e' || sql.charAt(i) == 'E')) {
                    isInt = false;
                    i++;
                    if (i < n && (sql.charAt(i) == '+' || sql.charAt(i) == '-')) { i++; }
                    while (i < n && Character.isDigit(sql.charAt(i))) { i++; }
                }
                String num = sql.substring(s, i);
                Tok t = new Tok();
                t.kind = Tok.NUM;
                t.text = num;
                t.isInt = isInt;
                try {
                    if (isInt) { t.ival = Long.parseLong(num); }
                    else { t.fval = Double.parseDouble(num); }
                } catch (NumberFormatException e) {
                    return null;
                }
                t.pos = s;
                out.add(t);
                continue;
            }
            String op2 = i + 1 < n ? sql.substring(i, i + 2) : "";
            if (op2.equals("<=") || op2.equals(">=") || op2.equals("<>") || op2.equals("!=")) {
                Tok t = new Tok();
                t.kind = Tok.OP;
                t.text = op2;
                t.pos = i;
                out.add(t);
                i += 2;
                continue;
            }
            if ("=<>(),;*?+-/%".indexOf(c) >= 0) {
                Tok t = new Tok();
                t.kind = Tok.OP;
                t.text = String.valueOf(c);
                t.pos = i;
                out.add(t);
                i++;
                continue;
            }
            return null;   // unknown character
        }
        Tok end = new Tok();
        end.kind = Tok.END;
        end.pos = n;
        out.add(end);
        return out;
    }

    static boolean kw(Tok t, String word) {
        return t.kind == Tok.IDENT && t.text.equalsIgnoreCase(word);
    }

    // Parses one statement; returns null on syntax error.
    static StmtAst parse(String sql) {
        List<Tok> toks = tokenize(sql);
        if (toks == null) { return null; }
        P p = new P(toks, sql);
        if (p.at().kind == Tok.END) { return new StmtAst(); }   // empty statement
        StmtAst ast = parseStmt(p);
        if (ast == null) { return null; }
        // Allow a trailing semicolon, then the statement must end.
        if (p.at().kind == Tok.OP && p.at().text.equals(";")) { p.next(); }
        if (p.at().kind != Tok.END) { return null; }
        return ast;
    }

    static final class P {
        List<Tok> toks;
        String src;
        int i = 0;

        P(List<Tok> toks, String src) {
            this.toks = toks;
            this.src = src;
        }

        Tok at() { return toks.get(i); }

        Tok next() {
            Tok t = toks.get(i);
            if (t.kind != Tok.END) { i++; }
            return t;
        }

        boolean eat(String op) {
            if (at().kind == Tok.OP && at().text.equals(op)) {
                next();
                return true;
            }
            return false;
        }

        boolean eatKw(String word) {
            if (kw(at(), word)) {
                next();
                return true;
            }
            return false;
        }

        // Raw source slice from token a.pos up to (exclusive) token b's
        // position, trailing whitespace trimmed — used for expression names.
        String slice(int from, int to) {
            int end = Math.max(from, Math.min(to, src.length()));
            return src.substring(from, end).trim();
        }
    }

    static StmtAst parseStmt(P p) {
        Tok t = p.at();
        if (kw(t, "CREATE")) {
            p.next();
            if (!p.eatKw("TABLE")) { return null; }
            if (p.eatKw("IF")) {
                if (!p.eatKw("NOT")) { return null; }
                if (!p.eatKw("EXISTS")) { return null; }
            }
            Tok name = p.at();
            if (name.kind != Tok.IDENT) { return null; }
            p.next();
            if (!p.eat("(")) { return null; }
            StmtAst ast = new StmtAst();
            ast.kind = 4;
            ast.table = name.text;
            int pk = -1;
            List<Column> cols = new ArrayList<Column>();
            while (true) {
                Tok c = p.at();
                if (c.kind != Tok.IDENT) { return null; }
                p.next();
                StringBuilder decl = new StringBuilder();
                boolean isPk = false;
                boolean first = true;
                while (true) {
                    Tok w = p.at();
                    if (w.kind != Tok.IDENT) { break; }
                    if (kw(w, "PRIMARY")) {
                        p.next();
                        if (!p.eatKw("KEY")) { return null; }
                        isPk = true;
                        decl.append(first ? "" : " ").append("PRIMARY KEY");
                        first = false;
                        continue;
                    }
                    if (kw(w, "NOT") || kw(w, "NULL") || kw(w, "UNIQUE") || kw(w, "DEFAULT")
                            || kw(w, "AUTOINCREMENT") || kw(w, "REFERENCES")) {
                        // Skip constraint keywords (and DEFAULT's literal).
                        p.next();
                        if (kw(w, "DEFAULT") && (p.at().kind == Tok.NUM || p.at().kind == Tok.STR)) {
                            p.next();
                        }
                        if (kw(w, "DEFAULT") && p.at().kind == Tok.OP && p.at().text.equals("(")) {
                            int depth = 0;
                            while (true) {
                                Tok d = p.at();
                                if (d.kind == Tok.END) { return null; }
                                if (d.kind == Tok.OP && d.text.equals("(")) { depth++; }
                                if (d.kind == Tok.OP && d.text.equals(")")) {
                                    depth--;
                                    if (depth == 0) { p.next(); break; }
                                }
                                p.next();
                            }
                        }
                        if (kw(w, "REFERENCES")) {
                            if (p.at().kind != Tok.IDENT) { return null; }
                            p.next();
                            if (p.at().kind == Tok.OP && p.at().text.equals("(")) {
                                p.next();
                                if (p.at().kind != Tok.IDENT) { return null; }
                                p.next();
                                if (!p.eat(")")) { return null; }
                            }
                        }
                        decl.append(first ? "" : " ").append(w.text);
                        first = false;
                        continue;
                    }
                    // Type words.
                    decl.append(first ? "" : " ").append(w.text);
                    first = false;
                    p.next();
                }
                cols.add(new Column(c.text, decl.toString(), affinityOf(decl.toString()), isPk));
                if (isPk) {
                    String dt = decl.toString().toUpperCase();
                    if (dt.startsWith("INTEGER") || dt.startsWith("INT")) { pk = cols.size() - 1; }
                }
                if (p.eat(",")) { continue; }
                if (p.eat(")")) { break; }
                return null;
            }
            // COUNT-free CREATE may also carry table constraints — reject.
            if (pk >= 0 && cols.get(pk).integerPk) { ast.pkIndex = pk; }
            ast.cols = cols;
            return ast;
        }
        if (kw(t, "INSERT")) {
            p.next();
            if (!p.eatKw("INTO")) { return null; }
            Tok name = p.at();
            if (name.kind != Tok.IDENT) { return null; }
            p.next();
            StmtAst ast = new StmtAst();
            ast.kind = 1;
            ast.table = name.text;
            if (p.at().kind == Tok.OP && p.at().text.equals("(")) {
                p.next();
                while (true) {
                    Tok c = p.at();
                    if (c.kind != Tok.IDENT) { return null; }
                    p.next();
                    ast.insCols.add(c.text);
                    if (p.eat(",")) { continue; }
                    if (p.eat(")")) { break; }
                    return null;
                }
            }
            if (!p.eatKw("VALUES")) { return null; }
            while (true) {
                if (!p.eat("(")) { return null; }
                List<Expr> row = new ArrayList<Expr>();
                while (true) {
                    Expr e = parseExpr(p, ast);
                    if (e == null) { return null; }
                    row.add(e);
                    if (p.eat(",")) { continue; }
                    if (p.eat(")")) { break; }
                    return null;
                }
                ast.insRows.add(row);
                if (p.eat(",")) { continue; }
                break;
            }
            return ast;
        }
        if (kw(t, "SELECT")) {
            p.next();
            StmtAst ast = new StmtAst();
            ast.kind = 0;
            while (true) {
                if (p.at().kind == Tok.OP && p.at().text.equals("*")) {
                    int s = p.at().pos;
                    p.next();
                    ast.star = true;
                    ast.selExprs.add(null);
                    ast.selCols.add(p.slice(s, p.at().pos).equals("*") ? "*" : "*");
                    continue;
                }
                Expr e = parseExpr(p, ast);
                if (e == null) { return null; }
                ast.selExprs.add(e);
                if (e.kind == 8) {
                    ast.selCols.add("COUNT(*)");
                } else if (p.eatKw("AS")) {
                    Tok a = p.at();
                    if (a.kind != Tok.IDENT) { return null; }
                    p.next();
                    ast.selCols.add(a.text);
                } else if (e.kind == 5) {
                    ast.selCols.add(e.sval);
                } else {
                    ast.selCols.add(p.slice(e.pos0, e.pos1));
                }
                if (p.eat(",")) { continue; }
                break;
            }
            if (!p.eatKw("FROM")) { return null; }
            Tok name = p.at();
            if (name.kind != Tok.IDENT) { return null; }
            p.next();
            ast.table = name.text;
            if (p.eatKw("WHERE")) {
                ast.where = parseExpr(p, ast);
                if (ast.where == null) { return null; }
            }
            if (p.eatKw("ORDER")) {
                if (!p.eatKw("BY")) { return null; }
                Expr o = parseExpr(p, ast);
                if (o == null || o.kind != 5) { return null; }
                ast.orderBy = o.sval;
                if (p.eatKw("DESC")) { ast.orderDesc = true; }
                else { p.eatKw("ASC"); }
            }
            return ast;
        }
        if (kw(t, "UPDATE")) {
            p.next();
            Tok name = p.at();
            if (name.kind != Tok.IDENT) { return null; }
            p.next();
            StmtAst ast = new StmtAst();
            ast.kind = 2;
            ast.table = name.text;
            if (!p.eatKw("SET")) { return null; }
            while (true) {
                Tok c = p.at();
                if (c.kind != Tok.IDENT) { return null; }
                p.next();
                if (!p.eat("=")) { return null; }
                Expr e = parseExpr(p, ast);
                if (e == null) { return null; }
                ast.setCols.add(c.text);
                ast.setExprs.add(e);
                if (p.eat(",")) { continue; }
                break;
            }
            if (p.eatKw("WHERE")) {
                ast.where = parseExpr(p, ast);
                if (ast.where == null) { return null; }
            }
            return ast;
        }
        if (kw(t, "DELETE")) {
            p.next();
            if (!p.eatKw("FROM")) { return null; }
            Tok name = p.at();
            if (name.kind != Tok.IDENT) { return null; }
            p.next();
            StmtAst ast = new StmtAst();
            ast.kind = 3;
            ast.table = name.text;
            if (p.eatKw("WHERE")) {
                ast.where = parseExpr(p, ast);
                if (ast.where == null) { return null; }
            }
            return ast;
        }
        if (kw(t, "BEGIN")) {
            p.next();
            p.eatKw("TRANSACTION");
            StmtAst ast = new StmtAst();
            ast.kind = 5;
            ast.txKind = 1;
            return ast;
        }
        if (kw(t, "COMMIT") || kw(t, "END")) {
            p.next();
            p.eatKw("TRANSACTION");
            StmtAst ast = new StmtAst();
            ast.kind = 5;
            ast.txKind = 2;
            return ast;
        }
        if (kw(t, "ROLLBACK")) {
            p.next();
            p.eatKw("TRANSACTION");
            StmtAst ast = new StmtAst();
            ast.kind = 5;
            ast.txKind = 3;
            return ast;
        }
        if (kw(t, "DROP")) {
            p.next();
            if (!p.eatKw("TABLE")) { return null; }
            Tok name = p.at();
            if (name.kind != Tok.IDENT) { return null; }
            p.next();
            p.eatKw("IF");
            p.eatKw("EXISTS");
            StmtAst ast = new StmtAst();
            ast.kind = 6;
            ast.table = name.text;
            return ast;
        }
        return null;
    }

    static int affinityOf(String declType) {
        String d = declType.toUpperCase();
        if (d.contains("INT")) { return 1; }                 // INTEGER
        if (d.contains("CHAR") || d.contains("CLOB") || d.contains("TEXT")) { return 2; }
        if (d.contains("BLOB") || d.length() == 0) { return 4; }   // NONE
        if (d.contains("REAL") || d.contains("FLOA") || d.contains("DOUB")) { return 3; }
        return 5;                                            // NUMERIC
    }

    // Expression parsing. Counts ? placeholders into the statement AST.
    static Expr parseExpr(P p, StmtAst ast) {
        return parseOr(p, ast);
    }

    static Expr parseOr(P p, StmtAst ast) {
        Expr left = parseAnd(p, ast);
        if (left == null) { return null; }
        while (kw(p.at(), "OR")) {
            p.next();
            Expr right = parseAnd(p, ast);
            if (right == null) { return null; }
            left = binop("OR", left, right);
        }
        return left;
    }

    static Expr parseAnd(P p, StmtAst ast) {
        Expr left = parseNot(p, ast);
        if (left == null) { return null; }
        while (kw(p.at(), "AND")) {
            p.next();
            Expr right = parseNot(p, ast);
            if (right == null) { return null; }
            left = binop("AND", left, right);
        }
        return left;
    }

    static Expr parseNot(P p, StmtAst ast) {
        if (kw(p.at(), "NOT")) {
            int s = p.at().pos;
            p.next();
            Expr e = parseNot(p, ast);
            if (e == null) { return null; }
            Expr out = new Expr();
            out.kind = 7;
            out.op = "NOT";
            out.left = e;
            out.pos0 = s;
            out.pos1 = p.at().pos;
            return out;
        }
        return parseCmp(p, ast);
    }

    static Expr parseCmp(P p, StmtAst ast) {
        Expr left = parseAdd(p, ast);
        if (left == null) { return null; }
        Tok t = p.at();
        if (t.kind == Tok.OP && ("= <> != < <= > >=".contains(t.text)) && t.text.length() <= 2
                && (t.text.equals("=") || t.text.equals("<") || t.text.equals(">")
                    || t.text.equals("<=") || t.text.equals(">=") || t.text.equals("<>")
                    || t.text.equals("!="))) {
            p.next();
            Expr right = parseAdd(p, ast);
            if (right == null) { return null; }
            return binop(t.text, left, right);
        }
        // IS NULL / IS NOT NULL
        if (kw(t, "IS")) {
            p.next();
            boolean negate = p.eatKw("NOT");
            if (!p.eatKw("NULL")) { return null; }
            Expr out = new Expr();
            out.kind = 6;
            out.op = negate ? "ISNOTNULL" : "ISNULL";
            out.left = left;
            out.pos0 = left.pos0;
            out.pos1 = p.at().pos;
            return out;
        }
        return left;
    }

    static Expr parseAdd(P p, StmtAst ast) {
        Expr left = parseMul(p, ast);
        if (left == null) { return null; }
        while (p.at().kind == Tok.OP && (p.at().text.equals("+") || p.at().text.equals("-"))) {
            String op = p.at().text;
            p.next();
            Expr right = parseMul(p, ast);
            if (right == null) { return null; }
            left = binop(op, left, right);
        }
        return left;
    }

    static Expr parseMul(P p, StmtAst ast) {
        Expr left = parseUnary(p, ast);
        if (left == null) { return null; }
        while (p.at().kind == Tok.OP && (p.at().text.equals("*") || p.at().text.equals("/")
                || p.at().text.equals("%"))) {
            String op = p.at().text;
            p.next();
            Expr right = parseUnary(p, ast);
            if (right == null) { return null; }
            left = binop(op, left, right);
        }
        return left;
    }

    static Expr parseUnary(P p, StmtAst ast) {
        if (p.at().kind == Tok.OP && (p.at().text.equals("-") || p.at().text.equals("+"))) {
            String op = p.at().text;
            int s = p.at().pos;
            p.next();
            Expr e = parseUnary(p, ast);
            if (e == null) { return null; }
            if (op.equals("+")) { return e; }
            Expr out = new Expr();
            out.kind = 7;
            out.op = "NEG";
            out.left = e;
            out.pos0 = s;
            out.pos1 = p.at().pos;
            return out;
        }
        return parsePrimary(p, ast);
    }

    static Expr binop(String op, Expr left, Expr right) {
        Expr out = new Expr();
        out.kind = 6;
        out.op = op;
        out.left = left;
        out.right = right;
        out.pos0 = left.pos0;
        out.pos1 = right.pos1;
        return out;
    }

    static Expr parsePrimary(P p, StmtAst ast) {
        Tok t = p.at();
        Expr e = new Expr();
        e.pos0 = t.pos;
        if (t.kind == Tok.NUM) {
            p.next();
            e.pos1 = p.at().pos;
            if (t.isInt) {
                e.kind = 0;
                e.ival = t.ival;
            } else {
                e.kind = 1;
                e.fval = t.fval;
            }
            return e;
        }
        if (t.kind == Tok.STR) {
            p.next();
            e.pos1 = p.at().pos;
            e.kind = 2;
            e.sval = t.text;
            return e;
        }
        if (t.kind == Tok.OP && t.text.equals("(")) {
            p.next();
            Expr inner = parseOr(p, ast);
            if (inner == null) { return null; }
            if (!p.eat(")")) { return null; }
            e.pos1 = p.at().pos;
            return inner;
        }
        if (t.kind == Tok.OP && t.text.equals("?")) {
            p.next();
            e.kind = 4;
            e.pos1 = p.at().pos;
            if (p.at().kind == Tok.NUM && p.at().isInt) {
                e.paramIndex = (int) p.at().ival;
                p.next();
                e.pos1 = p.at().pos;
                if (e.paramIndex > ast.paramCount) { ast.paramCount = e.paramIndex; }
            } else {
                ast.paramCount = ast.paramCount + 1;
                e.paramIndex = ast.paramCount;
            }
            return e;
        }
        if (t.kind == Tok.IDENT) {
            if (kw(t, "NULL")) {
                p.next();
                e.pos1 = p.at().pos;
                e.kind = 3;
                return e;
            }
            if (kw(t, "COUNT") && p.at().kind == Tok.OP || kw(t, "COUNT")) {
                // COUNT ( * ) or COUNT ( expr )
                p.next();
                if (!p.eat("(")) { return null; }
                e.kind = 8;   // count-star unless a column follows
                if (p.at().kind == Tok.OP && p.at().text.equals("*")) {
                    p.next();
                    if (!p.eat(")")) { return null; }
                    e.pos1 = p.at().pos;
                    return e;
                }
                Tok c = p.at();
                if (c.kind != Tok.IDENT) { return null; }
                p.next();
                if (!p.eat(")")) { return null; }
                e.kind = 9;
                e.sval = c.text;
                e.pos1 = p.at().pos;
                return e;
            }
            // Column reference.
            p.next();
            if (p.at().kind == Tok.OP && p.at().text.equals("(")) {
                return null;   // unsupported function
            }
            e.pos1 = p.at().pos;
            e.kind = 5;
            e.sval = t.text;
            return e;
        }
        return null;
    }

    // ------------------------------------------------------------------
    // Public surface (names and shapes fixed by sql.lsh's @from block)
    // ------------------------------------------------------------------

    public static Object lsql_open(String path) {
        if (path == null) { return null; }
        Dbh d = new Dbh();
        if (path.equals(":memory:") || path.length() == 0) {
            d.path = null;
            d.errmsg = "not an error";
            return d;
        }
        d.path = path;
        java.io.File f = new java.io.File(path);
        if (f.exists()) {
            if (!f.isFile() || !load(d, f)) {
                d.errmsg = "file is not a database";
                return null;
            }
        } else {
            if (!save(d)) {
                return null;
            }
        }
        return d;
    }

    public static void lsql_close(Object dbh) {
        if (!(dbh instanceof Dbh)) { return; }
        Dbh d = (Dbh) dbh;
        if (!d.inTx) { save(d); }
        d.closed = true;
    }

    public static String lsql_errmsg(Object dbh) {
        if (!(dbh instanceof Dbh)) { return "database is closed"; }
        return ((Dbh) dbh).errmsg;
    }

    public static long lsql_changes(Object dbh) {
        if (!(dbh instanceof Dbh)) { return 0 - 1L; }
        return ((Dbh) dbh).changes;
    }

    public static long lsql_last_insert_rowid(Object dbh) {
        if (!(dbh instanceof Dbh)) { return 0 - 1L; }
        return ((Dbh) dbh).lastRowid;
    }

    // sqlite3_exec: multiple ';'-separated statements, stops at first error.
    public static int lsql_exec(Object dbh, String sql) {
        if (!(dbh instanceof Dbh) || sql == null) { return MISUSE; }
        Dbh d = (Dbh) dbh;
        if (d.closed) { d.errmsg = "attempt to write a readonly database"; return ERROR; }
        List<Tok> toks = tokenize(sql);
        if (toks == null) {
            d.errmsg = "unrecognized token";
            return ERROR;
        }
        P p = new P(toks, sql);
        boolean dirty = false;
        while (p.at().kind != Tok.END) {
            if (p.at().kind == Tok.OP && p.at().text.equals(";")) {
                p.next();
                continue;
            }
            StmtAst ast = parseStmt(p);
            if (ast == null) {
                d.errmsg = "near \"" + p.slice(p.at().pos, p.at().pos + 24) + "\": syntax error";
                return ERROR;
            }
            int rc = execute(d, ast);
            if (rc != OK) { return rc; }
            if (ast.kind != 5) { dirty = true; }
            if (p.at().kind == Tok.OP && p.at().text.equals(";")) { p.next(); }
        }
        if (dirty && !d.inTx) {
            if (!save(d)) { return ERROR; }
        }
        return OK;
    }

    public static Object lsql_prepare(Object dbh, String sql) {
        if (!(dbh instanceof Dbh) || sql == null) { return null; }
        Dbh d = (Dbh) dbh;
        if (d.closed) { d.errmsg = "attempt to write a readonly database"; return null; }
        List<Tok> toks = tokenize(sql);
        if (toks == null) {
            d.errmsg = "unrecognized token";
            return null;
        }
        P p = new P(toks, sql);
        StmtAst ast = parseStmt(p);
        if (ast == null) {
            d.errmsg = "near \"" + p.slice(p.at().pos, p.at().pos + 24) + "\": syntax error";
            return null;
        }
        if (p.at().kind == Tok.OP && p.at().text.equals(";")) { p.next(); }
        if (p.at().kind != Tok.END) {
            d.errmsg = "near \"" + p.slice(p.at().pos, p.at().pos + 24) + "\": syntax error";
            return null;
        }
        // Bind slots are shared by the AST's param nodes and the handle.
        ast.binds = new Object[ast.paramCount];
        linkBindsExprs(ast);
        Sth st = new Sth();
        st.db = d;
        st.ast = ast;
        st.binds = ast.binds;
        // Resolve the schema now (sqlite prepares against the current
        // schema; missing tables/columns fail here, not at step).
        if (!bindAstMetadata(d, ast, st)) { return null; }
        return st;
    }

    public static int lsql_bind_int(Object sth, int idx, long v) {
        return bind(sth, idx, Value.of(v));
    }

    public static int lsql_bind_float(Object sth, int idx, double v) {
        return bind(sth, idx, Value.of(v));
    }

    public static int lsql_bind_text(Object sth, int idx, String v) {
        return bind(sth, idx, v == null ? Value.NULL : Value.of(v));
    }

    public static int lsql_bind_null(Object sth, int idx) {
        return bind(sth, idx, Value.NULL);
    }

    private static int bind(Object sth, int idx, Value v) {
        if (!(sth instanceof Sth)) { return MISUSE; }
        Sth st = (Sth) sth;
        if (idx < 1 || idx > st.binds.length) { return RANGE; }
        st.binds[idx - 1] = v;
        return OK;
    }

    public static int lsql_step(Object sth) {
        if (!(sth instanceof Sth)) { return MISUSE; }
        Sth st = (Sth) sth;
        if (st.db.closed) { st.db.errmsg = "attempt to write a readonly database"; return ERROR; }
        if (!st.executed) {
            int rc = execute(st.db, st.ast, st);
            if (rc != OK) {
                st.executed = true;
                return rc;
            }
            if (st.ast.kind != 0) {
                st.executed = true;
                return DONE;   // DDL/DML/tx: no rows
            }
            st.executed = true;
            st.cursor = 0;
        }
        if (st.cursor < st.resultRows.size()) {
            st.cursor = st.cursor + 1;
            return ROW;
        }
        return DONE;
    }

    public static int lsql_reset(Object sth) {
        if (!(sth instanceof Sth)) { return MISUSE; }
        Sth st = (Sth) sth;
        st.executed = false;
        st.cursor = 0;
        st.resultRows.clear();
        return OK;
    }

    public static int lsql_finalize(Object sth) {
        return OK;
    }

    public static int lsql_column_count(Object sth) {
        if (!(sth instanceof Sth)) { return 0; }
        return ((Sth) sth).resultCols.size();
    }

    public static String lsql_column_name(Object sth, int i) {
        if (!(sth instanceof Sth)) { return ""; }
        Sth st = (Sth) sth;
        if (i < 0 || i >= st.resultCols.size()) { return ""; }
        return st.resultCols.get(i);
    }

    private static Value curCell(Sth st, int i) {
        if (i < 0 || i >= st.resultCols.size()) { return Value.NULL; }
        if (st.cursor < 1 || st.cursor > st.resultRows.size()) { return Value.NULL; }
        Value[] row = st.resultRows.get(st.cursor - 1);
        if (i >= row.length) { return Value.NULL; }
        return row[i];
    }

    public static int lsql_column_type(Object sth, int i) {
        if (!(sth instanceof Sth)) { return T_NULL; }
        return curCell((Sth) sth, i).type;
    }

    public static long lsql_column_int(Object sth, int i) {
        if (!(sth instanceof Sth)) { return 0L; }
        return curCell((Sth) sth, i).asLong();
    }

    public static double lsql_column_float(Object sth, int i) {
        if (!(sth instanceof Sth)) { return 0.0; }
        return curCell((Sth) sth, i).asDouble();
    }

    public static String lsql_column_text(Object sth, int i) {
        if (!(sth instanceof Sth)) { return ""; }
        return curCell((Sth) sth, i).asText();
    }

    // ------------------------------------------------------------------
    // Schema resolution and execution
    // ------------------------------------------------------------------

    static Table findTable(Dbh d, String name) {
        Table exact = d.tables.get(name);
        if (exact != null) { return exact; }
        for (Map.Entry<String, Table> e : d.tables.entrySet()) {
            if (e.getKey().equalsIgnoreCase(name)) { return e.getValue(); }
        }
        return null;
    }

    static int findCol(Table t, String name) {
        for (int i = 0; i < t.cols.size(); i++) {
            if (t.cols.get(i).name.equalsIgnoreCase(name)) { return i; }
        }
        return 0 - 1;
    }

    // Point every ?-placeholder node at the statement's bind slots.
    static void linkBindsExprs(StmtAst ast) {
        if (ast.where != null) { linkBinds(ast.where, ast.binds); }
        for (Expr e : ast.selExprs) {
            if (e != null) { linkBinds(e, ast.binds); }
        }
        for (Expr e : ast.setExprs) { linkBinds(e, ast.binds); }
        for (List<Expr> row : ast.insRows) {
            for (Expr e : row) { linkBinds(e, ast.binds); }
        }
    }

    static void linkBinds(Expr e, Object[] binds) {
        if (e == null) { return; }
        if (e.kind == 4) { e.bindsRef = binds; }
        if (e.left != null) { linkBinds(e.left, binds); }
        if (e.right != null) { linkBinds(e.right, binds); }
    }

    // Prepare-time schema binding: resolves tables, expands result
    // columns (lsql_column_count/name must answer before the first step),
    // validates INSERT/UPDATE targets and value counts.
    static boolean bindAstMetadata(Dbh d, StmtAst ast, Sth st) {
        if (ast.kind == 0) {
            Table t = findTable(d, ast.table);
            if (t == null) {
                d.errmsg = "no such table: " + ast.table;
                return false;
            }
            st.resultCols.clear();
            for (int i = 0; i < ast.selExprs.size(); i++) {
                Expr e = ast.selExprs.get(i);
                if (e == null) {                       // * → all columns
                    for (Column c : t.cols) { st.resultCols.add(c.name); }
                } else if (e.kind == 8) {
                    st.resultCols.add("COUNT(*)");
                } else {
                    st.resultCols.add(ast.selCols.get(i));
                }
            }
            if (ast.star && ast.selExprs.size() == 1) {
                // Plain SELECT *: names come straight from the schema.
                st.resultCols.clear();
                for (Column c : t.cols) { st.resultCols.add(c.name); }
            }
            return true;
        }
        if (ast.kind == 1 || ast.kind == 2 || ast.kind == 3) {
            Table t = findTable(d, ast.table);
            if (t == null) {
                d.errmsg = "no such table: " + ast.table;
                return false;
            }
            if (ast.kind == 1) {
                List<Integer> map = insertMap(d, t, ast);
                if (map == null) { return false; }
                for (List<Expr> row : ast.insRows) {
                    if (row.size() != map.size()) {
                        d.errmsg = "table " + t.name + " has " + map.size()
                                + " columns but " + row.size() + " values were supplied";
                        return false;
                    }
                }
            }
            if (ast.kind == 2) {
                for (String c : ast.setCols) {
                    if (findCol(t, c) < 0) {
                        d.errmsg = "no such column: " + c;
                        return false;
                    }
                }
            }
            return true;
        }
        return true;
    }

    // INSERT's target-column index list (null on error, errmsg set).
    static List<Integer> insertMap(Dbh d, Table t, StmtAst ast) {
        List<Integer> map = new ArrayList<Integer>();
        if (ast.insCols.isEmpty()) {
            for (int i = 0; i < t.cols.size(); i++) { map.add(i); }
            return map;
        }
        for (String c : ast.insCols) {
            int idx = findCol(t, c);
            if (idx < 0) {
                d.errmsg = "table " + t.name + " has no column named " + c;
                return null;
            }
            map.add(idx);
        }
        return map;
    }

    // Runs one parsed statement. For SELECT through a statement handle
    // the materialized rows land in st.resultRows.
    static int execute(Dbh d, StmtAst ast) {
        return execute(d, ast, null);
    }

    static int execute(Dbh d, StmtAst ast, Sth st) {
        if (ast.kind == 4) {   // CREATE TABLE
            if (findTable(d, ast.table) != null) {
                d.errmsg = "table " + ast.table + " already exists";
                return ERROR;
            }
            Table t = new Table();
            t.name = ast.table;
            t.cols.addAll(ast.cols);
            t.pkIndex = ast.pkIndex;
            d.tables.put(t.name, t);
            return OK;
        }
        if (ast.kind == 6) {   // DROP TABLE
            Table t = findTable(d, ast.table);
            if (t == null) {
                d.errmsg = "no such table: " + ast.table;
                return ERROR;
            }
            d.tables.remove(t.name);
            return OK;
        }
        if (ast.kind == 5) {   // BEGIN / COMMIT / ROLLBACK
            if (ast.txKind == 1) {
                if (d.inTx) {
                    d.errmsg = "cannot start a transaction within a transaction";
                    return ERROR;
                }
                d.txSnapshot = snapshot(d.tables);
                d.inTx = true;
                return OK;
            }
            if (ast.txKind == 2) {
                if (!d.inTx) {
                    d.errmsg = "cannot commit - no transaction is active";
                    return ERROR;
                }
                d.inTx = false;
                d.txSnapshot = null;
                if (d.path != null && !save(d)) { return ERROR; }
                return OK;
            }
            if (!d.inTx) {
                d.errmsg = "cannot rollback - no transaction is active";
                return ERROR;
            }
            d.tables = d.txSnapshot;
            d.txSnapshot = null;
            d.inTx = false;
            return OK;
        }

        Table t = findTable(d, ast.table);
        if (t == null) {
            d.errmsg = "no such table: " + ast.table;
            return ERROR;
        }

        if (ast.kind == 0) {   // SELECT
            List<Value[]> src = matchingRows(d, t, ast);
            if (src == null) { return ERROR; }
            boolean aggregate = false;
            for (Expr e : ast.selExprs) {
                if (e != null && (e.kind == 8 || e.kind == 9)) { aggregate = true; }
            }
            List<Value[]> out = new ArrayList<Value[]>();
            if (aggregate) {
                Value[] row = new Value[ast.selExprs.size()];
                for (int i = 0; i < ast.selExprs.size(); i++) {
                    Expr e = ast.selExprs.get(i);
                    if (e == null) {
                        row[i] = Value.NULL;
                        continue;
                    }
                    if (e.kind == 8) {
                        row[i] = Value.of((long) src.size());
                    } else if (e.kind == 9) {
                        long n = 0;
                        int ci = findCol(t, e.sval);
                        for (Value[] r : src) {
                            if (ci >= 0 && ci < r.length && !r[ci].isNull()) { n++; }
                        }
                        row[i] = Value.of(n);
                    } else {
                        row[i] = src.isEmpty() ? Value.NULL : evalExpr(d, t, src.get(0), e);
                    }
                }
                out.add(row);
            } else {
                if (ast.orderBy != null) {
                    int oci = findCol(t, ast.orderBy);
                    if (oci < 0) {
                        d.errmsg = "no such column: " + ast.orderBy;
                        return ERROR;
                    }
                    final int oci2 = oci;
                    final boolean desc = ast.orderDesc;
                    java.util.Collections.sort(src, (a, b) -> {
                        int c = orderCmp(a[oci2], b[oci2]);
                        return desc ? 0 - c : c;
                    });
                }
                for (Value[] r : src) {
                    int width = 0;
                    for (Expr e : ast.selExprs) {
                        width += (e == null) ? t.cols.size() : 1;
                    }
                    Value[] outRow = new Value[width];
                    int oi = 0;
                    for (Expr e : ast.selExprs) {
                        if (e == null) {
                            for (int c = 0; c < t.cols.size(); c++) {
                                outRow[oi] = r[c];
                                oi++;
                            }
                            continue;
                        }
                        outRow[oi] = evalExpr(d, t, r, e);
                        oi++;
                    }
                    out.add(outRow);
                }
            }
            if (st != null) {
                st.resultRows = out;
            }
            return OK;
        }

        if (ast.kind == 1) {   // INSERT
            List<Integer> map = insertMap(d, t, ast);
            if (map == null) { return ERROR; }
            for (List<Expr> tuple : ast.insRows) {
                if (tuple.size() != map.size()) {
                    d.errmsg = "table " + t.name + " has " + map.size()
                            + " columns but " + tuple.size() + " values were supplied";
                    return ERROR;
                }
            }
            long lastId = d.lastRowid;
            for (List<Expr> tuple : ast.insRows) {
                Value[] cells = new Value[t.cols.size()];
                for (int i = 0; i < cells.length; i++) { cells[i] = Value.NULL; }
                for (int i = 0; i < map.size(); i++) {
                    int ci = map.get(i);
                    cells[ci] = applyStore(t.cols.get(ci).affinity, evalExpr(d, t, null, tuple.get(i)));
                }
                long rowid = t.nextRowid;
                if (t.pkIndex >= 0) {
                    Value pkv = cells[t.pkIndex];
                    if (!pkv.isNull() && pkv.type == T_INT) {
                        rowid = pkv.ival;
                    } else {
                        rowid = t.nextRowid;
                        cells[t.pkIndex] = Value.of(rowid);
                    }
                    if (rowid >= t.nextRowid) { t.nextRowid = rowid + 1; }
                } else {
                    t.nextRowid = rowid + 1;
                }
                t.rows.add(cells);
                t.rowids.add(rowid);
                lastId = rowid;
            }
            d.changes = ast.insRows.size();
            d.lastRowid = lastId;
            if (d.path != null && !d.inTx && !save(d)) { return ERROR; }
            return OK;
        }

        if (ast.kind == 2) {   // UPDATE
            int[] targets = new int[ast.setCols.size()];
            for (int i = 0; i < ast.setCols.size(); i++) {
                targets[i] = findCol(t, ast.setCols.get(i));
            }
            long n = 0;
            for (Value[] r : t.rows) {
                if (!matches(d, t, r, ast.where)) { continue; }
                for (int i = 0; i < targets.length; i++) {
                    r[targets[i]] = applyStore(t.cols.get(targets[i]).affinity,
                            evalExpr(d, t, r, ast.setExprs.get(i)));
                }
                n++;
            }
            d.changes = n;
            if (d.path != null && !d.inTx && !save(d)) { return ERROR; }
            return OK;
        }

        if (ast.kind == 3) {   // DELETE
            List<Value[]> keep = new ArrayList<Value[]>();
            List<Long> keepIds = new ArrayList<Long>();
            long n = 0;
            for (int i = 0; i < t.rows.size(); i++) {
                Value[] r = t.rows.get(i);
                if (matches(d, t, r, ast.where)) {
                    n++;
                    continue;
                }
                keep.add(r);
                keepIds.add(t.rowids.get(i));
            }
            t.rows = keep;
            t.rowids = keepIds;
            d.changes = n;
            if (d.path != null && !d.inTx && !save(d)) { return ERROR; }
            return OK;
        }
        return ERROR;
    }

    // Rows passing WHERE (null = all), materialized as cell arrays.
    static List<Value[]> matchingRows(Dbh d, Table t, StmtAst ast) {
        List<Value[]> out = new ArrayList<Value[]>();
        for (Value[] r : t.rows) {
            if (matches(d, t, r, ast.where)) { out.add(r); }
        }
        return out;
    }

    static boolean matches(Dbh d, Table t, Value[] row, Expr where) {
        if (where == null) { return true; }
        Value v = evalExpr(d, t, row, where);
        return truthy(v);
    }

    static boolean truthy(Value v) {
        if (v.isNull()) { return false; }
        if (v.type == T_INT) { return v.ival != 0; }
        if (v.type == T_FLOAT) { return v.fval != 0.0; }
        return v.isNumericText() && v.asDouble() != 0.0;
    }


    // ------------------------------------------------------------------
    // Expression evaluation
    // ------------------------------------------------------------------

    static Value evalExpr(Dbh d, Table t, Value[] row, Expr e) {
        switch (e.kind) {
            case 0: return Value.of(e.ival);
            case 1: return Value.of(e.fval);
            case 2: return Value.of(e.sval);
            case 3: return Value.NULL;
            case 4: {
                int idx = e.paramIndex;
                if (idx < 1 || e.bindsRef == null || idx > e.bindsRef.length) { return Value.NULL; }
                Object b = e.bindsRef[idx - 1];
                return b instanceof Value ? (Value) b : Value.NULL;
            }
            case 5: {
                if (row == null || t == null) { return Value.NULL; }
                int ci = findCol(t, e.sval);
                if (ci < 0 || ci >= row.length) {
                    // Unknown column in an expression: sqlite fails at
                    // prepare; reaching here means the reference was
                    // introduced dynamically — treat as NULL.
                    return Value.NULL;
                }
                return row[ci];
            }
            case 6: return evalBinop(d, t, row, e);
            case 7: {
                if (e.op.equals("NOT")) {
                    Value v = evalExpr(d, t, row, e.left);
                    if (v.isNull()) { return Value.NULL; }
                    return truthy(v) ? Value.of(0L) : Value.of(1L);
                }
                Value v = evalExpr(d, t, row, e.left);
                if (v.isNull()) { return Value.NULL; }
                if (v.type == T_INT) { return Value.of(0 - v.ival); }
                return Value.of(0 - v.asDouble());
            }
            case 8: return Value.NULL;
            case 9: return Value.NULL;
            default: return Value.NULL;
        }
    }

    static Value evalBinop(Dbh d, Table t, Value[] row, Expr e) {
        String op = e.op;
        if (op.equals("AND")) {
            if (!truthy(evalExpr(d, t, row, e.left))) { return Value.of(0L); }
            return truthy(evalExpr(d, t, row, e.right)) ? Value.of(1L) : Value.of(0L);
        }
        if (op.equals("OR")) {
            if (truthy(evalExpr(d, t, row, e.left))) { return Value.of(1L); }
            return truthy(evalExpr(d, t, row, e.right)) ? Value.of(1L) : Value.of(0L);
        }
        if (op.equals("ISNULL")) {
            return evalExpr(d, t, row, e.left).isNull() ? Value.of(1L) : Value.of(0L);
        }
        if (op.equals("ISNOTNULL")) {
            return evalExpr(d, t, row, e.left).isNull() ? Value.of(0L) : Value.of(1L);
        }
        Value a = evalExpr(d, t, row, e.left);
        Value b = evalExpr(d, t, row, e.right);
        if (op.equals("=") || op.equals("!=") || op.equals("<>") || op.equals("<")
                || op.equals("<=") || op.equals(">") || op.equals(">=")) {
            int c = compareAffine(d, t, a, b, e);
            if (c == Integer.MIN_VALUE) { return Value.NULL; }
            boolean res;
            if (op.equals("=")) { res = c == 0; }
            else if (op.equals("!=") || op.equals("<>")) { res = c != 0; }
            else if (op.equals("<")) { res = c < 0; }
            else if (op.equals("<=")) { res = c <= 0; }
            else if (op.equals(">")) { res = c > 0; }
            else { res = c >= 0; }
            return Value.of(res ? 1L : 0L);
        }
        // Arithmetic: NULL propagates; text coerces numerically; / and %
        // by zero yield NULL (sqlite parity).
        if (a.isNull() || b.isNull()) { return Value.NULL; }
        if (op.equals("+") || op.equals("-") || op.equals("*") || op.equals("/") || op.equals("%")) {
            if (op.equals("/") && b.asDouble() == 0.0) { return Value.NULL; }
            if (op.equals("%") && b.asDouble() == 0.0) { return Value.NULL; }
            boolean ints = a.type == T_INT && b.type == T_INT;
            if (ints) {
                if (op.equals("+")) { return Value.of(a.ival + b.ival); }
                if (op.equals("-")) { return Value.of(a.ival - b.ival); }
                if (op.equals("*")) { return Value.of(a.ival * b.ival); }
                if (op.equals("/")) { return Value.of(a.ival / b.ival); }
                return Value.of(a.ival % b.ival);
            }
            double x = a.asDouble();
            double y = b.asDouble();
            if (op.equals("+")) { return Value.of(x + y); }
            if (op.equals("-")) { return Value.of(x - y); }
            if (op.equals("*")) { return Value.of(x * y); }
            if (op.equals("/")) { return Value.of(x / y); }
            return Value.of((double) (a.asLong() % b.asLong()));
        }
        return Value.NULL;
    }

    // sqlite type rank for cross-type ordering: NULL < numbers < text.
    static int typeRank(Value v) {
        if (v.isNull()) { return 0; }
        if (v.type == T_INT || v.type == T_FLOAT) { return 1; }
        return 2;
    }

    // Three-way compare with the affinity of the column involved (when
    // one side is a direct column reference). Returns MIN_VALUE when a
    // NULL is involved (undefined comparison).
    static int compareAffine(Dbh d, Table t, Value a, Value b, Expr cmpExpr) {
        int aff = 0;
        if (t != null) {
            if (cmpExpr.left.kind == 5) {
                int ci = findCol(t, cmpExpr.left.sval);
                if (ci >= 0) { aff = t.cols.get(ci).affinity; }
            } else if (cmpExpr.right != null && cmpExpr.right.kind == 5) {
                int ci = findCol(t, cmpExpr.right.sval);
                if (ci >= 0) { aff = t.cols.get(ci).affinity; }
            }
        }
        a = coerceForCompare(aff, a, b);
        b = coerceForCompare(aff, b, a);
        return compareValues(a, b);
    }

    static Value coerceForCompare(int affinity, Value v, Value other) {
        if (v.isNull() || other.isNull()) { return v; }
        if ((affinity == 1 || affinity == 3 || affinity == 5) && v.type == T_TEXT && other.type != T_TEXT) {
            if (v.isNumericText()) { return Value.of(v.asDouble()); }
            return v;
        }
        if (affinity == 2 && v.type != T_TEXT && other.type == T_TEXT) {
            return Value.of(v.asText());
        }
        return v;
    }

    static int compareValues(Value a, Value b) {
        if (a.isNull() || b.isNull()) { return Integer.MIN_VALUE; }
        int ra = typeRank(a);
        int rb = typeRank(b);
        if (ra != rb) { return ra < rb ? 0 - 1 : 1; }
        if (ra == 1) {
            double x = a.asDouble();
            double y = b.asDouble();
            if (x < y) { return 0 - 1; }
            if (x > y) { return 1; }
            return 0;
        }
        int c = a.asText().compareTo(b.asText());
        if (c < 0) { return 0 - 1; }
        if (c > 0) { return 1; }
        return 0;
    }

    static int orderCmp(Value a, Value b) {
        int c = compareValues(a, b);
        if (c == Integer.MIN_VALUE) {
            // NULLs sort first ascending (sqlite parity).
            if (a.isNull() && b.isNull()) { return 0; }
            return a.isNull() ? 0 - 1 : 1;
        }
        return c;
    }

    // SQLite type affinity applied when storing into a typed column.
    static Value applyStore(int affinity, Value v) {
        if (v.isNull()) { return v; }
        if (affinity == 1) {   // INTEGER
            if (v.type == T_FLOAT && v.isIntegral()) { return Value.of((long) v.fval); }
            if (v.type == T_TEXT && v.isNumericText()) {
                double x = v.asDouble();
                if (x == Math.rint(x) && !Double.isInfinite(x)) { return Value.of((long) x); }
                return Value.of(x);
            }
            return v;
        }
        if (affinity == 3) {   // REAL
            if (v.type == T_INT) { return Value.of((double) v.ival); }
            if (v.type == T_TEXT && v.isNumericText()) { return Value.of(v.asDouble()); }
            return v;
        }
        if (affinity == 2) {   // TEXT
            if (v.type == T_INT || v.type == T_FLOAT) { return Value.of(v.asText()); }
            return v;
        }
        if (affinity == 5) {   // NUMERIC
            if (v.type == T_TEXT && v.isNumericText()) {
                double x = v.asDouble();
                if (x == Math.rint(x) && !Double.isInfinite(x) && Math.abs(x) < 9.0e15) {
                    return Value.of((long) x);
                }
                return Value.of(x);
            }
            if (v.type == T_FLOAT && v.isIntegral()) { return Value.of((long) v.fval); }
            return v;
        }
        return v;
    }

    // ------------------------------------------------------------------
    // Persistence (own "LEASHSQL1" text format — not sqlite3 files)
    // ------------------------------------------------------------------

    static LinkedHashMap<String, Table> snapshot(Map<String, Table> tables) {
        LinkedHashMap<String, Table> out = new LinkedHashMap<String, Table>();
        for (Map.Entry<String, Table> e : tables.entrySet()) {
            Table t = e.getValue();
            Table c = new Table();
            c.name = t.name;
            c.pkIndex = t.pkIndex;
            c.nextRowid = t.nextRowid;
            c.cols.addAll(t.cols);
            for (Value[] r : t.rows) {
                Value[] copy = new Value[r.length];
                System.arraycopy(r, 0, copy, 0, r.length);
                c.rows.add(copy);
            }
            c.rowids.addAll(t.rowids);
            out.put(c.name, c);
        }
        return out;
    }

    static String escapeText(String s) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\') { sb.append("\\\\"); }
            else if (c == '\n') { sb.append("\\n"); }
            else if (c == '\r') { sb.append("\\r"); }
            else if (c == '\t') { sb.append("\\t"); }
            else { sb.append(c); }
        }
        return sb.toString();
    }

    static String unescapeText(String s) {
        StringBuilder sb = new StringBuilder();
        int i = 0;
        while (i < s.length()) {
            char c = s.charAt(i);
            if (c == '\\' && i + 1 < s.length()) {
                char n = s.charAt(i + 1);
                if (n == 'n') { sb.append('\n'); i += 2; continue; }
                if (n == 'r') { sb.append('\r'); i += 2; continue; }
                if (n == 't') { sb.append('\t'); i += 2; continue; }
                if (n == '\\') { sb.append('\\'); i += 2; continue; }
            }
            sb.append(c);
            i++;
        }
        return sb.toString();
    }

    static String encodeCell(Value v) {
        if (v.isNull()) { return "n"; }
        if (v.type == T_INT) { return "i:" + Long.toString(v.ival); }
        if (v.type == T_FLOAT) { return "f:" + Double.toString(v.fval); }
        return "t:" + escapeText(v.sval);
    }

    static Value decodeCell(String s) {
        if (s.equals("n")) { return Value.NULL; }
        if (s.startsWith("i:")) {
            try { return Value.of(Long.parseLong(s.substring(2))); }
            catch (NumberFormatException e) { return Value.NULL; }
        }
        if (s.startsWith("f:")) {
            try { return Value.of(Double.parseDouble(s.substring(2))); }
            catch (NumberFormatException e) { return Value.NULL; }
        }
        if (s.startsWith("t:")) { return Value.of(unescapeText(s.substring(2))); }
        return Value.NULL;
    }

    static boolean save(Dbh d) {
        if (d.path == null) { return true; }
        try {
            FileOutputStream fo = new FileOutputStream(d.path);
            BufferedWriter w = new BufferedWriter(new OutputStreamWriter(fo, StandardCharsets.UTF_8));
            w.write("LEASHSQL1");
            w.write("\n");
            for (Table t : d.tables.values()) {
                w.write("T " + t.name + " " + t.cols.size() + " " + t.nextRowid + " " + t.pkIndex + "\n");
                for (Column c : t.cols) {
                    w.write("C " + c.name + " " + c.declType.replace("\n", " ") + "\n");
                }
                for (int i = 0; i < t.rows.size(); i++) {
                    StringBuilder sb = new StringBuilder();
                    sb.append("R ").append(t.rowids.get(i));
                    for (Value v : t.rows.get(i)) {
                        sb.append('\t').append(encodeCell(v));
                    }
                    w.write(sb.toString());
                    w.write("\n");
                }
            }
            w.close();
            return true;
        } catch (Exception e) {
            d.errmsg = "unable to open database file";
            return false;
        }
    }

    static boolean load(Dbh d, java.io.File f) {
        try {
            FileInputStream fi = new FileInputStream(f);
            BufferedReader r = new BufferedReader(new InputStreamReader(fi, StandardCharsets.UTF_8));
            String magic = r.readLine();
            if (!"LEASHSQL1".equals(magic)) { r.close(); return false; }
            Table cur = null;
            String line;
            while ((line = r.readLine()) != null) {
                if (line.startsWith("T ")) {
                    String[] parts = line.split(" ");
                    if (parts.length != 5) { r.close(); return false; }
                    cur = new Table();
                    cur.name = parts[1];
                    try {
                        int ncols = Integer.parseInt(parts[2]);
                        cur.nextRowid = Long.parseLong(parts[3]);
                        cur.pkIndex = Integer.parseInt(parts[4]);
                        if (ncols < 0) { r.close(); return false; }
                    } catch (NumberFormatException e) { r.close(); return false; }
                    d.tables.put(cur.name, cur);
                } else if (line.startsWith("C ")) {
                    if (cur == null) { r.close(); return false; }
                    int sp = line.indexOf(' ', 2);
                    if (sp < 0) { r.close(); return false; }
                    String name = line.substring(2, sp);
                    String decl = line.substring(sp + 1);
                    cur.cols.add(new Column(name, decl, affinityOf(decl),
                            decl.toUpperCase().contains("PRIMARY KEY")));
                } else if (line.startsWith("R ")) {
                    if (cur == null) { r.close(); return false; }
                    String[] parts = line.substring(2).split("\t", 0 - 1);
                    try {
                        cur.rowids.add(Long.parseLong(parts[0]));
                    } catch (NumberFormatException e) { r.close(); return false; }
                    Value[] cells = new Value[cur.cols.size()];
                    for (int i = 0; i < cells.length; i++) {
                        cells[i] = (i + 1 < parts.length) ? decodeCell(parts[i + 1]) : Value.NULL;
                    }
                    cur.rows.add(cells);
                } else if (line.length() > 0) {
                    r.close();
                    return false;
                }
            }
            r.close();
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
