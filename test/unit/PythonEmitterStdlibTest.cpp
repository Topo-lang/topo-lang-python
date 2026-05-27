// PythonEmitter stdlib bridging-type tests.
//
// Verifies that the 6 first-batch stdlib types (bool / i64 / f64 / string /
// optional<T> / slice<T>) emit the Python idioms:
//
//   bool        -> bool
//   i64         -> int
//   f64         -> float
//   string      -> str
//   optional<T> -> "<T> | None"   (PEP 604; Python >= 3.10)
//   slice<T>    -> "list[<T>]"
//
// These are unit-level checks on the emitter's `emitType` output via the
// public `emit()` entry point. Cross-language equivalence is intentionally
// out of scope for this PR — each per-language emitter lands in its own
// PR, so the equivalence fixture covering all 5 emitters with
// stdlib types is deferred until at least the C++ batch (2.3).

#include "PythonEmitter.h"
#include "topo/Stdlib/Types.h"
#include "topo/Transpile/TranspileModel.h"
#include <gtest/gtest.h>
#include <string>

using namespace topo;
using namespace topo::transpile;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build a TypeNode for a Batch-1 stdlib type. Single-arg constructor for
/// the 4 scalar types; overload below for parameterized optional / slice.
static TypeNode stdlibScalar(stdlib::TypeId id) {
    TypeNode t;
    t.nameParts = {stdlib::keywordOf(id)};
    t.stdlibId = id;
    return t;
}

static TypeNode stdlibParametric(stdlib::TypeId id, TypeNode inner) {
    TypeNode t;
    t.nameParts = {stdlib::keywordOf(id)};
    t.stdlibId = id;
    t.templateArgs.push_back(std::move(inner));
    return t;
}

/// Build a minimal module with a single function `boundary(p0: T) -> T`,
/// emit, and return the generated Python source. Stripping the leading
/// `from dataclasses import dataclass\n` / typing imports is left to the
/// caller via substring search — we want to assert on the type annotation
/// the emitter produced.
static std::string emitWithParamAndReturn(TypeNode paramType, TypeNode returnType) {
    TranspileModule mod;
    TranspileFunction fn;
    fn.qualifiedName = "boundary";
    fn.returnType = std::move(returnType);
    Parameter p;
    p.type = std::move(paramType);
    p.name = "value";
    fn.params.push_back(std::move(p));

    // Trivial body so the emitter has something to emit. `pass` is fine.
    mod.functions.push_back(std::move(fn));

    PythonEmitter emitter;
    return emitter.emit(mod).code;
}

// ---------------------------------------------------------------------------
// Scalar stdlib types
// ---------------------------------------------------------------------------

