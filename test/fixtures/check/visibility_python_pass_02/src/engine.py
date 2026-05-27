# `engine.coordinate` (public) calls `engine.detail` (private) from the
# same namespace `engine` — visibility check must allow this.
#
# The .topo declaration uses `private:` for `detail`. Because both
# functions live in `engine.py` (file stem == namespace), the extractor
# synthesizes `engine::coordinate -> engine::detail` and the check sees
# them as same-namespace.


def detail():
    pass


def coordinate():
    detail()  # same-namespace private call — OK


def run():
    coordinate()
