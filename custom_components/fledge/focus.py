"""The Focus card (protocol 16): Pomodoro blocks and busy, its timer on the
Nest itself.

The card has an id of its own in the cards' order (FOCUS_ID, never a real
entity) and its settings in the entry's options (CONF_FOCUS). The timer
runs on the Nest, so a block carries on through a restart of either; the
Nest publishes it on <base>/focus (the Focus entities read it) and what
happens to it on <base>/focus/event, and takes commands on <base>/set/focus.

Busy can start by itself: while any of the card's "busy when" entities is
on (a laptop's camera or microphone in use, say), Home Assistant tells the
Nest it's busy, and once all of them have been off for a while, that it no
longer is. Busy started by hand is only ended by hand.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.const import STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import FOCUS_DOMAIN, FOCUS_EVENT_TOPIC, FOCUS_SET_TOPIC, FOCUS_STATE_TOPIC, TOPIC_PREFIX

FOCUS_ID = f"{FOCUS_DOMAIN}.focus"
SOUNDS = ("three", "one", "off")
COMMANDS = ("start", "pause", "resume", "toggle", "skip", "stop", "busy_on", "busy_off", "length")
# How long the busy entities stay off before busy ends, by default: a call
# dropped and joined again stays one call.
BUSY_END_AFTER = 60

SCHEMA = vol.Schema({
    vol.Optional("focus", default=25): vol.All(vol.Coerce(int), vol.Range(min=5, max=90)),
    vol.Optional("short_break", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
    vol.Optional("long_break", default=15): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
    vol.Optional("every", default=4): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
    vol.Optional("straight_on", default=False): bool,
    vol.Optional("keep_on", default=True): bool,
    vol.Optional("wake_to", default=True): bool,
    vol.Optional("sound", default="three"): vol.In(SOUNDS),
    vol.Optional("busy_when", default=[]): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional("busy_end_after", default=BUSY_END_AFTER): vol.All(vol.Coerce(int), vol.Range(min=0, max=900)),
    vol.Optional("busy_status", default=""): vol.All(str, vol.Length(max=30)),
})


def is_focus(entity_id: Any) -> bool:
    """Whether an id in the cards' order is the Focus card's."""
    return entity_id == FOCUS_ID


def settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The Focus card's settings with every default filled in."""
    try:
        return SCHEMA(dict(raw or {}))
    except vol.Invalid:
        return SCHEMA({})


def card(focus: dict[str, Any], given: str | None = None) -> dict[str, Any]:
    """The Focus card as the Nest takes it."""
    out = {"kind": "focus", "id": FOCUS_ID, "name": (given or "").strip() or "Focus", "icon": "mdi:timer-outline"}
    out.update({k: focus[k] for k in ("focus", "short_break", "long_break", "every", "straight_on", "keep_on", "wake_to", "sound")})
    return out


def order(command: str, minutes: int | None = None, auto: bool = False, status: str = "") -> str:
    """A command for the Nest's timer (<base>/set/focus)."""
    out: dict[str, Any] = {"command": command}
    if minutes is not None:
        out["minutes"] = int(minutes)
    if auto:
        out["auto"] = True
    if status:
        out["status"] = status
    return json.dumps(out)


class BusyWatch:
    """Tells the Nest it's busy while any of the card's busy entities is on,
    and no longer once all have been off for its settings' while."""

    def __init__(self, hass: HomeAssistant, serial: str, focus: dict[str, Any]) -> None:
        self.hass = hass
        self.topic = FOCUS_SET_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        self.entities: list[str] = list(focus["busy_when"])
        self.after = int(focus["busy_end_after"])
        self.status = focus["busy_status"]
        self.busy: bool | None = None
        self._unsubscribe: Any = None
        self._ending: Any = None

    def on(self) -> bool:
        return any((s := self.hass.states.get(e)) is not None and s.state == STATE_ON for e in self.entities)

    async def async_start(self) -> None:
        if not self.entities:
            return
        self._unsubscribe = async_track_state_change_event(self.hass, self.entities, self._changed)
        if self.on():
            await self._say(True)

    def async_stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
        self._cancel()

    def _cancel(self) -> None:
        if self._ending:
            self._ending()
            self._ending = None

    @callback
    def _changed(self, _event: Event) -> None:
        if self.on():
            self._cancel()
            if self.busy is not True:
                self.hass.async_create_task(self._say(True))
        elif self.busy and self._ending is None:
            self._ending = async_call_later(self.hass, timedelta(seconds=self.after), self._ended)

    @callback
    def _ended(self, _now: Any) -> None:
        self._ending = None
        if not self.on():
            self.hass.async_create_task(self._say(False))

    async def _say(self, busy: bool) -> None:
        self.busy = busy
        await mqtt.async_publish(self.hass, self.topic,
                                 order("busy_on" if busy else "busy_off", auto=True, status=self.status if busy else ""))


# What the Nest says of its timer, by the Focus sensor's states.
STATES = ("ready", "focus", "short_break", "long_break", "paused", "waiting", "busy")
EVENTS = ("focus_started", "focus_done", "break_started", "break_done", "paused", "resumed", "busy_started",
          "busy_ended", "stopped")


class Feed:
    """The Nest's timer as it last said, and what just happened to it, for
    the Focus entities; and its commands."""

    def __init__(self, hass: HomeAssistant, serial: str) -> None:
        self.hass = hass
        self.state_topic = FOCUS_STATE_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        self.event_topic = FOCUS_EVENT_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        self.set_topic = FOCUS_SET_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        self.state: dict[str, Any] | None = None
        self.listeners: list[Any] = []
        self.event_listeners: list[Any] = []
        self._unsubscribe: list[Any] = []

    async def async_start(self) -> None:
        self._unsubscribe.append(await mqtt.async_subscribe(self.hass, self.state_topic, self._state))
        self._unsubscribe.append(await mqtt.async_subscribe(self.hass, self.event_topic, self._event))

    def async_stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()

    @callback
    def _state(self, msg: Any) -> None:
        try:
            state = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(state, dict):
            return
        self.state = state
        for listener in list(self.listeners):
            listener()

    @callback
    def _event(self, msg: Any) -> None:
        try:
            event = json.loads(msg.payload).get("event")
        except (ValueError, TypeError, AttributeError):
            return
        if event in EVENTS:
            for listener in list(self.event_listeners):
                listener(event, self.state or {})

    async def async_send(self, command: str, minutes: int | None = None, status: str = "") -> None:
        await mqtt.async_publish(self.hass, self.set_topic, order(command, minutes, status=status))


class Following:
    """An entity that follows the Nest's timer (Feed): unavailable until the
    Nest has said, redrawn each time it does."""

    _attr_should_poll = False

    def _feed(self) -> Feed:
        return self.thermostat.focus_feed  # type: ignore[attr-defined]

    @property
    def said(self) -> dict[str, Any]:
        return self._feed().state or {}

    @property
    def available(self) -> bool:
        return self._feed().state is not None

    async def async_added_to_hass(self) -> None:
        feed = self._feed()
        feed.listeners.append(self._heard)
        self.async_on_remove(lambda: feed.listeners.remove(self._heard))  # type: ignore[attr-defined]

    @callback
    def _heard(self) -> None:
        self.async_write_ha_state()  # type: ignore[attr-defined]


def wanted(entry: Any) -> bool:
    """Whether a thermostat's cards have the Focus card: only then its entities."""
    return any(is_focus(e) for e in entry.options.get("entities", []))
