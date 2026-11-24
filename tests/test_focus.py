"""The Focus card: its settings, what the Nest is sent, its entities from
what the Nest says of its timer, its commands, busy from Home Assistant's
entities, the panel and the options flow."""

import json
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message, async_fire_time_changed

from custom_components.fledge import focus
from custom_components.fledge.config_flow import options_from, with_faces
from custom_components.fledge.const import DOMAIN, needs

SERIAL = "09AB01AC36230301"
CARDS = f"nestlocal/{SERIAL}/set/ui"
STATE = f"nestlocal/{SERIAL}/focus"
EVENT = f"nestlocal/{SERIAL}/focus/event"
SET = f"nestlocal/{SERIAL}/set/focus"
FOCUS = focus.FOCUS_ID


def test_the_focus_card_s_settings_and_card():
    chosen = focus.settings({"focus": 50, "sound": "one", "busy_when": "binary_sensor.camera"})
    assert chosen["focus"] == 50 and chosen["short_break"] == 5 and chosen["busy_when"] == ["binary_sensor.camera"]
    assert focus.settings({"focus": 500}) == focus.settings({}), "out of range: the defaults"
    card = focus.card(chosen, " Deep work ")
    assert card == {"kind": "focus", "id": FOCUS, "name": "Deep work", "icon": "mdi:timer-outline", "focus": 50,
                    "short_break": 5, "long_break": 15, "every": 4, "straight_on": False, "keep_on": True,
                    "wake_to": True, "sound": "one"}
    assert focus.card(chosen)["name"] == "Focus"
    assert needs([FOCUS, "light.lamp"]) == 16
    assert json.loads(focus.order("start", 50)) == {"command": "start", "minutes": 50}
    assert json.loads(focus.order("busy_on", auto=True, status="In a call")) == {
        "command": "busy_on", "auto": True, "status": "In a call"}


def test_the_options_flow_keeps_the_focus_card_and_its_settings():
    assert with_faces(["light.lamp"], [FOCUS, "light.lamp"]) == [FOCUS, "light.lamp"]
    options = options_from({"entities": [FOCUS], "focus": {"focus": 30}})
    assert options["focus"] == {"focus": 30}
    assert "focus" not in options_from({"entities": ["light.lamp"], "focus": {"focus": 30}}), "only while it's a card"


def _thermostat(hass: HomeAssistant) -> None:
    config = MockConfigEntry(domain="mqtt")
    config.add_to_hass(hass)
    thermostat = dr.async_get(hass).async_get_or_create(config_entry_id=config.entry_id, identifiers={("mqtt", SERIAL)},
                                                        name="Desk", serial_number=SERIAL)
    er.async_get(hass).async_get_or_create("climate", "mqtt", "nestlocal_thermostat", device_id=thermostat.id,
                                           suggested_object_id="nest_thermostat")


def _sent(mqtt_mock, topic: str) -> list[dict]:
    return [json.loads(c.args[1]) for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic]


def _entity(hass: HomeAssistant, domain: str, key: str) -> str:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{SERIAL}_{key}")


