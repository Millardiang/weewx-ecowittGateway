#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""weewx-EcowittGateway.py

WeeWX driver and service for Ecowitt gateways/consoles using the Ecowitt local
HTTP API.

Copyright (C) 2026 Ian Millard

Derived from ecowitt_http.py, which carries the following notices:
    Copyright (C) 2024-25 Gary Roderick                 gjroderick<at>gmail.com

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program.  If not, see https://www.gnu.org/licenses/.

Version: 0.0.1 beta 7

Requires WeeWX 5.4.0 or later. Install in the WeeWX user directory and
reference it from weewx.conf:
    [Station]
        station_type = EcowittGateway
    [EcowittGateway]
        driver = user.weewx-EcowittGateway

Supported sensors: WN20, WN31, WN32(P), WN34, WN35, WN38, WH40, WH41/43,
WH45/46, WH51, WH52, WH54, WH55, WH57, WH68, WH69, WS80, WS85, WS90, WN64 and
WQT01. Legacy model names in API responses are normalised (WH31 -> WN31,
WH80 -> WS80 etc); API command names keep the legacy names.
"""

import calendar
import collections
import csv
import datetime
import io
import json
import logging
import math
import os
import queue
import re
import socket
import struct
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import MutableMapping
from operator import itemgetter

import configobj
import weecfg
import weeutil.logger
import weeutil.weeutil
import weewx.defaults
import weewx.drivers
import weewx.engine
import weewx.units
import weewx.wxformulas

log = logging.getLogger(__name__)


def timestamp_to_string(ts):
    """Format a unix timestamp as 'dd Month yyyy hh:mm:ss TZ (ts)'."""
    if ts is None:
        return '******* N/A *******'
    return f"{time.strftime('%d %B %Y %H:%M:%S %Z', time.localtime(ts))} ({int(ts)})"


DRIVER_NAME = 'EcowittGateway'
LEGACY_SECTIONS = ('EcowittHttp',)  # section names used by earlier versions and ecowitt_http.py
DRIVER_VERSION = '0.0.1b7'
DRIVER_MODULE = 'weewx-EcowittGateway'
MIN_WEEWX_VERSION = (5, 4, 0)


def _version_tuple(version):
    """'5.4.0', '5.5.2b1' -> (5, 4, 0), (5, 5, 2)"""
    return tuple(int(re.match(r'\d+', part).group()) if re.match(r'\d+', part) else 0
                 for part in version.split('.')[:3])


if _version_tuple(weewx.__version__) < MIN_WEEWX_VERSION:
    raise weewx.UnsupportedFeature(f"WeeWX {'.'.join(map(str, MIN_WEEWX_VERSION))} or later is required, "
                                   f"found {weewx.__version__}")


def html_root(config_dict):
    """Absolute path of the WeeWX web pages folder (HTML_ROOT), or None if unknown."""
    try:
        root = config_dict['WEEWX_ROOT']
    except (KeyError, TypeError):
        return None
    return os.path.abspath(os.path.join(root, config_dict.get('StdReport', {}).get('HTML_ROOT', 'public_html')))


def driver_config(config_dict):
    """The driver's section of weewx.conf, falling back to legacy section names."""
    for name in (DRIVER_NAME, *LEGACY_SECTIONS):
        if name in config_dict:
            return config_dict[name]
    return {}


SUPPORTED_DEVICES = ('GW1100', 'GW1200', 'GW2000', 'GW3000', 'WN1700', 'WN1820', 'WN1821',
                     'WN1920', 'WN1980', 'WS6210', 'WS3800', 'WS3820', 'WS3900', 'WS3910')
UNSUPPORTED_DEVICES = ('GW1000',)
KNOWN_DEVICES = SUPPORTED_DEVICES + UNSUPPORTED_DEVICES

DEFAULT_MAX_TRIES = 3
DEFAULT_RETRY_WAIT = 2
DEFAULT_URL_TIMEOUT = 10
DEFAULT_CATCHUP_GRACE = 0
DEFAULT_CATCHUP_RETRIES = 3
DEFAULT_MAX_AGE = 60
DEFAULT_POLL_INTERVAL = 20
DEFAULT_DISCOVERY_PORT = 59387
DEFAULT_DISCOVERY_PERIOD = 5
DEFAULT_DISCOVERY_TIMEOUT = 5
DEFAULT_LOST_CONTACT_LOG_PERIOD = 21600
DEFAULT_UNIT_SYSTEM = weewx.METRICWX
DEFAULT_FILTER_BATTERY = False
DEFAULT_FW_CHECK_INTERVAL = 86400
DEFAULT_ONLY_REGISTERED_SENSORS = False
DEFAULT_GET_SOILAD = True

BOLD, ENDC = '\x1b[1m', '\x1b[0m'


def _rng(fmt, n, start=1):
    """Return [fmt.format(start) .. fmt.format(n)]."""
    return [fmt.format(i) for i in range(start, n + 1)]


def _chmap(pairs, n, channel_major=False):
    """Build {key_fmt.format(i): value_fmt.format(i)} for channels 1..n."""
    if channel_major:
        return {k.format(i): v.format(i) for i in range(1, n + 1) for k, v in pairs}
    return {k.format(i): v.format(i) for k, v in pairs for i in range(1, n + 1)}


# ---------------------------------------------------------------------------
# WeeWX unit configuration
# ---------------------------------------------------------------------------

_U = weewx.units
for _std in (_U.USUnits, _U.MetricUnits, _U.MetricWXUnits):
    _std['group_dbm'] = 'dBm'
    _std['group_string'] = 'string'
_U.default_unit_format_dict.update({'dBm': '%.0f', 'hPa': '%.2f', 'string': '%s'})
_U.default_unit_label_dict.update({'dBm': ' dBm', 'string': ''})

_OBS_GROUPS = {
    'group_percent': _rng('leafWet{}', 8) + _rng('soilMoist{}', 16) + _rng('signal{}', 8) + ['consolebattp'],
    'group_count': _rng('soilad{}', 16) + _rng('soilECad{}', 16) + _rng('ldsheat_ch{}', 4) + [
        'charge_stat', 'ws6210_batt', 'wh68_batt', 'wh69_batt', 'wqt01_batt', 'wn64_batt', 'srain_piezo',
        'srain', 'ws90_ver', 'ws85_ver', 'rain_annual_reset', 'rain_day_reset', 'rain_week_reset',
        'rain_source', 'piezo', 'rain_batt', 'piezorain_batt', 'lightning_strike_count',
        'lightning_noise_count'],
    'group_temperature': _rng('soilmTemp{}', 16),
    'group_usiecm': _rng('soilEC{}', 16),
    'group_concentration': ['pm1_24h_co2', 'pm4_24h_co2', 'pm25_24h_co2', 'pm10_24h_co2',
                            *_rng('pm25_avg_24h_ch{}', 4)],
    'group_rainrate': ['rainrate', 'rrain_piezo', 'p_rainrate'],
    'group_rain': ['eventRain', 'hourRain', 'rain24', 'weekRain', 'totalRain', 'erain_piezo', 'hrain_piezo',
                   'drain_piezo', 'wrain_piezo', 'mrain_piezo', 'yrain_piezo', 'rain_piezo', 'rain24_piezo',
                   'train_piezo', 't_rain', 'p_rain', 't_rainyear', 'p_rainyear'],
    'group_volt': ['rainBatteryStatus', 'hailBatteryStatus', 'windBatteryStatus', 'ws80_batt', 'ws85_batt',
                   'ws90_batt', 'wqt01batt', 'wn64batt', 'ws1900batt', 'console_batt', 'consoleext_batt',
                   'ws90cap_volt', 'ws85cap_volt'] + _rng('ldsbatt{}', 4),
    'group_speed2': ['maxdailygust'],
    'group_distance': ['lightning_dist', 'lightning_distance'],
    'group_time': ['lightning_disturber_count'],
    'group_deltatime': ['runtime'],
    'group_data': ['heap', 'pb'],
    'group_direction': ['windDir10'],
    'group_string': ['apName', 'stationtype'],
}
for _grp, _fields in _OBS_GROUPS.items():
    for _f in _fields:
        _U.obs_group_dict[_f] = _grp


def _set_conv(src, targets, overwrite=False):
    """Add unit conversion functions, optionally keeping existing ones."""
    conv = _U.conversionDict.setdefault(src, {})
    for dest, fn in targets.items():
        if overwrite or dest not in conv:
            conv[dest] = fn


def define_units():
    """Define the additional unit groups, units, formats and conversions used."""
    groups = {'group_depth': ('foot2', 'mm2'), 'group_pressurevpd': ('inHg', 'hPa'),
              'group_usiecm': ('micro_siemens_per_centimeter',) * 2,
              'group_organicpollution': ('pounds_per_gallon', 'milligram_per_liter'),
              'group_ntu': ('nephelometric_turbidity_unit',) * 2,
              'group_deltat': ('degree_F2', 'degree_C2')}
    for group, (us, metric) in groups.items():
        _U.USUnits[group] = us
        _U.MetricUnits[group] = _U.MetricWXUnits[group] = metric
    _U.obs_group_dict['vpd'] = 'group_pressurevpd'
    _U.obs_group_dict['micro_siemens_per_centimeter'] = 'group_usiecm'
    _U.obs_group_dict['nephelometric_turbidity_unit'] = 'group_ntu'
    for unit, fmt, label in (('micro_siemens_per_centimeter', '%.0f', ' µS/cm'), ('foot2', '%.2f', ' ft'),
                             ('mm2', '%.0f', ' mm'), ('milligram_per_liter', '%.1f', ' mg/L'),
                             ('pounds_per_gallon', '%.9f', ' lb/gal'), ('nephelometric_turbidity_unit', '%.0f', 'NTU'),
                             ('degree_C2', '%.1f', '°C'), ('degree_E2', '%.1f', '°C'), ('degree_F2', '%.1f', '°F'),
                             ('degree_K2', '%.1f', '°C')):
        _U.default_unit_format_dict[unit] = fmt
        _U.default_unit_label_dict[unit] = label
    _U.default_unit_format_dict['microgram_per_meter_cubed'] = '%.1f'
    for unit, fmt, label in (('byte', '%.d', ' B'), ('kilobyte', '%.3f', ' kB'), ('megabyte', '%.3f', ' MB')):
        _U.default_unit_format_dict[unit] = _U.default_unit_format_dict.get(unit) or fmt
        _U.default_unit_label_dict[unit] = _U.default_unit_label_dict.get(unit) or label
    # conversions for units only this driver defines
    for src, targets in {
            'milligram_per_liter': {'pounds_per_gallon': lambda x: x * 8.345e-06},
            'pounds_per_gallon': {'milligram_per_liter': lambda x: x / 8.345e-06},
            'mm2': {'inch2': lambda x: x / 25.4, 'foot2': lambda x: x / 304.8, 'cm2': lambda x: x / 10.0,
                    'meter2': lambda x: x / 1000.0},
            'cm2': {'inch2': lambda x: x / 2.54, 'foot2': lambda x: x / 30.48, 'mm2': lambda x: x * 10.0,
                    'meter2': lambda x: x / 100.0},
            'm2': {'inch2': lambda x: x / 0.0254, 'foot2': lambda x: x / 0.3048, 'mm2': lambda x: x * 1000.0,
                   'cm2': lambda x: x * 100.0},
            'inch2': {'foot2': lambda x: x / 12.0, 'mm2': lambda x: x * 25.4, 'cm2': lambda x: x * 2.54,
                      'meter2': lambda x: x * 0.0254},
            'foot2': {'inch2': lambda x: x * 12.0, 'mm2': lambda x: x * 304.8, 'cm2': lambda x: x * 30.48,
                      'meter2': lambda x: x * 0.3048},
            'degree_C2': {'degree_F2': lambda x: x * 9 / 5, 'degree_E2': lambda x: x * 7 / 5, 'degree_K2': lambda x: x},
            'degree_E2': {'degree_C2': lambda x: x * 5 / 7, 'degree_F2': lambda x: x * 9 / 7,
                          'degree_K2': lambda x: x * 5 / 7},
            'degree_F2': {'degree_C2': lambda x: x * 5 / 9, 'degree_E2': lambda x: x * 7 / 9,
                          'degree_K2': lambda x: x * 5 / 9},
            'degree_K2': {'degree_C2': lambda x: x, 'degree_E2': lambda x: x * 7 / 5, 'degree_F2': lambda x: x * 9 / 5},
    }.items():
        _set_conv(src, targets, overwrite=True)
    # conversions that WeeWX may already provide
    for src, targets in {
            'nautical_mile': {'meter': lambda x: x * 1852.0, 'km': lambda x: x * 1.852, 'mile': lambda x: x * 1.151},
            'meter': {'nautical_mile': lambda x: x / 1852.0},
            'km': {'nautical_mile': lambda x: x / 1.852},
            'mile': {'nautical_mile': lambda x: x / 1.151},
            'knot': {'meter_per_second': lambda x: x * 0.514444, 'km_per_hour': lambda x: x * 1.852,
                     'mile_per_hour': lambda x: x * 1.151},
            'meter_per_second': {'knot': lambda x: x / 0.514444},
            'km_per_hour': {'knot': lambda x: x / 1.852},
            'mile_per_hour': {'knot': lambda x: x / 1.151},
            'byte': {'bit': lambda x: x * 8, 'kilobyte': lambda x: x / 1024.0, 'megabyte': lambda x: x / 1024.0 ** 2},
            'kilobyte': {'bit': lambda x: x * 8192, 'byte': lambda x: x * 1024, 'megabyte': lambda x: x / 1024.0},
            'megabyte': {'bit': lambda x: x * 8 * 1024 ** 2, 'byte': lambda x: x * 1024 ** 2,
                         'kilobyte': lambda x: x * 1024},
            'klux': {'lux': lambda x: x * 1000, 'watt_per_meter_squared': lambda x: x * 1000 / 126.7,
                     'kfc': lambda x: x * 10.76 / 0.1267 ** 2},
            'lux': {'klux': lambda x: x / 1000.0, 'watt_per_meter_squared': lambda x: x / 126.7,
                    'kfc': lambda x: x * 0.01076 / 0.1267 ** 2},
            'watt_per_meter_squared': {'klux': lambda x: x * 0.1267, 'lux': lambda x: x * 126.7,
                                       'kfc': lambda x: x * 10.76 / 0.1267},
            'kfc': {'klux': lambda x: x * 0.1267 ** 2 / 10.76, 'lux': lambda x: x * 126.7 ** 2 / 10.76,
                    'watt_per_meter_squared': lambda x: x * 0.1267 / 10.76},
    }.items():
        _set_conv(src, targets)


# ---------------------------------------------------------------------------
# Static data tables
# ---------------------------------------------------------------------------

# sensor model: number of channels (0 = single sensor)
SENSOR_CHANNELS = {'wn20': 0, 'wh24': 0, 'wh25': 0, 'wh26': 0, 'wh65': 0, 'wn32': 0, 'wn32p': 0, 'wn31': 8,
                   'wn34': 8, 'wn35': 8, 'wn38': 0, 'wh40': 0, 'wh41': 4, 'wh45': 0, 'wh51': 16, 'wh54': 4,
                   'wh55': 4, 'wh57': 0, 'wh68': 0, 'wh69': 0, 'ws80': 0, 'ws85': 0, 'ws90': 0}


# multi-channel sensor model: (number of channels, live data groups it feeds)
SENSOR_GROUPS = {'wn31': (8, ('ch_aisle',)), 'wn34': (8, ('ch_temp',)), 'wn35': (8, ('ch_leaf',)),
                 'wh41': (4, ('ch_pm25',)), 'wh51': (16, ('ch_soil', 'ch_ec')), 'wh54': (4, ('ch_lds',)),
                 'wh55': (4, ('ch_leak',))}
_GROUP_MODEL = {group: model for model, (_, groups) in SENSOR_GROUPS.items() for group in groups}
# a live data field that helps identify each multi-channel sensor
_SENSOR_READING = {'ch_aisle': ('temp', 'humidity'), 'ch_temp': ('temp',), 'ch_leaf': ('humidity',),
                   'ch_pm25': ('PM25',), 'ch_soil': ('humidity',), 'ch_ec': ('temp', 'ec'),
                   'ch_lds': ('depth',), 'ch_leak': ('status',)}
_READING_UNIT = {'temp': '\u00b0', 'humidity': '%', 'PM25': ' \u00b5g/m\u00b3', 'ec': ' \u00b5S/cm', 'depth': ' mm'}
_UNREGISTERED_IDS = ('FFFFFFFE', 'FFFFFFFF')


def _sensor_names(model, channels):
    return [model] if channels == 0 else _rng(model + '.ch{}', channels)


def _build_default_groups():
    """Unit group of every (flattened) field the driver can emit."""
    T, C, V, PCT, B = 'group_temperature', 'group_count', 'group_volt', 'group_percent', 'group_boolean'
    SPD, RAD, R, CONC, FRAC = 'group_speed', 'group_radiation', 'group_rain', 'group_concentration', 'group_fraction'
    g = {'datetime': 'group_time'}
    for obs, grp in (('0x02', T), ('0x03', T), ('3', T), ('0x04', T), ('4', T), ('0x05', T),
                     ('5', 'group_pressurevpd'), ('0x07', PCT), ('0x0A', 'group_direction'), ('0x0B', SPD),
                     ('0x0C', SPD), ('0x0F', SPD), ('0x14', SPD), ('0x15', RAD), ('0x16', RAD), ('0x17', 'group_uv'),
                     ('0x19', SPD), ('0xA1', T)):
        g.update({f'common_list.{obs}.val': grp, f'common_list.{obs}.battery': C, f'common_list.{obs}.voltage': V})
    g['common_list.0xA2.val'] = T
    for arr in ('rain', 'piezoRain'):
        for obs in ('0x0D', '0x0E', '0x10', '0x11', '0x12', '0x13'):
            g.update({f'{arr}.{obs}.val': R, f'{arr}.{obs}.battery': C, f'{arr}.{obs}.voltage': V})
        g.update({f'{arr}.0x0E.val': 'group_rainrate', f'{arr}.0x0F.val': R, f'{arr}.0x7D.val': R,
                  f'{arr}.0x14.val': R})
    g.update({'rain.srain': B, 'rain.srain.val': B, 'piezoRain.srain_piezo': B, 'piezoRain.srain_piezo.val': B,
              't_rain': R, 't_rainyear': R, 'p_rain': R, 'p_rainyear': R,
              'piezoRain.0x13.ws85cap_volt': V, 'piezoRain.0x13.ws90cap_volt': V,
              'piezoRain.0x13.ws85_ver': C, 'piezoRain.0x13.ws90_ver': C,
              'wh25.intemp': T, 'wh25.inhumi': PCT, 'wh25.abs': 'group_pressure', 'wh25.rel': 'group_pressure',
              'wh25.CO2': FRAC, 'wh25.CO2_24H': FRAC,
              'console.battery': C, 'console.console_batt_volt': V, 'console.console_ext_volt': V,
              'console.charge_stat': C, 'console.battery_proz': PCT,
              'lightning.distance': 'group_distance', 'lightning.timestamp': 'group_time',
              'lightning.count': C, 'lightning.num': C,
              'co2.temperature': T, 'co2.temp': T, 'co2.humidity': PCT, 'co2.CO2': FRAC, 'co2.CO2_24H': FRAC,
              'co2.battery': C,
              'debug.heap': 'group_data', 'debug.runtime': 'group_deltatime',
              'debug.usr_interval': 'group_deltatime', 'debug.is_cnip': B,
              'apName': 'group_string', 'stationtype': 'group_string'})
    for pm in ('PM25', 'PM10', 'PM1', 'PM4'):
        g.update({f'co2.{pm}': CONC, f'co2.{pm}_RealAQI': C, f'co2.{pm}_24HAQI': C})
    for ch in range(1, 17):
        g.update({f'ch_soil.{ch}.humidity': PCT, f'ch_soil.{ch}.voltage': V, f'ch_soil{ch}nowAd': C,
                  f'ch_ec.{ch}.temp': T, f'ch_ec.{ch}.ec': 'group_usiecm', f'wh51.ch{ch}.voltage': V})
        if ch <= 8:
            g.update({f'ch_aisle.{ch}.temp': T, f'ch_aisle.{ch}.humidity': PCT, f'ch_temp.{ch}.temp': T,
                      f'ch_temp.{ch}.voltage': V, f'ch_leaf.{ch}.humidity': PCT, f'ch_leaf.{ch}.voltage': V})
        if ch <= 4:
            g.update({f'ch_pm25.{ch}.PM25': CONC, f'ch_pm25.{ch}.PM25_24H': CONC, f'ch_pm25.{ch}.PM25x': CONC,
                      f'ch_pm25.{ch}.PM25_RealAQI': C, f'ch_pm25.{ch}.PM25_24HAQI': C, f'ch_leak.{ch}.status': C,
                      f'ch_lds.{ch}.air': 'group_depth', f'ch_lds.{ch}.depth': 'group_depth',
                      f'ch_lds.{ch}.total_height': 'group_depth', f'ch_lds.{ch}.total_heat': C,
                      f'ch_lds.{ch}.voltage': V})
    for model, channels in SENSOR_CHANNELS.items():
        for name in _sensor_names(model, channels):
            g.update({f'{name}.battery': C, f'{name}.signal': C, f'{name}.rssi': 'group_dbm'})
    for f in ('ws85.version', 'ws85_vers', 'ws90.version', 'ws90_vers', 'radcompensation', 'upgrade', 'newVersion'):
        g[f] = C
    g.update({'wqt01.ec': 'group_usiecm', 'wqt01.toc': 'group_organicpollution', 'wqt01.turb': 'group_ntu',
              'wqt01.cod': 'group_organicpollution', 'wqt01.tds': 'group_organicpollution', 'wqt01.CO2': FRAC,
              'wqt01.CO2_24H': FRAC})
    for model in ('wqt01', 'wn64'):
        g.update({f'{model}.voltage': V, f'{model}.battery': C, f'{model}.signal': C, f'{model}.rssi': 'group_dbm'})
    return g


DEFAULT_GROUPS = _build_default_groups()


class DebugOptions:
    """Driver specific debug switches from the 'debug' config option."""

    debug_groups = ('rain', 'raindelta', 'wind', 'lightning', 'loop', 'sensors', 'parser', 'catchup',
                    'collector', 'archive')

    def __init__(self, **config):
        try:
            requested = {d.lower() for d in weeutil.weeutil.option_as_list(config.get('debug', []))}
        except AttributeError:
            requested = set()
        for group in self.debug_groups:
            setattr(self, group, group in requested)

    @property
    def any(self):
        return any(getattr(self, g) for g in self.debug_groups)


class InvertibleSetError(Exception):
    def __init__(self, value):
        self.value = value
        super().__init__(f'The value "{value}" is already in the mapping.')


