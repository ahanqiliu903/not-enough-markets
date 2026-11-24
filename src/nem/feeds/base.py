"""Feed plugin interface.

A feed fetches observations from one external source. The engine calls `fetch` on the
feed's schedule when running live and stores every value, so replay and backtests see
exactly what was known when. Signals and gates never call feeds directly; they read stored
values through `ctx.feed(name)`.

Writing a feed: return one `(key, value, known_at)` per observation. `known_at` must be
when the value became knowable: the source's publish/issue time if it has one, otherwise
the fetch time. Use keys to separate several series from one source, e.g.
`"2026-10-09/high"` for a forecast of tomorrow's high.
"""

from collections.abc import Sequence
from datetime import datetime

from nem.core.registry import Plugin, PluginParams

Observation = tuple[str, float, datetime]  # (key, value, known_at)


class Feed(Plugin):
    def fetch(self, now: datetime) -> Sequence[Observation]:
        raise NotImplementedError

    @classmethod
    def forecast_model(cls, params: PluginParams) -> str | None:
        """For forecast feeds: the model it carries, named as in `nem.backfill.SOURCES`
        (`gfs`, `nbm`, `ecmwf_ifs`, ...), so `ctx.forecasts` never counts one model twice
        (e.g. a live GFS MOS feed and the `iem_mos_gfs` backfill). None = its own source."""
        del params
        return None
