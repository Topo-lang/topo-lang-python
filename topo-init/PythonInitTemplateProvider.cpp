#include "PythonInitTemplateProvider.h"

namespace topo::lang {

std::string PythonInitTemplateProvider::generateTopoToml(const std::string& projectName) const {
    // Realigned to match TopoGenerator::generateTopoToml (the live `topo init`
    // generator) and the Cpp/Rust providers: a [project] section with `name`,
    // root = "topo/main.topo" (not "<name>.topo"), the src-rooted sources glob
    // from sourceFileGlob() (not the inline "**/*.py"), and a [completeness]
    // section. This provider override is not on the CLI generation path
    // (TopoGenerator emits everything itself) — keeping it in lockstep with
    // TopoGenerator prevents the two definitions from silently disagreeing if
    // it is ever wired up or consulted.
    return "[project]\n"
           "name = \"" + projectName + "\"\n"
           "\n"
           "[topo]\n"
           "root = \"topo/main.topo\"\n"
           "\n"
           "[build]\n"
           "language = \"python\"\n"
           "sources = [\"" + sourceFileGlob() + "\"]\n"
           "output = \"" + projectName + "\"\n"
           "\n"
           "[completeness]\n"
           "ignore_main = true\n";
}

std::string PythonInitTemplateProvider::generateTypeBindings() const {
    return "using int = std::python::int;\n"
           "using float = std::python::float;\n"
           "using bool = std::python::bool;\n"
           "using str = std::python::str;\n";
}

} // namespace topo::lang
