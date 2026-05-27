#include "PythonSafePatterns.h"

#define TOML_HEADER_ONLY 1
#define TOML_EXCEPTIONS 0
#include <toml++/toml.hpp>

#include <filesystem>
#include <iostream>

namespace fs = std::filesystem;

namespace topo::check {

std::string PythonSafePatterns::dotToColonColon(const std::string& name) {
    std::string result = name;
    for (size_t i = 0; i < result.size(); ++i) {
        if (result[i] == '.') {
            result.replace(i, 1, "::");
            ++i; // skip the extra ':'
        }
    }
    return result;
}

bool PythonSafePatterns::load(const std::string& tomlPath) {
    toml::parse_result result = toml::parse_file(tomlPath);
    if (!result) {
        std::cerr << "PythonSafePatterns: failed to parse " << tomlPath << ": "
                  << result.error() << "\n";
        return false;
    }
    const auto& tbl = result.table();

    // [constructs].safe
    if (auto arr = tbl.at_path("constructs.safe").as_array()) {
        for (const auto& elem : *arr) {
            if (auto s = elem.value<std::string>()) safeConstructs_.insert(*s);
        }
    }
    // [constructs].unsafe
    if (auto arr = tbl.at_path("constructs.unsafe").as_array()) {
        for (const auto& elem : *arr) {
            if (auto s = elem.value<std::string>()) unsafeConstructs_.insert(*s);
        }
    }
    // [stdlib].safe — array of qualified names using "::" separator
    if (auto arr = tbl.at_path("stdlib.safe").as_array()) {
        for (const auto& elem : *arr) {
            if (auto s = elem.value<std::string>()) safeStdlib_.insert(*s);
        }
    }

    loaded_ = true;
    return true;
}

bool PythonSafePatterns::loadDefault() {
    // Try environment variable first.
    if (const char* dir = std::getenv("TOPO_PATTERNS_DIR")) {
        fs::path p = fs::path(dir) / "PythonSafePatterns.toml";
        if (fs::exists(p)) return load(p.string());
    }
    // For development, fall back to the in-source catalog location.
    fs::path p = fs::path(TOPO_SOURCE_DIR) / "topo-lang-python" /
                 "topo-check" / "analysis" / "catalog" /
                 "PythonSafePatterns.toml";
    if (fs::exists(p)) return load(p.string());
    return false;
}

bool PythonSafePatterns::isConstructSafe(const std::string& keyword) const {
    return safeConstructs_.count(keyword) > 0;
}

bool PythonSafePatterns::isConstructUnsafe(const std::string& keyword) const {
    return unsafeConstructs_.count(keyword) > 0;
}

bool PythonSafePatterns::isStdlibSymbolSafe(const std::string& qualifiedName) const {
    // Normalize to "::" separator for lookup
    std::string normalized = dotToColonColon(qualifiedName);

    // Exact match first
    if (safeStdlib_.count(normalized)) return true;

    // Try prefix match: "collections::Counter::most_common" -> check "collections::Counter"
    // Members of safe types/modules are safe
    auto pos = normalized.rfind("::");
    while (pos != std::string::npos && pos > 0) {
        std::string prefix = normalized.substr(0, pos);
        if (safeStdlib_.count(prefix)) return true;
        pos = prefix.rfind("::");
    }
    return false;
}

} // namespace topo::check
