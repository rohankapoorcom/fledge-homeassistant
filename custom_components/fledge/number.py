"""Focus length: the length of the Focus card's next focus block, as its
ring sets it while ready."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import focus
from .entity import FledgeEntity


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    if focus.wanted(entry):
        add([FocusLength(entry)])


class FocusLength(focus.Following, FledgeEntity, NumberEntity):
    _attr_icon = "mdi:timer-cog-outline"
    _attr_native_min_value = 5
    _attr_native_max_value = 90
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry) -> None:
        super().__init__(entry, "focus_length")

    @property
    def native_value(self) -> float | None:
        minutes = self.said.get("focus_minutes")
        return float(minutes) if isinstance(minutes, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.thermostat.focus_feed.async_send("length", int(value))
