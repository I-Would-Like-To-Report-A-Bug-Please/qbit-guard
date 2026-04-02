import subprocess
import os

def get_version():
    """Get version from git tags or fallback to default."""
    try:
        # Try to get version from the most recent git tag
        version = subprocess.check_output(['git', 'describe', '--tags', '--abbrev=0'], 
                                         stderr=subprocess.DEVNULL).decode('utf-8').strip()
        return version
    except (subprocess.CalledProcessError, FileNotFoundError):
        # If no git tag is available or not in a git repository
        # Check if version is set as an environment variable (useful for CI/CD)
        if 'APP_VERSION' in os.environ:
            return os.environ['APP_VERSION']
        # Fallback to a default version
        return "0.0.0-dev"

VERSION = get_version()