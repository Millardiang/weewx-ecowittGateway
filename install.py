"""install.py

Installer for the weewx-EcowittGateway driver/service.

Copyright (C) 2026 Ian Millard

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program.  If not, see https://www.gnu.org/licenses/.

Requires WeeWX 5.4.0 or later. Install with:
    weectl extension install weewx-EcowittGateway-<version>.zip

The installer asks for the gateway settings and writes them to weewx.conf,
with the [EcowittGateway] section placed directly after [Station]. Settings
in an [EcowittHttp] section (earlier versions, or ecowitt_http.py) are moved
to [EcowittGateway]. When run without a terminal (for example from a script)
it uses the defaults or the existing settings.

Every question can also be answered from an ECOWITT_* environment variable
(see SETTINGS below and INSTALL.md), so an install can be fully scripted:
    sudo ECOWITT_IP_ADDRESS=192.168.1.100 ECOWITT_MODE=driver \\
        weectl extension install --yes weewx-EcowittGateway-<version>.zip
ECOWITT_NO_PROMPT=yes stops the installer asking anything, even at a terminal.
"""

import getpass
import importlib.util
import io
import json
import os
import re
import socket
import sys
import urllib.request

import configobj
import weewx

from weecfg.extension import ExtensionInstaller

VERSION = '1.3.3'
MODULE = 'weewx-EcowittGateway'
SECTION = 'EcowittGateway'
LEGACY_SECTION = 'EcowittHttp'
SERVICE = f'user.{MODULE}.EcowittHttpService'
MIN_WEEWX_VERSION = (5, 4, 0)


def _rng(fmt, n, start=1):
    return [fmt.format(i) for i in range(start, n + 1)]


