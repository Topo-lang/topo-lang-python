#ifndef TOPO_LANG_PYTHON_INITTEMPLATEPROVIDER_H
#define TOPO_LANG_PYTHON_INITTEMPLATEPROVIDER_H

#include "topo/Lang/InitTemplateProvider.h"

namespace topo::lang {

class PythonInitTemplateProvider : public InitTemplateProvider {
public:
    std::string languageName() const override { return "python"; }

    std::vector<std::string> filePatterns() const override {
        return {"*.py"};
    }

    // src/-rooted to match TopoGenerator (the live `topo init` generator) and
    // the Cpp/Rust providers. Was "**/*.py" (root-recursive), which diverged
    // from the src-rooted convention the rest of the toolchain uses.
    std::string sourceFileGlob() const override { return "src/**/*.py"; }

    std::string generateTopoToml(const std::string& projectName) const override;
    std::string generateTypeBindings() const override;
};

} // namespace topo::lang

#endif // TOPO_LANG_PYTHON_INITTEMPLATEPROVIDER_H
