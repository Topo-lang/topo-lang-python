#include "PythonEmitter.h"
#include <algorithm>
#include <cctype>
#include <functional>
#include <map>
#include <sstream>

namespace topo::transpile {

static std::string ind(int level) {
    return std::string(level * 4, ' ');
}

// PEP 695 type-parameter list (`[T, U: Bound]`). Single-bound MVP: when
// constraintType is populated the parameter renders as `T: Bound`, mirroring
// PEP 695's bound syntax (`class C[T: Comparable]`, `def f[T: Bound]`) and
// PEP 696's default syntax (`class C[T = int]`, `def f[T = int]`); both
// route through the emitType callback so qualified / parameterised bounds
// and defaults round-trip. Empty ⇒ "" so non-generic decls stay
// byte-identical. PEP 696 allows defaults on both classes and functions
// (unlike Rust, no E0091-style restriction), so the helper renders the
// `=` uniformly at every emit-site.
// Python (PEP 695) has no equivalent to Rust associated-type bindings
// (`Iterator<Item = u8>`). PEP 695 `[T: Bound]` is line-syntactic and
// admits no inline `#` comment, so the drop note is collected into
// `outLeadingNotes` and the call site prepends it (as `# ...` lines) to
// the decl line. Empty TypeNode.assocBindings ⇒ no note ⇒ byte-identical
// to pre-feature output.
static bool pyTypeNodeHasAssocBindings(const TypeNode& t) {
    return !t.assocBindings.empty();
}

// Rust lifetime bounds (`T: 'a`) ride the wire as TypeNodes whose
// nameParts[0] starts with `'`. Python has no analogue — entries are
// silently dropped from any bound list. No `#`-prefixed note because
// lifetime annotations are noise for non-Rust hosts.
static bool pyIsWireLifetimeBound(const TypeNode& t) {
    return !t.nameParts.empty() && !t.nameParts[0].empty() &&
           t.nameParts[0][0] == '\'';
}

static std::string pep695ParamsImpl(const std::vector<TemplateParamDecl>& tpsIn,
                                    const std::function<std::string(const TypeNode&)>& renderType,
                                    std::string* outLeadingNotes = nullptr) {
    // Filter out kind=Lifetime entries up front.
    std::vector<TemplateParamDecl> tps;
    tps.reserve(tpsIn.size());
    for (const auto& p : tpsIn) {
        if (p.kind == TemplateParamDecl::LifetimeParam) continue;
        TemplateParamDecl q = p;
        if (q.kind == TemplateParamDecl::TypeParam &&
            !q.constraintType.nameParts.empty() &&
            pyIsWireLifetimeBound(q.constraintType)) {
            std::vector<TypeNode> kept;
            for (const auto& eb : q.extraBounds)
                if (!pyIsWireLifetimeBound(eb)) kept.push_back(eb);
            if (kept.empty()) {
                q.constraintType = TypeNode{};
                q.extraBounds.clear();
            } else {
                q.constraintType = kept.front();
                q.extraBounds.assign(kept.begin() + 1, kept.end());
            }
        } else if (q.kind == TemplateParamDecl::TypeParam) {
            std::vector<TypeNode> kept;
            for (const auto& eb : q.extraBounds)
                if (!pyIsWireLifetimeBound(eb)) kept.push_back(eb);
            q.extraBounds = std::move(kept);
        }
        tps.push_back(std::move(q));
    }
    if (tps.empty()) return "";
    std::string s = "[";
    for (size_t i = 0; i < tps.size(); ++i) {
        if (i > 0) s += ", ";
        s += tps[i].name;
        if (tps[i].kind == TemplateParamDecl::TypeParam &&
            !tps[i].constraintType.nameParts.empty()) {
            s += ": " + renderType(tps[i].constraintType);
            if (outLeadingNotes &&
                (pyTypeNodeHasAssocBindings(tps[i].constraintType) ||
                 std::any_of(tps[i].extraBounds.begin(), tps[i].extraBounds.end(),
                             pyTypeNodeHasAssocBindings))) {
                *outLeadingNotes +=
                    "# TOPO-TRANSPILE: associated-type bindings on " + tps[i].name +
                    " dropped (no Python equivalent)\n";
            }
        }
        if (tps[i].kind == TemplateParamDecl::TypeParam &&
            tps[i].defaultType.has_value() &&
            !tps[i].defaultType->nameParts.empty()) {
            s += " = " + renderType(*tps[i].defaultType);
        }
    }
    s += "]";
    return s;
}

static std::string fidelityComment(Fidelity f, int level) {
    if (f == Fidelity::Recovered) return ind(level) + "# [recovered]\n";
    if (f == Fidelity::Inferred) return ind(level) + "# [inferred]\n";
    return "";
}

static std::string binaryOpStr(BinaryOp op) {
    switch (op) {
    case BinaryOp::Add: return "+";
    case BinaryOp::Sub: return "-";
    case BinaryOp::Mul: return "*";
    case BinaryOp::Div: return "/";
    case BinaryOp::Mod: return "%";
    case BinaryOp::Eq: return "==";
    case BinaryOp::NotEq: return "!=";
    case BinaryOp::Less: return "<";
    case BinaryOp::Greater: return ">";
    case BinaryOp::LessEq: return "<=";
    case BinaryOp::GreaterEq: return ">=";
    case BinaryOp::And: return "and";
    case BinaryOp::Or: return "or";
    case BinaryOp::BitAnd: return "&";
    case BinaryOp::BitOr: return "|";
    case BinaryOp::BitXor: return "^";
    case BinaryOp::Shl: return "<<";
    case BinaryOp::Shr: return ">>";
    }
    return "??";
}

/// Map compound-assign operators to Python syntax. Bitwise compound
/// operators use the same symbol as in C-family languages.
static std::string compoundOpStr(BinaryOp op) {
    switch (op) {
    case BinaryOp::Add: return "+=";
    case BinaryOp::Sub: return "-=";
    case BinaryOp::Mul: return "*=";
    case BinaryOp::Div: return "/=";
    case BinaryOp::Mod: return "%=";
    case BinaryOp::BitAnd: return "&=";
    case BinaryOp::BitOr: return "|=";
    case BinaryOp::BitXor: return "^=";
    case BinaryOp::Shl: return "<<=";
    case BinaryOp::Shr: return ">>=";
    default: return binaryOpStr(op) + "=";
    }
}

/// Map known C++/Rust/Java container type names to Python equivalents.
/// Returns empty string if the name is not a recognized container.
static std::string mapContainerName(const std::string& name) {
    if (name == "vector" || name == "Vec" || name == "List" || name == "ArrayList" || name == "LinkedList")
        return "list";
    if (name == "optional" || name == "Option" || name == "Optional") return "Optional";
    if (name == "unordered_map" || name == "map" || name == "HashMap" || name == "TreeMap" || name == "Map")
        return "dict";
    if (name == "unordered_set" || name == "set" || name == "HashSet" || name == "TreeSet" || name == "Set")
        return "set";
    if (name == "tuple" || name == "Tuple") return "tuple";
    return "";
}

/// Map known concrete type names directly to Python types.
static std::string mapPrimitiveType(const std::string& name) {
    // Integer types
    if (name == "int" || name == "int32_t" || name == "int64_t" || name == "int16_t" || name == "int8_t" ||
        name == "uint32_t" || name == "uint64_t" || name == "uint16_t" || name == "uint8_t" || name == "i32" ||
        name == "i64" || name == "i16" || name == "i8" || name == "u32" || name == "u64" || name == "u16" ||
        name == "u8" || name == "size_t" || name == "usize" || name == "isize" || name == "long" || name == "short")
        return "int";
    // Float types
    if (name == "double" || name == "float" || name == "f64" || name == "f32") return "float";
    // Boolean
    if (name == "bool" || name == "boolean") return "bool";
    // String
    if (name == "string" || name == "String" || name == "std::string" || name == "str") return "str";
    // Void
    if (name == "void" || name == "Void") return "None";
    return "";
}

static std::pair<std::string, std::string> splitQualifiedName(const std::string& qname) {
    auto pos = qname.rfind("::");
    if (pos == std::string::npos)
        return {"", qname};
    return {qname.substr(0, pos), qname.substr(pos + 2)};
}

static std::string capitalize(const std::string& s) {
    if (s.empty()) return s;
    std::string result = s;
    result[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(result[0])));
    return result;
}

