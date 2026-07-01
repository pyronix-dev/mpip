"""Minimal PEP 508 environment-marker evaluation.

Supports the marker grammar used by ``requires_dist`` entries: comparisons of
marker variables against string literals, joined with ``and`` / ``or`` and
grouped with parentheses. Enough to correctly decide whether an optional or
platform-specific dependency applies to the running interpreter.
"""

from __future__ import annotations

import os
import platform
import sys

from .version import Version


def default_environment() -> dict:
    """The marker variables for the interpreter mpip is running under."""
    impl = sys.implementation
    iver = impl.version
    impl_version = f"{iver.major}.{iver.minor}.{iver.micro}"
    if iver.releaselevel != "final":
        impl_version += f"{iver.releaselevel[0]}{iver.serial}"
    return {
        "os_name": os.name,
        "sys_platform": sys.platform,
        "platform_machine": platform.machine(),
        "platform_python_implementation": platform.python_implementation(),
        "platform_release": platform.release(),
        "platform_system": platform.system(),
        "platform_version": platform.version(),
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "python_full_version": platform.python_version(),
        "implementation_name": impl.name,
        "implementation_version": impl_version,
    }


_VERSION_VARS = {"python_version", "python_full_version", "implementation_version"}


def evaluate(marker: str, extra: str | None = None) -> bool:
    """Evaluate a marker string. ``extra`` sets the ``extra`` variable."""
    if not marker:
        return True
    env = default_environment()
    env["extra"] = extra or ""
    tokens = _tokenize(marker)
    parser = _Parser(tokens, env)
    result = parser.parse_or()
    if parser.pos != len(tokens):
        raise ValueError(f"trailing tokens in marker: {marker!r}")
    return result


# --------------------------------------------------------------------------- #
# Tokenizer / recursive-descent parser
# --------------------------------------------------------------------------- #

_OPS = ("===", "==", "!=", ">=", "<=", "~=", ">", "<")


def _tokenize(s: str):
    tokens = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            tokens.append(("paren", c))
            i += 1
            continue
        if c in "'\"":
            j = i + 1
            while j < n and s[j] != c:
                j += 1
            tokens.append(("str", s[i + 1 : j]))
            i = j + 1
            continue
        matched = False
        for op in _OPS:
            if s.startswith(op, i):
                tokens.append(("op", op))
                i += len(op)
                matched = True
                break
        if matched:
            continue
        # bare word: variable name or 'and'/'or'/'in'/'not'
        j = i
        while j < n and (s[j].isalnum() or s[j] == "_" or s[j] == "."):
            j += 1
        if j == i:
            raise ValueError(f"cannot tokenize marker near: {s[i:]!r}")
        tokens.append(("word", s[i:j]))
        i = j
    return tokens


class _Parser:
    def __init__(self, tokens, env):
        self.tokens = tokens
        self.env = env
        self.pos = 0

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def _next(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_or(self):
        value = self.parse_and()
        while self._peek() == ("word", "or"):
            self._next()
            rhs = self.parse_and()
            value = value or rhs
        return value

    def parse_and(self):
        value = self.parse_atom()
        while self._peek() == ("word", "and"):
            self._next()
            rhs = self.parse_atom()
            value = value and rhs
        return value

    def parse_atom(self):
        kind, val = self._peek()
        if (kind, val) == ("paren", "("):
            self._next()
            value = self.parse_or()
            if self._peek() != ("paren", ")"):
                raise ValueError("expected closing paren in marker")
            self._next()
            return value
        return self.parse_comparison()

    def parse_comparison(self):
        left = self._operand()
        kind, op = self._peek()
        negate = False
        if (kind, op) == ("word", "not"):
            self._next()
            if self._peek() != ("word", "in"):
                raise ValueError("expected 'in' after 'not' in marker")
            self._next()
            op, kind = "in", "op"
            negate = True
        elif kind == "word" and op == "in":
            self._next()
        elif kind == "op":
            self._next()
        else:
            raise ValueError(f"expected operator in marker, got {op!r}")
        right = self._operand()
        result = self._compare(left, op, right)
        return (not result) if negate else result

    def _operand(self):
        kind, val = self._next()
        if kind == "str":
            return ("literal", val)
        if kind == "word":
            return ("var", val)
        raise ValueError(f"unexpected token in marker: {val!r}")

    def _resolve(self, operand):
        tag, val = operand
        if tag == "literal":
            return val
        return self.env.get(val, "")

    def _compare(self, left, op, right):
        # Decide whether to compare as versions or as plain strings.
        version_context = (
            (left[0] == "var" and left[1] in _VERSION_VARS)
            or (right[0] == "var" and right[1] in _VERSION_VARS)
        )
        lval = self._resolve(left)
        rval = self._resolve(right)

        if op == "in":
            return lval in rval
        if op == "not in":
            return lval not in rval

        if version_context and op in ("==", "!=", ">", "<", ">=", "<=", "~=", "==="):
            try:
                lv, rv = Version(lval), Version(rval)
                return _version_cmp(lv, rv, op)
            except Exception:
                pass  # fall back to string comparison

        return _string_cmp(lval, rval, op)


def _version_cmp(lv, rv, op):
    return {
        "==": lv == rv,
        "===": lv == rv,
        "!=": lv != rv,
        ">": lv > rv,
        "<": lv < rv,
        ">=": lv >= rv,
        "<=": lv <= rv,
        "~=": lv >= rv,
    }[op]


def _string_cmp(a, b, op):
    return {
        "==": a == b,
        "===": a == b,
        "!=": a != b,
        ">": a > b,
        "<": a < b,
        ">=": a >= b,
        "<=": a <= b,
    }.get(op, False)
