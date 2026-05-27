import os.path as op
from subprocess import Popen as Proc


def launch(cmd: str) -> int:
    p = Proc([cmd])
    return op.join("/tmp", cmd).find("x")