class InvertibleMap(dict):
    """A dict that maintains an inverse (value -> key) mapping."""

    def __init__(self, *args, inverse=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.inverse = inverse if inverse is not None else \
            self.__class__({v: k for k, v in self.items()}, inverse=self)

    def __setitem__(self, key, value):
        if value in self.inverse:
            raise InvertibleSetError(value)
        dict.__setitem__(self.inverse, value, key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        dict.__delitem__(self.inverse, self[key])
        dict.__delitem__(self, key)

    def pop(self, key):
        dict.__delitem__(self.inverse, self[key])
        return dict.pop(self, key)


class ServiceInitializationError(Exception):
    pass


class UnknownApiCommand(Exception):
    pass


class DeviceIOError(Exception):
    pass


class ParseError(Exception):
    pass


class ProcessorError(Exception):
    pass


class UnitError(Exception):
    pass


class CatchupObjectError(Exception):
    pass


class InvalidApiResponseError(Exception):
    pass


class ApiResponseError(Exception):
    pass


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------

class FieldMapper:
    """Maps flattened device fields to WeeWX fields."""

    def __init__(self, driver_debug=None, default_map=None, **mapper_config):
        self.driver_debug = driver_debug
        self.wn32_indoor = weeutil.weeutil.tobool(mapper_config.get('wn32_indoor', False))
        self.wn32_outdoor = weeutil.weeutil.tobool(mapper_config.get('wn32_outdoor', False))
        self.field_map = self.construct_field_map(default_map or {}, **mapper_config)

    def construct_field_map(self, def_map, **config):
        default_map = InvertibleMap(def_map)
        for enabled, old, new in ((self.wn32_indoor, 'wh25', 'wn32'), (self.wn32_outdoor, 'wh26', 'wn32p')):
            if enabled:
                for source in (f'{old}.battery', f'{old}.signal'):
                    if source in default_map.inverse:
                        dest = default_map.inverse[source]
                        default_map.pop(dest)
                        default_map[dest.replace(old, new)] = source
        field_map = config.get('field_map')
        if field_map is None:
            field_map = dict(default_map)
        extensions = config.get('field_map_extensions', {})
        if extensions:
            ext_sources = set(extensions.values())
            field_map = {k: v for k, v in field_map.items() if v not in ext_sources}
            field_map.update(extensions)
        return InvertibleMap(field_map)

    def map_data(self, rec, unit_system=None):
        if not self.field_map:
            return rec
        mapped = {dest: rec.get(src) for dest, src in self.field_map.items() if src in rec}
        if unit_system is not None:
            mapped['usUnits'] = unit_system
        return mapped


# (WeeWX field prefix, device model, channels) for signal/rssi fields
_SIGNAL_SENSORS = (('wn20', 'wn20', 0), ('wh25', 'wh25', 0), ('wh26', 'wh26', 0), ('wh31', 'wn31', 8),
                   ('wn34', 'wn34', 8), ('wn35', 'wn35', 8), ('wn38', 'wn38', 0), ('wh40', 'wh40', 0),
                   ('wh41', 'wh41', 4), ('wh45', 'wh45', 0), ('wh51', 'wh51', 16), ('wh54', 'wh54', 4),
                   ('wh55', 'wh55', 4), ('wh57', 'wh57', 0), ('wh68', 'wh68', 0), ('wh69', 'wh69', 0),
                   ('ws80', 'ws80', 0), ('ws85', 'ws85', 0), ('ws90', 'ws90', 0), ('wqt01', 'wqt01', 0),
                   ('wn64', 'wn64', 0))


def _signal_map():
    """WeeWX signal/rssi fields, e.g. 'wh31_ch1_sig': 'wn31.ch1.signal'."""
    result = {}
    for suffix, key in (('sig', 'signal'), ('rssi', 'rssi')):
        for dest, src, channels in _SIGNAL_SENSORS:
            if channels:
                result.update(_chmap(((f'{dest}_ch{{}}_{suffix}', f'{src}.ch{{}}.{key}'),), channels))
            else:
                result[f'{dest}_{suffix}'] = f'{src}.{key}'
    return result


class HttpMapper(FieldMapper):
    """Field mapper for data obtained from the device HTTP API."""

    default_obs_map = {
        'inTemp': 'wh25.intemp', 'inHumidity': 'wh25.inhumi', 'pressure': 'wh25.abs', 'barometer': 'wh25.rel',
        'outTemp': 'common_list.0x02.val', 'dewpoint': 'common_list.0x03.val', 'feelslike': 'common_list.3.val',
        'appTemp': 'common_list.4.val', 'vpd': 'common_list.5.val', 'outHumidity': 'common_list.0x07.val',
        'radiation': 'common_list.0x15.val', 'uvradiation': 'common_list.0x16.val', 'UV': 'common_list.0x17.val',
        'bgt': 'common_list.0xA1.val', 'wbgt': 'common_list.0xA2.val',
        'lightning_dist': 'lightning.distance', 'lightning_disturber_count': 'lightning.timestamp',
        'lightning_num': 'lightning.num', 'lightningcount': 'lightning.count',
        'lightning_strike_count': 'lightning.count',
        **_chmap((('extraTemp{}', 'ch_aisle.{}.temp'), ('extraHumid{}', 'ch_aisle.{}.humidity'),
                  ('soilTemp{}', 'ch_temp.{}.temp')), 8),
        'co2in': 'wh25.CO2', 'co2in_24h': 'wh25.CO2_24H', 'co2': 'co2.CO2', 'co2_24h': 'co2.CO2_24H',
        'co2_Temp': 'co2.temp', 'co2_Hum': 'co2.humidity',
        **{k: v for name, p in (('pm2_5', '25'), ('pm10_0', '10'), ('pm1_0', '1'), ('pm4_0', '4'))
           for k, v in ((name, f'co2.PM{p}'), (f'pm{p}_24h_co2', f'co2.PM{p}_24H'),
                        (f'pm{p}_RealAQI_co2', f'co2.PM{p}_RealAQI'), (f'pm{p}_24hAQI_co2', f'co2.PM{p}_24HAQI'))},
        **_chmap((('pm25_{}', 'ch_pm25.{}.PM25'), ('pm25_avg_24h_ch{}', 'ch_pm25.{}.PM25_24H'),
                  ('pm25_RealAQI_ch{}', 'ch_pm25.{}.PM25_RealAQI'), ('pm25_AQI_24h_ch{}', 'ch_pm25.{}.PM25_24HAQI')),
                 4, channel_major=True),
        **_chmap((('soilMoist{}', 'ch_soil.{}.humidity'), ('soilMoist{}e', 'ch_ec.{}.humidity'),
                  ('soilmTemp{}', 'ch_ec.{}.temp'), ('soilEC{}', 'ch_ec.{}.ec')), 16),
        **_chmap((('leafWet{}', 'ch_leaf.{}.humidity'),), 8),
        **_chmap((('leak_{}', 'ch_leak.{}.status'), ('air_ch{}', 'ch_lds.{}.air'), ('depth_ch{}', 'ch_lds.{}.depth'),
                  ('ldsheat_ch{}', 'ch_lds.{}.total_heat'), ('thi_ch{}', 'ch_lds.{}.total_height')), 4),
        'heap': 'debug.heap', 'runtime': 'debug.runtime', 'ws_interval': 'debug.usr_interval',
        'radcompensation': 'radcompensation', 'upgrade': 'upgrade', 'newVersion': 'newVersion',
        'apName': 'apName', 'stationtype': 'stationtype', 'rain_source': 'rain_priority',
        'rain_day_reset': 'rain_reset_day', 'rain_week_reset': 'rain_reset_week',
        'rain_annual_reset': 'rain_reset_year', 'piezo': 'rain_piezo', 'raingain': 'rain_gain',
        **{f'gain{i}': f'gain{i + 1}' for i in range(5)},
        **_chmap((('soilad{}', 'ch_soil{}nowAd'), ('soilECad{}', 'ch_ec{}nowAd')), 16),
    }
    default_rain_map = {
        't_rainRate': 'rain.0x0E.val', 't_rainyear': 'rain.0x13.val', 't_rain': 't_rain',
        'eventRain': 'rain.0x0D.val', 'rain': 'rain', 'rainRate': 'rain.0x0E.val', 'hourRain': 'rain.0x7D.val',
        'dayRain': 'rain.0x10.val', 'weekRain': 'rain.0x11.val', 'monthRain': 'rain.0x12.val',
        'yearRain': 'rain.0x13.val', 'rain24': 'rain.0x7C.val', 'totalRain': 'rain.0x14.val',
        'srain': 'rain.srain.val', 'srain_piezo': 'piezoRain.srain_piezo.val', 'p_rainrate': 'piezoRain.0x0E.val',
        'p_rainyear': 'piezoRain.0x13.val', 'p_rain': 'p_rain', 'erain_piezo': 'piezoRain.0x0D.val', 'hail': 'hail',
        'rrain_piezo': 'piezoRain.0x0E.val', 'hailRate': 'piezoRain.0x0E.val', 'hrain_piezo': 'piezoRain.0x7D.val',
        'drain_piezo': 'piezoRain.0x10.val', 'wrain_piezo': 'piezoRain.0x11.val',
        'mrain_piezo': 'piezoRain.0x12.val', 'yrain_piezo': 'piezoRain.0x13.val',
        'rain24_piezo': 'piezoRain.0x7C.val', 'train_piezo': 'piezoRain.0x14.val'}
    default_wind_map = {'windDir': 'common_list.0x0A.val', 'windDir10': 'common_list.0x6D.val',
                        'windSpeed': 'common_list.0x0B.val', 'windGust': 'common_list.0x0C.val',
                        'maxdailygust': 'common_list.0x19.val'}
    default_sensor_state_map = {
        'inTempBatteryStatus': 'wh25.battery', 'outTempBatteryStatus': 'wh65.battery',
        'wh25_batt': 'wh25.battery', 'wh26_batt': 'wh26.battery',
        **_chmap((('batteryStatus{}', 'wn31.ch{}.battery'), ('soilTempBatt{}s', 'wn34.ch{}.battery')), 8),
        **_chmap((('pm25_Batt{}', 'wh41.ch{}.battery'),), 4),
        **_chmap((('soilMoistBatt{}s', 'wh51.ch{}.battery'),), 16),
        'co2_Batt': 'wh45.battery', 'wh40_batt': 'wh40.battery', 'wn20_batt': 'wn20.battery',
        'wn38_batt': 'wn38.battery',
        **_chmap((('leak_Batt{}', 'wh55.ch{}.battery'),), 4),
        'lightning_Batt': 'wh57.battery',
        **_chmap((('soilTempBatt{}', 'ch_temp.{}.voltage'),), 8),
        **_chmap((('soilMoistBatt{}', 'ch_soil.{}.voltage'),), 16),
        **_chmap((('leafWetBatt{}', 'ch_leaf.{}.voltage'),), 8),
        **_chmap((('ldsbatt{}', 'ch_lds.{}.voltage'),), 4),
        'bgtbatt': 'common_list.0xA1.voltage', 'wh68_batt': 'wh68.battery', 'wh69_batt': 'wh69.battery',
        'wh80_batt': 'ws80.battery', 'wh85_batt': 'ws85.battery', 'wh90_batt': 'ws90.battery',
        'rain_batt': 'rain.0x13.battery', 'piezorain_batt': 'piezoRain.0x13.battery',
        'ws85cap_volt': 'piezoRain.0x13.ws85cap_volt', 'ws90cap_volt': 'piezoRain.0x13.ws90cap_volt',
        'ws80_batt': 'common_list.0x0A.voltage', 'ws85_batt': 'ws85.voltage', 'ws90_batt': 'ws90.voltage',
        'rainBatteryStatus': 'rain.0x13.voltage', 'hailBatteryStatus': 'piezoRain.0x13.voltage',
        'windBatteryStatus': 'ws80.voltage', 'consBatteryVoltage': 'console.console_batt_volt',
        'ws6210_batt': 'console.battery', 'console_batt': 'console.console_batt_volt',
        'consoleext_batt': 'console.console_ext_volt', 'charge_stat': 'console.charge_stat',
        'consolebattp': 'console.battery_proz', 'ws1900batt': 'wh25.ws1900_batt',
        'ws1800batt': 'wh25.ws1800_batt', 'ws6006batt': 'wh25.ws6006_batt', 'wqt01_batt': 'wqt01.battery',
        'wqt01batt': 'wqt01.voltage', 'wn64_batt': 'wn64.battery', 'wn64batt': 'wn64.voltage',
        'ws85_ver': 'piezoRain.0x13.ws85_ver', 'ws90_ver': 'piezoRain.0x13.ws90_ver',
        **_signal_map(),
    }
    default_map = {**default_obs_map, **default_rain_map, **default_wind_map, **default_sensor_state_map}

    def __init__(self, driver_debug=None, default_map=None, **mapper_config):
        super().__init__(driver_debug=driver_debug, default_map=default_map or HttpMapper.default_map,
                         **mapper_config)
        if 'datetime' in self.field_map.inverse:
            self.field_map.pop(self.field_map.inverse['datetime'])
        self.field_map['dateTime'] = 'datetime'
        rain_src, wind_src = set(self.default_rain_map.values()), set(self.default_wind_map.values())
        self.rain_map = {d: s for d, s in self.field_map.items() if s in rain_src}
        self.wind_map = {d: s for d, s in self.field_map.items() if s in wind_src}
        for w_field, e_field in self.field_map.items():
            if w_field not in weewx.units.obs_group_dict and e_field in DEFAULT_GROUPS:
                weewx.units.obs_group_dict[w_field] = DEFAULT_GROUPS[e_field]
        if getattr(driver_debug, 'any', False) or weewx.debug > 0:
            log.info('     field map is %s', natural_sort_dict(self.field_map))


class SdMapper(FieldMapper):
    """Field mapper for device SD card history (CSV column names)."""

    default_map = {
        'wh25.intemp': 'Indoor Temperature', 'wh25.inhumi': 'Indoor Humidity', 'wh25.abs': 'ABS Pressure',
        'wh25.rel': 'REL Pressure', 'feelslike': 'Feels Like', 'common_list.0x02.val': 'Outdoor Temperature',
        'common_list.0x07.val': 'Outdoor Humidity', 'common_list.0x03.val': 'Dew Point', 'common_list.5.val': 'VPD',
        'common_list.0x0B.val': 'Wind', 'common_list.0x0C.val': 'Gust', 'common_list.0x0A.val': 'Wind Direction',
        'common_list.0x6D.val': 'windDir_10min_avg', 'common_list.0x15.val': 'Solar Rad',
        'common_list.0x17.val': 'UV-Index', 'common_list.0xA1.val': 'BGT', 'common_list.0xA2.val': 'WBGT',
        **{f'{arr}.{obs}.val': f'{pre}{name}' for arr, pre in (('rain', ''), ('piezoRain', 'Piezo '))
           for obs, name in (('0x0E', 'Rate' if pre else 'Rain Rate'), ('0x7D', 'Hourly Rain'), ('0x0D', 'Event Rain'),
                             ('0x10', 'Daily Rain'), ('0x7C', '24h Rain'), ('0x11', 'Weekly Rain'),
                             ('0x12', 'Monthly Rain'), ('0x13', 'Yearly Rain'), ('0x14', 'Total Rain'))},
        'rain.srain.val': 'Rain Status', 'piezoRain.srain_piezo.val': 'Piezo srain',
        **_chmap((('ch_aisle.{}.temp', 'CH{} Temperature'), ('ch_aisle.{}.humidity', 'CH{} Humidity'),
                  ('dewpoint{}', 'CH{} Dew point'), ('heatindex{}', 'CH{} HeatIndex'),
                  ('ch_leaf.{}.humidity', 'WH35 CH{}hum'), ('ch_temp.{}.temp', 'WN34 CH{}')), 8),
        'lightning.timestamp': 'Thunder time', 'lightning.count': 'Thunder count',
        'lightning.distance': 'Thunder distance', 'co2.temp': 'AQIN Temperature', 'co2.humidity': 'AQIN Humidity',
        'co2.CO2': 'AQIN CO2', 'co2.PM25': 'AQIN PM2.5', 'co2.PM10': 'AQIN PM10', 'co2.PM1': 'AQIN PM1.0',
        'co2.PM4': 'AQIN PM4.0',
        **_chmap((('ch_soil.{}.humidity', 'SoilMoisture CH{}'), ('ch_soil{}nowAd', 'SoilMoistureAD CH{}'),
                  ('ch_ec.{}.temp', 'SoilTemp CH{}'), ('ch_ec.{}.ec', 'SoilEC CH{}')), 16),
        **_chmap((('ch_leak.{}.status', 'Water CH{}'), ('ch_pm25.{}.PM25x', 'Pm2.5 CH{}'),
                  ('ch_pm25.{}.PM25', 'PM2.5 CH{}'), ('ch_lds.{}.air', 'LDS_Air CH{}'),
                  ('ch_lds.{}.depth', 'LDS_Depth CH{}'), ('ch_lds.{}.total_heat', 'LDS_Heat CH{}')), 4),
        'console.console_batt_volt': 'Console Battery ', 'console.console_ext_volt': 'External Supply ',
        'console.battery_proz': 'Console Battery Percentage', 'console.charge_stat': 'Charge',
        **{f'wqt01.{k}': f'WQT_{k.upper()}' for k in ('ec', 'toc', 'turb', 'cod', 'tds', 'CO2')},
    }
    _leak_status = {'Normal': 0, 'Leaking': 1, 'Offline': 2}

    def __init__(self, driver_debug=None, default_map=None, **mapper_config):
        super().__init__(driver_debug=driver_debug, default_map=default_map or SdMapper.default_map,
                         **mapper_config)

    def map_data(self, rec, unit_system=None):
        if not self.field_map:
            return rec
        debug = getattr(self.driver_debug, 'catchup', False)
        mapped = {}
        for field, value in rec.items():
            key = re.sub(r'\(.*?\)', '', field)
            if 'Time' in key or key == '':
                if debug:
                    log.info('Problem with key %s', key)
                continue
            try:
                pm = re.search(r'Pm2\.5 CH([1-4])', key)
                dest = f'ch_pm25.{pm.group(1)}.PM25' if pm else self.field_map.inverse[key]
                if key == 'Thunder time':
                    mapped[dest] = datetime.datetime.strptime(value, '%Y-%m-%d %H:%M').timestamp()
                elif value in ('--', ''):
                    if debug:
                        log.info('no Data field %s', key)
                elif 'Water' in key:
                    if value in self._leak_status:
                        mapped[dest] = self._leak_status[value]
                else:
                    mapped[dest] = float(value)
            except (KeyError, TypeError, ValueError, OverflowError) as e:
                if debug:
                    log.info("Error mapping field '%s': %s", field, e)
        return mapped


# ---------------------------------------------------------------------------
# Driver and service
# ---------------------------------------------------------------------------

class DeltaTracker:
    """Turns a cumulative device counter into per-period deltas."""

    def __init__(self, label, candidates, debug=None, prefix='', flag='raindelta'):
        self.label, self.candidates, self.debug, self.prefix, self.flag = label, candidates, debug, prefix, flag
        self.field = self.last = self.delta = None

    def update(self, data):
        """Update from a data dict and return the latest delta."""
        if self.field is None:
            self.field = next((f for f in self.candidates if f in data), None)
            if self.field is not None:
                log.info("%sUsing '%s' for %s total", self.prefix, self.field, self.label)
            elif self.debug is not None and self.debug.rain:
                log.info('%sNo suitable field found for %s', self.prefix, self.label)
        if self.field is not None and self.field in data:
            self.delta = self.calc(data[self.field])
        return self.delta

    def calc(self, new):
        last, self.last = self.last, new
        if last is None:
            log.info('%sskipping %s measurement of %s: no last value', self.prefix, self.label, new)
            return None
        if new is None:
            log.info('%sskipping %s measurement: no current data', self.prefix, self.label)
            return None
        delta = new if new < last else new - last
        if new < last:
            log.info('%s%s counter wraparound detected: new=%s last=%s', self.prefix, self.label, new, last)
        if self.debug is not None and getattr(self.debug, self.flag) and delta != 0:
            log.info('%s%s: last=%s new=%s delta=%s', self.prefix, self.label, last, new, delta)
        return delta


def calc_twb(temp_c, humidity):
    """Stull wet bulb temperature (degree C) used as a WBGT estimate."""
    return (temp_c * math.atan(0.151977 * (humidity + 8.313659) ** 0.5) + math.atan(temp_c + humidity)
            - math.atan(humidity - 1.676331) + 0.00391838 * humidity ** 1.5 * math.atan(0.023101 * humidity)
            - 4.686035)


UNIT_SYSTEMS = {'us': weewx.US, 'metric': weewx.METRIC, 'metricwx': weewx.METRICWX}


def check_units(units, label):
    units = str(units).lower()
    if units not in ('native', *UNIT_SYSTEMS):
        log.error("%s: unknown units '%s', using 'native'", label, units)
        return 'native'
    return units


def export_packet(packet, units):
    """Copy of a loop packet in the requested unit system, with non-finite floats as None."""
    data = packet
    if units != 'native':
        data = weewx.units.StdUnitConverters[UNIT_SYSTEMS[units]].convertDict(packet)
        data['usUnits'] = UNIT_SYSTEMS[units]
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in data.items()}


class OnceLogger:
    """Logs a repeating error once, and logs when it clears."""

    def __init__(self, label):
        self.label, self.last = label, None

    def error(self, msg):
        if msg != self.last:
            log.error('%s: %s', self.label, msg)
            self.last = msg

    def ok(self, msg):
        if self.last is not None:
            log.info('%s: %s', self.label, msg)
            self.last = None


def sensor_id(value):
    """A hardware sensor ID in a comparable form: upper case hex, no 0x prefix or leading zeros."""
    text = str(value).strip().upper()
    text = text[2:] if text.startswith('0X') else text
    return text.lstrip('0') or '0'


def channel_keys(model, channel):
    """Prefixes (or whole names) of the fields that belong to channel of a multi-channel model."""
    keys = [f'{model}.ch{channel}.'] + [f'{group}.{channel}.' for group in SENSOR_GROUPS[model][1]]
    if model == 'wh51':
        keys += [f'ch_soil{channel}nowAd', f'ch_ec{channel}nowAd']
    return keys


_CHANNEL_KEY = re.compile(r'(?:(\w+?)\.ch(\d+)\.|(ch_[a-z0-9]+)\.(\d+)\.|(ch_soil|ch_ec)(\d+)nowAd$)')
_SENSOR_ID_KEY = re.compile(r'(\w+?)\.ch(\d+)\.id')


class SensorMapper:
    """Reports multi-channel sensors on fixed channels chosen by hardware ID ([[sensor_map]]).

    Each entry is '<sensor ID> = <channel>'. The sensor's data is reported on that channel whichever
    gateway channel it is paired on. A sensor pushed off that channel takes the channel that was
    freed, so data is never merged or lost.
    """

    def __init__(self, config=None):
        self.targets = {}
        for key, value in (config or {}).items():
            match = re.fullmatch(r'(?:ch)?\s*(\d+)', str(value).strip().lower())
            if match and re.fullmatch(r'(0x)?[0-9a-f]+', str(key).strip().lower()):
                self.targets[sensor_id(key)] = int(match.group(1))
            else:
                log.error("sensor_map: ignoring '%s = %s', expected '<sensor ID> = <channel number>'", key, value)
        self.plan_key = None
        self.perms, self.lines, self.problems = {}, [], []

    def __bool__(self):
        return bool(self.targets)

    @staticmethod
    def paired(data):
        """{model: {gateway channel: sensor ID}} for the registered multi-channel sensors in data."""
        found = {}
        for key, value in data.items():
            match = _SENSOR_ID_KEY.fullmatch(key)
            if match and match.group(1) in SENSOR_GROUPS and value is not None \
                    and str(value).upper() not in _UNREGISTERED_IDS:
                found.setdefault(match.group(1), {})[int(match.group(2))] = str(value)
        return found

    def plan(self, sensors):
        """Work out, and log when it changes, the channel moves {model: {from: to}} for sensors."""
        if not self.targets:
            return {}
        paired = self.paired(sensors)
        key = tuple(sorted((m, c, i) for m, chans in paired.items() for c, i in chans.items()))
        if key == self.plan_key:
            return self.perms
        self.plan_key = key
        where = {sensor_id(i): (m, c) for m, chans in paired.items() for c, i in chans.items()}
        wanted, problems = {}, []
        for sid, target in self.targets.items():
            if sid not in where:
                problems.append(f'sensor {sid} is not paired with the gateway, so it is not mapped')
                continue
            model, channel = where[sid]
            count = SENSOR_GROUPS[model][0]
            if not 1 <= target <= count:
                problems.append(f'{model.upper()} sensor {sid}: channel {target} is outside 1-{count}, not mapped')
            elif target in wanted.get(model, {}).values():
                problems.append(f'{model.upper()} sensor {sid}: channel {target} is already mapped to another '
                                'sensor, not mapped')
            else:
                wanted.setdefault(model, {})[channel] = target
        self.perms, self.lines = {}, []
        for model, moves in wanted.items():
            perm = self._permutation(moves, paired[model], SENSOR_GROUPS[model][0])
            for src, dst in sorted(perm.items()):
                if src != dst and src in paired[model]:
                    why = 'mapped' if src in moves else 'moved to make room'
                    self.lines.append(f'{model.upper()} sensor {paired[model][src]} on gateway channel {src} '
                                      f'is reported as channel {dst} ({why})')
            if any(src != dst for src, dst in perm.items()):
                self.perms[model] = perm
        self.problems = problems
        for problem in problems:
            log.warning('sensor_map: %s', problem)
        for line in self.lines:
            log.info('sensor_map: %s', line)
        if not self.lines and not problems:
            log.info('sensor_map: every mapped sensor is already on its channel')
        return self.perms

    @staticmethod
    def _permutation(moves, occupied, count):
        """Extend moves {from: to} to a one-to-one mapping of every channel 1..count."""
        perm = dict(moves)
        taken = set(moves.values())
        for channel in sorted(occupied):
            if channel not in perm and channel not in taken:
                perm[channel] = channel
                taken.add(channel)
        channels = range(1, count + 1)
        # sensors pushed off their channel go first, preferably to the channels mapped sensors left
        sources = sorted(c for c in occupied if c not in perm) + [c for c in channels
                                                                  if c not in perm and c not in occupied]
        free = [c for c in moves if c not in taken] + [c for c in channels if c not in taken and c not in moves]
        perm.update(zip(sources, free))
        return perm

    def reported_channel(self, model, channel):
        return self.perms.get(model, {}).get(channel, channel)

    def apply(self, data, sensors=None):
        """Return data with multi-channel sensor fields moved to their reported channels.

        sensors is the sensor data holding the IDs, if data itself does not (catchup records).
        """
        if not self.targets:
            return data
        perms = self.plan(data if sensors is None else sensors)
        if not perms:
            return data
        result = {}
        for key, value in data.items():
            match = _CHANNEL_KEY.match(key)
            if match:
                model, mch, group, gch, ad, adch = match.groups()
                owner, channel, fmt = ((model, mch, f'{model}.ch{{}}.') if model else
                                       (_GROUP_MODEL.get(group), gch, f'{group}.{{}}.') if group else
                                       ('wh51', adch, f'{ad}{{}}nowAd'))
                new = perms.get(owner, {}).get(int(channel))
                if new is not None:
                    key = fmt.format(new) + key[match.end():]
                    if group and key.endswith('.channel'):
                        value = new
            result[key] = value
        return result


class LoopJsonWriter:
    """Writes each loop packet to a JSON file (ecwLoop.json by default) for web pages and scripts."""

    default_name = 'ecwLoop.json'

    def __init__(self, config, html_dir=None):
        config = config or {}
        self.enabled = weeutil.weeutil.tobool(config.get('enable', False))
        self.units = check_units(config.get('units', 'native'), 'loop_json')
        path = os.path.expanduser(str(config.get('path', self.default_name)))
        if not os.path.isabs(path):
            path = os.path.join(html_dir or os.getcwd(), path)
        if path.endswith(os.sep) or os.path.isdir(path):
            path = os.path.join(path, self.default_name)
        self.path = path
        self.errors = OnceLogger('loop_json')
        if self.enabled:
            log.info('     loop data will be written to %s (%s units)', self.path, self.units)

    def write(self, packet):
        if not self.enabled:
            return
        try:
            tmp_path = f'{self.path}.tmp'
            with open(tmp_path, 'w') as f:
                json.dump(export_packet(packet, self.units), f, default=str, sort_keys=True)
            os.replace(tmp_path, self.path)
        except (OSError, TypeError, ValueError, KeyError) as e:
            self.errors.error(f'unable to write {self.path}: {e}')
        else:
            self.errors.ok(f'writing to {self.path} again')


class MqttPublisher:
    """Publishes each loop packet to an MQTT broker (needs the paho-mqtt package)."""

    formats = ('json', 'individual', 'both')

    def __init__(self, config):
        config = config or {}
        self.enabled = weeutil.weeutil.tobool(config.get('enable', False))
        self.client = None
        if not self.enabled:
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.error("mqtt: the paho-mqtt package is not installed, MQTT publishing is disabled. Install it with "
                      "'sudo apt install python3-paho-mqtt' (Debian) or 'pip install paho-mqtt' (pip install)")
            self.enabled = False
            return
        to_int, to_bool = weeutil.weeutil.to_int, weeutil.weeutil.tobool
        self.mqtt = mqtt
        self.host = config.get('host', 'localhost')
        tls = to_bool(config.get('tls', False))
        self.port = to_int(config.get('port', 8883 if tls else 1883))
        self.topic = str(config.get('topic', 'weewx/ecowitt')).rstrip('/')
        self.format = str(config.get('format', 'json')).lower()
        if self.format not in self.formats:
            log.error("mqtt: unknown format '%s', using 'json'", self.format)
            self.format = 'json'
        self.units = check_units(config.get('units', 'native'), 'mqtt')
        self.qos = min(max(to_int(config.get('qos', 0)), 0), 2)
        self.retain = to_bool(config.get('retain', False))
        self.errors = OnceLogger('mqtt')
        client_id = config.get('client_id') or f'weewx-ecowittgateway-{os.getpid()}'
        if hasattr(mqtt, 'CallbackAPIVersion'):                 # paho-mqtt 2.x
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        else:                                                    # paho-mqtt 1.x
            self.client = mqtt.Client(client_id=client_id)
        if config.get('username'):
            self.client.username_pw_set(config['username'], config.get('password') or None)
        if tls:
            self.client.tls_set(ca_certs=config.get('ca_certs') or None, certfile=config.get('certfile') or None,
                                keyfile=config.get('keyfile') or None)
            self.client.tls_insecure_set(to_bool(config.get('tls_insecure', False)))
        self.client.will_set(f'{self.topic}/status', 'offline', qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(1, 60)
        self.client.connect_async(self.host, self.port, to_int(config.get('keepalive', 60)))
        self.client.loop_start()
        log.info("     loop data will be published to MQTT broker %s:%s, topic '%s' (%s, %s units)",
                 self.host, self.port, self.topic, self.format, self.units)

    @staticmethod
    def _failed(rc):
        """True if a paho 1.x (int) or 2.x (ReasonCode) result code is a failure."""
        return rc.is_failure if hasattr(rc, 'is_failure') else rc != 0

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if self._failed(rc):
            self.errors.error(f'connection to {self.host}:{self.port} refused: {rc}')
            return
        log.info('mqtt: connected to %s:%s', self.host, self.port)
        self.errors.last = None
        client.publish(f'{self.topic}/status', 'online', qos=1, retain=True)

    def _on_disconnect(self, client, userdata, *args):
        # paho 1.x: (rc); paho 2.x: (flags, rc, properties)
        rc = args[1] if len(args) >= 2 else (args[0] if args else 0)
        if self._failed(rc):
            self.errors.error(f'disconnected from {self.host}:{self.port} ({rc}), reconnecting')

    def publish(self, packet):
        if not self.enabled:
            return
        try:
            data = export_packet(packet, self.units)
            messages = []
            if self.format in ('json', 'both'):
                messages.append((f'{self.topic}/loop', json.dumps(data, default=str, sort_keys=True)))
            if self.format in ('individual', 'both'):
                messages += [(f'{self.topic}/{k}', '' if v is None else str(v)) for k, v in data.items()]
            for topic, payload in messages:
                result = self.client.publish(topic, payload, qos=self.qos, retain=self.retain)
                if result.rc != self.mqtt.MQTT_ERR_SUCCESS:
                    self.errors.error(f'not published to {self.host}:{self.port}: '
                                      f'{self.mqtt.error_string(result.rc)}')
                    return
        except (ValueError, TypeError, KeyError) as e:
            self.errors.error(f'unable to publish: {e}')
        else:
            self.errors.ok(f'publishing to {self.host}:{self.port} again')

    def close(self):
        if self.client is not None:
            try:
                self.client.publish(f'{self.topic}/status', 'offline', qos=1, retain=True)
                self.client.disconnect()
                self.client.loop_stop()
            except Exception as e:
                log.debug('mqtt: error while closing: %s', e)
            self.client = None
            self.enabled = False


class EcowittCommon:
    """Functionality shared by the driver and the service."""

    def __init__(self, unit_system=None, html_dir=None, **ec_config):
        self.driver_debug = dbg = DebugOptions(**ec_config)
        self.mapper = HttpMapper(driver_debug=dbg, **ec_config)
        to_int, to_bool = weeutil.weeutil.to_int, weeutil.weeutil.tobool
        max_tries = to_int(ec_config.get('max_tries', DEFAULT_MAX_TRIES))
        retry_wait = to_int(ec_config.get('retry_wait', DEFAULT_RETRY_WAIT))
        self.url_timeout = to_int(ec_config.get('url_timeout', DEFAULT_URL_TIMEOUT))
        self.ip_address = ec_config.get('ip_address')
        self.poll_interval = int(ec_config.get('poll_interval', DEFAULT_POLL_INTERVAL))
        self.api_key = ec_config.get('api_key') or None
        self.app_key = ec_config.get('app_key') or None
        self.mac = ec_config.get('mac')
        define_units()
        log.info('     device IP address is %s', self.ip_address)
        log.info('     poll interval is %d seconds', self.poll_interval)
        if dbg.any or weewx.debug > 0:
            log.info('     Max tries is %d URL retry wait is %d seconds', max_tries, retry_wait)
            log.info('     URL timeout is %d seconds', self.url_timeout)
        for group in dbg.debug_groups:
            log.info('%9s debug is %s', group, 'set' if getattr(dbg, group) else 'not set')
        log.info("   wn32_indoor: sensor ID decoding will use %s",
                 "indoor 'WN32'" if self.mapper.wn32_indoor else "'WH26'")
        log.info("  wn32_outdoor: sensor ID decoding will use %s",
                 "outdoor 'WN32P'" if self.mapper.wn32_outdoor else "'WH26'")
        self.collector = EcowittHttpCollector(
            ip_address=self.ip_address, poll_interval=self.poll_interval, max_tries=max_tries,
            retry_wait=retry_wait, url_timeout=self.url_timeout, unit_system=unit_system,
            show_battery=to_bool(ec_config.get('show_all_batt', DEFAULT_FILTER_BATTERY)),
            get_soilad=to_bool(ec_config.get('get_soilad', DEFAULT_GET_SOILAD)),
            log_unknown_fields=to_bool(ec_config.get('log_unknown_fields', False)),
            fw_update_check_interval=int(ec_config.get('firmware_update_check_interval', DEFAULT_FW_CHECK_INTERVAL)),
            sensor_map=ec_config.get('sensor_map'), debug=dbg)
        self.rain, self.piezo, self.lightning = self._trackers()
        self.loop_json = LoopJsonWriter(ec_config.get('loop_json', {}), html_dir)
        self.mqtt = MqttPublisher(ec_config.get('mqtt', {}))

    def _trackers(self, prefix=''):
        return (DeltaTracker('rain', ('rain.0x13.val', 'rain.0x12.val'), self.driver_debug, prefix),
                DeltaTracker('piezo rain', ('piezoRain.0x13.val', 'piezoRain.0x12.val'), self.driver_debug, prefix),
                DeltaTracker('lightning', ('lightning.count',), self.driver_debug, prefix, 'lightning'))

    @property
    def model(self):
        return self.collector.device.model

    def export(self, packet):
        """Send a finished loop packet to the optional JSON file and MQTT broker."""
        self.loop_json.write(packet)
        self.mqtt.publish(packet)

    def log_subset(self, data, label=None):
        """Log the rain and/or wind fields in data as per debug settings."""
        for enabled, fmap, what in ((self.driver_debug.rain, self.mapper.rain_map, 'rain'),
                                    (self.driver_debug.wind, self.mapper.wind_map, 'wind')):
            if not enabled:
                continue
            msgs = [f'{w}={data[w]}' for w in fmap if w in data]
            msgs += [f'{g}={data[g]}' for w, g in fmap.items() if g in data and g != w]
            prefix = f'{label}: ' if label else ''
            log.info('%s%s', prefix, ' '.join(msgs) if msgs else f'no {what} data found')

    def log_data(self, label, data, loop_debug=None):
        """Log a full data dict (loop debug) or just its rain/wind subsets."""
        if self.driver_debug.loop if loop_debug is None else loop_debug:
            ts = data.get('dateTime', data.get('datetime'))
            ts_str = f'{timestamp_to_string(ts)} ' if ts is not None else ''
            log.info('%s: %s%s', label, ts_str, natural_sort_dict(data))
        else:
            self.log_subset(data, label)

    def process_live_data(self, data, packet, wbgt_us=False):
        """Update the counters from live data and add derived fields to packet."""
        self.rain.update(data)
        self.piezo.update(data)
        if 'lightning.count' in data:
            packet['lightning_num'] = data['lightning.count']
            data['lightning.count'] = self.lightning.calc(data['lightning.count'])
        for key in ('ws85', 'ws90'):
            if f'{key}.version' in data:
                packet[f'{key}_ver'] = data[f'{key}.version']
        for ch in range(1, 17):
            for src, dest in (('humidity', 'soilMoist'), ('voltage', 'soilMoistBatt')):
                if f'ch_ec.{ch}.{src}' in data:
                    packet[f'{dest}{ch}'] = data[f'ch_ec.{ch}.{src}']
        if 'piezoRain.0x13.voltage' in data:
            for key in ('ws85', 'ws90'):
                if f'piezoRain.0x13.{key}_ver' in data or f'{key}.version' in data:
                    packet[f'{key}_batt'] = data['piezoRain.0x13.voltage']
                    break
        temp, hum = data.get('common_list.0x02.val'), data.get('common_list.0x07.val')
        if 'common_list.0xA2.val' not in data and temp is not None and hum is not None:
            twb = calc_twb(float(temp), float(hum))
            packet['wbgt'] = twb * 1.8 + 32 if wbgt_us else twb


class EcowittHttpService(weewx.engine.StdService, EcowittCommon):
    """WeeWX service that augments loop packets with Ecowitt device data."""

    def __init__(self, engine, config_dict):
        svc_config = config_dict.get('EcowittHttpService') or driver_config(config_dict)
        log.info('EcowittHttpService: version is %s', DRIVER_VERSION)
        self.unit_system = DEFAULT_UNIT_SYSTEM
        try:
            EcowittCommon.__init__(self, unit_system=self.unit_system, html_dir=html_root(config_dict),
                                   **svc_config)
        except weewx.ViolatedPrecondition as e:
            raise ServiceInitializationError from e
        weewx.engine.StdService.__init__(self, engine, config_dict)
        self.max_age = int(svc_config.get('max_age', DEFAULT_MAX_AGE))
        self.lost_contact_log_period = int(svc_config.get('lost_contact_log_period',
                                                          DEFAULT_LOST_CONTACT_LOG_PERIOD))
        if self.driver_debug.any or weewx.debug > 0:
            log.info('     max age of API data to be used is %d seconds', self.max_age)
            log.info('     lost contact will be logged every %d seconds', self.lost_contact_log_period)
        self.log_failures = True
        self.lost_con_ts = None
        self.latest_sensor_data = None
        self.collector.startup()
        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)

    def new_loop_packet(self, event):
        dbg = self.driver_debug
        verbose = dbg.loop or dbg.rain or dbg.wind
        packet = event.packet
        if verbose:
            log.info('EcowittHttpService: Processing loop packet: %s %s',
                     timestamp_to_string(packet['dateTime']), natural_sort_dict(packet))
        self.latest_sensor_data = None
        while True:
            try:
                data = self.collector.queue.get(True, 0.5)
            except queue.Empty:
                if self.latest_sensor_data is None and verbose:
                    log.info('EcowittHttpService: No queued items to process')
                if self.lost_con_ts is not None and time.time() > self.lost_con_ts + self.lost_contact_log_period:
                    self.lost_con_ts = time.time()
                    self.set_failure_logging(True)
                break
            if hasattr(data, 'keys'):
                self.lost_con_ts = None
                self.set_failure_logging(True)
                self.process_live_data(data, packet, wbgt_us=packet.get('usUnits') == weewx.US)
                packet['t_rain'] = self.rain.delta
                packet['p_rain'] = self.piezo.delta
                self.log_data(f'EcowittHttpService: newLoop Received {self.model} data', data)
                self.process_queued_sensor_data(data, packet['dateTime'])
            elif isinstance(data, BaseException):
                self.process_queued_exception(data)
            elif data is None:
                if dbg.loop:
                    log.info('EcowittHttpService: Received collector shutdown signal')
                self.shutDown()
        if self.latest_sensor_data is not None:
            mapped = self.mapper.map_data(self.latest_sensor_data)
            mapped['usUnits'] = self.unit_system
            self.log_data(f'EcowittHttpService: newLoop Mapped {self.model} data', mapped)
            self.augment_packet(packet, mapped)
            self.export(packet)
            self.log_data('EcowittHttpService: newLoop Augmented packet', packet, dbg.loop or weewx.debug >= 2)

    def process_queued_sensor_data(self, sensor_data, date_time):
        ts = sensor_data.get('datetime')
        if ts is None:
            if self.driver_debug.loop or weewx.debug >= 2:
                log.info('EcowittHttpService: Discarded non-timestamped packet')
        elif ts > date_time - self.max_age:
            if self.latest_sensor_data is None or ts > self.latest_sensor_data['datetime']:
                self.latest_sensor_data = dict(sensor_data)
        elif self.driver_debug.loop or weewx.debug >= 2:
            log.info('EcowittHttpService: Discarded packet with timestamp %s', timestamp_to_string(ts))

    def process_queued_exception(self, e):
        if isinstance(e, DeviceIOError):
            if self.lost_con_ts is None:
                self.lost_con_ts = time.time()
            self.set_failure_logging(False)
        else:
            log.error('EcowittHttpService: Caught unexpected exception %s: %s', e.__class__.__name__, e)

    def augment_packet(self, packet, data):
        converted = weewx.units.StdUnitConverters[packet['usUnits']].convertDict(data)
        if self.driver_debug.loop:
            log.info('EcowittHttpService: Converted %s data: %s', self.model, natural_sort_dict(converted))
        for field, value in converted.items():
            packet.setdefault(field, value)

    def set_failure_logging(self, log_failures):
        self.log_failures = self.collector.log_failures = self.collector.device.log_failures = log_failures

    def shutDown(self):
        self.collector.shutdown()
        self.mqtt.close()


def loader(config_dict, engine):
    return EcowittHttpDriver(html_dir=html_root(config_dict), **driver_config(config_dict))


def configurator_loader(config_dict):
    return EcowittHttpDriverConfigurator()


def confeditor_loader():
    return EcowittHttpDriverConfEditor()


class EcowittHttpDriver(weewx.drivers.AbstractDevice, EcowittCommon):
    """WeeWX driver for Ecowitt devices using the local HTTP API."""

    def __init__(self, html_dir=None, **stn_dict):
        log.info('EcowittHttpDriver: version is %s', DRIVER_VERSION)
        self.unit_system = DEFAULT_UNIT_SYSTEM
        log.info('unit_system: %s', self.unit_system)
        try:
            EcowittCommon.__init__(self, unit_system=self.unit_system, html_dir=html_dir, **stn_dict)
        except weewx.ViolatedPrecondition as e:
            raise weewx.engine.InitializationError from e
        catchup = stn_dict.get('catchup', {})
        self.catchup_source = catchup.get('source')
        log.info('catchup source: %s', self.catchup_source)
        self.catchup_grace = weeutil.weeutil.to_int(catchup.get('grace', DEFAULT_CATCHUP_GRACE))
        self.catchup_retries = weeutil.weeutil.to_int(catchup.get('retries', DEFAULT_CATCHUP_RETRIES))
        self.rain_source = str(stn_dict.get('rain_source', 'tipping')).lower()
        if self.rain_source not in ('tipping', 'piezo', 'both'):
            log.error("Unknown rain_source '%s', using 'tipping'", self.rain_source)
            self.rain_source = 'tipping'
        log.info("WeeWX 'rain' and 'rainRate' are taken from the %s gauge%s",
                 'piezo' if self.rain_source == 'piezo' else 'tipping',
                 "; the piezo gauge is recorded in 'p_rain'/'hail'" if self.rain_source == 'both' else '')
        self.rain_a, self.piezo_a, self.lightning_a = self._trackers('Archive: ')
        self.collector.startup()

    def genLoopPackets(self):
        while True:
            try:
                data = self.collector.queue.get(True, 10)
            except queue.Empty:
                continue
            if hasattr(data, 'keys'):
                self.log_data(f'EcowittHttpDriver: Loop Received {self.model} data', data)
                packet = {'dateTime': data['datetime'] if 'datetime' in data else int(time.time() + 0.5),
                          'usUnits': self.unit_system}
                self.process_live_data(data, packet)
                packet['t_rain'] = self.rain.delta
                packet['hail'] = packet['p_rain'] = self.piezo.delta
                packet['rain'] = self.piezo.delta if self.rain_source == 'piezo' else self.rain.delta
                mapped = self.mapper.map_data(data)
                self.log_data(f'EcowittHttpDriver: Loop Mapped {self.model} data', mapped)
                packet.update(mapped)
                self.use_piezo_rate(data, packet)
                self.log_data('EcowittHttpDriver: Loop Packet', packet, self.driver_debug.loop or weewx.debug >= 2)
                self.export(packet)
                yield packet
            elif isinstance(data, BaseException):
                if isinstance(data, DeviceIOError):
                    raise weewx.WeeWxIOError from data
                log.error('EcowittHttpDriver: Loop Caught unexpected exception %s: %s', data.__class__.__name__, data)
                raise data
            elif data is None:
                if self.driver_debug.loop:
                    log.info('EcowittHttpDriver: Received shutdown signal from the Collector')
                self.closePort()
                raise DeviceIOError('EcowittHttpCollector needs to shutdown')

    def genStartupRecords(self, last_ts):
        return self.genArchiveRecords(last_ts)

    def genArchiveRecords(self, lastgood_ts):
        if self.driver_debug.archive or self.driver_debug.catchup:
            log.info('genArchiveRecords: Using MAC address: %s', self.mac_address)
        try:
            for rec in self.gen_ecowitt_archive_records(since_ts=lastgood_ts):
                if self.driver_debug.catchup:
                    log.info('genArchiveRecords: Yielding archive record %s', timestamp_to_string(rec['dateTime']))
                yield rec
        except (DeviceIOError, OSError, InvalidApiResponseError) as e:
            log.error('genArchiveRecords: History access error: %s', e)

    def gen_ecowitt_archive_records(self, since_ts):
        try:
            catchup_obj = self.catchup_factory()
        except CatchupObjectError:
            return
        mapper, sensors = self.collector.sensor_mapper, None
        if mapper:
            try:
                sensors = self.collector.device.get_sensors_data()
            except (DeviceIOError, InvalidApiResponseError) as e:
                log.error('sensor_map: cannot read sensor IDs, catchup records are not remapped: %s', e)
        for rec in catchup_obj.gen_history_records(start_ts=since_ts):
            if sensors is not None:
                rec = mapper.apply(rec, sensors)
            record = {'dateTime': rec['datetime'], 'usUnits': self.unit_system, 'interval': rec['interval']}
            rain, piezo = self.rain_a.update(rec), self.piezo_a.update(rec)
            if 'lightning.count' in rec:
                rec['lightning.count'] = self.lightning_a.calc(rec['lightning.count'])
            if 'common_list.5.val' not in rec:
                try:
                    temp, hum = float(rec['common_list.0x02.val']), float(rec['common_list.0x07.val'])
                    rec['common_list.5.val'] = round(0.61094 * math.exp(17.625 * temp / (temp + 243.04))
                                                     * (1 - hum / 100), 2) * 10
                except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
                    pass
            rec['t_rain'] = rain
            rec['hail'] = rec['p_rain'] = piezo
            rec['rain'] = piezo if self.rain_source == 'piezo' else rain
            if 'piezoRain.0x13.voltage' in rec:
                key = 'ws85' if 'ws85.version' in rec else 'ws90'
                rec[f'{key}_batt'] = rec['piezoRain.0x13.voltage']
                rec[f'{key}cap_volt'] = rec.get('piezocap_volt')
            if self.driver_debug.archive:
                log.info('Archive rec data %s', rec)
            record.update(self.mapper.map_data(rec))
            self.use_piezo_rate(rec, record)
            yield record

    def use_piezo_rate(self, data, packet):
        """Take rainRate from the piezo gauge when rain_source = piezo."""
        if self.rain_source == 'piezo' and 'piezoRain.0x0E.val' in data:
            packet['rainRate'] = data['piezoRain.0x0E.val']

    def catchup_factory(self):
        source = (self.catchup_source or 'either').lower()
        if source not in ('either', 'both', 'net', 'device'):
            raise CatchupObjectError
        if source != 'net':
            try:
                return EcowittDeviceCatchup(ip_address=self.ip_address, unit_system=self.unit_system,
                                            catchup_grace=self.catchup_grace, catchup_retries=self.catchup_retries,
                                            url_timeout=self.url_timeout, driver_debug=self.driver_debug)
            except weewx.ViolatedPrecondition as e:
                raise CatchupObjectError from e
            except CatchupObjectError:
                if source == 'device':
                    raise
        return EcowittNetCatchup(api_key=self.api_key, app_key=self.app_key, mac=self.mac_address,
                                 driver_debug=self.driver_debug)

    @property
    def hardware_name(self):
        model = self.collector.device.model
        if model is not None:
            log.info('Station is : %s', model)
            return model
        return DRIVER_NAME

    @property
    def mac_address(self):
        return self.collector.device.mac_address

    def closePort(self):
        self.collector.shutdown()
        self.mqtt.close()


# ---------------------------------------------------------------------------
# Configuration editor and configurator
# ---------------------------------------------------------------------------

_EXTRACTORS = {
    'sum': ('lightning_strike_count', 'lightning_noise_count', 't_rain', 'p_rain', 'hail'),
    'max': ('rainRate', 'rrain_piezo', 'p_rainrate'),
    'last': ('model', 'stationtype', 'apName', *_rng('gain{}', 5, 0), 'lightning_distance',
             'lightning_last_det_time', 'lightningcount', 'maxdailygust', 'daymaxwind', 'windspdmph_avg10m',
             'winddir_avg10m', 'stormRain', 'hourRain', 'dayRain', 'weekRain', 'monthRain', 'yearRain', 'totalRain',
             'erain_piezo', 'hrain_piezo', 'drain_piezo', 'wrain_piezo', 'mrain_piezo', 'yrain_piezo',
             'totalRain_piezo', 'p_eventrain', 'p_hourrain', 'p_dayrain', 'p_weekrain', 'p_monthrain', 'p_yearrain',
             'dayHail', 'vpd', *_rng('depth_ch{}', 4), *_rng('pm2_5{}_24hav', 4), '24havpm255',
             *_rng('pm2_5{}_24h_avg', 5), 'pm10_24h_avg', 'co2_24h_avg', 'wh25_batt', 'wh26_batt',
             *_rng('wh31_ch{}_batt', 8), *_rng('wn35_ch{}_batt', 8), 'wh40_batt', 'wn20_batt', 'wn38_batt',
             *_rng('wh41_ch{}_batt', 4), 'wh45_batt', *_rng('wh51_ch{}_batt', 16), *_rng('wh55_ch{}_batt', 4),
             'wh57_batt', 'wh65_batt', 'wh68_batt', 'wh69_batt', 'ws80_batt', 'ws85_batt', 'ws90_batt',
             'ws85cap_volt', 'ws90cap_volt', 'ws1900batt', 'console_batt', 'consoleext_batt', *_rng('ldsbatt{}', 4)),
}
_FIRSTLAST = ('model', 'stationtype', 'apName')


class EcowittHttpDriverConfEditor(weewx.drivers.AbstractConfEditor):
    t_src_fields = ('rain.0x13.val', 'rain.0x12.val', 'rain.0x11.val', 'rain.0x10.val')
    p_src_fields = tuple(f.replace('rain.', 'piezoRain.') for f in t_src_fields)

    @property
    def default_stanza(self):
        return f"""
    [{DRIVER_NAME}]
        # This section is for the weewx-EcowittGateway driver.

        # the driver to use
        driver = user.{DRIVER_MODULE}

        # how often to poll the device
        poll_interval = {DEFAULT_POLL_INTERVAL:d}
        # how many attempts to contact the device before giving up
        max_tries = {DEFAULT_MAX_TRIES:d}
        # wait time in seconds between retries to contact the device
        retry_wait = {DEFAULT_RETRY_WAIT:d}
        # max wait for device to respond to a HTTP request
        url_timeout = {DEFAULT_URL_TIMEOUT:d}

        # whether to show all battery state data including nonsense data and
        # sensors that are disabled sensors and connecting
        show_all_batt = False

        # whether to always log unknown API fields, unknown fields are always
        # logged at the debug level, this will log them at the info level
        log_unknown_fields = False

        # How often to check for device firmware updates, 0 disables firmware
        # update checks. Available firmware updates are logged.
        firmware_update_check_interval = {DEFAULT_FW_CHECK_INTERVAL:d}
"""

    def prompt_for_settings(self):
        print()
        ip_address = weecfg.prompt_with_options('Specify the device IP address, for example: 192.168.1.100.',
                                                self.existing_options.get('ip_address'))
        print()
        poll_interval = int(weecfg.prompt_with_options('Specify how often to poll the device in seconds.',
                                                       self.existing_options.get('poll_interval',
                                                                                 DEFAULT_POLL_INTERVAL)))
        return {'ip_address': ip_address, 'poll_interval': poll_interval}

    def modify_config(self, config_dict):
        self.do_loop_on_init(config_dict)
        self.do_rain(config_dict)
        self.do_archive_record_generation(config_dict)
        self.do_extractors(config_dict)

    @staticmethod
    def _wrap(text):
        return '\n'.join(textwrap.wrap(text, 80, break_long_words=False))

    @staticmethod
    def _merge(config_dict, text):
        config_dict.merge(configobj.ConfigObj(io.StringIO(textwrap.dedent(text))))

    @staticmethod
    def do_loop_on_init(config_dict):
        print()
        prompt = ("The Ecowitt HTTP driver requires a network connection to the device.\n"
                  "Consequently, the absence of a network connection when WeeWX starts will cause\n"
                  "WeeWX to exit. The WeeWX 'loop_on_init' setting can be used to mitigate such\n"
                  "problems by having WeeWX retry startup indefinitely. Set to '0' to attempt\n"
                  "startup once only or '1' to attempt startup indefinitely.")
        loop_on_init = int(weecfg.prompt_with_options(prompt, config_dict.get('loop_on_init', '1'), ['0', '1']))
        EcowittHttpDriverConfEditor._merge(config_dict, f'loop_on_init = {loop_on_init:d}')
        if not config_dict.comments['loop_on_init']:
            config_dict.comments['loop_on_init'] = ['', '# Whether to try indefinitely to load the driver']

    @staticmethod
    def do_rain(config_dict):
        cls = EcowittHttpDriverConfEditor
        drv_config = config_dict.get(DRIVER_NAME, {})
        mapper = HttpMapper(**drv_config)
        try:
            paired = EcowittDevice(ip_address=drv_config.get('ip_address')).paired_rain_gauges
        except (weewx.ViolatedPrecondition, DeviceIOError, ParseError) as e:
            print()
            print(f'Unable to query the device for paired rain gauges: {e}')
            paired = ()
        paired_str = 'none' if not paired else ('both' if len(paired) == 2 else paired[0])
        print()
        paired_gauges = weecfg.prompt_with_options(cls._wrap(
            "Ecowitt gateways/consoles can simultaneously support both tipping and piezoelectric (piezo) rain "
            "gauges. Select the gauge type(s) paired with this device. Set to 'none' if no gauges are paired, "
            "'tipping' if only a tipping gauge is paired, 'piezo' if only a piezo gauge is paired or 'both' if "
            "both a tipping gauge and a piezo gauge are paired."),
            paired_str, ['none', 'tipping', 'piezo', 'both']).lower()
        calc = config_dict.setdefault('StdWXCalculate', {})
        fme = drv_config.get('field_map_extensions')

        def drop_rain_rate():
            if fme is not None:
                fme.pop('rainRate', None)
                if not fme:
                    drv_config.pop('field_map_extensions')

        if paired_gauges not in ('tipping', 'piezo', 'both'):
            drop_rain_rate()
            if 'Delta' in calc:
                for field in ('rain', 'p_rain'):
                    calc['Delta'].pop(field, None)
                if not calc['Delta']:
                    calc.pop('Delta')
            return
        deltas = calc.get('Delta', {})
        curr_w_src = deltas['rain'].get('input') if 'rain' in deltas else None
        curr_e_src = mapper.field_map.get(curr_w_src) if curr_w_src is not None else None
        curr_type = ('tipping' if curr_e_src in cls.t_src_fields else
                     'piezo' if curr_e_src in cls.p_src_fields else 'none')
        possible = {'both': ['both', 'tipping', 'piezo', 'none'], 'tipping': ['tipping', 'none'],
                    'piezo': ['piezo', 'none']}[paired_gauges]
        default = curr_type if curr_type != 'none' or paired_gauges != 'both' else 'both'
        print()
        user_type = weecfg.prompt_with_options(cls._wrap(
            "Choose how the rain gauges are recorded: 'both' records the tipping gauge in 'rain'/'rainRate' and "
            "the piezo gauge in 'p_rain'/'hail'/'p_rainrate'; 'tipping' or 'piezo' feeds WeeWX 'rain'/'rainRate' "
            "from that gauge; 'none' leaves the WeeWX rain fields unpopulated."),
            default, possible).lower()
        if user_type in ('tipping', 'piezo', 'both'):
            rain_field, other = {'tipping': ('rain', 'p_rain'), 'both': ('rain', 'p_rain'),
                                 'piezo': ('p_rain', 'rain')}[user_type]
            add_back = None if curr_type == user_type else other
            cls._merge(config_dict, """
                [StdWXCalculate]
                    [[Calculations]]
                        rain = prefer_hardware""")
            config_dict.setdefault(DRIVER_NAME, {})['rain_source'] = user_type
            drop_rain_rate()
            if add_back is not None and paired_gauges == 'both':
                cls._merge(config_dict, f"""
                    [StdWXCalculate]
                        [[Calculations]]
                            {add_back} = prefer_hardware""")
            # the driver supplies per-packet rain, so WeeWX must not derive it from a running total
            delta = config_dict['StdWXCalculate'].get('Delta', {})
            for field in ('rain', rain_field):
                delta.pop(field, None)
            if 'Delta' in config_dict['StdWXCalculate'] and not delta:
                config_dict['StdWXCalculate'].pop('Delta')
        else:
            if curr_type in ('tipping', 'piezo'):
                g = curr_type[0]
                cls._merge(config_dict, f"""
                    [StdWXCalculate]
                        [[Calculations]]
                            {g}_rain = prefer_hardware
                        [[Delta]]
                            [[[{g}_rain]]]
                                input = {g}_rainyear""")
            drop_rain_rate()

    @staticmethod
    def do_archive_record_generation(config_dict):
        print()
        print('Setting record_generation to software.')
        config_dict['StdArchive']['record_generation'] = 'software'

    @staticmethod
    def do_extractors(config_dict):
        print()
        print('Setting accumulator extractor functions.')
        accum = {f: {'extractor': x} for x, fields in _EXTRACTORS.items() for f in fields}
        for f in _FIRSTLAST:
            accum[f] = {'accumulator': 'firstlast', 'extractor': 'last'}
        accum_config = configobj.ConfigObj({'Accumulator': accum})
        accum_config.merge(config_dict)
        config_dict.merge(accum_config)


class EcowittHttpDriverConfigurator(weewx.drivers.AbstractConfigurator):

    @property
    def description(self):
        return 'Read data and configuration from an Ecowitt device.'

    @property
    def usage(self):
        common = '[CONFIG_FILE|--config=CONFIG_FILE]\n            [--ip-address=IP_ADDRESS]\n            '
        return (f'{BOLD}%prog --help\n'
                f'       %prog --live-data\n            {common}[--units=us|metric|metricwx]\n'
                f'            [--show-all-batt]\n            [--debug=0|1|2|3]\n'
                f'       %prog --sensors\n            {common}[--show-all-batt]\n            [--debug=0|1|2|3]\n'
                f'       %prog --list-sensors\n            {common}[--no-sensor-map]\n            [--debug=0|1|2|3]\n'
                f'       %prog --dump-api\n            {common}[--output=FILE] [--unmask]\n'
                f'       %prog --firmware|--mac-address|--system-params|\n'
                f'            --get-rain-data|--get-all-rain_data\n            {common}[--debug=0|1|2|3]\n'
                f'       %prog --get-calibration|--get-mulch-th-cal|\n'
                f'            --get-mulch-soil-cal|--get-pm25-cal|\n'
                f'            --get-co2-cal|--get-lds-cal|\n            {common}[--debug=0|1|2|3]\n'
                f'       %prog --get-services\n            {common}[--unmask] [--debug=0|1|2|3]{ENDC}')

    @property
    def epilog(self):
        return ''

    def add_options(self, parser):
        for opt, dest, text in (
                ('--live-data', 'live', 'display device live sensor data'),
                ('--sensors', 'sensors', 'display device sensor information'),
                ('--list-sensors', 'list_sensors',
                 'list multi-channel sensors by hardware ID with their channels and WeeWX fields'),
                ('--dump-api', 'dump_api', 'save every raw API response as JSON (passwords masked)'),
                ('--no-sensor-map', 'no_sensor_map', 'ignore [[sensor_map]] and show gateway channels'),
                ('--firmware', 'firmware', 'display device firmware information'),
                ('--mac-address', 'mac', 'display device station MAC address'),
                ('--system-params', 'sys_params', 'display device system parameters'),
                ('--get-rain-data', 'get_rain', 'display device traditional rain data only'),
                ('--get-all-rain-data', 'get_rain_totals',
                 'display device traditional, piezo and rain reset time data'),
                ('--get-calibration', 'calibration', 'display device calibration data'),
                ('--get-mulch-th-cal', 'mulch_offset',
                 'display device multi-channel temperature and humidity calibration data'),
                ('--get-mulch-soil-cal', 'soil_calibration', 'display device soil moisture calibration data'),
                ('--get-mulch-t-cal', 'get_temp_calibration', 'display device temperature (WN34) calibration data'),
                ('--get-pm25-cal', 'pm25_offset', 'display device PM2.5 calibration data'),
                ('--get-co2-cal', 'co2_offset', 'display device CO2 (WH45) calibration data'),
                ('--get-lds-cal', 'lds_offset', 'display device LDS (WH54) calibration data'),
                ('--get-services', 'services', 'display device weather services configuration data'),
                ('--show-all-batt', 'show_battery', 'show all available battery state data regardless of sensor state'),
                ('--unmask', 'unmask', 'unmask sensitive settings')):
            parser.add_option(opt, dest=dest, action='store_true', help=text)
        parser.add_option('--ip-address', dest='ip_address', help='device IP address to use')
        parser.add_option('--output', dest='output', metavar='FILE', help='file for --dump-api output')
        for opt, dest, text in (('--max-tries', 'max_tries', 'max number of attempts to contact the device'),
                                ('--retry-wait', 'retry_wait',
                                 'how long to wait between attempts to contact the device'),
                                ('--timeout', 'timeout', 'how long to wait for a device to respond to a HTTP request'),
                                ('--debug', 'debug', 'how much status to display, 0-3')):
            parser.add_option(opt, dest=dest, type=int, help=text)
        parser.add_option('--units', dest='units', metavar='UNITS', default='metric',
                          help='unit system to use when displaying live data')
        parser.add_option('--config', dest='config_path', metavar='CONFIG_FILE',
                          help='use configuration file CONFIG_FILE.')
        parser.add_option('--yes', '-y', dest='noprompt', action='store_true', help='answer yes to every prompt')

    def do_options(self, options, parser, config_dict, prompt):
        _debug = getattr(options, 'driver_debug', None)
        if _debug is None:
            _debug = getattr(options, 'debug', None)
        weewx.debug = weeutil.weeutil.to_int(_debug if _debug is not None else config_dict.get('debug', 0))
        if weewx.debug > 0:
            print(f"debug level is '{weewx.debug:d}'")
        weeutil.logger.setup('weewx', config_dict)
        define_units()
        DirectEcowittDevice(options, parser, driver_config(config_dict),
                            html_dir=html_root(config_dict)).process_options()


# ---------------------------------------------------------------------------
# Catchup (history) sources
# ---------------------------------------------------------------------------

class EcowittNetCatchup:
    """Obtains history records from Ecowitt.net (API v3)."""

    endpoint = 'https://api.ecowitt.net/api/v3/device'
    commands = ('real_time', 'history', 'list', 'info')
    api_result_codes = {
        -1: 'System is busy', 0: 'success result', 40000: 'Illegal parameter',
        40010: 'Illegal Application_Key Parameter', 40011: 'Illegal Api_Key Parameter',
        40012: 'Illegal MAC/IMEI Parameter', 40013: 'Illegal start_date Parameter',
        40014: 'Illegal end_date Parameter', 40015: 'Illegal cycle_type Parameter',
        40016: 'Illegal call_back Parameter', 40017: 'Missing Application_Key Parameter',
        40018: 'Missing Api_Key Parameter', 40019: 'Missing MAC Parameter', 40020: 'Missing start_date Parameter',
        40021: 'Missing end_date Parameter', 40022: 'Illegal Voucher type', 43001: 'Needs other service support',
        44001: 'Media file or data packet is null', 45001: 'Over the limit or other error',
        46001: 'No existing request', 47001: 'Parse JSON/XML contents error', 48001: 'Privilege Problem'}
    _rain_obs = (('rain_rate', '0x0E'), ('event', '0x0D'), ('hourly', '0x7D'), ('daily', '0x10'),
                 ('24_hours', '0x7C'), ('weekly', '0x11'), ('monthly', '0x12'), ('yearly', '0x13'))
    net_to_driver_map = {
        'outdoor': {'temperature': 'common_list.0x02.val', 'humidity': 'common_list.0x07.val'},
        'indoor': {'temperature': 'wh25.intemp', 'humidity': 'wh25.inhumi'},
        'solar_and_uvi': {'solar': 'common_list.0x15.val', 'uvi': 'common_list.0x17.val'},
        'rainfall': {k: f'rain.{v}.val' for k, v in _rain_obs},
        'rainfall_piezo': {k: f'piezoRain.{v}.val' for k, v in _rain_obs},
        'wind': {'wind_speed': 'common_list.0x0B.val', 'wind_gust': 'common_list.0x0C.val',
                 'wind_direction': 'common_list.0x0A.val',
                 '10_minute_average_wind_direction': 'common_list.0x6D.val'},
        'pressure': {'absolute': 'wh25.abs', 'relative': 'wh25.rel'},
        'lightning': {'distance': 'lightning.distance', 'count': 'lightning.count'},
        'indoor_co2': {'co2': 'wh25.CO2', '24_hours_average': 'wh25.CO2_24H'},
        'co2_aqi_combo': {'co2': 'co2.CO2', '24_hours_average': 'co2.CO2_24H'},
        **{f'pm{p}_aqi_combo': {f'pm{p}': f'co2.PM{p}', 'real_time_aqi': f'co2.PM{p}_RealAQI',
                                '24_hours_aqi': f'co2.PM{p}_24HAQI'} for p in ('25', '10', '1', '4')},
        **{f'pm25_ch{i}': {'pm25': f'ch_pm25.{i}.PM25'} for i in range(1, 5)},
        't_rh_aqi_combo': {'temperature': 'co2.temp', 'humidity': 'co2.humidity'},
        **{f'temp_and_humidity_ch{i}': {'temperature': f'ch_aisle.{i}.temp', 'humidity': f'ch_aisle.{i}.humidity'}
           for i in range(1, 9)},
        **{f'soil_ch{i}': {'soilmoisture': f'ch_soil.{i}.humidity', 'ad': f'ch_soil{i}nowAd'} for i in range(1, 17)},
        **{f'temp_ch{i}': {'temperature': f'ch_temp.{i}.temp'} for i in range(1, 9)},
        **{f'leaf_ch{i}': {'leaf_wetness': f'ch_leaf.{i}.humidity'} for i in range(1, 9)},
        'battery': {
            'ws1900_console': 'wh25.ws1900_batt', 'ws1800_console': 'wh25.ws1800_batt',
            'ws6006_console': 'console.console_ext_volt', 'console': 'console.console_ext_volt',
            'wind_sensor': 'common_list.0x0A.voltage', 'haptic_array_battery': 'piezoRain.0x13.voltage',
            'haptic_array_capacitor': 'piezocap_volt', 'sonic_array': 'ws80.battery',
            'rainfall_sensor': 'wh40.voltage',
            **_chmap((('soilmoisture_sensor_ch{}', 'ch_soil.{}.voltage'),), 16),
            **_chmap((('temperature_sensor_ch{}', 'ch_temp.{}.voltage'),
                      ('leaf_wetness_sensor_ch{}', 'ch_leaf.{}.voltage')), 8),
            **_chmap((('ldsbatt_{}', 'ch_lds.{}.voltage'),), 4),
            'bgt_sensor': 'common_list.0xA1.voltage',
            **_chmap((('soilmoisture_ec_sensor_ch{}', 'ch_soil.{}.voltage'),), 16)},
        **{f'ch_lds{i}': {f'air_ch{i}': f'ch_lds.{i}.air', f'depth_ch{i}': f'ch_lds.{i}.depth',
                          f'lds_heat_ch{i}': f'ch_lds.{i}.total_heat'} for i in range(1, 5)},
        'black_globe_temperature': {'bgt': 'common_list.0xA1.val', 'wbgt': 'common_list.0xA2.val'},
        **{f'ch_soil_ec_temp_hum{i}': {'soilmoisture': f'ch_soil.{i}.humidity', 'ad': f'ch_soil{i}nowAd',
                                       'temperature': f'ch_ec.{i}.temp', 'ec': f'ch_ec.{i}.ec',
                                       'ec_ad': f'ch_ec{i}nowAd'} for i in range(1, 17)},
    }
    default_call_back = tuple(net_to_driver_map)

    def __init__(self, **options):
        self.api_key, self.app_key, self.mac = options.get('api_key'), options.get('app_key'), options.get('mac')
        for value, name in ((self.api_key, 'API key'), (self.app_key, 'Application key'),
                            (self.mac, 'Device MAC address')):
            if value is None:
                log.info('%s not specified', name)
        if self.mac is not None:
            log.info('EcowittNetCatchup using MAC: %s', self.mac)
        if None in (self.api_key, self.app_key, self.mac):
            log.info('Missing Data for Ecowitt.net - so do not try to get data')

    name = 'Ecowitt.net Catchup'
    source = 'Ecowitt.net'

    def do_debug_logging(self):
        log.info('EcowittNetCatchup: API key: %s Application key: %s', obfuscate(self.api_key), obfuscate(self.app_key))

    def gen_history_records(self, start_ts=None, stop_ts=None, **kwargs):
        if None in (self.api_key, self.app_key, self.mac):
            return
        start_90_dt = datetime.datetime.combine(datetime.date.today() - datetime.timedelta(days=90),
                                                datetime.time())
        start_ts = max(start_ts or 0, time.mktime(start_90_dt.timetuple()))
        stop_ts = int(time.time()) if stop_ts is None else stop_ts
        call_back = ','.join(kwargs.get('call_back', self.default_call_back))
        fmt = '%Y-%m-%d %H:%M:%S'
        for span in weeutil.weeutil.genDaySpans(start_ts, stop_ts):
            data = {'application_key': self.app_key, 'api_key': self.api_key, 'mac': self.mac,
                    'start_date': datetime.datetime.fromtimestamp(span.start + 1).strftime(fmt),
                    'end_date': datetime.datetime.fromtimestamp(span.stop).strftime(fmt),
                    'call_back': call_back, 'cycle_type': '5min', 'temp_unitid': 1, 'pressure_unitid': 3,
                    'wind_speed_unitid': 6, 'rainfall_unitid': 12, 'solar_irradiance_unitid': 16}
            day_data = self.parse_history(self.request('history', data=data).get('data', {}))
            for ts in sorted(day_data):
                if start_ts <= ts <= stop_ts:
                    yield {'datetime': ts, 'interval': 5, **day_data[ts]}

    def request(self, command_str, data=None, headers=None, max_tries=3):
        if command_str not in self.commands:
            return None
        url = f"{self.endpoint}/{command_str}?{urllib.parse.urlencode(data or {})}"
        if weewx.debug >= 2:
            log.info('url: %s', url)
        req = urllib.request.Request(url=url, headers=headers or {})
        for attempt in range(1, max_tries + 1):
            try:
                with urllib.request.urlopen(req) as w:
                    response = w.read().decode(w.headers.get_content_charset() or 'utf-8')
                return self.check_response(response)
            except (socket.timeout, urllib.error.URLError, InvalidApiResponseError, ApiResponseError) as e:
                log.error('Failed to obtain valid data from Ecowitt.net on attempt %d', attempt)
                log.error('   **** %s', e)
                if attempt == max_tries:
                    if isinstance(e, ApiResponseError):
                        raise InvalidApiResponseError(e) from e
                    raise

    def check_response(self, response):
        if not response:
            raise InvalidApiResponseError('Invalid API response received')
        try:
            json_resp = json.loads(response)
        except json.JSONDecodeError as e:
            raise InvalidApiResponseError(e)
        code = json_resp.get('code', 'no code')
        if code != 0:
            raise ApiResponseError(f"Received API response error code '{code}': {self.api_result_codes.get(code)}")
        return json_resp

    def parse_history(self, history_data):
        if not history_data:
            log.info('No history data to parse')
            return {}
        result = collections.defaultdict(dict)
        for set_name, set_data in history_data.items():
            for obs, obs_data in set_data.items():
                field = self.net_to_driver_map.get(set_name, {}).get(obs)
                if field is not None:
                    for ts, value in self.parse_float(obs_data).items():
                        result[ts][field] = value
        return dict(result)

    @staticmethod
    def parse_float(data):
        result = {}
        for ts_string, value_str in data['list'].items():
            try:
                ts = int(ts_string)
            except ValueError:
                continue
            result[ts] = _try(float, value_str)
        return result


class EcowittDeviceCatchup:
    """Obtains history records from the device SD card."""

    unit_groups_by_field = {
        'group_temperature': ('wh25.intemp', 'common_list.0x02.val', 'common_list.0x03.val', 'feelslike',
                              *_rng('ch_aisle.{}.temp', 8), *_rng('dewpoint{}', 8), *_rng('heatindex{}', 8),
                              *_rng('ch_temp.{}.temp', 8), 'co2.temperature', 'co2.temp', *_rng('ch_ec.{}.temp', 16),
                              'common_list.0xA1.val', 'common_list.0xA2.val'),
        'group_speed': ('common_list.0x0B.val', 'common_list.0x0C.val'),
        'group_pressure': ('wh25.abs', 'wh25.rel', 'common_list.5.val'),
        'group_rain': ('rain.0x0D.val', 'rain.0x10.val', 'rain.0x11.val', 'rain.0x12.val', 'rain.0x7D.val',
                       'piezoRain.0x7D.val', 'rain.0x13.val', 'rain.0x14.val', 'rain.0x0E.val', 'piezoRain.0x0D.val',
                       'piezoRain.0x10.val', 'piezoRain.0x11.val', 'piezoRain.0x12.val', 'piezoRain.0x13.val',
                       'piezoRain.0x14.val', 'piezoRain.0x0F.val'),
        'group_rainrate': ('rain.0x0E.val', 'piezoRain.0x0E.val'),
        'group_radiation': ('common_list.0x15.val',),
        'group_distance': ('lightning.distance',),
        'group_depth': tuple(f'ch_lds.{i}.{k}' for k in ('air', 'depth', 'total_height') for i in range(1, 5)),
    }
    fixed_units = {
        'group_altitude': 'meter', 'group_amp': 'amp', 'group_angle': 'degree_angle', 'group_boolean': 'boolean',
        'group_concentration': 'microgram_per_meter_cubed', 'group_count': 'count', 'group_data': 'byte',
        'group_db': 'dB', 'group_degree_day': 'degree_C_day', 'group_deltatime': 'second',
        'group_direction': 'degree_compass', 'group_elapsed': 'second', 'group_energy': 'watt_hour',
        'group_energy2': 'watt_second', 'group_fraction': 'ppm', 'group_frequency': 'hertz',
        'group_interval': 'minute', 'group_length': 'cm', 'group_moisture': 'centibar', 'group_percent': 'percent',
        'group_power': 'watt', 'group_pressurerate': 'mbar_per_hour', 'group_time': 'unix_epoch',
        'group_uv': 'uv_index', 'group_volt': 'volt', 'group_volume': 'liter',
        'group_usiecm': 'micro_siemens_per_centimeter', 'group_ntu': 'nephelometric_turbidity_unit'}
    # (column keyword, unit group checked, ((unit marker, {group: unit}), ...))
    _unit_rules = (
        ('temperature', 'group_temperature', (('c)', {'group_temperature': 'degree_C'}),
                                              ('℃', {'group_temperature': 'degree_C'}),
                                              ('f)', {'group_temperature': 'degree_F'}),
                                              ('℉', {'group_temperature': 'degree_F'}))),
        ('pressure', 'group_pressure', (('(hpa)', {'group_pressure': 'hPa'}), ('(inhg)', {'group_pressure': 'inHg'}),
                                        ('(mmhg)', {'group_pressure': 'mmHg'}))),
        ('gust', 'group_speed', tuple((m, {'group_speed': u, 'group_speed2': u}) for m, u in (
            ('(km/h)', 'km_per_hour'), ('(mph)', 'mile_per_hour'), ('(m/s)', 'meter_per_second'),
            ('(knots)', 'knot'), ('(nhg)', 'knot')))),
        ('rain', 'group_rain', (('(mm)', {'group_rain': 'mm', 'group_rainrate': 'mm_per_hour'}),
                                ('(in)', {'group_rain': 'inch', 'group_rainrate': 'inch_per_hour'}))),
        ('solar rad', 'group_radiation', (('(w/m2)', {'group_radiation': 'watt_per_meter_squared'}),
                                          ('(klux)', {'group_illuminance': 'kilolux'}),
                                          ('(kfc)', {'group_illuminance': 'kfc'}))),
        ('distance', 'group_distance', (('(km)', {'group_distance': 'km'}), ('(mi)', {'group_distance': 'mile'}),
                                        ('(nmi)', {'group_distance': 'nautical_mile'}))),
        ('lds', 'group_depth', (('(mm)', {'group_depth': 'mm2'}), ('cm)', {'group_depth': 'cm2'}),
                                ('in)', {'group_depth': 'inch2'}), ('(ft)', {'group_depth': 'foot2'}))),
    )

    def __init__(self, **options):
        if 'ip_address' not in options:
            raise CatchupObjectError('Device IP address not found.')
        self.ip_address = options['ip_address']
        to_int = weeutil.weeutil.to_int
        self.url_timeout = to_int(options.get('url_timeout', DEFAULT_URL_TIMEOUT))
        self.device = EcowittDevice(ip_address=self.ip_address, url_timeout=self.url_timeout)
        self.unit_system = to_int(options.get('unit_system', DEFAULT_UNIT_SYSTEM))
        self.catchup_grace = to_int(options.get('catchup_grace', DEFAULT_CATCHUP_GRACE))
        self.max_catchup_retries = to_int(options.get('catchup_retries', DEFAULT_CATCHUP_RETRIES))
        self.driver_debug = options.get('driver_debug') or DebugOptions()
        self.sdmmc_info()
        self.mapper = SdMapper(driver_debug=self.driver_debug)

    def sdmmc_info(self):
        try:
            return self.device.get_sdmmc_info_data()
        except (DeviceIOError, ParseError):
            raise CatchupObjectError(f"{self.device.model} at '{self.ip_address}' "
                                     f"does not appear to support HTTP API based catchup")

    def gen_history_records(self, start_ts=None):
        sdmmc_info = self.sdmmc_info()
        files = self.get_file_list(sdmmc_info, start_ts)
        try:
            interval = int(sdmmc_info['info']['Interval'])
        except (KeyError, ValueError, TypeError) as e:
            raise CatchupObjectError(f'Unable to determine history file record interval: {e}')
        model = self.device.model
        for ym in sorted(files):
            recs = collections.defaultdict(dict)
            for file in files[ym]:
                if file == 'log':
                    continue
                if weewx.debug >= 2 or self.driver_debug.archive:
                    log.info("Processing history file '%s' from %s at %s", file, model, self.ip_address)
                try:
                    lines = [line.decode('utf-8') for line in self.get_file(file).readlines()]
                except DeviceIOError:
                    log.error("Unable to download history file '%s' from %s at %s", file, model, self.ip_address)
                    continue
                except UnicodeDecodeError:
                    log.error("Unable to decode file '%s' from %s at %s", file, model, self.ip_address)
                    continue
                try:
                    file_recs = self.process_raw_csv_data(csv.DictReader(self.clean_data(lines)), start_ts, interval)
                except csv.Error:
                    log.error("Unable to parse CSV file '%s' from %s at %s", file, model, self.ip_address)
                    continue
                for rec in file_recs:
                    recs[rec['datetime']].update(rec)
            yield from sorted(recs.values(), key=itemgetter('datetime'))

    def clean_data(self, raw_data):
        clean = []
        for row in raw_data:
            if '\x00' in row:
                row = row.replace('\x00', '')
                if weewx.debug >= 2 or self.driver_debug.catchup:
                    log.info('One or more null bytes found in and removed')
            if row != '\n':
                clean.append(row)
        return clean

    def process_raw_csv_data(self, data_reader, start_ts, interval):
        result, units = [], {}
        for row in data_reader:
            try:
                ts = datetime.datetime.strptime(row['Time'], '%Y-%m-%d %H:%M').timestamp()
            except ValueError:
                continue
            if start_ts is None or ts > start_ts + self.catchup_grace:
                units = units or self.get_units(row.keys())
                rec = self.convert_history_rec(self.mapper.map_data(rec=row), units)
                rec.update(datetime=ts, interval=interval)
                result.append(rec)
        return result

    @classmethod
    def get_units(cls, keys):
        units = dict(cls.fixed_units)
        for key in (k.lower() for k in keys):
            for keyword, group, markers in cls._unit_rules:
                if keyword in key and group not in units:
                    for marker, found in markers:
                        if marker in key:
                            units.update(found)
                            break
                    break
        return units

    def convert_history_rec(self, rec, units):
        converted = dict(rec)
        dbg = weewx.debug >= 3 or self.driver_debug.catchup
        for group, fields in self.unit_groups_by_field.items():
            for field in fields:
                if field not in rec:
                    continue
                try:
                    value = rec[field] * 10 if field == 'common_list.5.val' and units.get(group) == 'hPa' \
                        else rec[field]
                    converted[field] = weewx.units.convertStd(weewx.units.ValueTuple(value, units.get(group), group),
                                                              self.unit_system).value
                except Exception as e:
                    if dbg:
                        log.info("Could not convert field '%s' with unit group '%s': %s", field, group, e)
        return converted

    def get_file(self, file_name):
        url = f'http://{self.ip_address}:81/{file_name}'
        for attempt in range(1, self.max_catchup_retries + 1):
            try:
                return urllib.request.urlopen(url, timeout=self.url_timeout)
            except urllib.error.URLError as e:
                if attempt < self.max_catchup_retries:
                    log.debug("Failed to obtain file '%s' after %d attempts: %s", file_name, attempt, e)
                    time.sleep(1)
                else:
                    msg = f"Failed to obtain file '{file_name}' after {attempt} attempts: {e}"
                    log.error(msg)
                    raise DeviceIOError(msg)
        raise DeviceIOError(f"Failed to obtain file '{file_name}'")

    @staticmethod
    def get_file_list(sd_info, start_ts=None):
        if start_ts is not None:
            tt = datetime.datetime.fromtimestamp(start_ts).timetuple()
            ts_index = tt.tm_year * 100 + tt.tm_mon
        else:
            ts_index = 0
        indexed = {}
        for f in sd_info['file_list']:
            if f['type'] in ('file', '1'):
                index = int(f['name'][0:4]) * 100 + int(f['name'][4:6])
                if index >= ts_index:
                    indexed.setdefault(index, []).append(f['name'])
        return indexed


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

class EcowittHttpCollector:
    """Polls the device in a thread and places the data on a queue."""

    def __init__(self, ip_address, poll_interval=DEFAULT_POLL_INTERVAL, max_tries=DEFAULT_MAX_TRIES,
                 retry_wait=DEFAULT_RETRY_WAIT, url_timeout=DEFAULT_URL_TIMEOUT, unit_system=DEFAULT_UNIT_SYSTEM,
                 show_battery=DEFAULT_FILTER_BATTERY, log_unknown_fields=False, get_soilad=DEFAULT_GET_SOILAD,
                 fw_update_check_interval=DEFAULT_FW_CHECK_INTERVAL, sensor_map=None, debug=None):
        self.queue = queue.Queue()
        self.poll_interval = poll_interval
        self.debug = debug or DebugOptions()
        self.fw_update_check_interval = fw_update_check_interval
        self.get_soilad = get_soilad
        if fw_update_check_interval > 0:
            log.info('     device firmware update checks will occur every %d seconds', fw_update_check_interval)
            log.info('     available device firmware updates will be logged')
        else:
            log.info('     device firmware update checks will not occur')
        log.info('     battery state will %s', 'be reported for all sensors' if show_battery
                 else 'not be reported for sensors with no signal data')
        log.info('     unknown fields will be %s', 'reported' if log_unknown_fields else 'ignored')
        log.info('     Soil Ad values are %sfetched', '' if get_soilad else 'not ')
        self.sensor_mapper = SensorMapper(sensor_map)
        if self.sensor_mapper:
            log.info('     %d sensor(s) reported on fixed channels by hardware ID (sensor_map)',
                     len(self.sensor_mapper.targets))
        self.device = EcowittDevice(ip_address=ip_address, unit_system=unit_system, max_tries=max_tries,
                                    retry_wait=retry_wait, url_timeout=url_timeout, show_battery=show_battery,
                                    get_soilad=get_soilad, log_unknown_fields=log_unknown_fields, debug=self.debug)
        self.log_failures = True
        self.thread = None
        self.collect_data = False

    def collect(self):
        last_poll = last_fw_check = 0
        while self.collect_data:
            now = time.time()
            if now - last_poll > self.poll_interval:
                if self.debug.collector:
                    log.info("Polling the device, time '%d' last poll '%d' elapsed '%d'",
                             now, last_poll, now - last_poll)
                try:
                    data = self.get_current_data()
                except DeviceIOError as e:
                    if self.log_failures:
                        log.error('Unable to obtain live sensor data')
                    data = e
                if self.debug.collector:
                    log.info('Collected data: %s', data)
                self.queue.put(data)
                if weewx.debug:
                    log.info('Next update in %d seconds', self.poll_interval)
                last_poll = now
                if 0 < self.fw_update_check_interval < now - last_fw_check:
                    self.check_firmware()
                    last_fw_check = now
            time.sleep(1)

    def check_firmware(self):
        if self.debug.collector:
            log.info('Performing firmware update check')
        try:
            if self.device.firmware_update_avail:
                log.info('A firmware update is available, current %s firmware version is %s',
                         self.device.model, self.device.firmware_version)
                log.info('    update at http://%s or via the WSView Plus app', self.device.ip_address)
                msg = self.device.firmware_update_message
                if msg is not None:
                    log.info("    firmware update message: '%s'", msg)
                else:
                    log.info('    no firmware update message found')
        except DeviceIOError as e:
            log.error('Firmware update check failed: %s', e)
        if self.debug.collector:
            log.info('Firmware update check complete')

    def get_current_data(self):
        timestamp = int(time.time())
        dev = self.device
        data = dev.get_live_data()
        for part in (dev.get_rain_totalspart(), dev.get_piezo_rain_datapart(), dev.get_device_info_datapart(),
                     dev.get_stationtype()):
            data.update(part)
        if self.get_soilad and any(f'ch_soil.{ch}.humidity' in data for ch in range(1, 17)):
            data.update(dev.get_soil_adnow_data())
        if 'WS6210' in (data.get('apName') or '') and data.get('debug.usr_interval') is not None:
            data['debug.usr_interval'] *= 60
        data['datetime'] = timestamp
        data.update(dev.get_sensors_data())
        data = self.sensor_mapper.apply(data)
        if weewx.debug >= 3:
            log.debug('Current data: %s', data)
        return data

    def startup(self):
        try:
            self.thread = threading.Thread(target=self._run, name='EcowittHttpCollectorThread', daemon=True)
            log.info('EcowittHttpCollector startup')
            self.collect_data = True
            self.thread.start()
        except threading.ThreadError:
            log.error('Unable to launch EcowittHttpCollector thread')
            self.thread = None

    def _run(self):
        try:
            self.collect()
        except Exception:
            weeutil.logger.log_traceback(log.critical, '    ****  ')

    def shutdown(self):
        if self.thread:
            self.collect_data = False
            self.thread.join(10.0)
            if self.thread.is_alive():
                log.error('Unable to shut down EcowittHttpCollector thread')
            else:
                log.info('EcowittHttpCollector thread has been terminated')
        self.thread = None


# ---------------------------------------------------------------------------
# Device HTTP API
# ---------------------------------------------------------------------------

class EcowittHttpApi:
    """Thin client for the Ecowitt local HTTP API."""

    commands = ('get_version', 'get_livedata_info', 'get_ws_settings', 'get_calibration_data', 'get_rain_totals',
                'get_device_info', 'get_sensors_info', 'get_network_info', 'get_units_info', 'get_cli_soilad',
                'get_cli_multiCh', 'get_cli_pm25', 'get_cli_co2', 'get_piezo_rain', 'get_cli_wh34', 'get_cli_lds',
                'get_sdmmc_info')
    sensor_rename_map = {'wh30': 'wn30', 'wh31': 'wn31', 'wh32': 'wn32', 'wh34': 'wn34', 'wh35': 'wn35',
                         'wh36': 'wn36', 'wh80': 'ws80', 'wh85': 'ws85', 'wh90': 'ws90'}

    def __init__(self, ip_address, max_tries=DEFAULT_MAX_TRIES, retry_wait=DEFAULT_RETRY_WAIT,
                 timeout=DEFAULT_URL_TIMEOUT):
        self.ip_address = ip_address
        self.max_tries = max_tries or DEFAULT_MAX_TRIES
        self.retry_wait = DEFAULT_RETRY_WAIT if retry_wait is None else retry_wait
        self.timeout = timeout or DEFAULT_URL_TIMEOUT

    def request(self, command_str, data=None, headers=None, rename=True):
        """Send an API command and return the deserialised JSON (None if undecodable)."""
        if command_str not in self.commands:
            raise UnknownApiCommand(f"Unknown HTTP API command '{command_str}'")
        url = f'http://{self.ip_address}/{command_str}?{urllib.parse.urlencode(data or {})}'
        req = urllib.request.Request(url=url, headers=headers or {})
        for attempt in range(1, self.max_tries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as w:
                    resp = w.read().decode(w.headers.get_content_charset() or 'utf-8')
                break
            except socket.timeout:
                if weewx.debug >= 2:
                    log.debug('Socket - Failed to get device data on attempt %d of %d', attempt, self.max_tries)
                if attempt == self.max_tries:
                    raise
                time.sleep(self.retry_wait)
            except OSError as e:
                log.error('URL - Failed to get device data on attempt %d of %d', attempt, self.max_tries)
                log.error('   **** %s', e)
                raise
        for old, new in self.sensor_rename_map.items() if rename else ():
            resp = resp.replace(old, new)
        try:
            resp_json = json.loads(resp)
        except json.JSONDecodeError as e:
            log.error('Cannot deserialize device response')
            log.error('   **** %s', e)
            return None
        if weewx.debug >= 3:
            log.debug('Deserialized HTTP response: %s', json.dumps(resp_json))
        return resp_json

    def call(self, command, **data):
        """request() with network errors raised as DeviceIOError."""
        try:
            return self.request(command, data=data or None)
        except OSError as e:
            raise DeviceIOError(f"Failed to obtain '{command}' data: {e}") from e

    def get_sensors_info(self):
        """Return all pages of sensor info concatenated."""
        pages = [self.call('get_sensors_info', page=n) for n in (1, 2, 3)]
        while len(pages) < 5 and pages[-1] is not None:
            pages.append(self.call('get_sensors_info', page=len(pages) + 1))
        if pages[0] is None:
            return pages[1]
        result = []
        for page in pages:
            if page is None:
                break
            result += page
        return result


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_SKIP = object()
_BLANK = weewx.units.ValueTuple(None, None, None)


def _try(fn, value, default=None):
    """Return fn(value) or default if the conversion fails."""
    try:
        return fn(value)
    except (TypeError, ValueError):
        return default


def _copy(dest, src, spec, skip_bad=False):
    """For each (dest_key, src_key, fn) with src_key in src set dest[dest_key] = fn(src[src_key]).

    fn None copies the raw value; a failed conversion stores None (or is skipped if skip_bad).
    """
    for dkey, skey, fn in spec:
        if skey in src:
            value = src[skey] if fn is None else _try(fn, src[skey], _SKIP)
            if value is _SKIP:
                if skip_bad:
                    continue
                value = None
            dest[dkey] = value
    return dest


def _same(fn, *keys):
    return tuple((k, k, fn) for k in keys)


def _to_vt(value, unit, group, fn=weeutil.weeutil.to_float):
    try:
        return weewx.units.ValueTuple(fn(value), unit, group)
    except (TypeError, ValueError):
        return _BLANK


def _pct(value):
    return float(value.split('%')[0])


class EcowittHttpParser:
    """Parses Ecowitt HTTP API responses."""

    default_device_units = {'group_volt': 'volt', 'group_uv': 'uv_index', 'group_percent': 'percent',
                            'group_direction': 'degree_compass', 'group_fraction': 'ppm',
                            'group_concentration': 'microgram_per_meter_cubed', 'group_boolean': 'boolean',
                            'group_usiecm': 'micro_siemens_per_centimeter',
                            'group_organicpollution': 'milligram_per_liter'}
    unit_lookup = {'c': 'degree_C', 'f': 'degree_F', 'km': 'km', 'mi': 'mile', 'nmi': 'nautical_mile', 'hpa': 'hPa',
                   'kfc': 'kfc', 'klux': 'klux', 'kpa': 'kPa', 'inhg': 'inHg', 'mmhg': 'mmHg', 'mm': 'mm',
                   'in': 'inch', 'ft': 'foot', 'mm/hr': 'mm_per_hour', 'in/hr': 'inch_per_hour',
                   'km/h': 'km_per_hour', 'm/s': 'meter_per_second', 'mph': 'mile_per_hour', 'knots': 'knot',
                   '%': 'percent', 'w/m2': 'watt_per_meter_squared', 'us/cm': 'micro_siemens_per_centimeter',
                   'mg/l': 'milligram_per_liter', 'ntu': 'nephelometric_turbidity_unit'}
    _T, _PRESS = ('group_temperature', 'conv'), ('group_pressure', 'conv')
    _HUM, _DIR = ('group_percent', int), ('group_direction', int)
    _SPD, _RAIN = ('group_speed', 'conv'), ('group_rain', 'conv')
    # common_list/rain/piezoRain object id -> (unit group, post-processing) or None (ignored)
    object_kinds = {
        '0x01': _T, '0x02': _T, '0x03': _T, '3': _T, '0x04': _T, '4': _T, '0x05': _T, '5': _PRESS,
        '0x07': _HUM, '0x08': None, '0x09': None, '0x0A': _DIR, '0x0B': _SPD, '0x0C': _SPD,
        '0x0D': _RAIN, '0x0E': ('group_rainrate', 'conv'), '0x0F': _RAIN, '0x10': _RAIN, '0x11': _RAIN,
        '0x12': _RAIN, '0x13': _RAIN, '0x14': _RAIN, '0x15': ('group_radiation', 'conv'), '0x16': _HUM,
        '0x17': _DIR, '0x18': None, '0x19': _SPD, '0x6D': _DIR, '0x7C': _RAIN, '0x7D': _RAIN, '0xA1': _T,
        '0xA2': _T, 'srain_piezo': ('group_boolean', int), 'srain': ('group_boolean', int)}
    rain_map = {'day_rain': 'rainDay', '24h_rain': 'rain24', 'week_rain': 'rainWeek', 'month_rain': 'rainMonth',
                'year_rain': 'rainYear', 'total_rain': 'rainTotal'}
    piezo_rain_map = {'day_rain': 'drain_piezo', '24h_rain': 'rain24_piezo', 'week_rain': 'wrain_piezo',
                      'month_rain': 'mrain_piezo', 'year_rain': 'yrain_piezo', 'total_rain': 'train_piezo'}
    not_registered = ('fffffffe', 'ffffffff')

    def __init__(self, unit_system=DEFAULT_UNIT_SYSTEM, show_battery=DEFAULT_FILTER_BATTERY,
                 log_unknown_fields=True, get_soilad=DEFAULT_GET_SOILAD, debug=None):
        self.unit_system = unit_system
        self.show_battery = show_battery
        self.log_unknown_fields = log_unknown_fields
        self.get_soilad = get_soilad
        self.debug = debug if isinstance(debug, DebugOptions) else DebugOptions()

    # -- generic helpers ---------------------------------------------------

    @staticmethod
    def _require_dict(response, name):
        try:
            return dict(response)
        except (TypeError, ValueError) as e:
            raise ParseError(f"Error parsing '{name}' data: {e}")

    def _post(self, vt, group, post):
        if post == 'vt':
            return vt
        if post == 'conv':
            return weewx.units.convert(vt, weewx.units.std_groups[self.unit_system][group]).value
        return int(vt.value) if post is int else vt.value

    def _put_obs(self, dest, src, key, group, post=None, device_units=None, bad=None):
        """Parse observation src[key] into dest[key]; return False if key is absent."""
        if key not in src:
            return False
        try:
            vt = self.parse_obs_value(key, src, group, device_units)
        except (ParseError, UnitError):
            dest[key] = bad
        else:
            dest[key] = self._post(vt, group, post)
        return True

    @staticmethod
    def _channels(response, key='channel'):
        """Yield (item, {'channel': n}) for list items with a valid channel number."""
        for item in response:
            try:
                yield item, {'channel': int(item[key])}
            except (KeyError, TypeError, ValueError):
                continue

    @staticmethod
    def _first(response, name):
        try:
            return response[0]
        except (KeyError, TypeError) as e:
            raise ParseError(f"Cannot parse '{name}' array: {e}")

    def _log_skip(self, msg, *args):
        if weewx.debug or self.debug.parser or self.log_unknown_fields:
            log.info(msg, *args)

    # -- API responses ---------------------------------------------------------

    @staticmethod
    def parse_get_version(response):
        EcowittHttpParser._require_dict(response, 'parse_get_version')
        parsed = {}
        if 'version' in response:
            version = str(response['version'])
            parsed['version'] = version
            parsed['stationtype'] = version[8:].strip(' \n\r')
            parts = version.split('_')
            parsed['firmware_version'] = parts[1].strip() if len(parts) > 1 else None
        _copy(parsed, response, _same(weeutil.weeutil.to_int, 'newVersion'))
        parsed['platform'] = response.get('platform')
        return parsed

    def parse_get_livedata_info(self, response, flatten_data=True):
        if response is None:
            raise ParseError("Error parsing 'get_livedata_info' data: No raw data")
        data = {}
        for group, group_data in response.items():
            fn = getattr(self, f'process_{group}_array', None)
            if fn is None:
                if weewx.debug or self.log_unknown_fields:
                    log.info("Skipped unknown livedata group '%s'", group)
                continue
            try:
                data[group] = fn(group_data)
            except ProcessorError as e:
                self._log_skip("Error processing livedata group '%s': %s", group, e)
        return flatten(data) if flatten_data else data

    def parse_get_sensors_info(self, response, connected_only=DEFAULT_ONLY_REGISTERED_SENSORS, flatten_data=True):
        if response is None:
            raise ParseError("Error parsing 'get_sensors_info' data: No raw data")
        parsed = {}
        for sensor in response:
            model, channel, data = self.process_sensor_array(sensor, connected_only=connected_only)
            if model is not None:
                if channel is not None:
                    parsed.setdefault(model, {})[channel] = data
                else:
                    parsed[model] = data
        return flatten(parsed) if flatten_data else parsed

    @staticmethod
    def parse_get_ws_settings(response):
        parsed = EcowittHttpParser._require_dict(response, 'parse_get_ws_settings')
        _copy(parsed, response, _same(weeutil.weeutil.to_int, 'ost_interval', 'wu_interval', 'wcl_interval',
                                      'wow_interval', 'mqtt_transport', 'mqtt_port', 'mqtt_keepalive',
                                      'mqtt_interval', 'ecowitt_port', 'ecowitt_upload', 'usr_wu_port',
                                      'usr_wu_upload'))
        for key, fn in (('Customized', lambda v: v.lower() == 'enable'), ('Protocol', lambda v: v.lower())):
            if key in response:
                try:
                    parsed[key] = fn(response[key])
                except AttributeError:
                    parsed[key] = None
        return parsed

    @staticmethod
    def parse_get_stationtype(response):
        EcowittHttpParser._require_dict(response, 'parse_get_version')
        return {'stationtype': str(response['version'])[8:].strip(' \n\r')} if 'version' in response else {}

    @staticmethod
    def parse_get_calibration_data(response, device_units):
        EcowittHttpParser._require_dict(response, 'parse_get_calibration_data')
        to_float = weeutil.weeutil.to_float
        parsed = {dest: _try(to_float, response.get(src)) for dest, src in (
            ('solar_wave', 'SolarRadWave'), ('solar_gain', 'solarRadGain'), ('uv_gain', 'uvGain'),
            ('wind_gain', 'windGain'))}
        for dest, src, group, unit in (
                ('intemp_offset', 'inTempOffset', 'group_deltat', None),
                ('inhumid_offset', 'inHumiOffset', 'group_percent', 'percent'),
                ('abs_offset', 'absOffset', 'group_pressure', None),
                ('rel_offset', 'relOffset', 'group_pressure', None),
                ('altitude', 'altitude', 'group_altitude', None),
                ('outtemp_offset', 'outTempOffset', 'group_deltat', None),
                ('outhumid_offset', 'outHumiOffset', 'group_percent', 'percent'),
                ('winddir_offset', 'windDirOffset', 'group_direction', 'degree_compass')):
            if src in response:
                vt = _to_vt(response[src], None, group)
                parsed[dest] = vt if vt is _BLANK else vt._replace(unit=unit or device_units[group])
        _copy(parsed, response, _same(weeutil.weeutil.to_bool, 'th_cli', 'wh34_cli', 'pm25_cli', 'soil_cli'))
        return parsed

    @staticmethod
    def parse_get_rain_totalspart(response, device_units=None):
        EcowittHttpParser._require_dict(response, 'get_rain_totals')
        to_int = weeutil.weeutil.to_int
        parsed = {dest: _try(fn, response.get(src)) for dest, src, fn in (
            ('rain_priority', 'rainFallPriority', to_int), ('rain_gain', 'rainGain', weeutil.weeutil.to_float),
            ('rain_reset_day', 'rstRainDay', to_int), ('rain_reset_week', 'rstRainWeek', to_int),
            ('rain_reset_year', 'rstRainYear', to_int), ('rain_piezo', 'piezo', to_int))}
        return parsed

    @staticmethod
    def parse_get_rain_totals(response, device_units):
        parsed = EcowittHttpParser.parse_get_rain_totalspart(response)
        if 'list' in response:
            parsed['rain_list'] = response['list']
            for gauge in parsed['rain_list']:
                if 'value' in gauge:
                    gauge['value'] = weeutil.weeutil.to_int(gauge['value'])
        for dest, src in EcowittHttpParser.rain_map.items():
            if src in response:
                parsed[dest] = _to_vt(response[src], device_units['group_rain'], 'group_rain')
        return parsed

    @staticmethod
    def parse_get_piezo_rainpart(response, device_unit_data=None):
        EcowittHttpParser._require_dict(response, 'get_piezo_rain')
        return _copy({}, response, tuple((f'gain{n}', f'rain{n}_gain', weeutil.weeutil.to_float)
                                         for n in range(1, 6)))

    @staticmethod
    def parse_get_piezo_rain(response, device_unit_data):
        parsed = EcowittHttpParser.parse_get_piezo_rainpart(response)
        for dest, src in EcowittHttpParser.piezo_rain_map.items():
            if src in response:
                parsed[dest] = _to_vt(response[src], device_unit_data['group_rain'], 'group_rain')
        return parsed

    @staticmethod
    def parse_get_device_infopart(response):
        return _copy({}, response, _same(weeutil.weeutil.to_int, 'radcompensation', 'upgrade', 'newVersion')
                     + (('apName', 'apName', None),))

    @staticmethod
    def parse_get_device_info(response):
        to_int = weeutil.weeutil.to_int
        parsed = _copy({}, response, (
            ('sensor_type', 'sensorType', to_int), ('rf_freq', 'rf_freq', to_int), ('afc', 'AFC', to_int),
            ('tz_auto', 'tz_auto', to_int), ('tz_name', 'tz_name', None), ('tz_index', 'tz_index', to_int),
            ('dst', 'dst_stat', to_int), ('rad_comp', 'radcompensation', to_int), ('upgrade', 'upgrade', to_int),
            ('ap_auto', 'apAuto', to_int), ('newVersion', 'newVersion', to_int), ('curr_msg', 'curr_msg', None),
            ('ap', 'apName', None), ('ap_pwd', 'APpwd', obfuscate), ('time', 'time', to_int),
            ('date', 'date', lambda v: int(time.mktime(datetime.datetime.strptime(v, '%Y-%m-%dT%H:%M').timetuple())))))
        return parsed

    @staticmethod
    def parse_get_network_info(response):
        parsed = EcowittHttpParser._require_dict(response, 'parse_get_network_info')
        wifi_pwd = parsed.pop('wifi_pwd', None)
        _copy(parsed, response, (('eth_ip_type', 'ethIpType', weeutil.weeutil.to_int),
                                 ('staIpType', 'staIpType', weeutil.weeutil.to_int)))
        if wifi_pwd is not None:
            parsed['wifi_pwd'] = obfuscate(wifi_pwd)
        return parsed

    @staticmethod
    def parse_get_units_info(response):
        parsed = EcowittHttpParser._require_dict(response, 'parse_get_units_info')
        return {k: _try(weeutil.weeutil.to_int, v) for k, v in parsed.items()}

    @staticmethod
    def parse_get_cli_soiladnow(response):
        parsed = {}
        for sensor, d in EcowittHttpParser._channels((s for s in response if isinstance(s, dict)), 'ch'):
            _copy(parsed, sensor, ((f"ch_soil{d['channel']}nowAd", 'nowAd', weeutil.weeutil.to_int),))
        return parsed

    @staticmethod
    def parse_get_cli_soilad(response):
        result = []
        for sensor, d in EcowittHttpParser._channels((s for s in response if isinstance(s, dict)), 'ch'):
            _copy(d, sensor, (('id', 'id', None), ('name', 'name', None))
                  + _same(weeutil.weeutil.to_int, 'soilVal', 'nowAd', 'minVal', 'maxVal')
                  + (('checked', 'checked', weeutil.weeutil.to_bool),))
            result.append(d)
        return sorted(result, key=itemgetter('channel'))

    def _parse_cli_list(self, response, obs, device_units):
        """Parse a calibration list; obs is ((key, unit group), ...), all required."""
        result = []
        for sensor, d in self._channels((s for s in response if isinstance(s, dict))):
            _copy(d, sensor, (('id', 'id', None), ('name', 'name', None)))
            if all(self._put_obs(d, sensor, k, g, 'vt', device_units) for k, g in obs):
                result.append(d)
        return sorted(result, key=itemgetter('channel'))

    def parse_get_cli_multich(self, response, device_units=None):
        return self._parse_cli_list(response, (('temp', 'group_deltat'), ('humi', 'group_percent')), device_units)

    def parse_get_cli_pm25(self, response, device_units=None):
        return self._parse_cli_list(response, (('val', 'group_concentration'),), device_units)

    def parse_get_cli_wh34(self, response, device_units=None):
        if device_units is None or 'group_temperature' not in device_units:
            raise ParseError('No WN34 or device temperature unit information.')
        result = self._parse_cli_list(response, (('temp', 'group_deltat'),), device_units)
        for d in result:
            d.setdefault('id', None)
            d.setdefault('name', None)
        return result

    def parse_get_cli_co2(self, response, device_units=None):
        self._require_dict(response, 'parse_get_cli_co2')
        parsed = {}
        for key in ('pm1', 'pm25', 'pm4', 'pm10'):
            self._put_obs(parsed, response, key, 'group_concentration', 'vt', device_units)
        self._put_obs(parsed, response, 'co2', 'group_fraction', 'vt', device_units)
        return _copy(parsed, response, (('id', 'id', None), ('name', 'name', None)))

    def parse_get_cli_lds(self, response):
        result = []
        for sensor, d in self._channels(response, 'ch'):
            for key in ('offset', 'total_height'):
                self._put_obs(d, sensor, key, 'group_depth', 'vt', bad=_BLANK)
            _copy(d, sensor, _same(int, 'total_heat', 'level'))
            if set(d) & {'offset', 'total_height', 'total_heat'}:
                d['id'], d['name'] = sensor.get('id'), sensor.get('name')
                result.append(d)
        return sorted(result, key=itemgetter('channel'))

    @staticmethod
    def parse_get_sdmmc_info(response):
        try:
            parsed = dict(response)
            parsed['info']['interval'] = int(response['info']['Interval'])
        except (KeyError, TypeError, ValueError) as e:
            raise ParseError(f"Error parsing 'get_sdmmc_info' data: {e}")
        return parsed

    # -- livedata groups ---------------------------------------------------

    def process_common_list_array(self, response):
        result = {}
        for item in response:
            if 'id' not in item:
                continue
            oid = item['id']
            if oid not in self.object_kinds:
                self._log_skip("Skipped unknown livedata observation ID '%s'", oid)
                continue
            kind = self.object_kinds[oid]
            if kind is None:
                result[oid] = None
                continue
            try:
                result[oid] = self._process_object(item, *kind)
            except ProcessorError as e:
                if weewx.debug >= 2 or self.debug.parser:
                    log.info("Error processing common_list ID '%s': %s", oid, e)
                result[oid] = {'val': None}
        return result

    process_rain_array = process_piezoRain_array = process_common_list_array

    def _process_object(self, item, group, post):
        obj = {'id': item['id']}
        try:
            vt = self.parse_obs_value('val', item, group)
        except (KeyError, UnitError) as e:
            raise ProcessorError(e) from e
        except ParseError:
            obj['val'] = None
        else:
            obj['val'] = self._post(vt, group, post)
        if group in ('group_rain', 'group_rainrate'):
            _copy(obj, item, _same(int, 'battery'), skip_bad=True)
        self._put_obs(obj, item, 'voltage', 'group_volt')
        if group == 'group_rain':
            for key in ('ws85cap_volt', 'ws90cap_volt'):
                self._put_obs(obj, item, key, 'group_volt')
            _copy(obj, item, _same(int, 'ws85_ver', 'ws90_ver'))
        return obj

    def process_ch_aisle_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            if any([self._put_obs(d, sensor, 'temp', 'group_temperature', 'conv'),
                    self._put_obs(d, sensor, 'humidity', 'group_percent', int)]):
                d['name'] = sensor.get('name')
                result.append(d)
        return result

    def process_ch_temp_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            if self._put_obs(d, sensor, 'temp', 'group_temperature', 'conv'):
                d['name'] = sensor.get('name')
                self._put_obs(d, sensor, 'voltage', 'group_volt')
                result.append(d)
        return result

    def process_ch_lds_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            found = [self._put_obs(d, sensor, k, 'group_depth', 'conv') for k in ('air', 'depth', 'total_height')]
            if found[0] or found[1]:
                _copy(d, sensor, _same(int, 'total_heat'), skip_bad=True)
                d['name'] = sensor.get('name')
                self._put_obs(d, sensor, 'voltage', 'group_volt')
                result.append(d)
        return result

    def process_ch_ec_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            if not (self._put_obs(d, sensor, 'humidity', 'group_percent', int)
                    and self._put_obs(d, sensor, 'temp', 'group_temperature', 'conv')):
                continue
            if self._put_obs(d, sensor, 'ec', 'group_usiecm', int) and d['ec'] == 4095:
                d['ec'] = None
            d['name'] = sensor.get('name')
            self._put_obs(d, sensor, 'voltage', 'group_volt')
            result.append(d)
        return result

    def process_ch_soil_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            if self._put_obs(d, sensor, 'humidity', 'group_percent', int):
                d['name'] = sensor.get('name')
                self._put_obs(d, sensor, 'voltage', 'group_volt')
                result.append(d)
        return result

    def process_ch_pm25_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            _copy(d, sensor, _same(float, 'PM25', 'PM25_24H') + _same(int, 'PM25_RealAQI', 'PM25_24HAQI'))
            if set(d) & {'PM25', 'PM25_RealAQI', 'PM25_24HAQI'}:
                result.append(d)
        return result

    def process_wh25_array(self, response):
        item, d = self._first(response, 'wh25'), {}
        self._put_obs(d, item, 'intemp', 'group_temperature', 'conv')
        self._put_obs(d, item, 'inhumi', 'group_percent', int)
        for key in ('abs', 'rel'):
            self._put_obs(d, item, key, 'group_pressure', 'conv')
        return _copy(d, item, _same(int, 'CO2', 'CO2_24H'))

    def process_lightning_array(self, response):
        item = self._first(response, 'lightning')
        d = _copy({}, item, _same(int, 'count'))
        self._put_obs(d, item, 'distance', 'group_distance', 'conv')
        _copy(d, item, (('date', 'date', None), ('timestamp', 'timestamp', lambda v: int(time.mktime(
            datetime.datetime.strptime(v, '%m/%d/%Y %H:%M:%S').timetuple())))))
        return d

    def process_co2_array(self, response):
        item, d = self._first(response, 'co2'), {}
        self._put_obs(d, item, 'temp', 'group_temperature', 'conv')
        self._put_obs(d, item, 'humidity', 'group_percent', int)
        _copy(d, item, _same(int, 'CO2', 'CO2_24H'))
        for pm in ('PM25', 'PM10', 'PM1', 'PM4'):
            _copy(d, item, _same(float, pm, f'{pm}_24H') + _same(int, f'{pm}_RealAQI', f'{pm}_24HAQI'))
        return d

    def process_ch_leaf_array(self, response):
        result = []
        for item in response:
            d = dict(item)
            _copy(d, item, (('humidity', 'humidity', _pct),))
            d['name'] = item.get('name')
            self._put_obs(d, item, 'voltage', 'group_volt')
            result.append(d)
        return result

    def process_ch_leak_array(self, response):
        result = []
        for item in response:
            d = dict(item)
            if 'status' in item:
                d['status'] = {'normal': 0, 'leaking': 1}.get(str(item['status']).lower())
            _copy(d, item, _same(int, 'battery'))
            self._put_obs(d, item, 'voltage', 'group_volt')
            result.append(d)
        return result

    def process_debug_array(self, response):
        item = self._first(response, 'debug')
        return _copy({}, item, _same(int, 'heap', 'runtime', 'usr_interval')
                     + _same(weeutil.weeutil.to_bool, 'is_cnip'))

    def process_console_array(self, response):
        item = self._first(response, 'console')
        d = _copy({}, item, _same(int, 'battery', 'charge_stat'))
        for key in ('console_batt_volt', 'console_ext_volt'):
            self._put_obs(d, item, key, 'group_volt')
        return d

    def process_wqt01_array(self, response):
        item, d = self._first(response, 'wqt01'), {}
        self._put_obs(d, item, 'ec', 'group_usiecm', int)
        for key in ('toc', 'cod', 'tds'):
            self._put_obs(d, item, key, 'group_organicpollution')
        _copy(d, item, _same(int, 'turb', 'CO2', 'CO2_24H', 'battery'))
        self._put_obs(d, item, 'voltage', 'group_volt')
        return d

    def process_sensor_array(self, sensor, connected_only):
        sensor_id = sensor.get('id')
        unregistered = sensor_id.lower() in self.not_registered
        if connected_only and unregistered:
            return None, None, None
        data = {'address': _try(int, sensor.get('type')), 'id': sensor_id}
        try:
            data['battery'] = (None if not self.show_battery and int(sensor.get('signal')) == 0
                               else int(sensor.get('batt')))
        except (TypeError, ValueError):
            data['battery'] = None
        data['rssi'] = None if unregistered else _try(int, sensor.get('rssi'))
        data['signal'] = None if unregistered else _try(int, sensor.get('signal'))
        data['enabled'] = _try(lambda v: int(v) == 1, sensor.get('idst'))
        if 'version' in sensor:
            data['version'] = sensor['version']
        match = re.search(r'CH\d+', sensor.get('name') or '')
        return sensor.get('img'), match.group(0).lower() if match else None, data

    # -- values and units --------------------------------------------------------

    @staticmethod
    def get_model(text):
        if text is not None:
            return next((m for m in KNOWN_DEVICES if m in text.upper()), None)
        return None

    get_model_from_firmware = get_model

    def parse_obs_value(self, key, json_object, unit_group, device_units=None):
        """Return a ValueTuple for json_object[key] (raises KeyError if absent)."""
        device_units = self.default_device_units if device_units is None else device_units
        match = re.match(r'([0-9.,+-]+)(.*)', str(json_object[key]))
        if match is None:
            raise ParseError(f"Could not determine value and unit for '{key}' in JSON object '{json_object}'")
        value, unit = match.group(1), match.group(2).strip().lower()
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ParseError(f"Could not convert '{value}' to a float")
        unit_str = unit if unit else json_object['unit'] if 'unit' in json_object else None
        if unit_str is not None:
            try:
                return weewx.units.ValueTuple(numeric, self.get_weewx_unit(unit_str, unit_group), unit_group)
            except UnitError as e:
                log.error("parse_obs_value: Could not determine unit applicable to field '%s: %s': %s",
                          key, numeric, e)
                raise ParseError(f"Could not equate Ecowitt unit '{unit_str}' with a WeeWX unit")
        if unit_group not in device_units:
            raise UnitError(f"Could not determine device units for '{unit_group}'")
        return weewx.units.ValueTuple(numeric, device_units[unit_group], unit_group)

    @classmethod
    def get_weewx_unit(cls, unit_string, unit_group=None):
        try:
            unit = cls.unit_lookup[unit_string.lower()]
        except (AttributeError, KeyError):
            raise UnitError(f"unknown Ecowitt unit string: '{unit_string}'")
        if unit_group is not None and unit_group.lower() in ('group_depth', 'group_deltat'):
            unit += '2'
        return unit


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

class EcowittSensors:
    """Sensor metadata (from get_sensors_info) with battery voltages merged in."""

    no_low = ('ws80', 'ws85', 'ws90')
    batt_binary = ('wh68', 'wh69', 'wh25', 'wh26', 'wn31', 'wn32')
    batt_int = ('wn20', 'wn38', 'wh40', 'wh41', 'wh43', 'wh45', 'wh55', 'wh57')
    batt_volt = ('wh68', 'wh51', 'wh54', 'wn34', 'wn35', 'wn38', 'ws80', 'ws85', 'ws90')
    # live data voltage field: sensor address
    sensor_with_voltage = {
        'rain.0x13.voltage': 3, 'piezoRain.0x13.voltage': 49,
        **{f'{arr}.{i}.voltage': 13 + i if i <= 8 else 49 + i for arr in ('ch_soil', 'ch_ec') for i in range(1, 17)},
        **{f'ch_temp.{i}.voltage': 30 + i for i in range(1, 9)},
        **{f'ch_leaf.{i}.voltage': 39 + i for i in range(1, 9)},
        **{f'ch_lds.{i}.voltage': 65 + i for i in range(1, 5)},
        'common_list.0xA1.voltage': 71, 'wqt01.voltage': 72}

    def __init__(self, all_sensor_data=None, live_data=None):
        self.update_sensor_data(all_sensor_data, live_data)

    def update_sensor_data(self, all_sensor_data, live_data=None):
        self.all_sensor_data = all_sensor_data if all_sensor_data is not None else {}
        if live_data is not None:
            for field, address in self.sensor_with_voltage.items():
                if field in live_data:
                    self._set_voltage(address, live_data[field])

    def _set_voltage(self, address, voltage):
        for data in self.all_sensor_data.values():
            if 'address' in data:
                if data['address'] == address:
                    data['voltage'] = voltage
                    return
            else:
                for channel_data in data.values():
                    if _try(int, channel_data.get('address', 999)) == address:
                        channel_data['voltage'] = voltage
                        return

    @property
    def data(self):
        return self.all_sensor_data

    def batt_state_desc(self, model, sensor_data):
        batt = sensor_data.get('battery')
        if model in self.no_low:
            return '--'
        if model in self.batt_binary:
            return {0: 'OK', 1: 'low'}.get(batt, '--')
        if model in self.batt_int:
            if batt is None:
                return '--'
            return 'low' if batt <= 1 else 'DC' if batt == 6 else 'OK' if batt <= 5 else '--'
        if model in self.batt_volt:
            if sensor_data.get('voltage') is not None:
                return 'low' if sensor_data['voltage'] <= 1.2 else 'OK'
            if batt is None:
                return '--'
            return 'low' if batt <= 1 else 'OK' if batt <= 5 else '--'
        return 'Unknown sensor'


class EcowittDevice:
    """An Ecowitt device accessed via the local HTTP API."""

    unit_code_to_string = {
        'temperature': ('group_temperature', ('degree_C', 'degree_F')),
        'pressure': ('group_pressure', ('hPa', 'inHg', 'mmHg')),
        'wind': ('group_speed', ('meter_per_second', 'km_per_hour', 'mile_per_hour', 'knot')),
        'rain': ('group_rain', ('mm', 'inch')),
        'light': ('group_illuminance', ('klux', 'watt_per_meter_squared', 'kfc')),
        'deltat': ('group_deltat', ('degree_C2', 'degree_F2')),
        'rain_rate': ('group_rainrate', ('mm_per_hour', 'inch_per_hour')),
        'depth': ('group_depth', ('mm2', 'foot2')),
        'altitude': ('group_altitude', ('meter', 'foot'))}
    sensors_with_firmware = ('ws80', 'ws85', 'ws90')

    def __init__(self, ip_address, unit_system=DEFAULT_UNIT_SYSTEM, max_tries=DEFAULT_MAX_TRIES,
                 retry_wait=DEFAULT_RETRY_WAIT, url_timeout=DEFAULT_URL_TIMEOUT, show_battery=DEFAULT_FILTER_BATTERY,
                 log_unknown_fields=False, get_soilad=DEFAULT_GET_SOILAD, debug=None):
        if ip_address is None:
            raise weewx.ViolatedPrecondition('device IP address cannot be None')
        self.api = EcowittHttpApi(ip_address=ip_address, max_tries=max_tries, retry_wait=retry_wait,
                                  timeout=url_timeout)
        self.parser = EcowittHttpParser(unit_system=unit_system, show_battery=show_battery, get_soilad=get_soilad,
                                        log_unknown_fields=log_unknown_fields, debug=debug)
        self.sensors = EcowittSensors()
        self.log_failures = True
        self._model = None

    def _get(self, command, parse, *args, **kwargs):
        return parse(self.api.call(command), *args, **kwargs)

    def get_live_data(self, flatten_data=True):
        return self._get('get_livedata_info', self.parser.parse_get_livedata_info, flatten_data=flatten_data)

    def get_sensors_data(self, connected_only=DEFAULT_ONLY_REGISTERED_SENSORS, flatten_data=True):
        return self.parser.parse_get_sensors_info(self.api.get_sensors_info(), connected_only=connected_only,
                                                  flatten_data=flatten_data)

    def get_rain_totals(self):
        return self._get('get_rain_totals', self.parser.parse_get_rain_totals, self.get_device_units())

    def get_rain_totalspart(self):
        return self._get('get_rain_totals', self.parser.parse_get_rain_totalspart)

    def get_piezo_rain_data(self):
        return self._get('get_piezo_rain', self.parser.parse_get_piezo_rain, self.get_device_units())

    def get_piezo_rain_datapart(self):
        return self._get('get_piezo_rain', self.parser.parse_get_piezo_rainpart)

    def get_wn34_offset_data(self):
        return self._get('get_cli_wh34', self.parser.parse_get_cli_wh34, self.get_device_units())

    def get_pm25_offset_data(self):
        return self._get('get_cli_pm25', self.parser.parse_get_cli_pm25, self.get_device_units())

    def get_co2_offset_data(self):
        return self._get('get_cli_co2', self.parser.parse_get_cli_co2, self.get_device_units())

    def get_lds_offset_data(self):
        return self._get('get_cli_lds', self.parser.parse_get_cli_lds)

    def get_calibration_data(self):
        return self._get('get_calibration_data', self.parser.parse_get_calibration_data, self.get_device_units())

    def get_multich_calibration_data(self):
        return self._get('get_cli_multiCh', self.parser.parse_get_cli_multich, self.get_device_units())

    def get_soil_calibration_data(self):
        return self._get('get_cli_soilad', self.parser.parse_get_cli_soilad)

    def get_soil_adnow_data(self):
        return self._get('get_cli_soilad', self.parser.parse_get_cli_soiladnow)

    def get_device_info_data(self):
        return self._get('get_device_info', self.parser.parse_get_device_info)

    def get_device_info_datapart(self):
        return self._get('get_device_info', self.parser.parse_get_device_infopart)

    def get_stationtype(self):
        return self._get('get_version', self.parser.parse_get_stationtype)

    def get_ws_settings(self):
        return self._get('get_ws_settings', self.parser.parse_get_ws_settings)

    def get_sdmmc_info_data(self):
        return self._get('get_sdmmc_info', self.parser.parse_get_sdmmc_info)

    def get_device_units(self):
        parsed = self._get('get_units_info', self.parser.parse_get_units_info)
        units = {'group_percent': 'percent', 'group_direction': 'degree_compass', 'group_uv': 'uv_index',
                 'group_fraction': 'ppm', 'group_concentration': 'microgram_per_meter_cubed'}
        for code_group, code in parsed.items():
            group, names = self.unit_code_to_string.get(code_group, (None, ()))
            if isinstance(code, int) and 0 <= code < len(names):
                units[group] = names[code]
        if 'group_rain' in units:
            for key in ('rain_rate', 'depth', 'altitude'):
                group, names = self.unit_code_to_string[key]
                units[group] = names[parsed['rain']]
        if 'group_temperature' in units:
            units['group_deltat'] = self.unit_code_to_string['deltat'][1][parsed['temperature']]
        return units

    @property
    def ip_address(self):
        return self.api.ip_address

    def _version(self):
        return self.parser.parse_get_version(self.api.call('get_version'))

    @property
    def model(self):
        if self._model is None:
            self._model = self._version().get('version')[8:].strip(' \n\r')
        return self._model

    @property
    def mac_address(self):
        return self.parser.parse_get_network_info(self.api.call('get_network_info')).get('mac')

    @property
    def firmware_version(self):
        return self._version().get('firmware_version')

    @property
    def firmware_update_avail(self):
        version = self.api.call('get_version')
        return version['newVersion'] == '1' if version is not None and 'newVersion' in version else None

    @property
    def firmware_update_message(self):
        info = self.api.call('get_device_info')
        return info.get('curr_msg') if info is not None else None

    @property
    def sensor_firmware_versions(self):
        data = self.get_sensors_data()
        return {s.upper(): data.get(f'{s}.version', 'not available') for s in self.sensors_with_firmware
                if data.get(f'{s}.battery') not in (None, 9)}

    @property
    def paired_rain_gauges(self):
        info = self.get_sensors_data(connected_only=True, flatten_data=False)
        result = set()
        if 'wh69' in info or 'wh40' in info:
            result.add('tipping')
        if 'ws85' in info or 'ws90' in info:
            result.add('piezo')
        return tuple(result)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def natural_sort_keys(source_dict):
    return sorted(source_dict, key=lambda text: [int(c) if c.isdigit() else c
                                                 for c in re.split(r'(\d+)', text.lower())])


def natural_sort_dict(source_dict):
    return '{' + ', '.join(f"'{k}': '{source_dict[k]}'" for k in natural_sort_keys(source_dict)) + '}'


def bytes_to_hex(iterable, separator=' ', caps=True):
    fmt = '{:02X}' if caps else '{:02x}'
    try:
        return separator.join(fmt.format(c) for c in iterable)
    except ValueError:
        return separator.join(fmt.format(c) for c in str.encode(iterable))
    except (TypeError, AttributeError):
        return f"cannot represent '{iterable}' as hexadecimal bytes"


_SECRET_KEY = re.compile(r'pwd|pass|key|token|secret|_id$', re.IGNORECASE)


def mask_secrets(value, key=''):
    """A copy of a decoded API response with passwords, keys and station IDs obfuscated."""
    if isinstance(value, dict):
        return {k: mask_secrets(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v, key) for v in value]
    return obfuscate(str(value)) if value and _SECRET_KEY.search(str(key)) else value


def obfuscate(plain, obf_char='*'):
    """Obfuscate all but the last few characters of a string."""
    if not plain:
        return plain
    n = len(plain)
    stem = 0 if n < 3 else 1 if n < 4 else 2 if n < 6 else 3 if n < 8 else 4
    return obf_char * (n - stem) + (plain[-stem:] if stem else '')


def flatten(dictionary, parent_key=False, separator='.'):
    """Flatten nested dicts/lists into a single level dict with dotted keys."""
    if not hasattr(dictionary, 'keys'):
        return None
    items = {}
    for key, value in dictionary.items():
        new_key = f'{parent_key}{separator}{key}' if parent_key else key
        if isinstance(value, MutableMapping):
            items.update(flatten(value, new_key, separator) if value else {new_key: None})
        elif isinstance(value, list):
            if not value:
                items[new_key] = None
            for k, v in channelise_enumerate(value, channelise=True):
                items.update(flatten({str(k): v}, new_key, separator))
        else:
            items[new_key] = value
    return items


def channelise_enumerate(iterable, start=0, channelise=False):
    for n, elem in enumerate(iterable, start):
        if channelise and 'channel' in elem:
            n = int(elem['channel'])
        elif channelise and 'id' in elem:
            n = int(elem['id'])
        yield n, elem


def calc_checksum(data):
    return sum(data) % 256


# ---------------------------------------------------------------------------
# Command line utility
# ---------------------------------------------------------------------------

class DirectEcowittDevice:
    """Interacts directly with a device when the module is run from the command line."""

    sensor_display_order = ('wn20', 'wh25', 'wh26', 'wn31', 'wn34', 'wn35', 'wn38', 'wh40', 'wh41', 'wh45', 'wh51',
                            'wh54', 'wh55', 'wh57', 'wh68', 'wh69', 'ws80', 'ws85', 'ws90', 'wn64', 'wqt01')
    # (option attribute names, method name) in order of precedence
    actions = ((('test_driver',), 'test_driver'), (('test_service',), 'test_service'),
               (('weewx_fields',), 'weewx_fields'), (('sys_params',), 'display_system_params'),
               (('rain_totals', 'get_rain', 'get_rain_totals'), 'display_rain_totals'),
               (('mulch_offset',), 'display_mulch_offset'),
               (('temp_calibration', 'get_temp_calibration'), 'display_mulch_t_offset'),
               (('pm25_offset',), 'display_pm25_offset'), (('co2_offset',), 'display_co2_offset'),
               (('lds_offset',), 'display_lds_offset'), (('calibration',), 'display_calibration'),
               (('soil_calibration',), 'display_soil_calibration'), (('services',), 'display_services'),
               (('mac',), 'display_mac'), (('firmware',), 'display_firmware'), (('sensors',), 'display_sensors'),
               (('list_sensors',), 'display_sensor_list'), (('dump_api',), 'dump_api'),
               (('live',), 'display_live_data'), (('discover',), 'display_discovered_devices'),
               (('map',), 'display_field_map'), (('driver_map',), 'display_driver_field_map'),
               (('service_map',), 'display_service_field_map'))

    def __init__(self, namespace, arg_parser, stn_dict, **kwargs):
        self.namespace = namespace
        self.arg_parser = arg_parser
        self.stn_dict = stn_dict
        self.unit_system = DEFAULT_UNIT_SYSTEM
        self.discovery_period = kwargs.get('discovery_period', DEFAULT_DISCOVERY_PERIOD)
        self.html_dir = kwargs.get('html_dir')
        self.ip_address = self.ip_from_config_opts()
        self.show_battery, source = self.bool_from_config('show_battery', DEFAULT_FILTER_BATTERY)
        sources = {'default': 'using the default', 'station': 'obtained from station config',
                   'command': 'obtained from command line options'}
        if weewx.debug >= 1:
            print(f"Battery state filtering is {'disabled' if self.show_battery else 'enabled'} "
                  f"({sources.get(source, 'unknown config source')})")
        self.only_registered, source = self.bool_from_config('only_registered', DEFAULT_ONLY_REGISTERED_SENSORS)
        if weewx.debug >= 1:
            print(f"{'Only registered sensors' if self.only_registered else 'All sensors'} will be shown "
                  f"({sources.get(source, 'unknown config source')})")

    def opt(self, name, default=None):
        """Command line option value (the optparse/argparse namespaces differ)."""
        value = getattr(self.namespace, name, None)
        return default if value is None else value

    def ip_from_config_opts(self):
        ip_address = self.opt('ip_address') or None
        source = 'command line options'
        if ip_address is None:
            ip_address = self.stn_dict.get('ip_address')
            source = 'station config'
        if weewx.debug >= 1:
            print()
            print(f'IP address obtained from {source}' if ip_address else 'IP address not specified')
        return ip_address

    def bool_from_config(self, option, default):
        for value, source in ((self.opt(option), 'command'), (self.stn_dict.get(option), 'station')):
            if value is not None:
                try:
                    return weeutil.weeutil.tobool(value), source
                except ValueError:
                    pass
        return default, 'default'

    def get_device(self):
        try:
            return EcowittDevice(ip_address=self.ip_address, max_tries=self.opt('max_tries'),
                                 retry_wait=self.opt('retry_wait'), url_timeout=self.opt('timeout'),
                                 unit_system=self.unit_system, show_battery=self.show_battery)
        except weewx.ViolatedPrecondition as e:
            print()
            print(f'Unable to obtain EcowittDevice object: {e}')
        return None

    def process_options(self):
        for options, method in self.actions:
            if any(self.opt(o) for o in options):
                getattr(self, method)()
                return
        print()
        print('No option selected, nothing done')
        print()
        self.arg_parser.print_help()

    def query(self, fetch):
        """Obtain a device, announce it and return (device, fetch(device)); device is None on error."""
        device = self.get_device()
        if device is None:
            return None, None
        print()
        try:
            print(f'Interrogating {BOLD}{device.model}{ENDC} at {BOLD}{device.ip_address}{ENDC}')
            return device, fetch(device)
        except (DeviceIOError, socket.timeout) as e:
            print()
            print(f'Unable to connect to device at {self.ip_address}: {e}')
            print()
            self.device_connection_help()
        except ParseError as e:
            print()
            print(f'Error parsing device response: {e}')
        return None, None

    def no_response(self):
        print()
        print(f'Device at {self.ip_address} did not respond.')

    @staticmethod
    def formatter(**formats):
        unit_format_dict = dict(weewx.defaults.defaults['Units']['StringFormats'], **formats)
        return weewx.units.Formatter(unit_format_dict=unit_format_dict,
                                     unit_label_dict=weewx.defaults.defaults['Units']['Labels'])

    def vh(self, vt, fmt=None, unit_system=weewx.METRICWX):
        return weewx.units.ValueHelper(vt if vt is not None else _BLANK, formatter=fmt or self.formatter(),
                                       converter=weewx.units.StdUnitConverters[unit_system])

    def multi(self, vt, units, fmt=None):
        """Format vt in the first unit followed by the others in brackets."""
        if vt is None or vt.value is None:
            return '---'
        vh = self.vh(vt, fmt)
        first, *others = [vh.convert(u).toString() for u in units]
        return f"{first} ({'/'.join(others)})" if others else first

    @staticmethod
    def pair(default, other, preferred):
        """Order two units so the device's preferred unit comes first."""
        return [other, default] if preferred == other else [default, other]

    @staticmethod
    def decoded(value, decode):
        return 'unavailable' if value is None else f"{value} ({decode.get(value, 'unknown')})"

    def display_system_params(self):
        device, info = self.query(lambda d: d.get_device_info_data())
        if device is None:
            return
        on_off = {0: 'off', 1: 'on'}
        freq_str = {0: '433MHz', 1: '868Mhz', 2: '915MHz', 3: '920MHz'}.get(info.get('rf_freq'), 'Unknown')
        type_str = {0: 'WH24', 1: 'WH65'}.get(info.get('sensor_type'), 'unknown')
        print()
        print(f"{'sensor type':>35}: {info.get('sensor_type')} ({type_str})")
        print(f"{'frequency':>35}: {info.get('rf_freq')} ({freq_str})")
        for label, key, decode in (('automatic frequency control (AFC)', 'afc', on_off),
                                   ('temperature compensation', 'rad_comp', on_off),
                                   ('auto timezone', 'tz_auto', {0: 'on', 1: 'off'})):
            value = info.get(key)
            print(f"{label:>35}: {self.decoded(value, decode)}")
        tz_name = info.get('tz_name') or 'name not provided'
        print(f"{'timezone index':>35}: {info.get('tz_index')} ({tz_name})")
        print(f"{'DST status':>35}: {info.get('dst')} ({on_off.get(info.get('dst'), 'unknown')})")
        if info.get('date') is not None:
            print(f"{'date-time':>35}: {time.strftime('%d %B %Y %H:%M:%S', time.localtime(info['date']))}")
        for label, key in (('auto upgrade', 'upgrade'), ('device AP auto off', 'ap_auto')):
            value = info.get(key)
            print(f"{label:>35}: {self.decoded(value, on_off)}")
        print(f"{'device AP SSID':>35}: {info.get('ap')}")

    def display_rain_totals(self):
        device, data = self.query(lambda d: (d.get_rain_totals(), d.get_piezo_rain_data(), d.get_device_units()))
        if device is None:
            return
        totals, piezo, device_units = data
        inch = device_units.get('group_rain') == 'inch'
        units = ('inch', 'mm') if inch else ('mm', 'inch')
        limits = (('<', 4, 0.16), ('<', 10, 0.39), ('<', 30, 1.18), ('<', 60, 2.36), ('>', 60, 2.36))
        gain_label = {n: (f'({op} {i} in/hr / {op} {m} mm/hr)' if inch else f'({op} {m} mm/hr / {op} {i} in/hr)')
                      for n, (op, m, i) in enumerate(limits, 1)}
        gauge_name = next((g['gauge'] for g in totals.get('rain_list', [])
                           if g.get('value') == totals['rain_priority']), 'Unable to determine rainfall data priority')
        print()
        print(f"Rainfall data priority: {gauge_name} ({totals['rain_priority']})")
        for title, data in (('Traditional', totals), ('Piezo', piezo)):
            print()
            print(f'  {title} gauge rain data:')
            for key, label in (('day_rain', 'Day rain'), ('week_rain', 'Week rain'), ('month_rain', 'Month rain'),
                               ('year_rain', 'Year rain'), ('total_rain', 'Total rain')):
                if key in data:
                    print(f'{label:>15}: {self.multi(data[key], units)}')
            if data is totals:
                gain = totals.get('rain_gain')
                print(f"{'Rain gain':>15}: {f'{gain:.2f}' if gain is not None else '---'}")
        for n in range(1, 6):
            gain = piezo.get(f'gain{n}')
            print(f"{f'Rain{n} gain':>15}: {f'{gain:.2f}' if gain is not None else '--'} {gain_label[n]}")
        print()
        print('  Rainfall reset times:')
        day, week, year = (totals.get(f'rain_reset_{k}') for k in ('day', 'week', 'year'))
        print(f"{'Daily rainfall reset time':>30}: {f'{day:02d}:00' if day is not None else '-----'}")
        print(f"{'Weekly rainfall reset':>30}: {calendar.day_name[(week + 6) % 7] if week is not None else '-----'}")
        print(f"{'Annual rainfall reset':>30}: {calendar.month_name[year + 1] if year is not None else '-----'}")

    def _display_offsets(self, fetch, title, none_found, rows):
        """Display per-channel calibration offsets; rows(sensor, units) yields (label, text)."""
        device, data = self.query(lambda d: (fetch(d), d.get_device_units()))
        if device is None:
            return
        offsets, device_units = data
        if offsets is None:
            self.no_response()
            return
        print()
        print(title)
        if not offsets:
            print(f'{none_found:>{len(none_found) + 4}}')
        for sensor in offsets:
            for i, (label, text) in enumerate(rows(sensor, device_units)):
                channel = f"{'Channel':>11} {sensor['channel']:d}" if i == 0 else ''
                print(f"{channel:>13}{':' if i == 0 else ' '} {label:>18}: {text}")
        print()

    def temp_offset(self, sensor, device_units):
        return self.multi(sensor['temp'], self.pair('degree_C2', 'degree_F2', device_units.get('group_deltat')))

    def display_mulch_offset(self):
        self._display_offsets(
            lambda d: d.get_multich_calibration_data(), 'Multi-channel Temperature and Humidity Calibration',
            'No Multi-channel temperature and humidity sensors found',
            lambda s, u: (('Temperature offset', self.temp_offset(s, u)),
                          ('Humidity offset', self.vh(s['humi']).toString())))

    def display_mulch_t_offset(self):
        self._display_offsets(
            lambda d: d.get_wn34_offset_data(), 'Multi-channel Temperature Calibration',
            'No Multi-channel temperature sensors found',
            lambda s, u: (('Temperature offset', self.temp_offset(s, u)),))

    def display_pm25_offset(self):
        self._display_offsets(lambda d: d.get_pm25_offset_data(), 'PM2.5 Calibration', 'No PM2.5 sensors found',
                              lambda s, u: (('PM2.5 offset', self.vh(s['val']).toString()),))

    def display_co2_offset(self):
        device, data = self.query(lambda d: d.get_co2_offset_data())
        if device is None:
            return
        if data is None:
            self.no_response()
        else:
            fmt = self.formatter(microgram_per_meter_cubed='%.1f')
            print()
            print('CO2 Calibration')
            for key, label in (('co2', 'CO2'), ('pm1', 'PM1'), ('pm25', 'PM2.5'), ('pm4', 'PM4'), ('pm10', 'PM10')):
                if data.get(key) is not None or key in ('co2', 'pm25', 'pm10'):
                    print(f"{label + ' offset':>16}: {self.vh(data.get(key, _BLANK), fmt).toString()}")
        print()

    def display_lds_offset(self):
        device, data = self.query(lambda d: (d.get_lds_offset_data(), d.get_device_units()))
        if device is None:
            return
        lds, device_units = data
        if lds is None:
            self.no_response()
        elif not lds:
            print(f"{'No LDS sensors found':>26}")
        else:
            units = self.pair('mm2', 'foot2', device_units.get('group_depth'))
            print()
            print('LDS Calibration')
            for sensor in lds:
                name = f" ({sensor['name']})" if sensor.get('name') else ''
                print(f"    Channel {sensor['channel']:d}{name}")
                print(f"{'Offset':>15}: {self.multi(sensor.get('offset', _BLANK), units)}")
                print(f"{'Height':>15}: {self.multi(sensor.get('total_height', _BLANK), units)}")
                print(f"{'Heat':>15}: {sensor.get('total_heat')}")
                print(f"{'Level':>15}: {sensor.get('level')}")
        print()

    def display_calibration(self):
        device, data = self.query(lambda d: (d.get_calibration_data(), d.get_device_units()))
        if device is None:
            return
        cal, device_units = data
        if cal is None:
            self.no_response()
            return
        dt_units = self.pair('degree_C2', 'degree_F2', device_units.get('group_deltat'))
        p_units = {'mmHg': ['mmHg', 'hPa', 'inHg'], 'inHg': ['inHg', 'hPa', 'mmHg']}.get(
            device_units.get('group_pressure'), ['hPa', 'inHg', 'mmHg'])
        a_units = self.pair('meter', 'foot', device_units.get('group_altitude'))
        print()
        print('Calibration')
        for key, label in (('solar_gain', 'Irradiance gain'), ('uv_gain', 'UV gain'), ('wind_gain', 'Wind gain')):
            value = cal.get(key)
            print(f"{label:>26}: {f'{value:.2f}' if value is not None else '---'}")
        for key, label, units in (('intemp_offset', 'Inside temperature offset', dt_units),
                                  ('inhumid_offset', 'Inside humidity offset', None),
                                  ('outtemp_offset', 'Outside temperature offset', dt_units),
                                  ('outhumid_offset', 'Outside humidity offset', None),
                                  ('abs_offset', 'Absolute pressure offset', p_units),
                                  ('rel_offset', 'Relative pressure offset', p_units),
                                  ('altitude', 'Altitude for REL', a_units)):
            if key in cal:
                text = self.multi(cal[key], units) if units else self.vh(cal[key]).toString()
                print(f'{label:>26}: {text}')
        print(f"{'Wind direction offset':>26}: "
              f"{self.vh(cal.get('winddir_offset', _BLANK)).toString(useThisFormat='%d')}")
        print()

    def display_soil_calibration(self):
        device, data = self.query(lambda d: d.get_soil_calibration_data())
        if device is None:
            return
        if data is None:
            self.no_response()
        else:
            print()
            print('Soil Calibration')
            for sensor in data:
                print(f"    Channel {sensor['channel']:d} ({sensor.get('soilVal')}%)")
                for key, label in (('nowAd', 'Now AD'), ('minVal', '0% AD'), ('maxVal', '100% AD')):
                    print(f'{label:>16}: {sensor.get(key)}')
        print()

    def display_services(self):
        device, data = self.query(lambda d: d.get_ws_settings())
        if device is None:
            return
        if not data:
            self.no_response()
            return

        def mask(key):
            return data.get(key) if self.opt('unmask') else obfuscate(data.get(key))

        def interval_str(interval, seconds_from=None):
            if interval is None:
                return '--'
            if interval == 0:
                return '0 minutes (disabled)'
            if seconds_from is not None and interval >= seconds_from:
                return f'{interval:d} seconds'
            return '1 minute' if interval == 1 else f'{interval:d} minutes' if interval > 1 else '--'

        print()
        print('Weather Services')
        print()
        print('  Ecowitt.net')
        ost = data.get('ost_interval')
        print(f"{'Upload Interval':>22}: " + ('Upload to Ecowitt.net is disabled' if ost == 0 else interval_str(ost)))
        print(f"{'MAC':>22}: {data.get('sta_mac')}")
        for title, prefix, seconds_from in (('Wunderground', 'wu', 16), ('Weathercloud', 'wcl', None),
                                            ('Weather Observations Website', 'wow', None)):
            print()
            print(f'  {title}')
            if f'{prefix}_interval' not in data:
                print('       no data')
                continue
            print(f"{'Upload Interval':>22}: {interval_str(data[f'{prefix}_interval'], seconds_from)}")
            print(f"{'Station ID':>22}: {mask(f'{prefix}_id')}")
            print(f"{'Station Key':>22}: {mask(f'{prefix}_key')}")
        print()
        print('  Customized')
        custom = data.get('Customized')
        print(f"{'Upload':>22}: {'Unknown' if custom is None else 'Enabled' if custom else 'Disabled'}")
        protocol = (data.get('Protocol') or '').lower()
        if protocol in ('ecowitt', 'wunderground'):
            prefix = 'ecowitt' if protocol == 'ecowitt' else 'usr_wu'
            print(f"{'Upload Protocol':>22}: {protocol.capitalize()}")
            print(f"{'Server IP/Hostname':>22}: {data.get(f'{prefix}_ip')}")
            print(f"{'Path':>22}: {data.get(f'{prefix}_path')}")
            if protocol == 'wunderground':
                print(f"{'Station ID':>22}: {mask('usr_wu_id')}")
                print(f"{'Station Key':>22}: {mask('usr_wu_key')}")
            print(f"{'Port':>22}: {data.get(f'{prefix}_port')}")
            print(f"{'Upload Interval':>22}: {data.get(f'{prefix}_upload')}")
        elif protocol == 'mqtt':
            print(f"{'Upload Protocol':>22}: MQTT")
            for key, label, masked in (('mqtt_host', 'Host', False), ('mqtt_port', 'Port', False),
                                       ('mqtt_topic', 'Topic', False), ('mqtt_interval', 'Upload Interval', False),
                                       ('mqtt_keepalive', 'Keep Alive', False), ('mqtt_name', 'Client Name', True),
                                       ('mqtt_clientid', 'Client ID', True), ('mqtt_username', 'Username', False),
                                       ('mqtt_password', 'Password', True)):
                print(f'{label:>22}: {mask(key) if masked else data.get(key)}')

    def display_mac(self):
        device, mac = self.query(lambda d: d.mac_address)
        if device is not None:
            print()
            print(f"{'MAC address':>15}: {mac}")

    def display_firmware(self):
        device, data = self.query(lambda d: (d.firmware_version, d.sensor_firmware_versions,
                                             d.firmware_update_avail, d.firmware_update_message))
        if device is None:
            return
        fw_version, sensor_fw, update_avail, curr_msg = data
        model = device.model
        print()
        print(f"{f'installed {model} firmware version':>35}: {fw_version}")
        for sensor, version in (sensor_fw or {}).items():
            print(f"{f'installed {sensor} firmware version':>35}: {version}")
        print()
        if update_avail:
            print(f'    a firmware update is available for this {model}')
            print(f'    update at http://{self.ip_address} or via the WSView Plus app')
            if curr_msg is not None:
                print()
                print('    likely firmware update message:')
                for line in curr_msg.split('\r\n'):
                    print(f'      {line}')
            else:
                print('    no firmware update message found')
        elif update_avail is None:
            print(f'    could not determine if a firmware update is available for this {model}')
        else:
            print(f'    the firmware is up to date for this {model}')

    def display_sensors(self):
        device, data = self.query(lambda d: (d.get_sensors_data(connected_only=False, flatten_data=False),
                                             d.get_live_data(flatten_data=True)))
        if device is None:
            return
        sensors_data, live_data = data
        device.sensors.update_sensor_data(sensors_data, live_data)
        metadata = device.sensors.data
        if not metadata:
            print()
            print(f'Device at {self.ip_address} did not return any sensor data.')
            return
        registered_only = self.opt('registered_only', self.only_registered)
        for model in self.sensor_display_order:
            data = metadata.get(model)
            if data is None:
                continue
            channels = [('', data)] if 'address' in data else list(data.items())
            for channel, sensor in channels:
                sensor_id = str(sensor.get('id'))
                if sensor_id.lower() in EcowittHttpParser.not_registered:
                    if registered_only:
                        continue
                    id_str = 'sensor is disabled' if sensor_id.lower() == 'fffffffe' else 'sensor is registering...'
                    details = '  '
                else:
                    id_str = f'sensor ID: {sensor_id}'
                    rssi = sensor.get('rssi', '--')
                    volt = f" ({sensor['voltage']}V)" if 'voltage' in sensor else ''
                    batt = sensor.get('battery', '--')
                    desc = device.sensors.batt_state_desc(model=model, sensor_data=sensor)
                    details = (f"signal: {sensor.get('signal', '--')} {'' if rssi is None else f'rssi: {rssi}'} "
                               f"battery: {'--' if batt is None else batt}{volt} ({desc})")
                name = ' '.join([model, channel]).upper()
                print(f'{name:<10} {id_str:<25} {details}')

    def sensor_map(self):
        """The [[sensor_map]] settings, or None with --no-sensor-map."""
        return None if self.opt('no_sensor_map') else self.stn_dict.get('sensor_map')

    def print_sensor_map(self, mapper):
        if not mapper:
            return
        print(f'Sensor map: {len(mapper.targets)} sensor(s) locked to channels by hardware ID')
        for line in mapper.problems + mapper.lines or ['every mapped sensor is already on its channel']:
            print(f'    {line}')
        print()

    def display_sensor_list(self):
        """List sensors by hardware ID: gateway channel, reported channel and WeeWX fields."""
        device, data = self.query(lambda d: (d.get_sensors_data(connected_only=False), d.get_live_data()))
        if device is None:
            return
        sensors, live = data
        mapper = SensorMapper(self.sensor_map())
        mapper.plan(sensors)
        field_map = HttpMapper(driver_debug=None, **self.stn_dict).field_map
        paired = SensorMapper.paired(sensors)
        print()
        self.print_sensor_map(mapper)
        rows = [('Sensor', 'ID', 'Signal', 'Battery', 'Reading', 'Reported as', 'WeeWX fields')]
        for model, (count, groups) in SENSOR_GROUPS.items():
            for channel, sid in sorted(paired.get(model, {}).items()):
                reported = mapper.reported_channel(model, channel)
                keys = channel_keys(model, reported)
                fields = natural_sort_keys({w: 0 for w, src in field_map.items() if src.startswith(tuple(keys))})
                readings = [f"{live[k]}{_READING_UNIT.get(f, '')}" for g in groups for f in _SENSOR_READING[g]
                            for k in (f'{g}.{channel}.{f}',) if live.get(k) is not None]
                rows.append((f'{model.upper()} CH{channel}', sid, self._signal(sensors, f'{model}.ch{channel}'),
                             self._battery(sensors, f'{model}.ch{channel}'), ' / '.join(readings) or '--',
                             f'CH{reported}' + ('' if reported == channel else ' (mapped)'),
                             ', '.join(fields) or '(none)'))
        single = [m for m in self.sensor_display_order if m not in SENSOR_GROUPS
                  and str(sensors.get(f'{m}.id', 'FFFFFFFF')).upper() not in _UNREGISTERED_IDS]
        for model in single:
            fields = natural_sort_keys({w: 0 for w, src in field_map.items() if src.startswith(f'{model}.')})
            rows.append((model.upper(), str(sensors[f'{model}.id']), self._signal(sensors, model),
                         self._battery(sensors, model), '', 'fixed', ', '.join(fields) or '(none)'))
        if len(rows) == 1:
            print(f'Device at {self.ip_address} did not report any registered sensors.')
            return
        widths = [max(len(str(r[i])) for r in rows) for i in range(6)]
        for n, row in enumerate(rows):
            text = '  '.join(f'{str(v):<{w}}' for v, w in zip(row, widths))
            indent = len(text) + 2
            wrapped = textwrap.wrap(row[6], width=max(30, 120 - indent)) or ['']
            print(f'{text}  {wrapped[0]}')
            for more in wrapped[1:]:
                print(' ' * indent + more)
            if n == 0:
                print('-' * min(120, indent + max(len(r[6]) for r in rows)))
        locked = [(model, channel, sid) for model in SENSOR_GROUPS
                  for channel, sid in sorted(paired.get(model, {}).items())]
        if locked:
            print()
            print('To keep each sensor on the channel it is reported on now, even if it is re-paired')
            print('onto a different gateway channel, add this to [EcowittGateway] in weewx.conf:')
            print()
            print('    [[sensor_map]]')
            for model, channel, sid in locked:
                reported = mapper.reported_channel(model, channel)
                print(f"        {sid} = {reported}{' ' * max(1, 10 - len(sid) - len(str(reported)))}"
                      f'# {model.upper()}')

    @staticmethod
    def _signal(sensors, prefix):
        signal, rssi = sensors.get(f'{prefix}.signal'), sensors.get(f'{prefix}.rssi')
        return f"{'--' if signal is None else signal}/4" + ('' if rssi is None else f' {rssi}dBm')

    @staticmethod
    def _battery(sensors, prefix):
        batt = sensors.get(f'{prefix}.battery')
        return '--' if batt is None else str(batt)

    def dump_api(self):
        """Save every raw API response as one JSON document, for fault reports and new sensors."""
        if not self.ip_address:
            print()
            print('No device IP address: use --ip-address or set ip_address in weewx.conf')
            return
        api = EcowittHttpApi(self.ip_address, max_tries=self.opt('max_tries'), retry_wait=self.opt('retry_wait'),
                             timeout=self.opt('timeout'))
        unmask = bool(self.opt('unmask'))
        result = {'_about': {'driver': f'{DRIVER_MODULE} {DRIVER_VERSION}', 'ip_address': self.ip_address,
                             'time': timestamp_to_string(int(time.time())), 'secrets_masked': not unmask}}
        print()
        print(f'Reading every API response from {self.ip_address}...')
        for command in api.commands:
            for page in (1, 2, 3, 4, 5) if command == 'get_sensors_info' else (None,):
                label = command if page is None else f'{command}?page={page}'
                try:
                    resp = api.request(command, data=None if page is None else {'page': page}, rename=False)
                except OSError as e:
                    resp = {'_error': str(e)}
                result[label] = resp if unmask else mask_secrets(resp)
        text = json.dumps(result, indent=2)
        output = self.opt('output')
        if output:
            with open(output, 'w') as f:
                f.write(text + '\n')
            print(f'Saved {len(result) - 1} responses to {output}')
        else:
            print()
            print(text)
        if not unmask:
            print('Passwords, keys and station IDs are masked; use --unmask to include them.')

    def display_live_data(self):
        try:
            collector = EcowittHttpCollector(ip_address=self.ip_address, show_battery=self.show_battery,
                                             sensor_map=self.sensor_map())
            print()
            print(f'Interrogating {collector.device.model} at {self.ip_address}')
            current_data = collector.get_current_data()
        except weewx.ViolatedPrecondition as e:
            print()
            print(f'Unable to obtain EcowittDevice object: {e}')
            return
        except (DeviceIOError, socket.timeout) as e:
            print()
            print(f'Unable to connect to device at {self.ip_address}: {e}')
            print()
            self.device_connection_help()
            return
        mapped = HttpMapper(driver_debug=None).map_data(current_data)
        ts = mapped.pop('datetime', int(time.time()))
        mapped['usUnits'] = self.unit_system
        weewx.units.obs_group_dict.prepend(DEFAULT_GROUPS)
        unit_system = weewx.units.unit_constants[self.opt('units', 'METRICWX').upper()]
        fmt = self.formatter(volt='%.2f', microgram_per_meter_cubed='%.1f')
        result = {}
        for key in mapped:
            if key != 'usUnits':
                try:
                    result[key] = self.vh(weewx.units.as_value_tuple(mapped, key), fmt, unit_system).toString(
                        None_string='None')
                except Exception:
                    pass
        print()
        print(f'Displaying data using the WeeWX {weewx.units.unit_nicknames.get(unit_system)} unit group.')
        print()
        self.print_sensor_map(collector.sensor_mapper)
        print(f'{collector.device.model} live sensor data ({timestamp_to_string(ts)}): '
              f'{weeutil.weeutil.to_sorted_string(result)}')

    def display_discovered_devices(self):
        print()
        print('Discovering Ecowitt devices...')
        print()
        devices = sorted(self.discover(), key=itemgetter('ip_address'))
        if not devices:
            print('No devices were discovered.')
            return
        possible_ip = [d['ip_address'] for d in devices if d['model'] not in KNOWN_DEVICES and self.is_supported(d)]
        groups = (('Supported Devices', [d for d in devices if d['model'] in SUPPORTED_DEVICES]),
                  ('Possibly Supported Devices', [d for d in devices if d['ip_address'] in possible_ip]),
                  ('Unsupported Devices', [d for d in devices if d['model'] in UNSUPPORTED_DEVICES]))
        printed = False
        for title, group in groups:
            group = [d for d in group if d['ip_address'] is not None]
            if not group:
                continue
            if printed:
                print()
            print(title)
            printed = True
            for d in group:
                model = d['model'] or (f"{d['ssid'][0:6]} (model unconfirmed)" if d.get('ssid') else 'unknown')
                print(f"  {model} discovered at IP address {d['ip_address']}")

    @staticmethod
    def is_supported(device):
        try:
            EcowittDevice(ip_address=device.get('ip_address')).get_device_info_data()
        except Exception:
            return False
        return True

    def discover(self):
        results = []
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(self.opt('discovery_timeout', DEFAULT_DISCOVERY_TIMEOUT))
        s.bind(('', self.opt('discovery_port', DEFAULT_DISCOVERY_PORT)))
        start_ts = time.time()
        try:
            while True:
                try:
                    response = s.recv(1024)
                except socket.timeout:
                    break
                if response and len(response) > 3 and response[2] == 18 and \
                        calc_checksum(response[2:-1]) == response[-1]:
                    found = self.decode_broadcast_response(response)
                    if not any(d['mac'] == found['mac'] for d in results):
                        found['model'] = EcowittHttpParser.get_model(found.get('ssid'))
                        results.append(found)
                if time.time() - start_ts > self.discovery_period:
                    break
        finally:
            s.close()
        return results

    @staticmethod
    def decode_broadcast_response(raw_data):
        size = struct.unpack('>H', raw_data[3:5])[0]
        data = raw_data[5:size + 2]
        return {'mac': bytes_to_hex(data[0:6], separator=':'),
                'ip_address': '%d.%d.%d.%d' % struct.unpack('>BBBB', data[6:10]),
                'port': struct.unpack('>H', data[10:12])[0],
                'ssid': ''.join(chr(x) for x in data[13:])}

    @staticmethod
    def print_field_map(field_map, title, source):
        print()
        print(title)
        print(f'(format is WeeWX field name: {source} field name)')
        print()
        for key in natural_sort_keys(field_map):
            print(f'{key:>23}: {field_map[key]}')

    def display_field_map(self):
        self.print_field_map(HttpMapper(driver_debug=None).field_map, 'Ecowitt HTTP driver/service default field map:',
                             'Ecowitt HTTP driver')

    def display_driver_field_map(self):
        print()
        print('This may take a moment...')
        driver = None
        try:
            driver = EcowittHttpDriver(**self.stn_dict)
            self.print_field_map(driver.mapper.field_map, 'Ecowitt HTTP driver actual field map:', 'driver')
        except DeviceIOError as e:
            print()
            print(f'Unable to connect to device: {e}')
            print()
            print('Unable to display actual driver field map')
            print()
            self.device_connection_help()
        except KeyboardInterrupt:
            pass
        finally:
            if driver:
                driver.closePort()

    def engine_config(self):
        service = f'{DRIVER_MODULE}.EcowittHttpService'
        config = {'Station': {'station_type': 'Simulator', 'altitude': [0, 'meter'], 'latitude': 0, 'longitude': 0},
                  'Simulator': {'driver': 'weewx.drivers.simulator', 'mode': 'simulator'},
                  DRIVER_NAME: {'ip_address': self.ip_address},
                  'Engine': {'Services': {'archive_services': service, 'report_services': 'weewx.engine.StdPrint'}}}
        for opt, key in (('poll_interval', 'poll_interval'), ('max_tries', 'max_tries'), ('retry_wait', 'retry_wait')):
            if self.opt(opt):
                config[DRIVER_NAME][key] = self.opt(opt)
        return config

    def run_service(self, action):
        """Start an engine running the service and pass the service to action."""
        engine = None
        try:
            engine = weewx.engine.StdEngine(self.engine_config())
            svc = next((s for s in engine.service_obj if hasattr(s, 'collector')), None)
            if svc is not None:
                action(engine, svc)
        except DeviceIOError as e:
            print()
            print(f'Unable to connect to device: {e}')
            print()
            self.device_connection_help()
        except KeyboardInterrupt:
            pass
        finally:
            if engine:
                engine.shutDown()

    def display_service_field_map(self):
        print()
        print('This may take a moment...')
        self.run_service(lambda engine, svc: self.print_field_map(
            svc.mapper.field_map, 'Ecowitt HTTP driver service actual field map:', 'service'))

    def run_driver(self, action):
        """Create a driver from the station config (plus command line overrides) and pass it to action."""
        self.stn_dict['ip_address'] = self.ip_address
        for opt, key in (('poll_interval', 'poll_interval'), ('max_tries', 'max_tries'),
                         ('retry_wait', 'retry_wait'), ('timeout', 'url_timeout')):
            if self.opt(opt):
                self.stn_dict[key] = self.opt(opt)
        if self.opt('no_sensor_map'):
            self.stn_dict.pop('sensor_map', None)
        driver = None
        try:
            driver = EcowittHttpDriver(html_dir=self.html_dir, **self.stn_dict)
            device = driver.collector.device
            print()
            print(f'Interrogating {BOLD}{device.model}{ENDC} at {BOLD}{device.ip_address}{ENDC}')
            print()
            mapper = driver.collector.sensor_mapper
            if mapper:
                mapper.plan(device.get_sensors_data())
                self.print_sensor_map(mapper)
            action(driver)
        except DeviceIOError as e:
            print()
            print(f'Unable to connect to device: {e}')
            print()
            self.device_connection_help()
        except KeyboardInterrupt:
            pass
        finally:
            if driver is not None:
                driver.closePort()

    def test_driver(self):
        log.info('Testing Ecowitt HTTP driver...')

        def run(driver):
            for pkt in driver.genLoopPackets():
                print(f"{timestamp_to_string(pkt['dateTime'])}: {weeutil.weeutil.to_sorted_string(pkt)}")

        self.run_driver(run)
        log.info('Ecowitt HTTP driver testing complete')

    def test_service(self):
        log.info('Testing Ecowitt HTTP driver as a service...')
        weewx.units.obs_group_dict['dummyTemp'] = 'group_temperature'

        def run(engine, svc):
            device = svc.collector.device
            print()
            print(f'Interrogating {BOLD}{device.model}{ENDC} at {BOLD}{device.ip_address}{ENDC}')
            print()
            while True:
                packet = {'dateTime': int(time.time()), 'usUnits': weewx.US, 'dummyTemp': 96.3}
                engine.dispatchEvent(weewx.Event(weewx.NEW_LOOP_PACKET, packet=packet, origin='software'))
                time.sleep(10)

        self.run_service(run)
        log.info('Ecowitt HTTP driver service testing complete')

    def weewx_fields(self):
        log.info('Displaying WeeWX loop packet fields emitted by the Ecowitt HTTP driver...')
        unit_system = {'us': weewx.US, 'metric': weewx.METRIC}.get(str(self.opt('units', '')).lower(), weewx.METRICWX)
        fmt = self.formatter(volt='%.2f', microgram_per_meter_cubed='%.1f')

        def run(driver):
            pkt = next(driver.genLoopPackets())
            result = {}
            for key in pkt:
                if key == 'usUnits':
                    continue
                vt = weewx.units.as_value_tuple(pkt, key)
                try:
                    if key in ('apName', 'stationtype'):
                        result[key] = weewx.units.ValueHelper(vt, formatter=fmt).toString(None_string='None')
                    else:
                        result[key] = self.vh(vt, fmt, unit_system).toString(None_string='None')
                except Exception:
                    result[key] = f"Unable to convert/format '{key}'"
            for key in natural_sort_keys(result):
                print(f'{key:>25}: {result[key]}')

        self.run_driver(run)

    @staticmethod
    def device_connection_help():
        print('    Things to check include that the correct device IP address is being used,')
        print('    the device is powered on and the device is not otherwise disconnected from')
        print('    the local network.')


def main():
    import argparse
    usage = f"""{BOLD}%(prog)s --help
                --version
                --test-driver|--test-service
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS] [--poll-interval=INTERVAL]
                     [--max-tries=MAX_TRIES] [--retry-wait=RETRY_WAIT]
                     [--show-all-batt] [--debug=0|1|2|3]
                --live-data
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--units=us|metric|metricwx]
                     [--ip-address=IP_ADDRESS] [--no-sensor-map]
                     [--show-all-batt] [--debug=0|1|2|3]
                --list-sensors
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS] [--no-sensor-map]
                --dump-api
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS] [--output=FILE] [--unmask]
                --weewx-fields
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS]
                     [--show-all-batt] [--debug=0|1|2|3]
                --default-map|--driver-map|--service-map
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--debug=0|1|2|3]
                --discover
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--debug=0|1|2|3]{ENDC}
    """
    parser = argparse.ArgumentParser(usage=usage, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description='Interact with an Ecowitt device via the Ecowitt local HTTP API.')
    for opt, dest, text in (
            ('--version', 'version', 'display driver version number'),
            ('--discover', 'discover', 'display details of discovered devices'),
            ('--live-data', 'live', 'display device live sensor data'),
            ('--sensors', 'sensors', 'display device sensor data'),
            ('--list-sensors', 'list_sensors',
             'list multi-channel sensors by hardware ID with their channels and WeeWX fields'),
            ('--dump-api', 'dump_api', 'save every raw API response as JSON (passwords masked)'),
            ('--no-sensor-map', 'no_sensor_map', 'ignore [[sensor_map]] and show gateway channels'),
            ('--test-driver', 'test_driver', 'exercise the driver'),
            ('--test-service', 'test_service', 'exercise the driver as a WeeWX service'),
            ('--weewx-fields', 'weewx_fields', 'display WeeWX loop packet fields emitted by the current configuration'),
            ('--default-map', 'map', 'display the default field map'),
            ('--driver-map', 'driver_map', 'display the field map that would be used by the driver'),
            ('--service-map', 'service_map', 'display the field map that would be used by the service'),
            ('--firmware', 'firmware', 'display device firmware information'),
            ('--mac', 'mac', 'display device MAC address'),
            ('--system', 'sys_params', 'display device system parameters'),
            ('--get-rain-totals', 'rain_totals',
             'display rain gauge totals and gains, rainfallpriority and rainfall reset times'),
            ('--get-calibration', 'calibration', 'display device calibration data'),
            ('--get-th-cal', 'mulch_offset', 'display device multi-channel temperature and humidity calibration data'),
            ('--get-soil-cal', 'soil_calibration', 'display device multi-channel soil moisture calibration data'),
            ('--get-t-cal', 'temp_calibration', 'display device multi-channel temperature calibration data'),
            ('--get-pm25-cal', 'pm25_offset', 'display device multi-channel PM2.5 calibration data'),
            ('--get-co2-cal', 'co2_offset', 'display device CO2 (WH45) calibration data'),
            ('--get-lds-cal', 'lds_offset', 'display device LDS (WH54) calibration data'),
            ('--get-services', 'services', 'display device weather services configuration data'),
            ('--show-all-batt', 'show_battery', 'show all available battery state data regardless of sensor state'),
            ('--registered-only', 'registered_only', 'show only registered sensors'),
            ('--unmask', 'unmask', 'unmask sensitive settings')):
        parser.add_argument(opt, dest=dest, action='store_true', help=text)
    parser.add_argument('--ip-address', dest='ip_address', help='device IP address to use')
    for opt, dest, default, text in (
            ('--poll-interval', 'poll_interval', None, 'how often to poll the device API'),
            ('--max-tries', 'max_tries', DEFAULT_MAX_TRIES, 'max number of attempts to contact the device'),
            ('--retry-wait', 'retry_wait', None, 'how long to wait between attempts to contact the device'),
            ('--timeout', 'timeout', None, 'how long to wait for the device to respond to a HTTP request'),
            ('--discovery-port', 'discovery_port', DEFAULT_DISCOVERY_PORT,
             'port to listen to when discovering devices'),
            ('--discovery-timeout', 'discovery_timeout', DEFAULT_DISCOVERY_TIMEOUT,
             'how long to listen when discovering devices'),
            ('--debug', 'driver_debug', 0, 'How much status to display, 0-3')):
        parser.add_argument(opt, dest=dest, type=int, default=default, help=text)
    parser.add_argument('--units', dest='units', metavar='UNIT SYSTEM',
                        default=weewx.units.unit_nicknames[DEFAULT_UNIT_SYSTEM],
                        help='unit system to use when displaying live data')
    parser.add_argument('--output', dest='output', metavar='FILE', help='file for --dump-api output')
    parser.add_argument('--config', dest='config', metavar='CONFIG_FILE', help='Use configuration file CONFIG_FILE.')
    namespace = parser.parse_args()
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)
    if namespace.version:
        print(f'{DRIVER_NAME} driver version {DRIVER_VERSION}')
        sys.exit(0)
    config_path, config_dict = weecfg.read_config(namespace.config)
    print(f'Using configuration file {BOLD}{config_path}{ENDC}')
    weewx.debug = weeutil.weeutil.to_int(namespace.driver_debug if namespace.driver_debug is not None
                                         else config_dict.get('debug', 0))
    if weewx.debug > 0:
        print(f'debug level is {weewx.debug:d}')
    weeutil.logger.setup('ecowitt_http', config_dict)
    define_units()
    DirectEcowittDevice(namespace, parser, driver_config(config_dict),
                        html_dir=html_root(config_dict)).process_options()


if __name__ == '__main__':
    main()
