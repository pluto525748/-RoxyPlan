"""Test-only ownership cleanup; never creates an application during import."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


class QtTestResourceError(RuntimeError):
    pass


# A failing test must not GC an owning widget while its QThread still runs.
# Retention is released on a later successful cleanup, never by force-stopping
# the thread. Tests are responsible for explicit worker completion/join.
_unsafe_widgets = []


def snapshot_top_level_widgets():
    if not isinstance(QApplication.instance(), QApplication):
        return ()
    return tuple(widget for widget in QApplication.topLevelWidgets() if isValid(widget))


def cleanup_new_top_level_widgets(baseline):
    """Dispose only this test's new windows, keeping application/baseline alive."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    baseline_ids = {id(widget) for widget in baseline}
    owned = [widget for widget in snapshot_top_level_widgets() if id(widget) not in baseline_ids]
    if not owned:
        return
    running = [
        thread
        for widget in owned
        for thread in widget.findChildren(QThread)
        if isValid(thread) and thread.isRunning()
    ]
    if running:
        retained_ids = {id(widget) for widget in _unsafe_widgets}
        _unsafe_widgets.extend(widget for widget in owned if id(widget) not in retained_ids)
        raise QtTestResourceError(
            f"Qt test left {len(running)} running QThread(s); owning windows were retained, "
            "not closed or destroyed. Finish and join test-owned workers before teardown."
        )

    callback_errors = []
    previous_hook = sys.excepthook

    def observe_callback_error(kind, error, traceback):
        callback_errors.append(error)
        previous_hook(kind, error, traceback)

    sys.excepthook = observe_callback_error
    try:
        for widget in owned:
            widget.close()
            widget.deleteLater()
        # Delete widget-owned timers/animations before draining arbitrary queued
        # events. A QWidget.close() alone only hides its native object.
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    finally:
        sys.excepthook = previous_hook
        _unsafe_widgets[:] = [widget for widget in _unsafe_widgets if isValid(widget)]

    if callback_errors:
        raise QtTestResourceError("Unhandled Qt callback error during resource cleanup") from callback_errors[0]
    if any(isValid(widget) for widget in owned):
        raise QtTestResourceError("DeferredDelete did not dispose all test-owned windows")


def test_cleanup_disposes_new_closed_widget_and_its_active_timer():
    app = QApplication.instance() or QApplication([])
    baseline = snapshot_top_level_widgets()
    widget = QWidget()
    timer = QTimer(widget)
    timer.start(60000)
    widget.close()
    assert isValid(widget) and timer.isActive()

    cleanup_new_top_level_widgets(baseline)

    assert not isValid(widget)
    assert not isValid(timer)
    assert QApplication.instance() is app


def test_cleanup_preserves_baseline_window_and_its_resources():
    _app = QApplication.instance() or QApplication([])
    baseline_window = QWidget()
    baseline_timer = QTimer(baseline_window)
    baseline_timer.start(60000)
    baseline = snapshot_top_level_widgets()
    new_window = QWidget()

    cleanup_new_top_level_widgets(baseline)

    assert isValid(baseline_window)
    assert baseline_timer.isActive()
    assert not isValid(new_window)


def test_running_thread_refuses_cleanup_without_stop_or_widget_destruction():
    _app = QApplication.instance() or QApplication([])
    baseline = snapshot_top_level_widgets()
    widget = QWidget()
    thread = QThread(widget)
    thread.start()
    try:
        with pytest.raises(QtTestResourceError, match="running QThread"):
            cleanup_new_top_level_widgets(baseline)
        assert isValid(widget)
        assert isValid(thread) and thread.isRunning()
    finally:
        thread.quit()
        assert thread.wait(2000), "regression test must explicitly join its own thread"
        cleanup_new_top_level_widgets(baseline)
    assert not isValid(widget)
    assert not isValid(thread)


def test_cleanup_reports_queued_callback_exception_and_restores_hook():
    _app = QApplication.instance() or QApplication([])
    baseline = snapshot_top_level_widgets()
    _widget = QWidget()
    previous_hook = sys.excepthook

    def callback():
        raise RuntimeError("deliberate isolated Qt callback failure")

    QTimer.singleShot(0, callback)
    with pytest.raises(QtTestResourceError, match="Unhandled Qt callback error") as caught:
        cleanup_new_top_level_widgets(baseline)
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert sys.excepthook is previous_hook
