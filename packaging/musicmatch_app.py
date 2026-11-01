"""Entry point of the packaged app. Opens the window; with --background, runs the
background service instead (that's how the app starts itself and its login item)."""

import sys

from musicmatch.service import BACKGROUND_FLAG

if __name__ == "__main__":
    if "uninstall" in sys.argv:
        from musicmatch.autostart import uninstall_windows_app

        uninstall_windows_app()
        sys.exit(0)
    if BACKGROUND_FLAG in sys.argv:
        from musicmatch.service import main

        sys.exit(main())
    from musicmatch.gui import main

    main()
