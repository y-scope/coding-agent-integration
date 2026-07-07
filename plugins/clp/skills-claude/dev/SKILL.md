---
name: dev
description: Developer workflow for building, testing, linting, and formatting a local CLP/clpp source tree, and for pointing the plugin wrappers at a locally-built clp-s binary.
allowed-tools:
  - "Bash"
---

# Dev

Developer workflow for two related tasks:

1. **Build/test/lint/format a local CLP source tree** (e.g. the `clp` / `clp_s` /
   `clpp` C++ code, plus Rust components).
2. **Point the plugin wrappers at a locally-built `clp-s` binary** so compress/
   search/experimental run against the local build instead of the installed
   plugin binary — primarily for testing in-flight clpp-branch work.

This skill is for CLP contributors. For end-user compress/search, use the
`compress`, `search`, and `experimental` skills instead.

## Point the wrappers at a local binary

`resolve_clp_s` (in the plugin's `bin/lib/clp-common.sh`) honors the **`CLP_S_BIN`
environment variable**: if set to an executable, every wrapper uses it. This is
the primary mechanism and needs no wrapper changes.

```bash
# From a CLP source checkout after building:
export CLP_S_BIN="$PWD/build/core/clp-s"

"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" --session-file … --parsing-specification spec.txt
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental /tmp/archive '*'
```

For a one-off invocation, use the **`--clp-s-bin PATH`** flag that the
`clp-s-compress-session` and `clp-s-search-kql` wrappers accept. The flag wins
for that invocation only and composes with `CLP_S_BIN`:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --experimental --clp-s-bin "$PWD/build/core/clp-s" \
  /tmp/archive 'shape(message): "*"'
```

Tip: wrappers can also be run directly from a plugin source checkout
(`~/yscope/coding-agent-integration`), which is how the plugin's own
`LOCAL_TESTING.md` smoke-tests them:

```bash
CLP_S_BIN="$PWD/build/core/clp-s" \
  ~/yscope/coding-agent-integration/plugins/clp/bin/clp-s-search-kql …
```

## Build (local CLP source)

Run from the CLP source root (e.g. `~/yscope/clp-dev`):

```bash
# Full core build (generates and compiles in one command)
CC=/usr/bin/clang CXX=/usr/bin/clang++ CMAKE_BUILD_PARALLEL_LEVEL=16 task core -C 4

# Incremental: rebuild only clp-s when no dependencies changed
cmake --build ./build/core --parallel 24 --target clp-s

# Rust components
task rust

# Full package
task package

# Clean build artifacts
task clean
```

## Test

```bash
# C++ unit tests (after building core)
./build/core/unitTest
./build/core/unitTest "test name pattern"

# Rust tests
cargo test --all
```

**clpp-branch constraint:** on the `clpp` branch the C++ unit tests do **not**
link. Verify changes with the binary targets (`clp-s`) plus the regression suite
instead:

```bash
# 34-case regression suite; default archive ./benchmark/archives/hive-24hr-best.cached
./benchmark/regression-test.sh
```

## Lint and format

```bash
# Everything (C++, JS, Python, Rust, YAML)
task lint:check
task lint:fix

# C++ only (format + static analysis)
task lint:check-cpp-full
task lint:fix-cpp-full

# Only changed C++ files vs origin/HEAD
task lint:check-cpp-diff
task lint:fix-cpp-diff

# Only changed C++ files vs HEAD (unstaged work)
GIT_REF=HEAD task lint:check-cpp-diff
GIT_REF=HEAD task lint:fix-cpp-diff

# Changed C++ files, clang-tidy static analysis only (no format)
GIT_REF=HEAD task lint:check-cpp-static-diff

# Python / Rust
task lint:check-py
task lint:fix-py
task lint:check-rust
task lint:fix-rust
```

## Plugin preflight

When changing the plugin wrappers or skills (in the
`~/yscope/coding-agent-integration` checkout), validate before installing:

```bash
cd ~/yscope/coding-agent-integration
claude plugin validate .
claude plugin validate ./plugins/clp

# Wrapper syntax
for f in plugins/clp/bin/clp-s-*; do bash -n "$f"; done

# Static analysis (if shellcheck is installed)
shellcheck \
  plugins/clp/bin/clp-s-list-sessions \
  plugins/clp/bin/clp-s-compress-session \
  plugins/clp/bin/clp-s-search-kql \
  plugins/clp/bin/clp-s-decompress \
  plugins/clp/bin/lib/clp-common.sh
```

To activate edited plugin source in the live session, sync it into the plugin
cache (preserve the installer-generated `bin/clp-s` shim):

```bash
rsync -a --exclude='.clp-core' --exclude='.yscope-clp-install.json' \
  --exclude='.codex-plugin' \
  ~/yscope/coding-agent-integration/plugins/clp/ \
  ~/.claude/plugins/cache/yscope/clp/0.1.11/
```