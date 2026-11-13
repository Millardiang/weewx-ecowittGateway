"""
This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU General Public License for more details.

                 Installer for weewx-ecowittGateway

Built on the installer for Gary Roderick's Ecowitt Local HTTP API driver
(ecowitt_http.py 0.1.0a28).

Version: 2.0.0

The installer asks for the gateway settings and writes them to weewx.conf,
with the [EcowittGateway] section placed directly after [Station]. Without a
terminal (for example from a script) it uses the existing settings or the
defaults. Every question can also be answered from an ECOWITT_* environment
variable (see SETTINGS below and the README), so an install can be fully
scripted:

    sudo ECOWITT_IP_ADDRESS=192.168.1.100 ECOWITT_MODE=driver \
        weectl extension install --yes weewx-ecowittGateway-<version>.zip

ECOWITT_NO_PROMPT=yes stops the installer asking anything, even at a terminal.

Revision History
    2.0.0 (12 November 2026)
        -   installs weewx-ecowittGateway; moves an [EcowittHttp] section
            (ecowitt_http.py) to [EcowittGateway]
        -   asks for the gateway settings (or reads ECOWITT_* environment
            variables) and configures the station driver or service, the
            rain source, catchup, sensor map, ecwLoop.json and MQTT
        -   removes the [Accumulator] entries earlier extensions added for
            fields the driver doesn't produce (as the station driver)
    xx May 2025      v0.1.0
        -   initial implementation
"""

# python imports
import configobj
import getpass
import glob
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import sys
import urllib.request
from setup import ExtensionInstaller

# WeeWX imports
import weewx


REQUIRED_WEEWX_VERSION = "5.0.0"
DRIVER_VERSION = "2.0.0"
SECTION = 'EcowittGateway'
# ecowitt_http.py's section and service, moved to the names above
LEGACY_SECTION = 'EcowittHttp'
MODULE = 'user.weewx-ecowittGateway'
LEGACY_MODULE = 'user.ecowitt_http'
EXPORT_SERVICE = f'{MODULE}.EcowittGatewayExport'
SERVICE = f'{MODULE}.EcowittHttpService'
# ecowitt_http.py's extension name and file
LEGACY_EXTENSION = 'Ecowitt_HTTP'
LEGACY_FILE = 'ecowitt_http.py'
# an earlier extension this replaces, installed as weewx-EcowittGateway:
# extension, file and module
OLD_NAME = 'weewx-EcowittGateway'
OLD_MODULE = f'user.{OLD_NAME}'
# earlier extensions this replaces: (extension name, file in bin/user, description)
REPLACED_EXTENSIONS = ((LEGACY_EXTENSION, LEGACY_FILE, 'the Ecowitt_HTTP extension (ecowitt_http.py)'),
                       (OLD_NAME, f'{OLD_NAME}.py', f'the {OLD_NAME} extension (now weewx-ecowittGateway)'))
