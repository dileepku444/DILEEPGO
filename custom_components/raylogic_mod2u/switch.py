"""Raylogic MOD2U - 2 channel WiFi smart switch (relay) platform for Home Assistant.

MOD2U ek RELAY device hai (dimmer nahi), isliye ise `switch` platform ke
through expose kiya gaya hai - sirf ON/OFF, koi brightness slider nahi.

configuration.yaml mein ek ya zyada MOD2U devices ki 'devices' list do (har
ek ke liye sirf ip aur port zaroori hai - area/device_id/name optional hain,
default values aapke Docklight capture se already set hain):

    switch:
      - platform: raylogic_mod2u
        devices:
          - name: "MOD2U Switch"
            ip: 192.168.120.101
            port: 5550

Har device ke 2 channels (Channel 1, Channel 2) khud ban jaate hain.
"""
from __future__ import annotations

import logging

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.components.switch import PLATFORM_SCHEMA, SwitchEntity
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import protocol

_LOGGER = logging.getLogger(__name__)

CONF_DEVICES = "devices"
CONF_IP = "ip"
CONF_PORT = "port"
CONF_AREA = "area"
CONF_DEVICE_ID = "device_id"

DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IP): cv.string,
        vol.Required(CONF_PORT): cv.port,
        vol.Optional(CONF_NAME, default=""): cv.string,
        # Sirf tab override karo agar dusre MOD2U device ka Docklight capture
        # alag area/ID dikhaye - warna default (0C / 101) hi kaafi hai.
        vol.Optional(CONF_AREA, default=protocol.DEFAULT_AREA): cv.string,
        vol.Optional(CONF_DEVICE_ID, default=protocol.DEFAULT_DEVICE_ID): cv.positive_int,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_DEVICES): vol.All(cv.ensure_list, [DEVICE_SCHEMA]),
    }
)


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Har configured MOD2U device ke liye 2 channel (relay) entities banao."""
    entities: list[RaylogicRelayChannel] = []

    for device_conf in config[CONF_DEVICES]:
        ip = device_conf[CONF_IP]
        port = device_conf[CONF_PORT]
        area = device_conf[CONF_AREA]
        device_id = device_conf[CONF_DEVICE_ID]
        panel_name = device_conf[CONF_NAME] or f"Raylogic MOD2U {ip}"

        device = protocol.get_device(ip, port, area=area, device_id=device_id, name=panel_name)
        # Background receive-loop + keepalive start karo taaki mobile app se
        # hone wale ON/OFF ka feedback bhi turant HA mein sync ho.
        device.ensure_started()

        for channel in range(1, protocol.CHANNELS_PER_DEVICE + 1):
            entity_name = f"{panel_name} Channel {channel}"
            entities.append(RaylogicRelayChannel(entity_name, device, channel))

        _LOGGER.info(
            "Raylogic MOD2U [%s] %s:%s - %s channels ready",
            panel_name, ip, port, protocol.CHANNELS_PER_DEVICE,
        )

    add_entities(entities, True)


class RaylogicRelayChannel(SwitchEntity):
    """Ek MOD2U relay channel = ek HA switch entity."""

    # Push-based: protocol.py se listener callback aane pe khud
    # schedule_update_ha_state() karte hain, isliye periodic polling nahi
    # chahiye.
    _attr_should_poll = False

    def __init__(self, name: str, device: "protocol.RaylogicMod2uDevice", channel: int) -> None:
        self._attr_name = name
        self._device = device
        self._channel = channel
        self._attr_unique_id = f"raylogic_mod2u_{device.key}_ch{channel}"
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Entity HA mein add hote hi apne device+channel ka feedback listener register karo."""
        self._device.register_listener(self._channel, self._handle_external_update)

    async def async_will_remove_from_hass(self) -> None:
        """Entity remove hone pe listener saaf karo (stale callback na reh jaye)."""
        self._device.unregister_listener(self._channel, self._handle_external_update)

    def _handle_external_update(self, is_on: bool) -> None:
        """
        protocol.py ke receive-loop se callback - jab bhi is channel ka
        *AR= frame kahin se bhi aaye (mobile app se, switch se, ya khud HA
        ke apne command ka loopback), turant entity ka state sync karo aur
        HA UI ko update karo. Ye kisi bhi thread se call ho sakta hai,
        isliye schedule_update_ha_state() use karte hain (thread-safe).
        """
        self._attr_is_on = is_on
        self.schedule_update_ha_state()

    def turn_on(self, **kwargs) -> None:
        try:
            self._device.set_channel(self._channel, True)
            self._attr_is_on = True
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Raylogic MOD2U channel %s ON command fail: %s", self._channel, err)
            return
        # ZAROORI: should_poll=False hone ki wajah se HA khud-ba-khud state
        # write nahi karta service call ke baad - humein khud batana padta hai.
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs) -> None:
        try:
            self._device.set_channel(self._channel, False)
            self._attr_is_on = False
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Raylogic MOD2U channel %s OFF command fail: %s", self._channel, err)
            return
        self.schedule_update_ha_state()
