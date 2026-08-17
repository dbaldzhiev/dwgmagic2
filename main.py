"""Application entrypoint for both the console and windowed executables.

Startup is wrapped so that a failure before the GUI exists still reaches the
user — see :mod:`dwgmagic.crashlog`.
"""
import sys

from dwgmagic import crashlog
from dwgmagic.cli import build_environment, run  # noqa: F401 - build_environment is re-exported


def main() -> None:
    crashlog.install()
    try:
        exit_code = run()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - the last line of defence before Windows eats it
        crashlog.report(*sys.exc_info())
        raise SystemExit(1) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