# accumulator extractor: fields
EXTRACTORS = {
    'sum': ('lightning_strike_count', 't_rain', 'p_rain', 'hail', 'lightningcount'),
    'max': ('rainRate', 'rrain_piezo', 'p_rainrate'),
    'last': ('gain0', 'gain1', 'gain2', 'gain3', 'gain4', 'maxdailygust', 'hourRain',
             'dayRain', 'weekRain', 'monthRain', 'yearRain', 'totalRain', 'erain_piezo', 'hrain_piezo',
             'drain_piezo', 'wrain_piezo', 'mrain_piezo', 'yrain_piezo', 'vpd', 'depth_ch1',
             'depth_ch2', 'depth_ch3', 'depth_ch4', 'wh25_batt', 'wh26_batt', 'wh40_batt', 'wn20_batt',
             'wn38_batt', 'wh68_batt', 'wh69_batt', 'ws68_batt', 'ws80_batt', 'ws85_batt', 'ws90_batt',
             'ws85cap_volt', 'ws90cap_volt', 'ws1900batt', 'console_batt', 'consoleext_batt',
             'ldsbatt1', 'ldsbatt2', 'ldsbatt3', 'ldsbatt4', 'batteryStatus1', 'batteryStatus2',
             'batteryStatus3', 'batteryStatus4', 'batteryStatus5', 'batteryStatus6', 'batteryStatus7',
             'batteryStatus8', 'bgtbatt', 'co2_24h', 'co2_Batt', 'co2in_24h', 'consBatteryVoltage',
             'consolebattp', 'hailBatteryStatus', 'inTempBatteryStatus', 'leafWetBatt1', 'leafWetBatt2',
             'leafWetBatt3', 'leafWetBatt4', 'leafWetBatt5', 'leafWetBatt6', 'leafWetBatt7',
             'leafWetBatt8', 'leak_Batt1', 'leak_Batt2', 'leak_Batt3', 'leak_Batt4', 'lightning_Batt',
             'lightning_disturber_count', 'outTempBatteryStatus', 'piezorain_batt', 'pm1_24h_co2',
             'pm1_24hAQI_co2', 'pm1_RealAQI_co2', 'pm4_24h_co2', 'pm4_24hAQI_co2', 'pm4_RealAQI_co2',
             'pm10_24h_co2', 'pm10_24hAQI_co2', 'pm10_RealAQI_co2', 'pm25_24h_co2', 'pm25_24hAQI_co2',
             'pm25_AQI_24h_ch1', 'pm25_AQI_24h_ch2', 'pm25_AQI_24h_ch3', 'pm25_AQI_24h_ch4',
             'pm25_avg_24h_ch1', 'pm25_avg_24h_ch2', 'pm25_avg_24h_ch3', 'pm25_avg_24h_ch4',
             'pm25_Batt1', 'pm25_Batt2', 'pm25_Batt3', 'pm25_Batt4', 'pm25_RealAQI_ch1',
             'pm25_RealAQI_ch2', 'pm25_RealAQI_ch3', 'pm25_RealAQI_ch4', 'pm25_RealAQI_co2',
             'rain_batt', 'rainBatteryStatus', 'soilMoistBatt1', 'soilMoistBatt1s', 'soilMoistBatt2',
             'soilMoistBatt2s', 'soilMoistBatt3', 'soilMoistBatt3s', 'soilMoistBatt4',
             'soilMoistBatt4s', 'soilMoistBatt5', 'soilMoistBatt5s', 'soilMoistBatt6',
             'soilMoistBatt6s', 'soilMoistBatt7', 'soilMoistBatt7s', 'soilMoistBatt8',
             'soilMoistBatt8s', 'soilMoistBatt9', 'soilMoistBatt9s', 'soilMoistBatt10',
             'soilMoistBatt10s', 'soilMoistBatt11', 'soilMoistBatt11s', 'soilMoistBatt12',
             'soilMoistBatt12s', 'soilMoistBatt13', 'soilMoistBatt13s', 'soilMoistBatt14',
             'soilMoistBatt14s', 'soilMoistBatt15', 'soilMoistBatt15s', 'soilMoistBatt16',
             'soilMoistBatt16s', 'soilTempBatt1', 'soilTempBatt1s', 'soilTempBatt2', 'soilTempBatt2s',
             'soilTempBatt3', 'soilTempBatt3s', 'soilTempBatt4', 'soilTempBatt4s', 'soilTempBatt5',
             'soilTempBatt5s', 'soilTempBatt6', 'soilTempBatt6s', 'soilTempBatt7', 'soilTempBatt7s',
             'soilTempBatt8', 'soilTempBatt8s', 'wh80_batt', 'wh85_batt', 'wh90_batt',
             'windBatteryStatus', 'wn32_batt', 'wn32p_batt', 'wn64_batt', 'wn64batt', 'wqt01_batt',
             'wqt01batt', 'ws1800batt', 'ws6006batt', 'ws6210_batt'),
}
FIRSTLAST = ('stationtype', 'apName')
# accumulator entries added by 1.3.2 and earlier for fields the driver never produces; removed on upgrade
STALE_EXTRACTORS = {
    'sum': ('lightning_noise_count',),
    'last': ('24havpm255', 'co2_24h_avg', 'dayHail', 'daymaxwind', 'gain5', 'lightning_distance',
             'lightning_last_det_time', 'p_dayrain', 'p_eventrain', 'p_hourrain', 'p_monthrain',
             'p_weekrain', 'p_yearrain', 'pm10_24h_avg', 'pm2_51_24h_avg', 'pm2_51_24hav',
             'pm2_52_24h_avg', 'pm2_52_24hav', 'pm2_53_24h_avg', 'pm2_53_24hav', 'pm2_54_24h_avg',
             'pm2_54_24hav', 'pm2_55_24h_avg', 'stormRain', 'totalRain_piezo', 'wh31_ch1_batt',
             'wh31_ch2_batt', 'wh31_ch3_batt', 'wh31_ch4_batt', 'wh31_ch5_batt', 'wh31_ch6_batt',
             'wh31_ch7_batt', 'wh31_ch8_batt', 'wh41_ch1_batt', 'wh41_ch2_batt', 'wh41_ch3_batt',
             'wh41_ch4_batt', 'wh45_batt', 'wh51_ch10_batt', 'wh51_ch11_batt', 'wh51_ch12_batt',
             'wh51_ch13_batt', 'wh51_ch14_batt', 'wh51_ch15_batt', 'wh51_ch16_batt', 'wh51_ch1_batt',
             'wh51_ch2_batt', 'wh51_ch3_batt', 'wh51_ch4_batt', 'wh51_ch5_batt', 'wh51_ch6_batt',
             'wh51_ch7_batt', 'wh51_ch8_batt', 'wh51_ch9_batt', 'wh55_ch1_batt', 'wh55_ch2_batt',
             'wh55_ch3_batt', 'wh55_ch4_batt', 'wh57_batt', 'wh65_batt', 'winddir_avg10m',
             'windspdmph_avg10m', 'wn35_ch1_batt', 'wn35_ch2_batt', 'wn35_ch3_batt', 'wn35_ch4_batt',
             'wn35_ch5_batt', 'wn35_ch6_batt', 'wn35_ch7_batt', 'wn35_ch8_batt'),
}

def tidy_accumulator(config_dict):
    """Remove the [Accumulator] entries 1.3.2 and earlier added for fields the driver never produces
    (only those still as they were added), and total lightningcount over each archive interval instead
    of keeping its last value. Returns the names removed."""
    accum = config_dict.get('Accumulator')
    if not hasattr(accum, 'keys'):
        return []
    removed = []
    stale = {f: {'extractor': x} for x, fields in STALE_EXTRACTORS.items() for f in fields}
    stale['model'] = {'accumulator': 'firstlast', 'extractor': 'last'}
    for field, entry in stale.items():
        if hasattr(accum.get(field), 'keys') and dict(accum[field]) == entry:
            del accum[field]
            removed.append(field)
    if hasattr(accum.get('lightningcount'), 'keys') and dict(accum['lightningcount']) == {'extractor': 'last'}:
        accum['lightningcount']['extractor'] = 'sum'     # it is a per-packet count of strikes
    return removed

