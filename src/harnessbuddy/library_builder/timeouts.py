"""The wall-clock ceilings the library builder enforces, in one place.

The two are far apart because they bound different things:

* `DEFAULT_BUILD_TIMEOUT_SECONDS` bounds a full library build (configure, compile, install) in
  either environment. Real libraries take minutes.
* `HARNESS_PROBE_TIMEOUT_SECONDS` bounds one harness compile-and-link attempt — one stub
  translation unit plus a link, so an overrun means a hung linker, not a slow build.
"""

from __future__ import annotations

DEFAULT_BUILD_TIMEOUT_SECONDS = 900
HARNESS_PROBE_TIMEOUT_SECONDS = 60