PythonEmitter::PythonEmitter(TypeBinder binder) : binder_(std::move(binder)) {}

EmitResult PythonEmitter::emit(const TranspileModule& module) {
    EmitResult result;

    // Collect imports needed
    bool needsDataclass = !module.types.empty();
    bool needsOptional = false;

    // Quick scan for Optional usage. The first-batch stdlib `optional<T>` is
    // routed to PEP 604 `T | None` (see emitType), so it never needs the
    // `typing.Optional` import — skip stdlib optionals here.
    auto scanTypeForOptional = [](const TypeNode& t, auto& self) -> bool {
        if (t.isStdlib() && t.stdlibId == stdlib::TypeId::Optional) {
            // Recurse into inner T only.
            for (const auto& arg : t.templateArgs) {
                if (self(arg, self)) return true;
            }
            return false;
        }
        for (const auto& part : t.nameParts) {
            if (part == "optional" || part == "Option" || part == "Optional") return true;
        }
        for (const auto& arg : t.templateArgs) {
            if (self(arg, self)) return true;
        }
        return false;
    };

    for (const auto& t : module.types) {
        for (const auto& f : t.fields) {
            if (scanTypeForOptional(f.type, scanTypeForOptional)) needsOptional = true;
        }
    }
    for (const auto& f : module.functions) {
        if (scanTypeForOptional(f.returnType, scanTypeForOptional)) needsOptional = true;
        for (const auto& p : f.params) {
            if (scanTypeForOptional(p.type, scanTypeForOptional)) needsOptional = true;
        }
    }

    bool needsTyping = needsOptional;

    // `uuid` maps to the stdlib `uuid.UUID`, which needs `import uuid`.
    // Recurse through both templateArgs and recordFields so a uuid nested
    // in optional/slice/array/record/union still pulls the import.
    auto typeUsesUuid = [](const TypeNode& t, auto& self) -> bool {
        if (t.isStdlib() && t.stdlibId == stdlib::TypeId::Uuid) return true;
        for (const auto& arg : t.templateArgs) {
            if (self(arg, self)) return true;
        }
        for (const auto& f : t.recordFields) {
            if (self(f.type(), self)) return true;
        }
        return false;
    };
    bool needsUuid = false;
    for (const auto& t : module.types) {
        for (const auto& f : t.fields) {
            if (typeUsesUuid(f.type, typeUsesUuid)) needsUuid = true;
        }
    }
    for (const auto& f : module.functions) {
        if (typeUsesUuid(f.returnType, typeUsesUuid)) needsUuid = true;
        for (const auto& p : f.params) {
            if (typeUsesUuid(p.type, typeUsesUuid)) needsUuid = true;
        }
    }

    // Emit imports
    if (needsDataclass) result.code += "from dataclasses import dataclass\n";
    if (needsTyping) result.code += "from typing import Optional\n";
    if (needsUuid) result.code += "import uuid\n";
    if (needsDataclass || needsTyping || needsUuid) result.code += "\n";

    // Group types and functions by namespace
    struct NsGroup {
        std::vector<const TranspileType*> types;
        std::vector<const TranspileFunction*> functions;
    };
    std::map<std::string, NsGroup> groups;

    for (const auto& t : module.types) {
        auto [ns, _] = splitQualifiedName(t.qualifiedName);
        groups[ns].types.push_back(&t);
    }
    for (const auto& f : module.functions) {
        auto [ns, _] = splitQualifiedName(f.qualifiedName);
        groups[ns].functions.push_back(&f);
    }

    for (const auto& [ns, group] : groups) {
        if (!ns.empty()) {
            auto lastSep = ns.rfind("::");
            std::string className = capitalize(lastSep == std::string::npos ? ns : ns.substr(lastSep + 2));
            result.code += "class " + className + ":\n";
        }

        for (const auto* t : group.types) {
            if (!ns.empty())
                result.code += emitStruct(*t, 1) + "\n";
            else
                result.code += emitStruct(*t) + "\n";
        }
        for (const auto* f : group.functions) {
            if (!ns.empty()) {
                result.code += ind(1) + "@staticmethod\n";
                result.code += emitFunction(*f, 1) + "\n";
            } else {
                result.code += emitFunction(*f) + "\n";
            }
        }
    }

    return result;
}

