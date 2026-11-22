"""Entry point for ``python -m livecaption`` and the PyInstaller bundle."""

import sys

from livecaption.live_caption import main


if __name__ == "__main__":
    sys.exit(main())
