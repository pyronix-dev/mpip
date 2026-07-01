"""Compute the wheel-compatibility tags for the running interpreter.

This mirrors what the ``packaging`` library's ``tags`` module produces, but is
written from scratch so mpip has no third-party dependencies. The returned list
is ordered from most specific / most preferred to least, so a lower index means
a better match.
"""

from __future__ import annotations

import platform
import re
import sys
import sysconfig


def _interpreter_versions():
    major, minor = sys.version_info[:2]
    # py314, py313 ... py30, then the bare major tag py3 (used by most
    # pure-python wheels, e.g. foo-1.0-py3-none-any.whl).
    py_versions = [f"{major}{minor}"]
    for m in range(minor - 1, -1, -1):
        py_versions.append(f"{major}{m}")
    py_versions.append(f"{major}")
    return major, minor, py_versions


def _cpython_abis(minor):
    major = sys.version_info[0]
    abis = []
    soabi = sysconfig.get_config_var("SOABI")
    impl = f"cp{major}{minor}"
    if soabi and soabi.startswith("cpython"):
        # e.g. cpython-313-darwin -> cp313
        abis.append(impl)
    else:
        abis.append(impl)
    # abi3 is forward-compatible from the version it was built against.
    return abis


def _mac_platforms():
    """Generate macOS platform tags, newest→oldest, for this machine's arch."""
    version_str, _, machine = platform.mac_ver()
    if not version_str:
        return []
    major, minor = (int(x) for x in version_str.split(".")[:2])
    arch = machine or platform.machine()

    # Since macOS 11 the minor version is effectively 0 for tag purposes.
    if major >= 11:
        release_versions = [(m, 0) for m in range(major, 10, -1)]
        release_versions += [(10, m) for m in range(16, -1, -1)]
    else:
        release_versions = [(10, m) for m in range(minor, -1, -1)]

    if arch == "arm64":
        arches = ["arm64", "universal2"]
    elif arch == "x86_64":
        arches = ["x86_64", "universal2", "intel", "fat64", "fat32"]
    else:
        arches = [arch]

    plats = []
    for maj, minr in release_versions:
        for a in arches:
            plats.append(f"macosx_{maj}_{minr}_{a}")
    return plats


def _manylinux_platforms(glibc_major, glibc_minor, arch):
    """Generate glibc/manylinux platform tags, newest→oldest.

    A wheel tagged ``manylinux_2_N_<arch>`` needs glibc >= 2.N, so the system is
    compatible with every tag from its own glibc version down to the manylinux
    baselines. We also emit the legacy aliases (manylinux1/2010/2014).
    """
    plats = []
    # PEP 600 perennial tags from the running glibc down to the 2.5 floor.
    floor = 5 if arch in ("x86_64", "i686") else 17
    for minor in range(glibc_minor, floor - 1, -1):
        plats.append(f"manylinux_{glibc_major}_{minor}_{arch}")
        if (glibc_major, minor) == (2, 17):
            plats.append(f"manylinux2014_{arch}")
        elif (glibc_major, minor) == (2, 12):
            plats.append(f"manylinux2010_{arch}")
        elif (glibc_major, minor) == (2, 5):
            plats.append(f"manylinux1_{arch}")
    return plats


def _linux_platforms():
    arch = platform.machine() or "x86_64"
    plats = []
    try:
        libc_name, libc_ver = platform.libc_ver()
    except OSError:
        libc_name, libc_ver = "", ""
    if libc_name == "glibc" and libc_ver:
        parts = libc_ver.split(".")
        major, minor = int(parts[0]), int(parts[1] if len(parts) > 1 else 0)
        plats.extend(_manylinux_platforms(major, minor, arch))
    plats.append(f"linux_{arch}")
    return plats


def _generic_platform():
    plat = sysconfig.get_platform()  # e.g. 'macosx-14.0-arm64', 'linux-x86_64'
    return re.sub(r"[-.]", "_", plat)


def platform_tags():
    if sys.platform == "darwin":
        mac = _mac_platforms()
        if mac:
            return mac
    if sys.platform.startswith("linux"):
        return _linux_platforms()
    return [_generic_platform()]


def compatible_tags():
    """Ordered list of ``(interpreter, abi, platform)`` tuples we can install."""
    major, minor, py_versions = _interpreter_versions()
    interp = f"cp{major}{minor}"
    abis = _cpython_abis(minor)
    plats = platform_tags()

    tags: list[tuple[str, str, str]] = []

    # 1. cpXY-cpXY-<plat>  (native extension modules for this exact version)
    for abi in abis:
        for plat in plats:
            tags.append((interp, abi, plat))

    # 2. cpXY-abi3-<plat>  (stable-ABI extensions, current and older minors)
    for m in range(minor, 1, -1):
        for plat in plats:
            tags.append((f"cp{major}{m}", "abi3", plat))

    # 3. cpXY-none-<plat>
    for plat in plats:
        tags.append((interp, "none", plat))

    # 4. pyXY-none-<plat> and pyX-none-<plat>  (platform-specific pure python)
    for py in py_versions:
        for plat in plats:
            tags.append((f"py{py}", "none", plat))

    # 5. pure-python, any platform (the big one for most libraries)
    for py in py_versions:
        tags.append((f"py{py}", "none", "any"))

    return tags
