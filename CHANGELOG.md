# Changelog

All notable changes to the Leash compiler are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

## [0.23.8] - 2026-09-08

### Fixed
- Integer constant folding for `/` and `%` now respects operand width, matching
  the guard already applied to `+`, `-` and `*`. Previously `-2147483648 / -1`
  was folded to the 64-bit constant `2147483648` instead of remaining a 32-bit
  runtime `sdiv`, so the folded result no longer diverges from runtime
  semantics on 32-bit operands.
- `leash update` now runs its `git pull` in the Leash *installation* directory
  instead of the caller's current working directory, preventing an unintended
  fetch/merge into whatever unrelated git repository the user happens to be in.

### Security
- `build_project` now rejects a malformed `out_name` from `config.lshc` that
  would escape the project's `out/` directory (e.g. `..` segments, absolute
  paths, or embedded separators), so a tampered config cannot write the object
  file and binary to arbitrary locations.

### Changed
- Bump version to `0.23.8 Beta`.

## [0.23.7] - 2026-09-08

### Fixed
- Constant-folding miscompiles: div/mod sign semantics and i32 overflow.
- Fixed the `%0` compiler crash.

### Changed
- Bump version to `0.23.7 Beta`.

## [0.23.6] - 2026-09-08

### Added
- Full vector math (add, sub, mul, div) with scalar broadcasting.

### Changed
- Bump version to `0.23.6 Beta`.

## [0.23.5] - 2026-09-08

### Added
- Dynamic union `show`/`showb`.

### Fixed
- Robust `.cur` / value-store handling.
- Unsigned integer display.

### Changed
- Bump version to `0.23.5 Beta`.

[0.23.8]: https://github.com/foksiny/leash/releases/tag/v0.23.8
[0.23.7]: https://github.com/foksiny/leash/releases/tag/v0.23.7
[0.23.6]: https://github.com/foksiny/leash/releases/tag/v0.23.6
[0.23.5]: https://github.com/foksiny/leash/releases/tag/v0.23.5