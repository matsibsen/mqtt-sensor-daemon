#!/usr/bin/env python3
import sys
import json
import time
import socket
import configparser

import paho.mqtt.client as mqtt
import adafruit_dht
import board, busio, adafruit_bme280

_DHT_CACHE = {}

def get_hostname():
    return socket.gethostname()

def format_device_name(name):
    return name.lower().replace(' ', '_')

def read_config(config_file):
    cfg = configparser.ConfigParser()
    cfg.read(config_file)
    return cfg

def _board_pin_from_bcm(bcm_pin):
    return getattr(board, f"D{int(bcm_pin)}")

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
        "manufacturer": manufacturer
    }
    if sw_version:
        d["sw_version"] = sw_version
    return d

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
            return {
                "temperature": round(temp, 2)
            }


        elif sensor_type == "dht22":
            bcm = int(params.get("pin", 4))
            if bcm not in _DHT_CACHE:
                _DHT_CACHE[bcm] = adafruit_dht.DHT22(_board_pin_from_bcm(bcm), use_pulseio=False)
            dht = _DHT_CACHE[bcm]
            temp = dht.temperature
            hum = dht.humidity
            if temp is None or hum is None:
                return None
            return {
                "temperature": round(float(temp), 2),
                "humidity": round(float(hum), 2)
            }

        elif sensor_type == "bme280":
            addr = int(params.get("i2c_address", "0x76"), 0)
            i2c = busio.I2C(board.SCL, board.SDA)
            sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=addr)
            return {
                "temperature": round(sensor.temperature, 2),
                "humidity": round(sensor.relative_humidity, 2),
                "pressure": round(sensor.pressure, 2)
            }

    except Exception as e:
        print(f"[ERROR] Could not read {sensor_type}: {e}")
    return None

def publish_discovery(client, section, cfg, hostname):
    params = cfg[section]
    prefix = params.get("discovery_prefix", "homeassistant")
    dev_name = format_device_name(params["device_name"])
    state_topic = params.get("topic", f"{hostname}/{dev_name}/state")
    device = build_device(cfg, hostname)
    sensor_type = params.get("type", "ds18b20")
    unique_base = params.get("unique_id", dev_name)

    if sensor_type in ("dht22", "bme280"):
        data_keys = ["temperature", "humidity"] if sensor_type == "dht22" else ["temperature", "humidity", "pressure"]
        for key in data_keys:
            cfg_msg = {
                "name": f"{params['device_name']} {key}",
                "state_topic": state_topic,
                "unit_of_measurement": params.get(f"{key}_unit", {
                    "temperature": "°C",
                    "humidity": "%",
                    "pressure": "hPa"
                }[key]),
                "device_class": {
                    "temperature": "temperature",
                    "humidity": "humidity",
                    "pressure": "pressure"
                }[key],
                "state_class": "measurement",
                "value_template": f"{{{{ value_json.{key} }}}}",
                "unique_id": f"{unique_base}_{key}",
                "device": device
            }
            disc_topic = f"{prefix}/sensor/{hostname}/{dev_name}_{key}/config"
            client.publish(disc_topic, json.dumps(cfg_msg), retain=True)
            print(f"Discovery → {disc_topic}: {cfg_msg}")

    else:
        cfg_msg = {
            "name": params["device_name"],
            "state_topic": state_topic,
            "unit_of_measurement": params.get("unit_of_measurement", "°C"),
            "device_class": params.get("device_class", "temperature"),
            "state_class": "measurement",
            "value_template": "{{ value_json.temperature }}",
            "unique_id": unique_base,
            "device": device
        }
        disc_topic = f"{prefix}/sensor/{hostname}/{dev_name}/config"
        client.publish(disc_topic, json.dumps(cfg_msg), retain=True)
        print(f"Discovery → {disc_topic}: {cfg_msg}")

def on_connect_factory(cfg, hostname):
    def _on_connect(client, userdata, flags, rc):
        print(f"[MQTT] Connected with result code {rc}")
        if rc == 0:
            for section in cfg.sections():
                if section.startswith("sensor_"):
                    publish_discovery(client, section, cfg, hostname)
        else:
            print("[MQTT] Connection failed:", mqtt.connack_string(rc))
    return _on_connect

def main(config_file):
    cfg = read_config(config_file)
    mqtt_cfg = cfg["MQTT"]

    client = mqtt.Client()
    hostname = get_hostname()
    client.on_connect = on_connect_factory(cfg, hostname)
    client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
    client.connect(mqtt_cfg["host"], int(mqtt_cfg["port"]), keepalive=60)
    client.loop_start()

    try:
        interval = int(cfg.get("MAIN", "sleep_interval", fallback="60"))
        while True:
            for section in cfg.sections():
                if not section.startswith("sensor_"):
                    continue
                params = cfg[section]
                typ = params.get("type", "ds18b20")
                data = read_sensor_data(typ, params)
                if data:
                    topic = params.get("topic", f"{hostname}/{format_device_name(params['device_name'])}/state")
                    payload = json.dumps(data)
                    client.publish(topic, payload)
                    print(f"[MQTT] Published to {topic}: {payload}")
            time.sleep(interval)

    except KeyboardInterrupt:
        print("Exiting…")
    finally:
        client.disconnect()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python mqtt-sensor-daemon.py config.ini")
    else:
        main(sys.argv[1])
