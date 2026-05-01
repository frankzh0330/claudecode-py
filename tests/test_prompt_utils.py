"""Prompt utility helpers."""

from types import SimpleNamespace

from termpilot.prompt_utils import _patch_safe_application_exit


def test_safe_application_exit_ignores_already_stopped_application():
    def exit_raises(*args, **kwargs):
        raise Exception("Application is not running. Application.exit() failed.")

    app = SimpleNamespace(exit=exit_raises)

    _patch_safe_application_exit(app)

    assert app.exit(exception=KeyboardInterrupt) is None


def test_safe_application_exit_reraises_other_errors():
    def exit_raises(*args, **kwargs):
        raise RuntimeError("boom")

    app = SimpleNamespace(exit=exit_raises)

    _patch_safe_application_exit(app)

    try:
        app.exit()
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")