std::string PythonEmitter::emitOwnership(const TypeNode& type) {
    // Python has no ownership semantics — all ownership kinds map to plain
    // type. Copy-and-mutate, not positional reconstruction: a positional
    // TypeNode{...} silently drops any field not listed (stdlibId,
    // recordFields), so `owned slice<T>` / `owned record<...>` would lose
    // their stdlib identity through the ownership path.
    TypeNode bare = type;
    bare.ownership = OwnershipKind::None;
    bare.modifier = TypeNode::None;
    return emitType(bare);
}

std::string PythonEmitter::emitType(const TypeNode& type) {
    if (type.ownership != OwnershipKind::None) return emitOwnership(type);

    // stdlib bridging types route through this branch BEFORE TypeBinder /
    // primitive / container fallbacks, so the explicit stdlib mapping wins
    // over the legacy name-based heuristics. The 6 first-batch types land
    // on Python builtins (no runtime helper needed):
    //   bool -> bool, i64 -> int, f64 -> float, string -> str,
    //   optional<T> -> "<T> | None" (PEP 604, Python >= 3.10),
    //   slice<T>    -> "list[<T>]" (see note below on non-owning semantics).
    // NOTE: `slice<T>` represents non-owning data over T at the ABI boundary
    // ({u32 len, T* ptr} at the ABI boundary). Python's value semantics
    // cannot preserve "non-owning" — `list[T]` is the simplest idiom that
    // round-trips correctly for first-batch scalar T. Future revisions may
    // emit `memoryview` (for byte-flavored T) or `numpy.ndarray` (numeric T)
    // when context warrants.
    if (type.isStdlib()) {
        switch (type.stdlibId) {
        case stdlib::TypeId::Bool:   return "bool";
        case stdlib::TypeId::I64:    return "int";
        case stdlib::TypeId::TimeNs: return "int"; // ns since epoch, i64-isomorphic
        case stdlib::TypeId::Uuid:   return "uuid.UUID"; // native stdlib UUID (needs `import uuid`)
        case stdlib::TypeId::Decimal128: return "bytes"; // 16-byte IEEE 754-2008 buffer (builtin bytes; no import, no codec)
        case stdlib::TypeId::F64:    return "float";
        case stdlib::TypeId::String: return "str";
        case stdlib::TypeId::Optional: {
            std::string inner = type.templateArgs.empty()
                ? "object"  // defensive; Sema rejects optional<> upstream
                : emitType(type.templateArgs[0]);
            return inner + " | None";
        }
        case stdlib::TypeId::Slice: {
            std::string inner = type.templateArgs.empty()
                ? "object"  // defensive; Sema rejects slice<> upstream
                : emitType(type.templateArgs[0]);
            // TODO: consider memoryview / numpy.ndarray for byte / numeric
            // T when non-owning semantics matter.
            return "list[" + inner + "]";
        }
        case stdlib::TypeId::Bytes: {
            // `bytes` is slice<u8>-isomorphic ({u32 len, u8* ptr} at the
            // ABI boundary). It carries no template args of its own — the
            // element type is fixed to u8. To stay byte-for-byte aligned
            // with how this emitter renders `slice<u8>`, synthesize that
            // exact node and delegate, so `bytes` and `slice<u8>` can
            // never drift apart here. The non-owning byte-view contract
            // lives in the .topo declaration, same as slice<T>.
            TypeNode u8;
            u8.nameParts = {stdlib::keywordOf(stdlib::TypeId::U8)};
            u8.stdlibId = stdlib::TypeId::U8;
            TypeNode sliceU8;
            sliceU8.nameParts = {stdlib::keywordOf(stdlib::TypeId::Slice)};
            sliceU8.stdlibId = stdlib::TypeId::Slice;
            sliceU8.templateArgs.push_back(std::move(u8));
            return emitType(sliceU8);
        }
        // Python has no fixed-width integer types; `int` is
        // arbitrary-precision and accepts the full unsigned/signed range. `float`
        // is C double; f32 round-trips through it without loss for ABI purposes.
        case stdlib::TypeId::U8:     return "int";
        case stdlib::TypeId::I32:    return "int";
        case stdlib::TypeId::U32:    return "int";
        case stdlib::TypeId::U64:    return "int";
        case stdlib::TypeId::F32:    return "float";
        case stdlib::TypeId::I8:     return "int";
        case stdlib::TypeId::I16:    return "int";
        case stdlib::TypeId::U16:    return "int";
        case stdlib::TypeId::Record: {
            // `record<a: T1, b: T2, ...>` has no inline named-field literal
            // in Python's type grammar (an anonymous TypedDict cannot be
            // written inline). The field order is load-bearing for the
            // cross-language byte layout, so the order-preserving idiom that
            // round-trips at the boundary is a positional tuple of the field
            // types — analogous to how slice<T> chose list[T] as the
            // simplest faithful idiom. Field names live in the .topo
            // declaration (the byte-layout contract), not in the Python
            // annotation.
            if (type.recordFields.empty()) return "tuple[()]";  // Sema rejects this upstream
            std::string out = "tuple[";
            for (size_t i = 0; i < type.recordFields.size(); ++i) {
                if (i > 0) out += ", ";
                out += emitType(type.recordFields[i].type());
            }
            out += "]";
            return out;
        }
        case stdlib::TypeId::Union: {
            // `union<tag: TagT, v1: T1, ...>` has the same lack of an inline
            // named-aggregate literal in Python's type grammar as record, so
            // it uses the same order-preserving idiom: a positional tuple of
            // (tag, variant...) types. Field names and the variant-overlap
            // semantics (only one variant occupies the shared storage at a
            // time, selected by the tag) live in the .topo declaration — the
            // byte-layout contract — not in the Python annotation, which
            // necessarily widens to "tag plus every possible variant type".
            if (type.recordFields.empty()) return "tuple[()]";  // Sema rejects upstream
            std::string out = "tuple[";
            for (size_t i = 0; i < type.recordFields.size(); ++i) {
                if (i > 0) out += ", ";
                out += emitType(type.recordFields[i].type());
            }
            out += "]";
            return out;
        }
        case stdlib::TypeId::Array: {
            // `array<T, N>` is a fixed-length inline buffer: N contiguous T
            // with no header. Python has no fixed-size inline array type;
            // the order-and-element-preserving idiom that round-trips at
            // the boundary is `list[T]` (mirroring how slice<T> chose
            // list[T]). The fixed length N is a load-bearing part of the
            // cross-language byte-layout contract
            // (size = N * stride(T) at align(T)); like slice<T>'s
            // non-owning semantics and record<...>'s field names, it is
            // not expressible in the Python annotation and lives in the
            // .topo declaration where the caller computes the layout.
            // templateArgs[0] = element type T (recurse, like slice);
            // templateArgs[1].nonTypeValue = the integer N.
            std::string inner = type.templateArgs.empty()
                ? "object"  // defensive; Sema rejects array<> upstream
                : emitType(type.templateArgs[0]);
            return "list[" + inner + "]";
        }
        case stdlib::TypeId::None:
            break;  // fall through to legacy paths
        }
    }

    // `union<A, B, ...>` carried positionally in templateArgs (the form a
    // Python `TypeVar('T', int, str)` constraint tuple lowers to) renders
    // as a PEP 604 union `A | B | ...`. This is the untagged member-choice
    // sense — `T` is exactly one of the listed types — distinct from the
    // stdlib *tagged* `union<tag: …, v1: …>` whose discriminant + named
    // variant fields ride `recordFields` (handled by the `stdlibId` branch
    // above when present). A wire-loaded node has `stdlibId == None`, so it
    // reaches here; matching on `nameParts` keeps the two senses apart.
    if (type.nameParts.size() == 1 && type.nameParts[0] == "union" &&
        !type.templateArgs.empty()) {
        std::string out;
        for (size_t i = 0; i < type.templateArgs.size(); ++i) {
            if (i > 0) out += " | ";
            out += emitType(type.templateArgs[i]);
        }
        return out;
    }

    // Try TypeBinder resolution for single-part abstract names
    if (type.nameParts.size() == 1) {
        auto resolved = binder_.resolve(type.nameParts[0]);
        if (resolved) return *resolved;
    }

    // Single-part name: check for direct primitive mapping
    if (type.nameParts.size() == 1) {
        auto prim = mapPrimitiveType(type.nameParts[0]);
        if (!prim.empty()) {
            if (!type.templateArgs.empty()) {
                // e.g. something unexpected — emit as-is with subscript
                prim += "[";
                for (size_t i = 0; i < type.templateArgs.size(); ++i) {
                    if (i > 0) prim += ", ";
                    prim += emitType(type.templateArgs[i]);
                }
                prim += "]";
            }
            return prim;
        }
    }

    // Check for container type mapping (single-part name with template args)
    if (type.nameParts.size() == 1) {
        auto container = mapContainerName(type.nameParts[0]);
        if (!container.empty()) {
            if (!type.templateArgs.empty()) {
                container += "[";
                for (size_t i = 0; i < type.templateArgs.size(); ++i) {
                    if (i > 0) container += ", ";
                    container += emitType(type.templateArgs[i]);
                }
                container += "]";
            }
            return container;
        }
    }

    // Qualified names: check last part for container/primitive
    // e.g. std::vector<T> → list[T]
    if (type.nameParts.size() > 1) {
        const auto& lastName = type.nameParts.back();
        auto container = mapContainerName(lastName);
        if (!container.empty()) {
            if (!type.templateArgs.empty()) {
                container += "[";
                for (size_t i = 0; i < type.templateArgs.size(); ++i) {
                    if (i > 0) container += ", ";
                    container += emitType(type.templateArgs[i]);
                }
                container += "]";
            }
            return container;
        }

        auto prim = mapPrimitiveType(lastName);
        if (!prim.empty()) return prim;
    }

    // Fallback: emit as dot-separated name (Python module path)
    std::string result;
    for (size_t i = 0; i < type.nameParts.size(); ++i) {
        if (i > 0) result += ".";
        result += type.nameParts[i];
    }

    if (!type.templateArgs.empty()) {
        result += "[";
        for (size_t i = 0; i < type.templateArgs.size(); ++i) {
            if (i > 0) result += ", ";
            result += emitType(type.templateArgs[i]);
        }
        result += "]";
    }

    // Python ignores Ref/Ptr modifiers and const
    return result;
}

