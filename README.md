# mpip

**mpip** is a from-scratch Python package installer — a small, pip-like tool
that installs packages and all their dependencies straight from
[PyPI](https://pypi.org), **without using pip at all**.

It talks to the PyPI JSON API, resolves a dependency graph, downloads the right
wheels for your interpreter and platform, and unpacks them into `site-packages`
itself. It has **zero third-party dependencies** — only the Python standard
library (`urllib`, `zipfile`, `json`, …). No `pip`, no `packaging`, no
`requests`.

> ⚠️ mpip is an educational / utility reimplementation of the core install
> path. For production work, use `pip`. mpip is great for understanding how
> installation actually works, for constrained environments, or just for fun.

---

## Features

- **Installs from PyPI without pip** — nothing shells out to or imports `pip`.
- **Real dependency resolution** — walks `Requires-Dist`, honours version
  specifiers, extras (`requests[socks]`) and PEP 508 environment markers
  (`; python_version >= "3.9"`).
- **PEP 440 versions** — full ordering incl. pre/post/dev releases, epochs,
  and specifier operators `== != >= <= > < ~= ===` plus `==1.4.*` wildcards.
- **Correct wheel selection** — computes your interpreter's compatibility tags
  (CPython ABI, `abi3`, pure-python, macOS `arm64`/`universal2`, …) and picks
  the best matching wheel.
- **Console scripts** — generates working launchers from a wheel's
  `entry_points.txt`.
- **Integrity checks** — verifies the SHA-256 that PyPI publishes for each file.
- Extra commands: `download`, `show`, `list`, plus `--dry-run`, `--target`,
  `--user`, `--pre`, `--force-reinstall`.

## Install

mpip is a single pure-Python package with no dependencies. Clone it and either
run it in place or install it so you get the `mpip` command on your `PATH`:

```bash
git clone https://github.com/pyronix-dev/mpip.git
cd mpip

# run it straight from the clone, no install needed
python -m mpip --help
python -m mpip install requests

# or install it so `mpip` works as a command anywhere
pip install .        # or: pipx install .
```

> **Heads up:** don't run `mpip install mpip` — the name `mpip` on PyPI belongs
> to an unrelated project, so that would fetch *that* package, not this one.
> This mpip is installed from the clone with `pip install .` (above). If you
> install into a user/target directory, the generated `mpip` launcher goes to
> that scheme's `bin/`, which may not be on your `PATH` — mpip prints the exact
> `export PATH=...` line to fix it when that happens.

## Usage

```bash
# install a package and its dependencies
mpip install requests

# version specifiers, extras and multiple packages
mpip install "flask==3.0.*" "requests[socks]" rich

# just show the resolved graph, install nothing
mpip install --dry-run requests

# install into a specific directory (great for testing / vendoring)
mpip install --target ./vendor requests

# install into your per-user site-packages
mpip install --user httpx

# download wheels without installing
mpip download numpy -d ./wheels

# inspect a package on the index
mpip show flask

# list what's installed in a target
mpip list --target ./vendor
```

Equivalent module form works everywhere: `python -m mpip install ...`.

## How it works

```
requirement string ──▶ Resolver ──▶ install plan ──▶ Installer
  (PEP 508)              │             (Candidates)      │
                         │                               ├─ download wheel (urllib)
     ┌───────────────────┤                               ├─ verify sha256
     ▼                   ▼                               ├─ unpack zip → site-packages
  PyPIClient        markers + tags                       ├─ relocate *.data/{scripts,…}
  (JSON API)        (PEP 440 / 425)                      └─ generate console scripts
```

| Module            | Responsibility                                            |
|-------------------|-----------------------------------------------------------|
| `mpip/version.py` | PEP 440 versions, specifier sets, comparison operators    |
| `mpip/markers.py` | PEP 508 environment-marker tokenizer + evaluator          |
| `mpip/requirements.py` | Requirement + wheel-filename parsing                 |
| `mpip/tags.py`    | Compatibility tags for the running interpreter (PEP 425)  |
| `mpip/pypi.py`    | Minimal PyPI JSON-API client over `urllib`                |
| `mpip/resolver.py`| Greedy dependency resolution + wheel selection            |
| `mpip/installer.py`| Wheel unpacking, data relocation, script generation      |
| `mpip/cli.py`     | Argument parsing and subcommands                          |

## Limitations

- **Wheels only.** If a project publishes no compatible wheel (source-only),
  mpip reports it rather than building an sdist — building requires a full build
  backend, which is out of scope.
- **Greedy resolver.** It doesn't backtrack across conflicting version
  constraints the way pip's resolver does; it reports the conflict instead.
- No editable installs, no VCS/URL requirements, no lockfiles.

## Development

```bash
python -m unittest discover -s tests -v
```

The tests are offline (no network) and cover version ordering, specifier and
marker evaluation, requirement/wheel parsing and tag generation.

## License

MIT — see [LICENSE](LICENSE).
