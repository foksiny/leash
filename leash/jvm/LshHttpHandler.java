// The httpserver module's request-handler contract on the JVM target.
//
// The compiler lowers `&handler_fn` (a Leash function value with the
// handler signature) into a generated adapter class implementing this
// interface; the shim calls back through it once per request, mirroring
// the native lshhttpd_handler_t contract:
//   method  "GET", "POST", ...
//   path    URL path without the query string
//   query   raw query string without the leading '?'
//   headers request headers, one "Name: value" per LF-separated line
//   body    request body ("" when there is none)
// returns the HTTP status code to reply with (clamped to 200..599).
public interface LshHttpHandler {
    int handle(String method, String path, String query, String headers, String body);
}
