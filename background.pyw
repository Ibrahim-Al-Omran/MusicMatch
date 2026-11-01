"""Started at login: runs MusicMatch in the background with a tray / menu-bar icon."""

import sys

from musicmatch.service import main

sys.exit(main())