DRIVER_CONFIG = f"""
[{SECTION}]
    # This section is for the weewx-EcowittGateway driver/service.

    # the driver to use
    driver = user.{MODULE}

    # IP address of the gateway/console, eg 192.168.1.100
    ip_address = replace_me
    # device API: auto, http (GW1100/GW2000 and consoles) or tcp (GW1000/WH2650,
    # which have no local HTTP API); auto picks the right one
    api = auto
    # port of the TCP API
    tcp_port = 45000

    # how often to poll the device (seconds)
    poll_interval = 20
    # how many attempts to contact the device before giving up
    max_tries = 3
    # wait time in seconds between retries to contact the device
    retry_wait = 2
    # max wait for device to respond to a HTTP request (seconds)
    url_timeout = 10

    # rain gauges to use: both (tipping in rain/rainRate, piezo in p_rain/hail),
    # tipping or piezo (that gauge feeds the WeeWX rain/rainRate fields)
    rain_source = tipping

    # whether to show battery state data for sensors with no signal
    show_all_batt = False
    # whether to log unknown API fields at the info level
    log_unknown_fields = False
    # whether to log 'Next update in N seconds' after each poll when debug is set
    log_next_update = True
    # whether to ignore readings from multi-channel sensors (WN31, WN34, WN35,
    # WH41, WH51, WH54, WH55) while the gateway reports their signal as 0
    ignore_no_signal = False
    # how often to check for device firmware updates (seconds), 0 disables
    firmware_update_check_interval = 86400

    # Ecowitt.net keys, only needed for catchup from Ecowitt.net
    api_key = ""
    app_key = ""

    # report multi-channel sensors (WN31, WN34, WN35, WH41, WH51, WH54, WH55) on fixed
    # channels by hardware ID, whichever gateway channel they are paired on:
    #     <sensor ID> = <channel>
    # 'weectl device --list-sensors' shows the IDs and a ready-made list
    [[sensor_map]]

    # drop readings outside these limits:  <WeeWX field> = min, max[, unit]
    # without a unit, the limits are in METRICWX units (degree_C, meter_per_second,
    # hPa, mm, mm_per_hour); leave min or max empty for no limit on that side
    #     outTemp = -50, 60
    #     windGust = , 75
    [[limits]]

    # where to fetch missed data from at startup: either, device, net or none
    [[catchup]]
        source = either
        grace = 0
        retries = 3

    # write each loop packet to a JSON file for web pages and scripts
    [[loop_json]]
        enable = False
        # file or folder; a relative path is inside the WeeWX web pages folder (HTML_ROOT)
        path = ecwLoop.json
        # units in the file: native (as in the loop packet), us, metric or metricwx
        units = native

    # publish each loop packet to an MQTT broker (needs the paho-mqtt package)
    [[mqtt]]
        enable = False
        host = localhost
        port = 1883
        username = ""
        password = ""
        # messages go to <topic>/loop (json) and/or <topic>/<field> (individual)
        topic = weewx/ecowitt
        # json, individual or both
        format = json
        # native, us, metric or metricwx
        units = native
        qos = 0
        retain = False
        # set tls = True for an encrypted connection (usually port 8883);
        # ca_certs is only needed for a private certificate authority
        tls = False
        ca_certs = ""
"""

TIPPING_MODELS = ('wh40', 'wh69', 'wn20')
# TCP API (GW1000) sensor addresses: (model, first address, channels, first channel)
TCP_CHANNEL_ADDRESSES = (('wn31', 6, 8, 1), ('wh51', 14, 8, 1), ('wh41', 22, 4, 1), ('wh55', 27, 4, 1),
                         ('wn34', 31, 8, 1), ('wn35', 40, 8, 1), ('wh51', 58, 8, 9))
# multi-channel sensor models (the API still reports some by their older WHnn names)
CHANNEL_MODELS = {'wn31': 'wn31', 'wh31': 'wn31', 'wn34': 'wn34', 'wh34': 'wn34', 'wn35': 'wn35', 'wh35': 'wn35',
                  'wh41': 'wh41', 'wh51': 'wh51', 'wh54': 'wh54', 'wh55': 'wh55'}
PIEZO_MODELS = ('ws85', 'ws90', 'wh85', 'wh90')

# Answers that can be given in ECOWITT_<NAME> environment variables instead of at the prompts.
# The kind is 'text', 'required' (text that can't be empty), 'int', 'bool' or a tuple of choices.
ENV_PREFIX = 'ECOWITT_'
UNITS = ('native', 'us', 'metric', 'metricwx')
SETTINGS = {
    'NO_PROMPT': 'bool',
    'IP_ADDRESS': 'required',
    'POLL_INTERVAL': 'int',
    'MODE': ('driver', 'service', 'skip'),
    'RAIN_SOURCE': ('both', 'tipping', 'piezo'),
    'CATCHUP': ('either', 'device', 'net', 'none'),
    'API_KEY': 'text',
    'APP_KEY': 'text',
    'SENSOR_MAP': 'bool',
    'SHOW_ALL_BATT': 'bool',
    'LOOP_ON_INIT': 'bool',
    'LOOP_JSON': 'bool',
    'LOOP_JSON_LOCATION': ('web', 'data', 'tmp', 'custom'),
    'LOOP_JSON_PATH': 'required',
    'LOOP_JSON_UNITS': UNITS,
    'MQTT': 'bool',
    'MQTT_HOST': 'required',
    'MQTT_TLS': 'bool',
    'MQTT_PORT': 'int',
    'MQTT_USERNAME': 'text',
    'MQTT_PASSWORD': 'text',
    'MQTT_TOPIC': 'required',
    'MQTT_FORMAT': ('json', 'individual', 'both'),
    'MQTT_UNITS': UNITS,
    'MQTT_RETAIN': 'bool',
    'MQTT_CA_CERTS': 'text',
}
SECRETS = ('API_KEY', 'APP_KEY', 'MQTT_PASSWORD')
YES, NO = ('y', 'yes', 'true', 'on', '1'), ('n', 'no', 'false', 'off', '0')


