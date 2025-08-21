from iolite_client.entity import (
    Blind,
    Device,
    Heating,
    HumiditySensor,
    InFloorValve,
    Lamp,
    RadiatorValve,
    Room,
    Switch,
)
from iolite_client.exceptions import UnsupportedDeviceError


def create_room(payload: dict) -> Room:
    entity_class = payload.get("class")
    identifier = payload.get("id")

    if not entity_class:
        raise ValueError("Payload missing class")

    if not identifier:
        raise ValueError("Payload missing id")

    if entity_class != "Room":
        raise NotImplementedError(
            f"An unsupported entity class was provided when trying to create a room - {entity_class}"
        )

    return Room(identifier, payload["placeName"])


def create_device(payload: dict) -> Device:
    entity_class = payload.get("class")
    identifier = payload.get("id")

    if not entity_class:
        raise ValueError("Payload missing class")

    if not identifier:
        raise ValueError("Payload missing id")

    if entity_class != "Device":
        raise NotImplementedError(
            f"An unsupported entity class was provided when trying to create a device - {entity_class}"
        )

    return _create_device(identifier, payload["typeName"], payload)


def create_heating(payload: dict) -> Heating:
    return Heating(
        payload["id"],
        payload["name"],
        payload.get("currentTemperature", None),
        payload["targetTemperature"],
        payload.get("windowOpen", None),
    )


from iolite_client.entity import (
    Blind,
    Device,
    Heating,
    HumiditySensor,
    InFloorValve,
    Lamp,
    RadiatorValve,
    Room,
    Switch,
)
from iolite_client.exceptions import UnsupportedDeviceError


def create_room(payload: dict) -> Room:
    entity_class = payload.get("class")
    identifier = payload.get("id")

    if not entity_class:
        raise ValueError("Payload missing class")

    if not identifier:
        raise ValueError("Payload missing id")

    if entity_class != "Room":
        raise NotImplementedError(
            f"An unsupported entity class was provided when trying to create a room - {entity_class}"
        )

    return Room(identifier, payload["placeName"])


def create_device(payload: dict) -> Device:
    entity_class = payload.get("class")
    identifier = payload.get("id")

    if not entity_class:
        raise ValueError("Payload missing class")

    if not identifier:
        raise ValueError("Payload missing id")

    if entity_class != "Device":
        raise NotImplementedError(
            f"An unsupported entity class was provided when trying to create a device - {entity_class}"
        )

    return _create_device(identifier, payload["typeName"], payload)


def create_heating(payload: dict) -> Heating:
    return Heating(
        payload["id"],
        payload["name"],
        payload.get("currentTemperature", None),
        payload["targetTemperature"],
        payload.get("windowOpen", None),
    )


def _get_prop_optional(properties: list, key: str, default=None):
    match = next((p for p in properties if p.get("name") == key), None)
    return match.get("value") if match and "value" in match else default


def _get_prop(properties: list, key: str):
    val = _get_prop_optional(properties, key, default=None)
    if val is None:
        available = [p.get("name") for p in properties]
        raise ValueError(
            f"Failed to find {key} in property set. Available: {available}"
        )
    return val


def _create_device(identifier: str, type_name: str, payload: dict):
    place_identifier = payload["placeIdentifier"]
    model_name = payload.get("modelName")
    manufacturer = payload.get("manufacturer")  # may be None
    friendly = payload["friendlyName"]

    if type_name == "Lamp":
        return Lamp(identifier, friendly, place_identifier, manufacturer)

    elif type_name == "TwoChannelRockerSwitch":
        return Switch(identifier, friendly, place_identifier, manufacturer)

    elif type_name == "Heater":
        properties = payload["properties"]
        current_env_temp = _get_prop_optional(
            properties, "currentEnvironmentTemperature"
        )

        # 1) If model matches special InFloorValve signature, keep your original path
        if model_name is not None and model_name.startswith("38de6001c3ad"):
            heating_temperature_setting = _get_prop(
                properties, "heatingTemperatureSetting"
            )
            device_status = _get_prop_optional(properties, "deviceStatus", "UNKNOWN")
            return InFloorValve(
                identifier,
                friendly,
                place_identifier,
                manufacturer,
                current_env_temp,
                heating_temperature_setting,
                device_status,
            )

        # 2) Heuristic based on available properties (KNX style → InFloorValve-like)
        has_knx_setpoint = any(
            p.get("name") == "heatingTemperatureSetting" for p in properties
        )
        has_radiator_props = any(
            p.get("name") in ("batteryLevel", "valvePosition", "heatingMode")
            for p in properties
        )

        if has_radiator_props:
            battery_level = _get_prop_optional(properties, "batteryLevel")
            heating_mode = _get_prop_optional(properties, "heatingMode")
            valve_position = _get_prop_optional(properties, "valvePosition")
            return RadiatorValve(
                identifier,
                friendly,
                place_identifier,
                manufacturer,
                current_env_temp,
                battery_level,
                heating_mode,
                valve_position,
            )

        if has_knx_setpoint:
            heating_temperature_setting = _get_prop(
                properties, "heatingTemperatureSetting"
            )
            device_status = _get_prop_optional(properties, "deviceStatus", "UNKNOWN")
            return InFloorValve(
                identifier,
                friendly,
                place_identifier,
                manufacturer,
                current_env_temp,
                heating_temperature_setting,
                device_status,
            )

        # 3) Fallback: raise with context
        available = [p.get("name") for p in properties]
        raise UnsupportedDeviceError(
            f"Heater with unrecognized property set {available}", identifier, payload
        )

    elif type_name == "Blind":
        properties = payload["properties"]
        blind_level = _get_prop(properties, "blindLevel")
        return Blind(identifier, friendly, place_identifier, manufacturer, blind_level)

    elif type_name == "HumiditySensor":
        properties = payload["properties"]
        current_env_temp = _get_prop_optional(
            properties, "currentEnvironmentTemperature"
        )
        humidity_level = _get_prop(properties, "humidityLevel")
        return HumiditySensor(
            identifier,
            friendly,
            place_identifier,
            manufacturer,
            current_env_temp,
            humidity_level,
        )

    else:
        raise UnsupportedDeviceError(type_name, identifier, payload)
