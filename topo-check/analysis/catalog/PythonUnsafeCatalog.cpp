#include "PythonUnsafeCatalog.h"

#include <unordered_set>

namespace topo::check {

UnsafeLevel PythonUnsafeCatalog::classifyCall(const std::string& pattern) {
    // Level 4: Language escape mechanisms
    static const std::unordered_set<std::string> escape = {
        "exec", "eval", "__import__",
        "ctypes.cdll", "ctypes.CDLL", "ctypes.windll",
        "ctypes.pythonapi", "ctypes.CFUNCTYPE",
        "pickle.loads", "pickle.load",
        "os.system", "os.popen", "os.popen2", "os.popen3", "os.popen4",
        "os.exec", "os.execvp",
        "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
        "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
        "sys._getframe", "inspect.stack", "inspect.currentframe",
        "globals", "locals", "vars",
        // --- issue #5: dynamic reflection (conservative posture) ---
        // L1 cannot inspect args, so all calls are classified as escape.
        "getattr", "setattr", "delattr",
        "builtins.getattr", "builtins.setattr", "builtins.delattr",
        // --- issue #6: serialization / deserialization ---
        "marshal.loads", "marshal.load", "marshal.dumps", "marshal.dump",
        "shelve.open",
        "yaml.load", // bare yaml.load (without SafeLoader) is unsafe
        "pickle.Unpickler",
        // --- issue #6: metaprogramming / attribute punning ---
        "__dict__.update", "__class__", "__bases__", "__mro__",
        "__init_subclass__",
        // --- issue #6: frame / trace manipulation ---
        "gc.get_referrers", "gc.get_objects",
        "sys.settrace", "sys.setprofile",
        "inspect.getinnerframes", "inspect.getframeinfo",
        // --- issue #6: dynamic loading / monkey patching ---
        "importlib.reload",
        // --- issue #6: FFI / raw memory ---
        "cffi.FFI", "mmap.mmap",
    };
    if (escape.count(pattern)) return UnsafeLevel::Escape;

    // compile() — only dangerous as standalone builtin, not re.compile/ast.compile
    // Check that the pattern is bare "compile", not "re.compile" etc.
    if (pattern == "compile") return UnsafeLevel::Escape;

    // Level 3: User input handling
    static const std::unordered_set<std::string> input = {
        "flask.request", "django.http.HttpRequest",
        "input",
    };
    if (input.count(pattern)) return UnsafeLevel::Input;

    // Level 1: System calls
    static const std::unordered_set<std::string> systemCalls = {
        "open", "os.open", "os.read", "os.write",
        "os.makedirs", "os.remove", "os.unlink", "os.rmdir",
        "os.listdir", "os.walk", "os.rename",
        "os.chmod", "os.chown", "os.stat",
        "os.path.exists", "os.path.isfile",
        "os.getenv", "os.putenv",
        "pathlib.Path",
        "socket.socket", "socket.create_connection", "socket.getaddrinfo",
        "urlopen",
        "requests.get", "requests.post", "requests.put", "requests.delete",
        "subprocess.run", "subprocess.Popen", "subprocess.call",
        "subprocess.check_output",
        "subprocess.getoutput", "subprocess.getstatusoutput",
        "os.fork",
        "importlib.import_module",
        "multiprocessing.Process",
        "shutil.rmtree", "shutil.copytree", "shutil.move",
        "shutil.copy", "shutil.copy2", "shutil.disk_usage",
        "print",
        "sys.stdout.write", "sys.stdout.writelines",
        "sys.stderr.write", "sys.stderr.writelines",
        "logging.debug", "logging.info", "logging.warning",
        "logging.error", "logging.critical", "logging.log",
        "logging.exception",
    };
    if (systemCalls.count(pattern)) return UnsafeLevel::System;

    return UnsafeLevel::Safe;
}

UnsafeLevel PythonUnsafeCatalog::classifyImport(const std::string& path) {
    // Level 4
    static const std::unordered_set<std::string> escape = {
        "ctypes", "pickle",
        // --- issue #6 ---
        "marshal",  // wraps the same byte-code (de)serialization as pickle
        "shelve",   // implemented via pickle under the hood
        "yaml",     // PyYAML default loader allows arbitrary constructors
        "cffi",     // foreign function interface
        "mmap",     // raw shared-memory mapping
    };
    if (escape.count(path)) return UnsafeLevel::Escape;

    // Level 1
    static const std::unordered_set<std::string> system = {
        "os", "io", "pathlib", "shutil", "tempfile", "glob", "fnmatch",
        "socket", "http", "urllib", "ssl", "ftplib", "smtplib", "xmlrpc",
        "subprocess", "multiprocessing", "importlib", "signal",
        "sys", "logging",
        // --- issue #6: gc / inspect expose frame-level state ---
        "gc", "inspect",
    };
    if (system.count(path)) return UnsafeLevel::System;

    // Level 3
    static const std::unordered_set<std::string> input = {
        "flask", "django", "fastapi", "starlette",
    };
    if (input.count(path)) return UnsafeLevel::Input;

    // Level 2: any module not in stdlib
    // Heuristic: known stdlib modules return Safe, everything else is Dep
    static const std::unordered_set<std::string> stdlib = {
        "abc", "argparse", "ast", "asyncio", "base64", "bisect",
        "collections", "concurrent", "configparser", "contextlib", "copy",
        "csv", "dataclasses", "datetime", "decimal", "difflib",
        "enum", "errno", "fractions", "functools",
        "hashlib", "heapq", "hmac", "html",
        "inspect", "itertools", "json", "math",
        "operator", "pprint", "queue", "random", "re",
        "statistics", "string", "struct", "textwrap",
        "threading", "time", "traceback", "types", "typing",
        "unittest", "uuid", "warnings", "weakref", "zipfile",
    };
    if (stdlib.count(path)) return UnsafeLevel::Safe;

    // Not in any known category -> third-party dep
    return UnsafeLevel::Dep;
}

} // namespace topo::check
