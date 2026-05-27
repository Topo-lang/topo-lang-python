# `Renderer.render` is declared `external` in .topo. The host emits its
# callerQualifiedName as `Renderer.render` (Python uses `.` as the scope
# separator). Containment must recognize the class-method caller as
# external via the simple-name fallback in ContainmentCheck — this requires
# LanguageAnalysisProvider::separator() to return "." for Python.
#
# Without the per-language separator, the fallback would split on `::`,
# fail to strip `Renderer.`, and report `os.system` as a violation.

import os


class Renderer:
    def render(self, id):
        os.system("echo " + str(id))
        return id * 2
