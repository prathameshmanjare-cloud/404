"""example_call: a ready-to-run call of the generated function with sample values (shown in the Run panel).

Values come from type hints first, then the parameter name, so the user never starts from a
NameError such as `is_palindrome(text)`.
"""
from __future__ import annotations

import ast

HEADER = "# Change the values below and press Run. The last line's value is shown.\n"


def _by_type(t: str, fn: str, idx: int, total: int) -> str | None:
    t = t.lower().replace("typing.", "")
    if t.startswith(("list", "sequence", "iterable")):
        return "[1, 3, 5, 7, 9]" if "search" in fn else "[3, 1, 4, 1, 5]"
    if t.startswith("tuple"):
        return "(4, 5, 6)"
    if t.startswith(("dict", "mapping")):
        return '{"a": 1, "b": 2}'
    if t.startswith("set"):
        return "{1, 2, 3}"
    if t.startswith("bool"):
        return "True"
    if t.startswith("float"):
        return "3.5"
    if t.startswith("int") or t == "number":
        if "palindrom" in fn:
            return "121"
        if "prime" in fn:
            return "17"
        if "gcd" in fn or "lcm" in fn:
            return ["12", "18"][idx % 2]
        return "7" if idx == total - 1 and "search" in fn else "10"
    if t.startswith("str"):
        if "anagram" in fn:
            return ['"listen"', '"silent"'][idx % 2]
        if "palindrom" in fn:
            return '"racecar"'
        return '"hello world"'
    return None


def _by_name(name: str, fn: str, idx: int, total: int) -> str:
    n = name.lower()
    if n in {"items", "values", "nums", "numbers", "lst", "arr", "array", "data", "seq", "sequence", "elements"}:
        return _by_type("list", fn, idx, total)
    if n in {"s", "text", "string", "word", "sentence", "name", "a_str", "b_str"} or n.endswith(("_str", "text")):
        return _by_type("str", fn, idx, total)
    if n in {"target", "key", "value", "x", "n", "num", "number", "k", "a", "b", "i", "m", "count", "size"}:
        return _by_type("int", fn, idx, total)
    if n.startswith(("is_", "flag")):
        return "True"
    return _by_type("int", fn, idx, total) or "10"


MUTATORS = ("put", "push", "add", "insert", "set", "append", "enqueue", "update", "deposit", "register")


def _args(fn: ast.FunctionDef, req_types: dict | None = None, skip_self: bool = False) -> str:
    req_types = req_types or {}
    positional = fn.args.posonlyargs + fn.args.args
    required = positional[: len(positional) - len(fn.args.defaults)]
    if skip_self:
        required = required[1:]
    required = [a for a in required if a.arg not in {"self", "cls"}]
    values = []
    for idx, arg in enumerate(required):
        hint = ast.unparse(arg.annotation) if arg.annotation is not None else req_types.get(arg.arg, "")
        first = hint.split("|")[0].strip() if hint else ""
        value = _by_type(first, fn.name.lower(), idx, len(required)) if first else None
        values.append(value or _by_name(arg.arg, fn.name.lower(), idx, len(required)))
    return ", ".join(values)


DESTRUCTIVE = ("pop", "remove", "dequeue", "delete", "clear", "discard", "withdraw", "evict")


def _snake(name: str) -> str:
    import re
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()
    return out if out.isidentifier() and out not in {"list", "dict", "set", "str", "int"} else "obj"


def _class_example(cls: ast.ClassDef) -> str:
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)]
    init = next((m for m in methods if m.name == "__init__"), None)
    public = [m for m in methods if not m.name.startswith("_")
              and not any(isinstance(d, ast.Name) and d.id in {"property", "staticmethod", "classmethod"}
                          for d in m.decorator_list)]
    var = _snake(cls.name)
    lines = [f"{var} = {cls.name}({_args(init, skip_self=True) if init else ''})"]
    mutators = [m for m in public if m.name.lower().startswith(MUTATORS)]
    readers = [m for m in public if m not in mutators]
    readers.sort(key=lambda m: m.name.lower().startswith(DESTRUCTIVE))  # look before you take
    for m in mutators[:3]:
        lines.append(f"{var}.{m.name}({_args(m, skip_self=True)})")
    for m in readers[:3]:
        lines.append(f"print({m.name!r}, {var}.{m.name}({_args(m, skip_self=True)}))")
    if not public:
        lines.append(var)
    return HEADER + "\n".join(lines)


def _inside_raises(node: ast.AST, parents: dict) -> bool:
    """True when the call sits in `with pytest.raises(...)`: an expected error is a bad example."""
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.With) and any("raises" in ast.unparse(i.context_expr) for i in node.items):
            return True
    return False


def call_from_tests(tests: str | None, fn_name: str) -> str | None:
    """The first call of fn_name in the tests whose arguments are all literals, e.g. roman_to_int("XIV")."""
    if not tests:
        return None
    try:
        tree = ast.parse(tests)
    except SyntaxError:
        return None
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    best = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == fn_name):
            continue
        if node.keywords or not node.args or _inside_raises(node, parents):
            continue
        try:
            for a in node.args:
                ast.literal_eval(a)
        except ValueError:
            continue
        text = ast.unparse(node)
        if len(text) > 160:
            continue
        parent = parents.get(node)
        # Prefer the call a test asserts is true or compares to a value; `assert not f(x)` comes last.
        negated = isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not)
        asserted = isinstance(parent, (ast.Assert, ast.Compare))
        # ...and among those, calls with real data over edge cases like f([], []) or f("").
        empties = sum(1 for a in node.args if ast.literal_eval(a) in ("", [], (), {}, set(), None))
        rank = (2 if negated else 0 if asserted else 1, empties, -len(text) if len(text) < 60 else 0, node.lineno)
        if best is None or rank < best[0]:
            best = (rank, text)
    return best[1] if best and best[0][0] < 2 else None


def example_call(code: str | None, requirements: dict | None = None, tests: str | None = None) -> str:
    if not code:
        return ""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return HEADER
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and not n.name.startswith("_")]
    wanted = (requirements or {}).get("function_name") or ""
    if requirements and wanted:
        named = [f for f in funcs if f.name == wanted]
        funcs = named or funcs
    named_cls = [c for c in classes if c.name.lower() == wanted.lower().replace("_", "")]
    # A class is the thing to demo when the spec names it, when there is no function, or when the only
    # functions just build the class (create_stack() -> Stack()).
    factory_only = funcs and all(f.name.lower().startswith(("create_", "make_", "new_", "build_")) for f in funcs)
    if classes and (named_cls or not funcs or factory_only):
        return _class_example((named_cls or classes)[0])
    if not funcs:
        return HEADER
    fn = funcs[0]
    from_tests = call_from_tests(tests, fn.name)
    if from_tests:
        return HEADER + from_tests
    req_types = {p.get("name"): p.get("type", "") for p in (requirements or {}).get("parameters", [])}
    positional = fn.args.posonlyargs + fn.args.args
    required = positional[: len(positional) - len(fn.args.defaults)]
    required = [a for a in required if a.arg not in {"self", "cls"}]
    values = []
    for idx, arg in enumerate(required):
        hint = ast.unparse(arg.annotation) if arg.annotation is not None else req_types.get(arg.arg, "")
        first = hint.split("|")[0].strip() if hint else ""
        value = _by_type(first, fn.name.lower(), idx, len(required)) if first else None
        values.append(value or _by_name(arg.arg, fn.name.lower(), idx, len(required)))
    return HEADER + f"{fn.name}({', '.join(values)})"
