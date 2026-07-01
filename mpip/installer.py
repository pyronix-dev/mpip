"""Install resolved candidates into a target directory.

Wheels are ZIP archives with a defined layout (PEP 427), so "installing" is
really: verify the download, unpack the archive into ``site-packages``, relocate
anything under the ``*.data`` tree, generate console-script launchers from the
wheel's ``entry_points.txt``, and record what we did. No build step, no pip.
"""

from __future__ import annotations

import configparser
import io
import os
import stat
import sys
import sysconfig
import zipfile

from .pypi import PyPIClient
from .resolver import Candidate, verify_sha256


class InstallError(RuntimeError):
    pass


_SCRIPT_TEMPLATE = """\
#!{python}
# -*- coding: utf-8 -*-
import re
import sys
from {module} import {attr}
if __name__ == "__main__":
    sys.argv[0] = re.sub(r"(-script\\.pyw|\\.exe)?$", "", sys.argv[0])
    sys.exit({func}())
"""


def default_target() -> str:
    return sysconfig.get_path("purelib")


def user_target() -> str:
    return sysconfig.get_path("purelib", scheme=_user_scheme())


def _user_scheme() -> str:
    if os.name == "nt":
        return "nt_user"
    if sys.platform == "darwin" and sys._framework:
        return "osx_framework_user"
    return "posix_user"


def scripts_dir_for(target: str) -> str:
    """Where console-script launchers should go for a given target dir.

    If ``target`` is the interpreter's own purelib we use its matching scripts
    directory; otherwise (a custom ``--target``) we keep scripts self-contained
    in ``<target>/bin`` so nothing escapes the chosen directory.
    """
    if os.path.normpath(target) == os.path.normpath(default_target()):
        return sysconfig.get_path("scripts")
    return os.path.join(target, "bin")


class Installer:
    def __init__(self, client: PyPIClient, target: str, *, scripts_dir: str | None = None,
                 log=print, force: bool = False):
        self.client = client
        self.target = target
        self.scripts_dir = scripts_dir or scripts_dir_for(target)
        self.log = log
        self.force = force
        # Console-script names created this run (used to warn about PATH).
        self.created_scripts: list[str] = []

    def install(self, candidate: Candidate) -> None:
        if not candidate.is_wheel:
            raise InstallError(
                f"{candidate.filename}: only wheel installs are supported "
                f"(no compatible wheel was published for {candidate.name} "
                f"{candidate.version})"
            )

        dist_info = self._already_installed(candidate)
        if dist_info and not self.force:
            self.log(f"  already satisfied: {candidate.name}=={candidate.version}")
            return

        self.log(f"  downloading {candidate.filename}")
        data = self.client.download(candidate.url)
        if not verify_sha256(data, candidate.sha256):
            raise InstallError(f"sha256 mismatch for {candidate.filename}")

        self._unpack_wheel(candidate, data)

    def _already_installed(self, candidate: Candidate) -> str | None:
        from .requirements import canonical_name
        norm = canonical_name(candidate.name)
        try:
            for entry in os.listdir(self.target):
                if entry.endswith(".dist-info"):
                    base = entry[: -len(".dist-info")]
                    if "-" in base:
                        nm, ver = base.rsplit("-", 1)
                        if canonical_name(nm) == norm and ver == candidate.version:
                            return os.path.join(self.target, entry)
        except FileNotFoundError:
            pass
        return None

    def _unpack_wheel(self, candidate: Candidate, data: bytes) -> None:
        os.makedirs(self.target, exist_ok=True)
        zf = zipfile.ZipFile(io.BytesIO(data))

        dist_info_name = self._find_dist_info(zf, candidate)
        data_prefix = dist_info_name[: -len(".dist-info")] + ".data/"

        installed_files: list[str] = []

        for name in zf.namelist():
            if name.endswith("/"):
                continue
            if name.startswith(data_prefix):
                dest = self._data_destination(name, data_prefix)
                if dest is None:
                    continue
                category = name[len(data_prefix):].split("/", 1)[0]
                self._write(zf, name, dest, installed_files,
                            is_script=(category == "scripts"))
            else:
                dest = os.path.join(self.target, name)
                self._write(zf, name, dest, installed_files)

        self._generate_console_scripts(zf, dist_info_name, installed_files)

        # Mark the installer so tools can see this was placed by mpip.
        installer_path = os.path.join(self.target, dist_info_name, "INSTALLER")
        with open(installer_path, "w", encoding="utf-8") as fh:
            fh.write("mpip\n")

        self.log(f"  installed {candidate.name}=={candidate.version} -> {self.target}")

    def _find_dist_info(self, zf: zipfile.ZipFile, candidate: Candidate) -> str:
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) >= 2 and parts[0].endswith(".dist-info"):
                return parts[0]
        raise InstallError(f"{candidate.filename}: no .dist-info directory in wheel")

    def _data_destination(self, name: str, data_prefix: str) -> str | None:
        # name = "pkg-1.0.data/<category>/<rest...>"
        remainder = name[len(data_prefix):]
        category, _, rest = remainder.partition("/")
        if not rest:
            return None
        if category in ("purelib", "platlib"):
            return os.path.join(self.target, rest)
        if category == "scripts":
            return os.path.join(self.scripts_dir, rest)
        if category in ("data", "headers"):
            # Install relative to the environment prefix (parent of bin/).
            prefix = os.path.dirname(self.scripts_dir)
            return os.path.join(prefix, category, rest)
        return os.path.join(self.target, rest)

    def _write(self, zf, name, dest, installed_files, *, is_script=False):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zf.open(name) as src, open(dest, "wb") as out:
            out.write(src.read())
        if is_script or dest.startswith(self.scripts_dir):
            _make_executable(dest)
        installed_files.append(dest)

    def _generate_console_scripts(self, zf, dist_info_name, installed_files):
        ep_path = f"{dist_info_name}/entry_points.txt"
        if ep_path not in zf.namelist():
            return
        text = zf.read(ep_path).decode("utf-8")
        parser = configparser.ConfigParser(delimiters=("=",))
        parser.optionxform = str
        try:
            parser.read_string(text)
        except configparser.Error:
            return
        for section in ("console_scripts", "gui_scripts"):
            if not parser.has_section(section):
                continue
            for script_name, target in parser.items(section):
                module, _, attr = target.partition(":")
                attr = attr.strip() or "main"
                # For "pkg.mod:obj.run" we import `obj` and call `obj.run()`.
                import_name = attr.split(".")[0]
                call_expr = attr
                script_path = os.path.join(self.scripts_dir, script_name.strip())
                os.makedirs(self.scripts_dir, exist_ok=True)
                content = _SCRIPT_TEMPLATE.format(
                    python=sys.executable,
                    module=module.strip(),
                    attr=import_name,
                    func=call_expr,
                )
                with open(script_path, "w", encoding="utf-8") as fh:
                    fh.write(content)
                _make_executable(script_path)
                installed_files.append(script_path)
                self.created_scripts.append(script_name.strip())


def _make_executable(path: str):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
