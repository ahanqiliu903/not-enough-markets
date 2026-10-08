import pytest
from pydantic import PositiveInt

from nem.core.config import PluginSpec
from nem.core.registry import Plugin, PluginError, PluginParams, Registry


class Sizer(Plugin):
    pass


class FixedSizer(Sizer):
    class Params(PluginParams):
        contracts: PositiveInt

    def __init__(self, params: Params) -> None:
        self.contracts = params.contracts


@pytest.fixture
def reg() -> Registry:
    reg = Registry()
    reg.register("sizer", "fixed")(FixedSizer)
    return reg


def spec(**kw: object) -> PluginSpec:
    return PluginSpec.model_validate(kw)


def test_build(reg: Registry) -> None:
    sizer = reg.build("sizer", spec(type="fixed", contracts=5), FixedSizer)
    assert sizer.name == "fixed"
    assert sizer.contracts == 5


def test_unknown_name_lists_available(reg: Registry) -> None:
    with pytest.raises(PluginError, match=r"unknown sizer 'kelly'; available: \['fixed'\]"):
        reg.build("sizer", spec(type="kelly"), Sizer)
    with pytest.raises(PluginError, match="available: none"):
        reg.build("gate", spec(type="canary"), Plugin)


@pytest.mark.parametrize("params", [{}, {"contracts": 0}, {"contracts": 5, "extra": 1}], ids=str)
def test_bad_params_rejected(reg: Registry, params: dict[str, object]) -> None:
    with pytest.raises(PluginError, match="sizer 'fixed'"):
        reg.build("sizer", spec(type="fixed", **params), Sizer)


def test_duplicate_registration_rejected(reg: Registry) -> None:
    with pytest.raises(PluginError, match="already registered"):
        reg.register("sizer", "fixed")(Sizer)


def test_same_name_different_kind_ok(reg: Registry) -> None:
    reg.register("gate", "fixed")(Plugin)
    assert reg.names("gate") == ["fixed"]


def test_wrong_base_class_rejected(reg: Registry) -> None:
    class Gate(Plugin):
        pass

    with pytest.raises(PluginError, match="not a Gate"):
        reg.build("sizer", spec(type="fixed", contracts=1), Gate)
