"""mpip — a small, dependency-free Python package installer.

mpip installs packages and their dependencies straight from the PyPI JSON API
using nothing but the Python standard library. It never imports or shells out to
pip; it resolves versions, downloads wheels, and unpacks them into
``site-packages`` itself.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