std::string PythonEmitter::emitExpr(const Expr& expr) {
    switch (expr.kind()) {
    case Expr::Kind::BinaryOp: {
        const auto& e = static_cast<const BinaryOpExpr&>(expr);
        return "(" + emitExpr(*e.lhs) + " " + binaryOpStr(e.op) + " " + emitExpr(*e.rhs) + ")";
    }
    case Expr::Kind::UnaryOp: {
        const auto& e = static_cast<const UnaryOpExpr&>(expr);
        std::string op;
        switch (e.op) {
        case UnaryOp::Negate: op = "-"; break;
        case UnaryOp::Not: op = "not "; break;
        case UnaryOp::BitNot: op = "~"; break;
        case UnaryOp::PreIncrement:
            return "(" + emitExpr(*e.operand) + " := " + emitExpr(*e.operand) + " + 1)";
        case UnaryOp::PostIncrement:
            return "(" + emitExpr(*e.operand) + " + 1)  # TOPO-TRANSPILE: post-increment approximated";
        case UnaryOp::PreDecrement:
            return "(" + emitExpr(*e.operand) + " := " + emitExpr(*e.operand) + " - 1)";
        case UnaryOp::PostDecrement:
            return "(" + emitExpr(*e.operand) + " - 1)  # TOPO-TRANSPILE: post-decrement approximated";
        }
        return op + emitExpr(*e.operand);
    }
    case Expr::Kind::Call: {
        const auto& e = static_cast<const CallExpr&>(expr);
        std::string result = e.callee + "(";
        for (size_t i = 0; i < e.args.size(); ++i) {
            if (i > 0) result += ", ";
            result += emitExpr(*e.args[i]);
        }
        result += ")";
        return result;
    }
    case Expr::Kind::MemberAccess: {
        const auto& e = static_cast<const MemberAccessExpr&>(expr);
        return emitExpr(*e.object) + "." + e.member;
    }
    case Expr::Kind::Index: {
        const auto& e = static_cast<const IndexExpr&>(expr);
        return emitExpr(*e.object) + "[" + emitExpr(*e.index) + "]";
    }
    case Expr::Kind::Literal: {
        const auto& e = static_cast<const LiteralExpr&>(expr);
        if (e.litKind == LiteralKind::String) return "\"" + e.value + "\"";
        if (e.litKind == LiteralKind::Boolean) return (e.value == "true") ? "True" : "False";
        return e.value;
    }
    case Expr::Kind::VarRef: {
        const auto& e = static_cast<const VarRefExpr&>(expr);
        // Map null/nullptr to None
        if (e.name == "null" || e.name == "nullptr") return "None";
        if (e.name == "true") return "True";
        if (e.name == "false") return "False";
        return e.name;
    }
    case Expr::Kind::Construct: {
        const auto& e = static_cast<const ConstructExpr&>(expr);
        std::string result = emitType(e.type) + "(";
        for (size_t i = 0; i < e.args.size(); ++i) {
            if (i > 0) result += ", ";
            result += emitExpr(*e.args[i]);
        }
        result += ")";
        return result;
    }
    case Expr::Kind::Lambda: {
        const auto& e = static_cast<const LambdaExpr&>(expr);
        // Single-expression lambdas → lambda; multi-statement → nested def
        if (e.body.size() == 1 && e.body[0]->kind() == Stmt::Kind::Return) {
            const auto& ret = static_cast<const ReturnStmt&>(*e.body[0]);
            if (ret.value) {
                std::string result = "lambda ";
                for (size_t i = 0; i < e.params.size(); ++i) {
                    if (i > 0) result += ", ";
                    result += e.params[i].name;
                }
                result += ": " + emitExpr(*ret.value);
                return result;
            }
        }
        // Multi-statement: emit as inline comment placeholder
        // (Python has no multi-statement lambda expression)
        std::string result = "lambda ";
        for (size_t i = 0; i < e.params.size(); ++i) {
            if (i > 0) result += ", ";
            result += e.params[i].name;
        }
        result += ": ...  # TOPO-TRANSPILE: multi-statement lambda requires named function";
        return result;
    }
    case Expr::Kind::Throw: {
        const auto& e = static_cast<const ThrowExpr&>(expr);
        return "raise " + emitExpr(*e.operand);
    }
    case Expr::Kind::Unsupported: {
        const auto& e = static_cast<const UnsupportedExpr&>(expr);
        return "...  # TOPO-TRANSPILE: unsupported -- " + e.description;
    }
    case Expr::Kind::Ternary: {
        const auto& e = static_cast<const TernaryExpr&>(expr);
        return "(" + emitExpr(*e.trueExpr) + " if " + emitExpr(*e.condition) + " else " + emitExpr(*e.falseExpr) + ")";
    }
    case Expr::Kind::CompoundAssign: {
        const auto& e = static_cast<const CompoundAssignExpr&>(expr);
        return emitExpr(*e.target) + " " + compoundOpStr(e.op) + " " + emitExpr(*e.value);
    }
    }
    return "...  # TOPO-TRANSPILE: unsupported -- unknown expression";
}

