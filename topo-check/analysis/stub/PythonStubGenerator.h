#ifndef TOPO_CHECK_PYTHONSTUBGENERATOR_H
#define TOPO_CHECK_PYTHONSTUBGENERATOR_H

#include "topo/Check/StubGenerator.h"
#include <string>

namespace topo::check {

/// Python implementation of StubGenerator.
/// Finds function definitions in Python source files by name matching,
/// then replaces the body using indentation-level detection.
class PythonStubGenerator : public StubGenerator {
public:
    StubResult stubFunction(const std::string& filePath, const std::string& funcName) override;
    bool restoreFile(const std::string& filePath, const StubResult& result) override;
};

} // namespace topo::check
#endif
