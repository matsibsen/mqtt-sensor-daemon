#!/usr/bin/env python3
import sys
import json
import time
import socket
import configparser

import paho.mqtt.client as mqtt
import Adafruit_DHT
import board, busio, adafruit_bme280

def get_hostname():
    return socket.gethostname()

def format_device_name(name):
    return name.lower().replace(' ', '_')

def read_config(config_file):
    cfg = configparser.ConfigParser()
    cfg.read(config_file)
    return cfg

def read_sensor_data(sensor_type, params):
    """Returnerar ett dict med mätvärden."""
    try:
        if sensor_type == "ds18b20":
            sensor_file = params["sensor_file"]
            with open(sensor_file, "r") as f:
                temp_str = f.read()
            temp = float(temp_str) / 1000.0
            return {"temperature": round(temp, 2)}

        elif sensor_type == "dht22":
            pin = int(params.get("pin", 4))
            hum, temp = Adafruit_DHT.read_retry(Adafruit_DHT.DHT22, pin)
            return {
                "temperature": round(temp, 2),
                "humidity": round(hum, 2)
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
        print(f"[ERROR] Kunde inte läsa {sensor_type}: {e}")
    return None

def publish_discovery(client, section, cfg, hostname):
    params = cfg[section]
    prefix = params.get("discovery_prefix", "homeassistant")
    dev_name = format_device_name(params["device_name"])
    state_topic = params.get("topic", f"{hostname}/{dev_name}/state")

    # Basen för "device"-blocket i HA
    device = {
        "identifiers": [hostname],
        "name": params["device_name"],
        "model": params.get("type", "unknown"),
        "manufacturer": params.get("manufacturer", "Your Manufacturer")
    }

    sensor_type = params.get("type", "ds18b20")
    unique_base = params.get("unique_id", dev_name)

    # Om multivärdig, skickar vi JSON och skapar flera sensors i HA
    if sensor_type in ("dht22", "bme280"):
        data_keys = list(read_sensor_data(sensor_type, params).keys())
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
                "value_template": f"{{{{ value_json.{key} }}}}",
                "unique_id": f"{unique_base}_{key}",
                "device": device
            }
            disc_topic = f"{prefix}/sensor/{hostname}/{dev_name}_{key}/config"
            client.publish(disc_topic, json.dumps(cfg_msg), retain=True)
            print(f"Discovery → {disc_topic}: {cfg_msg}")

    else:
        # Enkelt fall: bara temperatur
        cfg_msg = {
            "name": params["device_name"],
            "state_topic": state_topic,
            "unit_of_measurement": params.get("unit_of_measurement", "°C"),
            "device_class": params.get("device_class", "temperature"),
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
            print("[MQTT] Koppling misslyckades:", mqtt.connack_string(rc))
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
                    print(f"[MQTT] Publiserade till {topic}: {payload}")
            time.sleep(interval)

    except KeyboardInterrupt:
        print("Avslutar…")
    finally:
        client.disconnect()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python mqtt-sensor-daemon.py config.ini")
    else:
        main(sys.argv[1])