std::string PythonEmitter::emitStmt(const Stmt& stmt, int level) {
    std::string prefix = fidelityComment(stmt.fidelity, level);

    switch (stmt.kind()) {
    case Stmt::Kind::VarDecl: {
        const auto& s = static_cast<const VarDeclStmt&>(stmt);
        std::string result = prefix + ind(level);
        if (s.init) {
            // Emit typed assignment: name: Type = expr
            if (!s.type.nameParts.empty())
                result += s.name + ": " + emitType(s.type) + " = " + emitExpr(*s.init);
            else
                result += s.name + " = " + emitExpr(*s.init);
        } else {
            // Declaration without init: name: Type
            if (!s.type.nameParts.empty())
                result += s.name + ": " + emitType(s.type);
            else
                result += s.name + " = None";
        }
        result += "\n";
        return result;
    }
    case Stmt::Kind::Assign: {
        const auto& s = static_cast<const AssignStmt&>(stmt);
        return prefix + ind(level) + emitExpr(*s.target) + " = " + emitExpr(*s.value) + "\n";
    }
    case Stmt::Kind::Return: {
        const auto& s = static_cast<const ReturnStmt&>(stmt);
        if (s.value) return prefix + ind(level) + "return " + emitExpr(*s.value) + "\n";
        return prefix + ind(level) + "return\n";
    }
    case Stmt::Kind::If: {
        const auto& s = static_cast<const IfStmt&>(stmt);
        std::string result = prefix + ind(level) + "if " + emitExpr(*s.condition) + ":\n";
        if (s.thenBody.empty()) {
            result += ind(level + 1) + "pass\n";
        } else {
            for (const auto& st : s.thenBody)
                result += emitStmt(*st, level + 1);
        }
        if (!s.elseBody.empty()) {
            // Check if the else body is a single if-statement (elif chain)
            if (s.elseBody.size() == 1 && s.elseBody[0]->kind() == Stmt::Kind::If) {
                const auto& elseIf = static_cast<const IfStmt&>(*s.elseBody[0]);
                result += ind(level) + "elif " + emitExpr(*elseIf.condition) + ":\n";
                if (elseIf.thenBody.empty()) {
                    result += ind(level + 1) + "pass\n";
                } else {
                    for (const auto& st : elseIf.thenBody)
                        result += emitStmt(*st, level + 1);
                }
                // Recursively handle further elif/else chains
                if (!elseIf.elseBody.empty()) {
                    if (elseIf.elseBody.size() == 1 && elseIf.elseBody[0]->kind() == Stmt::Kind::If) {
                        // Build a temporary IfStmt wrapper to reuse emitStmt
                        // Instead, emit the remaining else body inline
                        // For simplicity, emit as else + nested if
                        result += ind(level) + "else:\n";
                        for (const auto& st : elseIf.elseBody)
                            result += emitStmt(*st, level + 1);
                    } else {
                        result += ind(level) + "else:\n";
                        for (const auto& st : elseIf.elseBody)
                            result += emitStmt(*st, level + 1);
                    }
                }
            } else {
                result += ind(level) + "else:\n";
                for (const auto& st : s.elseBody)
                    result += emitStmt(*st, level + 1);
            }
        }
        return result;
    }
    case Stmt::Kind::For: {
        const auto& s = static_cast<const ForStmt&>(stmt);
        // Attempt to detect simple range-based for: init is VarDecl with 0,
        // condition is < N, increment is ++
        // Fallback: emit as while loop with init
        std::string result = prefix;

        // Try to emit as "for i in range(...):"
        bool emittedForRange = false;
        if (s.init && s.init->kind() == Stmt::Kind::VarDecl && s.condition && s.increment) {
            const auto& initDecl = static_cast<const VarDeclStmt&>(*s.init);
            if (initDecl.init && s.condition->kind() == Expr::Kind::BinaryOp) {
                const auto& cond = static_cast<const BinaryOpExpr&>(*s.condition);
                if (cond.op == BinaryOp::Less && cond.lhs->kind() == Expr::Kind::VarRef) {
                    const auto& condVar = static_cast<const VarRefExpr&>(*cond.lhs);
                    if (condVar.name == initDecl.name) {
                        std::string start = emitExpr(*initDecl.init);
                        std::string end = emitExpr(*cond.rhs);
                        if (start == "0")
                            result += ind(level) + "for " + initDecl.name + " in range(" + end + "):\n";
                        else
                            result += ind(level) + "for " + initDecl.name + " in range(" + start + ", " + end + "):\n";
                        emittedForRange = true;
                    }
                }
            }
        }

        if (!emittedForRange) {
            // Fallback: emit init + while loop
            if (s.init) result += emitStmt(*s.init, level);
            std::string cond = s.condition ? emitExpr(*s.condition) : "True";
            result += ind(level) + "while " + cond + ":\n";
            for (const auto& st : s.body)
                result += emitStmt(*st, level + 1);
            if (s.increment) result += ind(level + 1) + emitExpr(*s.increment) + "\n";
            return result;
        }

        if (s.body.empty()) {
            result += ind(level + 1) + "pass\n";
        } else {
            for (const auto& st : s.body)
                result += emitStmt(*st, level + 1);
        }
        return result;
    }
    case Stmt::Kind::While: {
        const auto& s = static_cast<const WhileStmt&>(stmt);
        std::string result = prefix + ind(level) + "while " + emitExpr(*s.condition) + ":\n";
        if (s.body.empty()) {
            result += ind(level + 1) + "pass\n";
        } else {
            for (const auto& st : s.body)
                result += emitStmt(*st, level + 1);
        }
        return result;
    }
    case Stmt::Kind::ExprStmt: {
        const auto& s = static_cast<const ExprStmt&>(stmt);
        return prefix + ind(level) + emitExpr(*s.expr) + "\n";
    }
    case Stmt::Kind::TryCatch: {
        const auto& s = static_cast<const TryCatchStmt&>(stmt);
        std::string result = prefix + ind(level) + "try:\n";
        if (s.tryBody.empty()) {
            result += ind(level + 1) + "pass\n";
        } else {
            for (const auto& st : s.tryBody)
                result += emitStmt(*st, level + 1);
        }
        for (const auto& c : s.catchClauses) {
            result += ind(level) + "except " + emitType(c.exceptionType);
            if (!c.varName.empty()) result += " as " + c.varName;
            result += ":\n";
            if (c.body.empty()) {
                result += ind(level + 1) + "pass\n";
            } else {
                for (const auto& st : c.body)
                    result += emitStmt(*st, level + 1);
            }
        }
        if (!s.finallyBody.empty()) {
            result += ind(level) + "finally:\n";
            for (const auto& st : s.finallyBody)
                result += emitStmt(*st, level + 1);
        }
        return result;
    }
    case Stmt::Kind::Break: return prefix + ind(level) + "break\n";
    case Stmt::Kind::Continue: return prefix + ind(level) + "continue\n";
    case Stmt::Kind::Switch: {
        const auto& s = static_cast<const SwitchStmt&>(stmt);
        // Python 3.10+ match/case
        std::string result = prefix + ind(level) + "match " + emitExpr(*s.subject) + ":\n";
        for (const auto& c : s.cases) {
            if (c.value)
                result += ind(level + 1) + "case " + emitExpr(*c.value) + ":\n";
            else
                result += ind(level + 1) + "case _:\n";
            if (c.body.empty()) {
                result += ind(level + 2) + "pass\n";
            } else {
                for (const auto& st : c.body)
                    result += emitStmt(*st, level + 2);
            }
        }
        return result;
    }
    }
    return prefix + ind(level) + "pass  # TOPO-TRANSPILE: unsupported -- unknown statement\n";
}

