"""Plugin registry: signals, gates, sizers and reporters are registered by name and
built from YAML specs. Adding a plugin is one file plus one decorator, no engine edits.

    @register("sizer", "fixed")
    class FixedSizer(Plugin):
        class Params(PluginParams):
            contracts: PositiveInt

        def __init__(self, params: Params) -> None:
            self.contracts = params.contracts
"""

from collections.abc import Callable
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from nem.core.config import PluginSpec

Kind = Literal["signal", "gate", "sizer", "reporter"]


class PluginError(ValueError):
    pass


class PluginParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Plugin:
    Params: ClassVar[type[PluginParams]] = PluginParams
    name: ClassVar[str] = ""  # set by @register

    def __init__(self, params: PluginParams) -> None:
        del params


class Registry:
    def __init__(self) -> None:
        self._plugins: dict[Kind, dict[str, type[Plugin]]] = {}

    def register[T: Plugin](self, kind: Kind, name: str) -> Callable[[type[T]], type[T]]:
        def deco(cls: type[T]) -> type[T]:
            by_name = self._plugins.setdefault(kind, {})
            if name in by_name:
                raise PluginError(f"{kind} {name!r} already registered by {by_name[name]!r}")
            cls.name = name
            by_name[name] = cls
            return cls

        return deco

    def names(self, kind: Kind) -> list[str]:
        return sorted(self._plugins.get(kind, {}))

    def get(self, kind: Kind, name: str) -> type[Plugin]:
        try:
            return self._plugins.get(kind, {})[name]
        except KeyError:
            raise PluginError(
                f"unknown {kind} {name!r}; available: {self.names(kind) or 'none'}"
            ) from None

    def build[T: Plugin](self, kind: Kind, spec: PluginSpec, expected: type[T]) -> T:
        cls = self.get(kind, spec.type)
        try:
            params = cls.Params.model_validate(spec.params)
        except ValidationError as e:
            raise PluginError(f"{kind} {spec.type!r}: {e}") from e
        obj = cls(params)
        if not isinstance(obj, expected):
            raise PluginError(f"{kind} {spec.type!r} is not a {expected.__name__}")
        return obj


REGISTRY = Registry()
register = REGISTRY.register
build = REGISTRY.build
