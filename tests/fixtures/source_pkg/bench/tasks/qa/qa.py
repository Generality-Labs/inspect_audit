from bench.utils.data import load

from ..shared import CONSTANT


def task():
    from bench.utils.grading import grade  # lazy, inside a function

    return load(), grade, CONSTANT