class SettingError(ValueError):
    """An ECOWITT_* environment variable has a value the installer can't use."""


def _env(name):
    """The stripped value of environment variable ECOWITT_<name>, or None if it isn't set."""
    value = os.environ.get(ENV_PREFIX + name)
    return None if value is None else value.strip()


def _env_bool(name):
    value = _env(name)
    return None if value is None else value.lower() in YES


def check_env():
    """Check every ECOWITT_* variable before anything is changed; returns warnings for unknown names."""
    warnings = []
    for var in sorted(v for v in os.environ if v.startswith(ENV_PREFIX)):
        name, value = var[len(ENV_PREFIX):], _env(var[len(ENV_PREFIX):])
        kind = SETTINGS.get(name)
        if kind is None:
            warnings.append(f'Ignoring unknown setting {var}')
        elif kind == 'required' and not value:
            raise SettingError(f'{var} is empty')
        elif kind == 'int' and not (value.isdigit() and int(value) > 0):
            raise SettingError(f"{var}='{value}' is not a whole number above 0")
        elif kind == 'bool' and value.lower() not in YES + NO:
            raise SettingError(f"{var}='{value}' is not yes or no")
        elif isinstance(kind, tuple) and value.lower() not in kind:
            raise SettingError(f"{var}='{value}' is not one of: {', '.join(kind)}")
    return warnings


def _version_tuple(version):
    """'5.4.0', '5.5.2b1' -> (5, 4, 0), (5, 5, 2)"""
    return tuple(int(re.match(r'\d+', part).group()) if re.match(r'\d+', part) else 0
                 for part in version.split('.')[:3])


def loader():
    return EcowittGatewayInstaller()


