#ifndef TOPO_TRANSPILE_PYTHONEMITTER_H
#define TOPO_TRANSPILE_PYTHONEMITTER_H

#include "topo/Transpile/Emitter.h"
#include "topo/Sema/TypeBinder.h"

namespace topo::transpile {

class PythonEmitter : public Emitter {
public:
    explicit PythonEmitter(TypeBinder binder = TypeBinder::createDefault(HostLanguage::Python));
    EmitResult emit(const TranspileModule& module) override;

private:
    TypeBinder binder_;

    std::string emitType(const TypeNode& type);
    std::string emitExpr(const Expr& expr);
    std::string emitStmt(const Stmt& stmt, int indent);
    std::string emitFunction(const TranspileFunction& func, int baseIndent = 0);
    std::string emitStruct(const TranspileType& type, int baseIndent = 0);
    std::string emitOwnership(const TypeNode& type);
};

} // namespace topo::transpile

#endif // TOPO_TRANSPILE_PYTHONEMITTER_H
