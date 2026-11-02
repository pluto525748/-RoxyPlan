"""Explicit packaged entry for the user-observation, date-advancing edition."""
import sys

from modules.runtime_paths import (
    STUDY_VERSION, ensure_windowed_streams, report_startup_failure,
    resolve_runtime, show_startup_error,
)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ensure_windowed_streams()
    runtime = None
    try:
        if args == ["--version"]:
            print(STUDY_VERSION)
            return 0
        runtime = resolve_runtime("study", args)
        from frontend.pet_app import main as run_desktop
        return run_desktop(runtime=runtime, argv=["--open-chat"])
    except Exception as error:
        path = report_startup_failure(runtime, error)
        show_startup_error(error, path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
