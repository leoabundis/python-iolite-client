import os, json, logging, unicodedata
from typing import Dict, List, Optional

import boto3
from environs import Env

from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_core.utils import is_request_type, is_intent_name
from ask_sdk_model import Response
from ask_sdk_model.ui import SimpleCard

from iolite_client.client import Client
from iolite_client.entity import Blind
from iolite_client.oauth_handler import OAuthHandler, OAuthWrapper, OAuthStorageInterface

# ================== LOG ==================
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
sb = SkillBuilder()

# ================== ENV ==================
env = Env()
USERNAME = env("HTTP_USERNAME")
PASSWORD = env("HTTP_PASSWORD")
# Importante: usa el client_id correcto para tu backend (p.ej. "deuwo_mia_app" o "iq_diwa_app")
CLIENT_ID = env("CLIENT_ID")              # e.g. "deuwo_mia_app"
NAME = env("NAME", "AlexaSkill")
CODE = os.environ.get("CODE", "")         # Sólo para bootstrap si NO hay token en SSM
BRIEF_MODE = env.bool("BRIEF_MODE", False)
SSM_PARAM = os.environ.get("IOLITE_TOKEN_PARAM", "/iolite/access_token")

# ================== SSM storage ==================
_ssm = boto3.client("ssm")

def _ssm_fetch_json(name: str) -> Optional[dict]:
    try:
        r = _ssm.get_parameter(Name=name, WithDecryption=True)
        return json.loads(r["Parameter"]["Value"])
    except _ssm.exceptions.ParameterNotFound:
        return None
    except Exception as e:
        logger.warning(f"SSM get_parameter error: {e}")
        return None

def _ssm_store_json(name: str, value: dict):
    _ssm.put_parameter(
        Name=name,
        Value=json.dumps(value),
        Type="SecureString",
        Overwrite=True
    )

class SSMOAuthStorage(OAuthStorageInterface):
    """Implementación de storage para OAuthWrapper usando SSM Parameter Store."""
    def __init__(self, param_name: str):
        self.param_name = param_name

    def store_access_token(self, payload: dict):
        _ssm_store_json(self.param_name, payload)

    def fetch_access_token(self) -> Optional[dict]:
        return _ssm_fetch_json(self.param_name)

# ================== SID / Client helpers ==================
_client: Optional[Client] = None
_discovery_done = False

def get_sid() -> str:
    """
    Obtiene un SID usando OAuthWrapper con storage en SSM:
    - Si hay token en SSM: lo usa; el wrapper lo refrescará si expira y re-escribirá en SSM.
    - Si NO hay token y sí hay CODE: canjea una sola vez y guarda en SSM.
    - Si NO hay token y NO hay CODE: error claro.
    """
    storage = SSMOAuthStorage(SSM_PARAM)
    token = storage.fetch_access_token()

    oauth_handler = OAuthHandler(USERNAME, PASSWORD, CLIENT_ID)
    wrapper = OAuthWrapper(oauth_handler, storage)

    if not token:
        if not CODE:
            raise RuntimeError("No hay access_token en SSM. Sube uno o define CODE para bootstrap.")
        logger.info("Bootstrapping access_token con CODE (una sola vez).")
        token = oauth_handler.get_access_token(CODE, NAME)
        storage.store_access_token(token)

    # OAuthWrapper.get_sid(token) refresca si expiró y guarda en storage
    sid = wrapper.get_sid(token)
    return sid

def get_client(force=False) -> Client:
    global _client
    if force or _client is None:
        sid = get_sid()
        _client = Client(sid, USERNAME, PASSWORD)
    return _client

def ensure_discovery():
    global _discovery_done, _client
    if _discovery_done:
        return
    try:
        get_client().discover()
        _discovery_done = True
    except Exception as e:
        logger.warning(f"discover() falló, reintentando con nuevo SID: {e}")
        _client = Client(get_sid(), USERNAME, PASSWORD)
        get_client().discover()
        _discovery_done = True

# ================== Cuartos ==================
ES_TO_INTERNAL = {
    "sala": "WoKo",
    "pasillo": "Flur",
    "cuarto": "Schlafen",
    "dormitorio": "Schlafen",
    "recamara": "Schlafen", "recámara": "Schlafen",
    "bano": "Bad", "baño": "Bad",
    # originales
    "woko": "WoKo", "wohnzimmer": "WoKo",
    "flur": "Flur", "hall": "Flur",
    "schlafen": "Schlafen", "bad": "Bad",
}
INTERNAL_TO_ES = {
    "WoKo": "la sala",
    "Flur": "el pasillo",
    "Schlafen": "el cuarto",
    "Bad": "el baño",
}
FALLBACK_BLINDS: Dict[str, List[str]] = {
    "WoKo": ["Blind_22", "Blind_21"],
    "Flur": ["Blind_11"],
    "Schlafen": ["Blind_41"],
    "Bad": [],
}

def _strip_articles_and_accents(text: str) -> str:
    t = text.strip().lower()
    for art in ("la ", "el ", "los ", "las "):
        if t.startswith(art):
            t = t[len(art):]
            break
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return t.replace("ñ", "n")

def normalize_room(spoken: Optional[str]) -> Optional[str]:
    if not spoken: return None
    key = _strip_articles_and_accents(spoken)
    return ES_TO_INTERNAL.get(key)

