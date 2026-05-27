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

    std::string sourceFileGlob() const override { return "**/*.py"; }

    std::string generateTopoToml(const std::string& projectName) const override;
    std::string generateTypeBindings() const override;
};

} // namespace topo::lang

#endif // TOPO_LANG_PYTHON_INITTEMPLATEPROVIDER_H
