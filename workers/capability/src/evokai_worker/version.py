"""The installed version, read from the package metadata."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("evokai-capability-worker")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+unknown"
