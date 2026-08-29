import collections
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

with open("/data/options.json", encoding="utf-8") as stream:
    options = json.load(stream)

LOCAL_HOST = options["mqtt_host"]
LOCAL_PORT = int(options["mqtt_port"])
LOCAL_USERNAME = options["mqtt_username"]
LOCAL_PASSWORD = options["mqtt_password"]
KEY = b"vaysunic20260824"
OUTPUT_PREFIX = "khirale/decoded"
SITE = options["site_code"].strip()
ALLOWED_SERIALS = set(options["allowed_serials"])
VPS_HOST = options["vps_host"]
VPS_PORT = int(options["vps_port"])
VPS_USERNAME = options["vps_username"]
VPS_PASSWORD = options["vps_password"]
UP_FAMILIES = {"connect", "time", "sensor", "refresh", "warn", "cmdack", "will"}
CONTROL_PREFIX = "khirale/control"
PROTOCOLS_FILE = Path("/data/device_protocols.json")
HA_REFRESH_STATE_FILE = Path("/data/ha_refresh_state.json")
HA_REFRESH_WINDOW = 240
HA_SENSOR_FORWARD_INTERVAL = 185
CLOUD_REFRESH_WINDOW = 125
HA_COMMAND_ACK_WINDOW = 300
GATEWAY_STATUS_TOPIC = f"sites/{SITE}/up/status/gateway"

if len(KEY) != 16:
    raise RuntimeError("La clé XOR doit contenir exactement 16 octets UTF-8")
if not SITE:
    raise RuntimeError("Le code du site est obligatoire")
if not ALLOWED_SERIALS:
    raise RuntimeError("La liste des SN autorisés est vide")

queue = collections.deque(maxlen=10000)
queue_lock = threading.Lock()
state_lock = threading.Lock()
device_protocols = {}
ha_refresh_until = {}
cloud_refresh_until = {}
last_ha_sensor_forwarded = {}
local_command_echoes = {}
pending_ha_commands = {}


def load_json_file(path, default):
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


