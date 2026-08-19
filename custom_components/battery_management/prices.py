"""Reading a forecast out of whatever price sensor a site happens to have.

Free of Home Assistant imports so it can be unit-tested anywhere.

There is deliberately no per-supplier support. Nord Pool, ENTSO-e, Tibber, Frank
and EnergyZero all publish upcoming prices, but each in its own shape, and a
site should be able to change supplier by pointing at a different sensor. So we
recognise the shapes rather than the integrations, and give up cleanly when we
recognise none - an empty forecast disables cheap-hour charging instead of
guessing at a price.

Exchange prices are enough to decide with: energy tax, VAT and supplier markup
are a near-flat per-kWh adder, so they shift every slot equally and leave the
cheap-to-expensive *ranking* unchanged. That is why this can be built and used
before a site is even on a dynamic contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

#: attribute names that hold a list of upcoming prices, most specific first
_LIST_KEYS = (
    "raw_today",
    "raw_tomorrow",
    "prices_today",
    "prices_tomorrow",
    "prices",
    "forecast",
    "data",
    "today",
    "tomorrow",
)
#: keys that carry the moment a slot starts
_START_KEYS = ("start", "datetime", "time", "from", "startsAt", "start_time", "hour")
#: keys that carry the price itself
_PRICE_KEYS = ("price", "value", "electricity_price", "total", "amount", "cost")
#: keys that carry the moment a slot ends
_END_KEYS = ("end", "till", "to", "end_time")

#: attributes that name "tomorrow", so bare number lists land on the right day
_TOMORROW_KEYS = ("raw_tomorrow", "prices_tomorrow", "tomorrow")


@dataclass(frozen=True)
class Slot:
    """One price period."""

    start: datetime
    end: datetime
    price: float

    def covers(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_price(value) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price


def _from_mapping(entry: dict) -> tuple[datetime, datetime | None, float] | None:
    start = next(
        (dt for key in _START_KEYS if (dt := _as_datetime(entry.get(key)))), None
    )
    price = next(
        (p for key in _PRICE_KEYS if (p := _as_price(entry.get(key))) is not None), None
    )
    if start is None or price is None:
        return None
    end = next((dt for key in _END_KEYS if (dt := _as_datetime(entry.get(key)))), None)
    return start, end, price


def _from_numbers(values: list, day_start: datetime) -> list[tuple[datetime, None, float]]:
    """A bare list of prices: 24 means hourly, 96 means quarter-hourly."""
    prices = [p for value in values if (p := _as_price(value)) is not None]
    if not prices:
        return []
    minutes = 24 * 60 / len(prices)
    return [
        (day_start + timedelta(minutes=minutes * index), None, price)
        for index, price in enumerate(prices)
    ]


def _close_gaps(raw: list[tuple[datetime, datetime | None, float]]) -> list[Slot]:
    """Fill in missing end times from the next slot's start."""
    raw = sorted(raw, key=lambda item: item[0])
    slots: list[Slot] = []
    for index, (start, end, price) in enumerate(raw):
        if end is None:
            if index + 1 < len(raw):
                end = raw[index + 1][0]
            else:
                # last slot: assume it is as long as the one before it
                previous = slots[-1] if slots else None
                span = (previous.end - previous.start) if previous else timedelta(hours=1)
                end = start + span
        if end > start:
            slots.append(Slot(start, end, price))
    return slots


def parse_forecast(attributes: dict, now: datetime) -> list[Slot]:
    """Upcoming price slots from a sensor's attributes; empty when unreadable."""
    if not attributes:
        return []

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    collected: list[tuple[datetime, datetime | None, float]] = []

    for key in _LIST_KEYS:
        values = attributes.get(key)
        if not isinstance(values, (list, tuple)) or not values:
            continue
        if isinstance(values[0], dict):
            collected.extend(
                parsed for entry in values if (parsed := _from_mapping(entry))
            )
        else:
            day = midnight + timedelta(days=1 if key in _TOMORROW_KEYS else 0)
            collected.extend(_from_numbers(list(values), day))

    # the same slot can appear under several attributes; keep one of each
    unique = {start: (start, end, price) for start, end, price in collected}
    return _close_gaps(list(unique.values()))


def slot_at(slots: list[Slot], moment: datetime) -> Slot | None:
    return next((slot for slot in slots if slot.covers(moment)), None)



def to_hourly(slots: list[Slot]) -> list[Slot]:
    """Fold sub-hourly slots into whole hours, averaging by how long each lasts.

    The Dutch market settles in 15-minute blocks, so a feed can publish 96 slots
    a day. Nothing in the ranking minds - `cheap_hours` is converted into a
    number of slots from whatever arrives - but 96 bars is a lot of bars, and
    somebody who only wants the shape of the day is better served by 24.

    Weighted by duration rather than a plain mean, so a partial hour at the end
    of a feed does not get the same say as a full one. An already-hourly feed
    passes through untouched, and the last hour keeps its real end rather than
    claiming a full one it has no prices for.
    """
    if not slots:
        return []
    buckets: dict[datetime, list[Slot]] = {}
    for slot in sorted(slots, key=lambda s: s.start):
        hour = slot.start.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour, []).append(slot)

    folded = []
    for hour, members in sorted(buckets.items()):
        spans = [(s.end - s.start).total_seconds() for s in members]
        total = sum(spans) or 1.0
        price = sum(s.price * span for s, span in zip(members, spans)) / total
        folded.append(
            Slot(
                start=min(s.start for s in members),
                end=max(s.end for s in members),
                price=price,
            )
        )
    return folded

