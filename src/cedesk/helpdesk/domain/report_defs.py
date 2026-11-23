"""Report definitions (feature-gap plan 3.1, 3.2): what a custom or built-in report asks for,
checked before any SQL runs. Nothing here touches a database.

A definition is `{metric, group_by, filters, range}`:

- **metric** is one number: a *period* metric counts what happened in the range (created,
  resolved, first response, resolution, CSAT, reopen rate, time logged); `open_now` is how
  many are open *now* (the range doesn't apply: it can't be grouped by time); `backlog` is
  open and pending at the end of each past day, from the daily snapshots (grouped by day
  only, filtered by team only).
- **group_by** is one field: by time (day, week, month, in the workspace's time zone) or by a
  ticket field. A ticket counts once per group, except under `tag` (it counts in each of
  its tags, so the groups add up to more than the total).
- **filters** only use the keys in `FILTER_KEYS`; any other key is refused, so nothing a
  person types ever reaches SQL: the repo maps each allowed key to fixed SQL.
- **range** is a preset (`last_30d`, `this_month`, ...), re-worked at every run so a saved
  report never goes stale, or an absolute `from`/`to` (an ad-hoc run).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from cedesk.helpdesk.domain import reports as rules
from cedesk.helpdesk.domain.tickets import Channel, Priority, Sentiment

PERIOD, NOW, BACKLOG = "period", "now", "backlog"


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str  # count, minutes, percent or score
    kind: str  # PERIOD, NOW or BACKLOG


METRICS: dict[str, MetricSpec] = {
    m.key: m
    for m in (
        MetricSpec("created", "Tickets created", "count", PERIOD),
        MetricSpec("resolved", "Tickets resolved", "count", PERIOD),
        MetricSpec("avg_first_response", "Average first response", "minutes", PERIOD),
        MetricSpec("avg_resolution", "Average resolution time", "minutes", PERIOD),
        MetricSpec("csat_average", "CSAT average", "score", PERIOD),
        MetricSpec("csat_count", "CSAT ratings", "count", PERIOD),
        MetricSpec("reopen_rate", "Reopen rate", "percent", PERIOD),
        MetricSpec("time_logged", "Time logged", "minutes", PERIOD),
        MetricSpec("open_now", "Open now", "count", NOW),
        MetricSpec("backlog", "Open and pending at the end of the day", "count", BACKLOG),
    )
}

TIME_GROUPS = ("day", "week", "month")
FIELD_GROUPS = (
    "team", "assignee", "priority", "type", "channel", "tag", "topic", "sentiment",
)  # fmt: skip
GROUP_BYS = ("none", *TIME_GROUPS, *FIELD_GROUPS)

FILTER_KEYS = (
    "team", "assignee", "priority", "type", "channel", "tag", "topic", "sentiment", "imported",
)  # fmt: skip
IMPORTED_CHOICES = ("any", "exclude", "only")

PRESET_RANGES = ("last_7d", "last_30d", "last_90d", "last_365d", "this_month", "last_month")
DEFAULT_RANGE = "last_30d"

MAX_GROUPS = 50  # a grouped answer keeps this many groups, the biggest first
TEXT_MAX = 60


class DefinitionError(ValueError):
    """The message says what is wrong, for the person who wrote the definition."""


@dataclass(frozen=True)
class Range:
    preset: str | None = None
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class Definition:
    metric: str
    group_by: str = "none"
    filters: Mapping[str, Any] = field(default_factory=dict)
    range: Range = field(default_factory=lambda: Range(preset=DEFAULT_RANGE))

    @property
    def spec(self) -> MetricSpec:
        return METRICS[self.metric]

    def stored(self) -> dict[str, Any]:
        """What is saved: a preset range is kept as the preset, an absolute one as ISO times."""
        rng: dict[str, Any]
        if self.range.preset:
            rng = {"preset": self.range.preset}
        else:
            assert self.range.start is not None and self.range.end is not None
            rng = {"from": self.range.start.isoformat(), "to": self.range.end.isoformat()}
        return {
            "metric": self.metric,
            "group_by": self.group_by,
            "filters": dict(self.filters),
            "range": rng,
        }


def _one_of(name: str, value: Any, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise DefinitionError(f"{name} is one of: {', '.join(allowed)}.")
    return value


def _uuid(name: str, value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise DefinitionError(f"The {name} filter must be an id.") from exc


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= TEXT_MAX:
        raise DefinitionError(f"The {name} filter is 1-{TEXT_MAX} characters.")
    return value.strip()


def _filters(raw: Any, spec: MetricSpec) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise DefinitionError("filters is an object.")
    unknown = sorted(set(raw) - set(FILTER_KEYS))
    if unknown:
        raise DefinitionError(
            f"A report can't filter on {', '.join(unknown)}. Use: {', '.join(FILTER_KEYS)}."
        )
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if key in ("team", "assignee", "type"):
            out[key] = _uuid(key, value)
        elif key == "priority":
            out[key] = _one_of(key, value, tuple(p.value for p in Priority))
        elif key == "channel":
            out[key] = _one_of(key, value, tuple(c.value for c in Channel))
        elif key == "sentiment":
            out[key] = _one_of(key, value, tuple(s.value for s in Sentiment))
        elif key == "imported":
            out[key] = _one_of(key, value, IMPORTED_CHOICES)
        else:  # tag, topic
            out[key] = _text(key, value)
    if spec.kind == BACKLOG and set(out) - {"team"}:
        raise DefinitionError("The backlog can only be filtered by team.")
    return out


def _time(name: str, value: Any) -> datetime:
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise DefinitionError(f"{name} is a date and time with a time zone.") from exc
    if at.tzinfo is None:
        raise DefinitionError(f"{name} needs a time zone, e.g. 2026-11-01T00:00:00+05:30.")
    return at


def _range(raw: Any) -> Range:
    if raw is None:
        return Range(preset=DEFAULT_RANGE)
    if not isinstance(raw, Mapping):
        raise DefinitionError("range is an object.")
    unknown = sorted(set(raw) - {"preset", "from", "to"})
    if unknown:
        raise DefinitionError(f"A range has preset, or from and to; not {', '.join(unknown)}.")
    if "preset" in raw:
        if "from" in raw or "to" in raw:
            raise DefinitionError("Give a preset, or from and to, not both.")
        return Range(preset=_one_of("preset", raw["preset"], PRESET_RANGES))
    if "from" not in raw or "to" not in raw:
        raise DefinitionError("A range has a preset, or both from and to.")
    start, end = _time("from", raw["from"]), _time("to", raw["to"])
    try:
        rules.period(start, end, datetime.now(UTC))
    except rules.BadPeriod as exc:
        raise DefinitionError(str(exc)) from exc
    return Range(start=start, end=end)


def parse(raw: Any) -> Definition:
    """A definition from JSON, or `DefinitionError`."""
    if not isinstance(raw, Mapping):
        raise DefinitionError("A report definition is an object.")
    unknown = sorted(set(raw) - {"metric", "group_by", "filters", "range"})
    if unknown:
        raise DefinitionError(
            f"A definition has metric, group_by, filters, range; not {unknown[0]}."
        )
    metric = _one_of("metric", raw.get("metric"), tuple(METRICS))
    spec = METRICS[metric]
    group_by = _one_of("group_by", raw.get("group_by", "none"), GROUP_BYS)
    if spec.kind == NOW and group_by in TIME_GROUPS:
        raise DefinitionError(
            f"{metric} is how many are open now, so it can't be grouped by {group_by}. "
            "Use the backlog metric for open tickets over time."
        )
    if spec.kind == BACKLOG and group_by != "day":
        raise DefinitionError("The backlog is grouped by day.")
    return Definition(
        metric=metric,
        group_by=group_by,
        filters=_filters(raw.get("filters"), spec),
        range=_range(raw.get("range")),
    )


# The range ------------------------------------------------------------------------------


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone)


def resolve_range(rng: Range, now: datetime, timezone: str) -> tuple[datetime, datetime]:
    """[start, end) for a definition's range at `now`, in the workspace's time zone. A
    preset is whole local days, ending now."""
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    if rng.preset is None:
        assert rng.start is not None and rng.end is not None
        return rules.period(rng.start, rng.end, now)
    days = {"last_7d": 7, "last_30d": 30, "last_90d": 90, "last_365d": 365}
    if rng.preset in days:
        start = _midnight(today - timedelta(days=days[rng.preset] - 1), zone)
        return start, now
    first = today.replace(day=1)
    if rng.preset == "this_month":
        return _midnight(first, zone), now
    previous = (first - timedelta(days=1)).replace(day=1)  # last_month
    return _midnight(previous, zone), _midnight(first, zone)


def bucket_start(day: date, bucket: str) -> date:
    """The first local day of the day, ISO week (Monday) or month `day` is in."""
    if bucket == "week":
        return day - timedelta(days=day.weekday())
    if bucket == "month":
        return day.replace(day=1)
    return day


def _next(day: date, bucket: str) -> date:
    if bucket == "day":
        return day + timedelta(days=1)
    if bucket == "week":
        return day + timedelta(days=7)
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def buckets(start: datetime, end: datetime, timezone: str, bucket: str) -> list[date]:
    """Every bucket start from the one holding `start` to the one holding the last instant
    of the range, in the workspace's time zone: the zero-filled axis of a time series."""
    zone = ZoneInfo(timezone)
    first = bucket_start(start.astimezone(zone).date(), bucket)
    last = bucket_start((end - timedelta(microseconds=1)).astimezone(zone).date(), bucket)
    out = []
    day = first
    while day <= last:
        out.append(day)
        day = _next(day, bucket)
    return out


