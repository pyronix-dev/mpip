"""A tiny PyPI client built on the standard library only.

Talks to the JSON API (https://warehouse.pypa.io/api-reference/json.html) to
list a project's releases and their downloadable files. No ``requests``, no
``pip`` — just ``urllib``.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from .requirements import canonical_name

DEFAULT_INDEX = "https://pypi.org/pypi"
_USER_AGENT = "mpip/0.1 (+https://github.com/)"


class PyPIError(RuntimeError):
    pass


class PyPIClient:
    def __init__(self, index_url: str = DEFAULT_INDEX, timeout: int = 30):
        self.index_url = index_url.rstrip("/")
        self.timeout = timeout
        self._ctx = ssl.create_default_context()
        self._cache: dict[str, dict] = {}

    def _get_json(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise PyPIError(f"not found on index: {url}") from exc
            raise PyPIError(f"HTTP {exc.code} fetching {url}") from exc
        except urllib.error.URLError as exc:
            raise PyPIError(f"network error fetching {url}: {exc.reason}") from exc

    def project(self, name: str) -> dict:
        """Return the full JSON metadata document for a project."""
        key = canonical_name(name)
        if key not in self._cache:
            url = f"{self.index_url}/{name}/json"
            self._cache[key] = self._get_json(url)
        return self._cache[key]

    def releases(self, name: str) -> dict[str, list]:
        """Map of version string -> list of file dicts."""
        return self.project(name).get("releases", {})

    def download(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                return resp.read()
        except urllib.error.URLError as exc:
            raise PyPIError(f"failed to download {url}: {exc}") from exc
