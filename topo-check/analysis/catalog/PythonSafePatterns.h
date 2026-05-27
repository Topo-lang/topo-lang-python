#ifndef TOPO_CHECK_PYTHONSAFEPATTERNS_H
#define TOPO_CHECK_PYTHONSAFEPATTERNS_H

#include <string>
#include <unordered_set>
#include <vector>

namespace topo::check {

/// Loads and queries the Python safety whitelist (PythonSafePatterns.toml).
/// Used by L2 (LSP) analysis to determine if a resolved symbol is safe.
/// Python uses "." module separators externally; internally stored with "::".
class PythonSafePatterns {
public:
    /// Load patterns from a TOML file. Returns false on parse error.
    bool load(const std::string& tomlPath);

    /// Load from the default location relative to the topo installation.
    /// Searches: $TOPO_PATTERNS_DIR, then alongside the source tree.
    bool loadDefault();

    // --- Construct whitelist ---

    /// Is this a known unsafe construct keyword? (e.g., exec, eval)
    bool isConstructUnsafe(const std::string& keyword) const;

    /// Is this a known safe construct keyword? (e.g., if, for, def)
    bool isConstructSafe(const std::string& keyword) const;

    // --- stdlib symbol whitelist (L2) ---

    /// Is this fully qualified symbol name safe?
    /// Accepts both "::" separator (internal) and "." separator (Python native).
    /// e.g., "builtins::print", "math::sqrt", "collections.Counter"
    bool isStdlibSymbolSafe(const std::string& qualifiedName) const;

    // --- Accessors ---
    const std::unordered_set<std::string>& safeConstructs() const { return safeConstructs_; }
    const std::unordered_set<std::string>& unsafeConstructs() const { return unsafeConstructs_; }
    const std::unordered_set<std::string>& safeStdlib() const { return safeStdlib_; }

    /// Convert Python dot-separated name to internal "::" separator.
    static std::string dotToColonColon(const std::string& name);

private:
    std::unordered_set<std::string> safeConstructs_;
    std::unordered_set<std::string> unsafeConstructs_;
    std::unordered_set<std::string> safeStdlib_;
    bool loaded_ = false;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONSAFEPATTERNS_H
