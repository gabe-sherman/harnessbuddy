library_builder:
- enable local library builds
- seed corpus?
- add option to target specific header files

artifact_extractor: [IMPLEMENTED — see specs/006-feature-extractor/]
- extract artifacts to yaml for oss-fuzz-gen, build locally and use compile_commands.json
- shipped as `harnessbuddy.feature_extractor`: a Clang LibTooling-based native tool
  (`extract-features`) produces a maximal JSON artifact from compile_commands.json,
  and `generate-benchmark` converts it to an oss-fuzz-gen-compatible YAML