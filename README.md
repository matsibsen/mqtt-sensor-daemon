# MQTT Sensor Daemon

Publishes data from Raspberry Pi–connected sensors to MQTT and advertises them to Home Assistant via MQTT Discovery.  
Supported sensors (current script):

- **DS18B20** (1-Wire)
- **DHT22** (via CircuitPython: `adafruit-circuitpython-dht`)
- **BME280** (I²C: `adafruit-circuitpython-bme280`)

---

## Requirements

- Raspberry Pi with GPIO/I²C enabled
- **Python 3.7+** (recommend using a virtualenv)
- MQTT broker reachable from the Pi
- Home Assistant (optional) with MQTT integration (for discovery)

OS packages you’ll likely need:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev libgpiod2 i2c-tools
```

Enable interfaces (if not already):
```bash
sudo raspi-config
# Enable I2C
# Enable 1-Wire (for DS18B20)
# Reboot after changes
```

User groups for the account running the service (e.g. `pi`):
```bash
sudo usermod -aG gpio,i2c pi
# Optional, if you’ll use SPI / serial later:
sudo usermod -aG spi,dialout pi
```
Log out/in or restart the service after changing groups.

---

## Directory Layout

Recommended location:
```
/opt/mqtt-sensor-daemon/
  mqtt-sensor-daemon.py
  config.ini
  requirements.txt
  venv/                  # created below
```

---

## Virtual Environment Setup

```bash
sudo mkdir -p /opt/mqtt-sensor-daemon
sudo chown -R pi:pi /opt/mqtt-sensor-daemon
cd /opt/mqtt-sensor-daemon

python3 -m venv venv
source venv/bin/activate

# requirements.txt example is below
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

deactivate
```

**Example `requirements.txt`:**
```
paho-mqtt==1.6.1
adafruit-circuitpython-dht
adafruit-circuitpython-bme280==2.6.0
Adafruit-Blinka==8.8.0
RPi.GPIO==0.7.1
```

> Note: `libgpiod2` is required by `adafruit-circuitpython-dht` at runtime.

---

## Configuration

Create `/opt/mqtt-sensor-daemon/config.ini`:

```ini
[MQTT]
host = mqtt.example.com
port = 1883
username = your_user
password = your_pass

[MAIN]
sleep_interval = 30

[sensor_ds18b20_1]
type = ds18b20
device_name = DS18B20 Sensor 1
unique_id = thermalpi_ds18b20_28xxxxxxxxxxxx
discovery_prefix = homeassistant
# MQTT state topic where JSON will be published:
topic = ThermalPi/DS18B20_Sensor_1/state
# Path to w1 device file (adjust to your sensor id):
sensor_file = /sys/bus/w1/devices/28-XXXXXXXXXXXX/w1_slave
interval = 30

[sensor_dht22_1]
type = dht22
device_name = DHT22 Sensor 1
unique_id = thermalpi_dht22_gpio4
discovery_prefix = homeassistant
topic = ThermalPi/DHT22_Sensor_1/state
pin = 4
interval = 30

[sensor_bme280_1]
type = bme280
device_name = BME280 Sensor 1
unique_id = thermalpi_bme280_0x76
discovery_prefix = homeassistant
topic = ThermalPi/BME280_Sensor_1/state
i2c_address = 0x76
interval = 30
```

### Notes

- `topic` is the **state topic** the script publishes JSON to (e.g. `{"temperature": 21.88, "humidity": 48.5}`).
- `discovery_prefix` defaults to `homeassistant`.
- `unique_id` should be stable and unique per entity base (the script appends `_temperature`, `_humidity`, `_pressure` where applicable).
- For DS18B20, make sure 1-Wire is enabled and the device path is correct.

---

## Run Manually

```bash
cd /opt/mqtt-sensor-daemon
source venv/bin/activate
python mqtt-sensor-daemon.py config.ini
```

You should see logs about MQTT connection, discovery publication, and state publishes.

---

## Systemd Service

Create `/etc/systemd/system/mqtt-sensor-daemon.service`:

```ini
[Unit]
Description=MQTT Sensor Daemon (venv)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/opt/mqtt-sensor-daemon
ExecStart=/opt/mqtt-sensor-daemon/venv/bin/python /opt/mqtt-sensor-daemon/mqtt-sensor-daemon.py /opt/mqtt-sensor-daemon/config.ini
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable & start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable mqtt-sensor-daemon
sudo systemctl start mqtt-sensor-daemon
sudo journalctl -u mqtt-sensor-daemon -f
```

---

## Home Assistant Discovery

The script publishes **one discovery config per measurement** for multi-value sensors:

- DHT22: temperature, humidity  
- BME280: temperature, humidity, pressure  
- DS18B20: temperature

Each discovery entry points to your configured `state_topic` and uses a `value_template` to extract the JSON key. Entities should appear automatically under the MQTT integration in Home Assistant.

If you change discovery fields (name, unique_id, etc.), Home Assistant updates the existing entity if `unique_id` is stable.

---

## Troubleshooting

- **No entities in HA:**
  - Confirm MQTT credentials and broker reachability.
  - Check that discovery messages are retained on `homeassistant/sensor/.../config`.
  - Verify that `unique_id` is present and stable.

- **Clear retained discovery messages:**
  ```bash
  # Replace with your actual discovery topic:
  mosquitto_pub -h <broker> -t homeassistant/sensor/<host>/<object_id>/config -r -n
  ```

- **DHT22 intermittent errors:** common. The script skips a cycle on read errors. Ensure `libgpiod2` is installed and wiring is correct (3.3V, GND, data on the configured BCM pin with a 10k pull-up).

- **BME280 not detected:** check I²C address (`0x76` or `0x77`) and wiring. You can scan with:
  ```bash
  sudo i2cdetect -y 1
  ```

- **Permissions:** ensure the service user is in `gpio` and `i2c` groups; restart the service after adding groups.

- **1-Wire path missing:** ensure 1-Wire is enabled in `raspi-config` and the sensor is wired correctly.

---

## Security

Credentials are read from `config.ini`. If you prefer, you can modify the script to read from environment variables or a secrets manager. Restrict file permissions on `/opt/mqtt-sensor-daemon/config.ini`.

---

## License

MIT
