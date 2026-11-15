"""Template tier (R5): reviewed code and tests for five known tasks, served when models are down."""
from __future__ import annotations

import re

TEMPLATES: dict[str, dict] = {
    "palindrome_number": {
        "keywords": [r"palindrom\w*.*\b(numbers?|integers?|int|digits?)\b",
                     r"\b(numbers?|integers?|int|digits?)\b.*palindrom"],
        "code": '''def is_palindrome_number(n: int) -> bool:
    """Return True if the integer n reads the same forwards and backwards (negative numbers never do)."""
    if n < 0:
        return False
    digits = str(n)
    return digits == digits[::-1]
''',
        "tests": '''from solution import is_palindrome_number


def test_palindromes():
    for n in (0, 7, 11, 121, 12321):
        assert is_palindrome_number(n)


def test_not_palindromes():
    for n in (10, 123, 1231):
        assert not is_palindrome_number(n)


def test_negative():
    assert not is_palindrome_number(-121)
''',
    },
    "palindrome": {
        "keywords": [r"palindrome"],
        "code": '''def is_palindrome(text: str) -> bool:
    """Return True if text reads the same forwards and backwards (case and non-alphanumerics ignored)."""
    cleaned = [ch.lower() for ch in str(text) if ch.isalnum()]
    return cleaned == cleaned[::-1]
''',
        "tests": '''from solution import is_palindrome


def test_simple():
    assert is_palindrome("racecar")


def test_not_palindrome():
    assert not is_palindrome("hello")


def test_empty_string():
    assert is_palindrome("")


def test_mixed_case_and_spaces():
    assert is_palindrome("A man, a plan, a canal: Panama")


def test_number_input():
    assert is_palindrome(121)
    assert not is_palindrome(123)
''',
    },
    "factorial": {
        "keywords": [r"factorial"],
        "code": '''def factorial(n: int) -> int:
    """Return n! for a non-negative integer n."""
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result
''',
        "tests": '''import pytest
from solution import factorial


def test_zero():
    assert factorial(0) == 1


def test_five():
    assert factorial(5) == 120


def test_negative():
    with pytest.raises(ValueError):
        factorial(-1)
''',
    },
    "fibonacci": {
        "keywords": [r"fibonacci", r"\bfib\b"],
        "code": '''def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number (fibonacci(0) == 0)."""
    if n < 0:
        raise ValueError("n must be non-negative")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
''',
        "tests": '''import pytest
from solution import fibonacci


def test_first_values():
    assert [fibonacci(i) for i in range(8)] == [0, 1, 1, 2, 3, 5, 8, 13]


def test_negative():
    with pytest.raises(ValueError):
        fibonacci(-3)
''',
    },
    "fizzbuzz": {
        "keywords": [r"fizz\s*buzz"],
        "code": '''def fizzbuzz(n: int) -> list[str]:
    """Return the FizzBuzz sequence from 1 to n."""
    out = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            out.append("FizzBuzz")
        elif i % 3 == 0:
            out.append("Fizz")
        elif i % 5 == 0:
            out.append("Buzz")
        else:
            out.append(str(i))
    return out
''',
        "tests": '''from solution import fizzbuzz


def test_fifteen():
    assert fizzbuzz(15)[-1] == "FizzBuzz"
    assert fizzbuzz(5) == ["1", "2", "Fizz", "4", "Buzz"]


def test_zero():
    assert fizzbuzz(0) == []
''',
    },
    "list_sum": {
        "keywords": [r"\bsum(s|ming)?\b.*\b(list|tuple)", r"\b(list|tuple)\b.*\bsum(s|ming)?\b"],
        "code": '''from functools import reduce
from operator import add


def sum_numbers(values):
    """Sum a list with a comprehension and a tuple in functional style."""
    if isinstance(values, list):
        return sum([v for v in values])
    if isinstance(values, tuple):
        return reduce(add, values, 0)
    raise TypeError("values must be a list or a tuple")
''',
        "tests": '''import pytest
from solution import sum_numbers


def test_list():
    assert sum_numbers([1, 2, 3]) == 6


def test_tuple():
    assert sum_numbers((4, 5, 6)) == 15


def test_empty():
    assert sum_numbers([]) == 0
    assert sum_numbers(()) == 0


def test_other_type():
    with pytest.raises(TypeError):
        sum_numbers("123")
''',
    },
    "reverse_string": {
        "keywords": [r"\breverse\b.*\b(string|word|text|sentence)\b"],
        "code": '''def reverse_string(text: str) -> str:
    """Return text with its characters in reverse order."""
    return text[::-1]
''',
        "tests": '''from solution import reverse_string


def test_reverse():
    assert reverse_string("hello") == "olleh"


def test_empty_and_single():
    assert reverse_string("") == ""
    assert reverse_string("a") == "a"
''',
    },
    "is_prime": {
        "keywords": [r"\bprime\b"],
        "code": '''def is_prime(n: int) -> bool:
    """Return True if n is a prime number."""
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True
''',
        "tests": '''from solution import is_prime


def test_small_values():
    assert [n for n in range(20) if is_prime(n)] == [2, 3, 5, 7, 11, 13, 17, 19]


def test_non_positive():
    assert not is_prime(0)
    assert not is_prime(-7)


def test_large_prime_and_square():
    assert is_prime(7919)
    assert not is_prime(49)
''',
    },
    "gcd": {
        "keywords": [r"\b(gcd|hcf|greatest common divisor|highest common factor)\b"],
        "code": '''def gcd(a: int, b: int) -> int:
    """Return the greatest common divisor of a and b (always non-negative)."""
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a
''',
        "tests": '''from solution import gcd


def test_basic():
    assert gcd(12, 18) == 6
    assert gcd(17, 5) == 1


def test_zero_and_negative():
    assert gcd(0, 9) == 9
    assert gcd(0, 0) == 0
    assert gcd(-12, 18) == 6
''',
    },
    "binary_search": {
        "keywords": [r"binary\s+search"],
        "code": '''def binary_search(items: list, target) -> int:
    """Return the index of target in the sorted list items, or -1 if it is absent."""
    lo, hi = 0, len(items) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
''',
        "tests": '''from solution import binary_search


def test_found():
    data = [1, 3, 5, 7, 9, 11]
    for i, v in enumerate(data):
        assert binary_search(data, v) == i


def test_missing_and_empty():
    assert binary_search([1, 3, 5], 4) == -1
    assert binary_search([], 1) == -1
''',
    },
    "count_vowels": {
        "keywords": [r"\bvowels?\b"],
        "code": '''def count_vowels(text: str) -> int:
    """Return the number of vowels (a, e, i, o, u, any case) in text."""
    return sum(1 for ch in text.lower() if ch in "aeiou")
''',
        "tests": '''from solution import count_vowels


def test_count():
    assert count_vowels("Hello World") == 3
    assert count_vowels("AEIOU") == 5


def test_none():
    assert count_vowels("") == 0
    assert count_vowels("rhythm") == 0
''',
    },
    "anagram": {
        "keywords": [r"\banagrams?\b"],
        "code": '''from collections import Counter


def is_anagram(a: str, b: str) -> bool:
    """Return True if a and b use the same letters (case and spaces ignored)."""
    clean = lambda s: Counter(ch.lower() for ch in s if not ch.isspace())
    return clean(a) == clean(b)
''',
        "tests": '''from solution import is_anagram


def test_anagram():
    assert is_anagram("listen", "silent")
    assert is_anagram("Dormitory", "dirty room")


def test_not_anagram():
    assert not is_anagram("abc", "abd")
    assert not is_anagram("aab", "ab")


def test_empty():
    assert is_anagram("", "")
''',
    },
}


def match_template(request: str) -> str | None:
    text = request.lower()
    for name, tpl in TEMPLATES.items():
        if any(re.search(k, text) for k in tpl["keywords"]):
            return name
    return None