std::string PythonEmitter::emitFunction(const TranspileFunction& func, int baseIndent) {
    std::string result;
    result += fidelityComment(func.fidelity, baseIndent);

    for (const auto& u : func.unsupported)
        result += ind(baseIndent) + "# TOPO-TRANSPILE: unsupported -- " + u + "\n";

    auto [_, simpleName] = splitQualifiedName(func.qualifiedName);
    std::string assocNotes;
    const std::string pep695Frag =
        pep695ParamsImpl(func.templateParams,
                         [this](const TypeNode& t) { return emitType(t); },
                         &assocNotes);
    if (!assocNotes.empty()) {
        // Prepend each leading note line as a properly-indented `#` line
        // immediately before the `def` so the decl reads cleanly.
        std::string indented;
        size_t start = 0;
        while (start < assocNotes.size()) {
            size_t nl = assocNotes.find('\n', start);
            if (nl == std::string::npos) nl = assocNotes.size();
            indented += ind(baseIndent) + assocNotes.substr(start, nl - start) + "\n";
            start = nl + 1;
        }
        result += indented;
    }
    result += ind(baseIndent) + "def " + simpleName + pep695Frag + "(";
    for (size_t i = 0; i < func.params.size(); ++i) {
        if (i > 0) result += ", ";
        result += func.params[i].name;
        if (!func.params[i].type.nameParts.empty()) result += ": " + emitType(func.params[i].type);
    }
    result += ")";

    // Return type annotation
    if (!func.returnType.nameParts.empty()) {
        std::string retType = emitType(func.returnType);
        result += " -> " + retType;
    }

    result += ":\n";

    if (func.body.empty()) {
        result += ind(baseIndent + 1) + "pass\n";
    } else {
        for (const auto& s : func.body)
            result += emitStmt(*s, baseIndent + 1);
    }

    return result;
}