def say_room_es(internal: str) -> str:
    return INTERNAL_TO_ES.get(internal, internal)

def room_blind_ids(room_internal: str) -> List[str]:
    ensure_discovery()
    client = get_client()
    ids: List[str] = []
    room = client.discovered.find_room_by_name(room_internal)
    if room:
        for dev in room.devices.values():
            if isinstance(dev, Blind):
                ids.append(dev.identifier)
    return ids or FALLBACK_BLINDS.get(room_internal, [])

def all_blind_ids() -> List[str]:
    ensure_discovery()
    client = get_client()
    ids: List[str] = []
    for room in client.discovered.get_rooms():
        for dev in room.devices.values():
            if isinstance(dev, Blind) and dev.identifier not in ids:
                ids.append(dev.identifier)
    for rid_list in FALLBACK_BLINDS.values():
        for i in rid_list:
            if i not in ids:
                ids.append(i)
    return ids

def speak(handler_input, text: str, reprompt: Optional[str] = None):
    rb = handler_input.response_builder.speak(text)
    if BRIEF_MODE or not reprompt:
        return rb.response
    else:
        return rb.ask(reprompt).response

# ================== HANDLERS ==================
class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_request_type("LaunchRequest")(handler_input)
    def handle(self, handler_input: HandlerInput) -> Response:
        msg = "Listo. Ejemplos: pon las persianas de la sala al cincuenta por ciento, o pregunta la temperatura del cuarto."
        return (handler_input.response_builder
                .speak(msg)
                .ask("¿Sala, cuarto, pasillo o baño?")
                .set_card(SimpleCard("Mi Depa", msg))
                .response)

class SetBlindLevelIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("SetBlindLevelIntent")(handler_input)
    def handle(self, handler_input: HandlerInput) -> Response:
        intent = handler_input.request_envelope.request.intent
        slots = intent.slots or {}
        room_spoken = slots.get("room").value if slots.get("room") else None
        percent_raw = slots.get("percent").value if slots.get("percent") else None

        room_internal = normalize_room(room_spoken)
        try:
            percent = int(percent_raw) if percent_raw is not None else None
        except ValueError:
            percent = None

        if not room_internal or percent is None:
            return speak(handler_input,
                "No entendí. Di: pon las persianas de la sala al cincuenta por ciento.",
                reprompt="¿Sala, cuarto, pasillo o baño? y el porcentaje."
            )

        ids = room_blind_ids(room_internal)
        if not ids:
            return speak(handler_input, f"No encontré persianas en {say_room_es(room_internal)}.")

        for dev_id in ids:
            get_client().set_blind_level(dev_id, percent)

        return speak(handler_input,
            f"Listo, persianas de {say_room_es(room_internal)} al {percent} por ciento.",
            reprompt="¿Algo más?")

class SetAllBlindsIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("SetAllBlindsIntent")(handler_input)
    def handle(self, handler_input: HandlerInput) -> Response:
        intent = handler_input.request_envelope.request.intent
        slots = intent.slots or {}
        percent_raw = slots.get("percent").value if slots.get("percent") else None
        try:
            percent = int(percent_raw) if percent_raw is not None else None
        except ValueError:
            percent = None

        if percent is None:
            return speak(handler_input, "¿A qué porcentaje? Di un número entre cero y cien.")

        ids = all_blind_ids()
        if not ids:
            return speak(handler_input, "No encontré persianas en tu depa.")

        for dev_id in ids:
            get_client().set_blind_level(dev_id, percent)

        return speak(handler_input, f"Listo, puse todas las persianas al {percent} por ciento.",
                     reprompt="¿Algo más?")

class GetRoomTempIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("GetRoomTempIntent")(handler_input)
    def handle(self, handler_input: HandlerInput) -> Response:
        intent = handler_input.request_envelope.request.intent
        slots = intent.slots or {}
        room_spoken = slots.get("room").value if slots.get("room") else None
        room_internal = normalize_room(room_spoken)

        if not room_internal:
            return speak(handler_input,
                "Necesito una habitación específica: sala, cuarto, pasillo o baño.",
                reprompt="¿Cuál habitación?")

        ensure_discovery()
        r = get_client().discovered.find_room_by_name(room_internal)
        if not r or not getattr(r, "heating", None):
            return speak(handler_input, f"No tengo temperatura para {say_room_es(room_internal)}.")

        current = r.heating.current_temp
        target = r.heating.target_temp
        return speak(handler_input, f"En {say_room_es(room_internal)} hay {current:.1f} grados, objetivo {target:.1f}.",
                     reprompt="¿Algo más?")

class HelpHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.HelpIntent")(handler_input)
    def handle(self, handler_input: HandlerInput) -> Response:
        return speak(handler_input,
            "Puedes decir: pon las persianas de la sala al cincuenta por ciento, o cuál es la temperatura en el cuarto.")

class CancelStopHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))
    def handle(self, handler_input: HandlerInput) -> Response:
        return handler_input.response_builder.speak("Hecho.").response

# ================== REGISTRO ==================
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(SetBlindLevelIntentHandler())
sb.add_request_handler(SetAllBlindsIntentHandler())
sb.add_request_handler(GetRoomTempIntentHandler())
sb.add_request_handler(HelpHandler())
sb.add_request_handler(CancelStopHandler())

lambda_handler = sb.lambda_handler()