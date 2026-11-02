"""Windows Beta entry. Source Developer use continues through roxy.bat."""

import sys

from modules.runtime_paths import (
    VERSION, ensure_windowed_streams, report_startup_failure, resolve_runtime,
    show_startup_error,
)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ensure_windowed_streams()
    runtime = None
    try:
        if args == ["--version"]:
            print(VERSION)
            return 0
        runtime = resolve_runtime("beta", args)
        # Defer Qt imports until the data root has been validated. Import and
        # initialization errors also get a log in windowed builds.
        from frontend.pet_app import main as run_desktop
        return run_desktop(runtime=runtime, argv=[])
    except Exception as error:
        path = report_startup_failure(runtime, error)
        show_startup_error(error, path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
