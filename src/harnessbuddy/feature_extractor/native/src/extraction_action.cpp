#include "feature_extractor.hpp"

#include <clang/AST/ASTConsumer.h>
#include <clang/AST/Decl.h>
#include <clang/AST/DeclCXX.h>
#include <clang/AST/RecursiveASTVisitor.h>
#include <clang/Frontend/CompilerInstance.h>
#include <llvm/ADT/SmallString.h>
#include <llvm/Support/Path.h>

namespace feature_extractor {

bool isWithinProject(const ProjectContext &ctx, llvm::StringRef abs_path) {
  if (ctx.project_root.empty() || abs_path.empty()) {
    return false;
  }
  llvm::SmallString<256> root(ctx.project_root);
  llvm::sys::path::remove_dots(root, /*remove_dot_dot=*/true);
  llvm::SmallString<256> path(abs_path);
  llvm::sys::path::remove_dots(path, /*remove_dot_dot=*/true);
  return path.str().starts_with(root.str());
}

std::string relativeHeaderPath(const ProjectContext &ctx,
                               llvm::StringRef abs_path) {
  if (!isWithinProject(ctx, abs_path)) {
    return std::string(abs_path);
  }
  llvm::StringRef rel = abs_path.drop_front(ctx.project_root.size());
  while (!rel.empty() && rel.front() == '/') {
    rel = rel.drop_front(1);
  }
  return std::string(rel);
}

void FeatureCollector::addFunction(FunctionInfo info) {
  if (!seen_functions_.insert(info.signature).second) {
    return;
  }
  artifact_.functions.push_back(std::move(info));
}

void FeatureCollector::addTypedef(TypedefInfo info) {
  std::string key = info.name + "|" + info.header_path;
  if (!seen_typedefs_.insert(key).second) {
    return;
  }
  artifact_.typedefs.push_back(std::move(info));
}

void FeatureCollector::addMacro(MacroInfo info) {
  std::string key = info.name + "|" + info.value;
  if (!seen_macros_.insert(key).second) {
    return;
  }
  artifact_.macros.push_back(std::move(info));
}

void FeatureCollector::addEnum(EnumInfo info) {
  std::string key = info.name.value_or("") + "|" + info.header_path;
  if (!seen_enums_.insert(key).second) {
    return;
  }
  artifact_.enums.push_back(std::move(info));
}

void FeatureCollector::addRecord(RecordInfo info) {
  std::string key =
      info.kind + "|" + info.name.value_or("") + "|" + info.header_path;
  if (!seen_records_.insert(key).second) {
    return;
  }
  artifact_.records.push_back(std::move(info));
}

void FeatureCollector::addWarning(std::string warning) {
  artifact_.warnings.push_back(std::move(warning));
}

namespace {

bool hasSourceExtension(llvm::StringRef path) {
  return path.ends_with(".c") || path.ends_with(".cc") ||
         path.ends_with(".cpp") || path.ends_with(".cxx") ||
         path.ends_with(".m") || path.ends_with(".mm");
}

// research.md section 5: external linkage plus a declaration location inside a
// library-owned header (not a system header, not a .c/.cpp translation unit).
bool isPublicApiLocation(const clang::NamedDecl &decl,
                         const ProjectContext &ctx,
                         const clang::SourceManager &sm) {
  if (decl.getLinkageInternal() != clang::Linkage::External) {
    return false;
  }
  clang::SourceLocation loc = sm.getSpellingLoc(decl.getLocation());
  if (loc.isInvalid() || sm.isInSystemHeader(loc)) {
    return false;
  }
  llvm::StringRef filename = sm.getFilename(loc);
  if (filename.empty() || !isWithinProject(ctx, filename)) {
    return false;
  }
  return !hasSourceExtension(filename);
}

std::string buildSignature(const FunctionInfo &info) {
  std::string sig = info.return_type + " " + info.name + "(";
  for (size_t i = 0; i < info.params.size(); ++i) {
    if (i > 0) {
      sig += ", ";
    }
    sig += info.params[i].type;
  }
  sig += ")";
  return sig;
}

class ExtractionVisitor : public clang::RecursiveASTVisitor<ExtractionVisitor> {
public:
  ExtractionVisitor(FeatureCollector &collector, const ProjectContext &ctx,
                    const clang::SourceManager &sm)
      : collector_(collector), ctx_(ctx), sm_(sm) {}

  bool VisitFunctionDecl(clang::FunctionDecl *decl) {
    if (decl->isImplicit() || llvm::isa<clang::CXXMethodDecl>(decl)) {
      return true;
    }
    clang::SourceLocation loc = sm_.getSpellingLoc(decl->getLocation());
    if (loc.isInvalid()) {
      return true;
    }
    llvm::StringRef filename = sm_.getFilename(loc);
    if (filename.empty()) {
      return true;
    }

    FunctionInfo info;
    info.name = decl->getNameAsString();
    if (info.name.empty()) {
      return true;
    }
    info.return_type = decl->getReturnType().getAsString();
    for (const clang::ParmVarDecl *param : decl->parameters()) {
      info.params.push_back(
          Param{param->getNameAsString(), param->getType().getAsString()});
    }
    info.signature = buildSignature(info);
    info.is_public_api = isPublicApiLocation(*decl, ctx_, sm_);
    info.header_path = relativeHeaderPath(ctx_, filename);
    collector_.addFunction(std::move(info));
    return true;
  }