# define our config as a multiline string so we can preserve comments
ecowitt_config = """
[EcowittGateway]
    # This section is for the weewx-ecowittGateway driver (Ecowitt local HTTP API).

    # The device IP address:
    ip_address = www.xxx.yyy.zzz
    
    # How often to poll the API, default is every 20 seconds:
    poll_interval = 20

    # The driver to use:
    driver = user.weewx-ecowittGateway

    # Is a WN32P used for indoor temperature, humidity and pressure
    wn32_indoor = False

    # Is a WN32 used for outdoor temperature and humidity
    wn32_outdoor = False

    # Whether to show battery state for sensors with no signal
    show_all_batt = False
    # Whether to record WeeWX radiation (W/m2), worked out from the light
    # sensor's illuminance (lux) with Ecowitt's factor
    radiation_from_illuminance = False

    # With debug logging on, whether to log 'Next update in N seconds' after
    # every poll
    log_next_update = True

    # Whether to ignore the readings of multi-channel sensors (WN31, WN34,
    # WN35, WH41, WH51, WH54, WH55) while the gateway reports their signal as 0
    ignore_no_signal = False

    # Fetching missed data when WeeWX starts. source: either (the device's SD
    # card, else Ecowitt.net), device, net or none. Ecowitt.net needs the API
    # and application keys of the account the device uploads to.
    [[catchup]]
        source = either
        api_key = ""
        app_key = ""

    # Multi-channel sensors (WN31, WN34, WN35, WH41, WH51, WH54, WH55)
    # reported on fixed channels by sensor ID, so re-pairing a sensor does not
    # move its data to other WeeWX fields. One '<sensor ID> = <channel>' entry
    # per sensor, for example:
    #     C0FFEE = 3
    # 'weectl device --sensor-map' lists the paired sensors and their IDs.
    [[sensor_map]]

    # Readings outside these limits are dropped (set to None), in loop packets
    # and catchup records: '<WeeWX field> = min, max' or 'min, max, unit'.
    # Without a unit the limits are in the gateway's units (degree_C,
    # meter_per_second, hPa, mm, mm_per_hour ...). Leave min or max empty for
    # no limit on that side, for example:
    #     outTemp = -50, 60
    #     windGust = , 75
    [[limits]]

    # Write each loop packet to a JSON file for web pages and scripts. A
    # relative path is relative to the WeeWX web pages folder (HTML_ROOT).
    # units: native, us, metric or metricwx
    [[loop_json]]
        enable = False
        path = ecwLoop.json
        units = native

    # Publish each loop packet to an MQTT broker (needs paho-mqtt).
    # format: json (one message on <topic>/loop), individual (one message per
    # field on <topic>/<field>) or both
    [[mqtt]]
        enable = False
        host = localhost
        port = 1883
        username = ""
        password = ""
        topic = weewx/ecowitt
        format = json
        units = native
        retain = False
        tls = False

[StdWXCalculate]

    [[Calculations]]
        t_rain = prefer_hardware
        p_rain = prefer_hardware
        lightning_strike_count = prefer_hardware
        
    [[Delta]]
        [[[t_rain]]]
            input = t_rainyear
        [[[p_rain]]]
            input = p_rainyear
        [[[lightning_strike_count]]]
            input = lightningcount
            
[Accumulator]
    # Start weewx-ecowittGateway extractors
    [[t_rain]]
        extractor = sum
    [[p_rain]]
        extractor = sum
    [[lightning_strike_count]]
        extractor = sum
    [[lightningdist]]
        extractor = last
    [[lightningdettime]]
        extractor = last
    [[lightningcount]]
        extractor = last
    [[pm25_ch1_24h]]
        extractor = last
    [[pm25_ch2_24h]]
        extractor = last
    [[pm25_ch3_24h]]
        extractor = last
    [[pm25_ch4_24h]]
        extractor = last
    [[t_rainevent]]
        extractor = last
    [[t_rainhour]]
        extractor = last
    [[t_rainday]]
        extractor = last
    [[t_rain24]]
        extractor = last
    [[t_rainweek]]
        extractor = last
    [[t_rainmonth]]
        extractor = last
    [[t_rainyear]]
        extractor = last
    [[is_raining]]
        extractor = last
    [[p_rainevent]]
        extractor = last
    [[p_rainhour]]
        extractor = last
    [[p_rainday]]
        extractor = last
    [[p_rain24]]
        extractor = last
    [[p_rainweek]]
        extractor = last
    [[p_rainmonth]]
        extractor = last
    [[p_rainyear]]
        extractor = last
    [[windDir10]]
        extractor = last
    [[daymaxwind]]
        extractor = last
    [[wh25_batt]]
        extractor = last
    [[wh25_sig]]
        extractor = last
    [[wh26_batt]]
        extractor = last
    [[wh26_sig]]
        extractor = last
    [[wn31_ch1_batt]]
        extractor = last
    [[wn31_ch1_sig]]
        extractor = last
    [[wn31_ch2_batt]]
        extractor = last
    [[wn31_ch2_sig]]
        extractor = last
    [[wn31_ch3_batt]]
        extractor = last
    [[wn31_ch3_sig]]
        extractor = last
    [[wn31_ch4_batt]]
        extractor = last
    [[wn31_ch4_sig]]
        extractor = last
    [[wn31_ch5_batt]]
        extractor = last
    [[wn31_ch5_sig]]
        extractor = last
    [[wn31_ch6_batt]]
        extractor = last
    [[wn31_ch6_sig]]
        extractor = last
    [[wn31_ch7_batt]]
        extractor = last
    [[wn31_ch7_sig]]
        extractor = last
    [[wn31_ch8_batt]]
        extractor = last
    [[wn31_ch8_sig]]
        extractor = last
    [[wn34_ch1_batt]]
        extractor = last
    [[wn34_ch1_sig]]
        extractor = last
    [[wn34_ch2_batt]]
        extractor = last
    [[wn34_ch2_sig]]
        extractor = last
    [[wn34_ch3_batt]]
        extractor = last
    [[wn34_ch3_sig]]
        extractor = last
    [[wn34_ch4_batt]]
        extractor = last
    [[wn34_ch4_sig]]
        extractor = last
    [[wn34_ch5_batt]]
        extractor = last
    [[wn34_ch5_sig]]
        extractor = last
    [[wn34_ch6_batt]]
        extractor = last
    [[wn34_ch6_sig]]
        extractor = last
    [[wn34_ch7_batt]]
        extractor = last
    [[wn34_ch7_sig]]
        extractor = last
    [[wn34_ch8_batt]]
        extractor = last
    [[wn34_ch8_sig]]
        extractor = last
    [[wh41_ch1_batt]]
        extractor = last
    [[wh41_ch1_sig]]
        extractor = last
    [[wh41_ch2_batt]]
        extractor = last
    [[wh41_ch2_sig]]
        extractor = last
    [[wh41_ch3_batt]]
        extractor = last
    [[wh41_ch3_sig]]
        extractor = last
    [[wh41_ch4_batt]]
        extractor = last
    [[wh41_ch4_sig]]
        extractor = last
    [[wh51_ch1_batt]]
        extractor = last
    [[wh51_ch1_sig]]
        extractor = last
    [[wh51_ch2_batt]]
        extractor = last
    [[wh51_ch2_sig]]
        extractor = last
    [[wh51_ch3_batt]]
        extractor = last
    [[wh51_ch3_sig]]
        extractor = last
    [[wh51_ch4_batt]]
        extractor = last
    [[wh51_ch4_sig]]
        extractor = last
    [[wh51_ch5_batt]]
        extractor = last
    [[wh51_ch5_sig]]
        extractor = last
    [[wh51_ch6_batt]]
        extractor = last
    [[wh51_ch6_sig]]
        extractor = last
    [[wh51_ch7_batt]]
        extractor = last
    [[wh51_ch7_sig]]
        extractor = last
    [[wh51_ch8_batt]]
        extractor = last
    [[wh51_ch8_sig]]
        extractor = last
    [[wh54_ch1_batt]]
        extractor = last
    [[wh54_ch1_sig]]
        extractor = last
    [[wh54_ch2_batt]]
        extractor = last
    [[wh54_ch2_sig]]
        extractor = last
    [[wh54_ch3_batt]]
        extractor = last
    [[wh54_ch3_sig]]
        extractor = last
    [[wh54_ch4_batt]]
        extractor = last
    [[wh54_ch4_sig]]
        extractor = last
    [[wh55_ch1_batt]]
        extractor = last
    [[wh55_ch1_sig]]
        extractor = last
    [[wh55_ch2_batt]]
        extractor = last
    [[wh55_ch2_sig]]
        extractor = last
    [[wh55_ch3_batt]]
        extractor = last
    [[wh55_ch3_sig]]
        extractor = last
    [[wh55_ch4_batt]]
        extractor = last
    [[wh55_ch4_sig]]
        extractor = last
    [[wh57_batt]]
        extractor = last
    [[wh57_sig]]
        extractor = last
    [[ws90_batt]]
        extractor = last
    [[ws90_sig]]
        extractor = last
    [[wh40_batt]]
        extractor = last
    [[wh40_sig]]
        extractor = last
    [[wh45_batt]]
        extractor = last
    [[wh45_sig]]
        extractor = last
    [[wh68_batt]]
        extractor = last
    [[wh68_sig]]
        extractor = last
    [[wh69_batt]]
        extractor = last
    [[wh69_sig]]
        extractor = last
    [[ws80_batt]]
        extractor = last
    [[ws80_sig]]
        extractor = last
    [[ws85_batt]]
        extractor = last
    [[ws85_sig]]
        extractor = last
    [[wn35_ch1_batt]]
        extractor = last
    [[wn35_ch1_sig]]
        extractor = last
    [[wn35_ch2_batt]]
        extractor = last
    [[wn35_ch2_sig]]
        extractor = last
    [[wn35_ch3_batt]]
        extractor = last
    [[wn35_ch3_sig]]
        extractor = last
    [[wn35_ch4_batt]]
        extractor = last
    [[wn35_ch4_sig]]
        extractor = last
    [[wn35_ch5_batt]]
        extractor = last
    [[wn35_ch5_sig]]
        extractor = last
    [[wn35_ch6_batt]]
        extractor = last
    [[wn35_ch6_sig]]
        extractor = last
    [[wn35_ch7_batt]]
        extractor = last
    [[wn35_ch7_sig]]
        extractor = last
    [[wn35_ch8_batt]]
        extractor = last
    [[wn35_ch8_sig]]
        extractor = last
    [[wh51_ch9_batt]]
        extractor = last
    [[wh51_ch9_sig]]
        extractor = last
    [[wh51_ch10_batt]]
        extractor = last
    [[wh51_ch10_sig]]
        extractor = last
    [[wh51_ch11_batt]]
        extractor = last
    [[wh51_ch11_sig]]
        extractor = last
    [[wh51_ch12_batt]]
        extractor = last
    [[wh51_ch12_sig]]
        extractor = last
    [[wh51_ch13_batt]]
        extractor = last
    [[wh51_ch13_sig]]
        extractor = last
    [[wh51_ch14_batt]]
        extractor = last
    [[wh51_ch14_sig]]
        extractor = last
    [[wh51_ch15_batt]]
        extractor = last
    [[wh51_ch15_sig]]
        extractor = last
    [[wh51_ch16_batt]]
        extractor = last
    [[wh51_ch16_sig]]
        extractor = last
    [[wn32_batt]]
        extractor = last
    [[wn32_sig]]
        extractor = last
    [[wn32p_batt]]
        extractor = last
    [[wn32p_sig]]
        extractor = last
    # End weewx-ecowittGateway extractors
"""

