"""inference-vitals: one screen for running and fine-tuning AI models on your own machine."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("inference-vitals")
except importlib.metadata.PackageNotFoundError:  # run from a folder, not installed
    __version__ = "dev"
