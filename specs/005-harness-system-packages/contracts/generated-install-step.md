# Contract: Generated Install Step

This is the interface between HarnessBuddy's package-resolution logic and
the two consumers of its output: the OSS-Fuzz build infrastructure (executes
the generated `Dockerfile`) and a developer's shell (executes the generated
`setup.sh`). This feature does not change this contract — it changes what
package lists are supplied to it (see data-model.md). Documented here so the
implementation can be verified against it and so a future change to
generation logic doesn't silently break it.

## Inputs

- `analysis.system_packages: list[str]` — apt package names, already
  deduplicated, in first-seen order across all pipeline stages (library
  build, harness link, agent repairs).
- `brew_packages: list[str]` — brew package names, same ordering guarantee,
  passed separately to `generate_local` (macOS only; not used by the
  Dockerfile, which always targets the Debian/Ubuntu OSS-Fuzz base image).

Both lists MUST already be deduplicated and MUST NOT contain base-system
libraries (`m`, `pthread`, `dl`, `rt`, `resolv`, `c`, `gcc_s`, `stdc++`) —
that filtering happens once, in `package_names.translate()`, before either
list reaches generation.

## Output: `oss-fuzz/Dockerfile`

When `analysis.system_packages` is non-empty, the Dockerfile MUST contain
exactly one line of the form:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends <pkg1> <pkg2> ...
```

placed after `FROM gcr.io/oss-fuzz-base/base-builder` and before the
`RUN git clone` line. Autotools bootstrap packages (`autoconf`, `automake`,
`libtool`, when the source needs `autogen`/`autoreconf`) are prepended to
the same line ahead of `analysis.system_packages`, in that order.

When `analysis.system_packages` is empty, no apt install line is emitted.

## Output: `local/setup.sh`

Exactly one dependency-install line is emitted, chosen by platform and
package availability (existing `_write_setup_sh` precedence, unchanged by
this feature):

1. If running on macOS (`sys.platform == "darwin"`) **and** `brew_packages`
   is non-empty:
   ```bash
   brew install <pkg1> <pkg2> ...
   ```
2. Else if `analysis.system_packages` is non-empty:
   ```bash
   apt-get install -y --no-install-recommends <pkg1> <pkg2> ...
   ```
3. Else:
   ```bash
   # TODO: install build dependencies for this library
   ```

Placed after the `git clone`/checkout lines, before the rest of the script.

## Guarantee this feature adds

Prior to this feature, `analysis.system_packages` / `brew_packages` could be
empty even when the generated `compile_harnesses.sh` /
`oss-fuzz/compile_harnesses.sh` embedded `-lxxx` flags for libraries not
present in the target build environment (Case: harness link succeeded on the
exploration machine only because it already had the library). After this
feature, every `-lxxx` flag in `transitive_link_flags` that has a known
package mapping is reflected in `analysis.system_packages` /
`brew_packages` before generation runs, so the guarantee above ("no apt/brew
line" implies "no known package is needed") holds in both the
missing-locally and present-locally cases.
