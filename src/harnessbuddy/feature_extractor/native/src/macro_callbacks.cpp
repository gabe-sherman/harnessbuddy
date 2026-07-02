#include "feature_extractor.hpp"

#include <clang/Lex/Lexer.h>
#include <clang/Lex/MacroInfo.h>
#include <clang/Lex/Token.h>

namespace feature_extractor {

namespace {

class MacroCollectorCallbacks : public clang::PPCallbacks {
public:
  MacroCollectorCallbacks(FeatureCollector &collector, ProjectContext ctx,
                          const clang::SourceManager &sm,
                          const clang::LangOptions &lang_opts)
      : collector_(collector), ctx_(std::move(ctx)), sm_(sm),
        lang_opts_(lang_opts) {}

  void MacroDefined(const clang::Token &name_tok,
                    const clang::MacroDirective *md) override {
    const clang::MacroInfo *mi = md->getMacroInfo();
    if (mi == nullptr || name_tok.getIdentifierInfo() == nullptr) {
      return;
    }
    clang::SourceLocation loc = sm_.getSpellingLoc(mi->getDefinitionLoc());
    if (loc.isInvalid() || sm_.isInSystemHeader(loc)) {
      return;
    }
    llvm::StringRef filename = sm_.getFilename(loc);
    if (filename.empty() || !isWithinProject(ctx_, filename)) {
      return;
    }

    MacroInfo info;
    info.name = name_tok.getIdentifierInfo()->getName().str();
    info.is_function_like = mi->isFunctionLike();
    if (info.is_function_like) {
      for (const clang::IdentifierInfo *param : mi->params()) {
        info.params.push_back(param->getName().str());
      }
    }
    info.value = macroValueText(*mi);
    info.header_path = relativeHeaderPath(ctx_, filename);
    collector_.addMacro(std::move(info));
  }

private:
  std::string macroValueText(const clang::MacroInfo &mi) const {
    std::string value;
    for (const clang::Token &tok : mi.tokens()) {
      if (!value.empty()) {
        value += " ";
      }
      value += clang::Lexer::getSpelling(tok, sm_, lang_opts_);
    }
    return value;
  }

  FeatureCollector &collector_;
  ProjectContext ctx_;
  const clang::SourceManager &sm_;
  const clang::LangOptions &lang_opts_;
};

} // namespace

std::unique_ptr<clang::PPCallbacks>
newMacroCollectorCallbacks(FeatureCollector &collector, ProjectContext ctx,
                           const clang::SourceManager &sm,
                           const clang::LangOptions &lang_opts) {
  return std::make_unique<MacroCollectorCallbacks>(collector, std::move(ctx),
                                                   sm, lang_opts);
}

} // namespace feature_extractor
