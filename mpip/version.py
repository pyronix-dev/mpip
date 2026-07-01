"""A compact, dependency-free PEP 440 version implementation.

Only what mpip needs: parse a version, compare two versions, and evaluate the
common specifier operators (==, !=, >=, <=, >, <, ~=, ===). It is not a full
implementation of the spec, but it handles the vast majority of real packages
on PyPI.
"""

from __future__ import annotations

import re
from functools import total_ordering

# Canonical PEP 440 version pattern (simplified but covers epoch/pre/post/dev).
_VERSION_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<epoch>[0-9]+)!)?                      # epoch
    (?P<release>[0-9]+(?:\.[0-9]+)*)             # release segment
    (?P<pre>[-_.]?(?P<pre_l>a|b|c|rc|alpha|beta|pre|preview)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>[-_.]?(?:post|rev|r)[-_.]?(?P<post_n>[0-9]+)?|-(?P<post_n2>[0-9]+))?
    (?P<dev>[-_.]?dev[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?  # local version
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Normalise the many spellings of pre-release labels down to one.
_PRE_NORMALISE = {"alpha": "a", "beta": "b", "c": "rc", "pre": "rc", "preview": "rc"}


class _InfinityType:
    """A sentinel that sorts greater than any object, of any type."""

    def __repr__(self):
        return "Infinity"

    def __hash__(self):
        return hash(repr(self))

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return False

    def __eq__(self, other):
        return isinstance(other, _InfinityType)

    def __gt__(self, other):
        return True

    def __ge__(self, other):
        return True

    def __neg__(self):
        return NegativeInfinity


class _NegativeInfinityType:
    """A sentinel that sorts less than any object, of any type."""

    def __repr__(self):
        return "-Infinity"

    def __hash__(self):
        return hash(repr(self))

    def __lt__(self, other):
        return True

    def __le__(self, other):
        return True

    def __eq__(self, other):
        return isinstance(other, _NegativeInfinityType)

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return False

    def __neg__(self):
        return Infinity


Infinity = _InfinityType()
NegativeInfinity = _NegativeInfinityType()


@total_ordering
class Version:
    """A parsed, comparable PEP 440 version."""

    def __init__(self, raw: str):
        match = _VERSION_RE.match(str(raw))
        if match is None:
            raise InvalidVersion(f"invalid version: {raw!r}")
        self.raw = str(raw)
        g = match.groupdict()

        self.epoch = int(g["epoch"]) if g["epoch"] else 0
        self.release = tuple(int(x) for x in g["release"].split("."))

        if g["pre_l"]:
            label = _PRE_NORMALISE.get(g["pre_l"].lower(), g["pre_l"].lower())
            self.pre = (label, int(g["pre_n"]) if g["pre_n"] else 0)
        else:
            self.pre = None

        post_n = g["post_n"] or g["post_n2"]
        self.post = int(post_n) if post_n is not None else (
            0 if g["post"] else None
        )

        if g["dev"] is not None:
            self.dev = int(g["dev_n"]) if g["dev_n"] else 0
        else:
            self.dev = None

        self.local = g["local"]

    @property
    def is_prerelease(self) -> bool:
        return self.pre is not None or self.dev is not None

    def _key(self):
        # Build a sortable key following PEP 440 ordering rules.
        release = _trim_trailing_zeros(self.release)

        # Pre-release sorts before the final release; absence sorts after.
        if self.pre is None and self.post is None and self.dev is not None:
            pre = NegativeInfinity          # a lone .devN is before everything
        elif self.pre is None:
            pre = Infinity                  # not a pre-release => sorts high
        else:
            pre = self.pre

        post = NegativeInfinity if self.post is None else self.post
        dev = Infinity if self.dev is None else self.dev  # no dev sorts high
        local = NegativeInfinity if self.local is None else _local_key(self.local)

        return (self.epoch, release, pre, post, dev, local)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __repr__(self) -> str:
        return f"Version({self.raw!r})"

    def __str__(self) -> str:
        return self.raw


class InvalidVersion(ValueError):
    pass


def _trim_trailing_zeros(release):
    # Drop trailing zero components so 1.0 == 1.0.0, keeping order otherwise.
    values = list(release)
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    return tuple(values)


def _local_key(local: str):
    parts = re.split(r"[-_.]", local)
    key = []
    for part in parts:
        if part.isdigit():
            key.append((int(part), ""))
        else:
            key.append((NegativeInfinity, part))
    return tuple(key)


# --------------------------------------------------------------------------- #
# Specifier matching
# --------------------------------------------------------------------------- #

_SPEC_RE = re.compile(r"(===|==|!=|~=|>=|<=|>|<)\s*([^,;\s]+)")


class SpecifierSet:
    """A comma-separated set of version specifiers, e.g. ``>=1.0,<2``."""

    def __init__(self, spec: str = ""):
        self.specs = []
        for op, ver in _SPEC_RE.findall(spec or ""):
            self.specs.append((op, ver.strip()))

    def __bool__(self) -> bool:
        return bool(self.specs)

    def contains(self, version: str | Version, allow_prerelease: bool | None = None) -> bool:
        if not isinstance(version, Version):
            try:
                version = Version(version)
            except InvalidVersion:
                return False

        # By default pre-releases only match if the set explicitly allows them
        # or if the version being tested is itself final.
        if allow_prerelease is None:
            allow_prerelease = version.is_prerelease and self._mentions_prerelease()

        if version.is_prerelease and not allow_prerelease:
            # Still allow if every operator is satisfied and one names a pre.
            if not self._mentions_prerelease():
                return False

        return all(_match_one(op, ver, version) for op, ver in self.specs)

    def _mentions_prerelease(self) -> bool:
        for _op, ver in self.specs:
            try:
                if Version(ver).is_prerelease:
                    return True
            except InvalidVersion:
                continue
        return False

    def __str__(self) -> str:
        return ",".join(f"{op}{ver}" for op, ver in self.specs)


def _match_one(op: str, spec_ver: str, version: Version) -> bool:
    if op == "===":
        return version.raw == spec_ver

    if op == "~=":
        # Compatible release: ~=1.4.2 means >=1.4.2, ==1.4.*
        base = Version(spec_ver)
        if len(base.release) < 2:
            return False
        upper_release = base.release[:-1]
        lower_ok = version >= base
        upper_ok = version.release[: len(upper_release)] == upper_release or (
            _prefix_lt(version.release, upper_release)
        )
        # Correct compatible-release check: same length-1 prefix and >= base.
        prefix = base.release[:-1]
        same_prefix = version.release[: len(prefix)] == prefix
        return lower_ok and same_prefix

    if op == "==":
        if spec_ver.endswith(".*"):
            prefix = Version(spec_ver[:-2]).release
            return version.release[: len(prefix)] == prefix
        return version == Version(spec_ver)
    if op == "!=":
        if spec_ver.endswith(".*"):
            prefix = Version(spec_ver[:-2]).release
            return version.release[: len(prefix)] != prefix
        return version != Version(spec_ver)

    other = Version(spec_ver)
    if op == ">":
        return version > other
    if op == "<":
        return version < other
    if op == ">=":
        return version >= other
    if op == "<=":
        return version <= other
    return False


def _prefix_lt(a, b):
    return a[: len(b)] < b
