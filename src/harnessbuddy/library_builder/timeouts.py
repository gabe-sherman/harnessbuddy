"""The wall-clock ceilings the library builder enforces, in one place.

The two ceilings are deliberately far apart, so a reader can tell they were each chosen
rather than one being a forgotten copy of the other:

* `DEFAULT_BUILD_TIMEOUT_SECONDS` bounds a full library build (configure + compile +
  install) in either environment. Real libraries in the build matrix take minutes.
* `HARNESS_PROBE_TIMEOUT_SECONDS` bounds one harness compile-and-link attempt. That is a
  single stub translation unit plus a link, so an overrun there is a hung linker, not a
  slow build.
"""

from __future__ import annotations

DEFAULT_BUILD_TIMEOUT_SECONDS = 900
HARNESS_PROBE_TIMEOUT_SECONDS = 60
