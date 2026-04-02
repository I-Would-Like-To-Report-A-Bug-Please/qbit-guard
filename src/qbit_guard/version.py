from __future__ import annotations

import os
import subprocess
from importlib.metadata import PackageNotFoundError, version as package_version


def get_version() -> str:
    """
    Resolve runtime version in a stable order:
    1) APP_VERSION env var (CI/CD override)
    2) Installed package metadata
    3) Git tag (source tree fallback)
    4) Static dev fallback
    """
    app_version = os.getenv("APP_VERSION", "").strip()
    if app_version:
        return app_version

    try:
        return package_version("qbit-guard")
    except PackageNotFoundError:
        pass
    except Exception:
        pass

    try:
        return (
            subprocess.check_output(
                ["git", "describe", "--tags", "--abbrev=0"],
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "0.0.0-dev"


VERSION = get_version()
