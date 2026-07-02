#include "feature_extractor.hpp"

#include <sstream>

namespace feature_extractor {

namespace {

void appendEscaped(std::ostringstream &out, const std::string &value) {
  out << '"';
  for (unsigned char c : value) {
    switch (c) {
    case '"':
      out << "\\\"";
      break;
    case '\\':
      out << "\\\\";
      break;
    case '\n':
      out << "\\n";
      break;
    case '\r':
      out << "\\r";
      break;
    case '\t':
      out << "\\t";
      break;
    default:
      if (c < 0x20) {
        static const char *kHexDigits = "0123456789abcdef";
        out << "\\u00" << kHexDigits[(c >> 4) & 0xF] << kHexDigits[c & 0xF];
      } else {
        out << static_cast<char>(c);
      }
    }
  }
  out << '"';
}

void writeString(std::ostringstream &out, const std::string &value) {
  appendEscaped(out, value);
}

void writeOptionalString(std::ostringstream &out,
                         const std::optional<std::string> &value) {
  if (value.has_value()) {
    appendEscaped(out, *value);
  } else {
    out << "null";
  }
}

void writeBool(std::ostringstream &out, bool value) {
  out << (value ? "true" : "false");
}

template <typename Container, typename WriteItem>
void writeArray(std::ostringstream &out, const Container &items,
                WriteItem write_item) {
  out << '[';
  bool first = true;
  for (const auto &item : items) {
    if (!first) {
      out << ',';
    }
    first = false;
    write_item(item);
  }
  out << ']';
}

void writeParam(std::ostringstream &out, const Param &param) {
  out << "{\"name\":";
  writeString(out, param.name);
  out << ",\"type\":";
  writeString(out, param.type);
  out << '}';
}

void writeParams(std::ostringstream &out, const std::vector<Param> &params) {
  writeArray(out, params, [&](const Param &p) { writeParam(out, p); });
}

void writeFunction(std::ostringstream &out, const FunctionInfo &fn) {
  out << "{\"name\":";
  writeString(out, fn.name);
  out << ",\"return_type\":";
  writeString(out, fn.return_type);
  out << ",\"params\":";
  writeParams(out, fn.params);
  out << ",\"signature\":";
  writeString(out, fn.signature);
  out << ",\"is_public_api\":";
  writeBool(out, fn.is_public_api);
  out << ",\"header_path\":";
  writeString(out, fn.header_path);
  out << '}';
}

void writeTypedef(std::ostringstream &out, const TypedefInfo &td) {
  out << "{\"name\":";
  writeString(out, td.name);
  out << ",\"underlying_type\":";
  writeString(out, td.underlying_type);
  out << ",\"header_path\":";
  writeString(out, td.header_path);
  out << '}';
}

void writeMacro(std::ostringstream &out, const MacroInfo &macro) {
  out << "{\"name\":";
  writeString(out, macro.name);
  out << ",\"is_function_like\":";
  writeBool(out, macro.is_function_like);
  out << ",\"params\":";
  writeArray(out, macro.params,
             [&](const std::string &p) { writeString(out, p); });
  out << ",\"value\":";
  writeString(out, macro.value);
  out << ",\"header_path\":";
  writeString(out, macro.header_path);
  out << '}';
}

void writeEnumerator(std::ostringstream &out, const Enumerator &e) {
  out << "{\"name\":";
  writeString(out, e.name);
  out << ",\"value\":" << e.value << '}';
}

void writeEnum(std::ostringstream &out, const EnumInfo &e) {
  out << "{\"name\":";
  writeOptionalString(out, e.name);
  out << ",\"enumerators\":";
  writeArray(out, e.enumerators,
             [&](const Enumerator &en) { writeEnumerator(out, en); });
  out << ",\"header_path\":";
  writeString(out, e.header_path);
  out << '}';
}

void writeField(std::ostringstream &out, const Field &field) {
  out << "{\"name\":";
  writeString(out, field.name);
  out << ",\"type\":";
  writeString(out, field.type);
  out << '}';
}

void writeRecord(std::ostringstream &out, const RecordInfo &record) {
  out << "{\"name\":";
  writeOptionalString(out, record.name);
  out << ",\"kind\":";
  writeString(out, record.kind);
  out << ",\"fields\":";
  writeArray(out, record.fields, [&](const Field &f) { writeField(out, f); });
  out << ",\"header_path\":";
  writeString(out, record.header_path);
  out << '}';
}

} // namespace

std::string writeJson(const FeatureArtifact &artifact) {
  std::ostringstream out;
  out << "{\"schema_version\":" << artifact.schema_version;
  out << ",\"project_name\":";
  writeString(out, artifact.project_name);
  out << ",\"language\":";
  writeString(out, artifact.language);
  out << ",\"functions\":";
  writeArray(out, artifact.functions,
             [&](const FunctionInfo &fn) { writeFunction(out, fn); });
  out << ",\"typedefs\":";
  writeArray(out, artifact.typedefs,
             [&](const TypedefInfo &td) { writeTypedef(out, td); });
  out << ",\"macros\":";
  writeArray(out, artifact.macros,
             [&](const MacroInfo &m) { writeMacro(out, m); });
  out << ",\"enums\":";
  writeArray(out, artifact.enums,
             [&](const EnumInfo &e) { writeEnum(out, e); });
  out << ",\"records\":";
  writeArray(out, artifact.records,
             [&](const RecordInfo &r) { writeRecord(out, r); });
  out << ",\"warnings\":";
  writeArray(out, artifact.warnings,
             [&](const std::string &w) { writeString(out, w); });
  out << '}';
  return out.str();
}

} // namespace feature_extractor
