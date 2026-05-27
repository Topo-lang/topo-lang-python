#ifndef TOPO_CHECK_PYTHONLSPSYMBOLEXTRACTOR_H
#define TOPO_CHECK_PYTHONLSPSYMBOLEXTRACTOR_H

#include "topo/Check/SymbolExtractor.h"

// Forward declaration
namespace topo::lsp { class PyrightBridge; }

namespace topo::check {

/// LSP-based Python symbol extractor using Pyright semantic tokens and hover.
///
/// Extracts symbols by:
/// 1. Opening the document for Pyright analysis
/// 2. Requesting semantic tokens to find declarations/definitions
/// 3. Using hover to resolve qualified names and signatures
///
/// Falls back gracefully: if Pyright returns empty tokens for a file,
/// the result is simply empty (caller can fall back to regex extractor).
class PythonLSPSymbolExtractor : public SymbolExtractor {
public:
    explicit PythonLSPSymbolExtractor(lsp::PyrightBridge& bridge);

    std::vector<HostSymbol> extractSymbols(const std::string& filePath) override;

private:
    /// Parse return type from a Pyright hover signature.
    /// E.g. "(function) func(x: int) -> str" -> "str"
    static std::string parseReturnType(const std::string& hover);

    /// Parse parameter types from a Pyright hover signature.
    /// E.g. "(function) func(x: int, y: str) -> bool" -> ["int", "str"]
    static std::vector<std::string> parseParamTypes(const std::string& hover);

    /// Detect enclosing class from a qualified name like "mod::Class::method".
    static std::string detectEnclosingClass(const std::string& qualifiedName);

    /// Determine visibility from Python naming convention.
    static Visibility pythonVisibility(const std::string& name);

    lsp::PyrightBridge& bridge_;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONLSPSYMBOLEXTRACTOR_H
