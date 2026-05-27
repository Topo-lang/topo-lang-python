# `prepare` (stage<1>) directly calls `cleanup` (stage<3>), skipping
# stage<2>. Expected: one stage-isolation violation.


def cleanup():
    pass  # stage 3 work


def execute():
    pass  # stage 2 work


def prepare():
    cleanup()  # forward call: stage 1 -> stage 3 — violation


def run():
    prepare()
    execute()
    cleanup()
