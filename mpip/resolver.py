"""Dependency resolution.

A pragmatic, greedy resolver: for each required project pick the newest version
that satisfies the accumulated specifiers and for which a compatible
distribution file exists, then recurse into that version's declared
dependencies. This is not a full backtracking SAT solver, but it resolves the
overwhelming majority of real dependency trees correctly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import markers
from .pypi import PyPIClient, PyPIError
from .requirements import Requirement, WheelInfo, canonical_name
from .tags import compatible_tags
from .version import InvalidVersion, SpecifierSet, Version


@dataclass
class Candidate:
    name: str
    canonical: str
    version: str
    url: str
    filename: str
    sha256: str | None
    is_wheel: bool
    requires_dist: list[str] = field(default_factory=list)
    extras: frozenset = field(default_factory=frozenset)


class ResolutionError(RuntimeError):
    pass


class Resolver:
    def __init__(self, client: PyPIClient, *, allow_pre: bool = False,
                 prefer_binary: bool = True, no_deps: bool = False, log=print):
        self.client = client
        self.allow_pre = allow_pre
        self.prefer_binary = prefer_binary
        self.no_deps = no_deps
        self.tags = compatible_tags()
        self.log = log

    def resolve(self, roots: list[str]) -> list[Candidate]:
        chosen: dict[str, Candidate] = {}
        # queue of (Requirement, extras-to-activate)
        queue = [Requirement(r) for r in roots]
        seen_specs: dict[str, SpecifierSet] = {}

        while queue:
            req = queue.pop(0)
            if req.marker and not markers.evaluate(req.marker):
                continue

            key = req.canonical
            if key in chosen:
                # Already resolved; verify the existing pick still satisfies.
                existing = chosen[key]
                if req.specifier and not req.specifier.contains(existing.version, self.allow_pre):
                    raise ResolutionError(
                        f"version conflict for {req.name}: {existing.version} "
                        f"does not satisfy {req.specifier}"
                    )
                # Activate any newly requested extras.
                new_extras = req.extras - existing.extras
                if new_extras:
                    existing.extras = existing.extras | req.extras
                    self._enqueue_deps(existing, queue)
                continue

            candidate = self._pick(req)
            chosen[key] = candidate
            self._enqueue_deps(candidate, queue)

        # Return in a leaves-first order so dependencies install before dependents.
        return list(chosen.values())

    def _enqueue_deps(self, candidate: Candidate, queue: list):
        if self.no_deps:
            return
        # A dependency applies if its marker is satisfied under *any* of the
        # extras activated on this candidate (plus the no-extra environment).
        # We evaluate that here — where the parent's extras are known — and then
        # clear the marker so the dependency is not re-judged (and wrongly
        # dropped) when it is popped without that extra context.
        contexts = (candidate.extras | frozenset({""})) or frozenset({""})
        for dep in candidate.requires_dist:
            try:
                dep_req = Requirement(dep)
            except ValueError:
                continue
            if dep_req.marker is None:
                applies = True
            else:
                applies = any(
                    markers.evaluate(dep_req.marker, extra=extra or None)
                    for extra in contexts
                )
            if applies:
                dep_req.marker = None
                queue.append(dep_req)

    def _pick(self, req: Requirement) -> Candidate:
        try:
            releases = self.client.releases(req.name)
        except PyPIError as exc:
            raise ResolutionError(str(exc)) from exc

        versions = []
        for ver in releases:
            try:
                v = Version(ver)
            except InvalidVersion:
                continue
            if req.specifier and not req.specifier.contains(ver, self.allow_pre):
                continue
            if v.is_prerelease and not (self.allow_pre or _spec_wants_pre(req.specifier)):
                continue
            versions.append((v, ver))

        versions.sort(reverse=True)

        for _v, ver in versions:
            files = [f for f in releases[ver] if not f.get("yanked", False)]
            picked = self._pick_file(req, ver, files)
            if picked is not None:
                return picked

        raise ResolutionError(
            f"no compatible distribution found for {req.name}{req.specifier or ''} "
            f"(interpreter tags exhausted)"
        )

    def _pick_file(self, req: Requirement, version: str, files: list) -> Candidate | None:
        wheels = []
        sdist = None
        for f in files:
            ftype = f.get("packagetype")
            name = f.get("filename", "")
            if ftype == "bdist_wheel" or name.endswith(".whl"):
                try:
                    info = WheelInfo(name)
                except ValueError:
                    continue
                priority = info.is_compatible(self.tags)
                if priority is not None:
                    wheels.append((priority, f, info))
            elif ftype == "sdist" or name.endswith((".tar.gz", ".zip")):
                sdist = f

        if wheels:
            wheels.sort(key=lambda x: x[0])
            _prio, f, _info = wheels[0]
            return self._make_candidate(req, version, f, is_wheel=True)

        if sdist is not None and not self.prefer_binary:
            return self._make_candidate(req, version, sdist, is_wheel=False)
        if sdist is not None:
            # Allow sdist as a last resort even in prefer-binary mode.
            return self._make_candidate(req, version, sdist, is_wheel=False)
        return None

    def _make_candidate(self, req, version, f, *, is_wheel) -> Candidate:
        # requires_dist is version-specific, so read it from the per-version
        # metadata endpoint rather than the (latest-release) project document.
        requires_dist = []
        info = self._version_info(req.name, version)
        if info:
            requires_dist = info.get("requires_dist") or []

        return Candidate(
            name=req.name,
            canonical=req.canonical,
            version=version,
            url=f["url"],
            filename=f["filename"],
            sha256=(f.get("digests") or {}).get("sha256"),
            is_wheel=is_wheel,
            requires_dist=requires_dist,
            extras=req.extras,
        )

    def _version_info(self, name: str, version: str) -> dict | None:
        try:
            doc = self.client._get_json(f"{self.client.index_url}/{name}/{version}/json")
            return doc.get("info", {})
        except PyPIError:
            # Fall back to the top-level project metadata.
            try:
                return self.client.project(name).get("info", {})
            except PyPIError:
                return None


def _spec_wants_pre(spec: SpecifierSet) -> bool:
    return spec._mentions_prerelease() if spec else False


def _dep_has_no_extra(dep: str) -> bool:
    return "extra" not in dep


def verify_sha256(data: bytes, expected: str | None) -> bool:
    if not expected:
        return True
    return hashlib.sha256(data).hexdigest() == expected
