#include "PythonPlugin.h"

#include "PythonAnalysisProvider.h"
#include "PythonCheckRunner.h"
#include "PythonEmitter.h"
#include "PyrightBridge.h"

namespace topo::lang {

// -----------------------------------------------------------------------
// EmitterFactory
// -----------------------------------------------------------------------

class PythonPlugin::PythonEmitterFactory : public EmitterFactory {
public:
    std::unique_ptr<transpile::Emitter> createEmitter() override {
        return std::make_unique<transpile::PythonEmitter>();
    }
    std::string fileExtension() const override { return ".py"; }
};

// -----------------------------------------------------------------------
// BuildDriverFactory
// -----------------------------------------------------------------------

class PythonPlugin::PythonBuildDriverFactory : public BuildDriverFactory {
public:
    std::string backendToolName() const override { return "topo-build-python"; }
    std::string extractorToolName() const override { return "topo-extract-python"; }
};

// -----------------------------------------------------------------------
// PythonPlugin
// -----------------------------------------------------------------------

PythonPlugin::PythonPlugin()
    : emitterFactory_(std::make_unique<PythonEmitterFactory>()),
      buildDriverFactory_(std::make_unique<PythonBuildDriverFactory>()) {}

HostLanguage PythonPlugin::language() const { return HostLanguage::Python; }

std::unique_ptr<check::LanguageAnalysisProvider> PythonPlugin::createAnalysisProvider() {
    return check::createPythonAnalysisProvider();
}

EmitterFactory* PythonPlugin::emitterFactory() { return emitterFactory_.get(); }
BuildDriverFactory* PythonPlugin::buildDriverFactory() { return buildDriverFactory_.get(); }
InitTemplateProvider* PythonPlugin::initTemplateProvider() { return &initProvider_; }

std::unique_ptr<lsp::LSPBridge> PythonPlugin::createLSPBridge() {
    return std::make_unique<lsp::PyrightBridge>();
}

std::unique_ptr<CheckRunnerBase> PythonPlugin::createCheckRunner() {
    return std::make_unique<PythonCheckRunner>();
}

void registerPythonPlugin() {
    registerPlugin(std::make_unique<PythonPlugin>());
}

} // namespace topo::lang
