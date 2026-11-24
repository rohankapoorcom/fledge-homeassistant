"""Focus: what happens to the Focus card's timer, as it happens (a block
started, done, a break over, busy), for automations to trigger on."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import focus
from .entity import FledgeEntity


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    if focus.wanted(entry):
        add([FocusEvent(entry)])


class FocusEvent(FledgeEntity, EventEntity):
    _attr_event_types = list(focus.EVENTS)
    _attr_icon = "mdi:timer-alert-outline"
    _attr_should_poll = False

    def __init__(self, entry) -> None:
        super().__init__(entry, "focus_event")

    async def async_added_to_hass(self) -> None:
        feed = self.thermostat.focus_feed
        feed.event_listeners.append(self._happened)
        self.async_on_remove(lambda: feed.event_listeners.remove(self._happened))

    @callback
    def _happened(self, event: str, state: dict[str, Any]) -> None:
        self._trigger_event(event, {k: state.get(k) for k in ("block", "blocks", "today", "status", "auto") if k in state})
        self.async_write_ha_state()