async def _setup(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    _thermostat(hass)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_the_focus_card_reaches_the_nest_and_its_entities_follow_the_timer(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp", "supported_color_modes": ["onoff"]})
    await _setup(hass, {"entities": [FOCUS, "light.lamp"], "wake": "chooser", "focus": {"focus": 30, "keep_on": False}})

    card = _sent(mqtt_mock, CARDS)[-1]["cards"][0]
    assert (card["kind"], card["id"], card["focus"], card["keep_on"]) == ("focus", FOCUS, 30, False)

    state = _entity(hass, "sensor", "focus")
    assert hass.states.get(state).state == "unavailable", "until the Nest has said"
    ends = 1_800_000_000
    async_fire_mqtt_message(hass, STATE, json.dumps({
        "state": "focus", "phase": "focus", "running": True, "ends": ends, "left": 1100, "length": 1800, "block": 2,
        "blocks": 4, "today": 1, "busy": True, "focus_minutes": 30, "status": "", "auto": False}))
    await hass.async_block_till_done()
    assert hass.states.get(state).state == "focus"
    assert hass.states.get(state).attributes["block"] == 2
    assert hass.states.get(_entity(hass, "sensor", "focus_ends")).state == dt_util.utc_from_timestamp(ends).isoformat()
    assert hass.states.get(_entity(hass, "sensor", "focus_blocks_today")).state == "1"
    assert hass.states.get(_entity(hass, "binary_sensor", "busy")).state == "on"
    assert hass.states.get(_entity(hass, "switch", "busy_mode")).state == "off", "focusing isn't busy mode"
    assert hass.states.get(_entity(hass, "number", "focus_length")).state == "30.0"

    event = _entity(hass, "event", "focus_event")
    async_fire_mqtt_message(hass, EVENT, json.dumps({"event": "focus_done"}))
    async_fire_mqtt_message(hass, EVENT, json.dumps({"event": "made_up"}))
    await hass.async_block_till_done()
    assert hass.states.get(event).attributes["event_type"] == "focus_done"

    await hass.services.async_call(DOMAIN, "focus", {"command": "start", "minutes": 50}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": _entity(hass, "switch", "busy_mode")}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": _entity(hass, "number", "focus_length"), "value": 45},
                                   blocking=True)
    assert _sent(mqtt_mock, SET) == [{"command": "start", "minutes": 50}, {"command": "busy_on"},
                                      {"command": "length", "minutes": 45}]


async def test_without_the_focus_card_it_has_no_entities(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp", "supported_color_modes": ["onoff"]})
    await _setup(hass, {"entities": ["light.lamp"], "wake": "chooser"})
    assert _entity(hass, "sensor", "focus") is None
    assert _entity(hass, "binary_sensor", "busy") is None


async def test_busy_follows_its_entities_and_ends_once_they_have_been_quiet(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("binary_sensor.camera", "off")
    hass.states.async_set("binary_sensor.mic", "off")
    await _setup(hass, {"entities": [FOCUS], "wake": "chooser", "focus": {
        "busy_when": ["binary_sensor.camera", "binary_sensor.mic"], "busy_end_after": 60, "busy_status": "In a call"}})
    hass.states.async_set("binary_sensor.camera", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.mic", "on")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock, SET) == [{"command": "busy_on", "auto": True, "status": "In a call"}], "once"
    hass.states.async_set("binary_sensor.camera", "off")
    hass.states.async_set("binary_sensor.mic", "off")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    hass.states.async_set("binary_sensor.mic", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.mic", "off")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=45))
    await hass.async_block_till_done()
    assert len(_sent(mqtt_mock, SET)) == 1, "a call dropped and joined again stays one"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=120))
    await hass.async_block_till_done()
    assert _sent(mqtt_mock, SET)[-1] == {"command": "busy_off", "auto": True}


async def test_the_panel_adds_and_saves_the_focus_card(hass: HomeAssistant, mqtt_mock, hass_ws_client) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp", "supported_color_modes": ["onoff"]})
    entry = await _setup(hass, {"entities": ["light.lamp"], "wake": "chooser"})
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "fledge/preview", "entry_id": entry.entry_id, "entities": [FOCUS, "light.lamp"],
                            "focus": {"focus": 40}})
    shown = (await client.receive_json())["result"]["cards"]
    assert shown[FOCUS]["kind"] == "focus" and shown[FOCUS]["focus"] == 40
    await client.send_json({"id": 2, "type": "fledge/save", "entry_id": entry.entry_id, "entities": [FOCUS, "light.lamp"],
                            "wake": "chooser", "focus": {"focus": 40, "busy_when": ["binary_sensor.camera"]}})
    result = await client.receive_json()
    assert result["success"], result
    await hass.async_block_till_done()
    assert entry.options["entities"] == [FOCUS, "light.lamp"]
    assert entry.options["focus"]["focus"] == 40 and entry.options["focus"]["busy_when"] == ["binary_sensor.camera"]
    await client.send_json({"id": 3, "type": "fledge/save", "entry_id": entry.entry_id, "entities": ["light.lamp"],
                            "wake": "chooser", "focus": {"focus": 50}})
    assert (await client.receive_json())["success"]
    await client.send_json({"id": 4, "type": "fledge/save", "entry_id": entry.entry_id, "entities": [FOCUS],
                            "wake": "chooser", "focus": {"focus": 500}})
    assert not (await client.receive_json())["success"], "settings out of range refused"
