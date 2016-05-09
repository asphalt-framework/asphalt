from importlib import import_module
from typing import Any, Union, List, Dict

from pkg_resources import EntryPoint, iter_entry_points
from typeguard import check_argument_types

__all__ = ('resolve_reference', 'qualified_name', 'merge_config', 'PluginContainer')


def resolve_reference(ref):
    """
    Return the object pointed to by ``ref``.

    If ``ref`` is not a string or does not contain ``:``, it is returned as is.

    References must be in the form  <modulename>:<varname> where <modulename> is the fully
    qualified module name and varname is the path to the variable inside that module.

    For example, "concurrent.futures:Future" would give you the
    :class:`~concurrent.futures.Future` class.

    :raises LookupError: if the reference could not be resolved

    """
    if not isinstance(ref, str) or ':' not in ref:
        return ref

    modulename, rest = ref.split(':', 1)
    try:
        obj = import_module(modulename)
    except ImportError as e:
        raise LookupError(
            'error resolving reference {}: could not import module'.format(ref)) from e

    try:
        for name in rest.split('.'):
            obj = getattr(obj, name)
        return obj
    except AttributeError:
        raise LookupError('error resolving reference {}: error looking up object'.format(ref))


def qualified_name(obj) -> str:
    """Return the qualified name (e.g. package.module.Type) for the given object."""
    try:
        module = obj.__module__
        qualname = obj.__qualname__
    except AttributeError:
        type_ = type(obj)
        module = type_.__module__
        qualname = type_.__qualname__

    return qualname if module in ('typing', 'builtins') else '{}.{}'.format(module, qualname)


def merge_config(original: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of the ``original`` configuration dictionary, with overrides from ``overrides``
    applied.

    This similar to what :meth:`dict.update` does, but when a dictionary is about to be
    replaced with another dictionary, it instead merges the contents.

    If a key in ``overrides`` is a dotted path (ie. ``foo.bar.baz: value``), it is assumed to be a
    shorthand for ``foo: {bar: {baz: value}}``.

    :param original: a configuration dictionary
    :param overrides: a dictionary containing overriding values to the configuration
    :return: the merge result

    """
    assert check_argument_types()
    copied = original.copy()
    for key, value in overrides.items():
        if '.' in key:
            key, rest = key.split('.', 1)
            value = {rest: value}

        orig_value = copied.get(key)
        if isinstance(orig_value, dict) and isinstance(value, dict):
            copied[key] = merge_config(orig_value, value)
        else:
            copied[key] = value

    return copied


class PluginContainer:
    """
    A convenience class for loading and instantiating plugins through the use of entry points.

    :param namespace: a setuptools entry points namespace
    :param base_class: the base class for plugins of this type (or ``None`` if the entry points
        don't point to classes)
    """

    __slots__ = 'namespace', 'base_class', '_entrypoints'

    def __init__(self, namespace: str, base_class: type=None):
        self.namespace = namespace
        self.base_class = base_class
        self._entrypoints = {ep.name: ep for ep in iter_entry_points(namespace)}

    def resolve(self, obj):
        """
        Resolve a reference to an entry point or a variable in a module.

        If ``obj`` is a ``module:varname`` reference to an object, :func:`resolve_reference` is
        used to resolve it. If it is a string of any other kind, the named entry point is loaded
        from this container's namespace. Otherwise, ``obj`` is returned as is.

        :param obj: an entry point identifier, an object reference or an arbitrary object
        :return: the loaded entry point, resolved object or the unchanged input value
        :raises LookupError: if ``obj`` was a string but the named entry point was not found

        """
        if not isinstance(obj, str):
            return obj
        if ':' in obj:
            return resolve_reference(obj)

        value = self._entrypoints.get(obj)
        if value is None:
            raise LookupError('no such entry point in {}: {}'.format(self.namespace, obj))

        if isinstance(value, EntryPoint):
            value = self._entrypoints[obj] = value.load()

        return value

    def create_object(self, type: Union[type, str], **constructor_kwargs):
        """
        Instantiate a plugin.

        The entry points in this namespace must point to subclasses of the ``base_class`` parameter
        passed to this container.

        :param type: an entry point identifier, a ``module:varname`` reference to a class, or an
            actual class object
        :param constructor_kwargs: keyword arguments passed to the constructor of the plugin class
        :return: the plugin instance

        """
        assert check_argument_types()
        assert self.base_class, 'base class has not been defined'
        plugin_class = self.resolve(type)
        if not issubclass(plugin_class, self.base_class):
            raise TypeError('{} is not a subclass of {}'.format(
                qualified_name(plugin_class), qualified_name(self.base_class)))

        return plugin_class(**constructor_kwargs)

    @property
    def names(self) -> List[str]:
        """Return names of all entry points in this namespace."""
        return list(self._entrypoints)

    def all(self) -> List[Any]:
        """
        Load all entry points (if not already loaded) in this namespace and return the resulting
        objects as a list.

        """
        values = []
        for name, value in self._entrypoints.items():
            if isinstance(value, EntryPoint):
                value = self._entrypoints[name] = value.load()

            values.append(value)

        return values

    def __repr__(self):
        return ('{0.__class__.__name__}(namespace={0.namespace!r}, base_class={1})'
                .format(self, qualified_name(self.base_class)))
