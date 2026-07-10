#include "feature_extractor.hpp"

#include <cJSON.h>

namespace feature_extractor {

namespace {

cJSON *createParamArray(const std::vector<Param> &params) {
  cJSON *array = cJSON_CreateArray();
  for (const auto &param : params) {
    cJSON *item = cJSON_CreateObject();
    cJSON_AddStringToObject(item, "name", param.name.c_str());
    cJSON_AddStringToObject(item, "type", param.type.c_str());
    cJSON_AddItemToArray(array, item);
  }
  return array;
}

cJSON *createStringArray(const std::vector<std::string> &values) {
  cJSON *array = cJSON_CreateArray();
  for (const auto &value : values) {
    cJSON_AddItemToArray(array, cJSON_CreateString(value.c_str()));
  }
  return array;
}

void addOptionalString(cJSON *object, const char *key,
                        const std::optional<std::string> &value) {
  if (value.has_value()) {
    cJSON_AddStringToObject(object, key, value->c_str());
  } else {
    cJSON_AddNullToObject(object, key);
  }
}

cJSON *createFunction(const FunctionInfo &fn) {
  cJSON *object = cJSON_CreateObject();
  cJSON_AddStringToObject(object, "name", fn.name.c_str());
  cJSON_AddStringToObject(object, "return_type", fn.return_type.c_str());
  cJSON_AddItemToObject(object, "params", createParamArray(fn.params));
  cJSON_AddStringToObject(object, "signature", fn.signature.c_str());
  cJSON_AddBoolToObject(object, "is_public_api", fn.is_public_api);
  cJSON_AddStringToObject(object, "header_path", fn.header_path.c_str());
  return object;
}

cJSON *createTypedef(const TypedefInfo &td) {
  cJSON *object = cJSON_CreateObject();
  cJSON_AddStringToObject(object, "name", td.name.c_str());
  cJSON_AddStringToObject(object, "underlying_type", td.underlying_type.c_str());
  cJSON_AddStringToObject(object, "header_path", td.header_path.c_str());
  return object;
}

cJSON *createMacro(const MacroInfo &macro) {
  cJSON *object = cJSON_CreateObject();
  cJSON_AddStringToObject(object, "name", macro.name.c_str());
  cJSON_AddBoolToObject(object, "is_function_like", macro.is_function_like);
  cJSON_AddItemToObject(object, "params", createStringArray(macro.params));
  cJSON_AddStringToObject(object, "value", macro.value.c_str());
  cJSON_AddStringToObject(object, "header_path", macro.header_path.c_str());
  return object;
}

cJSON *createEnumerator(const Enumerator &e) {
  cJSON *object = cJSON_CreateObject();
  cJSON_AddStringToObject(object, "name", e.name.c_str());
  // cJSON stores numbers as double, so enumerator values beyond +-2^53 lose
  // precision; real-world enum constants stay well within that range.
  cJSON_AddNumberToObject(object, "value", static_cast<double>(e.value));
  return object;
}

cJSON *createEnum(const EnumInfo &e) {
  cJSON *object = cJSON_CreateObject();
  addOptionalString(object, "name", e.name);
  cJSON *enumerators = cJSON_CreateArray();
  for (const auto &enumerator : e.enumerators) {
    cJSON_AddItemToArray(enumerators, createEnumerator(enumerator));
  }
  cJSON_AddItemToObject(object, "enumerators", enumerators);
  cJSON_AddStringToObject(object, "header_path", e.header_path.c_str());
  return object;
}

cJSON *createField(const Field &field) {
  cJSON *object = cJSON_CreateObject();
  cJSON_AddStringToObject(object, "name", field.name.c_str());
  cJSON_AddStringToObject(object, "type", field.type.c_str());
  return object;
}

cJSON *createRecord(const RecordInfo &record) {
  cJSON *object = cJSON_CreateObject();
  addOptionalString(object, "name", record.name);
  cJSON_AddStringToObject(object, "kind", record.kind.c_str());
  cJSON *fields = cJSON_CreateArray();
  for (const auto &field : record.fields) {
    cJSON_AddItemToArray(fields, createField(field));
  }
  cJSON_AddItemToObject(object, "fields", fields);
  cJSON_AddStringToObject(object, "header_path", record.header_path.c_str());
  return object;
}

} // namespace

std::string writeJson(const FeatureArtifact &artifact) {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddNumberToObject(root, "schema_version", artifact.schema_version);
  cJSON_AddStringToObject(root, "project_name", artifact.project_name.c_str());
  cJSON_AddStringToObject(root, "language", artifact.language.c_str());

  cJSON *functions = cJSON_CreateArray();
  for (const auto &fn : artifact.functions) {
    cJSON_AddItemToArray(functions, createFunction(fn));
  }
  cJSON_AddItemToObject(root, "functions", functions);

  cJSON *typedefs = cJSON_CreateArray();
  for (const auto &td : artifact.typedefs) {
    cJSON_AddItemToArray(typedefs, createTypedef(td));
  }
  cJSON_AddItemToObject(root, "typedefs", typedefs);

  cJSON *macros = cJSON_CreateArray();
  for (const auto &macro : artifact.macros) {
    cJSON_AddItemToArray(macros, createMacro(macro));
  }
  cJSON_AddItemToObject(root, "macros", macros);

  cJSON *enums = cJSON_CreateArray();
  for (const auto &e : artifact.enums) {
    cJSON_AddItemToArray(enums, createEnum(e));
  }
  cJSON_AddItemToObject(root, "enums", enums);

  cJSON *records = cJSON_CreateArray();
  for (const auto &record : artifact.records) {
    cJSON_AddItemToArray(records, createRecord(record));
  }
  cJSON_AddItemToObject(root, "records", records);

  cJSON_AddItemToObject(root, "warnings", createStringArray(artifact.warnings));

  char *rendered = cJSON_PrintUnformatted(root);
  std::string result(rendered);
  cJSON_free(rendered);
  cJSON_Delete(root);
  return result;
}

} // namespace feature_extractor