# Built-in reports -----------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    definition: Mapping[str, Any]


def _p(key: str, label: str, metric: str, group_by: str) -> Preset:
    return Preset(key, label, {"metric": metric, "group_by": group_by})


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in (
        _p("created_per_day", "Tickets created per day", "created", "day"),
        _p("resolved_per_day", "Tickets resolved per day", "resolved", "day"),
        _p("first_response_over_time", "First response over time", "avg_first_response", "week"),
        _p("resolution_over_time", "Resolution time over time", "avg_resolution", "week"),
        _p("csat_trend", "CSAT trend", "csat_average", "week"),
        _p("reopen_rate_over_time", "Reopen rate over time", "reopen_rate", "week"),
        _p("backlog_by_day", "Backlog by day", "backlog", "day"),
        _p("created_by_tag", "Created by tag", "created", "tag"),
        _p("created_by_type", "Created by ticket type", "created", "type"),
        _p("created_by_priority", "Created by priority", "created", "priority"),
        _p("created_by_team", "Created by team", "created", "team"),
        _p("created_by_channel", "Created by channel", "created", "channel"),
        _p("open_by_priority", "Open now by priority", "open_now", "priority"),
        _p("open_by_type", "Open now by ticket type", "open_now", "type"),
        _p("open_by_team", "Open now by team", "open_now", "team"),
        _p("open_by_channel", "Open now by channel", "open_now", "channel"),
        _p("first_response_by_team", "First response by team", "avg_first_response", "team"),
        _p("resolution_by_team", "Resolution time by team", "avg_resolution", "team"),
        _p("csat_by_team", "CSAT by team", "csat_average", "team"),
    )
}


def preset_definition(key: str) -> Definition:
    if key not in PRESETS:
        raise DefinitionError(f"No built-in report called {key}.")
    return parse(PRESETS[key].definition)
