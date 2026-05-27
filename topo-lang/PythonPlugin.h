#ifndef TOPO_LANG_PYTHONPLUGIN_H
#define TOPO_LANG_PYTHONPLUGIN_H

#include "topo/Lang/LanguagePlugin.h"
#include "topo/Lang/CheckRunnerBase.h"
#include "topo/Lang/EmitterFactory.h"
#include "topo/Lang/BuildDriverFactory.h"
#include "PythonInitTemplateProvider.h"

namespace topo::lang {

class PythonPlugin : public LanguagePlugin {
public:
    PythonPlugin();

    HostLanguage language() const override;
    std::unique_ptr<check::LanguageAnalysisProvider> createAnalysisProvider() override;
    EmitterFactory* emitterFactory() override;
    BuildDriverFactory* buildDriverFactory() override;
    InitTemplateProvider* initTemplateProvider() override;
    std::unique_ptr<lsp::LSPBridge> createLSPBridge() override;
    std::unique_ptr<CheckRunnerBase> createCheckRunner() override;

private:
    class PythonEmitterFactory;
    class PythonBuildDriverFactory;
    std::unique_ptr<PythonEmitterFactory> emitterFactory_;
    std::unique_ptr<PythonBuildDriverFactory> buildDriverFactory_;
    PythonInitTemplateProvider initProvider_;
};

/// Call once at startup to register the Python plugin.
void registerPythonPlugin();

} // namespace topo::lang

#endif // TOPO_LANG_PYTHONPLUGIN_H