def slots_in_window(
    slots: list[Slot], now: datetime, window_hours: float = 24.0
) -> list[Slot]:
    """The slots the ranking actually considers: still running, and near enough.

    Both rankings and the chart have to agree on this set, or a bar would be
    coloured by a decision it was never part of.
    """
    horizon = now + timedelta(hours=window_hours)
    return sorted(
        (slot for slot in slots if slot.end > now and slot.start < horizon),
        key=lambda slot: slot.start,
    )


def cheapest_slots(
    slots: list[Slot],
    now: datetime,
    cheap_hours: float,
    window_hours: float = 24.0,
    min_margin: float = 0.0,
) -> list[Slot]:
    """Slots worth *buying* on in the window ahead.

    Ranked over a rolling window rather than over everything the sensor knows:
    with today and tomorrow both published, ranking across 48 hours could decide
    that nothing today is worth charging on and leave the packs flat all evening.

    But a rank alone has no reference point, and that was a real fault. "The
    cheapest three of what is left" always finds three, however dear they are:
    at 22:00 with only today published it returned the single most expensive
    hour of the day and the dashboard called it cheap. Reported from the
    primary site, where the card offered to charge at 0.305 on a day whose
    cheapest hour had been 0.162.

    So `min_margin` adds the missing reference, and it is the economic one.
    Buying early only pays if it beats the dear hours it saves for by enough to
    cover the round trip - roughly 12 % of these packs, plus something for the
    wear. Below that margin nothing qualifies, which is the correct answer for
    a flat day and for the tail end of an expensive one.
    """
    if cheap_hours <= 0:
        return []
    upcoming = slots_in_window(slots, now, window_hours)
    if not upcoming:
        return []

    span_minutes = min(
        (slot.end - slot.start).total_seconds() / 60 for slot in upcoming
    )
    wanted = max(1, round(cheap_hours * 60 / span_minutes))
    ranked = sorted(upcoming, key=lambda slot: (slot.price, slot.start))
    picked = ranked[:wanted]

    if min_margin > 0:
        # what charging now would displace: the dearest hours still ahead. The
        # *cheapest* of those is the reference, because that is the weakest
        # hour the stored energy would actually be replacing.
        dearest = sorted(upcoming, key=lambda slot: -slot.price)[: max(1, wanted)]
        reference = min(slot.price for slot in dearest)
        picked = [slot for slot in picked if slot.price + min_margin <= reference]

    return sorted(picked, key=lambda slot: slot.start)


def dearest_slots(
    slots: list[Slot], now: datetime, hours: float, window_hours: float = 24.0
) -> list[Slot]:
    """The most expensive `hours` worth of slots in the window ahead."""
    if hours <= 0:
        return []
    upcoming = slots_in_window(slots, now, window_hours)
    if not upcoming:
        return []
    span_minutes = min(
        (slot.end - slot.start).total_seconds() / 60 for slot in upcoming
    )
    wanted = max(1, round(hours * 60 / span_minutes))
    ranked = sorted(upcoming, key=lambda slot: (-slot.price, slot.start))
    return sorted(ranked[:wanted], key=lambda slot: slot.start)


def is_dear_now(
    slots: list[Slot], now: datetime, hours: float, window_hours: float = 24.0
) -> bool:
    """Is the slot we are in one of the dearest ahead?"""
    current = slot_at(slots, now)
    if current is None:
        return False
    return current in dearest_slots(slots, now, hours, window_hours)


def is_cheap_now(
    slots: list[Slot],
    now: datetime,
    cheap_hours: float,
    window_hours: float = 24.0,
    min_margin: float = 0.0,
) -> bool:
    """Is the slot we are in one worth buying on?"""
    current = slot_at(slots, now)
    if current is None:
        return False
    return current in cheapest_slots(
        slots, now, cheap_hours, window_hours, min_margin
    )


def slots_to_buy(
    slots: list[Slot],
    now: datetime,
    cheap_hours: float,
    window_hours: float = 24.0,
    min_margin: float = 0.0,
    needed_hours: float | None = None,
) -> list[Slot]:
    """Of the hours cheap enough to buy on, the cheapest few actually needed.

    `cheapest_slots` answers "which hours are cheap enough". It does not answer
    "which of them will we use", and buying on whichever cheap hour comes round
    first spends the dearest of the set. Reported from the primary site on
    2026-08-19: dynamic mode was switched on at 12:20, the packs were full an
    hour later, and the day's cheapest hour arrived at 14:00 - unused, because
    12:00 was also in the cheapest four and it was the one that came first.

    The need is measured in hours of charging, which is what the empty-to-full
    time already gives. Rounded **up** to whole slots, and never below one: the
    packs draw what they draw, so half an hour of need still occupies an hour.

    `needed_hours` of None means the need is not knowable - the empty-to-full
    time has not been measured - and then every cheap hour qualifies, which is
    the behaviour this had before. A need of zero returns nothing: there is
    nothing to buy, and saying otherwise would paint hours on a dashboard that
    no charging is planned for.
    """
    candidates = cheapest_slots(slots, now, cheap_hours, window_hours, min_margin)
    if needed_hours is None or not candidates:
        return candidates
    if needed_hours <= 0:
        return []
    span_minutes = min(
        (slot.end - slot.start).total_seconds() / 60 for slot in candidates
    )
    wanted = max(1, ceil(needed_hours * 60 / span_minutes))
    ranked = sorted(candidates, key=lambda slot: (slot.price, slot.start))
    return sorted(ranked[:wanted], key=lambda slot: slot.start)
