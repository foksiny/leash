#!/bin/sh
# Rebuild the JVM stdlib shims (leash/jvm/*.class) that implement the
# http, httpserver and sql modules' @from surfaces for --target jvm.
#
# Bytecode target is Java 11: java.net.http (the JDK HTTP client the
# client shim rides on) first shipped in JDK 11, so http/httpserver on
# the JVM require Java 11+. Everything else about the JVM target keeps
# its Java 5 class-file floor.
#
# The compiled .class files are committed next to this script (the same
# policy as the prebuilt liblsh*.a archives for the native targets):
# end users need only a JRE, and `leashc` copies these classes next to
# any program that actually calls into a shim.
set -e
cd "$(dirname "$0")"
javac --release 11 LeashHttp.java LeashHttpServer.java LshHttpHandler.java LeashSql.java
echo "ok: JVM shims rebuilt"