def save_json_file(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(path)
    except OSError as error:
        logging.error("Impossible d'enregistrer l'état %s : %s", path, error)


def remember_local_command(topic, payload):
    now = time.monotonic()
    key = (topic, bytes(payload))
    with state_lock:
        expired = [item for item, timestamp in local_command_echoes.items()
                   if now - timestamp > 30]
        for item in expired:
            local_command_echoes.pop(item, None)
        local_command_echoes[key] = now


def consume_local_command_echo(topic, payload):
    key = (topic, bytes(payload))
    with state_lock:
        timestamp = local_command_echoes.pop(key, None)
    return timestamp is not None and time.monotonic() - timestamp <= 30


def remember_ha_command(sn, command):
    uid = command.get("uid")
    name = command.get("cmd")
    if not isinstance(uid, (int, str)) or not isinstance(name, str) or not name:
        logging.warning("Commande HA sans cmd/uid corrélable sn=%s", sn)
        return False
    now = time.time()
    key = (sn, str(uid))
    with state_lock:
        expired = [item for item, value in pending_ha_commands.items()
                   if value["expires_at"] <= now]
        for item in expired:
            pending_ha_commands.pop(item, None)
        pending_ha_commands[key] = {
            "cmd": name,
            "expires_at": now + HA_COMMAND_ACK_WINDOW,
        }
    return True


def is_ha_command_ack(sn, acknowledgement):
    if not isinstance(acknowledgement, dict):
        return False
    uid = acknowledgement.get("uid")
    name = acknowledgement.get("cmd")
    if not isinstance(uid, (int, str)) or not isinstance(name, str):
        return False
    now = time.time()
    key = (sn, str(uid))
    with state_lock:
        pending = pending_ha_commands.get(key)
        if not pending or pending["expires_at"] <= now:
            pending_ha_commands.pop(key, None)
            return False
        return pending["cmd"] == name


device_protocols.update(load_json_file(PROTOCOLS_FILE, {}))
protocols_migrated = False
for stored_sn, stored_device in device_protocols.items():
    if not isinstance(stored_device, dict):
        continue
    stored_topic = stored_device.get("command_topic")
    expected_topic = f"/vaysunic/vysc/cmd/{stored_sn}"
    if stored_topic != expected_topic:
        stored_device["command_topic"] = expected_topic
        protocols_migrated = True
if protocols_migrated:
    save_json_file(PROTOCOLS_FILE, device_protocols)
ha_refresh_until.update({
    sn: float(deadline)
    for sn, deadline in load_json_file(HA_REFRESH_STATE_FILE, {}).items()
    if sn in ALLOWED_SERIALS and isinstance(deadline, (int, float)) and deadline > time.time()
})


def decode_enc1(payload):
    if not payload.startswith(b"ENC1"):
        return None
    encrypted = payload[4:]
    clear = bytes(value ^ KEY[index % len(KEY)] for index, value in enumerate(encrypted))
    parsed = json.loads(clear.decode("utf-8"))
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def decoded_json(payload):
    try:
        clear = decode_enc1(payload)
        return json.loads(clear if clear is not None else payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def learn_device(sn, topic, payload):
    data = decoded_json(payload)
    if not isinstance(data, dict):
        return
    protocol = "enc1" if payload.startswith(b"ENC1") else "legacy"
    parts = topic.lstrip("/").split("/")
    if not (
        (len(parts) >= 4 and parts[0] == "iot" and parts[2] == sn)
        or (len(parts) >= 4 and parts[0:2] == ["Cot", "izy"] and parts[2] == sn)
        or (len(parts) >= 3 and parts[0:2] == ["vaysunic", "vysc"])
    ):
        return
    command_topic = f"/vaysunic/vysc/cmd/{sn}"
    learned = {"protocol": protocol, "command_topic": command_topic}
    with state_lock:
        if device_protocols.get(sn) == learned:
            return
        device_protocols[sn] = learned
        save_json_file(PROTOCOLS_FILE, device_protocols)
    logging.info("Protocole appris sn=%s protocole=%s topic=%s", sn, protocol, command_topic)


def publish_decoded(sn, family, payload):
    """Publie une vue locale en clair, identique pour ENC1 et les firmwares legacy."""
    try:
        decoded = decode_enc1(payload)
        if decoded is None:
            decoded = payload.decode("utf-8")
        local.publish(f"{OUTPUT_PREFIX}/{sn}/{family}", decoded, qos=1, retain=False)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        logging.warning(
            "Copie lisible impossible famille=%s sn=%s erreur=%s", family, sn, error
        )


def json_sn(payload):
    data = decoded_json(payload)
    value = data.get("sn") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def classify_up(topic, payload):
    parts = topic.lstrip("/").split("/")
    if len(parts) >= 4 and parts[0] == "iot":
        sn, family = parts[2], parts[3]
        if family == "will" and sn == "%s":
            try:
                will_parts = payload.decode("utf-8").strip().split("/")
                if len(will_parts) == 2:
                    sn = will_parts[1].upper()
            except UnicodeDecodeError:
                pass
    elif len(parts) >= 4 and parts[0:2] == ["Cot", "izy"]:
        sn, family = parts[2], parts[3]
    elif len(parts) >= 3 and parts[0:2] == ["vaysunic", "vysc"]:
        family = parts[2]
        sn = next((part for part in parts[3:] if part in ALLOWED_SERIALS), None)
        sn = sn or json_sn(payload)
        if not sn and family == "will":
            try:
                sn = payload.decode("utf-8").strip().split("/")[-1]
            except UnicodeDecodeError:
                pass
    else:
        return None, None
    if family not in UP_FAMILIES or sn not in ALLOWED_SERIALS:
        return None, None
    return sn, family


def refresh_to_sensor_topic(topic):
    """Remplace uniquement la famille MQTT, sans toucher au payload."""
    leading_slash = topic.startswith("/")
    parts = topic.lstrip("/").split("/")
    if len(parts) >= 4 and (parts[0] == "iot" or parts[0:2] == ["Cot", "izy"]):
        if parts[3] != "refresh":
            return None
        parts[3] = "sensor"
    elif len(parts) >= 3 and parts[0:2] == ["vaysunic", "vysc"]:
        if parts[2] != "refresh":
            return None
        parts[2] = "sensor"
    else:
        return None
    converted = "/".join(parts)
    return "/" + converted if leading_slash else converted


def publish_or_queue(topic, payload):
    if vps.is_connected():
        result = vps.publish(topic, payload, qos=1, retain=False)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            return
    with queue_lock:
        if len(queue) == queue.maxlen:
            logging.error("File VPS pleine : suppression du message le plus ancien")
        queue.append((topic, payload))


def flush_queue():
    while vps.is_connected():
        with queue_lock:
            if not queue:
                return
            topic, payload = queue.popleft()
        result = vps.publish(topic, payload, qos=1, retain=False)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            with queue_lock:
                queue.appendleft((topic, payload))
            return


def on_local_connect(client, userdata, flags, rc):
    if rc != 0:
        logging.error("Connexion au Mosquitto HA refusée : %s", rc)
        return
    for topic in ("iot/#", "Cot/izy/#", "/vaysunic/vysc/sensor/#",
                  "/vaysunic/vysc/refresh/#", "/vaysunic/vysc/cmdack",
                  "/vaysunic/vysc/will", "/vaysunic/vysc/connect",
                  f"{CONTROL_PREFIX}/+/+"):
        client.subscribe(topic, qos=1)
    logging.info("Connecté au Mosquitto HA")


def on_local_message(client, userdata, message):
    if consume_local_command_echo(message.topic, message.payload):
        logging.debug("Écho de commande locale ignoré topic=%s", message.topic)
        return

    control_parts = message.topic.split("/")
    if len(control_parts) == 4 and control_parts[0:2] == ["khirale", "control"]:
        sn = control_parts[2].upper()
        control_command = control_parts[3]
        if sn not in ALLOWED_SERIALS:
            logging.warning("Commande HA rejetée pour SN non autorisé : %s", sn)
            return
        command = decoded_json(message.payload)
        if (not isinstance(command, dict)
                or command.get("cmd") != control_command
                or str(command.get("sn", "")).upper() != sn):
            logging.warning("Commande HA invalide sn=%s cmd=%s", sn, control_command)
            return
        clear_payload = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        command_topic = f"/vaysunic/vysc/cmd/{sn}"
        if not remember_ha_command(sn, command):
            return
        if control_command == "refresh":
            now = time.time()
            with state_lock:
                ha_refresh_until[sn] = now + HA_REFRESH_WINDOW
                save_json_file(HA_REFRESH_STATE_FILE, ha_refresh_until)
        publish_decoded(sn, "cmd", clear_payload)
        remember_local_command(command_topic, clear_payload)
        local.publish(command_topic, clear_payload, qos=1, retain=False)
        logging.info("Commande HA acceptée sn=%s cmd=%s topic=%s", sn, control_command, command_topic)
        return

    sn, family = classify_up(message.topic, message.payload)
    if not sn:
        logging.warning("Message local rejeté topic=%s", message.topic)
        return
    learn_device(sn, message.topic, message.payload)
    publish_decoded(sn, family, message.payload)
    if family == "cmdack":
        acknowledgement = decoded_json(message.payload)
        if is_ha_command_ack(sn, acknowledgement):
            logging.info(
                "CMDACK commande HA conservé localement sn=%s cmd=%s uid=%s",
                sn, acknowledgement.get("cmd"), acknowledgement.get("uid"),
            )
            return
    if family == "refresh":
        now = time.time()
        with state_lock:
            cloud_active = now < cloud_refresh_until.get(sn, 0)
            ha_active = now < ha_refresh_until.get(sn, 0)
            last_sent = last_ha_sensor_forwarded.get(sn, 0)
        if not cloud_active and ha_active:
            if now - last_sent < HA_SENSOR_FORWARD_INTERVAL:
                logging.info("Refresh HA bloqué vers le cloud sn=%s", sn)
                return
            sensor_payload = decoded_json(message.payload)
            if (not isinstance(sensor_payload, dict)
                    or str(sensor_payload.get("sn", "")).upper() != sn
                    or not isinstance(sensor_payload.get("data"), list)):
                logging.error("Payload refresh incompatible avec sensor sn=%s", sn)
                return
            sensor_topic = refresh_to_sensor_topic(message.topic)
            if sensor_topic is None:
                logging.error("Conversion refresh vers sensor impossible sn=%s topic=%s", sn, message.topic)
                return
            with state_lock:
                last_ha_sensor_forwarded[sn] = now
            publish_or_queue(f"sites/{SITE}/up/{sensor_topic.lstrip('/')}", message.payload)
            logging.info("Refresh HA converti en sensor vers le cloud sn=%s", sn)
            return
    publish_or_queue(f"sites/{SITE}/up/{message.topic.lstrip('/')}", message.payload)
    logging.info("Message montant accepté famille=%s sn=%s", family, sn)


def on_vps_connect(client, userdata, flags, rc):
    if rc != 0:
        logging.error("Connexion au VPS refusée : %s", rc)
        return
    client.subscribe(f"sites/{SITE}/down/#", qos=1)
    client.publish(GATEWAY_STATUS_TOPIC, b"1", qos=1, retain=True)
    logging.info("Connecté au VPS en TLS")
    flush_queue()


def on_vps_disconnect(client, userdata, rc):
    reason = mqtt.error_string(rc)
    if rc == mqtt.MQTT_ERR_SUCCESS:
        logging.info("Déconnecté du VPS proprement rc=%s raison=%s", rc, reason)
    else:
        logging.warning("Connexion VPS perdue rc=%s raison=%s", rc, reason)


def on_vps_message(client, userdata, message):
    prefix = f"sites/{SITE}/down/"
    if not message.topic.startswith(prefix):
        return
    target = message.topic[len(prefix):]
    parts = target.lstrip("/").split("/")
    if len(parts) >= 4 and parts[0] == "iot":
        sn = parts[2]
        family = parts[3]
    elif len(parts) >= 4 and parts[0:2] == ["Cot", "izy"]:
        sn = parts[2]
        family = parts[3]
    elif len(parts) >= 3 and parts[0:2] == ["vaysunic", "vysc"]:
        family = parts[2]
        sn = parts[3] if len(parts) >= 4 else json_sn(message.payload)
        target = "/" + target.lstrip("/")
    else:
        logging.warning("Topic descendant inconnu rejeté : %s", target)
        return
    if sn not in ALLOWED_SERIALS:
        logging.warning("SN descendant rejeté : %s", sn)
        return
    command = decoded_json(message.payload)
    is_refresh = family == "refresh" or (
        family == "cmd" and isinstance(command, dict) and command.get("cmd") == "refresh"
    )
    if is_refresh:
        with state_lock:
            cloud_refresh_until[sn] = time.time() + CLOUD_REFRESH_WINDOW
        logging.info("Fenêtre refresh cloud ouverte sn=%s", sn)
    remember_local_command(target, message.payload)
    local.publish(target, message.payload, qos=1, retain=False)
    publish_decoded(sn, family, message.payload)
    logging.info("Commande descendante acceptée sn=%s topic=%s", sn, target)


local = mqtt.Client(client_id="khirale-decoder-local")
local.username_pw_set(LOCAL_USERNAME, LOCAL_PASSWORD)
local.on_connect = on_local_connect
local.on_message = on_local_message
local.reconnect_delay_set(1, 60)

vps = mqtt.Client(client_id=f"bridge-{SITE}-vps", clean_session=True)
vps.username_pw_set(VPS_USERNAME, VPS_PASSWORD)
vps.tls_set()
vps.tls_insecure_set(False)
vps.will_set(GATEWAY_STATUS_TOPIC, b"0", qos=1, retain=True)
vps.on_connect = on_vps_connect
vps.on_message = on_vps_message
vps.on_disconnect = on_vps_disconnect
vps.reconnect_delay_set(1, 60)


def stop(*_):
    local.disconnect()
    if vps.is_connected():
        info = vps.publish(GATEWAY_STATUS_TOPIC, b"0", qos=1, retain=True)
        try:
            info.wait_for_publish(timeout=2)
        except RuntimeError:
            pass
    vps.disconnect()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

try:
    vps.connect_async(VPS_HOST, VPS_PORT, keepalive=60)
    vps.loop_start()
    local.connect(LOCAL_HOST, LOCAL_PORT, keepalive=60)
    local.loop_forever()
except Exception:
    logging.exception("Arrêt de la passerelle")
    sys.exit(1)
finally:
    vps.loop_stop()