std::string PythonEmitter::emitStruct(const TranspileType& type, int baseIndent) {
    std::string result;
    result += fidelityComment(type.fidelity, baseIndent);
    auto [_, simpleName] = splitQualifiedName(type.qualifiedName);
    std::string structAssocNotes;
    const std::string pep695StructFrag =
        pep695ParamsImpl(type.templateParams,
                         [this](const TypeNode& t) { return emitType(t); },
                         &structAssocNotes);
    if (!structAssocNotes.empty()) {
        std::string indented;
        size_t start = 0;
        while (start < structAssocNotes.size()) {
            size_t nl = structAssocNotes.find('\n', start);
            if (nl == std::string::npos) nl = structAssocNotes.size();
            indented += ind(baseIndent) + structAssocNotes.substr(start, nl - start) + "\n";
            start = nl + 1;
        }
        result += indented;
    }
    result += ind(baseIndent) + "@dataclass\n";
    result += ind(baseIndent) + "class " + simpleName + pep695StructFrag;

    // Inheritance hierarchy, source order. Python has no class/interface
    // distinction, so baseClassKinds is irrelevant — all bases become base
    // classes. Empty baseClasses ⇒ `class S:`, byte-identical to
    // pre-inheritance emission.
    if (!type.baseClasses.empty()) {
        result += "(";
        for (size_t i = 0; i < type.baseClasses.size(); ++i) {
            if (i > 0) result += ", ";
            result += emitType(type.baseClasses[i]);
        }
        result += ")";
    }
    result += ":\n";

    if (type.fields.empty()) {
        result += ind(baseIndent + 1) + "pass\n";
    } else {
        for (const auto& f : type.fields) {
            result += fidelityComment(f.fidelity, baseIndent + 1);
            result += ind(baseIndent + 1) + f.name + ": " + emitType(f.type) + "\n";
        }
    }

    return result;
}

} // namespace topo::transpile
