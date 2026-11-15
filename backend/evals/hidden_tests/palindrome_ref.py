# Hidden reference tests, written by hand. They never come from a model.
import inspect

import solution


def _target():
    funcs = [f for n, f in inspect.getmembers(solution, inspect.isfunction)
             if not n.startswith("_") and f.__module__ == "solution"]
    assert funcs, "solution defines no public function"
    named = [f for f in funcs if "palindrome" in f.__name__.lower()]
    return (named or funcs)[0]


def test_odd_and_even_palindromes():
    f = _target()
    assert f("racecar") is True
    assert f("abba") is True


def test_not_palindrome():
    f = _target()
    assert f("hello") is False
    assert f("ab") is False


def test_empty_and_single():
    f = _target()
    assert f("") is True
    assert f("a") is True
