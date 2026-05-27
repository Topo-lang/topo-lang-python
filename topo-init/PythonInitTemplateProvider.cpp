#include "PythonInitTemplateProvider.h"

namespace topo::lang {

std::string PythonInitTemplateProvider::generateTopoToml(const std::string& projectName) const {
    return "[topo]\n"
           "root = \"" + projectName + ".topo\"\n"
           "\n"
           "[build]\n"
           "language = \"python\"\n"
           "sources = [\"**/*.py\"]\n"
           "output = \"" + projectName + "\"\n";
}

std::string PythonInitTemplateProvider::generateTypeBindings() const {
    return "using int = std::python::int;\n"
           "using float = std::python::float;\n"
           "using bool = std::python::bool;\n"
           "using str = std::python::str;\n";
}

} // namespace topo::lang
