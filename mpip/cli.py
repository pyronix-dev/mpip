"""Command-line interface for mpip."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .installer import Installer, default_target, user_target
from .pypi import DEFAULT_INDEX, PyPIClient, PyPIError
from .requirements import Requirement
from .resolver import Resolver, ResolutionError


def _read_requirements_file(path: str) -> list[str]:
    reqs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            reqs.append(line)
    return reqs


def _collect_requirements(args) -> list[str]:
    reqs = list(args.packages)
    for rf in args.requirement or []:
        reqs.extend(_read_requirements_file(rf))
    return reqs


def cmd_install(args) -> int:
    reqs = _collect_requirements(args)
    if not reqs:
        print("mpip: no packages given", file=sys.stderr)
        return 2

    client = PyPIClient(index_url=args.index_url)
    resolver = Resolver(
        client,
        allow_pre=args.pre,
        prefer_binary=True,
        log=print,
    )

    print(f"Resolving {len(reqs)} requirement(s)...")
    try:
        plan = resolver.resolve(reqs)
    except (ResolutionError, PyPIError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Resolved dependency graph:")
    for cand in plan:
        kind = "wheel" if cand.is_wheel else "sdist"
        print(f"  - {cand.name}=={cand.version} ({kind})")

    if args.dry_run:
        print("(dry run — nothing installed)")
        return 0

    import sysconfig
    from .installer import _user_scheme
    if args.target:
        target = os.path.abspath(args.target)
        scripts_dir = os.path.join(target, "bin")
    elif args.user:
        target = user_target()
        scripts_dir = sysconfig.get_path("scripts", scheme=_user_scheme())
    else:
        target = default_target()
        scripts_dir = sysconfig.get_path("scripts")

    installer = Installer(client, target, scripts_dir=scripts_dir, log=print,
                          force=args.force_reinstall)
    print(f"Installing into {target}")

    try:
        for cand in plan:
            installer.install(cand)
    except Exception as exc:  # noqa: BLE001 — surface any install failure cleanly
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Successfully installed {len(plan)} package(s).")
    return 0


def cmd_download(args) -> int:
    reqs = _collect_requirements(args)
    client = PyPIClient(index_url=args.index_url)
    resolver = Resolver(client, allow_pre=args.pre, log=print)
    try:
        plan = resolver.resolve(reqs)
    except (ResolutionError, PyPIError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    dest_dir = os.path.abspath(args.dest)
    os.makedirs(dest_dir, exist_ok=True)
    for cand in plan:
        print(f"downloading {cand.filename}")
        data = client.download(cand.url)
        with open(os.path.join(dest_dir, cand.filename), "wb") as fh:
            fh.write(data)
    print(f"Downloaded {len(plan)} file(s) to {dest_dir}")
    return 0


def cmd_show(args) -> int:
    client = PyPIClient(index_url=args.index_url)
    try:
        info = client.project(args.package).get("info", {})
    except PyPIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for field in ("name", "version", "summary", "author", "license", "home_page",
                  "requires_python"):
        value = info.get(field)
        if value:
            print(f"{field.replace('_', '-').title()}: {value}")
    reqs = info.get("requires_dist") or []
    if reqs:
        print("Requires-Dist:")
        for r in reqs:
            print(f"  {r}")
    return 0


def cmd_list(args) -> int:
    target = os.path.abspath(args.target) if args.target else default_target()
    rows = []
    try:
        for entry in sorted(os.listdir(target)):
            if entry.endswith(".dist-info"):
                base = entry[: -len(".dist-info")]
                if "-" in base:
                    name, ver = base.rsplit("-", 1)
                    rows.append((name, ver))
    except FileNotFoundError:
        pass
    if not rows:
        print("(no distributions found)")
        return 0
    width = max(len(n) for n, _ in rows)
    for name, ver in rows:
        print(f"{name.ljust(width)}  {ver}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mpip",
        description="mpip — a from-scratch Python package installer "
                    "(a pip-like tool that never calls pip).",
    )
    parser.add_argument("--version", action="version",
                        version=f"mpip {__version__}")
    parser.add_argument("--index-url", default=DEFAULT_INDEX,
                        help="Base URL of the PyPI JSON API "
                             f"(default: {DEFAULT_INDEX})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="Install packages and dependencies")
    p_install.add_argument("packages", nargs="*", help="Requirement specifiers")
    p_install.add_argument("-r", "--requirement", action="append",
                           help="Install from a requirements file")
    p_install.add_argument("-t", "--target", help="Install into a specific directory")
    p_install.add_argument("--user", action="store_true",
                           help="Install into the per-user site-packages")
    p_install.add_argument("--pre", action="store_true",
                           help="Allow pre-release versions")
    p_install.add_argument("--dry-run", action="store_true",
                           help="Resolve only; do not download or install")
    p_install.add_argument("--force-reinstall", action="store_true",
                           help="Reinstall even if already present")
    p_install.set_defaults(func=cmd_install)

    p_dl = sub.add_parser("download", help="Download distributions without installing")
    p_dl.add_argument("packages", nargs="*")
    p_dl.add_argument("-r", "--requirement", action="append")
    p_dl.add_argument("-d", "--dest", default=".", help="Download directory")
    p_dl.add_argument("--pre", action="store_true")
    p_dl.set_defaults(func=cmd_download)

    p_show = sub.add_parser("show", help="Show metadata for a package on the index")
    p_show.add_argument("package")
    p_show.set_defaults(func=cmd_show)

    p_list = sub.add_parser("list", help="List installed distributions")
    p_list.add_argument("-t", "--target", help="Directory to inspect")
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
