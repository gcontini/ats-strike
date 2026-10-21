"""Entry script for the frozen executable.

Separate from ``cv_client/__main__.py`` because PyInstaller runs its
entry script as a top-level module, where that file's relative import has no
package to resolve against.
"""

from cv_client.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
