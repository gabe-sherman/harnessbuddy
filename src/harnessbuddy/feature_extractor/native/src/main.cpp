#include "feature_extractor.hpp"

#include <clang/Tooling/CompilationDatabase.h>
#include <clang/Tooling/Tooling.h>
#include <llvm/Support/FileSystem.h>
#include <llvm/Support/Path.h>
#include <llvm/Support/raw_ostream.h>

#include <fstream>
#include <string>
#include <vector>

namespace {

bool looksLikeCxx(llvm::StringRef path) {
  return path.ends_with(".cc") || path.ends_with(".cpp") ||
         path.ends_with(".cxx") || path.ends_with(".hpp") ||
         path.ends_with(".hh") || path.ends_with(".hxx") ||
         path.ends_with(".mm");
}

std::string detectLanguage(const std::vector<std::string> &files) {
  // Must match harnessbuddy.library_builder.models.Language's values ("c" | "c++"),
  // consumed by extraction.py's Language parsing.
  for (const std::string &file : files) {
    if (looksLikeCxx(file)) {
      return "c++";
    }
  }
  return "c";
}

// The compile-commands directory (argv[1]) is often a separate build directory
// (e.g. CMake's -B), not the source checkout, so it can't be used as the
// project root for isWithinProject() checks. Recover the real source root as
// the common ancestor of every translation unit's absolute file path instead.
std::string computeProjectRoot(const std::vector<std::string> &files) {
  std::vector<llvm::StringRef> common;
  bool first = true;
  for (const std::string &file : files) {
    llvm::SmallString<256> dir(file);
    llvm::sys::path::remove_filename(dir);
    llvm::sys::path::remove_dots(dir, /*remove_dot_dot=*/true);
    std::vector<llvm::StringRef> components(llvm::sys::path::begin(dir),
                                             llvm::sys::path::end(dir));
    if (first) {
      common = std::move(components);
      first = false;
      continue;
    }
    size_t limit = std::min(common.size(), components.size());
    size_t matched = 0;
    while (matched < limit && common[matched] == components[matched]) {
      ++matched;
    }
    common.resize(matched);
  }
  llvm::SmallString<256> root;
  for (llvm::StringRef component : common) {
    llvm::sys::path::append(root, component);
  }
  return std::string(root);
}

} // namespace

int main(int argc, char **argv) {
  if (argc != 4) {
    llvm::errs()
        << "usage: feature_extractor <compile-commands-dir> <output-json-path> "
           "<project-name>\n";
    return 1;
  }

  llvm::SmallString<256> compile_commands_dir(argv[1]);
  llvm::sys::fs::make_absolute(compile_commands_dir);
  llvm::sys::path::remove_dots(compile_commands_dir, /*remove_dot_dot=*/true);
  const std::string output_json_path = argv[2];
  const std::string project_name = argv[3];

  std::string error_message;
  std::unique_ptr<clang::tooling::CompilationDatabase> db =
      clang::tooling::CompilationDatabase::autoDetectFromDirectory(
          compile_commands_dir, error_message);
  if (db == nullptr) {
    llvm::errs() << "error loading compile_commands.json from "
                 << compile_commands_dir << ": " << error_message << "\n";
    return 1;
  }

  std::vector<std::string> files = db->getAllFiles();
  feature_extractor::FeatureCollector collector;
  feature_extractor::ProjectContext ctx{computeProjectRoot(files)};

  clang::tooling::ClangTool tool(*db, files);
  tool.appendArgumentsAdjuster(clang::tooling::getInsertArgumentAdjuster(
      "-resource-dir=" CLANG_RESOURCE_DIR,
      clang::tooling::ArgumentInsertPosition::BEGIN));
  // -resource-dir only covers clang's own builtin headers (stddef.h, stdarg.h, ...).
  // Platform libc/SDK headers (inttypes.h, stdio.h, ...) still need an explicit
  // -isysroot on macOS, since ClangTool's ad hoc driver invocation doesn't reliably
  // auto-detect the Xcode SDK the way a plain `clang` invocation does. CLANG_SYSROOT
  // is empty on non-Apple platforms, where the default system include paths apply.
  const std::string clang_sysroot = CLANG_SYSROOT;
  if (!clang_sysroot.empty()) {
    tool.appendArgumentsAdjuster(clang::tooling::getInsertArgumentAdjuster(
        {"-isysroot", clang_sysroot},
        clang::tooling::ArgumentInsertPosition::BEGIN));
  }
  std::unique_ptr<clang::tooling::FrontendActionFactory> factory =
      feature_extractor::newExtractionActionFactory(collector, ctx);
  int run_result = tool.run(factory.get());

  feature_extractor::FeatureArtifact artifact = collector.artifact();
  artifact.project_name = project_name;
  artifact.language = detectLanguage(files);
  if (run_result != 0) {
    artifact.warnings.push_back(
        "one or more translation units in compile_commands.json failed to "
        "compile "
        "cleanly; extraction may be incomplete for those files");
  }

  std::ofstream out(output_json_path);
  if (!out) {
    llvm::errs() << "error: could not open " << output_json_path
                 << " for writing\n";
    return 1;
  }
  out << feature_extractor::writeJson(artifact);
  return 0;
}
