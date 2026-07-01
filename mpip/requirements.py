"""Parsing of PEP 508 requirement strings and PyPI wheel filenames."""

from __future__ import annotations

import re

from .version import SpecifierSet

# name[extra1,extra2] (specifier) ; marker
_REQ_RE = re.compile(
    r"""
    ^\s*
    (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)
    \s*
    (?:\[(?P<extras>[^\]]*)\])?
    \s*
    (?P<spec>(?:[<>=!~][^;]*)?)
    \s*
    (?:;\s*(?P<marker>.*))?
    \s*$
    """,
    re.VERBOSE,
)


def canonical_name(name: str) -> str:
    """PEP 503 normalisation: lowercase, runs of -_. collapse to a hyphen."""
    return re.sub(r"[-_.]+", "-", name).lower()


class Requirement:
    def __init__(self, raw: str):
        match = _REQ_RE.match(raw)
        if match is None:
            raise ValueError(f"could not parse requirement: {raw!r}")
        g = match.groupdict()
        self.raw = raw
        self.name = g["name"]
        self.canonical = canonical_name(g["name"])
        self.extras = frozenset(
            e.strip().lower() for e in (g["extras"] or "").split(",") if e.strip()
        )
        # Only keep the specifier if it isn't actually a URL requirement.
        spec = (g["spec"] or "").strip()
        self.specifier = SpecifierSet(spec)
        self.marker = (g["marker"] or "").strip() or None

    def __repr__(self) -> str:
        return f"Requirement({self.raw!r})"


# --------------------------------------------------------------------------- #
# Wheel filename parsing
# --------------------------------------------------------------------------- #

# {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl
_WHEEL_RE = re.compile(
    r"^(?P<name>.+?)-(?P<ver>.+?)"
    r"(-(?P<build>\d[^-]*))?"
    r"-(?P<pyver>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$"
)


class WheelInfo:
    def __init__(self, filename: str):
        match = _WHEEL_RE.match(filename)
        if match is None:
            raise ValueError(f"not a valid wheel filename: {filename!r}")
        g = match.groupdict()
        self.filename = filename
        self.name = g["name"]
        self.version = g["ver"]
        self.build = g["build"]
        # Compressed tag sets are '.'-separated (e.g. cp37.cp38-abi3-manylinux).
        pys = g["pyver"].split(".")
        abis = g["abi"].split(".")
        plats = g["plat"].split(".")
        self.tags = {
            (py, abi, plat) for py in pys for abi in abis for plat in plats
        }

    def is_compatible(self, supported_tags) -> int | None:
        """Return the best (lowest) priority index, or None if incompatible."""
        best = None
        for i, tag in enumerate(supported_tags):
            if tag in self.tags:
                if best is None or i < best:
                    best = i
        return best
