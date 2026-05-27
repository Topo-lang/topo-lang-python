Wildcard import `from os import *` followed by unqualified `system("ls /tmp")`. Validates issue #7 fix: the wildcard import is detected as importing `os` and Pass 1 emits a containment violation.