class EcowittGatewayInstaller(ExtensionInstaller):
    def __init__(self):
        if _version_tuple(weewx.__version__) < MIN_WEEWX_VERSION:
            raise weewx.UnsupportedFeature(f"weewx-EcowittGateway requires WeeWX "
                                           f"{'.'.join(map(str, MIN_WEEWX_VERSION))} or later, "
                                           f"found {weewx.__version__}")
        config = configobj.ConfigObj(io.StringIO(DRIVER_CONFIG))
        accum = {f: {'extractor': x} for x, fields in EXTRACTORS.items() for f in fields}
        for f in FIRSTLAST:
            accum[f] = {'accumulator': 'firstlast', 'extractor': 'last'}
        config['Accumulator'] = accum
        super().__init__(
            version=VERSION,
            name='weewx-EcowittGateway',
            description='WeeWX driver/service for Ecowitt gateways and consoles (local HTTP API, or TCP API for the GW1000).',
            author='Ian Millard',
            author_email='',
            files=[('bin/user', [f'bin/user/{MODULE}.py'])],
            config=config,
            # declared so 'weectl extension uninstall' removes it; configure() keeps it only in service mode
            data_services=SERVICE,
        )

    # -- helpers ---------------------------------------------------------------

    def _out(self, engine, msg=''):
        printer = getattr(engine, 'printer', None)
        if printer is not None:
            printer.out(msg)
        else:
            print(msg)

    @staticmethod
    def _interactive():
        return sys.stdin is not None and sys.stdin.isatty() and not _env_bool('NO_PROMPT')

    def _from_env(self, prompt, name, options=None):
        """The answer to a question from ECOWITT_<name>, or None if that isn't set."""
        value = _env(name) if name else None
        if value is None:
            return None
        if options:
            value = value.lower()
            if value not in options:
                raise SettingError(f"{ENV_PREFIX}{name}='{value}' can't be used here: use one of "
                                   f"{', '.join(options)}")
        shown = '********' if name in SECRETS and value else value
        self._out(getattr(self, '_engine', None), f'{prompt.rstrip("?")}: {shown}  (from {ENV_PREFIX}{name})')
        return value

    def _ask(self, prompt, default, options=None, env=None):
        """Prompt for a value. The answer comes from ECOWITT_<env> if it is set, and is the default
        when there is no terminal."""
        value = self._from_env(prompt, env, options)
        if value is not None:
            return value
        if not self._interactive():
            return default
        opts = f" ({'/'.join(options)})" if options else ''
        while True:
            answer = input(f'{prompt}{opts} [{default}]: ').strip() or str(default)
            if options is None or answer.lower() in options:
                return answer.lower() if options else answer
            print(f"    Please enter one of: {', '.join(options)}")

    def _ask_yes(self, prompt, default=True, env=None):
        if env and _env(env) is not None:
            answer = _env_bool(env)
            self._out(getattr(self, '_engine', None),
                      f"{prompt.rstrip('?')}: {'yes' if answer else 'no'}  (from {ENV_PREFIX}{env})")
            return answer
        return self._ask(prompt, 'y' if default else 'n', ['y', 'n']) == 'y'

    @staticmethod
    def _get_json(ip, command, **params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        with urllib.request.urlopen(f'http://{ip}/{command}?{query}', timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))

    @staticmethod
    def _tcp(ip, cmd, port=45000):
        """Data from a binary TCP API command (GW1000/WH2650), or None."""
        try:
            body = bytes([cmd, 3])
            with socket.create_connection((ip.split(':')[0], port), timeout=5) as sock:
                sock.sendall(b'\xff\xff' + body + bytes([sum(body) & 0xFF]))
                resp = b''
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                    size_len = 2 if cmd == 0x3C else 1
                    if len(resp) >= 3 + size_len and len(resp) >= 2 + int.from_bytes(resp[3:3 + size_len], 'big'):
                        break
        except OSError:
            return None
        if len(resp) < 5 or resp[:2] != b'\xff\xff' or resp[2] != cmd or sum(resp[2:-1]) & 0xFF != resp[-1]:
            return None
        return resp[(5 if cmd == 0x3C else 4):-1]

    def _probe_tcp(self, ip):
        """_probe() for a GW1000/WH2650 over the TCP API."""
        firmware = self._tcp(ip, 0x50)
        if not firmware:
            return None, None, None
        model = firmware[1:1 + firmware[0]].decode('ascii', 'replace')
        gauges, channels = set(), []
        data = self._tcp(ip, 0x3C) or b''
        for n in range(0, len(data) - 6, 7):
            address, sid = data[n], int.from_bytes(data[n + 1:n + 5], 'big')
            if sid in (0xFFFFFFFE, 0xFFFFFFFF):
                continue
            if address == 3:
                gauges.add('tipping')
            elif address == 48:
                gauges.add('piezo')
            for tcp_model, first, count, ch1 in TCP_CHANNEL_ADDRESSES:
                if first <= address < first + count:
                    channels.append((tcp_model, address - first + ch1, f'{sid:X}'))
        order = list(dict.fromkeys(CHANNEL_MODELS.values()))
        channels.sort(key=lambda c: (order.index(c[0]), c[1]))
        return f'{model} (TCP API)', gauges, channels

    def _probe(self, ip):
        """Return (model, set of paired gauge types, [(sensor model, channel, ID)]) or Nones if unreachable."""
        try:
            version = self._get_json(ip, 'get_version').get('version', '')
        except Exception:
            return self._probe_tcp(ip)
        gauges, channels = set(), []
        try:
            for page in range(1, 6):
                sensors = self._get_json(ip, 'get_sensors_info', page=page)
                if not sensors:
                    break
                for sensor in sensors:
                    if str(sensor.get('id', '')).lower() in ('fffffffe', 'ffffffff'):
                        continue
                    img = str(sensor.get('img', '')).lower()
                    if img in TIPPING_MODELS:
                        gauges.add('tipping')
                    elif img in PIEZO_MODELS:
                        gauges.add('piezo')
                    match = re.search(r'CH(\d+)', str(sensor.get('name', '')))
                    if img in CHANNEL_MODELS and match:
                        channels.append((CHANNEL_MODELS[img], int(match.group(1)), str(sensor['id'])))
        except Exception:
            pass
        order = list(dict.fromkeys(CHANNEL_MODELS.values()))
        channels.sort(key=lambda c: (order.index(c[0]), c[1]))
        return version[8:].strip() or 'unknown model', gauges, channels

    @staticmethod
    def _place_after_station(config_dict):
        """Move the driver section so it directly follows [Station]."""
        sections = config_dict.sections
        if SECTION in sections and 'Station' in sections:
            sections.remove(SECTION)
            sections.insert(sections.index('Station') + 1, SECTION)

    # -- configuration -------------------------------------------------------

    def configure(self, engine):
        self._engine = engine
        config_dict = engine.config_dict
        legacy = SECTION not in config_dict and LEGACY_SECTION in config_dict
        existing = config_dict.get(LEGACY_SECTION if legacy else SECTION, {})

        def out(msg=''):
            self._out(engine, msg)

        for warning in check_env():   # raises SettingError before anything is changed
            out(warning)
        out()
        out('Configuring weewx-EcowittGateway')
        if self._interactive():
            out('Press Enter to accept the value shown in [brackets].')
        out()
        if legacy:
            out(f'Existing [{LEGACY_SECTION}] settings found; they will be moved to [{SECTION}].')
            out()

        # gateway address, checked by contacting the device
        ip = existing.get('ip_address', 'replace_me')
        ip_from_env = _env('IP_ADDRESS') is not None
        model = gauges = channels = None
        while True:
            ip = self._ask('Gateway IP address, eg 192.168.1.100', ip, env='IP_ADDRESS')
            if ip == 'replace_me' or not (self._interactive() or ip_from_env):
                break
            model, gauges, channels = self._probe(ip)
            if model:
                out(f'    Found {model} at {ip}')
                break
            out(f'    No response from a gateway at {ip}.')
            if ip_from_env or not self._interactive():
                out('    Using this address anyway; paired sensors and rain gauges could not be checked.')
                break
            if self._ask_yes('    Use this address anyway?', default=False):
                break
        poll = self._ask('Poll interval in seconds', existing.get('poll_interval', 20), env='POLL_INTERVAL')

        station_type = config_dict.get('Station', {}).get('station_type')
        if station_type in (SECTION, LEGACY_SECTION):
            default_mode = 'driver'
        elif existing.get('ip_address', 'replace_me') != 'replace_me':
            default_mode = 'service'
        else:
            default_mode = 'driver' if self._interactive() or ip_from_env else 'skip'
        mode = self._ask('Use as the station driver, as a service alongside another driver, or skip',
                         default_mode, ['driver', 'service', 'skip'], env='MODE')
        if mode == 'driver' and ip == 'replace_me':
            out('    No gateway IP address given, so the station driver is not being changed.')
            mode = 'skip'

        both = {'tipping', 'piezo'}
        rain_source = existing.get('rain_source', 'both' if gauges == both else 'tipping')
        if mode == 'driver':
            if gauges:
                out(f"    Paired rain gauges: {' and '.join(sorted(gauges))}")
            if _env('RAIN_SOURCE') is not None:
                rain_source = self._ask('Rain gauges to use', rain_source, ['both', 'tipping', 'piezo'],
                                        env='RAIN_SOURCE')
            elif gauges == {'piezo'}:
                rain_source = 'piezo'
            elif gauges == {'tipping'}:
                rain_source = 'tipping'
            else:
                if self._interactive():
                    out("    both    = record both gauges: tipping in 'rain'/'rainRate', "
                        "piezo in 'p_rain'/'hail'/'p_rainrate'")
                    out("    tipping = WeeWX 'rain'/'rainRate' from the tipping gauge")
                    out("    piezo   = WeeWX 'rain'/'rainRate' from the piezo gauge")
                rain_source = self._ask('Rain gauges to use', rain_source, ['both', 'tipping', 'piezo'],
                                        env='RAIN_SOURCE')

        catchup = existing.get('catchup', {}).get('source', 'either')
        if model and model.endswith('(TCP API)'):
            out('    This gateway has no SD card or local HTTP API, so missed data can only come')
            out('    from Ecowitt.net (net), and only if it uploads there.')
            catchup = self._ask('Fetch missed data at startup from', 'net' if catchup == 'device' else catchup,
                                ['net', 'none', 'either'], env='CATCHUP')
        else:
            catchup = self._ask('Fetch missed data at startup from', catchup, ['either', 'device', 'net', 'none'],
                                env='CATCHUP')
        api_key, app_key = existing.get('api_key', ''), existing.get('app_key', '')
        if catchup in ('either', 'net') and self._interactive():
            out('    Ecowitt.net keys are only needed to fetch missed data from Ecowitt.net')
            out('    (press Enter to leave them blank).')
        api_key = self._ask('    Ecowitt.net API key', api_key or '', env='API_KEY') \
            if catchup in ('either', 'net') else api_key
        app_key = self._ask('    Ecowitt.net application key', app_key or '', env='APP_KEY') \
            if catchup in ('either', 'net') else app_key
        sensor_map = self._ask_sensor_map(existing.get('sensor_map', {}), channels, out)
        show_batt = self._ask_yes('Show battery state for sensors with no signal?',
                                  str(existing.get('show_all_batt', 'False')).lower() == 'true', env='SHOW_ALL_BATT')
        loop_on_init = mode != 'driver' or self._ask_yes(
            'Keep retrying at startup if the gateway cannot be reached (loop_on_init)?', True, env='LOOP_ON_INIT')
        loop_json = self._ask_loop_json(config_dict, existing.get('loop_json', {}), out)
        mqtt = self._ask_mqtt(existing.get('mqtt', {}), out)

        if getattr(engine, 'dry_run', False):
            out('Dry run: weewx.conf not changed.')
            return False
        if mode != 'service':
            self._remove_service(config_dict)

        # driver section: create it after [Station] with all defaults, then apply the answers
        template = configobj.ConfigObj(io.StringIO(DRIVER_CONFIG))
        if legacy:
            # move the old section's settings (and comments) to the new section name
            config_dict[SECTION] = {}
            config_dict.comments[SECTION] = config_dict.comments.get(LEGACY_SECTION, [''])
            _copy_section(config_dict[LEGACY_SECTION], config_dict[SECTION])
            config_dict[SECTION]['driver'] = f'user.{MODULE}'
            del config_dict[LEGACY_SECTION]
            if config_dict.get('Station', {}).get('station_type') == LEGACY_SECTION:
                config_dict['Station']['station_type'] = SECTION
        if SECTION not in config_dict:
            config_dict[SECTION] = {}
            config_dict.comments[SECTION] = template.comments[SECTION] or ['']
        self._place_after_station(config_dict)
        _merge_missing(config_dict, template)
        removed = tidy_accumulator(config_dict)
        if removed:
            out(f'Removed {len(removed)} unused [Accumulator] entries left by earlier versions.')
        section = config_dict[SECTION]
        section.update({'ip_address': ip, 'poll_interval': str(poll), 'rain_source': rain_source,
                        'show_all_batt': str(show_batt), 'api_key': api_key, 'app_key': app_key})
        section['catchup']['source'] = catchup
        for sid, (channel, note) in sensor_map.items():
            if sid not in section['sensor_map']:
                section['sensor_map'][sid] = channel
                section['sensor_map'].inline_comments[sid] = f'# {note}'
        section['loop_json'].update(loop_json)
        section['mqtt'].update(mqtt)

        if mode == 'driver':
            config_dict.setdefault('Station', {})['station_type'] = SECTION
            config_dict['loop_on_init'] = '1' if loop_on_init else '0'
            config_dict.setdefault('StdArchive', {})['record_generation'] = 'software'
            calc = config_dict.setdefault('StdWXCalculate', {})
            calc.setdefault('Calculations', {})['rain'] = 'prefer_hardware'
            if 'rain' in calc.get('Delta', {}):
                calc['Delta'].pop('rain')
                if not calc['Delta']:
                    calc.pop('Delta')
            out()
            out(f'Station driver set to {SECTION} (user.{MODULE}); archive records generated in software.')
        elif mode == 'service':
            services = config_dict.setdefault('Engine', {}).setdefault('Services', {})
            data_services = _as_list(services.get('data_services', []))
            if SERVICE not in data_services:
                services['data_services'] = data_services + [SERVICE]
            out()
            out(f'Added {SERVICE} to data_services.')
        else:
            out()
            out('Gateway settings saved; the station driver and services were not changed.')
        out('Restart WeeWX to start using the new settings.')
        return True

    def _ask_sensor_map(self, current, channels, out):
        """Offer to lock multi-channel sensors to their channels; returns {ID: (channel, comment)}."""
        result = {sid: (str(ch), '') for sid, ch in current.items()}
        if not channels:
            return result
        known = {_sensor_id(sid) for sid in current}
        new = [c for c in channels if _sensor_id(c[2]) not in known]
        out('    Multi-channel sensors found:')
        for model, channel, sid in channels:
            state = '' if _sensor_id(sid) not in known else '  (already in the sensor map)'
            out(f'        {model.upper():<5} CH{channel:<3} ID {sid}{state}')
        if not new:
            return result
        out('    Locking a sensor to its channel keeps its data in the same WeeWX fields')
        out('    if it is re-paired onto a different gateway channel later.')
        what = 'these sensors' if not current else f'the {len(new)} new sensor(s)'
        if self._ask_yes(f'Lock {what} to their current channels (sensor_map)?', True, env='SENSOR_MAP'):
            for model, channel, sid in new:
                result[sid] = (str(channel), model.upper())
        return result

    def _ask_secret(self, prompt, current, env=None):
        """Ask for a password without echoing it; Enter keeps the current value."""
        value = self._from_env(prompt, env)
        if value is not None:
            return value
        if not self._interactive():
            return current
        hint = ' (Enter keeps the current one)' if current else ' (Enter for none)'
        return getpass.getpass(f'{prompt}{hint}: ') or current

    def _ask_mqtt(self, current, out):
        """Ask whether and how to publish loop data to an MQTT broker; returns the [[mqtt]] settings."""
        def cur(key, default=''):
            return str(current.get(key, default))

        enable = self._ask_yes('Publish each loop packet to an MQTT broker?', cur('enable', 'False').lower() == 'true',
                               env='MQTT')
        if not enable:
            return {'enable': 'False'}
        if importlib.util.find_spec('paho') is None:
            out('    Note: the paho-mqtt package is needed for MQTT. Install it with')
            out('          sudo apt install python3-paho-mqtt      (Debian package install)')
            out('          pip install paho-mqtt                   (pip install, in the WeeWX environment)')
        host = self._ask('    Broker host name or IP address', cur('host', 'localhost'), env='MQTT_HOST')
        tls = self._ask_yes('    Use an encrypted (TLS) connection?', cur('tls', 'False').lower() == 'true', env='MQTT_TLS')
        default_port = cur('port', '8883' if tls else '1883')
        if tls and default_port == '1883':
            default_port = '8883'
        port = self._ask('    Broker port', default_port, env='MQTT_PORT')
        if self._interactive():
            try:
                with socket.create_connection((host, int(port)), timeout=3):
                    out(f'    Broker {host}:{port} is reachable')
            except (OSError, ValueError) as e:
                out(f'    Could not reach {host}:{port} ({e}); the driver will keep retrying once WeeWX starts.')
        username = self._ask('    Username (Enter for none)', cur('username'), env='MQTT_USERNAME')
        password = self._ask_secret('    Password', cur('password'), env='MQTT_PASSWORD') if username else ''
        topic = self._ask('    Topic', cur('topic', 'weewx/ecowitt'), env='MQTT_TOPIC').rstrip('/')
        if self._interactive() and _env('MQTT_FORMAT') is None:
            out(f'    json       = one message with all fields on {topic}/loop')
            out(f'    individual = one message per field, e.g. {topic}/outTemp')
            out('    both       = both of the above')
        fmt = self._ask('    Message format', cur('format', 'json').lower(), ['json', 'individual', 'both'],
                        env='MQTT_FORMAT')
        units = self._ask('    Units', cur('units', 'native').lower(), list(UNITS), env='MQTT_UNITS')
        retain = self._ask_yes('    Retain the latest messages on the broker?',
                               cur('retain', 'False').lower() == 'true', env='MQTT_RETAIN')
        settings = {'enable': 'True', 'host': host, 'port': port, 'username': username, 'password': password,
                    'topic': topic, 'format': fmt, 'units': units, 'retain': str(retain), 'tls': str(tls)}
        if tls:
            settings['ca_certs'] = self._ask('    CA certificate file (Enter for the system certificates)',
                                             cur('ca_certs'), env='MQTT_CA_CERTS')
        return settings

    def _ask_loop_json(self, config_dict, current, out):
        """Ask whether and where to write ecwLoop.json; returns the [[loop_json]] settings."""
        name = 'ecwLoop.json'
        weewx_root = config_dict.get('WEEWX_ROOT', '')
        html_root = config_dict.get('StdReport', {}).get('HTML_ROOT', 'public_html')
        web_dir = os.path.abspath(os.path.join(weewx_root, html_root))
        locations = {'web': name, 'data': os.path.join(weewx_root, name), 'tmp': os.path.join('/tmp', name)}
        path = str(current.get('path', name))
        location = next((k for k, v in locations.items() if v == path), 'custom')
        enable = self._ask_yes(f'Write each loop packet to {name} (for web pages and scripts)?',
                               str(current.get('enable', 'False')).lower() == 'true', env='LOOP_JSON')
        if not enable:
            return {'enable': 'False'}
        if _env('LOOP_JSON_PATH') is not None and _env('LOOP_JSON_LOCATION') is None:
            location = 'custom'   # a path on its own means a custom location
        else:
            if self._interactive() and _env('LOOP_JSON_LOCATION') is None:
                out(f'    web    = WeeWX web pages folder: {os.path.join(web_dir, name)}')
                out(f"    data   = WeeWX data folder: {locations['data']}")
                out(f"    tmp    = {locations['tmp']} (often held in memory, which saves SD card writes)")
                out('    custom = a folder or file path of your choice')
            location = self._ask(f'Where should {name} be written', location, ['web', 'data', 'tmp', 'custom'],
                                 env='LOOP_JSON_LOCATION')
        if location == 'custom':
            while True:
                path = self._ask('    Folder or full file path', path if path not in locations.values() else '',
                                 env='LOOP_JSON_PATH')
                if path:
                    break
                if not self._interactive():
                    raise SettingError(f'{ENV_PREFIX}LOOP_JSON_PATH is needed for a custom {name} location')
                out('    Please enter a path.')
            folder = path if path.endswith(os.sep) or os.path.isdir(path) else os.path.dirname(path)
            if folder and not os.path.isdir(os.path.expanduser(folder)):
                out(f'    Note: {folder} does not exist yet; create it and make it writable by WeeWX.')
        else:
            path = locations[location]
        units = self._ask(f'Units for {name}', str(current.get('units', 'native')).lower(), list(UNITS),
                          env='LOOP_JSON_UNITS')
        return {'enable': 'True', 'path': path, 'units': units}

    @staticmethod
    def _remove_service(config_dict):
        services = config_dict.get('Engine', {}).get('Services', {})
        if 'data_services' in services:
            data_services = _as_list(services['data_services'])
            if SERVICE in data_services:
                services['data_services'] = [s for s in data_services if s != SERVICE]


def _sensor_id(value):
    """A hardware sensor ID in a comparable form: upper case hex, no 0x prefix or leading zeros."""
    text = str(value).strip().upper()
    text = text[2:] if text.startswith('0X') else text
    return text.lstrip('0') or '0'


def _as_list(value):
    if isinstance(value, str):
        return [v.strip() for v in value.split(',') if v.strip()]
    return list(value or [])


def _copy_section(source, target):
    """Copy all keys, subsections and comments from one ConfigObj section to another."""
    for key in source.scalars:
        target[key] = source[key]
    for key in source.sections:
        target[key] = {}
        _copy_section(source[key], target[key])
    for key in source.scalars + source.sections:
        target.comments[key] = source.comments.get(key, [])
        target.inline_comments[key] = source.inline_comments.get(key)


def _merge_missing(target, source):
    """Add keys (with their comments) from source that are missing in target."""
    for key in source:
        if isinstance(source[key], dict):
            if key not in target:
                target[key] = {}
                target.comments[key] = source.comments.get(key, [])
            _merge_missing(target[key], source[key])
        elif key not in target:
            target[key] = source[key]
            target.comments[key] = source.comments.get(key, [])
