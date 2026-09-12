import os
import sys

from .core.runtime.paths import prepare_user_data_dir, resource_dir, user_data_dir
from .initialization.installation_manager import InstallationManager


def _prefer_sibling_streamget() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    sibling_streamget_root = os.path.normpath(os.path.join(project_root, "..", "Streamget"))
    sibling_streamget_package = os.path.join(sibling_streamget_root, "streamget")

    if os.path.isdir(sibling_streamget_package) and sibling_streamget_root not in sys.path:
        sys.path.insert(0, sibling_streamget_root)


_prefer_sibling_streamget()

prepare_user_data_dir()

execute_dir = str(user_data_dir)
resource_dir = str(resource_dir)

__all__ = ["InstallationManager", "execute_dir", "resource_dir"]
