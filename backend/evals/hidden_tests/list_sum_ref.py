# Hidden reference tests, written by hand. They accept one function for both types or one per type.
import inspect

import solution


def _funcs():
    funcs = [f for n, f in inspect.getmembers(solution, inspect.isfunction)
             if not n.startswith("_") and f.__module__ == "solution"]
    assert funcs, "solution defines no public function"
    return funcs


def _some(value, expected):
    for f in _funcs():
        try:
            if f(value) == expected:
                return True
        except Exception:
            continue
    return False


def test_list_sum():
    assert _some([1, 2, 3], 6)
    assert _some([-1, 1, 10], 10)


def test_tuple_sum():
    assert _some((4, 5, 6), 15)
    assert _some((2.5, 2.5), 5)


def test_empty():
    assert _some([], 0)
    assert _some((), 0)
