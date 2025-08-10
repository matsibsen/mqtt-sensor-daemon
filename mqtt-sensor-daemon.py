#!/usr/bin/env python3
import sys
import json
import time
import socket
import configparser
import re
import unicodedata

import paho.mqtt.client as mqtt
from pigpio_dht import DHT22 as PIGPIO_DHT22
import board, busio, adafruit_bme280

_DHT_CACHE = {}

def get_hostname():
    return socket.gethostname()

def format_device_name(name):
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    s = s.lower().replace(' ', '_')
    s = re.sub(r'[^a-z0-9_]', '_', s)
    s = re.sub(r'__+', '_', s).strip('_')
    return s

def read_config(config_file):
    cfg = configparser.ConfigParser()
    cfg.read(config_file)
    return cfg


def build_device(cfg, hostname):
    dev = cfg["DEVICE"] if "DEVICE" in cfg else {}
    name = dev.get("name", hostname)
    model = dev.get("model", "Raspberry Pi")
    manufacturer = dev.get("manufacturer", "Your Manufacturer")
    identifiers = [x.strip() for x in dev.get("identifiers", hostname).split(",")]
    sw_version = dev.get("sw_version", "").strip()

    d = {
        "identifiers": identifiers,
        "name": name,
        "model": model,
        "manufacturer": manufacturer,
    }
    if sw_version:
        d["sw_version"] = sw_version
    return d

def _board_pin_from_bcm(bcm_pin):
    return getattr(board, f"D{int(bcm_pin)}")

def read_sensor_data(sensor_type, params):
    try:
        if sensor_type == "ds18b20":
            sensor_file = params["sensor_file"]
            with open(sensor_file, "r") as f:
                content = f.read().strip()
            if "t=" in content:
                milli_c = int(content.rsplit("t=", 1)[1])
                temp = milli_c / 1000.0
            else:
                val = float(content)
                temp = val / 1000.0 if abs(val) > 170 else val
            return {"temperature": round(temp, 1)}

        elif sensor_type == "dht22":
            bcm = int(params.get("pin", 4))
            print("[DHT22] {} on BCM{}".format(params.get("device_name", "<unnamed>"), bcm), flush=True)
            try:
                if bcm not in _DHT_CACHE:
                    _DHT_CACHE[bcm] = PIGPIO_DHT22(bcm)
                    print("[DHT22] init BCM{}".format(bcm), flush=True)
                    time.sleep(0.5)
                sensor = _DHT_CACHE[bcm]
                res = sensor.sample(samples=5)
                if res and res.get("valid"):
                    t = res.get("temp_c")
                    h = res.get("humidity")
                    if t is not None and h is not None:
                        return {
                            "temperature": round(float(t), 1), 
                            "humidity": round(float(h), 1)
                        }
                return None
            except Exception as e:
                print("[ERROR] DHT22 (pigpio) BCM{}: {}".format(bcm, e), flush=True)
                _DHT_CACHE.pop(bcm, None)
                return None

        elif sensor_type == "bme280":
            addr = int(params.get("i2c_address", "0x76"), 0)
            i2c = busio.I2C(board.SCL, board.SDA)
            sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=addr)
            return {
                "temperature": round(float(sensor.temperature), 1),
                "humidity": round(float(sensor.relative_humidity), 1),
                "pressure": round(float(sensor.pressure), 1),
            }

        else:
            print("[WARN] Okänt sensortyp: {}".format(sensor_type), flush=True)
            return None

    except Exception as e:
        print("[ERROR] Kunde inte läsa {}: {}".format(sensor_type, e), flush=True)
        return None


def publish_discovery(client, section, cfg, hostname):
    params = cfg[section]
    prefix = params.get("discovery_prefix", "homeassistant")
    dev_name = format_device_name(params["device_name"])
    state_topic = params.get("topic", f"{hostname}/{dev_name}/state")
    device = build_device(cfg, hostname)

    sensor_type = params.get("type", "ds18b20")
    unique_base = params.get("unique_id", f"{hostname}_{dev_name}")

    def _pub(payload, obj_suffix):
        topic = f"{prefix}/sensor/{hostname}/{obj_suffix}/config"
        client.publish(topic, json.dumps(payload), retain=True)
        print("[DISCOVERY] {} -> {}".format(topic, payload), flush=True)

    if sensor_type in ("dht22", "bme280"):
        keys = ["temperature", "humidity"] if sensor_type == "dht22" else ["temperature", "humidity", "pressure"]
        units = {"temperature": "°C", "humidity": "%", "pressure": "hPa"}
        dclass = {"temperature": "temperature", "humidity": "humidity", "pressure": "pressure"}
        for k in keys:
            cfg_msg = {
                "name": "{} {}".format(params["device_name"], k),
                "state_topic": state_topic,
                "unit_of_measurement": params.get("{}_unit".format(k), units[k]),
                "device_class": dclass[k],
                "state_class": "measurement",
                "value_template": "{{{{ value_json.{} }}}}".format(k),
                "unique_id": "{}_{}".format(unique_base, k),
                "device": device,
            }
            _pub(cfg_msg, "{}_{}".format(dev_name, k))
    else:
        cfg_msg = {
            "name": params["device_name"],
            "state_topic": state_topic,
            "device_class": "temperature",
            "unit_of_measurement": params.get("unit_of_measurement", "°C"),
            "state_class": "measurement",
            "value_template": "{{ value_json.temperature }}",
            "unique_id": unique_base,
            "device": device,
        }
        _pub(cfg_msg, dev_name)

def on_connect_factory(cfg, hostname):
    def _on_connect(client, userdata, flags, rc):
        print("[MQTT] Connected with result code {}".format(rc), flush=True)
        if rc == 0:
            sections = [s for s in cfg.sections() if s not in ("MQTT", "MAIN", "DEVICE")]
            print("[DISCOVERY] sections: {}".format(sections), flush=True)
            for section in sections:
                try:
                    publish_discovery(client, section, cfg, hostname)
                except Exception as e:
                    print("[ERROR] discovery for {} failed: {}".format(section, e), flush=True)
        else:
            try:
                print("[MQTT] {}".format(mqtt.connack_string(rc)), flush=True)
            except Exception:
                pass
    return _on_connect

def main(config_file):
    cfg = read_config(config_file)
    mqtt_cfg = cfg["MQTT"]

    client = mqtt.Client()
    hostname = get_hostname()
    client.on_connect = on_connect_factory(cfg, hostname)
    client.enable_logger()
    if mqtt_cfg.get("username"):
        client.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password"))
    client.connect(mqtt_cfg["host"], int(mqtt_cfg.get("port", "1883")), keepalive=60)
    client.loop_start()

    try:
        interval = int(cfg.get("MAIN", "sleep_interval", fallback="60"))
        while True:
            for section in cfg.sections():
                if section in ("MQTT", "MAIN", "DEVICE"):
                    continue
                params = cfg[section]
                typ = params.get("type", "ds18b20")
                data = read_sensor_data(typ, params)
                if data:
                    topic = params.get("topic", f"{hostname}/{format_device_name(params['device_name'])}/state")
                    payload = json.dumps(data)
                    client.publish(topic, payload)
                    print("[MQTT] Published to {}: {}".format(topic, payload), flush=True)
                else:
                    print("[WARN] No data from {} ({}) this cycle".format(section, typ), flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Exiting…", flush=True)
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python mqtt-sensor-daemon.py config.ini")
        sys.exit(1)
    main(sys.argv[1])