  bool VisitTypedefNameDecl(clang::TypedefNameDecl *decl) {
    if (decl->isImplicit()) {
      return true;
    }
    clang::SourceLocation loc = sm_.getSpellingLoc(decl->getLocation());
    if (loc.isInvalid()) {
      return true;
    }
    llvm::StringRef filename = sm_.getFilename(loc);
    if (filename.empty() || !isWithinProject(ctx_, filename)) {
      return true;
    }
    TypedefInfo info;
    info.name = decl->getNameAsString();
    info.underlying_type = decl->getUnderlyingType().getAsString();
    info.header_path = relativeHeaderPath(ctx_, filename);
    collector_.addTypedef(std::move(info));
    return true;
  }

  bool VisitEnumDecl(clang::EnumDecl *decl) {
    if (!decl->isThisDeclarationADefinition()) {
      return true;
    }
    clang::SourceLocation loc = sm_.getSpellingLoc(decl->getLocation());
    if (loc.isInvalid()) {
      return true;
    }
    llvm::StringRef filename = sm_.getFilename(loc);
    if (filename.empty() || !isWithinProject(ctx_, filename)) {
      return true;
    }
    EnumInfo info;
    if (decl->getIdentifier() != nullptr) {
      info.name = decl->getNameAsString();
    }
    for (const clang::EnumConstantDecl *ecd : decl->enumerators()) {
      info.enumerators.push_back(
          Enumerator{ecd->getNameAsString(), ecd->getInitVal().getSExtValue()});
    }
    info.header_path = relativeHeaderPath(ctx_, filename);
    collector_.addEnum(std::move(info));
    return true;
  }

  bool VisitRecordDecl(clang::RecordDecl *decl) {
    if (!decl->isThisDeclarationADefinition() ||
        (!decl->isStruct() && !decl->isUnion())) {
      return true;
    }
    clang::SourceLocation loc = sm_.getSpellingLoc(decl->getLocation());
    if (loc.isInvalid()) {
      return true;
    }
    llvm::StringRef filename = sm_.getFilename(loc);
    if (filename.empty() || !isWithinProject(ctx_, filename)) {
      return true;
    }
    RecordInfo info;
    if (decl->getIdentifier() != nullptr) {
      info.name = decl->getNameAsString();
    }
    info.kind = decl->isUnion() ? "union" : "struct";
    for (const clang::FieldDecl *field : decl->fields()) {
      info.fields.push_back(
          Field{field->getNameAsString(), field->getType().getAsString()});
    }
    info.header_path = relativeHeaderPath(ctx_, filename);
    collector_.addRecord(std::move(info));
    return true;
  }

private:
  FeatureCollector &collector_;
  const ProjectContext &ctx_;
  const clang::SourceManager &sm_;
};

class ExtractionASTConsumer : public clang::ASTConsumer {
public:
  ExtractionASTConsumer(FeatureCollector &collector, ProjectContext ctx)
      : collector_(collector), ctx_(std::move(ctx)) {}

  void HandleTranslationUnit(clang::ASTContext &context) override {
    ExtractionVisitor visitor(collector_, ctx_, context.getSourceManager());
    visitor.TraverseDecl(context.getTranslationUnitDecl());
  }

private:
  FeatureCollector &collector_;
  ProjectContext ctx_;
};

class ExtractionFrontendAction : public clang::ASTFrontendAction {
public:
  ExtractionFrontendAction(FeatureCollector &collector, ProjectContext ctx)
      : collector_(collector), ctx_(std::move(ctx)) {}

  bool BeginSourceFileAction(clang::CompilerInstance &ci) override {
    ci.getPreprocessor().addPPCallbacks(newMacroCollectorCallbacks(
        collector_, ctx_, ci.getSourceManager(), ci.getLangOpts()));
    return true;
  }

  std::unique_ptr<clang::ASTConsumer>
  CreateASTConsumer(clang::CompilerInstance & /*ci*/,
                    llvm::StringRef /*file*/) override {
    return std::make_unique<ExtractionASTConsumer>(collector_, ctx_);
  }

private:
  FeatureCollector &collector_;
  ProjectContext ctx_;
};

class ExtractionFrontendActionFactory
    : public clang::tooling::FrontendActionFactory {
public:
  ExtractionFrontendActionFactory(FeatureCollector &collector,
                                  ProjectContext ctx)
      : collector_(collector), ctx_(std::move(ctx)) {}

  std::unique_ptr<clang::FrontendAction> create() override {
    return std::make_unique<ExtractionFrontendAction>(collector_, ctx_);
  }

private:
  FeatureCollector &collector_;
  ProjectContext ctx_;
};

} // namespace

std::unique_ptr<clang::tooling::FrontendActionFactory>
newExtractionActionFactory(FeatureCollector &collector, ProjectContext ctx) {
  return std::make_unique<ExtractionFrontendActionFactory>(collector,
                                                           std::move(ctx));
}

} // namespace feature_extractor
