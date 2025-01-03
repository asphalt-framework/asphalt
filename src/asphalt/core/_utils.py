from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from functools import partial
from importlib import import_module
from inspect import isclass
from typing import TYPE_CHECKING, Any, TypeVar, overload

if sys.version_info >= (3, 10):
    from importlib.metadata import entry_points
else:
    from importlib_metadata import entry_points

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup

if TYPE_CHECKING:
    from ._component import Component

T_Object = TypeVar("T_Object")


def resolve_reference(ref: object) -> Any:
    """
    Return the object pointed to by ``ref``.
    If ``ref`` is not a string or does not contain ``:``, it is returned as is.
    References must be in the form  <modulename>:<varname> where <modulename> is the
    fully qualified module name and varname is the path to the variable inside that
    module. For example, "concurrent.futures:Future" would give you the
    :class:`~concurrent.futures.Future` class.

    :raises LookupError: if the reference could not be resolved

    """
    if not isinstance(ref, str) or ":" not in ref:
        return ref

    modulename, rest = ref.split(":", 1)
    try:
        obj = import_module(modulename)
    except ImportError as e:
        raise LookupError(
            f"error resolving reference {ref}: could not import module"
        ) from e

    try:
        for name in rest.split("."):
            obj = getattr(obj, name)

        return obj
    except AttributeError:
        raise LookupError(f"error resolving reference {ref}: error looking up object")


def qualified_name(obj: object) -> str:
    """
    Return the qualified name (e.g. package.module.Type) for the given object.

    If ``obj`` is not a class, the returned name will match its type instead.

    """
    cls = obj if isclass(obj) else type(obj)
    if cls.__module__ == "builtins":
        return cls.__name__
    else:
        return f"{cls.__module__}.{cls.__qualname__}"


def callable_name(func: Callable[..., Any]) -> str:
    """Return the qualified name (e.g. package.module.func) for the given callable."""
    if isinstance(func, partial):
        func = func.func

    if func.__module__ == "builtins":
        return func.__name__
    else:
        return f"{func.__module__}.{func.__qualname__}"


def merge_config(
    original: Mapping[str, Any] | None, overrides: Mapping[str, Any] | None
) -> dict[str, Any]:
    """
    Return a copy of the ``original`` configuration dictionary, with overrides from
    ``overrides`` applied.

    This similar to what :meth:`dict.update` does, but when a dictionary is about to be
    replaced with another dictionary, it instead merges the contents.

    :param original: a configuration dictionary (or ``None``)
    :param overrides: a dictionary containing overriding values to the configuration
        (or ``None``)
    :return: the merge result

    .. versionchanged:: 5.0
        Previously, if a key in ``overrides`` was a dotted path (ie.
        ``foo.bar.baz: value``), it was assumed to be a shorthand for
        ``foo: {bar: {baz: value}}``. In v5.0, this feature was removed, as it turned
        out to be a problem with logging configuration, as it was not possible to
        configure any logging that had a dot in its name (as is the case with most
        loggers).

    """
    copied = dict(original) if original else {}
    if overrides:
        for key, value in overrides.items():
            orig_value = copied.get(key)
            if isinstance(orig_value, dict) and isinstance(value, dict):
                copied[key] = merge_config(orig_value, value)
            else:
                copied[key] = value

    return copied


@asynccontextmanager
async def coalesce_exceptions() -> AsyncIterator[None]:
    try:
        yield
    except ExceptionGroup as excgrp:
        if len(excgrp.exceptions) == 1 and not isinstance(
            excgrp.exceptions[0], ExceptionGroup
        ):
            raise excgrp.exceptions[0] from excgrp.exceptions[0].__cause__

        raise


def format_component_name(
    path: str,
    component_class: type[Component] | None = None,
    *,
    capitalize: bool = False,
) -> str:
    formatted = f"component {path!r}" if path else "the root component"
    if component_class is not None:
        formatted += f" ({qualified_name(component_class)})"

    if capitalize:
        formatted = formatted[0].upper() + formatted[1:]

    return formatted


class PluginContainer:
    """
    A convenience class for loading and instantiating plugins through the use of entry
    points.

    :param namespace: a setuptools entry points namespace
    :param base_class: the base class for plugins of this type (or ``None`` if the
        entry points don't point to classes)
    """

    __slots__ = "_entrypoints", "_resolved", "base_class", "namespace"

    def __init__(self, namespace: str, base_class: type | None = None) -> None:
        self.namespace: str = namespace
        self.base_class: type | None = base_class
        group = entry_points(group=namespace)
        self._entrypoints = {ep.name: ep for ep in group}
        self._resolved: dict[str, Any] = {}

    @overload
    def resolve(self, obj: str) -> Any:
        pass

    @overload
    def resolve(self, obj: T_Object) -> T_Object:
        pass

    def resolve(self, obj: Any) -> Any:
        """
        Resolve a reference to an entry point.

        If ``obj`` is a string, the named entry point is loaded from this container's
        namespace. Otherwise, ``obj`` is returned as is.

        :param obj: an entry point identifier, an object reference or an arbitrary
            object
        :return: the loaded entry point, resolved object or the unchanged input value
        :raises LookupError: if ``obj`` was a string but the named entry point was not
            found

        """
        if not isinstance(obj, str):
            return obj
        elif ":" in obj:
            return resolve_reference(obj)
        elif obj in self._resolved:
            return self._resolved[obj]

        value = self._entrypoints.get(obj)
        if value is None:
            raise LookupError(f"no such entry point in {self.namespace}: {obj}")

        value = self._resolved[obj] = value.load()
        return value

    def create_object(self, type: type | str, **constructor_kwargs: Any) -> Any:
        """
        Instantiate a plugin.

        The entry points in this namespace must point to subclasses of the
        ``base_class`` parameter passed to this container.

        :param type: an entry point identifier or an actual class object
        :param constructor_kwargs: keyword arguments passed to the constructor of the
            plugin class
        :return: the plugin instance

        """
        assert self.base_class, "base class has not been defined"
        plugin_class = self.resolve(type)
        if not isclass(plugin_class) or not issubclass(plugin_class, self.base_class):
            raise TypeError(
                f"{qualified_name(plugin_class)} is not a subclass of "
                f"{qualified_name(self.base_class)}"
            )

        return plugin_class(**constructor_kwargs)

    @property
    def names(self) -> list[str]:
        """Return names of all entry points in this namespace."""
        return list(self._entrypoints)

    def all(self) -> list[Any]:
        """
        Load all entry points (if not already loaded) in this namespace and return the
        resulting objects as a list.

        """
        values = []
        for name, ep in self._entrypoints.items():
            if name in self._resolved:
                value = self._resolved[name]
            else:
                value = self._resolved[name] = ep.load()

            values.append(value)

        return values

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(namespace={self.namespace!r}, "
            f"base_class={qualified_name(self.base_class)})"
        )
