# Hidden reference tests, written by hand. The request is ambiguous, so three shapes are accepted:
#   * one function for both types:            f([1, 2, 3]) == 6 and f((4, 5, 6)) == 15
#   * one function per type:                  sum_list([...]) == 6, sum_tuple((...)) == 15
#   * one function taking both, returning both: f([1, 2, 3], (4, 5, 6)) == (6, 15)
import inspect

import solution


def _funcs():
    funcs = [f for n, f in inspect.getmembers(solution, inspect.isfunction)
             if not n.startswith("_") and f.__module__ == "solution"]
    assert funcs, "solution defines no public function"
    return funcs


def _required(f):
    params = inspect.signature(f).parameters.values()
    return [p for p in params if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]


def _some(value, expected):
    for f in _funcs():
        try:
            if len(_required(f)) <= 1 and f(value) == expected:
                return True
        except Exception:
            continue
    return False


def _pair(lst, tup, expected):
    for f in _funcs():
        try:
            if len(_required(f)) == 2 and tuple(f(lst, tup)) == expected:
                return True
        except Exception:
            continue
    return False


def test_list_sum():
    assert _some([1, 2, 3], 6) or _pair([1, 2, 3], (), (6, 0))
    assert _some([-1, 1, 10], 10) or _pair([-1, 1, 10], (), (10, 0))


def test_tuple_sum():
    assert _some((4, 5, 6), 15) or _pair([], (4, 5, 6), (0, 15))
    assert _some((2.5, 2.5), 5) or _pair([], (2.5, 2.5), (0, 5))


def test_both_together():
    assert (_some([1, 2, 3], 6) and _some((4, 5, 6), 15)) or _pair([1, 2, 3], (4, 5, 6), (6, 15))


def test_empty():
    assert (_some([], 0) and _some((), 0)) or _pair([], (), (0, 0))
