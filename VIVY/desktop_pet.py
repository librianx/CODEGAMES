"""
Compatibility entrypoint.

The legacy `desktop_pet.py` script in this repo has had encoding/corruption issues on some
systems. The actively maintained implementation lives in `desktop_pet_dual_platform.py`.

Keep this file as the stable entrypoint for:
- `python VIVY/desktop_pet.py`
- PyInstaller specs/scripts that reference `desktop_pet.py`
"""

from __future__ import annotations

from desktop_pet_dual_platform import main


if __name__ == "__main__":
    main()

