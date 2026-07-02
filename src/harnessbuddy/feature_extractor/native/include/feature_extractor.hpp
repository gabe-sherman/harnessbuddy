#pragma once

#include <clang/Basic/LangOptions.h>
#include <clang/Basic/SourceManager.h>
#include <clang/Frontend/FrontendAction.h>
#include <clang/Lex/PPCallbacks.h>
#include <clang/Tooling/Tooling.h>
#include <llvm/ADT/StringRef.h>

#include <memory>
#include <optional>
#include <string>
#include <unordered_set>
#include <vector>

namespace feature_extractor {

struct Param {
  std::string name;
  std::string type;
};

struct FunctionInfo {
  std::string name;
  std::string return_type;
  std::vector<Param> params;
  std::string signature;
  bool is_public_api = false;
  std::string header_path;
};

struct TypedefInfo {
  std::string name;
  std::string underlying_type;
  std::string header_path;
};

struct MacroInfo {
  std::string name;
  bool is_function_like = false;
  std::vector<std::string> params;
  std::string value;
  std::string header_path;
};

struct Enumerator {
  std::string name;
  long long value = 0;
};

struct EnumInfo {
  std::optional<std::string> name;
  std::vector<Enumerator> enumerators;
  std::string header_path;
};

struct Field {
  std::string name;
  std::string type;
};

struct RecordInfo {
  std::optional<std::string> name;
  std::string kind; // "struct" | "union"
  std::vector<Field> fields;
  std::string header_path;
};

struct FeatureArtifact {
  int schema_version = 1;
  std::string project_name;
  std::string language; // "c" | "cpp"
  std::vector<FunctionInfo> functions;
  std::vector<TypedefInfo> typedefs;
  std::vector<MacroInfo> macros;
  std::vector<EnumInfo> enums;
  std::vector<RecordInfo> records;
  std::vector<std::string> warnings;
};

// Identifies the project source root (the directory containing
// compile_commands.json), used to compute header_path values relative to it and
// to decide whether a declaration's location is "library-owned" for
// is_public_api (research.md section 5) rather than third-party/system.
struct ProjectContext {
  std::string project_root;
};

// Not thread-safe. ClangTool runs one translation unit at a time by default, so
// a single collector instance can be shared across every TU's
// FrontendAction/PPCallbacks to accumulate and deduplicate declarations seen
// from more than one TU (e.g. via a shared header).
class FeatureCollector {
public:
  void addFunction(FunctionInfo info);
  void addTypedef(TypedefInfo info);
  void addMacro(MacroInfo info);
  void addEnum(EnumInfo info);
  void addRecord(RecordInfo info);
  void addWarning(std::string warning);

  const FeatureArtifact &artifact() const { return artifact_; }

private:
  FeatureArtifact artifact_;
  std::unordered_set<std::string> seen_functions_;
  std::unordered_set<std::string> seen_typedefs_;
  std::unordered_set<std::string> seen_macros_;
  std::unordered_set<std::string> seen_enums_;
  std::unordered_set<std::string> seen_records_;
};

// Returns true when absPath is inside ctx.project_root (i.e. library-owned, not
// a system or third-party header).
bool isWithinProject(const ProjectContext &ctx, llvm::StringRef abs_path);

// Returns absPath relative to ctx.project_root when isWithinProject(ctx,
// absPath), otherwise returns absPath unchanged.
std::string relativeHeaderPath(const ProjectContext &ctx,
                               llvm::StringRef abs_path);

// Builds the ASTFrontendAction that extracts functions/typedefs/enums/records
// (extraction_action.cpp) and registers the macro-extracting PPCallbacks
// (macro_callbacks.cpp) for a single translation unit, writing results into
// collector.
std::unique_ptr<clang::tooling::FrontendActionFactory>
newExtractionActionFactory(FeatureCollector &collector, ProjectContext ctx);

// Registers a PPCallbacks (macro_callbacks.cpp) on the given Preprocessor that
// extracts macro definitions (FR-006) into collector.
std::unique_ptr<clang::PPCallbacks>
newMacroCollectorCallbacks(FeatureCollector &collector, ProjectContext ctx,
                           const clang::SourceManager &sm,
                           const clang::LangOptions &lang_opts);

// Serializes artifact to the exact JSON shape of
// contracts/feature-artifact.schema.json.
std::string writeJson(const FeatureArtifact &artifact);

} // namespace feature_extractor
