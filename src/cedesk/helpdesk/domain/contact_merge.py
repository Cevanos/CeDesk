"""Contact merge (feature-gap plan 2.4), the rules that need no database.

The survivor keeps its id, name and email. From the duplicate it takes: every ticket,
rating, note and other address; its email as another address of the survivor; a phone,
organization or attribute value the survivor has no value for (the survivor's wins when
both have one: listed as a conflict, the duplicate's is dropped); VIP if either is; the
earlier "customer since".
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cedesk.helpdesk.domain.contact_fields import merge_values


@dataclass(frozen=True)
class Conflict:
    field: str  # name, phone, organization, or "attributes.<key>"
    kept: Any  # the survivor's
    dropped: Any  # the duplicate's


@dataclass(frozen=True)
class MergePlan:
    phone: str | None
    organization: str | None
    vip: bool
    customer_since: datetime
    attributes: dict[str, Any]
    taken: list[str]  # filled from the duplicate
    conflicts: list[Conflict]


def _blank(value: object) -> bool:
    return value is None or value == ""


def plan(survivor: Mapping[Any, Any], duplicate: Mapping[Any, Any]) -> MergePlan:
    taken: list[str] = []
    conflicts: list[Conflict] = []
    if survivor["name"] != duplicate["name"]:
        conflicts.append(Conflict("name", survivor["name"], duplicate["name"]))
    kept: dict[str, Any] = {}
    for key in ("phone", "organization"):
        mine, theirs = survivor.get(key), duplicate.get(key)
        if _blank(mine) and not _blank(theirs):
            kept[key] = theirs
            taken.append(key)
        else:
            kept[key] = mine
            if not _blank(theirs) and theirs != mine:
                conflicts.append(Conflict(key, mine, theirs))
    mine_attrs: Mapping[str, Any] = survivor.get("attributes") or {}
    theirs_attrs: Mapping[str, Any] = duplicate.get("attributes") or {}
    attributes, attr_taken, attr_conflicts = merge_values(mine_attrs, theirs_attrs)
    taken += [f"attributes.{k}" for k in attr_taken]
    conflicts += [
        Conflict(f"attributes.{k}", mine_attrs.get(k), theirs_attrs.get(k)) for k in attr_conflicts
    ]
    if duplicate["vip"] and not survivor["vip"]:
        taken.append("vip")
    return MergePlan(
        phone=kept["phone"],
        organization=kept["organization"],
        vip=bool(survivor["vip"] or duplicate["vip"]),
        customer_since=min(survivor["customer_since"], duplicate["customer_since"]),
        attributes=attributes,
        taken=taken,
        conflicts=conflicts,
    )
