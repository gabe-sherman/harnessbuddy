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
  for (const std::string &file : files) {
    if (looksLikeCxx(file)) {
      return "cpp";
    }
  }
  return "c";
}

} // namespace

int main(int argc, char **argv) {
  if (argc != 4) {
    llvm::errs()
        << "usage: feature_extractor <compile-commands-dir> <output-json-path> "
           "<project-name>\n";
    return 1;
  }

  llvm::SmallString<256> project_root(argv[1]);
  llvm::sys::fs::make_absolute(project_root);
  llvm::sys::path::remove_dots(project_root, /*remove_dot_dot=*/true);
  const std::string output_json_path = argv[2];
  const std::string project_name = argv[3];

  std::string error_message;
  std::unique_ptr<clang::tooling::CompilationDatabase> db =
      clang::tooling::CompilationDatabase::autoDetectFromDirectory(
          project_root, error_message);
  if (db == nullptr) {
    llvm::errs() << "error loading compile_commands.json from " << project_root
                 << ": " << error_message << "\n";
    return 1;
  }

  std::vector<std::string> files = db->getAllFiles();
  feature_extractor::FeatureCollector collector;
  feature_extractor::ProjectContext ctx{std::string(project_root)};

  clang::tooling::ClangTool tool(*db, files);
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