TEST(PythonEmitterStdlib, BoolMapsToPyBool) {
    std::string code = emitWithParamAndReturn(stdlibScalar(stdlib::TypeId::Bool),
                                              stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(code.find("def boundary(value: bool) -> bool:"), std::string::npos)
        << "Generated code:\n" << code;
}

TEST(PythonEmitterStdlib, I64MapsToPyInt) {
    std::string code = emitWithParamAndReturn(stdlibScalar(stdlib::TypeId::I64),
                                              stdlibScalar(stdlib::TypeId::I64));
    EXPECT_NE(code.find("def boundary(value: int) -> int:"), std::string::npos)
        << "Generated code:\n" << code;
}

TEST(PythonEmitterStdlib, F64MapsToPyFloat) {
    std::string code = emitWithParamAndReturn(stdlibScalar(stdlib::TypeId::F64),
                                              stdlibScalar(stdlib::TypeId::F64));
    EXPECT_NE(code.find("def boundary(value: float) -> float:"), std::string::npos)
        << "Generated code:\n" << code;
}

TEST(PythonEmitterStdlib, StringMapsToPyStr) {
    std::string code = emitWithParamAndReturn(stdlibScalar(stdlib::TypeId::String),
                                              stdlibScalar(stdlib::TypeId::String));
    EXPECT_NE(code.find("def boundary(value: str) -> str:"), std::string::npos)
        << "Generated code:\n" << code;
}

// ---------------------------------------------------------------------------
// Parameterized stdlib types — outer mapping
// ---------------------------------------------------------------------------

TEST(PythonEmitterStdlib, OptionalEmitsPep604Union) {
    // optional<i64> -> int | None
    auto inner = stdlibScalar(stdlib::TypeId::I64);
    auto opt = stdlibParametric(stdlib::TypeId::Optional, inner);
    std::string code = emitWithParamAndReturn(opt, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(code.find("value: int | None"), std::string::npos)
        << "Generated code:\n" << code;
}

TEST(PythonEmitterStdlib, OptionalSkipsTypingImport) {
    // Stdlib-routed optional must NOT pull in `from typing import Optional`
    // because the emitter produces `T | None` (PEP 604).
    auto inner = stdlibScalar(stdlib::TypeId::String);
    auto opt = stdlibParametric(stdlib::TypeId::Optional, inner);
    std::string code = emitWithParamAndReturn(opt, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_EQ(code.find("from typing import Optional"), std::string::npos)
        << "stdlib optional pulled in unused typing.Optional import:\n" << code;
}

TEST(PythonEmitterStdlib, SliceEmitsList) {
    // slice<f64> -> list[float]
    auto inner = stdlibScalar(stdlib::TypeId::F64);
    auto sl = stdlibParametric(stdlib::TypeId::Slice, inner);
    std::string code = emitWithParamAndReturn(sl, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(code.find("value: list[float]"), std::string::npos)
        << "Generated code:\n" << code;
}

// ---------------------------------------------------------------------------
// Nested parameterized: optional<slice<i64>>
// ---------------------------------------------------------------------------

TEST(PythonEmitterStdlib, NestedOptionalOfSlice) {
    // optional<slice<i64>> -> list[int] | None
    auto i64 = stdlibScalar(stdlib::TypeId::I64);
    auto sl = stdlibParametric(stdlib::TypeId::Slice, std::move(i64));
    auto opt = stdlibParametric(stdlib::TypeId::Optional, std::move(sl));
    std::string code = emitWithParamAndReturn(opt, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(code.find("value: list[int] | None"), std::string::npos)
        << "Generated code:\n" << code;
}

// ---------------------------------------------------------------------------
// Combined signature — covers all 6 first-batch types in one fn.
// Verification signature: `boundary(id: i64, name: string,
// flags: optional<bool>, values: slice<f64>) -> optional<i64>`.
// ---------------------------------------------------------------------------

TEST(PythonEmitterStdlib, AllSixTypesInOneSignature) {
    TranspileModule mod;
    TranspileFunction fn;
    fn.qualifiedName = "boundary";
    fn.returnType = stdlibParametric(stdlib::TypeId::Optional,
                                     stdlibScalar(stdlib::TypeId::I64));

    auto addParam = [&](const std::string& name, TypeNode ty) {
        Parameter p;
        p.name = name;
        p.type = std::move(ty);
        fn.params.push_back(std::move(p));
    };
    addParam("id", stdlibScalar(stdlib::TypeId::I64));
    addParam("name", stdlibScalar(stdlib::TypeId::String));
    addParam("flags", stdlibParametric(stdlib::TypeId::Optional,
                                       stdlibScalar(stdlib::TypeId::Bool)));
    addParam("values", stdlibParametric(stdlib::TypeId::Slice,
                                        stdlibScalar(stdlib::TypeId::F64)));

    mod.functions.push_back(std::move(fn));

    PythonEmitter emitter;
    std::string code = emitter.emit(mod).code;

    EXPECT_NE(code.find("def boundary(id: int, name: str, "
                        "flags: bool | None, values: list[float])"
                        " -> int | None:"),
              std::string::npos)
        << "Generated code:\n" << code;
}

// ---------------------------------------------------------------------------
// record<...> composite — annotation idiom + byte layout single-source check
// ---------------------------------------------------------------------------

static TypeNode stdlibRecord(std::vector<std::pair<std::string, TypeNode>> fields) {
    TypeNode t;
    t.nameParts = {stdlib::keywordOf(stdlib::TypeId::Record)};
    t.stdlibId = stdlib::TypeId::Record;
    for (auto& [name, ty] : fields) {
        TypeNode::RecordField f;
        f.name = name;
        f.typeBox.push_back(std::move(ty));
        t.recordFields.push_back(std::move(f));
    }
    return t;
}

TEST(PythonEmitterStdlib, RecordEmitsPositionalTuple) {
    // record<id: i64, price: f64> -> tuple[int, float]
    auto rec = stdlibRecord({{"id", stdlibScalar(stdlib::TypeId::I64)},
                             {"price", stdlibScalar(stdlib::TypeId::F64)}});
    std::string code = emitWithParamAndReturn(rec, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(code.find("value: tuple[int, float]"), std::string::npos)
        << "Generated code:\n" << code;
}

TEST(PythonEmitterStdlib, RecordNestedComposite) {
    // record<key: string, items: slice<i64>> -> tuple[str, list[int]]
    auto rec = stdlibRecord(
        {{"key", stdlibScalar(stdlib::TypeId::String)},
         {"items", stdlibParametric(stdlib::TypeId::Slice,
                                    stdlibScalar(stdlib::TypeId::I64))}});
    std::string code = emitWithParamAndReturn(rec, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(code.find("value: tuple[str, list[int]]"), std::string::npos)
        << "Generated code:\n" << code;
}

// ---------------------------------------------------------------------------
// bytes — slice<u8>-isomorphic. Must emit EXACTLY what slice<u8> emits.
// ---------------------------------------------------------------------------

TEST(PythonEmitterStdlib, BytesEmitsSameAsSliceOfU8) {
    // bytes -> list[int]   (identical to slice<u8>)
    auto bytesTy = stdlibScalar(stdlib::TypeId::Bytes);
    std::string bytesCode =
        emitWithParamAndReturn(bytesTy, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(bytesCode.find("value: list[int]"), std::string::npos)
        << "Generated code:\n" << bytesCode;

    // Cross-check: slice<u8> produces the very same annotation, so `bytes`
    // and `slice<u8>` are byte-for-byte aligned at this boundary.
    auto sliceU8 = stdlibParametric(stdlib::TypeId::Slice,
                                    stdlibScalar(stdlib::TypeId::U8));
    std::string sliceCode =
        emitWithParamAndReturn(sliceU8, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(sliceCode.find("value: list[int]"), std::string::npos)
        << "Generated code:\n" << sliceCode;
}

TEST(PythonEmitterStdlib, BytesSkipsTypingImport) {
    // bytes routes to list[int]; it must not pull in typing imports.
    auto bytesTy = stdlibScalar(stdlib::TypeId::Bytes);
    std::string code =
        emitWithParamAndReturn(bytesTy, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_EQ(code.find("from typing import"), std::string::npos)
        << "bytes pulled in an unused typing import:\n" << code;
}

// ---------------------------------------------------------------------------
// array<T, N> — fixed-length inline buffer. Python has no inline array;
// element type round-trips as list[T], the fixed length N is part of the
// .topo byte-layout contract (not expressible in the annotation).
// ---------------------------------------------------------------------------

/// Build array<T, N>: templateArgs[0]=T, templateArgs[1].nonTypeValue=N.
static TypeNode stdlibArray(TypeNode elem, int n) {
    TypeNode t;
    t.nameParts = {stdlib::keywordOf(stdlib::TypeId::Array)};
    t.stdlibId = stdlib::TypeId::Array;
    t.templateArgs.push_back(std::move(elem));
    TypeNode countArg;
    countArg.nonTypeValue = n;
    t.templateArgs.push_back(std::move(countArg));
    return t;
}

TEST(PythonEmitterStdlib, ArrayOfScalarEmitsList) {
    // array<i64, 4> -> list[int]
    auto arr = stdlibArray(stdlibScalar(stdlib::TypeId::I64), 4);
    std::string code =
        emitWithParamAndReturn(arr, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(code.find("value: list[int]"), std::string::npos)
        << "Generated code:\n" << code;
}

TEST(PythonEmitterStdlib, ArrayOfRecordNestedComposite) {
    // array<record<a: i64>, 2> -> list[tuple[int]]
    auto rec = stdlibRecord({{"a", stdlibScalar(stdlib::TypeId::I64)}});
    auto arr = stdlibArray(std::move(rec), 2);
    std::string code =
        emitWithParamAndReturn(arr, stdlibScalar(stdlib::TypeId::Bool));
    EXPECT_NE(code.find("value: list[tuple[int]]"), std::string::npos)
        << "Generated code:\n" << code;
}

// The Python host derives its record byte layout from the SAME single
// source (composeRecordLayout over each field's layoutOf), so the layout it
// would marshal is identical to the hand calculation in the table design.
TEST(PythonEmitterStdlib, RecordByteLayoutMatchesSingleSource) {
    using namespace stdlib;
    // record<flag: u8, n: i64>:
    //   flag@0 size1; n align8 -> @8 size8 -> 16; align8 -> total 16.
    std::vector<AbiLayout> fields = {layoutOf(TypeId::U8), layoutOf(TypeId::I64)};
    AbiLayout L = composeRecordLayout(fields);
    EXPECT_EQ(L.size, 16u);
    EXPECT_EQ(L.align, 8u);

    // Explicit padding field is just a regular field:
    // record<flag: u8, pad,pad2,pad3: u8, n: u32>:
    //   4x u8 @0..3; n align4 -> @4 size4 -> 8; align4 -> total 8.
    std::vector<AbiLayout> padded = {layoutOf(TypeId::U8), layoutOf(TypeId::U8),
                                     layoutOf(TypeId::U8), layoutOf(TypeId::U8),
                                     layoutOf(TypeId::U32)};
    AbiLayout P = composeRecordLayout(padded);
    EXPECT_EQ(P.size, 8u);
    EXPECT_EQ(P.align, 4u);
}

// ---------------------------------------------------------------------------
// TranspileType.baseClasses -> Python `class S(Base1, Base2):`. Python has
// no class/interface split, so baseClassKinds is ignored — all bases
// become positional base classes.
// ---------------------------------------------------------------------------

static std::string emitPyClassWithBases(const std::string& qname, std::vector<TypeNode> bases) {
    TranspileModule mod;
    TranspileType ty;
    ty.qualifiedName = qname;
    ty.baseClasses = std::move(bases);
    mod.types.push_back(std::move(ty));
    PythonEmitter emitter;
    return emitter.emit(mod).code;
}

static TypeNode pyNamed(const std::string& name) {
    TypeNode t;
    t.nameParts = {name};
    return t;
}

TEST(PythonEmitterStdlib, ClassSingleBase) {
    std::string code = emitPyClassWithBases("Dog", {pyNamed("Animal")});
    EXPECT_NE(code.find("class Dog(Animal):"), std::string::npos) << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, ClassMultipleBases) {
    std::string code = emitPyClassWithBases("Service", {pyNamed("Base"), pyNamed("Mixin")});
    EXPECT_NE(code.find("class Service(Base, Mixin):"), std::string::npos) << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, ClassEmptyBasesByteIdenticalToPreInheritance) {
    // Pre-inheritance emission was exactly `class <name>:`.
    std::string code = emitPyClassWithBases("Plain", {});
    EXPECT_NE(code.find("class Plain:"), std::string::npos) << "Generated:\n" << code;
    EXPECT_EQ(code.find("class Plain("), std::string::npos) << "no base-list expected:\n" << code;
}

// ---------------------------------------------------------------------------
// Declaration-level generics: TranspileType/TranspileFunction.templateParams
// -> PEP 695 (`class Box[T]:` / `def identity[T](...)`).
// ---------------------------------------------------------------------------

TEST(PythonEmitterStdlib, GenericClassEmitsPep695ParamList) {
    TranspileModule mod;
    TranspileType ty;
    ty.qualifiedName = "Box";
    ty.templateParams.push_back({TemplateParamDecl::TypeParam, "T"});
    mod.types.push_back(std::move(ty));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("class Box[T]:"), std::string::npos) << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, GenericClassParamListPrecedesBaseList) {
    TranspileModule mod;
    TranspileType ty;
    ty.qualifiedName = "Repo";
    ty.templateParams.push_back({TemplateParamDecl::TypeParam, "K"});
    ty.templateParams.push_back({TemplateParamDecl::TypeParam, "V"});
    ty.baseClasses = {pyNamed("Base")};
    mod.types.push_back(std::move(ty));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("class Repo[K, V](Base):"), std::string::npos) << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, GenericFunctionEmitsPep695ParamList) {
    TranspileModule mod;
    TranspileFunction fn;
    fn.qualifiedName = "identity";
    fn.templateParams.push_back({TemplateParamDecl::TypeParam, "T"});
    mod.functions.push_back(std::move(fn));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("def identity[T]("), std::string::npos) << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, NonGenericByteIdenticalToPreGenerics) {
    TranspileModule mod;
    TranspileType ty;
    ty.qualifiedName = "Plain";
    mod.types.push_back(std::move(ty));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("class Plain:"), std::string::npos) << "Generated:\n" << code;
    EXPECT_EQ(code.find("class Plain["), std::string::npos)
        << "no PEP 695 param list expected for non-generic:\n" << code;
}

// --- PEP 695 single trait-bound rendering: `[T: Bound]` ---

TEST(PythonEmitterStdlib, GenericClassWithSingleBoundEmitsPep695Colon) {
    TranspileModule mod;
    TranspileType ty;
    ty.qualifiedName = "Sortable";
    TemplateParamDecl tp{TemplateParamDecl::TypeParam, "T"};
    tp.constraintType = pyNamed("Comparable");
    ty.templateParams.push_back(tp);
    mod.types.push_back(std::move(ty));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("class Sortable[T: Comparable]:"), std::string::npos)
        << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, GenericFunctionWithSingleBoundEmitsPep695Colon) {
    TranspileModule mod;
    TranspileFunction fn;
    fn.qualifiedName = "pick";
    TemplateParamDecl tp{TemplateParamDecl::TypeParam, "T"};
    tp.constraintType = pyNamed("Comparable");
    fn.templateParams.push_back(tp);
    mod.functions.push_back(std::move(fn));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("def pick[T: Comparable]("), std::string::npos)
        << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, UnboundedTypeParamByteIdenticalToPreBoundsOutput) {
    // Absence of a bound must still emit a bare `[T]`, byte-identical to
    // pre-bounds emission. No stray `: ` should appear inside the brackets.
    TranspileModule mod;
    TranspileType ty;
    ty.qualifiedName = "Box";
    ty.templateParams.push_back({TemplateParamDecl::TypeParam, "T"});
    mod.types.push_back(std::move(ty));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("class Box[T]:"), std::string::npos)
        << "Generated:\n" << code;
    EXPECT_EQ(code.find("[T:"), std::string::npos)
        << "no bound expected when constraintType absent; got:\n" << code;
    EXPECT_EQ(code.find("[T ="), std::string::npos)
        << "no default expected when defaultType absent; got:\n" << code;
}

// --- PEP 696 default rendering: `[T = X]` ---
// Python supports type-parameter defaults in PEP 695 syntax on both classes
// and functions (3.13+). The emitter renders the default uniformly at every
// PEP-695 emit-site; bound + default coexist as `[T: Bound = Default]`.

TEST(PythonEmitterStdlib, GenericClassWithDefaultEmitsPep695Assign) {
    TranspileModule mod;
    TranspileType ty;
    ty.qualifiedName = "Box";
    TemplateParamDecl tp{TemplateParamDecl::TypeParam, "T"};
    tp.defaultType = pyNamed("int");
    ty.templateParams.push_back(tp);
    mod.types.push_back(std::move(ty));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("class Box[T = int]:"), std::string::npos)
        << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, GenericFunctionWithDefaultEmitsPep695Assign) {
    TranspileModule mod;
    TranspileFunction fn;
    fn.qualifiedName = "id";
    TemplateParamDecl tp{TemplateParamDecl::TypeParam, "T"};
    tp.defaultType = pyNamed("str");
    fn.templateParams.push_back(tp);
    mod.functions.push_back(std::move(fn));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("def id[T = str]("), std::string::npos)
        << "Generated:\n" << code;
}

TEST(PythonEmitterStdlib, BoundAndDefaultEmitInColonThenAssignOrder) {
    TranspileModule mod;
    TranspileFunction fn;
    fn.qualifiedName = "pick";
    TemplateParamDecl tp{TemplateParamDecl::TypeParam, "T"};
    tp.constraintType = pyNamed("Comparable");
    tp.defaultType = pyNamed("int");
    fn.templateParams.push_back(tp);
    mod.functions.push_back(std::move(fn));
    std::string code = PythonEmitter().emit(mod).code;
    EXPECT_NE(code.find("def pick[T: Comparable = int]("), std::string::npos)
        << "Generated:\n" << code;
}
