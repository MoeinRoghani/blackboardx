"""The package imports, and every name in its public surface resolves."""

import warnings

import blackboard


def test_package_imports() -> None:
    assert blackboard.__name__ == "blackboard"


def test_public_surface_is_declared_and_resolves() -> None:
    assert isinstance(blackboard.__all__, list)
    with warnings.catch_warnings():
        # A deprecated name is still part of the surface until it is removed.
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in blackboard.__all__:
            assert hasattr(blackboard, name)