# construct our config dict
ecowitt_dict = configobj.ConfigObj(io.StringIO(ecowitt_config))


# ---------------------------------------------------------------------------
# answers from ECOWITT_* environment variables
# ---------------------------------------------------------------------------

# Answers that can be given in ECOWITT_<NAME> environment variables instead of
# at the prompts. The kind is 'text', 'required' (text that can't be empty),
# 'int', 'bool' or a tuple of choices.
ENV_PREFIX = 'ECOWITT_'
UNITS = ('native', 'us', 'metric', 'metricwx')
SETTINGS = {
    'NO_PROMPT': 'bool',
    'IP_ADDRESS': 'required',
    'POLL_INTERVAL': 'int',
    'MODE': ('driver', 'service', 'skip'),
    'RAIN_SOURCE': ('tipping', 'piezo', 'none', 'both'),
    'CATCHUP': ('either', 'device', 'net', 'none'),
    'API_KEY': 'text',
    'APP_KEY': 'text',
    'SENSOR_MAP': 'bool',
    'SHOW_ALL_BATT': 'bool',
    'RADIATION': 'bool',
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

# rain gauge and multi-channel sensor models, for probing the device
TIPPING_MODELS = ('wh40', 'wh69', 'wn20')
PIEZO_MODELS = ('ws85', 'ws90', 'wh85', 'wh90')
CHANNEL_MODELS = {'wn31': 'wn31', 'wh31': 'wn31', 'wn34': 'wn34', 'wh34': 'wn34',
                  'wn35': 'wn35', 'wh35': 'wn35', 'wh41': 'wh41', 'wh43': 'wh41',
                  'wh51': 'wh51', 'wh54': 'wh54', 'wh55': 'wh55'}
# TCP API (GW1000) sensor addresses: (model, first address, channels, first channel)
TCP_CHANNEL_ADDRESSES = (('wn31', 6, 8, 1), ('wh51', 14, 8, 1), ('wh41', 22, 4, 1),
                         ('wh55', 27, 4, 1), ('wn34', 31, 8, 1), ('wn35', 40, 8, 1),
                         ('wh51', 58, 8, 9))


class SettingError(ValueError):
    """An ECOWITT_* environment variable has a value the installer can't use."""


def _env(name):
    """The stripped value of ECOWITT_<name>, or None if it isn't set."""
    value = os.environ.get(ENV_PREFIX + name)
    return None if value is None else value.strip()


def _env_bool(name):
    value = _env(name)
    return None if value is None else value.lower() in YES


def check_env():
    """Check every ECOWITT_* variable before anything is changed.

    Raises SettingError for a value that can't be used, returns warnings for
    unknown names."""
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


def _sensor_id(value):
    """A sensor ID in comparable form: upper case hex, no '0x' or leading zeros."""
    text = str(value).strip().upper()
    text = text[2:] if text.startswith('0X') else text
    return text.lstrip('0') or '0'


def _as_list(value):
    if isinstance(value, str):
        return [v.strip() for v in value.split(',') if v.strip()]
    return list(value or [])


def _place_after_station(config_dict):
    """Move the driver section so it directly follows [Station]."""
    sections = config_dict.sections
    if SECTION in sections and 'Station' in sections:
        sections.remove(SECTION)
        sections.insert(sections.index('Station') + 1, SECTION)


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


def _current_rain_source(config_dict):
    """'tipping', 'piezo' or 'none': the gauge WeeWX 'rain' is derived from."""
    rain = config_dict.get('StdWXCalculate', {}).get('Delta', {}).get('rain', {})
    source = str(rain.get('input', '')) if hasattr(rain, 'get') else ''
    return 'tipping' if source.startswith('t_') else 'piezo' if source.startswith('p_') else 'none'


def version_compare(v1, v2):
    """Basic 'distutils' and 'packaging' free version comparison.

    v1 and v2 are WeeWX version numbers in string format.

    Returns:
        0 if v1 and v2 are the same
        -1 if v1 is less than v2
        +1 if v1 is greater than v2
    """

    import itertools
    import re

    def number(part):
        # compare the leading digits as numbers ('10' > '9'); 5.4.0b1 counts as 5.4.0
        match = re.match(r'\d+', part)
        return int(match.group()) if match else 0

    mash = itertools.zip_longest(v1.split('.'), v2.split('.'), fillvalue='0')
    for x1, x2 in mash:
        if number(x1) > number(x2):
            return 1
        if number(x1) < number(x2):
            return -1
    return 0


def loader():
    return EcowittHttpInstaller()


def move_legacy_section(config_dict):
    """Move ecowitt_http.py's [EcowittHttp] section to [EcowittGateway] (unless there is one already),
    and point station_type and the service lists at this driver. Returns a list of what was changed."""
    changes = []
    if LEGACY_SECTION in config_dict:
        if SECTION not in config_dict:
            config_dict[SECTION] = config_dict[LEGACY_SECTION].dict()
            config_dict.comments[SECTION] = config_dict.comments.get(LEGACY_SECTION, [])
            changes.append(f'moved [{LEGACY_SECTION}] to [{SECTION}]')
        else:
            changes.append(f'removed [{LEGACY_SECTION}]; [{SECTION}] is already there')
        del config_dict[LEGACY_SECTION]
        config_dict[SECTION]['driver'] = MODULE
    if SECTION in config_dict and config_dict[SECTION].get('driver') == OLD_MODULE:
        config_dict[SECTION]['driver'] = MODULE
        changes.append(f'[{SECTION}] driver is now {MODULE}')
    station = config_dict.get('Station', {})
    if station.get('station_type') == LEGACY_SECTION:
        station['station_type'] = SECTION
        changes.append(f'station_type is now {SECTION}')
    services = config_dict.get('Engine', {}).get('Services', {})
    for group, value in services.items():
        items = value if isinstance(value, list) else [v.strip() for v in str(value).split(',')]
        renamed = list(items)
        for old in (LEGACY_MODULE, OLD_MODULE):
            renamed = [v.replace(f'{old}.', f'{MODULE}.') if v.startswith(f'{old}.') else v for v in renamed]
        # WeeWX may have added the new name already, before configure()
        renamed = list(dict.fromkeys(renamed))
        if renamed != items:
            services[group] = renamed
            changes.append(f'{group}: the services are now in {MODULE}')
    return changes


class EcowittHttpInstaller(ExtensionInstaller):
    def __init__(self):
        if version_compare(weewx.__version__, REQUIRED_WEEWX_VERSION) < 0:
            msg = "%s requires WeeWX %s or greater, found %s" % (''.join(('Ecowitt local HTTP API driver ', DRIVER_VERSION)),
                                                                 REQUIRED_WEEWX_VERSION,
                                                                 weewx.__version__)
            raise weewx.UnsupportedFeature(msg)
        # check ECOWITT_* values now, before WeeWX copies any files
        check_env()
        super().__init__(
            version=DRIVER_VERSION,
            name='weewx-ecowittGateway',
            description='WeeWX driver for Ecowitt gateways and consoles (local HTTP API, '
                        'or TCP API for the GW1000).',
            author="Gary Roderick, Ian Millard",
            author_email="",
            files=[('bin/user', ['bin/user/weewx-ecowittGateway.py'])],
            config=ecowitt_dict,
            # declared so 'weectl extension uninstall' removes it, configure()
            # keeps it only in service mode
            data_services=SERVICE,
            # writes ecwLoop.json and publishes to MQTT when enabled, does
            # nothing otherwise
            restful_services=EXPORT_SERVICE
        )
        self._engine = None

    # -- output and questions ----------------------------------------------

    def _out(self, msg=''):
        printer = getattr(self._engine, 'printer', None)
        if printer is not None:
            printer.out(msg)
        else:
            print(msg)

    @staticmethod
    def _interactive():
        return sys.stdin is not None and sys.stdin.isatty() and not _env_bool('NO_PROMPT')

    def _from_env(self, prompt, name, options=None):
        """The answer to a question from ECOWITT_<name>, or None if not set."""
        value = _env(name) if name else None
        if value is None:
            return None
        if options:
            value = value.lower()
            if value not in options:
                raise SettingError(f"{ENV_PREFIX}{name}='{value}' can't be used here: use one of "
                                   f"{', '.join(options)}")
        shown = '********' if name in SECRETS and value else value
        self._out(f'{prompt.rstrip("?")}: {shown}  (from {ENV_PREFIX}{name})')
        return value

    def _ask(self, prompt, default, options=None, env=None):
        """Ask a question. The answer comes from ECOWITT_<env> if set, and is the
        default when there is no terminal."""
        value = self._from_env(prompt, env, options)
        if value is not None:
            return value
        if not self._interactive():
            # show what was decided, for scripted installs
            shown = '********' if env in SECRETS and default else default
            self._out(f'{prompt.strip().rstrip("?")}: {shown}  (default)')
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
            self._out(f"{prompt.rstrip('?')}: {'yes' if answer else 'no'}  (from {ENV_PREFIX}{env})")
            return answer
        return self._ask(prompt, 'y' if default else 'n', ['y', 'n']) == 'y'

    def _ask_secret(self, prompt, current, env=None):
        """Ask for a password without echoing it; Enter keeps the current value."""
        value = self._from_env(prompt, env)
        if value is not None:
            return value
        if not self._interactive():
            return current
        hint = ' (Enter keeps the current one)' if current else ' (Enter for none)'
        return getpass.getpass(f'{prompt}{hint}: ') or current

    # -- probing the device -------------------------------------------------

    @staticmethod
    def _get_json(ip, command, **params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        with urllib.request.urlopen(f'http://{ip}/{command}?{query}', timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))

    @staticmethod
    def _tcp(ip, cmd, port=45000):
        """Data from a binary TCP API command (GW1000), or None."""
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
                    if len(resp) >= 3 + size_len and \
                            len(resp) >= 2 + int.from_bytes(resp[3:3 + size_len], 'big'):
                        break
        except OSError:
            return None
        if len(resp) < 5 or resp[:2] != b'\xff\xff' or resp[2] != cmd or \
                sum(resp[2:-1]) & 0xFF != resp[-1]:
            return None
        return resp[(5 if cmd == 0x3C else 4):-1]

    def _probe_tcp(self, ip):
        """_probe() for a GW1000 over the TCP API."""
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
        """(model, set of paired gauge types, [(sensor model, channel, ID)]), or
        Nones if the device could not be contacted."""
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

    # -- configuration ------------------------------------------------------

    def configure(self, engine):
        """Replace ecowitt_http.py if installed, then ask for (or read from
        ECOWITT_* variables) the gateway settings and apply them.

        WeeWX adds this installer's config (anything still missing from
        [EcowittGateway], [StdWXCalculate] and [Accumulator]) after this, so
        moved and answered settings are kept. It is merged here first so the
        rain configuration can work on the complete config."""
        self._engine = engine
        config_dict = engine.config_dict
        for warning in check_env():   # raises SettingError before anything is changed
            self._out(warning)
        changes = move_legacy_section(config_dict)
        changes += self.remove_legacy_extension(engine)
        for change in changes:
            self._out(f'weewx-ecowittGateway: {change}')
        existing = config_dict.get(SECTION, {})
        out = self._out

        out()
        out('Configuring weewx-ecowittGateway')
        if self._interactive():
            out('Press Enter to accept the value shown in [brackets].')
        out()

        # device address, checked by contacting the device
        ip = existing.get('ip_address', 'www.xxx.yyy.zzz')
        ip_from_env = _env('IP_ADDRESS') is not None
        unset = ip in ('www.xxx.yyy.zzz', 'replace_me', '')
        model = gauges = channels = None
        while True:
            ip = self._ask('Gateway IP address, eg 192.168.1.100', ip, env='IP_ADDRESS')
            unset = ip in ('www.xxx.yyy.zzz', 'replace_me', '')
            if unset or not (self._interactive() or ip_from_env):
                break
            model, gauges, channels = self._probe(ip)
            if model:
                out(f'    Found {model} at {ip}')
                break
            out(f'    No response from a device at {ip}.')
            if ip_from_env or not self._interactive():
                out('    Using this address anyway; paired sensors and rain gauges could not be checked.')
                break
            if self._ask_yes('    Use this address anyway?', default=False):
                break
        poll = self._ask('Poll interval in seconds', existing.get('poll_interval', 20),
                         env='POLL_INTERVAL')

        # driver, service or neither
        station_type = config_dict.get('Station', {}).get('station_type')
        services = config_dict.get('Engine', {}).get('Services', {})
        if station_type == SECTION:
            default_mode = 'driver'
        elif SERVICE in _as_list(services.get('data_services', [])) and not unset and \
                'ip_address' in existing:
            default_mode = 'service'
        else:
            default_mode = 'driver' if (self._interactive() or ip_from_env) else 'skip'
        mode = self._ask('Use as the station driver, as a service alongside another driver, or skip',
                         default_mode, ['driver', 'service', 'skip'], env='MODE')
        if mode != 'skip' and unset:
            out('    No gateway IP address given, so the station driver and services are not being changed.')
            mode = 'skip'

        # which gauge WeeWX 'rain' and 'rainRate' come from
        rain_source = None
        if mode == 'driver':
            rain_source = self._ask_rain_source(config_dict, existing, gauges)

        # catchup on startup
        catchup = existing.get('catchup', {})
        source = str(catchup.get('source', 'either')).lower()
        if model and model.endswith('(TCP API)'):
            out('    This device has no SD card or local HTTP API, so missed data can only come')
            out('    from Ecowitt.net (net), and only if it uploads there.')
            source = self._ask('Fetch missed data at startup from', 'net' if source == 'device' else source,
                               ['net', 'none', 'either'], env='CATCHUP')
        else:
            source = self._ask('Fetch missed data at startup from', source,
                               ['either', 'device', 'net', 'none'], env='CATCHUP')
        api_key = catchup.get('api_key', existing.get('api_key', ''))
        app_key = catchup.get('app_key', existing.get('app_key', ''))
        if source in ('either', 'net'):
            if self._interactive():
                out('    Ecowitt.net keys are only needed to fetch missed data from Ecowitt.net')
                out('    (press Enter to leave them blank).')
            api_key = self._ask('    Ecowitt.net API key', api_key or '', env='API_KEY')
            app_key = self._ask('    Ecowitt.net application key', app_key or '', env='APP_KEY')

        sensor_map = self._ask_sensor_map(existing.get('sensor_map', {}), channels)
        show_batt = self._ask_yes('Show battery state for sensors with no signal?',
                                  str(existing.get('show_all_batt', 'False')).lower() == 'true',
                                  env='SHOW_ALL_BATT')
        # the earlier weewx-EcowittGateway extension recorded radiation; its
        # settings have rain_source
        replacing_earlier = 'rain_source' in existing
        radiation = self._ask_yes("Record WeeWX 'radiation' (W/m2), worked out from the light sensor "
                                  "(illuminance, lux)?",
                                  str(existing.get('radiation_from_illuminance', replacing_earlier)).lower() == 'true',
                                  env='RADIATION')
        loop_on_init = None
        if mode == 'driver':
            loop_on_init = self._ask_yes('Keep retrying at startup if the gateway cannot be reached '
                                         '(loop_on_init)?',
                                         str(config_dict.get('loop_on_init', '1')) != '0',
                                         env='LOOP_ON_INIT')
        loop_json = self._ask_loop_json(config_dict, existing.get('loop_json', {}))
        mqtt = self._ask_mqtt(existing.get('mqtt', {}))

        if getattr(engine, 'dry_run', False):
            out('Dry run: weewx.conf not changed.')
            return bool(changes)

        # the complete default config first, then the answers
        _merge_missing(config_dict, ecowitt_dict)
        if not config_dict.comments.get(SECTION):
            config_dict.comments[SECTION] = ['', '#' * 78]
        _place_after_station(config_dict)
        section = config_dict[SECTION]
        section['driver'] = MODULE
        section.update({'ip_address': ip, 'poll_interval': str(poll), 'show_all_batt': str(show_batt),
                        'radiation_from_illuminance': str(radiation)})
        # earlier settings at the top level; the keys now live in [[catchup]]
        for key in ('rain_source', 'api_key', 'app_key'):
            section.pop(key, None)
        catchup = section.setdefault('catchup', {})
        catchup.update({'source': source, 'api_key': api_key, 'app_key': app_key})
        for sid, (channel, note) in sensor_map.items():
            if sid not in section['sensor_map']:
                section['sensor_map'][sid] = channel
                if note:
                    section['sensor_map'].inline_comments[sid] = f'# {note}'
        section['loop_json'].update(loop_json)
        section['mqtt'].update(mqtt)

        if mode != 'service':
            self._remove_service(config_dict)
        if mode == 'driver':
            config_dict.setdefault('Station', {})['station_type'] = SECTION
            config_dict['loop_on_init'] = '1' if loop_on_init else '0'
            config_dict.setdefault('StdArchive', {})['record_generation'] = 'software'
            self._apply_rain_source(engine, config_dict, rain_source, gauges)
            self._tidy_accumulator(engine, config_dict)
            out()
            out(f'Station driver set to {SECTION} ({MODULE}); archive records generated in software.')
        elif mode == 'service':
            out()
            out(f'{SERVICE} is in data_services.')
        else:
            out()
            out('Gateway settings saved; the station driver and services were not changed.')
        out('Restart WeeWX to start using the new settings.')
        return True

    def _ask_rain_source(self, config_dict, existing, gauges):
        """Which gauge WeeWX 'rain' and 'rainRate' come from: tipping, piezo or none."""
        current = _current_rain_source(config_dict)
        legacy = str(existing.get('rain_source', '')).lower()     # earlier extension's option
        if current == 'none' and legacy in ('tipping', 'both', 'piezo'):
            current = 'piezo' if legacy == 'piezo' else 'tipping'
        if gauges:
            self._out(f"    Paired rain gauges: {' and '.join(sorted(gauges))}")
            possible = [g for g in ('tipping', 'piezo') if g in gauges] + ['none']
        else:
            possible = ['tipping', 'piezo', 'none']
        # 'none' is only a choice to keep if this driver already supplies the
        # station's data; on a new install there is just no rain config yet
        ours = config_dict.get('Station', {}).get('station_type') == SECTION
        if current in possible and (current != 'none' or ours and (gauges is None or not gauges)):
            default = current
        else:
            default = 'tipping' if 'tipping' in possible else possible[0]
        env_value = _env('RAIN_SOURCE')
        if env_value is not None and env_value.lower() == 'both':
            # 'both' (earlier extension): 'rain' from the tipping gauge, the piezo gauge in p_rain
            os.environ[ENV_PREFIX + 'RAIN_SOURCE'] = 'tipping'
        if self._interactive() and env_value is None:
            self._out("    tipping = WeeWX 'rain'/'rainRate' from the tipping gauge")
            self._out("    piezo   = WeeWX 'rain'/'rainRate' from the piezo gauge")
            self._out("    none    = leave 'rain'/'rainRate' empty")
            self._out("    Each paired gauge is also recorded in 't_rain'/'t_rainrate' or "
                      "'p_rain'/'p_rainrate', unless it is the one used for 'rain'.")
        return self._ask("Source of WeeWX 'rain'", default, possible, env='RAIN_SOURCE')

    @staticmethod
    def _load_driver(engine):
        """The driver module just installed in the user directory."""
        roots = getattr(engine, 'root_dict', None) or {}
        path = os.path.join(roots.get('USER_DIR', ''), os.path.basename(MODULE.replace('.', '/')) + '.py')
        spec = importlib.util.spec_from_file_location('_weewx_ecowittgateway_driver', path)
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        return driver

    def _apply_rain_source(self, engine, config_dict, rain_source, gauges):
        """Configure [StdWXCalculate] for the rain source using the driver's own
        ConfEditor, as 'weectl station reconfigure' does."""
        try:
            driver = self._load_driver(engine)
        except Exception as e:
            self._out(f"    Could not load the driver to configure the rain source ({e}); "
                      f"run 'weectl station reconfigure' to set it.")
            return
        editor = driver.EcowittHttpDriverConfEditor
        if gauges is None:
            # the device could not be checked, allow either gauge
            paired = 'both'
        else:
            paired = 'both' if gauges == {'tipping', 'piezo'} else next(iter(gauges), 'none')
        editor.preset = {'rain_source': rain_source, 'paired_gauges': paired}
        try:
            editor.do_rain(config_dict)
        finally:
            editor.preset = {}
        # WeeWX merges this installer's config into weewx.conf after
        # configure(), which would put back the t_rain/p_rain calculation that
        # 'rain' now replaces, so drop what do_rain() removed from it
        calc = config_dict.get('StdWXCalculate', {})
        ours = self['config'].get('StdWXCalculate', {})
        for group in ('Calculations', 'Delta'):
            for field in ('t_rain', 'p_rain'):
                if field not in calc.get(group, {}):
                    ours.get(group, {}).pop(field, None)

    def _tidy_accumulator(self, engine, config_dict):
        """Remove the [Accumulator] entries earlier extensions added for
        fields this driver doesn't produce (see the driver's
        tidy_accumulator()). Only for the station driver: as a service, the
        station's own driver may produce those fields."""
        try:
            removed, changed = self._load_driver(engine).tidy_accumulator(config_dict)
        except Exception as e:
            self._out(f"    Could not load the driver to check [Accumulator] ({e}); "
                      f"'weectl station reconfigure' does it too.")
            return
        if removed:
            self._out(f'Removed {len(removed)} unused [Accumulator] entries left by earlier versions.')
        for field in changed:
            self._out(f"[Accumulator] {field}: extractor is now "
                      f"'{config_dict['Accumulator'][field]['extractor']}'.")

    def _ask_sensor_map(self, current, channels):
        """Offer to fix multi-channel sensors to their channels; returns {ID: (channel, comment)}."""
        result = {sid: (str(ch), '') for sid, ch in current.items()}
        if not channels:
            return result
        known = {_sensor_id(sid) for sid in current}
        new = [c for c in channels if _sensor_id(c[2]) not in known]
        self._out('    Multi-channel sensors found:')
        for model, channel, sid in channels:
            state = '' if _sensor_id(sid) not in known else '  (already in the sensor map)'
            self._out(f'        {model.upper():<5} CH{channel:<3} ID {sid}{state}')
        if not new:
            return result
        self._out('    Fixing a sensor to its channel keeps its data in the same WeeWX fields')
        self._out('    if it is re-paired onto a different channel later.')
        what = 'these sensors' if not current else f'the {len(new)} new sensor(s)'
        if self._ask_yes(f'Fix {what} to their current channels (sensor_map)?', True, env='SENSOR_MAP'):
            for model, channel, sid in new:
                result[sid] = (str(channel), model.upper())
        return result

    def _ask_mqtt(self, current):
        """Ask whether and how to publish loop data to an MQTT broker; returns [[mqtt]] settings."""
        out = self._out

        def cur(key, default=''):
            return str(current.get(key, default))

        enable = self._ask_yes('Publish each loop packet to an MQTT broker?',
                               cur('enable', 'False').lower() == 'true', env='MQTT')
        if not enable:
            return {'enable': 'False'}
        if importlib.util.find_spec('paho') is None:
            out('    Note: the paho-mqtt package is needed for MQTT. Install it with')
            out('          sudo apt install python3-paho-mqtt      (Debian package install)')
            out('          pip install paho-mqtt                   (pip install, in the WeeWX environment)')
        host = self._ask('    Broker host name or IP address', cur('host', 'localhost'), env='MQTT_HOST')
        tls = self._ask_yes('    Use an encrypted (TLS) connection?', cur('tls', 'False').lower() == 'true',
                            env='MQTT_TLS')
        default_port = cur('port', '8883' if tls else '1883')
        if tls and default_port == '1883':
            default_port = '8883'
        port = self._ask('    Broker port', default_port, env='MQTT_PORT')
        if self._interactive():
            try:
                with socket.create_connection((host, int(port)), timeout=3):
                    out(f'    Broker {host}:{port} is reachable')
            except (OSError, ValueError) as e:
                out(f'    Could not reach {host}:{port} ({e}); WeeWX will keep retrying once it starts.')
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
        settings = {'enable': 'True', 'host': host, 'port': port, 'username': username,
                    'password': password, 'topic': topic, 'format': fmt, 'units': units,
                    'retain': str(retain), 'tls': str(tls)}
        if tls:
            settings['ca_certs'] = self._ask('    CA certificate file (Enter for the system certificates)',
                                             cur('ca_certs'), env='MQTT_CA_CERTS')
        return settings

    def _ask_loop_json(self, config_dict, current):
        """Ask whether and where to write ecwLoop.json; returns [[loop_json]] settings."""
        out = self._out
        name = 'ecwLoop.json'
        weewx_root = config_dict.get('WEEWX_ROOT', '')
        html_root = config_dict.get('StdReport', {}).get('HTML_ROOT', 'public_html')
        web_dir = os.path.abspath(os.path.join(weewx_root, html_root))
        locations = {'web': name, 'data': os.path.join(weewx_root, name),
                     'tmp': os.path.join('/tmp', name)}
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
            location = self._ask(f'Where should {name} be written', location,
                                 ['web', 'data', 'tmp', 'custom'], env='LOOP_JSON_LOCATION')
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

    @staticmethod
    def remove_legacy_extension(engine):
        """Remove the extensions this replaces (ecowitt_http.py, and one
        installed as weewx-EcowittGateway): their files and extension records,
        but not their config.

        'weectl extension uninstall' would also delete the config sections and
        the [StdWXCalculate] and [Accumulator] entries they added, which this
        driver uses too. On a case-insensitive file system (macOS, Windows)
        weewx-EcowittGateway.py and weewx-ecowittGateway.py are the same file,
        as are the two extension records, so only what isn't ours is removed."""
        roots = getattr(engine, 'root_dict', None) or {}
        ext_dir, user_dir = roots.get('EXT_DIR'), roots.get('USER_DIR')
        if not ext_dir:
            return []

        def ours(path, own):
            return os.path.exists(own) and os.path.samefile(path, own)

        own_file = os.path.join(user_dir or '', os.path.basename(MODULE.replace('.', '/')) + '.py')
        changes = []
        for name, filename, description in REPLACED_EXTENSIONS:
            record = os.path.join(ext_dir, name)
            if not os.path.isdir(record) or ours(record, os.path.join(ext_dir, 'weewx-ecowittGateway')):
                continue
            if not getattr(engine, 'dry_run', False):
                shutil.rmtree(record)
                if user_dir:
                    for path in [os.path.join(user_dir, filename)] + glob.glob(
                            os.path.join(user_dir, '__pycache__', glob.escape(filename[:-3]) + '.*.pyc')):
                        if os.path.isfile(path) and not ours(path, own_file):
                            os.remove(path)
            changes.append(f'removed {description}, which this replaces; its settings are kept')
        return changes
