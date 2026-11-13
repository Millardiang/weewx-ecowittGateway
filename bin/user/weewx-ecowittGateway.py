#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""weewx-ecowittGateway.py

A WeeWX driver for devices using the Ecowitt local HTTP API, or the TCP API for
the GW1000.

Copyright (C) 2024-25 Gary Roderick                     gjroderick<at>gmail.com
Copyright (C) 2026 Ian Millard (weewx-ecowittGateway 2.0.0 and later)

weewx-ecowittGateway 2.0.0 is built on Gary Roderick's ecowitt_http.py 0.1.0a28
(https://github.com/weewx-contrib/weewx-ecowitt_local_http, commit 31bdfe5).

The multi-gateway merge follows the design of weewx-ecowitt-multi:
Copyright (C) 2026 mKainzbauer          https://github.com/mKainzbauer/weewx-ecowitt-multi

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program.  If not, see https://www.gnu.org/licenses/.

Version: 2.0.0

Revision History
    2.0.0 (12 November 2026)
        - built on Gary Roderick's ecowitt_http.py 0.1.0a28 as weewx-ecowittGateway:
          driver EcowittGateway, config section [EcowittGateway] ([EcowittHttp]
          still read)
        - fields 0x6D (windDir10), 0x7C (t_rain24/p_rain24) and 0x7D
          (t_rainhour/p_rainhour); battery and signal
          fields for every sensor the gateway reports; radiation_from_illuminance;
          pm2_5 from a WH45/WH46 without a channel 1 PM2.5 sensor
        - [[sensor_map]], ecwLoop.json/MQTT export (EcowittGatewayExport service),
          the GW1000 via the TCP API, [[limits]], ignore_no_signal,
          gateway_online, catchup checks and the sensor map for catchup records
        - several gateways ([[sources]], after weewx-ecowitt-multi by mKainzbauer):
          priority merge, totals merged so WeeWX counts rain and lightning once,
          restart for catchup after an outage of every gateway, merged catchup
        - logging options, --active-map, --dump-api, --sd-files, --sources,
          --catchup-dry-run; [Accumulator] entries made from the field map; a
          warning when WeeWX's rain rule names a total the driver doesn't produce
        - the per-gateway code in one class (GatewaySource), the API parser
          rewritten with shared helpers, a test suite
        - fixes to ecowitt_http.py 0.1.0a28: see CHANGELOG.md
    X May 2025            v0.1.0
        - initial release


This driver is based on the Ecowitt local HTTP API. At the time of release the
following sensors are supported:

WN31        temperature, humidity, signal level, battery state. channels 1-8
            inclusive
WN32P       temperature, humidity, pressure, signal level, battery state.
            single device only
WN32        (non-WH32P models) temperature, humidity, signal level, battery
            state. single device only
WN34        temperature, signal level, battery state. channels 1-8 inclusive
WN35        leaf wetness, signal level, battery state. channels 1-8 inclusive
WH41/43:    PM2.5, 24-hour average PM2.5, signal level, battery state.
            Channels 1-4 inclusive
WH45        CO2, PM2.5, PM10, signal level, battery state. single device only
WH46
WH54
WH55
WH57        , signal level, battery state. single device only
WS80        temperature, humidity, wind speed, wind direction, illuminance,
            UV index, signal level, battery state. single device only
WS85        piezo rain, wind speed, wind direction, signal level, battery
            state. single device only
WS90        temperature, humidity, piezo rain, wind speed, wind direction,
            illuminance, UV index, signal level, battery state. single device
            only

The Ecowitt local HTTP API is poorly documented with the documentation largely
consisting of a dated (circa April 2022) example get_livedata_info API command
response. Other API commands have been published informally on wxforum.net but
no documentation for the use of the commands or the interpretation of the
device responses exists.

The following deviations have been made from the known 'documentation':

-   common list field 3. The example 'get_livedata_info' includes a common_list
    field with id '3' that is a temperature, but give no explanation as to what
    this field is. When using the WS View+ app this field appears to populate
    the 'Feels Like' display. Therefore the Ecowitt HTTP driver assumes
    common_list field '3' is the feels like temperature.

-   HTTP API command get_ws_services response does not populate the 'mqtt_name'
    field. In WS View+ the MQTT 'Client Name' and 'Username' fields mirror each
    other (ie populating one populates the other with the same data); however,
    the get_ws_services response 'mqtt_name' field is always an empty string.

-   Ecowitt continues to chop and change sensor model numbers; eg WH31 is
    referred to as both WH31 and WN31 on shop.ecowitt.com and the sensor manual
    and is referred to as WH31 in Ecowitt HTTP API responses. Similarly for
    WH32. WN34 and WN35 sensors are referred to as WN34 and WN35 on
    shop.ecowitt.com and the sensor manual, but the HTTP API responses refer
    to WH34 and WH35. Like for LDS01 and WH54. To provide consistency this
    driver uses the following sensor model numbers throughout:
    -   WN31 in lieu of WH31
    -   WN32 in lieu of WH32
    -   WN34 in lieu of WH34
    -   WN35 in lieu of WH35
    -   WN36 in lieu of WH36
    -   WS80 in lieu of WH80
    -   WS85 in lieu of WH85
    -   WS90 in lieu of WH90
    Note: Where Ecowitt HTTP API commands contain a legacy model number,
          eg 'get_cli_wh34' the use of the legacy model is retained.
"""

# Python imports
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import calendar
import collections
import csv
import datetime
import io
import json
import logging
import math
import operator
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

# WeeWX imports
import weecfg
import weeutil.weeutil
import weeutil.logger
import weewx.defaults
import weewx.drivers
import weewx.engine
import weewx.manager
import weewx.units
import weewx.xtypes
import weewx.wxformulas
from weeutil.weeutil import bcolors, timestamp_to_string

log = logging.getLogger(__name__)


DRIVER_NAME = 'EcowittGateway'
DRIVER_VERSION = '2.0.0'
# config section used by ecowitt_http.py; read if there is no [EcowittGateway]
LEGACY_DRIVER_NAME = 'EcowittHttp'
# this module, as WeeWX loads it
MODULE = 'user.weewx-ecowittGateway'

# device models that are supported by the driver
SUPPORTED_DEVICES = ('GW1000', 'GW1100', 'GW1200', 'GW2000',
                     'GW3000', 'WH2650', 'WH2680',
                     'WN1900', 'WS3900', 'WS3910')
# devices with only the binary TCP API (no local HTTP API), supported via the
# TCP API
TCP_ONLY_DEVICES = ('GW1000',)
# device models that are not supported by the driver
UNSUPPORTED_DEVICES = ()
# device models that we know about
KNOWN_DEVICES = SUPPORTED_DEVICES + UNSUPPORTED_DEVICES
# sensors we know about
KNOWN_SENSORS = ('wh25', 'wh26', 'wn31', 'wn34', 'wn35',
                 'wh40', 'wh41', 'wh45',
                 'wh51', 'wh54', 'wh55', 'wh57',
                 'wh65', 'wh68', 'wh69', 'ws80', 'ws85', 'ws90')
# default max number of attempts to obtain data from the device
DEFAULT_MAX_TRIES = 3
# default wait time between retries when attempting to obtain data from the
# device
DEFAULT_RETRY_WAIT = 2
# default timeout when fetching data from a URL
DEFAULT_URL_TIMEOUT = 3
# default grace period after last_good_ts/start_ts after which we accept
# catchup records
DEFAULT_CATCHUP_GRACE = 0
# default max tries when downloading a catchup file
DEFAULT_CATCHUP_RETRIES = 3
# the port the device serves its SD card history files on
DEFAULT_SD_FILE_PORT = 81
# When run as a service the default age in seconds after which API data is
# considered stale and will not be used to augment loop packets
DEFAULT_MAX_AGE = 60
# default device poll interval
DEFAULT_POLL_INTERVAL = 20
# default discovery port
DEFAULT_DISCOVERY_PORT = 59387
# default discovery listening period
DEFAULT_DISCOVERY_PERIOD = 5
# default discovery timeout period
DEFAULT_DISCOVERY_TIMEOUT = 2
# default period between lost contact log entries during an extended period of
# lost contact when run as a Service
DEFAULT_LOST_CONTACT_LOG_PERIOD = 21600
# default loop packet unit system emitted by the driver
DEFAULT_UNIT_SYSTEM = weewx.METRICWX
# which device API to use: 'auto', 'http' or 'tcp' (GW1000)
DEFAULT_API = 'auto'
# TCP API port
DEFAULT_TCP_PORT = 45000
# default battery state filtering (whether to only display battery state data
# for connected sensors)
DEFAULT_FILTER_BATTERY = False
# default firmware update check interval
DEFAULT_FW_CHECK_INTERVAL = 86400
# The HTTP API will return some sensor data (usually meta data) for sensors
# that are 'unregistered' or 'learning' as well as 'registered' sensors. The
# default is to only accept data from registered sensors (True).
DEFAULT_ONLY_REGISTERED_SENSORS = True
# whether 'Next update in N seconds' is logged after each poll when debugging
DEFAULT_LOG_NEXT_UPDATE = True

# define the WeeWX unit group used by each device field
DEFAULT_GROUPS = {
    'datetime': 'group_time',
    'common_list.0x02.val': 'group_temperature',
    'common_list.0x02.battery': 'group_count',
    'common_list.0x02.voltage': 'group_volt',
    'common_list.0x03.val': 'group_temperature',
    'common_list.0x03.battery': 'group_count',
    'common_list.0x03.voltage': 'group_volt',
    'common_list.3.val': 'group_temperature',
    'common_list.3.battery': 'group_count',
    'common_list.3.voltage': 'group_volt',
    'common_list.0x04.val': 'group_temperature',
    'common_list.0x04.battery': 'group_count',
    'common_list.0x04.voltage': 'group_volt',
    'common_list.4.val': 'group_temperature',
    'common_list.4.battery': 'group_count',
    'common_list.4.voltage': 'group_volt',
    'common_list.0x05.val': 'group_temperature',
    'common_list.0x05.battery': 'group_count',
    'common_list.0x05.voltage': 'group_volt',
    'common_list.5.val': 'group_pressure',
    'common_list.5.battery': 'group_count',
    'common_list.5.voltage': 'group_volt',
    'common_list.0x07.val': 'group_percent',
    'common_list.0x07.battery': 'group_count',
    'common_list.0x07.voltage': 'group_volt',
    'common_list.0x0A.val': 'group_direction',
    'common_list.0x6D.val': 'group_direction',
    'common_list.0x0A.battery': 'group_count',
    'common_list.0x0A.voltage': 'group_volt',
    'common_list.0x0B.val': 'group_speed',
    'common_list.0x0B.battery': 'group_count',
    'common_list.0x0B.voltage': 'group_volt',
    'common_list.0x0C.val': 'group_speed',
    'common_list.0x0C.battery': 'group_count',
    'common_list.0x0C.voltage': 'group_volt',
    'common_list.0x0F.val': 'group_speed',
    'common_list.0x0F.battery': 'group_count',
    'common_list.0x0F.voltage': 'group_volt',
    'common_list.0x14.val': 'group_speed',
    'common_list.0x14.battery': 'group_count',
    'common_list.0x14.voltage': 'group_volt',
    'common_list.0x15.val': 'group_illuminance',
    'common_list.0x15.battery': 'group_count',
    'common_list.0x15.voltage': 'group_volt',
    'common_list.0x16.val': 'group_radiation',
    'common_list.0x16.battery': 'group_count',
    'common_list.0x16.voltage': 'group_volt',
    'common_list.0x17.val': 'group_uv',
    'common_list.0x17.battery': 'group_count',
    'common_list.0x17.voltage': 'group_volt',
    'common_list.0x19.val': 'group_speed',
    'common_list.0x19.battery': 'group_count',
    'common_list.0x19.voltage': 'group_volt',
    'rain.0x0D.val': 'group_rain',
    'rain.0x0D.battery': 'group_count',
    'rain.0x0D.voltage': 'group_volt',
    'rain.0x0E.val': 'group_rainrate',
    'rain.0x0E.battery': 'group_count',
    'rain.0x0E.voltage': 'group_volt',
    'rain.0x10.val': 'group_rain',
    'rain.0x7C.val': 'group_rain',
    'rain.0x7D.val': 'group_rain',
    'rain.0x10.battery': 'group_count',
    'rain.0x10.voltage': 'group_volt',
    'rain.0x11.val': 'group_rain',
    'rain.0x11.battery': 'group_count',
    'rain.0x11.voltage': 'group_volt',
    'rain.0x12.val': 'group_rain',
    'rain.0x12.battery': 'group_count',
    'rain.0x12.voltage': 'group_volt',
    'rain.0x13.val': 'group_rain',
    't_rain': 'group_rain',
    #    'rain.0x13.battery': 'group_count',
    'rain.0x13.voltage': 'group_volt',
    'piezoRain.srain_piezo.val': 'group_boolean',
    'piezoRain.0x0D.val': 'group_rain',
    'piezoRain.0x0D.battery': 'group_count',
    'piezoRain.0x0D.voltage': 'group_volt',
    'piezoRain.0x0E.val': 'group_rainrate',
    'piezoRain.0x0E.battery': 'group_count',
    'piezoRain.0x0E.voltage': 'group_volt',
    'piezoRain.0x10.val': 'group_rain',
    'piezoRain.0x7C.val': 'group_rain',
    'piezoRain.0x7D.val': 'group_rain',
    'piezoRain.0x10.battery': 'group_count',
    'piezoRain.0x10.voltage': 'group_volt',
    'piezoRain.0x11.val': 'group_rain',
    'piezoRain.0x11.battery': 'group_count',
    'piezoRain.0x11.voltage': 'group_volt',
    'piezoRain.0x12.val': 'group_rain',
    'piezoRain.0x12.battery': 'group_count',
    'piezoRain.0x12.voltage': 'group_volt',
    'piezoRain.0x13.val': 'group_rain',
    'piezoRain.0x13.voltage': 'group_volt',
    'piezoRain.srain_piezo': 'group_boolean',
    'p_rain': 'group_rain',
    'wh25.intemp': 'group_temperature',
    'wh25.inhumi': 'group_percent',
    'wh25.abs': 'group_pressure',
    'wh25.rel': 'group_pressure',
    'wh25.CO2': 'group_fraction',
    'wh25.CO2_24H': 'group_fraction',
    'lightning.distance': 'group_distance',
    'lightning.timestamp': 'group_time',
    'lightning.count': 'group_count',
    'co2.temp': 'group_temperature',
    'co2.humidity': 'group_percent',
    'co2.PM25': 'group_concentration',
    'co2.PM25_RealAQI': 'group_count',
    'co2.PM25_24HAQI': 'group_count',
    'co2.PM10': 'group_concentration',
    'co2.PM10_RealAQI': 'group_count',
    'co2.PM10_24HAQI': 'group_count',
    'co2.CO2': 'group_fraction',
    'co2.CO2_24H': 'group_fraction',
    'co2.PM25_24H': 'group_concentration',
    'co2.PM10_24H': 'group_concentration',
    **{f'co2.{pm}{suffix}': group for pm in ('PM1', 'PM4')
       for suffix, group in (('', 'group_concentration'), ('_24H', 'group_concentration'),
                             ('_RealAQI', 'group_count'), ('_24HAQI', 'group_count'))},
    **{f'ch_pm25.{ch}.PM25_24H': 'group_concentration' for ch in range(1, 5)},
#    'co2.battery': 'group_count',
    'ch_pm25.1.PM25': 'group_concentration',
    'ch_pm25.1.PM25_RealAQI': 'group_count',
    'ch_pm25.1.PM25_24HAQI': 'group_count',
    'ch_pm25.2.PM25': 'group_concentration',
    'ch_pm25.2.PM25_RealAQI': 'group_count',
    'ch_pm25.2.PM25_24HAQI': 'group_count',
    'ch_pm25.3.PM25': 'group_concentration',
    'ch_pm25.3.PM25_RealAQI': 'group_count',
    'ch_pm25.3.PM25_24HAQI': 'group_count',
    'ch_pm25.4.PM25': 'group_concentration',
    'ch_pm25.4.PM25_RealAQI': 'group_count',
    'ch_pm25.4.PM25_24HAQI': 'group_count',
    'ch_leak.1.status': 'group_count',
    'ch_leak.2.status': 'group_count',
    'ch_leak.3.status': 'group_count',
    'ch_leak.4.status': 'group_count',
    'ch_aisle.1.temp': 'group_temperature',
    'ch_aisle.1.humidity': 'group_percent',
    'ch_aisle.2.temp': 'group_temperature',
    'ch_aisle.2.humidity': 'group_percent',
    'ch_aisle.3.temp': 'group_temperature',
    'ch_aisle.3.humidity': 'group_percent',
    'ch_aisle.4.temp': 'group_temperature',
    'ch_aisle.4.humidity': 'group_percent',
    'ch_aisle.5.temp': 'group_temperature',
    'ch_aisle.5.humidity': 'group_percent',
    'ch_aisle.6.temp': 'group_temperature',
    'ch_aisle.6.humidity': 'group_percent',
    'ch_aisle.7.temp': 'group_temperature',
    'ch_aisle.7.humidity': 'group_percent',
    'ch_aisle.8.temp': 'group_temperature',
    'ch_aisle.8.humidity': 'group_percent',
    'ch_soil.1.humidity': 'group_percent',
    'ch_soil.1.voltage': 'group_volt',
    'ch_soil.2.humidity': 'group_percent',
    'ch_soil.2.voltage': 'group_volt',
    'ch_soil.3.humidity': 'group_percent',
    'ch_soil.3.voltage': 'group_volt',
    'ch_soil.4.humidity': 'group_percent',
    'ch_soil.4.voltage': 'group_volt',
    'ch_soil.5.humidity': 'group_percent',
    'ch_soil.5.voltage': 'group_volt',
    'ch_soil.6.humidity': 'group_percent',
    'ch_soil.6.voltage': 'group_volt',
    'ch_soil.7.humidity': 'group_percent',
    'ch_soil.7.voltage': 'group_volt',
    'ch_soil.8.humidity': 'group_percent',
    'ch_soil.8.voltage': 'group_volt',
    'ch_soil.9.humidity': 'group_percent',
    'ch_soil.9.voltage': 'group_volt',
    'ch_soil.10.humidity': 'group_percent',
    'ch_soil.10.voltage': 'group_volt',
    'ch_soil.11.humidity': 'group_percent',
    'ch_soil.11.voltage': 'group_volt',
    'ch_soil.12.humidity': 'group_percent',
    'ch_soil.12.voltage': 'group_volt',
    'ch_soil.13.humidity': 'group_percent',
    'ch_soil.13.voltage': 'group_volt',
    'ch_soil.14.humidity': 'group_percent',
    'ch_soil.14.voltage': 'group_volt',
    'ch_soil.15.humidity': 'group_percent',
    'ch_soil.15.voltage': 'group_volt',
    'ch_soil.16.humidity': 'group_percent',
    'ch_soil.16.voltage': 'group_volt',
    'ch_temp.1.temp': 'group_temperature',
    'ch_temp.1.voltage': 'group_volt',
    'ch_temp.2.temp': 'group_temperature',
    'ch_temp.2.voltage': 'group_volt',
    'ch_temp.3.temp': 'group_temperature',
    'ch_temp.3.voltage': 'group_volt',
    'ch_temp.4.temp': 'group_temperature',
    'ch_temp.4.voltage': 'group_volt',
    'ch_temp.5.temp': 'group_temperature',
    'ch_temp.5.voltage': 'group_volt',
    'ch_temp.6.temp': 'group_temperature',
    'ch_temp.6.voltage': 'group_volt',
    'ch_temp.7.temp': 'group_temperature',
    'ch_temp.7.voltage': 'group_volt',
    'ch_temp.8.temp': 'group_temperature',
    'ch_temp.8.voltage': 'group_volt',
    'ch_leaf.1.humidity': 'group_percent',
    'ch_leaf.2.humidity': 'group_percent',
    'ch_leaf.3.humidity': 'group_percent',
    'ch_leaf.4.humidity': 'group_percent',
    'ch_leaf.5.humidity': 'group_percent',
    'ch_leaf.6.humidity': 'group_percent',
    'ch_leaf.7.humidity': 'group_percent',
    'ch_leaf.8.humidity': 'group_percent',
    'ch_lds.1.air': 'group_depth',
    'ch_lds.1.depth': 'group_depth',
    'ch_lds.1.heat': 'group_count',
    'ch_lds.1.voltage': 'group_volt',
    'ch_lds.2.air': 'group_depth',
    'ch_lds.2.depth': 'group_depth',
    'ch_lds.2.heat': 'group_count',
    'ch_lds.2.voltage': 'group_volt',
    'ch_lds.3.air': 'group_depth',
    'ch_lds.3.depth': 'group_depth',
    'ch_lds.3.heat': 'group_count',
    'ch_lds.3.voltage': 'group_volt',
    'ch_lds.4.air': 'group_depth',
    'ch_lds.4.depth': 'group_depth',
    'ch_lds.4.heat': 'group_count',
    'ch_lds.4.voltage': 'group_volt',
    'debug.heap': 'group_data',
    'debug.runtime': 'group_deltatime',
    'debug.usr_interval': 'group_deltatime',
    'debug.is_cnip': 'group_boolean',
    'wh26.battery': 'group_count',
    'wh25.battery': 'group_count',
    'wh24.battery': 'group_count',
    'wh65.battery': 'group_count',
    'wh69.battery': 'group_count',
    'wn32.battery': 'group_count',
    'wn32p.battery': 'group_count',
    'wn31.ch1.battery': 'group_count',
    'wn31.ch2.battery': 'group_count',
    'wn31.ch3.battery': 'group_count',
    'wn31.ch4.battery': 'group_count',
    'wn31.ch5.battery': 'group_count',
    'wn31.ch6.battery': 'group_count',
    'wn31.ch7.battery': 'group_count',
    'wn31.ch8.battery': 'group_count',
    'wn34.ch1.battery': 'group_count',
    'wn34.ch2.battery': 'group_count',
    'wn34.ch3.battery': 'group_count',
    'wn34.ch4.battery': 'group_count',
    'wn34.ch5.battery': 'group_count',
    'wn34.ch6.battery': 'group_count',
    'wn34.ch7.battery': 'group_count',
    'wn34.ch8.battery': 'group_count',
    'wn35.ch1.battery': 'group_count',
    'wn35.ch2.battery': 'group_count',
    'wn35.ch3.battery': 'group_count',
    'wn35.ch4.battery': 'group_count',
    'wn35.ch5.battery': 'group_count',
    'wn35.ch6.battery': 'group_count',
    'wn35.ch7.battery': 'group_count',
    'wn35.ch8.battery': 'group_count',
    'wh40.battery': 'group_count',
    'wh41.ch1.battery': 'group_count',
    'wh41.ch2.battery': 'group_count',
    'wh41.ch3.battery': 'group_count',
    'wh41.ch4.battery': 'group_count',
    'wh45.battery': 'group_count',
    'wh51.ch1.battery': 'group_count',
    'wh51.ch2.battery': 'group_count',
    'wh51.ch3.battery': 'group_count',
    'wh51.ch4.battery': 'group_count',
    'wh51.ch5.battery': 'group_count',
    'wh51.ch6.battery': 'group_count',
    'wh51.ch7.battery': 'group_count',
    'wh51.ch8.battery': 'group_count',
    'wh51.ch9.battery': 'group_count',
    'wh51.ch10.battery': 'group_count',
    'wh51.ch11.battery': 'group_count',
    'wh51.ch12.battery': 'group_count',
    'wh51.ch13.battery': 'group_count',
    'wh51.ch14.battery': 'group_count',
    'wh51.ch15.battery': 'group_count',
    'wh51.ch16.battery': 'group_count',
    'wh54.ch1.battery': 'group_count',
    'wh54.ch2.battery': 'group_count',
    'wh54.ch3.battery': 'group_count',
    'wh54.ch4.battery': 'group_count',
    'wh55.ch1.battery': 'group_count',
    'wh55.ch2.battery': 'group_count',
    'wh55.ch3.battery': 'group_count',
    'wh55.ch4.battery': 'group_count',
    'wh57.battery': 'group_count',
    'wh68.battery': 'group_count',
    'ws80.battery': 'group_count',
    'ws85.battery': 'group_count',
    'ws90.battery': 'group_count',
    'wh51.ch1.voltage': 'group_volt',
    'wh51.ch2.voltage': 'group_volt',
    'wh51.ch3.voltage': 'group_volt',
    'wh51.ch4.voltage': 'group_volt',
    'wh51.ch5.voltage': 'group_volt',
    'wh51.ch6.voltage': 'group_volt',
    'wh51.ch7.voltage': 'group_volt',
    'wh51.ch8.voltage': 'group_volt',
    'wh51.ch9.voltage': 'group_volt',
    'wh51.ch10.voltage': 'group_volt',
    'wh51.ch11.voltage': 'group_volt',
    'wh51.ch12.voltage': 'group_volt',
    'wh51.ch13.voltage': 'group_volt',
    'wh51.ch14.voltage': 'group_volt',
    'wh51.ch15.voltage': 'group_volt',
    'wh51.ch16.voltage': 'group_volt',
    'wh26.signal': 'group_count',
    'wh25.signal': 'group_count',
    'wh24.signal': 'group_count',
    'wh65.signal': 'group_count',
    'wh69.signal': 'group_count',
    'wn32.signal': 'group_count',
    'wn32p.signal': 'group_count',
    'wn31.ch1.signal': 'group_count',
    'wn31.ch2.signal': 'group_count',
    'wn31.ch3.signal': 'group_count',
    'wn31.ch4.signal': 'group_count',
    'wn31.ch5.signal': 'group_count',
    'wn31.ch6.signal': 'group_count',
    'wn31.ch7.signal': 'group_count',
    'wn31.ch8.signal': 'group_count',
    'wn34.ch1.signal': 'group_count',
    'wn34.ch2.signal': 'group_count',
    'wn34.ch3.signal': 'group_count',
    'wn34.ch4.signal': 'group_count',
    'wn34.ch5.signal': 'group_count',
    'wn34.ch6.signal': 'group_count',
    'wn34.ch7.signal': 'group_count',
    'wn34.ch8.signal': 'group_count',
    'wn35.ch1.signal': 'group_count',
    'wn35.ch2.signal': 'group_count',
    'wn35.ch3.signal': 'group_count',
    'wn35.ch4.signal': 'group_count',
    'wn35.ch5.signal': 'group_count',
    'wn35.ch6.signal': 'group_count',
    'wn35.ch7.signal': 'group_count',
    'wn35.ch8.signal': 'group_count',
    'wh40.signal': 'group_count',
    'wh41.ch1.signal': 'group_count',
    'wh41.ch2.signal': 'group_count',
    'wh41.ch3.signal': 'group_count',
    'wh41.ch4.signal': 'group_count',
    'wh45.signal': 'group_count',
    'wh51.ch1.signal': 'group_count',
    'wh51.ch2.signal': 'group_count',
    'wh51.ch3.signal': 'group_count',
    'wh51.ch4.signal': 'group_count',
    'wh51.ch5.signal': 'group_count',
    'wh51.ch6.signal': 'group_count',
    'wh51.ch7.signal': 'group_count',
    'wh51.ch8.signal': 'group_count',
    'wh51.ch9.signal': 'group_count',
    'wh51.ch10.signal': 'group_count',
    'wh51.ch11.signal': 'group_count',
    'wh51.ch12.signal': 'group_count',
    'wh51.ch13.signal': 'group_count',
    'wh51.ch14.signal': 'group_count',
    'wh51.ch15.signal': 'group_count',
    'wh51.ch16.signal': 'group_count',
    'wh54.ch1.signal': 'group_count',
    'wh54.ch2.signal': 'group_count',
    'wh54.ch3.signal': 'group_count',
    'wh54.ch4.signal': 'group_count',
    'wh55.ch1.signal': 'group_count',
    'wh55.ch2.signal': 'group_count',
    'wh55.ch3.signal': 'group_count',
    'wh55.ch4.signal': 'group_count',
    'wh57.signal': 'group_count',
    'wh68.signal': 'group_count',
    'ws80.signal': 'group_count',
    'ws85.signal': 'group_count',
    'ws90.signal': 'group_count'
}


# ============================================================================
#                            InvertibleMap classes
#
# Based on the implementation of an invertible (bijective) map at
# https://stackoverflow.com/a/34460187
# ============================================================================

class InvertibleSetError(Exception):
    """Must set a unique value in a InvertibleMap."""

    def __init__(self, value):
        self.value = value
        msg = 'The value "{}" is already in the mapping.'
        super().__init__(msg.format(value))


class InvertibleMap(dict):
    """Class implementing a basic invertible map.

    An invertible map operates as per a normal python dictionary. However,
    unlike a dictionary an InvertibleMap object can look up a dictionary key
    given the value for that key. This 'reverse lookup' is achieved by using
    the InvertibleMap objects 'inverse' property and the value concerned.

    For example, if inv_map is created from the dictionary my_dict as follows:

    my_dict = {'a': 1, 'b': 2, 'c': 3}
    inv_map = InvertibleMap(my_dict)

    then the following expressions return the values indicated:

    inv_map['a']
    1
    inv_map['b']
    2
    inv_map.inverse[2]
    b
    inv_map.inverse[3]
    c

    This means that an InvertibleDict object must have not only unique keys,
    but must have unique values as well. An InvertibleMap object supports all
    standard dictionary methods and properties.
    """

    def __init__(self, *args, inverse=None, **kwargs):
        super().__init__(*args, **kwargs)
        if inverse is None:
            _inv = dict()
            for key, value in self.items():
                _inv[value] = key
            inverse = self.__class__(_inv, inverse=self, **kwargs)
        self.inverse = inverse

    def __setitem__(self, key, value):
        if value in self.inverse:
            raise InvertibleSetError(value)

        self.inverse._set_item(value, key)
        self._set_item(key, value)

    def __delitem__(self, key):
        self.inverse._del_item(self[key])
        self._del_item(key)

    def _del_item(self, key):
        super().__delitem__(key)

    def _set_item(self, key, value):
        super().__setitem__(key, value)

    def pop(self, key):
        self.inverse._del_item(self[key])
        return super().pop(key)


# ============================================================================
#                 Ecowitt Local HTTP API driver error classes
# ============================================================================

class ServiceInitializationError(Exception):
    """Exception raised during initialization of an EcowittHttpService object."""

class UnknownApiCommand(Exception):
    """Exception raised when an unknown API command was selected."""


class DeviceIOError(Exception):
    """Exception raised when an input/output error with the device is
    encountered."""


class ParseError(Exception):
    """Exception raised when an error occurs parsing an Ecowitt HTTP API
    response."""


class ProcessorError(Exception):
    """Exception raised when an error occurs processing an Ecowitt HTTP API
    response."""


class UnitError(Exception):
    """Exception raised when an error occurs determining the unit used by an
    observation."""


class CatchupObjectError(Exception):
    """Exception raised when an Ecowitt catchup object cannot be obtained."""


class InvalidApiResponseError(Exception):
    """Exception raised when an invalid API response is received."""


class ApiResponseError(Exception):
    """Exception raised when an API response error code is received."""


# ============================================================================
#                             class DebugOptions
# ============================================================================

class DebugOptions:
    """Class to simplify use and handling of device debug options."""

    debug_groups = ('rain', 'wind', 'loop', 'sensors', 'parser',
                    'catchup', 'collector', 'merge')

    def __init__(self, **config):
        debug_list = weeutil.weeutil.option_as_list(config.get('debug',
                                                               list()))
        # a quoted 'debug = "rain, wind"' is one string, not a list
        if isinstance(debug_list, list) and len(debug_list) == 1 and isinstance(debug_list[0], str):
            debug_list = debug_list[0].split(',')
        try:
            lower_debug_list = [d.strip().lower() for d in debug_list]
        except AttributeError:
            lower_debug_list = list()
        # rain
        self._debug_rain = 'rain' in lower_debug_list
        # wind
        self._debug_wind = 'wind' in lower_debug_list
        # loop data
        self._debug_loop = 'loop' in lower_debug_list
        # sensors
        self._debug_sensors = 'sensors' in lower_debug_list
        # parser
        self._debug_parser = 'parser' in lower_debug_list
        # catchup
        self._debug_catchup = 'catchup' in lower_debug_list
        # collector
        self._debug_collector = 'collector' in lower_debug_list
        # merging several gateways: which gateway each field came from
        self._debug_merge = 'merge' in lower_debug_list

    @property
    def rain(self):
        """Are we debugging rain data processing."""

        return self._debug_rain

    @property
    def wind(self):
        """Are we debugging wind data processing."""

        return self._debug_wind

    @property
    def loop(self):
        """Are we debugging loop data processing."""

        return self._debug_loop

    @property
    def sensors(self):
        """Are we debugging sensor processing."""

        return self._debug_sensors

    @property
    def parser(self):
        """Are we debugging the parser."""

        return self._debug_parser

    @property
    def catchup(self):
        """Are we debugging the catchup."""

        return self._debug_catchup

    @property
    def collector(self):
        """Are we debugging the collector."""

        return self._debug_collector

    @property
    def merge(self):
        """Are we logging which gateway each merged field came from."""

        return self._debug_merge

    @property
    def any(self):
        """Are we performing any debugging."""

        for debug_group in self.debug_groups:
            if getattr(self, debug_group):
                return True
        return False


# ============================================================================
#                              class FieldMapper
# ============================================================================

class FieldMapper:
    """Base class for mapping of dictionary data to new fields.

    Mapping of dictionary data is required to, for example, translate API field
    names to WeeWX archive field names. Field maps are defined in a config
    stanza as follows:

    [field_map]
        destination field = source field

    where:

        source field is the source field, if the source field data exists in a
        record the source field must be a key in the source data dict

        destination field is the destination field, if corresponding valid
        source field data exists the destination field will be a key in the
        mapped data dict

    The field map definition is contained in the field_map key in the config
    dict passed to the FieldMapper initialisation.

    A FieldMapper object also supports extensions to a field map. Field map
    extensions are identical to map definitions; however, field map extensions
    are used to extend/change an existing field map rather than define a new
    field map. Field map extensions are defined in a config stanza as follows:

    [field_map_extensions]
        destination field = source field

    where:

        source field is the source field, if the source field data exists in a
        record the source field must be a key in the source data dict

        destination field is the destination field, if corresponding valid
        source field data exists the destination field will be a key in the
        mapped data dict

    Field map extensions are not additive, that is, field map extensions
    definitions will change the mapping for a given source field rather than
    adding an additional mapping for that source field. In other words, a
    source field can only be mapped to a single destination field.

    Parameters:

        default_map: optional dict containing the default field map to be used
                     if no field map is provided
        debug: optional DebugOptions object containing debug options
        mapper_config: optional dict containing, among others, the field map and
                       field map extensions definitions

    station_columns ({station database column: (driver fields)}, set by
    use_station_columns()) renames, in mapped data, the first of the driver
    fields present to the station database column.
    """

    station_columns = {}

    def __init__(self, driver_debug=None, default_map=None, **mapper_config):
        """Initialise a FieldMapper object."""

        self.driver_debug = driver_debug
        # Depending on the model a WN32 TH sensor can override/provide indoor
        # and/or outdoor TH data and outdoor pressure data to the device. The
        # use of WN32 data by the device is transparent to the driver does not
        # need to know what type of sensor (WN32 or something else) is
        # providing this data. However, in terms of battery state the driver
        # needs to know the sensor type so that sensor battery and signal state
        # data can be reported against the correct sensor type.
        self.wn32_indoor = weeutil.weeutil.tobool(mapper_config.get('wn32_indoor', False))
        self.wn32_outdoor = weeutil.weeutil.tobool(mapper_config.get('wn32_outdoor', False))
        # obtain the default map we are to use
        def_map = default_map if default_map is not None else dict()
        self.field_map = self.construct_field_map(def_map=def_map,
                                                  **mapper_config)

    def construct_field_map(self, def_map, **config):
        """Construct a field map given a default field map and field map config.

        Returns a field map as defined by the field map definition and then
        modified by the field map extensions definition. If no field map
        definition exists the default map modified by the field map extensions
        definition is returned. If no field map or field map extensions
        definitions exist the default map is returned.

        Returns an InvertibleMap object.

    default_sensor_state_map = {
        'wh25_batt': 'wh25.battery',
        'wh25_sig': 'wh25.signal',
        'wh26_batt': 'wh26.battery',
        'wh26_sig': 'wh26.signal',
        'wn31_ch1_batt': 'wn31.ch1.battery',
        'wn31_ch1_sig': 'wn31.ch1.signal',



        """

        # Update the default map given the wn32 indoor/outdoor config. First
        # make sure the default map is an InvertibleMap object.
        default_map = InvertibleMap(def_map)
        if self.wn32_indoor:
            for source in ('wh25.battery', 'wh25.signal'):
                # is the source field in the default map
                if source in default_map.values():
                    # the source field is in the default map, obtain the
                    # current dest field
                    dest_field = default_map.inverse[source]
                    _ = default_map.pop(dest_field)
                    new_dest_field = dest_field.replace('wh25', 'wn32')
                    default_map[new_dest_field] = source
        if self.wn32_outdoor:
            for source in ('wh26.battery', 'wh26.signal'):
                # is the source field in the default map
                if source in default_map.values():
                    # the source field is in the default map, obtain the
                    # current dest field
                    dest_field = default_map.inverse[source]
                    _ = default_map.pop(dest_field)
                    new_dest_field = dest_field.replace('wh26', 'wn32p')
                    default_map[new_dest_field] = source
        field_map = config.get('field_map')
        # if no map was provided use the default
        if field_map is None:
            field_map = dict(default_map)

        # If a user wishes to map a device field differently to that in the
        # default map they can include an entry in field_map_extensions, but if
        # we just update the field map dict with the field map extensions that
        # will leave two entries for that device field in the field map; the
        # original field map entry as well as the entry from the extended map.
        # So if we have field_map_extensions we need to first go through the
        # field map and delete any entries that map device fields that are
        # included in the field_map_extensions.

        extensions = config.get('field_map_extensions', {})
        # we only need process the field_map_extensions if we have any
        if len(extensions) > 0:
            # first make a copy of the field map because we will be iterating
            # over it and likely changing it
            field_map_copy = dict(field_map)
            for k, v in field_map_copy.items():
                # if the 'value' (ie the device field) is in the field map
                # extensions we will be mapping that device field elsewhere so
                # pop that field map entry out of the field map so we don't end
                # up with multiple mappings for a device field
                if v in extensions.values():
                    # pop the field map entry
                    _dummy = field_map.pop(k)
            field_map.update(extensions)
        # we now have our final field map, ensuring it is an InvertibleMap
        return InvertibleMap(field_map)

    def map_data(self, rec, unit_system=None):
        """Map a dict of data according to the field map.

        Use the field map to map a packet of data. Typically used to produce a
        WeeWX loop packet.

        rec:         dict of source data to be mapped
        unit_system: optional unit system value to be included in the mapped
                     data 'usUnits' field. Note will override any source
                     data/field mapping that would result in source data being
                     mapped to the 'usUnits' field.
        """

        if self.field_map is not None and len(self.field_map) > 0:
            # we have a field map

            mapped_data = {}
            for dest_field, source_field in self.field_map.items():
                # if the field to be mapped exists in the data obtain its
                # value and map it to the packet
                if source_field in rec:
                    mapped_data[dest_field] = rec.get(source_field)
            if unit_system is not None:
                mapped_data['usUnits'] = unit_system
            return self.to_station_columns(mapped_data)
        else:
            return rec

    @classmethod
    def to_station_columns(cls, data):
        """Rename the fields of data that go to a station database column, or
        for a battery level, add the status (see use_station_columns()).
        Changes and returns data."""

        for column, fields in cls.station_columns.items():
            for field in fields:
                if field in data:
                    if column in BATTERY_LEVEL_COLUMNS:
                        data[column] = battery_status(data[field])
                    else:
                        data[column] = data.pop(field)
                    break
        return data


# ============================================================================
#                              class HttpMapper
# ============================================================================

class HttpMapper(FieldMapper):
    """Class for mapping data obtained from a device using the HTTP API.

    The HttpMapper uses a dictionary based map to map 'dotted' HTTP API fields
    to loop packet fields emitted by the Ecowitt HTTP driver/service. The map
    is produced as follows:

    - the base map is obtained from the 'default_map' initialisation parameter
      if non-None, otherwise the HttpMapper.default_map map is used as the base
      map
    - if the initialisation dict contains a 'field_map' entry the base map is
      replaced with the initialisation dict 'field_map'
    - if the initialisation dict contains a 'field_map_extensions' entry the
      base map is extended by updating it with the initialisation dict
      'field_map_extensions'

    Construction of the field map is handled by my super class initialisation.

    The resulting map must contain the map entry 'dateTime': 'datetime'. If
    this entry is missing, or the field names altered in the final map, the
    entry is removed (if applicable) and the map entry 'dateTime': 'datetime'
    inserted.

    The default field map (HttpMapper.default_map) is constructed from a number
    of modular maps to facilitate easier debug logging. The default map is used
    as the base map for mapping of HTTP API sensor data to WeeWX loop packet
    fields. Field names in the WeeWX wview_extended schema are used where there
    is a direct correlation to the WeeWX wview_extended schema or
    weewx.units.obs_group_dict (eg outTemp). If there is a related but
    different field in the wview_extended schema then a WeeWX field name with a
    similar format is used (eg extraTemp9). Otherwise, a suitable unique name
    is used.

    Field map format is:

        WeeWX field name: Device field name
    """

    # modular observation map
    default_obs_map = {
        'inTemp': 'wh25.intemp',
        'inHumidity': 'wh25.inhumi',
        'pressure': 'wh25.abs',
        'relbarometer': 'wh25.rel',
        'outTemp': 'common_list.0x02.val',
        'dewpoint': 'common_list.0x03.val',
        'feelslike': 'common_list.3.val',
        'appTemp': 'common_list.4.val',
        'vpd': 'common_list.5.val',
        'outHumidity': 'common_list.0x07.val',
        'illuminance': 'common_list.0x15.val',
        'uvradiation': 'common_list.0x16.val',
        'UV': 'common_list.0x17.val',
        'lightningdist': 'lightning.distance',
        'lightningdettime': 'lightning.timestamp',
        'lightningcount': 'lightning.count',
        'extraTemp1': 'ch_aisle.1.temp',
        'extraHumid1': 'ch_aisle.1.humidity',
        'extraTemp2': 'ch_aisle.2.temp',
        'extraHumid2': 'ch_aisle.2.humidity',
        'extraTemp3': 'ch_aisle.3.temp',
        'extraHumid3': 'ch_aisle.3.humidity',
        'extraTemp4': 'ch_aisle.4.temp',
        'extraHumid4': 'ch_aisle.4.humidity',
        'extraTemp5': 'ch_aisle.5.temp',
        'extraHumid5': 'ch_aisle.5.humidity',
        'extraTemp6': 'ch_aisle.6.temp',
        'extraHumid6': 'ch_aisle.6.humidity',
        'extraTemp7': 'ch_aisle.7.temp',
        'extraHumid7': 'ch_aisle.7.humidity',
        'extraTemp8': 'ch_aisle.8.temp',
        'extraHumid8': 'ch_aisle.8.humidity',
        'extraTemp9': 'ch_temp.1.temp',
        'extraTemp10': 'ch_temp.2.temp',
        'extraTemp11': 'ch_temp.3.temp',
        'extraTemp12': 'ch_temp.4.temp',
        'extraTemp13': 'ch_temp.5.temp',
        'extraTemp14': 'ch_temp.6.temp',
        'extraTemp15': 'ch_temp.7.temp',
        'extraTemp16': 'ch_temp.8.temp',
        'co2': 'co2.CO2',
        'co2_24h_avg': 'co2.CO2_24H',
        'co2_temp': 'co2.temp',
        'co2_humidity': 'co2.humidity',
        'pm2_55': 'co2.PM25',
        'pm2_55_24h_avg': 'co2.PM25_24H',
        'pm25_co2_real': 'co2.PM25_RealAQI',
        'pm25_co2_24h': 'co2.PM25_24HAQI',
        'pm10_0': 'co2.PM10',
        'pm10_24h_avg': 'co2.PM10_24H',
        'pm10_co2_real': 'co2.PM10_RealAQI',
        'pm10_co2_24h': 'co2.PM10_24HAQI',
        'pm1_0': 'co2.PM1',
        'pm1_0_24h_avg': 'co2.PM1_24H',
        'pm1_co2_real': 'co2.PM1_RealAQI',
        'pm1_co2_24h': 'co2.PM1_24HAQI',
        'pm4_0': 'co2.PM4',
        'pm4_0_24h_avg': 'co2.PM4_24H',
        'pm4_co2_real': 'co2.PM4_RealAQI',
        'pm4_co2_24h': 'co2.PM4_24HAQI',
        'co2_in': 'wh25.CO2',
        'co2_in_24h_avg': 'wh25.CO2_24H',
        'pm2_5': 'ch_pm25.1.PM25',
        'pm2_51_24h_avg': 'ch_pm25.1.PM25_24H',
        'pm25_ch1_real': 'ch_pm25.1.PM25_RealAQI',
        'pm25_ch1_24h': 'ch_pm25.1.PM25_24HAQI',
        'pm2_52': 'ch_pm25.2.PM25',
        'pm2_52_24h_avg': 'ch_pm25.2.PM25_24H',
        'pm25_ch2_real': 'ch_pm25.2.PM25_RealAQI',
        'pm25_ch2_24h': 'ch_pm25.2.PM25_24HAQI',
        'pm2_53': 'ch_pm25.3.PM25',
        'pm2_53_24h_avg': 'ch_pm25.3.PM25_24H',
        'pm25_ch3_real': 'ch_pm25.3.PM25_RealAQI',
        'pm25_ch3_24h': 'ch_pm25.3.PM25_24HAQI',
        'pm2_54': 'ch_pm25.4.PM25',
        'pm2_54_24h_avg': 'ch_pm25.4.PM25_24H',
        'pm25_ch4_real': 'ch_pm25.4.PM25_RealAQI',
        'pm25_ch4_24h': 'ch_pm25.4.PM25_24HAQI',
        'soilMoist1': 'ch_soil.1.humidity',
        'soilMoist2': 'ch_soil.2.humidity',
        'soilMoist3': 'ch_soil.3.humidity',
        'soilMoist4': 'ch_soil.4.humidity',
        'soilMoist5': 'ch_soil.5.humidity',
        'soilMoist6': 'ch_soil.6.humidity',
        'soilMoist7': 'ch_soil.7.humidity',
        'soilMoist8': 'ch_soil.8.humidity',
        'soilMoist9': 'ch_soil.9.humidity',
        'soilMoist10': 'ch_soil.10.humidity',
        'soilMoist11': 'ch_soil.11.humidity',
        'soilMoist12': 'ch_soil.12.humidity',
        'soilMoist13': 'ch_soil.13.humidity',
        'soilMoist14': 'ch_soil.14.humidity',
        'soilMoist15': 'ch_soil.15.humidity',
        'soilMoist16': 'ch_soil.16.humidity',
        'leafWet1': 'ch_leaf.1.humidity',
        'leafWet2': 'ch_leaf.2.humidity',
        'leafWet3': 'ch_leaf.3.humidity',
        'leafWet4': 'ch_leaf.4.humidity',
        'leafWet5': 'ch_leaf.5.humidity',
        'leafWet6': 'ch_leaf.6.humidity',
        'leafWet7': 'ch_leaf.7.humidity',
        'leafWet8': 'ch_leaf.8.humidity',
        'leak1': 'ch_leak.1.status',
        'leak2': 'ch_leak.2.status',
        'leak3': 'ch_leak.3.status',
        'leak4': 'ch_leak.4.status',
        'air1': 'ch_lds.1.air',
        'depth1': 'ch_lds.1.depth',
        'heat1': 'ch_lds.1.heat',
        'air2': 'ch_lds.2.air',
        'depth2': 'ch_lds.2.depth',
        'heat2': 'ch_lds.2.heat',
        'air3': 'ch_lds.3.air',
        'depth3': 'ch_lds.3.depth',
        'heat3': 'ch_lds.3.heat',
        'air4': 'ch_lds.4.air',
        'depth4': 'ch_lds.4.depth',
        'heat4': 'ch_lds.4.heat',
    }
    # modular rain map
    default_rain_map = {
        't_rainevent': 'rain.0x0D.val',
        'rainRate': 'rain.0x0E.val',
        't_rainhour': 'rain.0x7D.val',
        't_rainday': 'rain.0x10.val',
        't_rain24': 'rain.0x7C.val',
        't_rainweek': 'rain.0x11.val',
        't_rainmonth': 'rain.0x12.val',
        't_rainyear': 'rain.0x13.val',
        'is_raining': 'piezoRain.srain_piezo.val',
        'p_rainevent': 'piezoRain.0x0D.val',
        'p_rainrate': 'piezoRain.0x0E.val',
        'p_rainhour': 'piezoRain.0x7D.val',
        'p_rainday': 'piezoRain.0x10.val',
        'p_rain24': 'piezoRain.0x7C.val',
        'p_rainweek': 'piezoRain.0x11.val',
        'p_rainmonth': 'piezoRain.0x12.val',
        'p_rainyear': 'piezoRain.0x13.val'
    }
    # modular wind map
    default_wind_map = {
        'windDir': 'common_list.0x0A.val',
        'windDir10': 'common_list.0x6D.val',
        'windSpeed': 'common_list.0x0B.val',
        'windGust': 'common_list.0x0C.val',
        'daymaxwind': 'common_list.0x19.val',
    }
    # modular sensor state map
    default_sensor_state_map = {
        'wh25_batt': 'wh25.battery',
        'wh25_sig': 'wh25.signal',
        'wh26_batt': 'wh26.battery',
        'wh26_sig': 'wh26.signal',
        'wn31_ch1_batt': 'wn31.ch1.battery',
        'wn31_ch1_sig': 'wn31.ch1.signal',
        'wn31_ch2_batt': 'wn31.ch2.battery',
        'wn31_ch2_sig': 'wn31.ch2.signal',
        'wn31_ch3_batt': 'wn31.ch3.battery',
        'wn31_ch3_sig': 'wn31.ch3.signal',
        'wn31_ch4_batt': 'wn31.ch4.battery',
        'wn31_ch4_sig': 'wn31.ch4.signal',
        'wn31_ch5_batt': 'wn31.ch5.battery',
        'wn31_ch5_sig': 'wn31.ch5.signal',
        'wn31_ch6_batt': 'wn31.ch6.battery',
        'wn31_ch6_sig': 'wn31.ch6.signal',
        'wn31_ch7_batt': 'wn31.ch7.battery',
        'wn31_ch7_sig': 'wn31.ch7.signal',
        'wn31_ch8_batt': 'wn31.ch8.battery',
        'wn31_ch8_sig': 'wn31.ch8.signal',
        'wn34_ch1_batt': 'wn34.ch1.battery',
        'wn34_ch1_volt': 'ch_temp.1.voltage',
        'wn34_ch1_sig': 'wn34.ch1.signal',
        'wn34_ch2_batt': 'wn34.ch2.battery',
        'wn34_ch2_volt': 'ch_temp.2.voltage',
        'wn34_ch2_sig': 'wn34.ch2.signal',
        'wn34_ch3_batt': 'wn34.ch3.battery',
        'wn34_ch3_volt': 'ch_temp.3.voltage',
        'wn34_ch3_sig': 'wn34.ch3.signal',
        'wn34_ch4_batt': 'wn34.ch4.battery',
        'wn34_ch4_volt': 'ch_temp.4.voltage',
        'wn34_ch4_sig': 'wn34.ch4.signal',
        'wn34_ch5_batt': 'wn34.ch5.battery',
        'wn34_ch5_volt': 'ch_temp.5.voltage',
        'wn34_ch5_sig': 'wn34.ch5.signal',
        'wn34_ch6_batt': 'wn34.ch6.battery',
        'wn34_ch6_volt': 'ch_temp.6.voltage',
        'wn34_ch6_sig': 'wn34.ch6.signal',
        'wn34_ch7_batt': 'wn34.ch7.battery',
        'wn34_ch7_volt': 'ch_temp.7.voltage',
        'wn34_ch7_sig': 'wn34.ch7.signal',
        'wn34_ch8_batt': 'wn34.ch8.battery',
        'wn34_ch8_volt': 'ch_temp.8.voltage',
        'wn34_ch8_sig': 'wn34.ch8.signal',
        'wh41_ch1_batt': 'wh41.ch1.battery',
        'wh41_ch1_sig': 'wh41.ch1.signal',
        'wh41_ch2_batt': 'wh41.ch2.battery',
        'wh41_ch2_sig': 'wh41.ch2.signal',
        'wh41_ch3_batt': 'wh41.ch3.battery',
        'wh41_ch3_sig': 'wh41.ch3.signal',
        'wh41_ch4_batt': 'wh41.ch4.battery',
        'wh41_ch4_sig': 'wh41.ch4.signal',
        'wh51_ch1_batt': 'wh51.ch1.battery',
        'wh51_ch1_volt': 'ch_soil.1.voltage',
        'wh51_ch1_sig': 'wh51.ch1.signal',
        'wh51_ch2_batt': 'wh51.ch2.battery',
        'wh51_ch2_volt': 'ch_soil.2.voltage',
        'wh51_ch2_sig': 'wh51.ch2.signal',
        'wh51_ch3_batt': 'wh51.ch3.battery',
        'wh51_ch3_volt': 'ch_soil.3.voltage',
        'wh51_ch3_sig': 'wh51.ch3.signal',
        'wh51_ch4_batt': 'wh51.ch4.battery',
        'wh51_ch4_volt': 'ch_soil.4.voltage',
        'wh51_ch4_sig': 'wh51.ch4.signal',
        'wh51_ch5_batt': 'wh51.ch5.battery',
        'wh51_ch5_volt': 'ch_soil.5.voltage',
        'wh51_ch5_sig': 'wh51.ch5.signal',
        'wh51_ch6_batt': 'wh51.ch6.battery',
        'wh51_ch6_volt': 'ch_soil.6.voltage',
        'wh51_ch6_sig': 'wh51.ch6.signal',
        'wh51_ch7_batt': 'wh51.ch7.battery',
        'wh51_ch7_volt': 'ch_soil.7.voltage',
        'wh51_ch7_sig': 'wh51.ch7.signal',
        'wh51_ch8_batt': 'wh51.ch8.battery',
        'wh51_ch8_volt': 'ch_soil.8.voltage',
        'wh51_ch8_sig': 'wh51.ch8.signal',
        'wh54_ch1_batt': 'wh54.ch1.battery',
        'wh54_ch1_volt': 'ch_lds.1.voltage',
        'wh54_ch1_sig': 'wh54.ch1.signal',
        'wh54_ch2_batt': 'wh54.ch2.battery',
        'wh54_ch2_volt': 'ch_lds.2.voltage',
        'wh54_ch2_sig': 'wh54.ch2.signal',
        'wh54_ch3_batt': 'wh54.ch3.battery',
        'wh54_ch3_volt': 'ch_lds.3.voltage',
        'wh54_ch3_sig': 'wh54.ch3.signal',
        'wh54_ch4_batt': 'wh54.ch4.battery',
        'wh54_ch4_volt': 'ch_lds.4.voltage',
        'wh54_ch4_sig': 'wh54.ch4.signal',
        'wh55_ch1_batt': 'wh55.ch1.battery',
        'wh55_ch1_sig': 'wh55.ch1.signal',
        'wh55_ch2_batt': 'wh55.ch2.battery',
        'wh55_ch2_sig': 'wh55.ch2.signal',
        'wh55_ch3_batt': 'wh55.ch3.battery',
        'wh55_ch3_sig': 'wh55.ch3.signal',
        'wh55_ch4_batt': 'wh55.ch4.battery',
        'wh55_ch4_sig': 'wh55.ch4.signal',
        'wh57_batt': 'wh57.battery',
        'wh57_sig': 'wh57.signal',
        'ws90_batt': 'ws90.battery',
        'ws90_volt': 'piezoRain.0x13.voltage',
        'ws90_sig': 'ws90.signal',
        'wh40_batt': 'wh40.battery',
        'wh40_sig': 'wh40.signal',
        'wh45_batt': 'wh45.battery',
        'wh45_sig': 'wh45.signal',
        'wh68_batt': 'wh68.battery',
        'wh68_sig': 'wh68.signal',
        'wh69_batt': 'wh69.battery',
        'wh69_sig': 'wh69.signal',
        'ws80_batt': 'ws80.battery',
        'ws80_sig': 'ws80.signal',
        'ws85_batt': 'ws85.battery',
        'ws85_sig': 'ws85.signal',
        'wn35_ch1_batt': 'wn35.ch1.battery',
        'wn35_ch1_sig': 'wn35.ch1.signal',
        'wn35_ch2_batt': 'wn35.ch2.battery',
        'wn35_ch2_sig': 'wn35.ch2.signal',
        'wn35_ch3_batt': 'wn35.ch3.battery',
        'wn35_ch3_sig': 'wn35.ch3.signal',
        'wn35_ch4_batt': 'wn35.ch4.battery',
        'wn35_ch4_sig': 'wn35.ch4.signal',
        'wn35_ch5_batt': 'wn35.ch5.battery',
        'wn35_ch5_sig': 'wn35.ch5.signal',
        'wn35_ch6_batt': 'wn35.ch6.battery',
        'wn35_ch6_sig': 'wn35.ch6.signal',
        'wn35_ch7_batt': 'wn35.ch7.battery',
        'wn35_ch7_sig': 'wn35.ch7.signal',
        'wn35_ch8_batt': 'wn35.ch8.battery',
        'wn35_ch8_sig': 'wn35.ch8.signal',
        'wh51_ch9_batt': 'wh51.ch9.battery',
        'wh51_ch9_volt': 'ch_soil.9.voltage',
        'wh51_ch9_sig': 'wh51.ch9.signal',
        'wh51_ch10_batt': 'wh51.ch10.battery',
        'wh51_ch10_volt': 'ch_soil.10.voltage',
        'wh51_ch10_sig': 'wh51.ch10.signal',
        'wh51_ch11_batt': 'wh51.ch11.battery',
        'wh51_ch11_volt': 'ch_soil.11.voltage',
        'wh51_ch11_sig': 'wh51.ch11.signal',
        'wh51_ch12_batt': 'wh51.ch12.battery',
        'wh51_ch12_volt': 'ch_soil.12.voltage',
        'wh51_ch12_sig': 'wh51.ch12.signal',
        'wh51_ch13_batt': 'wh51.ch13.battery',
        'wh51_ch13_volt': 'ch_soil.13.voltage',
        'wh51_ch13_sig': 'wh51.ch13.signal',
        'wh51_ch14_batt': 'wh51.ch14.battery',
        'wh51_ch14_volt': 'ch_soil.14.voltage',
        'wh51_ch14_sig': 'wh51.ch14.signal',
        'wh51_ch15_batt': 'wh51.ch15.battery',
        'wh51_ch15_volt': 'ch_soil.15.voltage',
        'wh51_ch15_sig': 'wh51.ch15.signal',
        'wh51_ch16_batt': 'wh51.ch16.battery',
        'wh51_ch16_volt': 'ch_soil.16.voltage',
        'wh51_ch16_sig': 'wh51.ch16.signal'
    }
    # construct the default map based on the modular maps
    default_map = (dict(default_obs_map))
    default_map.update(default_rain_map)
    default_map.update(default_wind_map)
    default_map.update(default_sensor_state_map)

    def __init__(self, driver_debug=None, default_map=None, quiet=False, **mapper_config):
        """Initialise an HttpMapper object. quiet: don't log the field map
        (a map made only to read its field names)."""

        # obtain my base map, if we were passed a default map use it,
        # otherwise fall back to our default_map
        def_map = default_map if default_map is not None else HttpMapper.default_map
        super().__init__(driver_debug=driver_debug,
                         default_map=def_map,
                         **mapper_config)
        # We must have a mapping from device field 'datetime' to the WeeWX
        # packet field 'dateTime', too many parts of the driver depend on this.
        # So check to ensure this mapping is in place as the user could have
        # removed or altered it. If the mapping is not there add it in.
        # initialise the key that maps 'datetime'
        datetime_key = None
        for k, v in self.field_map.items():
            # if the mapping is for 'datetime' save the key and break
            if v == 'datetime':
                datetime_key = k
                break
        if datetime_key:
            _ = self.field_map.pop(datetime_key)
        self.field_map['dateTime'] = 'datetime'
        self.rain_map = {d: s for d,s in self.field_map.items() if s in self.default_rain_map.values()}
        self.wind_map = {d: s for d,s in self.field_map.items() if s in self.default_wind_map.values()}
        # ensure all destination (WeeWX) fields are assigned a unit group
        self.assign_unit_groups()
        # log our field map if required
        # The whole map is some 17 kB of text, so with debug = 1 only its size
        # is logged; it is logged in full (naturally sorted, over several
        # lines) with debug = 2 or more, or the 'loop' driver debug option.
        if quiet:
            pass
        elif weewx.debug >= 2 or getattr(driver_debug, 'loop', False):
            log_dict('     field map is ', self.field_map)
        elif getattr(driver_debug, 'any', False) or weewx.debug > 0:
            log.info('     field map has %d entries (logged in full with debug = 2)', len(self.field_map))

    def assign_unit_groups(self):
        """Assign destination fields to a WeeWX unit group.

        To allow destination fields to be formatted and converted using the
        WeeWX machinery each obs needs to be assigned to a unit group. This is
        already the case for obs in the WeeWX schemas, however, any obs
        'created' by the driver will need to be assigned a unit group. Obs are
        assigned to a unit group by adding an entry to
        weewx.units.obs_group.dict.
        """

        for w_field, e_field in self.field_map.items():
            if w_field not in weewx.units.obs_group_dict.keys():
                # the WeeWX field is not in the obs_group_dict so add an entry
                # for the WeeWX field using the group previously assigned to
                # the source Ecowitt field
                weewx.units.obs_group_dict[w_field] = DEFAULT_GROUPS[e_field]


# ============================================================================
#                               class SdMapper
# ============================================================================

class SdMapper(FieldMapper):
    """Class to map SD card history file records.

    The SdMapper uses a dictionary based map to map SD card history file fields
    to 'dotted' HTTP API fields used by the Ecowitt HTTP driver/service. This
    means that two mappings must be applied to map SD card history data fields
    to WeeWX loop packet/archive record fields; however, this approach removes
    the need for any user defined/changed field mapping to be applied to
    SD card history file field mapping.

    The SD card history file field names used are a modified version of the
    actual SD card history file field names that have had any applicable
    bracketed unit information removed, eg: 'Dew Point' is used in lieu of
    'Dew Point(°C)' (or 'Dew Point(°F)')

    Field map format is:

        HTTP driver 'dotted' field name: SD card history file field name
    """

    # default map to map SD card history file fields to 'dotted' fields used by
    # the Ecowitt local HTTP API driver
    default_map = {
        'wh25.intemp': 'Indoor temperature',
        'wh25.inhumi': 'Indoor Humidity',
        'common_list.0x02.val': 'Outdoor Temperature',
        'common_list.0x07.val': 'Outdoor Humidity',
        'common_list.0x03.val': 'Dew Point',
        'feelslike': 'Feels Like',
        'common_list.5.val': 'VPD',
        'common_list.0x0B.val': 'Wind',
        'common_list.0x0C.val': 'Gust',
        'common_list.0x0A.val': 'Wind Direction',
        'common_list.0x6D.val': '10 Minute Average Wind Direction',
        'wh25.abs': 'ABS Pressure',
        'wh25.rel': 'REL Pressure',
        'common_list.0x15.val': 'Solar Rad',
        'common_list.0x17.val': 'UV-Index',
        'rain.0x0E.val': 'Rain Rate',
        'rain.0x7D.val': 'Hourly Rain',
        'rain.0x0D.val': 'Event Rain',
        'rain.0x10.val': 'Daily Rain',
        'rain.0x7C.val': '24 Hour Rain',
        'rain.0x11.val': 'Weekly Rain',
        'rain.0x12.val': 'Monthly Rain',
        'rain.0x13.val': 'Yearly Rain',
        'piezoRain.0x0E.val': 'Piezo Rate',
        'piezoRain.0x7D.val': 'Piezo Hourly Rain',
        'piezoRain.0x0D.val': 'Piezo Event Rain',
        'piezoRain.0x10.val': 'Piezo Daily Rain',
        'piezoRain.0x7C.val': 'Piezo 24 Hour Rain',
        'piezoRain.0x11.val': 'Piezo Weekly Rain',
        'piezoRain.0x12.val': 'Piezo Monthly Rain',
        'piezoRain.0x13.val': 'Piezo Yearly Rain',
        'ch_aisle.1.temp': 'CH1 Temperature',
        'dewpoint1': 'CH1 Dew point',
        'heatindex1': 'CH1 HeatIndex',
        'ch_aisle.1.humidity': 'CH1 Humidity',
        'ch_aisle.2.temp': 'CH2 Temperature',
        'dewpoint2': 'CH2 Dew point',
        'heatindex2': 'CH2 HeatIndex',
        'ch_aisle.2.humidity': 'CH2 Humidity',
        'ch_aisle.3.temp': 'CH3 Temperature',
        'dewpoint3': 'CH3 Dew point',
        'heatindex3': 'CH3 HeatIndex',
        'ch_aisle.3.humidity': 'CH3 Humidity',
        'ch_aisle.4.temp': 'CH4 Temperature',
        'dewpoint4': 'CH4 Dew point',
        'heatindex4': 'CH4 HeatIndex',
        'ch_aisle.4.humidity': 'CH4 Humidity',
        'ch_aisle.5.temp': 'CH5 Temperature',
        'dewpoint5': 'CH5 Dew point',
        'heatindex5': 'CH5 HeatIndex',
        'ch_aisle.5.humidity': 'CH5 Humidity',
        'ch_aisle.6.temp': 'CH6 Temperature',
        'dewpoint6': 'CH6 Dew point',
        'heatindex6': 'CH6 HeatIndex',
        'ch_aisle.6.humidity': 'CH6 Humidity',
        'ch_aisle.7.temp': 'CH7 Temperature',
        'dewpoint7': 'CH7 Dew point',
        'heatindex7': 'CH7 HeatIndex',
        'ch_aisle.7.humidity': 'CH7 Humidity',
        'ch_aisle.8.temp': 'CH8 Temperature',
        'dewpoint8': 'CH8 Dew point',
        'heatindex8': 'CH8 HeatIndex',
        'ch_aisle.8.humidity': 'CH8 Humidity',
        'ch_leaf.1.humidity': 'WH35 CH1hum',
        'ch_leaf.2.humidity': 'WH35 CH2hum',
        'ch_leaf.3.humidity': 'WH35 CH3hum',
        'ch_leaf.4.humidity': 'WH35 CH4hum',
        'ch_leaf.5.humidity': 'WH35 CH5hum',
        'ch_leaf.6.humidity': 'WH35 CH6hum',
        'ch_leaf.7.humidity': 'WH35 CH7hum',
        'ch_leaf.8.humidity': 'WH35 CH8hum',
        'lightning.timestamp': 'Thunder time',
        'lightning.count': 'Thunder count',
        'lightning.distance': 'Thunder distance',
        'co2.temperature': 'AQIN Temperature',
        'co2.humidity': 'AQIN Humidity',
        'co2.CO2': 'AQIN CO2',
        'co2.PM25': 'AQIN Pm2.5',
        'co2.PM10': 'AQIN Pm10',
        'co2.PM1': 'AQIN Pm1.0',
        'co2.PM4': 'AQIN Pm4.0',
        'ch_soil.1.humidity': 'SoilMoisture CH1',
        'ch_soil.2.humidity': 'SoilMoisture CH2',
        'ch_soil.3.humidity': 'SoilMoisture CH3',
        'ch_soil.4.humidity': 'SoilMoisture CH4',
        'ch_soil.5.humidity': 'SoilMoisture CH5',
        'ch_soil.6.humidity': 'SoilMoisture CH6',
        'ch_soil.7.humidity': 'SoilMoisture CH7',
        'ch_soil.8.humidity': 'SoilMoisture CH8',
        'ch_soil.9.humidity': 'SoilMoisture CH9',
        'ch_soil.10.humidity': 'SoilMoisture CH10',
        'ch_soil.11.humidity': 'SoilMoisture CH11',
        'ch_soil.12.humidity': 'SoilMoisture CH12',
        'ch_soil.13.humidity': 'SoilMoisture CH13',
        'ch_soil.14.humidity': 'SoilMoisture CH14',
        'ch_soil.15.humidity': 'SoilMoisture CH15',
        'ch_soil.16.humidity': 'SoilMoisture CH16',
        'ch_leak.1.status': 'Water CH1',
        'ch_leak.2.status': 'Water CH2',
        'ch_leak.3.status': 'Water CH3',
        'ch_leak.4.status': 'Water CH4',
        'ch_pm25.1.PM25': 'Pm2.5 CH1',
        'ch_pm25.2.PM25': 'Pm2.5 CH2',
        'ch_pm25.3.PM25': 'Pm2.5 CH3',
        'ch_pm25.4.PM25': 'Pm2.5 CH4',
        'ch_temp.1.temp': 'WN34 CH1',
        'ch_temp.2.temp': 'WN34 CH2',
        'ch_temp.3.temp': 'WN34 CH3',
        'ch_temp.4.temp': 'WN34 CH4',
        'ch_temp.5.temp': 'WN34 CH5',
        'ch_temp.6.temp': 'WN34 CH6',
        'ch_temp.7.temp': 'WN34 CH7',
        'ch_temp.8.temp': 'WN34 CH8',
        'ch_lds.1.air': 'LDS_Air CH1',
        'ch_lds.1.depth': 'LDS_Depth CH1',
        'ch_lds.1.heat': 'LDS_Heat CH1',
        'ch_lds.2.air': 'LDS_Air CH2',
        'ch_lds.2.depth': 'LDS_Depth CH2',
        'ch_lds.2.heat': 'LDS_Heat CH2',
        'ch_lds.3.air': 'LDS_Air CH3',
        'ch_lds.3.depth': 'LDS_Depth CH3',
        'ch_lds.3.heat': 'LDS_Heat CH3',
        'ch_lds.4.air': 'LDS_Air CH4',
        'ch_lds.4.depth': 'LDS_Depth CH4',
        'ch_lds.4.heat': 'LDS_Heat CH4'
    }

    def __init__(self, driver_debug=None, default_map=None, **mapper_config):

        # obtain the default map to be used
        def_map = default_map if default_map is not None else SdMapper.default_map
        super().__init__(driver_debug=driver_debug,
                         default_map=def_map,
                         **mapper_config)

    def map_data(self, rec, unit_system=None):
        """Map a packet of data according to the field map.

        The normal mapping approach is to iterate over the source fields in the
        field map and if the source field exists in the source data that field
        is mapped to the destination field in the mapped data. This approach
        will not work with SD card history data as the source data fields
        contain unit information which must be stripped before mapping may be
        applied.

        For SD card history data we iterate over the source data fields then
        strip the extraneous unit information before mapping the source data if
        required. To better facilitate this process a modified dict structure
        that allows reversible lookups is used to hold the field map. The
        process is slower but this is less of an issue as this process only
        occurs during startup; not during the main packet loop.

        Parameters:

        rec:         dict of source data to be mapped
        unit_system: optional unit system value to be included in the mapped
                     data 'usUnits' field. Note will override any source
                     data/field mapping that would result in source data being
                     mapped to the 'usUnits' field.
        """

        if self.field_map is not None and len(self.field_map) > 0:
            mapped_rec = {}
            for field in rec.keys():
                # strip the units information from the key
                clean_key = re.sub(r"\(.*?\)","", field)
                # now try to map the source data using the sanitised key, but
                # be prepared to catch any one of a number of exceptions
                try:
                    dest_field = self.field_map.inverse[clean_key]
                    mapped_rec[dest_field] = float(rec[field])
                except (KeyError, TypeError, ValueError) as e:
                    # KeyError indicates no mapping exists for this source
                    # field, TypeError and ValueError indicate the source data
                    # could not be converted to a float. In either case log it,
                    # ignore this field and continue.
                    if self.driver_debug.catchup:
                        log.info("Error mapping field '%s': %s", field, e)
                    continue
            return self.to_station_columns(mapped_rec)
        else:
            return rec


# ============================================================================
#                             class SensorMapper
# ============================================================================

# multi-channel sensor models: (number of channels, live data groups the model
# feeds)
SENSOR_GROUPS = {'wn31': (8, ('ch_aisle',)), 'wn34': (8, ('ch_temp',)),
                 'wn35': (8, ('ch_leaf',)), 'wh41': (4, ('ch_pm25',)),
                 'wh51': (16, ('ch_soil',)), 'wh54': (4, ('ch_lds',)),
                 'wh55': (4, ('ch_leak',))}
# the model that feeds each live data group
_GROUP_MODEL = {group: model for model, (_, groups) in SENSOR_GROUPS.items()
                for group in groups}
# sensor IDs used by the device for sensors that are disabled or registering
_UNREGISTERED_IDS = ('FFFFFFFE', 'FFFFFFFF')
# a key belonging to a channel: 'wn31.ch2.' (sensor data) or 'ch_aisle.2.'
# (live data)
_CHANNEL_KEY = re.compile(r'(?:(\w+?)\.ch(\d+)\.|(ch_[a-z0-9]+)\.(\d+)\.)')
# a multi-channel sensor ID key, eg 'wn31.ch2.id'
_SENSOR_ID_KEY = re.compile(r'(\w+?)\.ch(\d+)\.id')


def sensor_id(value):
    """A sensor ID in comparable form: upper case hex, no '0x' or leading zeros."""

    text = str(value).strip().upper()
    text = text[2:] if text.startswith('0X') else text
    return text.lstrip('0') or '0'


def channel_keys(model, channel):
    """Key prefixes of the fields that belong to a channel of a model."""

    return [f'{model}.ch{channel}.'] + [f'{group}.{channel}.'
                                        for group in SENSOR_GROUPS[model][1]]


class SensorMapper:
    """Report multi-channel sensors on fixed channels chosen by sensor ID.

    The [[sensor_map]] stanza of the driver config consists of entries of the
    form:

        <sensor ID> = <channel number>

    A mapped sensor's data is reported on that channel whichever device
    channel the sensor is paired on, so re-pairing a sensor (eg after a
    battery change) does not move its data to different WeeWX fields. A sensor
    pushed off the channel takes the channel that was freed, so data is never
    merged or lost. Sensor IDs are those shown by 'weectl device --sensors'.
    """

    def __init__(self, config=None):
        # {sensor ID: channel}
        self.targets = {}
        for key, value in (config or {}).items():
            match = re.fullmatch(r'(?:ch)?\s*(\d+)', str(value).strip().lower())
            if match and re.fullmatch(r'(0x)?[0-9a-f]+', str(key).strip().lower()):
                self.targets[sensor_id(key)] = int(match.group(1))
            else:
                log.error("sensor_map: ignoring '%s = %s', expected "
                          "'<sensor ID> = <channel number>'", key, value)
        self.plan_key = None
        self.perms, self.lines, self.problems = {}, [], []

    def __bool__(self):
        return bool(self.targets)

    @staticmethod
    def paired(data):
        """{model: {device channel: sensor ID}} of registered multi-channel sensors."""

        found = {}
        for key, value in data.items():
            match = _SENSOR_ID_KEY.fullmatch(key)
            if match and match.group(1) in SENSOR_GROUPS and value is not None \
                    and str(value).upper() not in _UNREGISTERED_IDS:
                found.setdefault(match.group(1), {})[int(match.group(2))] = str(value)
        return found

    def plan(self, sensors):
        """Work out the channel moves {model: {from: to}} for the paired sensors.

        The moves are logged when they change.
        """

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
                problems.append(f'sensor {sid} is not paired with the device, so it is not mapped')
                continue
            model, channel = where[sid]
            count = SENSOR_GROUPS[model][0]
            if not 1 <= target <= count:
                problems.append(f'{model.upper()} sensor {sid}: channel {target} is '
                                f'outside 1-{count}, not mapped')
            elif target in wanted.get(model, {}).values():
                problems.append(f'{model.upper()} sensor {sid}: channel {target} is '
                                f'already mapped to another sensor, not mapped')
            else:
                wanted.setdefault(model, {})[channel] = target
        self.perms, self.lines = {}, []
        for model, moves in wanted.items():
            perm = self._permutation(moves, paired[model], SENSOR_GROUPS[model][0])
            for src, dst in sorted(perm.items()):
                if src != dst and src in paired[model]:
                    why = 'mapped' if src in moves else 'moved to make room'
                    self.lines.append(f'{model.upper()} sensor {paired[model][src]} on device '
                                      f'channel {src} is reported as channel {dst} ({why})')
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
        """Extend moves {from: to} to a one-to-one mapping of channels 1..count."""

        perm = dict(moves)
        taken = set(moves.values())
        for channel in sorted(occupied):
            if channel not in perm and channel not in taken:
                perm[channel] = channel
                taken.add(channel)
        channels = range(1, count + 1)
        # sensors pushed off their channel go first, preferably to the
        # channels the mapped sensors left
        sources = sorted(c for c in occupied if c not in perm) + \
            [c for c in channels if c not in perm and c not in occupied]
        free = [c for c in moves if c not in taken] + \
            [c for c in channels if c not in taken and c not in moves]
        perm.update(zip(sources, free))
        return perm

    def reported_channel(self, model, channel):
        """The channel a sensor on a device channel is reported on."""

        return self.perms.get(model, {}).get(channel, channel)

    def apply(self, data, sensors=None):
        """Return data with multi-channel sensor fields moved to their reported channels.

        The channels are planned from the sensor IDs in sensors, or in data
        itself if sensors is None (live data has them; catchup records don't,
        so the current sensor data is passed for those)."""

        if not self.targets:
            return data
        perms = self.plan(data if sensors is None else sensors)
        if not perms:
            return data
        result = {}
        for key, value in data.items():
            match = _CHANNEL_KEY.match(key)
            if match:
                model, mch, group, gch = match.groups()
                owner, channel, fmt = ((model, mch, f'{model}.ch{{}}.') if model else
                                       (_GROUP_MODEL.get(group), gch, f'{group}.{{}}.'))
                new = perms.get(owner, {}).get(int(channel))
                if new is not None:
                    key = fmt.format(new) + key[match.end():]
                    if group and key.endswith('.channel'):
                        value = new
            result[key] = value
        return result


def no_signal_keys(data):
    """{(model, channel): [data keys]} for multi-channel sensors that are paired but report signal 0.

    The keys are the sensor's readings (ch_aisle.N.*, ch_soil.N.* ...), not its
    signal, battery or ID (wn31.chN.*), which stay in the data."""

    found = {}
    for model, (count, _) in SENSOR_GROUPS.items():
        for channel in range(1, count + 1):
            signal = data.get(f'{model}.ch{channel}.signal')
            try:
                if signal is None or int(signal) != 0:
                    continue
            except (TypeError, ValueError):
                continue
            prefixes = [k for k in channel_keys(model, channel) if not k.startswith(f'{model}.ch')]
            found[(model, channel)] = [key for key in data
                                       if any(key.startswith(p) for p in prefixes)]
    return found


class FieldLimits:
    """[[limits]]: a reading outside '<WeeWX field> = min, max[, unit]' is dropped (set to None).

    Without a unit, min and max are in the gateway's own units (METRICWX:
    degree_C, meter_per_second, hPa, mm, mm_per_hour ...). Leave min or max
    empty (or 'None') for no limit on that side. The limits apply to the
    fields of the driver's loop packets and catchup records and the data the
    service adds; fields WeeWX calculates later (rain, dewpoint ...) are not
    checked.
    """

    def __init__(self, config=None, label=''):
        self.limits, self.label, self.reported = {}, label, set()
        prefix = f'{label}: ' if label else ''
        for field, value in (config or {}).items():
            parts = [str(p).strip() for p in (value if isinstance(value, (list, tuple)) else [value])]
            try:
                if len(parts) not in (2, 3):
                    raise ValueError('expected min, max or min, max, unit')
                low, high = (None if p.lower() in ('', 'none') else float(p) for p in parts[:2])
                unit = parts[2] if len(parts) == 3 else None
                if unit is not None and unit not in self.known_units():
                    raise ValueError(f"unknown unit '{unit}'")
                if low is not None and high is not None and low > high:
                    raise ValueError('min is above max')
            except ValueError as e:
                log.error("%slimits: ignoring '%s = %s': %s", prefix, field, ', '.join(parts), e)
                continue
            self.limits[field] = (low, high, unit)
        if self.limits:
            log.info('     limits apply to: %s', ', '.join(
                f"{f} {'' if lo is None else lo} to {'' if hi is None else hi}"
                f"{f' {u}' if u else ''}".replace('  ', ' ')
                for f, (lo, hi, u) in sorted(self.limits.items())))

    def __bool__(self):
        return bool(self.limits)

    @staticmethod
    def known_units():
        """Every unit name WeeWX can convert or that a unit system uses."""

        units = set(weewx.units.conversionDict)
        for targets in weewx.units.conversionDict.values():
            units.update(targets)
        for system in (weewx.units.USUnits, weewx.units.MetricUnits, weewx.units.MetricWXUnits):
            units.update(system.values())
        return units

    def apply(self, packet):
        """Set each field of packet (which has usUnits) that is outside its limits to None."""

        prefix = f'{self.label}: ' if self.label else ''
        for field, (low, high, unit) in self.limits.items():
            value = packet.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if unit is not None:
                try:
                    vt = weewx.units.as_value_tuple(packet, field)
                    value = value if vt.unit == unit else weewx.units.convert(vt, unit).value
                except (KeyError, ValueError, TypeError) as e:
                    if field not in self.reported:
                        log.error('%slimits: cannot compare %s in %s: %s', prefix, field, unit, e)
                        self.reported.add(field)
                    continue
            if (low is not None and value < low) or (high is not None and value > high):
                if field not in self.reported or weewx.debug > 0:
                    log.info('%slimits: %s %s is outside %s to %s%s, dropped%s', prefix, field,
                             packet[field], '' if low is None else low, '' if high is None else high,
                             f' {unit}' if unit else '',
                             '' if weewx.debug > 0 else ' (further readings outside the limits '
                                                        'are logged when debug is set)')
                    self.reported.add(field)
                packet[field] = None


# ============================================================================
#                 loop data export: ecwLoop.json and MQTT
# ============================================================================

EXPORT_SERVICE = f'{MODULE}.EcowittGatewayExport'
UNIT_SYSTEMS = {'us': weewx.US, 'metric': weewx.METRIC, 'metricwx': weewx.METRICWX}


def html_root(config_dict):
    """Absolute path of the WeeWX web pages folder (HTML_ROOT), or None."""

    try:
        root = config_dict['WEEWX_ROOT']
    except (KeyError, TypeError):
        return None
    return os.path.abspath(os.path.join(root, config_dict.get('StdReport', {}).get('HTML_ROOT',
                                                                                   'public_html')))


def check_units(units, label):
    """Validate a loop_json/mqtt 'units' setting, defaulting to 'native'."""

    units = str(units).lower()
    if units not in ('native', *UNIT_SYSTEMS):
        log.error("%s: unknown units '%s', using 'native'", label, units)
        return 'native'
    return units


def export_packet(packet, units):
    """Copy of a loop packet in the requested unit system, non-finite floats as None."""

    data = packet
    if units != 'native':
        data = weewx.units.StdUnitConverters[UNIT_SYSTEMS[units]].convertDict(packet)
        data['usUnits'] = UNIT_SYSTEMS[units]
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for k, v in data.items()}


class OnceLogger:
    """Log a repeating error once, and log when it clears."""

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


class LoopJsonWriter:
    """Write each loop packet to a JSON file (ecwLoop.json by default).

    Config ([EcowittGateway] [[loop_json]]):
        enable: whether to write the file (default False)
        path:   file or folder, relative paths are relative to HTML_ROOT
                (default ecwLoop.json)
        units:  native, us, metric or metricwx (default native)
    """

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
            log.info('loop data will be written to %s (%s units)', self.path, self.units)

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
    """Publish each loop packet to an MQTT broker (needs the paho-mqtt package).

    Config ([EcowittGateway] [[mqtt]]): enable, host, port, username,
    password, topic, format (json, individual or both), units, qos, retain,
    tls, ca_certs, certfile, keyfile, tls_insecure, client_id, keepalive.
    """

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
            log.error("mqtt: the paho-mqtt package is not installed, MQTT publishing is "
                      "disabled. Install it with 'sudo apt install python3-paho-mqtt' "
                      "(Debian) or 'pip install paho-mqtt' (pip install)")
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
        if hasattr(mqtt, 'CallbackAPIVersion'):
            # paho-mqtt 2.x
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        else:
            # paho-mqtt 1.x
            self.client = mqtt.Client(client_id=client_id)
        if config.get('username'):
            self.client.username_pw_set(config['username'], config.get('password') or None)
        if tls:
            self.client.tls_set(ca_certs=config.get('ca_certs') or None,
                                certfile=config.get('certfile') or None,
                                keyfile=config.get('keyfile') or None)
            self.client.tls_insecure_set(to_bool(config.get('tls_insecure', False)))
        self.client.will_set(f'{self.topic}/status', 'offline', qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(1, 60)
        self.client.connect_async(self.host, self.port, to_int(config.get('keepalive', 60)))
        self.client.loop_start()
        log.info("loop data will be published to MQTT broker %s:%s, topic '%s' (%s, %s units)",
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
                messages.append((f'{self.topic}/loop',
                                 json.dumps(data, default=str, sort_keys=True)))
            if self.format in ('individual', 'both'):
                messages += [(f'{self.topic}/{k}', '' if v is None else str(v))
                             for k, v in data.items()]
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


class EcowittGatewayExport(weewx.engine.StdService):
    """Export each finished loop packet to ecwLoop.json and/or an MQTT broker.

    Runs as a WeeWX service in restful_services so it sees the loop packet
    after StdWXCalculate has added the fields WeeWX derives (eg 'rain',
    't_rain', 'p_rain' and 'lightning_strike_count'). It works whether the
    gateway is used as the station driver or through EcowittHttpService. The
    settings are in the [[loop_json]] and [[mqtt]] subsections of
    [EcowittGateway]; with neither enabled the service does nothing.
    """

    def __init__(self, engine, config_dict):
        super().__init__(engine, config_dict)
        ec_config = driver_config(config_dict)
        self.loop_json = LoopJsonWriter(ec_config.get('loop_json', {}), html_root(config_dict))
        self.mqtt = MqttPublisher(ec_config.get('mqtt', {}))
        if self.loop_json.enabled or self.mqtt.enabled:
            self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)

    def new_loop_packet(self, event):
        self.loop_json.write(event.packet)
        self.mqtt.publish(event.packet)

    def shutDown(self):
        self.mqtt.close()


# ============================================================================
#              EcowittAdditions: fields the WeeWX database can't hold
# ============================================================================

# the additions database starts with only these columns; a column is added
# (with its daily summary) for each field the first time it has a value
ADDITIONS_SCHEMA = {
    'table': [('dateTime', 'INTEGER NOT NULL UNIQUE PRIMARY KEY'),
              ('usUnits', 'INTEGER NOT NULL'),
              ('interval', 'INTEGER NOT NULL')],
    'day_summaries': []
}
ADDITIONS_BINDING = 'ecowitt_binding'
COLUMN_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*$')

# Standard WeeWX columns (the ones the Seasons skin shows) that serve the same
# purpose as a driver field: {column: driver fields, first preferred}. A
# gateway has one outdoor temperature sensor (WH26, WN32P or an array), so they
# share one column.
STATION_COLUMNS = dict(
    {'inTempBatteryStatus': ('wh25_batt', 'wn32_batt'),
     'outTempBatteryStatus': ('wh26_batt', 'wn32p_batt', 'wh69_batt'),
     'rainBatteryStatus': ('wh40_batt',),
     'hailBatteryStatus': ('ws90_batt',),
     'windBatteryStatus': ('ws80_batt', 'ws85_batt', 'ws90_batt', 'wh68_batt'),
     'lightning_distance': ('lightningdist',)},
    **{f'batteryStatus{n}': (f'wn31_ch{n}_batt',) for n in range(1, 9)},
    **{f'signal{n}': (f'wn31_ch{n}_sig',) for n in range(1, 9)})
# WeeWX's *BatteryStatus columns are 0 (OK) or 1 (low). These sensors report a
# level instead (0-5, 0 or 1 low, 6 on DC power), so the column gets the status
# worked out from it and the level stays in the driver's field.
BATTERY_LEVEL_COLUMNS = ('rainBatteryStatus', 'hailBatteryStatus', 'windBatteryStatus')


def battery_status(level):
    """WeeWX's battery status, 0 (OK) or 1 (low), for an Ecowitt battery level;
    None if the level isn't one."""

    if isinstance(level, (int, float)) and 0 <= level <= 6:
        return 1 if level <= 1 else 0
    return None


def station_columns(config_dict):
    """The columns of the station's database ([StdArchive] data_binding), or
    of its schema if the database can't be opened (not made yet). None if
    neither can be read."""

    binding = (config_dict.get('StdArchive') or {}).get('data_binding', 'wx_binding')
    try:
        manager_dict = weewx.manager.get_manager_dict_from_config(config_dict, binding)
    except Exception as e:
        log.debug('Could not read data binding %s: %s', binding, e)
        return None
    try:
        with weewx.manager.open_manager(manager_dict) as manager:
            return set(manager.sqlkeys)
    except Exception:
        pass
    schema = manager_dict.get('schema')
    if isinstance(schema, str):
        try:
            schema = weeutil.weeutil.get_object(schema)
        except Exception:
            return None
    table = schema.get('table') if isinstance(schema, dict) else schema
    try:
        return {column[0] for column in table}
    except TypeError:
        return None


def station_column_map(config_dict, columns=None):
    """{station database column: driver fields} for the STATION_COLUMNS
    entries this station can use, unless [[additions]] use_station_columns is
    False: the column is in the station's database (columns, default
    station_columns()) and no field map setting names it, and the driver fields
    have no column there themselves and aren't named in a field map setting.
    (A battery level keeps its field, so its column of its own doesn't stop
    the status going to the station column.)"""

    conf = driver_config(config_dict).get('additions') or {}
    if not weeutil.weeutil.tobool(conf.get('use_station_columns', True)):
        return {}
    columns = station_columns(config_dict) if columns is None else columns
    if not columns:
        return {}
    configured = configured_fields(config_dict)
    named = configured | set(HttpMapper.default_map)
    result = {}
    for column, fields in STATION_COLUMNS.items():
        fields = tuple(f for f in fields if f not in configured
                       and (f not in columns or column in BATTERY_LEVEL_COLUMNS))
        if column in columns and column not in named and fields:
            result[column] = fields
    return result


def use_station_columns(config_dict, label=DRIVER_NAME):
    """Have the driver's field maps put the fields station_column_map() finds
    in their station database columns, and log them. Returns the map."""

    FieldMapper.station_columns = station_column_map(config_dict)
    if FieldMapper.station_columns:
        log.info('%s: using station database columns %s', label,
                 ', '.join(f"{c} ({' or '.join(f)})" for c, f in FieldMapper.station_columns.items()))
    return FieldMapper.station_columns


class EcowittAdditions(weewx.engine.StdService):
    """Archive the fields of each archive record that the station's database
    has no column for, in a database of their own.

    Runs in archive_services, so it sees every archive record, live and
    catchup, with all its fields; the station's database ([StdArchive]
    data_binding) keeps the ones it has columns for. The rest (numbers only)
    go to [[additions]] data_binding (default ecowitt_binding,
    ecowitt_additions.sdb), which gets a column, with a daily summary, the
    first time a field has a value. Skins use them by name like any other
    field (AdditionsXType), eg $day.wh40_batt.last, or with that binding. Off
    unless [[additions]] enable is set; an error is logged and never stops
    WeeWX.
    """

    def __init__(self, engine, config_dict):
        super().__init__(engine, config_dict)
        conf = driver_config(config_dict).get('additions') or {}
        self.enabled = weeutil.weeutil.tobool(conf.get('enable', False))
        self.binding = conf.get('data_binding', ADDITIONS_BINDING)
        self.main_binding = (config_dict.get('StdArchive') or {}).get('data_binding', 'wx_binding')
        self.xtype = None
        if self.enabled:
            self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)
            log.info("EcowittAdditions: fields the %s database has no column for are archived "
                     "with %s", self.main_binding, self.binding)
            self.xtype = AdditionsXType(config_dict, self.binding)
            weewx.xtypes.xtypes.append(self.xtype)

    def shutDown(self):
        if self.xtype in weewx.xtypes.xtypes:
            weewx.xtypes.xtypes.remove(self.xtype)

    def new_archive_record(self, event):
        try:
            self.archive(event.record)
        except Exception as e:
            log.error('EcowittAdditions: could not archive the additional fields: %s', e)

    def archive(self, record):
        """Store a record's fields that the station's database has no column for."""

        main_columns = set(self.engine.db_binder.get_manager(self.main_binding).sqlkeys)
        extra = {k: v for k, v in record.items()
                 if k not in main_columns and COLUMN_NAME.match(k)
                 and (v is None or isinstance(v, (int, float)))}
        if not any(v is not None for v in extra.values()):
            return
        manager = self.engine.db_binder.get_manager(self.binding, initialize=True)
        new = [k for k, v in extra.items() if v is not None and k not in manager.sqlkeys]
        if new:
            for column in new:
                manager.add_column(column, 'REAL')
            # the manager caches its columns and daily summaries
            manager.sqlkeys = manager.connection.columnsOf(manager.table_name)
            manager._sync()
            log.info('EcowittAdditions: added %s to %s', ', '.join(new), self.binding)
        values = {k: v for k, v in extra.items() if k in manager.sqlkeys}
        manager.addRecord(dict(values, dateTime=record['dateTime'], usUnits=record['usUnits'],
                               interval=record['interval']))


class AdditionsXType(weewx.xtypes.XType):
    """Lets skins use the additions database's fields by name, as if they were
    in the station's database: $current.wh40_batt, $day.co2_temp.max, plots.

    Asked about a field the database being queried has no column for, it
    answers from the additions database ([[additions]] data_binding) if that
    has the column. Each thread (reports run in their own) opens its own
    connection; the columns are read again at most every COLUMNS_REFRESH
    seconds, as EcowittAdditions adds them."""

    COLUMNS_REFRESH = 300

    def __init__(self, config_dict, binding):
        self.config_dict = config_dict
        self.binding = binding
        self.local = threading.local()

    def manager(self, obs_type):
        """The additions database's manager if it has a column obs_type, else
        None."""

        local = self.local
        now = time.time()
        if getattr(local, 'manager', None) is None:
            if now - getattr(local, 'tried', 0) < self.COLUMNS_REFRESH:
                return None
            local.tried = now
            try:
                local.manager = weewx.manager.open_manager_with_config(self.config_dict, self.binding)
                local.read = now
            except Exception:
                return None
        manager = local.manager
        if obs_type not in manager.sqlkeys and now - local.read >= self.COLUMNS_REFRESH:
            local.read = now
            try:
                manager.sqlkeys = manager.connection.columnsOf(manager.table_name)
                manager._sync()
            except Exception as e:
                log.debug('AdditionsXType: could not read the columns: %s', e)
        return manager if obs_type in manager.sqlkeys else None

    def _source(self, obs_type, db_manager):
        if db_manager is None or obs_type in db_manager.sqlkeys:
            raise weewx.UnknownType(obs_type)
        manager = self.manager(obs_type)
        if manager is None or manager.database_name == db_manager.database_name:
            raise weewx.UnknownType(obs_type)
        return manager

    def get_scalar(self, obs_type, record, db_manager=None, **option_dict):
        manager = self._source(obs_type, db_manager)
        if not record:
            raise weewx.CannotCalculate(obs_type)
        found = manager.getRecord(record['dateTime']) or {}
        return weewx.units.as_value_tuple({'usUnits': record['usUnits'], obs_type: found.get(obs_type)},
                                          obs_type)

    def get_series(self, obs_type, timespan, db_manager, aggregate_type=None,
                   aggregate_interval=None, **option_dict):
        return weewx.xtypes.get_series(obs_type, timespan, self._source(obs_type, db_manager),
                                       aggregate_type, aggregate_interval, **option_dict)

    def get_aggregate(self, obs_type, timespan, aggregate_type, db_manager, **option_dict):
        return weewx.xtypes.get_aggregate(obs_type, timespan, aggregate_type,
                                          self._source(obs_type, db_manager), **option_dict)

    def shut_down(self):
        manager = getattr(self.local, 'manager', None)
        if manager is not None:
            manager.close()
            self.local.manager = None


# ============================================================================
#                     One gateway: SourceStatus, GatewaySource
# ============================================================================

class SourceStatus:
    """Whether a gateway is answering, as seen by its GatewaySource.

    online is None until the first poll, then True or False. since is when it
    last changed, last_seen the time of the last data and last_error the last
    failure."""

    def __init__(self):
        self.online = None
        self.since = None
        self.last_seen = None
        self.last_error = None

    def seen(self, ts=None):
        """The gateway sent data (at ts, default now)."""

        ts = int(time.time()) if ts is None else ts
        if self.online is not True:
            self.since = ts
        self.online = True
        self.last_seen = ts

    def failed(self, error):
        """A poll failed with error."""

        if self.online is not False:
            self.since = int(time.time())
        self.online = False
        self.last_error = str(error)

    def __repr__(self):
        return (f'SourceStatus(online={self.online}, since={self.since}, '
                f'last_seen={self.last_seen}, last_error={self.last_error!r})')


class GatewaySource:
    """Everything that belongs to one gateway.

    A GatewaySource reads its settings from a config dict (the [EcowittGateway]
    section, or one gateway's settings when there are several), and owns the
    gateway's field map (HttpMapper), its collector thread
    (EcowittHttpCollector), its catchup settings and its status. The driver and
    the service use it to turn what the collector queues into loop packets and
    archive records:

        source = GatewaySource('gateway', config_dict)
        source.start()
        item = source.get(timeout)          # data, an exception or None
        packet = source.loop_packet(item)   # a loop packet in source.unit_system
        for record in source.history(since_ts): ...
        source.stop()

    name is the gateway's name (used when there are several); label starts
    the log lines (eg 'EcowittHttpDriver').
    """

    def __init__(self, name, config, unit_system=DEFAULT_UNIT_SYSTEM, label=None):
        """Initialise a GatewaySource object."""

        self.name = name
        self.label = label if label is not None else name
        self.unit_system = DEFAULT_UNIT_SYSTEM if unit_system is None else unit_system
        # get driver/service specific debug settings
        self.driver_debug = DebugOptions(**config)
        self.mapper = HttpMapper(driver_debug=self.driver_debug, **config)
        # how many times to try to contact the device and how long to wait
        # between tries
        max_tries = weeutil.weeutil.to_int(config.get('max_tries', DEFAULT_MAX_TRIES))
        retry_wait = weeutil.weeutil.to_int(config.get('retry_wait', DEFAULT_RETRY_WAIT))
        # the socket timeout to be used
        self.url_timeout = weeutil.weeutil.to_int(config.get('url_timeout', DEFAULT_URL_TIMEOUT))
        # the device IP address
        self.ip_address = config.get('ip_address')
        # how often (in seconds) we should poll the API
        self.poll_interval = int(config.get('poll_interval', DEFAULT_POLL_INTERVAL))
        show_battery = weeutil.weeutil.tobool(config.get('show_all_batt', DEFAULT_FILTER_BATTERY))
        # whether to log unknown API fields at the info level rather than the
        # debug level
        log_unknown_fields = weeutil.weeutil.tobool(config.get('log_unknown_fields', False))
        # how often (in seconds) to check for a device firmware update
        fw_update_check_interval = int(config.get('firmware_update_check_interval',
                                                  DEFAULT_FW_CHECK_INTERVAL))
        # catchup settings (used by the driver)
        catchup_dict = config.get('catchup', dict())
        self.catchup_source = catchup_dict.get('source')
        self.catchup_grace = weeutil.weeutil.to_int(catchup_dict.get('grace', DEFAULT_CATCHUP_GRACE))
        self.catchup_retries = weeutil.weeutil.to_int(catchup_dict.get('retries',
                                                                       DEFAULT_CATCHUP_RETRIES))
        # the port the SD card history files are read from (81 on the device)
        self.catchup_file_port = weeutil.weeutil.to_int(catchup_dict.get('file_port',
                                                                         DEFAULT_SD_FILE_PORT))
        # Ecowitt.net keys: [[catchup]], or the section itself
        self.api_key = catchup_dict.get('api_key', config.get('api_key')) or None
        self.app_key = catchup_dict.get('app_key', config.get('app_key')) or None
        # field map entries already logged as active (see log_active_map())
        self.active_fields = set()
        self.status = SourceStatus()
        # failures are logged until set_failure_logging(False)
        self.log_failures = True

        define_units()

        # readings outside [[limits]] are dropped
        self.limits = FieldLimits(config.get('limits'), self.label)
        # pm2_5 (a standard WeeWX column) comes from the WH41/WH43 on channel
        # 1; with none, from the WH45/WH46 (pm2_55), unless either has been
        # mapped to something else
        fmap = self.mapper.field_map
        self.pm25_from_wh45 = fmap.get('pm2_5') == 'ch_pm25.1.PM25' and fmap.get('pm2_55') == 'co2.PM25'
        self.co2_from_indoor = fmap.get('co2') == 'co2.CO2' and fmap.get('co2_in') == 'wh25.CO2'
        # WeeWX radiation (W/m2) worked out from illuminance (lux), if
        # radiation_from_illuminance is set and radiation isn't mapped to
        # something
        self.radiation_from_illuminance = (
            weeutil.weeutil.tobool(config.get('radiation_from_illuminance', False))
            and 'radiation' not in fmap)
        # ignore the readings of multi-channel sensors that report signal 0
        self.ignore_no_signal = weeutil.weeutil.tobool(config.get('ignore_no_signal', False))
        # (model, channel) of the sensors being ignored now
        self.no_signal = set()
        if self.ignore_no_signal:
            log.info('     readings from multi-channel sensors with signal 0 will be ignored')

        # log our config/settings that are not being pushed further down before
        # we obtain an EcowittHttpCollector object. Obtaining an
        # EcowittHttpCollector object may fail due to connectivity issues, this
        # way we at least log our config which may aid debugging.
        log.info('     device IP address is %s' % self.ip_address)
        log.info('     poll interval is %d seconds' % self.poll_interval)
        if self.driver_debug.any or weewx.debug > 0:
            log.info('     Max tries is %d URL retry wait is %d seconds', max_tries, retry_wait)
            log.info('     URL timeout is %d seconds', self.url_timeout)
        # log specific debug output but only if set
        debug_list = []
        if self.driver_debug.rain:
            debug_list.append(f"rain debug is {self.driver_debug.rain}")
        if self.driver_debug.wind:
            debug_list.append(f"wind debug is {self.driver_debug.wind}")
        if self.driver_debug.loop:
            debug_list.append(f"loop debug is {self.driver_debug.loop}")
        if len(debug_list) > 0:
            log.info(' '.join(debug_list))
        debug_list = []
        if self.driver_debug.sensors:
            debug_list.append(f"sensors debug is {self.driver_debug.sensors}")
        if self.driver_debug.catchup:
            debug_list.append(f"catchup debug is {self.driver_debug.catchup}")
        if self.driver_debug.parser:
            debug_list.append(f"parser debug is {self.driver_debug.parser}")
        if self.driver_debug.collector:
            debug_list.append(f"collector debug is {self.driver_debug.collector}")
        if len(debug_list) > 0:
            log.info(' '.join(debug_list))
        if self.mapper.wn32_indoor:
            log.debug("     sensor ID decoding will use indoor 'WN32'")
        else:
            log.debug("     sensor ID decoding will use 'WH25'")
        if self.mapper.wn32_outdoor:
            log.debug("     sensor ID decoding will use outdoor 'WN32P'")
        else:
            log.debug("     sensor ID decoding will use 'WH26'")

        # create a EcowittHttpCollector object to interact with the device API,
        # if there is a problem our owner will handle any exceptions
        self.collector = EcowittHttpCollector(ip_address=self.ip_address,
                                              poll_interval=self.poll_interval,
                                              max_tries=max_tries,
                                              retry_wait=retry_wait,
                                              url_timeout=self.url_timeout,
                                              unit_system=self.unit_system,
                                              show_battery=show_battery,
                                              log_unknown_fields=log_unknown_fields,
                                              fw_update_check_interval=fw_update_check_interval,
                                              sensor_map=config.get('sensor_map'),
                                              api=config.get('api', DEFAULT_API),
                                              tcp_port=weeutil.weeutil.to_int(config.get('tcp_port',
                                                                                         DEFAULT_TCP_PORT)),
                                              log_next_update=weeutil.weeutil.tobool(
                                                  config.get('log_next_update', DEFAULT_LOG_NEXT_UPDATE)),
                                              debug=self.driver_debug)

    # ---------------------------------------------------------------- device

    @property
    def device(self):
        """The EcowittDevice or EcowittTcpDevice the collector polls."""

        return self.collector.device

    @property
    def model(self):
        """The gateway model, eg 'GW2000'."""

        return self.collector.device.model

    @property
    def mac_address(self):
        """The gateway's MAC address."""

        return self.collector.device.mac_address

    # ------------------------------------------------------------ the queue

    def start(self):
        """Start polling the gateway (the collector thread)."""

        self.collector.startup()

    def stop(self):
        """Stop polling the gateway."""

        self.collector.shutdown()

    @property
    def queue(self):
        """The collector's queue: data dicts, exceptions, or None to stop."""

        return self.collector.queue

    def get(self, timeout):
        """The next item the collector queued, waiting up to timeout seconds.

        Raises queue.Empty if there is none. A data dict marks the gateway
        online, an exception marks it offline (see status)."""

        item = self.collector.queue.get(True, timeout)
        if hasattr(item, 'keys'):
            self.status.seen(item.get('datetime'))
        elif isinstance(item, BaseException):
            self.status.failed(item)
        return item

    def set_failure_logging(self, log_failures):
        """Turn failure logging on or off.

        When operating as a service lost contact or other non-fatal errors
        should only be logged every so often so as not to flood the logs.
        Failure logging occurs at three levels: here, in the collector and in
        the collector's device object."""

        self.log_failures = log_failures
        self.collector.log_failures = log_failures
        self.collector.device.log_failures = log_failures

    # -------------------------------------------------------- loop packets

    def log_received(self, data):
        """Log a data dict from the collector, as the debug options ask."""

        if self.driver_debug.loop:
            if 'datetime' in data:
                log_dict(f"{self.label}: Received {self.model} data: "
                         f"{timestamp_to_string(data['datetime'])} ", data)
            else:
                log_dict(f'{self.label}: Received {self.model} data: ', data)
        else:
            if self.driver_debug.rain:
                self.log_rain_data(data, f'{self.label}: Received {self.model} data')
            if self.driver_debug.wind:
                self.log_wind_data(data, f'{self.label}: Received {self.model} data')

    def mapped(self, data):
        """Collector data mapped to WeeWX fields, with usUnits.

        Readings of sensors with no signal are left out (ignore_no_signal) and
        readings outside [[limits]] are None."""

        self.log_active_map(data)
        if self.ignore_no_signal:
            data = self.drop_no_signal(data)
        mapped_data = self.mapper.map_data(data)
        mapped_data['usUnits'] = self.unit_system
        self.pm25_fallback(mapped_data)
        self.derive_radiation(mapped_data)
        self.limits.apply(mapped_data)
        if self.driver_debug.loop:
            if 'dateTime' in mapped_data:
                log_dict(f"{self.label}: Mapped {self.model} data: "
                         f"{timestamp_to_string(mapped_data['dateTime'])} ", mapped_data)
            else:
                log_dict(f'{self.label}: Mapped {self.model} data: ', mapped_data)
        else:
            if self.driver_debug.rain:
                self.log_rain_data(mapped_data, f'{self.label}: Mapped {self.model} data')
            if self.driver_debug.wind:
                self.log_wind_data(mapped_data, f'{self.label}: Mapped {self.model} data')
        return mapped_data

    def pm25_fallback(self, record):
        """pm2_5 from the WH45/WH46 when there is no channel 1 PM2.5 reading,
        and co2 from the console's own CO2 sensor (co2_in) without a WH45/WH46."""

        if self.pm25_from_wh45 and record.get('pm2_5') is None and record.get('pm2_55') is not None:
            record['pm2_5'] = record['pm2_55']
        if self.co2_from_indoor and record.get('co2') is None and record.get('co2_in') is not None:
            record['co2'] = record['co2_in']

    def derive_radiation(self, record):
        """radiation (W/m2) from illuminance (lux) with Ecowitt's factor
        (126.7 lux per W/m2), if radiation_from_illuminance is set."""

        if not self.radiation_from_illuminance or record.get('illuminance') is None:
            return
        groups = weewx.units.std_groups[record.get('usUnits', self.unit_system)]
        light = weewx.units.ValueTuple(record['illuminance'], groups['group_illuminance'], 'group_illuminance')
        record['radiation'] = weewx.units.convert(light, groups['group_radiation']).value

    def loop_packet(self, data):
        """A loop packet (in unit_system) from a data dict the collector queued."""

        # gateway_online: the driver only has a packet when the gateway answered
        packet = {'dateTime': data['datetime'] if data.get('datetime') is not None
                  else int(time.time() + 0.5),
                  'usUnits': self.unit_system,
                  'gateway_online': 1}
        packet.update(self.mapped(data))
        if self.driver_debug.loop or weewx.debug >= 2:
            log_dict(f'{self.label}: Packet {timestamp_to_string(packet["dateTime"])}: ', packet)
        else:
            if self.driver_debug.rain:
                self.log_rain_data(packet, f'{self.label}: Packet {timestamp_to_string(packet["dateTime"])}')
            if self.driver_debug.wind:
                self.log_wind_data(packet, f'{self.label}: Packet {timestamp_to_string(packet["dateTime"])}')
        return packet

    def drop_no_signal(self, data):
        """A copy of data without the readings of multi-channel sensors whose
        signal is 0 (ignore_no_signal). The log says when a sensor starts and
        stops being ignored."""

        found = no_signal_keys(data)
        for model, channel in sorted(set(found) - self.no_signal):
            log.info('%s: %s CH%d has no signal: its readings are ignored until it is heard again',
                     self.label, model.upper(), channel)
        for model, channel in sorted(self.no_signal - set(found)):
            log.info('%s: %s CH%d has a signal again', self.label, model.upper(), channel)
        self.no_signal = set(found)
        drop = {key for keys in found.values() for key in keys}
        return {k: v for k, v in data.items() if k not in drop} if drop else data

    def online(self, now, max_age):
        """1 if the gateway sent data within max_age seconds of now, else 0 (gateway_online)."""

        last = self.status.last_seen
        return int(bool(self.status.online and last is not None and last > now - max_age))

    # ------------------------------------------------------------- catchup

    def catchup_factory(self):
        """Return a 'catchup source' object.

        Return a 'catchup source' object (EcowittDeviceCatchup or
        EcowittNetCatchup) depending on the catchup source setting. If a
        'catchup source' object cannot be created raise a CatchupObjectError.
        """

        source = (self.catchup_source or 'either').lower()

        def net():
            return EcowittNetCatchup(api_key=self.api_key,
                                     app_key=self.app_key,
                                     mac=self.mac_address,
                                     driver_debug=self.driver_debug)

        def device():
            try:
                return EcowittDeviceCatchup(ip_address=self.ip_address,
                                            unit_system=self.unit_system,
                                            catchup_grace=self.catchup_grace,
                                            max_retries=self.catchup_retries,
                                            file_port=self.catchup_file_port,
                                            url_timeout=self.url_timeout,
                                            driver_debug=self.driver_debug)
            except weewx.ViolatedPrecondition as e:
                raise CatchupObjectError from e

        if source in ('either', 'both', 'device') and isinstance(self.collector.device, EcowittTcpDevice):
            # a GW1000 has no SD card or local HTTP API
            if source == 'device':
                raise CatchupObjectError(f'{self.model} has no SD card or local '
                                         f'HTTP API, catchup from the device is not possible')
            return net()
        if source in ('either', 'both'):
            try:
                return device()
            except CatchupObjectError:
                pass
            return net()
        if source == 'net':
            return net()
        if source == 'device':
            return device()
        raise CatchupObjectError(f"No catchup (catchup source '{self.catchup_source}')")

    def history(self, since_ts, archive_interval=None):
        """Archive records after since_ts from the catchup source, in unit_system.

        Yields nothing if there is no catchup source. The records get the same
        [[sensor_map]] and [[limits]] as loop packets. archive_interval
        (seconds), if given, is checked against the records' own interval, and
        a line after the catchup says how many records came from where."""

        try:
            catchup_obj = self.catchup_factory()
        except CatchupObjectError as e:
            if self.driver_debug.catchup:
                log.info('%s: no catchup: %s', self.label, e)
            return
        where = 'the SD card' if isinstance(catchup_obj, EcowittDeviceCatchup) else 'Ecowitt.net'
        sensors = self.catchup_sensors()
        count = first = last = 0
        for rec in catchup_obj.gen_history_records(start_ts=since_ts):
            if sensors is not None:
                rec = self.collector.sensor_mapper.apply(rec, sensors)
            # (SD card record times were floats; the field map also maps
            # 'datetime' to 'dateTime')
            rec['datetime'] = int(rec['datetime'])
            record = {'dateTime': rec['datetime'],
                      'usUnits': self.unit_system,
                      'interval': rec['interval']}
            record.update(self.mapper.map_data(rec))
            self.pm25_fallback(record)
            self.derive_radiation(record)
            self.limits.apply(record)
            if count == 0:
                first = record['dateTime']
                interval = record.get('interval')
                if archive_interval and interval and int(interval) * 60 != int(archive_interval):
                    log.warning('%s: Catchup: records from %s are every %s minutes, but archive_interval '
                                'is %s minutes. They are saved with their own interval.',
                                self.label, where, interval, archive_interval / 60)
            count, last = count + 1, record['dateTime']
            yield record
        if count:
            log.info('%s: Catchup: %d record(s) from %s, %s to %s', self.label, count, where,
                     timestamp_to_string(first), timestamp_to_string(last))
        elif self.driver_debug.catchup or weewx.debug > 0:
            log.info('%s: Catchup: no records from %s after %s', self.label, where,
                     timestamp_to_string(since_ts) if since_ts else 'the start')

    def catchup_sensors(self):
        """The gateway's sensor data, to apply [[sensor_map]] to catchup records; None without one.

        Catchup records have no sensor IDs, so the sensors paired now decide the
        channels. (A sensor re-paired since the records were made moves them
        to where it is now.)"""

        if not self.collector.sensor_mapper:
            return None
        try:
            return self.device.get_sensors_data()
        except Exception as e:
            log.error('%s: sensor_map: cannot read the sensor IDs, catchup records keep the device '
                      'channels: %s', self.label, e)
            return None

    # ------------------------------------------------------------- logging

    def log_active_map(self, data, label=None):
        """Log the part of the field map the device is filling.

        With debug logging on (debug = 1 or a driver debug option), the field
        map entries whose device field has a value in data are logged after the
        first poll, then any entries that appear later (a sensor added, or back
        in range). Sensors that are not paired are left out, so this is the
        map for this station, not the whole 200-plus-entry map."""

        if not (self.driver_debug.any or weewx.debug > 0):
            return
        label = self.label if label is None else label
        active = {w: s for w, s in active_field_map(self.mapper.field_map, data).items()
                  if w not in self.active_fields}
        if not active:
            return
        if self.active_fields:
            log_dict(f'{label}: {len(active)} more field(s) now reported: ', active)
        else:
            log_dict(f'{label}: active field map ({len(active)} of {len(self.mapper.field_map)} '
                     f'entries) is ', active)
        self.active_fields.update(active)

    def log_rain_data(self, data, preamble=None):
        """Log rain related data from the collector.

        General routine to obtain and log rain related data from a packet. The
        packet could be unmapped device data using 'device' field names, or it
        may be mapped data or a WeeWX loop packet that uses 'WeeWX' field
        names. In addition, the user may have specified a custom field map so
        any rain-related WeeWX fields may be different to those in the default
        map. So create a list of the values ('device' field names) in the rain
        map and the corresponding key ('WeeWX' field name). This combined list
        will consist of all 'device' and 'WeeWX' rain related fields

        This combined field list is then used to log any rain related fields.
        """

        msg_list = []
        for weewx_field, gw_field in self.mapper.rain_map.items():
            if weewx_field in data:
                msg_list.append(f"{weewx_field}={data[weewx_field]}")
            if gw_field in data and weewx_field != gw_field:
                msg_list.append(f"{gw_field}={data[gw_field]}")
        # pre-format the log line label
        label = f'{preamble}: ' if preamble is not None else ''
        if len(msg_list) > 0:
            log.info('%s%s' % (label, ' '.join(msg_list)))
        else:
            log.info('%sno rain data found' % (label,))

    def log_wind_data(self, data, preamble=None):
        """Log wind related data from the collector.

        General routine to obtain and log wind related data from a packet. The
        packet could be unmapped device data using 'device' field names, or it
        may be mapped data or a WeeWX loop packet that uses 'WeeWX' field
        names. So we iterate over the keys ('WeeWX' field names) and values
        ('device' field names) of the rain field map.
        """

        msg_list = []
        for weewx_field, gw_field in self.mapper.wind_map.items():
            if weewx_field in data:
                msg_list.append(f"{weewx_field}={data[weewx_field]}")
            if gw_field in data:
                msg_list.append(f"{gw_field}={data[gw_field]}")
        # pre-format the log line label
        label = f"{preamble}: " if preamble is not None else ""
        if len(msg_list) > 0:
            log.info('%s%s' % (label, ' '.join(msg_list)))
        else:
            log.info('%sno wind data found' % (label,))


# ============================================================================
#             Several gateways: [[sources]], the merge and catchup
# ============================================================================
#
# The priority merge, counter arbitration, outage restart and history
# aggregation follow the design of weewx-ecowitt-multi by mKainzbauer
# (https://github.com/mKainzbauer/weewx-ecowitt-multi, GPL-3.0-or-later:
# merge.py, cumulative.py, gap.py and history/aggregate.py), adapted to WeeWX
# working out rain and lightning from the gateways' totals ([StdWXCalculate] [[Delta]]).
#
# With several gateways each one has its own GatewaySource. A GatewayMerger
# combines their loop packets: each field comes from the first gateway in its
# priority order that has a fresh value. The totals WeeWX's [[Delta]] reads
# (t_rainyear, p_rainyear, lightningcount ...) can't simply be taken from one
# gateway and then another: gateways paired at different times, or whose
# totals were set by hand, have different totals for the same gauge, so a
# switch would look to WeeWX like rain (or a counter reset). MergedTotals keeps
# one total per field instead: it grows by the rise of one gateway's total at
# a time (CounterArbiter decides which, without counting any rain twice) and
# follows that gateway when its total resets (new year, new day).

# keys of the driver's section that are not inherited by the gateways in [[sources]]
NOT_INHERITED = ('sources', 'priority', 'source_priority', 'sensor_map', 'gap_mode', 'max_gap_wait',
                 'min_restart_interval', 'loop_json', 'mqtt')
# weewx-ecowitt-multi's 'history' values
HISTORY_ALIASES = {'auto': 'either', 'sd': 'device', 'cloud': 'net', 'none': 'none'}
# the cumulative fields WeeWX works out rain and lightning from (do_rain(), do_lightning())
DEFAULT_COUNTERS = ('t_rainyear', 'p_rainyear', 'lightningcount')
DEFAULT_GAP_MODE = 'restart'
DEFAULT_MAX_GAP_WAIT = 3600
DEFAULT_MIN_RESTART_INTERVAL = 900


def as_names(value):
    """A list of names from a config value: a list, or a comma separated string."""

    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(',')
    return [str(v).strip() for v in items if str(v).strip()]


def _source_config(base, name, own):
    """The config of gateway name: the driver's section with the gateway's own settings on top."""

    config = {k: (dict(v) if hasattr(v, 'keys') else v) for k, v in base.items() if k not in NOT_INHERITED}
    for key, value in own.items():
        if hasattr(value, 'keys') and hasattr(config.get(key), 'keys'):
            config[key] = {**config[key], **value}
        else:
            config[key] = dict(value) if hasattr(value, 'keys') else value
    # names used by weewx-ecowitt-multi
    if 'host' in own and 'ip_address' not in own:
        host, port = str(own['host']), own.get('port')
        config['ip_address'] = f'{host}:{port}' if port and ':' not in host else host
    if 'history' in own:
        config['catchup'] = {**config.get('catchup', {}),
                             'source': HISTORY_ALIASES.get(str(own['history']).lower(), own['history'])}
    if not config.get('ip_address') or config.get('ip_address') == 'replace_me':
        raise weewx.ViolatedPrecondition(f"[[sources]] [[[{name}]]] needs an ip_address")
    return config


def source_configs(stn_dict):
    """[(name, config)] for each gateway in the driver's section of weewx.conf, best first.

    Without [[sources]] there is one gateway, called 'gateway', and its config
    is the section itself. With [[sources]], each subsection is a gateway: it
    inherits the section's settings (except those in NOT_INHERITED) and can
    override any of them. source_priority orders them; by default they are in
    the order they are listed. A gateway with 'enabled = False' is left out.
    """

    listed = stn_dict.get('sources') or {}
    if not listed:
        return [('gateway', stn_dict)]
    order = as_names(stn_dict.get('source_priority'))
    unknown = [n for n in order if n not in listed]
    if unknown:
        log.error('source_priority: ignoring %s, not in [[sources]]', ', '.join(unknown))
    order = [n for n in order if n in listed] + [n for n in listed if n not in order]
    configs = []
    for name in order:
        own = listed[name]
        if not hasattr(own, 'keys'):
            raise weewx.ViolatedPrecondition(f"[[sources]] {name} must be a subsection: [[[{name}]]]")
        if str(own.get('enabled', 'True')).lower() in ('false', 'no', 'off', '0'):
            log.info('gateway %s is disabled', name)
            continue
        configs.append((name, _source_config(stn_dict, name, own)))
    if not configs:
        raise weewx.ViolatedPrecondition('[[sources]]: every gateway is disabled')
    addresses = [c['ip_address'] for _, c in configs]
    if len(set(addresses)) < len(addresses):
        log.warning('[[sources]]: two gateways have the same ip_address')
    return configs


def counter_fields(config_dict):
    """The cumulative fields that are merged as totals: DEFAULT_COUNTERS and every
    [StdWXCalculate] [[Delta]] input (a user may work out rain from another total)."""

    rules = (config_dict.get('StdWXCalculate') or {}).get('Delta') or {}
    inputs = [str(rule.get('input')) for rule in rules.values()
              if hasattr(rule, 'get') and rule.get('input')]
    return tuple(dict.fromkeys(list(DEFAULT_COUNTERS) + inputs))


class SourcePriority:
    """The order in which gateways are asked for a field: [[priority]] (exact
    field names, or a prefix ending in '*') and otherwise the gateways' own
    order."""

    def __init__(self, names, overrides=None):
        self.names = list(names)
        self.exact, self.prefixes = {}, []
        for key, value in (overrides or {}).items():
            listed = as_names(value)
            unknown = [n for n in listed if n not in self.names]
            if unknown:
                log.error('[[priority]] %s: ignoring %s, not in [[sources]]', key, ', '.join(unknown))
            order = [n for n in listed if n in self.names]
            order += [n for n in self.names if n not in order]
            if key.endswith('*'):
                self.prefixes.append((key[:-1], order))
            else:
                self.exact[key] = order
        # the longest prefix wins
        self.prefixes.sort(key=lambda p: -len(p[0]))

    def order(self, field):
        if field in self.exact:
            return self.exact[field]
        for prefix, order in self.prefixes:
            if field.startswith(prefix):
                return order
        return self.names


class CounterArbiter:
    """Takes rain and lightning deltas from several gateways without counting any of it twice.

    Each gateway's delta covers the time since its previous total. A field is
    counted from one gateway (its provider) at a time. Another gateway's delta
    is only used for the time after what has been counted already (a delta
    that partly overlaps it is cut down in proportion), and only if that
    gateway comes first for the field or the provider has gone quiet for
    stale_after seconds. Deltas that arrive while the provider might still
    come back are held, and added when the other gateway takes over, so no
    rain is lost while waiting. (weewx-ecowitt-multi drops a delta that partly
    overlaps, losing up to one poll's rain at each change of gateway.)
    """

    def __init__(self, priority, stale_after=DEFAULT_MAX_AGE):
        self.priority, self.stale_after = priority, stale_after
        self.last_ts = {}          # (gateway, field) -> time of the gateway's previous total
        self.provider = {}         # field -> gateway
        self.covered_until = {}    # field -> time up to which the field has been counted
        self.held = {}             # (gateway, field) -> deltas after covered_until, not counted yet

    def _rank(self, field, name):
        order = self.priority(field)
        return order.index(name) if name in order else len(order)

    def update(self, name, field, delta, ts):
        """The delta to count for field from gateway name's total at ts (None: count nothing)."""

        prev_ts = self.last_ts.get((name, field))
        self.last_ts[(name, field)] = ts
        if delta is None or prev_ts is None or ts <= prev_ts:
            return None
        provider, covered = self.provider.get(field), self.covered_until.get(field)
        if provider is not None and provider != name:
            if covered is not None and ts <= covered:
                # all of it has been counted already
                self.held.pop((name, field), None)
                return None
            if covered is not None and prev_ts < covered:
                # partly counted already: keep the share after covered
                delta = delta * (ts - covered) / (ts - prev_ts)
                self.held.pop((name, field), None)
            better = self._rank(field, name) < self._rank(field, provider)
            stale = covered is None or ts - covered > self.stale_after
            if not (better or stale):
                self.held[(name, field)] = self.held.get((name, field), 0.0) + delta
                return None
            log.info('%s is now counted from gateway %s (was %s)', field, name, provider)
            delta += self.held.pop((name, field), 0.0)
        # anything other gateways held now overlaps what has been counted
        for key in [k for k in self.held if k[1] == field and k[0] != name]:
            del self.held[key]
        self.provider[field], self.covered_until[field] = name, ts
        return delta


class MergedTotals:
    """One cumulative total per counter field (t_rainyear, p_rainyear,
    lightningcount ...) from the totals of several gateways.

    The merged total starts at the first total a gateway reports, rises by the
    rise of one gateway's total at a time (a CounterArbiter decides which) and
    follows the counting gateway when its total resets. So WeeWX's [[Delta]]
    works out the rain (or lightning) counted once, whichever gateway it came
    from. With a single gateway counting throughout, the merged total is that
    gateway's own total.
    """

    def __init__(self, priority, fields=DEFAULT_COUNTERS, stale_after=DEFAULT_MAX_AGE):
        self.priority = priority
        self.fields = tuple(fields)
        self.arbiter = CounterArbiter(priority, stale_after)
        self.last = {}             # (gateway, field) -> the gateway's previous total
        self.total = {}            # field -> merged total

    def _reset(self, name, field, value):
        """A gateway's total went down: if it is the one counting, follow it."""

        if self.arbiter.provider.get(field) in (name, None):
            log.info('%s: gateway %s reset its total (%s -> %s)', field, name,
                     self.last.get((name, field)), value)
            self.total[field] = value

    def update(self, name, field, value, ts):
        """Gateway name reported total value of field at ts (a loop packet)."""

        if value is None:
            return
        prev = self.last.get((name, field))
        self.last[(name, field)] = value
        delta = value - prev if prev is not None and value >= prev else None
        counted = self.arbiter.update(name, field, delta, ts)
        if self.total.get(field) is None:
            self.total[field] = value
        elif prev is not None and value < prev:
            self._reset(name, field, value)
        elif counted is not None:
            self.total[field] = round(self.total[field] + counted, 6)

    def add_interval(self, field, end, values):
        """The totals {gateway: total} of field at the end of a catchup interval
        (end): count the interval once, from the first gateway in the field's
        priority order whose rise doesn't overlap what has been counted."""

        values = {n: v for n, v in values.items() if v is not None}
        if not values:
            return
        order = list(self.priority(field)) + [n for n in values if n not in self.priority(field)]
        covered = self.arbiter.covered_until.get(field)
        provider = self.arbiter.provider.get(field)
        chosen = counted = None
        for name in order:
            prev, prev_end = self.last.get((name, field)), self.arbiter.last_ts.get((name, field))
            if name not in values or prev is None or values[name] < prev:
                continue
            if covered is not None and prev_end is not None and prev_end < covered:
                continue
            chosen, counted = name, values[name] - prev
            break
        if self.total.get(field) is None:
            self.total[field] = values[next(n for n in order if n in values)]
        elif provider in values and self.last.get((provider, field)) is not None \
                and values[provider] < self.last[(provider, field)]:
            self._reset(provider, field, values[provider])
        elif chosen is not None:
            self.total[field] = round(self.total[field] + counted, 6)
        if chosen is not None:
            self.arbiter.provider[field], self.arbiter.covered_until[field] = chosen, end
        for name, value in values.items():
            self.last[(name, field)] = value
            self.arbiter.last_ts[(name, field)] = end


class GatewayMerger:
    """Merges the loop packets of several gateways into one.

    Each field comes from the first gateway in its priority order whose latest
    packet is at most max_age seconds old and has a value for it (readings
    dropped by [[limits]] or ignore_no_signal have none, so the next gateway is
    used). The counter fields (the totals WeeWX's [[Delta]] reads) come from a
    MergedTotals.
    """

    NOT_MERGED = ('dateTime', 'usUnits', 'interval', 'gateway_online')

    def __init__(self, names, overrides=None, max_age=DEFAULT_MAX_AGE, counters=DEFAULT_COUNTERS,
                 debug=False):
        self.priority = SourcePriority(names, overrides)
        self.max_age = max_age
        self.counters = tuple(counters)
        self.debug = debug
        self.totals = MergedTotals(self.priority.order, self.counters, stale_after=max_age)
        self.cache = {}            # gateway -> (ts, packet)
        self.provenance = {}       # field -> gateway, for the last merged packet

    def add(self, name, packet):
        """A gateway's loop packet."""

        ts = packet['dateTime']
        for field in self.counters:
            if field in packet:
                self.totals.update(name, field, packet[field], ts)
        self.cache[name] = (ts, {k: v for k, v in packet.items() if k not in self.NOT_MERGED})

    def fresh(self, now):
        return {n: p for n, (ts, p) in self.cache.items() if now - ts <= self.max_age}

    def merged(self, now, unit_system=DEFAULT_UNIT_SYSTEM):
        """The merged packet at time now."""

        fresh = self.fresh(now)
        packet, provenance = {}, {}
        for field in {f for p in fresh.values() for f in p}:
            if field in self.counters:
                if self.totals.total.get(field) is not None:
                    packet[field] = self.totals.total[field]
                    provenance[field] = self.totals.arbiter.provider.get(field, '-')
                continue
            for name in self.priority.order(field):
                value = fresh.get(name, {}).get(field)
                if value is not None:
                    packet[field], provenance[field] = value, name
                    break
            else:
                packet[field] = None
        self.provenance = provenance
        if self.debug:
            log_long('merge: ', ', '.join(f'{f}<{n}' for f, n in sorted(provenance.items())))
        packet.update(dateTime=int(now + 0.5), usUnits=unit_system)
        return packet


# outage restart state; module level so that it survives the engine restarts it causes
_LAST_GAP_RESTART = {'ts': None}


class GapMonitor:
    """After every gateway has been silent for longer than archive_interval,
    waits until they are all back (or max_gap_wait has passed) without emitting
    loop packets, then asks for an engine restart so that WeeWX's catchup fills
    the outage from the gateways' history (gap_mode = restart).

    Loop packets are held back while waiting because the first archive record
    written after the outage would stop the catchup from going back any further.
    """

    NORMAL, OUTAGE, RECOVERING = 'normal', 'outage', 'recovering'
    EMIT, HOLD, RESTART = 'emit', 'hold', 'restart'

    def __init__(self, archive_interval=300, mode=DEFAULT_GAP_MODE, max_gap_wait=DEFAULT_MAX_GAP_WAIT,
                 min_restart_interval=DEFAULT_MIN_RESTART_INTERVAL):
        self.archive_interval, self.mode = archive_interval, mode
        self.max_gap_wait, self.min_restart_interval = max_gap_wait, min_restart_interval
        self.state, self.outage_start, self.recover_start = self.NORMAL, None, None
        self.expected = set()      # gateways that were up before the outage

    def update(self, now, seen, delivered, history_capable):
        """What to do now. seen: {gateway: time of its last data};
        delivered: gateways with new data now; history_capable: gateways with a catchup source."""

        latest = max((t for t in seen.values() if t is not None), default=None)
        if self.state == self.NORMAL:
            if latest is not None and now - latest > self.archive_interval:
                self.state, self.outage_start = self.OUTAGE, latest
                self.expected = {n for n, t in seen.items()
                                 if t is not None and latest - t <= self.archive_interval}
                log.warning('no data from any gateway for %d seconds', now - latest)
                return self.HOLD
            return self.EMIT
        if not delivered:
            return self.HOLD
        if self.state == self.OUTAGE:
            self.state, self.recover_start = self.RECOVERING, now
            log.info('gateways back after %d seconds: %s', now - self.outage_start,
                     ', '.join(sorted(delivered)))
        back = {n for n, t in seen.items() if t is not None and t >= self.recover_start}
        if not (self.expected <= back or now - self.recover_start >= self.max_gap_wait):
            return self.HOLD
        return self._finish(now, back & set(history_capable))

    def _finish(self, now, with_history):
        gap = self.recover_start - self.outage_start
        self.state, self.outage_start, self.recover_start = self.NORMAL, None, None
        if self.mode != 'restart':
            log.info('outage of %d seconds not filled (gap_mode = %s)', gap, self.mode)
            return self.EMIT
        if not with_history:
            log.warning('outage of %d seconds cannot be filled: no gateway with history is back', gap)
            return self.EMIT
        last = _LAST_GAP_RESTART['ts']
        if last is not None and now - last < self.min_restart_interval:
            log.warning('outage of %d seconds not filled: the last restart was only %d seconds ago '
                        '(min_restart_interval)', gap, now - last)
            return self.EMIT
        log.info('restarting so that catchup fills the %d second outage from %s', gap,
                 ', '.join(sorted(with_history)))
        _LAST_GAP_RESTART['ts'] = now
        return self.RESTART


def aggregation(field, counters=DEFAULT_COUNTERS):
    """How a field's history values are combined into one archive record."""

    group = weewx.units.obs_group_dict.get(field)
    if field in counters or group in ('group_rain', 'group_time', 'group_count'):
        # totals (the records have no rain of their own: WeeWX works it out)
        return 'last'
    if group == 'group_direction':
        return 'vecdir'
    if group == 'group_rainrate' or 'gust' in field.lower():
        return 'max'
    return 'avg'


def _combine(how, values):
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return next((v for v in reversed(values) if v is not None), None)
    if how == 'max':
        return max(numbers)
    if how == 'last':
        return numbers[-1]
    if how == 'vecdir':
        x = sum(math.sin(math.radians(v)) for v in numbers)
        y = sum(math.cos(math.radians(v)) for v in numbers)
        return None if abs(x) < 1e-9 and abs(y) < 1e-9 else round(math.degrees(math.atan2(x, y)) % 360.0, 1)
    return sum(numbers) / len(numbers)


def aggregate_records(records, interval, counters=DEFAULT_COUNTERS):
    """{end: record}: records combined into archive intervals (end - interval, end], interval in seconds."""

    buckets = {}
    for rec in sorted(records, key=lambda r: r['dateTime']):
        end = int(math.ceil(rec['dateTime'] / interval) * interval)
        buckets.setdefault(end, []).append(rec)
    result = {}
    for end, recs in buckets.items():
        fields = {f for r in recs for f in r} - {'dateTime', 'usUnits', 'interval'}
        result[end] = {f: _combine(aggregation(f, counters), [r.get(f) for r in recs]) for f in fields}
    return result


def merge_history(per_source, priority, interval, start_ts, stop_ts, totals,
                  unit_system=DEFAULT_UNIT_SYSTEM):
    """Archive records for the intervals ending after start_ts, up to stop_ts,
    from {gateway: {end: record}}: each field from the first gateway with a
    value; the counter fields from totals (a MergedTotals), which also takes
    the intervals ending at or before start_ts, so that the first record's
    rain is known and the loop packets carry on from the last record."""

    priority = priority if callable(priority) else priority.order
    records = []
    for end in sorted({e for b in per_source.values() for e in b if e <= stop_ts}):
        have = {n: b[end] for n, b in per_source.items() if end in b}
        for field in totals.fields:
            totals.add_interval(field, end, {n: r.get(field) for n, r in have.items()})
        if end <= start_ts:
            continue
        rec = {}
        for field in {f for r in have.values() for f in r} - set(GatewayMerger.NOT_MERGED):
            if field in totals.fields:
                if totals.total.get(field) is not None:
                    rec[field] = totals.total[field]
                continue
            rec[field] = next((have[n][field] for n in priority(field)
                               if have.get(n, {}).get(field) is not None), None)
        rec.update(dateTime=end, usUnits=unit_system, interval=interval // 60)
        records.append(rec)
    return records


# ============================================================================
#                            class EcowittCommon
# ============================================================================

class EcowittCommon:
    """What the driver and the service have in common: the GatewaySources.

    Usually there is one gateway, self.source. With [[sources]] there is a
    GatewaySource per gateway in self.sources (best first) and a GatewayMerger,
    self.merger, combines their packets; self.source is then the first gateway.

    The per-gateway work (settings, field map, collector, catchup, logging)
    is done by the GatewaySource; the attributes below are kept so that code
    written for ecowitt_http.py (eg driver.collector, driver.mapper) still
    works.
    """

    def __init__(self, unit_system=None, label=None, counters=None, **ec_config):
        """Initialise an EcowittCommon object.

        counters: the cumulative fields WeeWX's [[Delta]] reads (see
        counter_fields()); only used with several gateways."""

        label = label if label is not None else type(self).__name__
        configs = source_configs(ec_config)
        if len(configs) > 1:
            log.info('%s: %d gateways, in priority order: %s', label, len(configs),
                     ', '.join(f"{n} ({c['ip_address']})" for n, c in configs))
        self.sources = []
        for name, config in configs:
            if len(configs) > 1:
                log.info('%s: gateway %s:', label, name)
            self.sources.append(GatewaySource(name, config, unit_system=unit_system,
                                              label=f'{label} {name}' if len(configs) > 1 else label))
        self.merger = None
        if self.multi:
            self.merger = GatewayMerger([s.name for s in self.sources], ec_config.get('priority'),
                                        max_age=int(ec_config.get('max_age', DEFAULT_MAX_AGE)),
                                        counters=counters or DEFAULT_COUNTERS,
                                        debug=DebugOptions(**ec_config).merge)
            for field, order in sorted(self.merger.priority.exact.items()) + sorted(
                    (f'{p}*', o) for p, o in self.merger.priority.prefixes):
                log.info('%s: %s comes from %s', label, field, ', '.join(order))
            log.info('%s: totals merged for WeeWX to count once: %s', label, ', '.join(self.merger.counters))
            # gateway_online_<name> has the unit group of gateway_online
            for source in self.sources:
                weewx.units.obs_group_dict.setdefault(f'gateway_online_{source.name}', 'group_boolean')

    @property
    def multi(self):
        """Whether there is more than one gateway ([[sources]])."""

        return len(self.sources) > 1

    @property
    def source(self):
        """The gateway (the first one, with several)."""

        return self.sources[0]

    def drain_sources(self, label):
        """Feed everything the gateways' collectors have queued into the merger.

        Returns the names of the gateways that delivered data. A gateway that
        stops answering is logged once and skipped: the others carry on. Raises
        DeviceIOError if a collector has shut down."""

        delivered = set()
        for source in self.sources:
            while True:
                try:
                    item = source.get(0)
                except queue.Empty:
                    break
                if hasattr(item, 'keys'):
                    if not source.log_failures:
                        log.info('%s: gateway %s is answering again', label, source.name)
                        source.set_failure_logging(True)
                    source.log_received(item)
                    self.merger.add(source.name, source.loop_packet(item))
                    delivered.add(source.name)
                elif isinstance(item, DeviceIOError):
                    if source.log_failures:
                        log.warning('%s: gateway %s is not answering: %s', label, source.name, item)
                        source.set_failure_logging(False)
                elif isinstance(item, BaseException):
                    log.error('%s: gateway %s: unexpected exception %s: %s', label, source.name,
                              item.__class__.__name__, item)
                elif item is None:
                    raise DeviceIOError(f'the collector for gateway {source.name} has shut down')
        return delivered

    def online_fields(self, now, max_age):
        """gateway_online (1 if any gateway answered within max_age seconds of
        now) and, with several gateways, gateway_online_<name> for each."""

        fields = {f'gateway_online_{s.name}': s.online(now, max_age) for s in self.sources}
        online = int(any(fields.values()))
        return {'gateway_online': online, **fields} if self.multi else {'gateway_online': online}

    def stop_sources(self):
        """Stop polling every gateway."""

        for source in self.sources:
            source.stop()

    @property
    def collector(self):
        return self.source.collector

    @property
    def mapper(self):
        return self.source.mapper

    @property
    def driver_debug(self):
        return self.source.driver_debug

    @property
    def ip_address(self):
        return self.source.ip_address

    @property
    def poll_interval(self):
        return self.source.poll_interval

    @property
    def url_timeout(self):
        return self.source.url_timeout

    @property
    def active_fields(self):
        return self.source.active_fields

    def log_active_map(self, data, label=None):
        self.source.log_active_map(data, label)

    def log_rain_data(self, data, preamble=None):
        self.source.log_rain_data(data, preamble)

    def log_wind_data(self, data, preamble=None):
        self.source.log_wind_data(data, preamble)


# ============================================================================
#                          class EcowittHttpService
# ============================================================================

class EcowittHttpService(weewx.engine.StdService, EcowittCommon):
    """Ecowitt HTTP API device service class.

    A WeeWX service to augment loop packets with observational data obtained
    from a device via the Ecowitt local HTTP API. The EcowittHttpService is
    useful when data is required from more than one source; for example, WeeWX
    is using another driver and the EcowittHttpDriver cannot be used.

    Data is obtained via the Ecowitt local HTTP API. The data is parsed and
    mapped to WeeWX fields and if the device data is not stale the loop packet
    is augmented with the mapped device data.

    Class EcowittHttpCollector collects and parses data from the API. The
    EcowittHttpCollector runs in a separate thread so it does not block the
    main WeeWX processing loop. The EcowittHttpCollector is turn uses child
    class EcowittDevice to interact with the Ecowitt device and obtain and
    parse data from the API.
    """

    def __init__(self, engine, config_dict):
        """Initialise an EcowittHttpService object."""

        # first extract the service config dictionary, try looking for
        # [EcowittHttpService]
        if 'EcowittHttpService' in config_dict:
            gw_config_dict = config_dict['EcowittHttpService']
        else:
            # we don't have a [EcowittHttpService] stanza so use [EcowittGateway]
            # (or the older [EcowittGateway]) if it exists otherwise use an empty config
            gw_config_dict = driver_config(config_dict)

        # Log our driver version first. Normally we would call our superclass
        # initialisation method first; however, that involves establishing a
        # network connection to the device and it may fail. Doing our logging
        # first will aid in remote debugging.

        log.info('EcowittHttpService: version is %s' % DRIVER_VERSION)
        use_station_columns(config_dict, 'EcowittHttpService')
        self.unit_system = DEFAULT_UNIT_SYSTEM
        try:
            EcowittCommon.__init__(self, unit_system=self.unit_system, label='EcowittHttpService',
                                   counters=counter_fields(config_dict), **gw_config_dict)
        except weewx.ViolatedPrecondition as e:
            raise ServiceInitializationError from e
        weewx.engine.StdService.__init__(self, engine, config_dict)

        # age (in seconds) before API data is considered too old to use, use a
        # default
        self.max_age = int(gw_config_dict.get('max_age', DEFAULT_MAX_AGE))
        # minimum period in seconds between 'lost contact' log entries during
        # an extended lost contact period
        self.lost_contact_log_period = int(gw_config_dict.get('lost_contact_log_period',
                                                              DEFAULT_LOST_CONTACT_LOG_PERIOD))
        if self.driver_debug.any or weewx.debug > 0:
            log.info('     max age of API data to be used is %d seconds' % self.max_age)
            log.info('     lost contact will be logged every '
                     '%d seconds' % self.lost_contact_log_period)

        # set failure logging on
        self.log_failures = True
        self.lost_con_ts = None
        self.latest_sensor_data = None
        # start polling the device(s) (each collector runs in its own thread)
        for source in self.sources:
            source.start()
        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)

    def new_loop_packet(self, event):
        """Augment a loop packet with device data.

        When a new loop packet arrives process the queue looking for any device
        sensor data packets. If there are sensor data packets keep the most
        recent, non-stale packet and use it to augment the loop packet. If
        there are no sensor data packets, or they are all stale, then the loop
        packet is not augmented.

        The queue may also contain other control data, eg exception reporting
        from the EcowittHttpCollector thread. This control data needs to be
        processed as well.
        """

        # several gateways: their data is merged
        if self.multi:
            self.new_loop_packet_merged(event)
            return
        # log the loop packet received if necessary, there are several debug
        # settings that may require this
        if self.driver_debug.loop or self.driver_debug.rain or self.driver_debug.wind:
            log_dict(f"EcowittHttpService: Processing loop packet: "
                     f"{timestamp_to_string(event.packet['dateTime'])} ", event.packet)
        # we are about to process the queue so reset our latest sensor data
        # packet property
        self.latest_sensor_data = None
        while True:
            try:
                queue_data = self.source.get(0.5)
            except queue.Empty:
                # the queue is now empty, but that may be because we have
                # already processed any queued data, log if necessary and break
                # out of the while loop
                if self.latest_sensor_data is None and (self.driver_debug.loop or self.driver_debug.rain or self.driver_debug.wind):
                    log.info('EcowittHttpService: No queued items to process')
                if self.lost_con_ts is not None and time.time() > self.lost_con_ts + self.lost_contact_log_period:
                    self.lost_con_ts = time.time()
                    self.set_failure_logging(True)
                break
            else:
                # We received something in the queue, it will be one of three
                # things:
                # 1. a dict containing sensor data
                # 2. an exception
                # 3. the value None signalling a serious error that means the
                #    Collector needs to shut down

                # if the data has a 'keys' attribute it is a dict so must be
                # data
                if hasattr(queue_data, 'keys'):
                    # we have a dict so assume it is data
                    self.lost_con_ts = None
                    self.set_failure_logging(True)
                    # log the received data if the debug options ask for it
                    self.source.log_received(queue_data)
                    self.process_queued_sensor_data(queue_data, event.packet['dateTime'])

                # if it's a tuple then it's a tuple with an exception and
                # exception text
                elif isinstance(queue_data, BaseException):
                    # We have an exception. The collector did not deem it
                    # serious enough to want to shut down, or it would have
                    # sent None instead. The action we take depends on the type
                    # of exception it is. If it's a DeviceIOError we can ignore
                    # it as appropriate action will have been taken by the
                    # EcowittHttpCollector. If it is anything else we log it.
                    # process the exception
                    self.process_queued_exception(queue_data)

                elif queue_data is None:
                    # if debug_loop log what we received
                    if self.driver_debug.loop:
                        log.info('EcowittHttpService: Received collector shutdown signal')
                    # we received the signal that the EcowittHttpCollector
                    # needs to shut down, that means we cannot continue so call
                    # our shutdown method which will also shut down the
                    # EcowittHttpCollector thread
                    self.shutDown()
                    # the EcowittHttpCollector has been shut down, so we will
                    # not see anything more in the queue. We are still bound to
                    # NEW_LOOP_PACKET but since the queue is always empty we
                    # will just wait for the empty queue timeout before exiting

                # if it's none of the above (which it should never be) we don't
                # know what to do with it so pass and wait for the next item in
                # the queue
                else:
                    pass

        # gateway_online: 1 if the gateway has answered within max_age, else 0
        event.packet['gateway_online'] = self.source.online(event.packet['dateTime'], self.max_age)
        if self.latest_sensor_data is not None:
            # we have a sensor data packet
            # map the raw data to WeeWX loop packet fields (with usUnits)
            mapped_data = self.source.mapped(self.latest_sensor_data)
            self.augment_packet(event.packet, mapped_data)
            # log the augmented packet if necessary, there are several debug
            # settings that may require this, start from the highest (most
            # encompassing) and work to the lowest (least encompassing)
            if self.driver_debug.loop or weewx.debug >= 2:
                log_dict(f"EcowittHttpService: Augmented packet: "
                         f"{timestamp_to_string(event.packet['dateTime'])} ", event.packet)
            else:
                # perhaps we have individual debugs such as rain or wind
                if self.driver_debug.rain:
                    # debug_rain is set so log the 'rain' field in the
                    # augmented loop packet, if it does not exist say
                    # so
                    self.log_rain_data(event.packet, 'EcowittHttpService: Augmented packet')
                if self.driver_debug.wind:
                    # debug_wind is set so log the 'wind' fields in the
                    # loop packet being emitted, if they do not exist
                    # say so
                    self.log_wind_data(event.packet, 'EcowittHttpService: Augmented packet')

    def new_loop_packet_merged(self, event):
        """Augment a loop packet with the merged data of several gateways.

        Every item the gateways have queued goes into the merger (so the
        merged totals see every poll), then the merged data adds the fields the
        loop packet doesn't have, as with one gateway. gateway_online and
        gateway_online_<name> are set."""

        packet = event.packet
        if self.driver_debug.loop or self.driver_debug.rain or self.driver_debug.wind:
            log_dict(f"EcowittHttpService: Processing loop packet: "
                     f"{timestamp_to_string(packet['dateTime'])} ", packet)
        try:
            self.drain_sources('EcowittHttpService')
        except DeviceIOError as e:
            log.error('EcowittHttpService: %s', e)
        merged = self.merger.merged(packet['dateTime'], self.unit_system)
        merged = {k: v for k, v in merged.items() if k != 'dateTime'}
        if len(merged) > 1:
            self.augment_packet(packet, merged)
        packet.update(self.online_fields(packet['dateTime'], self.max_age))
        if self.driver_debug.loop or weewx.debug >= 2:
            log_dict(f"EcowittHttpService: Augmented packet: "
                     f"{timestamp_to_string(packet['dateTime'])} ", packet)

    def process_queued_sensor_data(self, sensor_data, date_time):
        """Process a sensor data packet received in the collector queue.

        When the queue is processed there may be multiple sensor data packets
        in the queue, but we only want the most recent, non-stale packet. Check
        the received sensor packet is timestamped and not stale, if it is not
        stale and is newer than the previously saved sensor data packet then
        replace the saved packet with this packet.

        Non-timestamped sensor data packets are discarded.

        sensor_data: the sensor data packet obtained from the queue
        date_time:   the timestamp of the current loop packet
        """

        # first up check we have a field 'datetime' and that it is not None
        if 'datetime' in sensor_data and sensor_data['datetime'] is not None:
            if sensor_data['datetime'] > date_time - self.max_age:
                # the sensor data is not stale, but is it more recent than our
                # current saved packet
                if self.latest_sensor_data is None or sensor_data['datetime'] > self.latest_sensor_data['datetime']:
                    # this packet is newer, so keep it
                    self.latest_sensor_data = dict(sensor_data)
            elif self.driver_debug.loop or weewx.debug >= 2:
                # the sensor data is stale and we have debug settings that
                # dictate we log the discard
                log.info('EcowittHttpService: Discarded packet with '
                         'timestamp %s' % timestamp_to_string(sensor_data['datetime']))
        elif self.driver_debug.loop or weewx.debug >= 2:
            # the sensor data is not timestamped so it will be discarded and we
            # have debug settings that dictate we log the discard
            log.info('EcowittHttpService: Discarded non-timestamped packet')

    def process_queued_exception(self, e):
        """Process an exception received in the collector queue."""

        # is it a DeviceIOError
        if isinstance(e, DeviceIOError):
            # set our failure logging appropriately
            if self.lost_con_ts is None:
                self.lost_con_ts = time.time()
                # any failure logging for this failure will already have
                # occurred in our EcowittHttpCollector object and its
                # EcowittDevice object, so turn off failure logging
                self.set_failure_logging(False)
            elif self.log_failures:
                # we are already in a lost contact state, but failure logging
                # may have been turned on for a 'once in a while' log entry so
                # we need to turn it off again
                self.set_failure_logging(False)
        else:
            log.error('EcowittHttpService: Caught unexpected exception %s: %s' % (e.__class__.__name__,
                                                                                  e))

    def augment_packet(self, packet, data):
        """Augment a loop packet with data from another packet.

        The data to be used for augmentation (the new data) may not be in the
        same unit system as the loop data being augmented so the new data is
        converted to the same unit system as used in the loop packet before
        augmentation occurs. Only fields that exist in the new data but not in
        the loop packet are added to the loop packet.

        packet: dict containing the loop packet
        data:   dict containing the data to be used to augment the loop packet
        """

        if self.driver_debug.loop:
            log.info('EcowittHttpService: Mapped data will be used to augment loop packet(%s)',
                     timestamp_to_string(packet['dateTime']))
        # But the mapped data must be converted to the same unit system as
        # the packet being augmented. First get a converter.
        converter = weewx.units.StdUnitConverters[packet['usUnits']]
        converted_data = converter.convertDict(data)
        # if required log the converted data
        if self.driver_debug.loop:
            log_dict(f"EcowittHttpService: Converted "
                     f"{'merged' if self.multi else self.source.model} data: ", converted_data)
        for field, field_data in converted_data.items():
            # Any existing packet fields, whether they contain data or are
            # None, are respected and left alone. Only fields from the
            # converted data that do not already exist in the packet are
            # used to augment the packet.
            if field not in packet:
                packet[field] = field_data

    def set_failure_logging(self, log_failures):
        """Turn failure logging on or off (see GatewaySource.set_failure_logging())."""

        self.log_failures = log_failures
        self.source.set_failure_logging(log_failures)

    def shutDown(self):
        """Shut down the service."""

        # stop the collector thread(s)
        self.stop_sources()


# ============================================================================
#                 [Accumulator] entries for the driver's fields
# ============================================================================

# fields counted per loop packet, so totalled over each archive interval
ACCUMULATOR_SUM = ('t_rain', 'p_rain', 'lightning_strike_count')


def _keeps_last_value(source):
    """True for a device field whose last value in an archive interval is the
    one to keep, not the average: battery and signal states, running rain
    totals, the lightning count, distance and time, the day's maximum wind,
    the 10 minute average wind direction and 24 hour averages and AQIs."""

    if source.endswith(('.battery', '.signal', '_24HAQI', '_24H')) or source.startswith('lightning.'):
        return True
    if source in HttpMapper.default_rain_map.values():
        # a rain rate is averaged
        return not source.endswith('.0x0E.val')
    return source in ('common_list.0x19.val', 'common_list.0x6D.val')


def accumulator_extractors():
    """{WeeWX field: extractor} for the driver's fields that are not averaged
    over an archive interval: the fields of the default field map (with
    either WN32 setting) and the counts WeeWX works out from them."""

    extractors = {field: 'sum' for field in ACCUMULATOR_SUM}
    for wn32 in (False, True):
        mapper = HttpMapper(driver_debug=DebugOptions(), quiet=True, wn32_indoor=wn32, wn32_outdoor=wn32)
        for field, source in mapper.field_map.items():
            if _keeps_last_value(source):
                extractors.setdefault(field, 'last')
    for column, fields in STATION_COLUMNS.items():
        if any(extractors.get(f) == 'last' for f in fields):
            extractors.setdefault(column, 'last')
    return extractors


def accumulator_config_str():
    """The driver's [Accumulator] entries, as a weewx.conf snippet."""

    lines = ['[Accumulator]', '    # Start weewx-ecowittGateway extractors']
    for field, extractor in accumulator_extractors().items():
        lines += [f'    [[{field}]]', f'        extractor = {extractor}']
    lines.append('    # End weewx-ecowittGateway extractors')
    return '\n'.join(lines) + '\n'


# [Accumulator] entries that earlier versions added, for fields this version
# doesn't produce. Each is removed (tidy_accumulator()) if it is still as it
# was added. ecowitt_http.py 0.1.0a28:
ECOWITT_HTTP_ACCUMULATOR_LAST = ('co2_24h_avg', 'heap_free', 'lightning_distance', 'lightning_last_det_time',
                                 'p_stormRain', 'pm10_24h_avg', 'pm2_51_24h_avg', 'pm2_52_24h_avg',
                                 'pm2_53_24h_avg', 'pm2_54_24h_avg', 'pm2_55_24h_avg', 't_stormRain')
# an earlier extension installed as weewx-EcowittGateway (extractor: fields)
EARLIER_ACCUMULATOR = {
    'sum': ('lightning_strike_count', 't_rain', 'p_rain', 'hail', 'lightningcount', 'lightning_noise_count'),
    'max': ('rainRate', 'rrain_piezo', 'p_rainrate'),
    'last': ('gain0', 'gain1', 'gain2', 'gain3', 'gain4', 'gain5', 'maxdailygust', 'hourRain', 'dayRain',
             'weekRain', 'monthRain', 'yearRain', 'totalRain', 'erain_piezo', 'hrain_piezo', 'drain_piezo',
             'wrain_piezo', 'mrain_piezo', 'yrain_piezo', 'vpd', 'depth_ch1', 'depth_ch2', 'depth_ch3',
             'depth_ch4', 'wh25_batt', 'wh26_batt', 'wh40_batt', 'wn20_batt', 'wn38_batt', 'wh68_batt',
             'wh69_batt', 'ws68_batt', 'ws80_batt', 'ws85_batt', 'ws90_batt', 'ws85cap_volt', 'ws90cap_volt',
             'ws1900batt', 'console_batt', 'consoleext_batt', 'ldsbatt1', 'ldsbatt2', 'ldsbatt3',
             'ldsbatt4', 'bgtbatt', 'co2_24h', 'co2_Batt', 'co2in_24h', 'consBatteryVoltage',
             'consolebattp', 'hailBatteryStatus', 'inTempBatteryStatus', 'lightning_Batt',
             'lightning_disturber_count', 'outTempBatteryStatus', 'piezorain_batt', 'pm1_24h_co2',
             'pm1_24hAQI_co2', 'pm1_RealAQI_co2', 'pm4_24h_co2', 'pm4_24hAQI_co2', 'pm4_RealAQI_co2',
             'pm10_24h_co2', 'pm10_24hAQI_co2', 'pm10_RealAQI_co2', 'pm25_24h_co2', 'pm25_24hAQI_co2',
             'pm25_RealAQI_co2', 'rain_batt', 'rainBatteryStatus', 'wh80_batt', 'wh85_batt', 'wh90_batt',
             'windBatteryStatus', 'wn32_batt', 'wn32p_batt', 'wn64_batt', 'wn64batt', 'wqt01_batt',
             'wqt01batt', 'ws1800batt', 'ws6006batt', 'ws6210_batt', '24havpm255', 'co2_24h_avg',
             'dayHail', 'daymaxwind', 'lightning_distance', 'lightning_last_det_time', 'p_dayrain',
             'p_eventrain', 'p_hourrain', 'p_monthrain', 'p_weekrain', 'p_yearrain', 'pm10_24h_avg',
             'stormRain', 'totalRain_piezo', 'wh45_batt', 'wh57_batt', 'wh65_batt', 'winddir_avg10m',
             'windspdmph_avg10m')
             + tuple(f'batteryStatus{n}' for n in range(1, 9))
             + tuple(f'{f}{n}' for f in ('leafWetBatt', 'leak_Batt', 'pm25_Batt') for n in range(1, 9))
             + tuple(f'{f}_ch{n}' for f in ('pm25_AQI_24h', 'pm25_avg_24h', 'pm25_RealAQI') for n in range(1, 5))
             + tuple(f'soilMoistBatt{n}{s}' for n in range(1, 17) for s in ('', 's'))
             + tuple(f'soilTempBatt{n}{s}' for n in range(1, 9) for s in ('', 's'))
             + tuple(f'pm2_5{n}_24h{a}' for n in range(1, 6) for a in ('_avg', 'av'))
             + tuple(f'{m}_ch{n}_batt' for m, last in (('wh31', 8), ('wh41', 4), ('wh51', 16), ('wh55', 4),
                                                        ('wn35', 8)) for n in range(1, last + 1)),
}
EARLIER_ACCUMULATOR_FIRSTLAST = ('stationtype', 'apName', 'model')


def configured_fields(config_dict):
    """The WeeWX fields named in [[field_map]] and [[field_map_extensions]] of
    the driver's section and its [[sources]]."""

    section = driver_config(config_dict)
    sections = [section] + [s for s in (section.get('sources') or {}).values() if hasattr(s, 'get')]
    return {field for s in sections for name in ('field_map', 'field_map_extensions')
            for field in (s.get(name) or {})}


def tidy_accumulator(config_dict):
    """Tidy the [Accumulator] entries earlier versions left.

    Removes the entries that ecowitt_http.py and an earlier extension
    installed as weewx-EcowittGateway added for fields this driver doesn't
    produce (unless they are in the field map settings), but only those still
    as they were added. An entry totalling lightningcount is changed to keep
    its last value: here lightningcount is the gateway's running count.
    Returns the names of the entries removed and of those changed."""

    accum = config_dict.get('Accumulator')
    if not hasattr(accum, 'keys'):
        return [], []
    produced = set(accumulator_extractors()) | set(HttpMapper.default_map) | configured_fields(config_dict)
    added = {}
    for extractor, fields in EARLIER_ACCUMULATOR.items():
        for field in fields:
            added.setdefault(field, []).append({'extractor': extractor})
    for field in ECOWITT_HTTP_ACCUMULATOR_LAST:
        added.setdefault(field, []).append({'extractor': 'last'})
    for field in EARLIER_ACCUMULATOR_FIRSTLAST:
        added.setdefault(field, []).append({'accumulator': 'firstlast', 'extractor': 'last'})
    removed, changed = [], []
    for field, entries in added.items():
        if field not in produced and hasattr(accum.get(field), 'keys') and dict(accum[field]) in entries:
            del accum[field]
            removed.append(field)
    if hasattr(accum.get('lightningcount'), 'keys') and dict(accum['lightningcount']) == {'extractor': 'sum'}:
        accum['lightningcount']['extractor'] = 'last'
        changed.append('lightningcount')
    return removed, changed


def check_rain_rule(config_dict, field_map):
    """Log a warning if WeeWX can't work out 'rain' from what the driver
    produces: [StdWXCalculate] [[Delta]] [[[rain]]] (WeeWX's default is
    totalRain) names a total that isn't in the field map. Returns the input
    field if so, else None."""

    calc = config_dict.get('StdWXCalculate') or {}
    if str((calc.get('Calculations') or {}).get('rain', 'prefer_hardware')).lower() == 'hardware':
        return None
    rule = (calc.get('Delta') or {}).get('rain')
    source = str(rule.get('input', '')) if hasattr(rule, 'get') else 'totalRain'
    if source in field_map:
        return None
    log.warning("WeeWX 'rain' will be empty: [StdWXCalculate] [[Delta]] [[[rain]]] input is %s, which this "
                "driver does not produce. Set it to t_rainyear (tipping gauge) or p_rainyear (piezo gauge), "
                "for example with 'weectl station reconfigure'.", source or '(empty)')
    return source


# ============================================================================
#           Ecowitt HTTP driver Loader/Configurator/Editor methods
# ============================================================================

def driver_config(config_dict):
    """The driver's config section: [EcowittGateway], or ecowitt_http.py's [EcowittHttp] if there is no
    [EcowittGateway] yet."""
    if DRIVER_NAME in config_dict:
        return config_dict[DRIVER_NAME]
    if LEGACY_DRIVER_NAME in config_dict:
        log.info('Using the [%s] section of weewx.conf; rename it [%s]', LEGACY_DRIVER_NAME, DRIVER_NAME)
        return config_dict[LEGACY_DRIVER_NAME]
    return {}


def std_archive_interval(config_dict):
    """[StdArchive] archive_interval in seconds (300 if not set)."""

    return weeutil.weeutil.to_int(config_dict.get('StdArchive', {}).get('archive_interval', 300))


def loader(config_dict, engine):
    use_station_columns(config_dict)
    driver = EcowittHttpDriver(archive_interval=std_archive_interval(config_dict),
                               counters=counter_fields(config_dict),
                               **driver_config(config_dict))
    check_rain_rule(config_dict, driver.mapper.field_map)
    return driver


def configurator_loader(config_dict):  # @UnusedVariable

    return EcowittHttpDriverConfigurator()


def confeditor_loader():
    return EcowittHttpDriverConfEditor()


# ============================================================================
#                     class EcowittHttpDriverConfEditor
# ============================================================================

class EcowittHttpDriverConfEditor(weewx.drivers.AbstractConfEditor):
    """Config editor class for the Ecowitt local HTTP driver."""

    # define our config as a multiline string so we can preserve comments
    # the driver's [Accumulator] entries (see accumulator_extractors())
    accum_config_str = accumulator_config_str()
    # Ecowitt cumulative rain fields, in order of preference, used to calculate
    # a WeeWX 'rain' field for a traditional type rainfall gauge
    t_src_fields = ('rain.0x13.val', 'rain.0x12.val', 'rain.0x11.val', 'rain.0x10.val')
    # Ecowitt cumulative rain fields, in order of preference, used to calculate
    # a WeeWX 'rain' field for a piezo type rainfall gauge
    p_src_fields = ('piezoRain.0x13.val', 'piezoRain.0x12.val', 'piezoRain.0x11.val', 'piezoRain.0x10.val')

    @property
    def default_stanza(self):
        return f"""
    [EcowittGateway]
        # This section is for the weewx-ecowittGateway driver (Ecowitt local HTTP API).

        # the driver to use
        driver = user.weewx-ecowittGateway

        # how often to poll the device
        poll_interval = {int(DEFAULT_POLL_INTERVAL):d}
        # how many attempts to contact the device before giving up
        max_tries = {int(DEFAULT_MAX_TRIES):d}
        # wait time in seconds between retries to contact the device
        retry_wait = {int(DEFAULT_RETRY_WAIT):d}
        # max wait for device to respond to a HTTP request
        url_timeout = {int(DEFAULT_URL_TIMEOUT):d}
        
        # whether to show all battery state data including nonsense data and 
        # sensors that are disabled sensors and connecting
        show_all_batt = False
        # whether to always log unknown API fields, unknown fields are always 
        # logged at the debug level, this will log them at the info level
        log_unknown_fields = False
        
        # How often to check for device firmware updates, 0 disables firmware 
        # update checks. Available firmware updates are logged.
        firmware_update_check_interval = 86400
        
        # With debug logging on, whether to log 'Next update in N seconds'
        # after every poll
        log_next_update = True

        # Whether to ignore the readings of multi-channel sensors (WN31, WN34,
        # WN35, WH41, WH51, WH54, WH55) while the device reports their signal
        # as 0
        ignore_no_signal = False

        # Extra log information to help debug particular areas, a comma
        # separated list of any of: rain, wind, loop, sensors, parser, catchup,
        # collector. For example:
        #     debug = rain, wind

        # Readings outside these limits are dropped (set to None), in loop
        # packets and catchup records: '<WeeWX field> = min, max' or
        # 'min, max, unit'. Without a unit the limits are in the device's
        # units (degree_C, meter_per_second, hPa, mm, mm_per_hour ...). Leave
        # min or max empty for no limit on that side, for example:
        #     outTemp = -50, 60
        #     windGust = , 75
        [[limits]]
    """

    # def get_conf(self, orig_stanza=None):
    #     """Given a configuration stanza, return a possibly modified copy
    #     that will work with the current version of the device driver.
    #
    #     The default behavior is to return the original stanza, unmodified.
    #
    #     Derived classes should override this if they need to modify previous
    #     configuration options or warn about deprecated or harmful options.
    #
    #     The return value should be a long string. See default_stanza above
    #     for an example string stanza.
    #     """
    #
    #     return self.default_stanza if orig_stanza is None else orig_stanza
    #
    # WeeWX only calls prompt_for_settings() when it is allowed to prompt
    # (ie not 'weectl station reconfigure --no-prompt'), and it calls
    # modify_config() on a different ConfEditor instance. So whether we may
    # prompt is recorded at class level. 'preset' holds answers supplied by
    # other means (eg the installer's ECOWITT_* environment variables); a
    # preset answer is used without prompting.
    prompting = False
    preset = {}

    @classmethod
    def ask(cls, key, prompt, default, options=None):
        """Obtain a setting from a preset answer, the user or the default.

        If a preset answer exists for key it is used. Otherwise, if we are
        prompting the user is prompted, if not the default is used.
        """

        if cls.preset.get(key) is not None:
            return str(cls.preset[key])
        if not cls.prompting:
            return default
        return weecfg.prompt_with_options(prompt, default, options)

    def prompt_for_settings(self):
        """Prompt for settings required for proper operation of this driver.

        Returns a dict of setting, value key pairs for settings to be included
        in the driver stanza. The _prompt() method may be used to prompt the
        user for input with a default.
        """

        # we were asked to prompt, so modify_config() may prompt as well
        EcowittHttpDriverConfEditor.prompting = True
        print()
        prompt = 'Specify the device IP address, for example: 192.168.1.100.'
        default = self.existing_options.get('ip_address')
        ip_address = weecfg.prompt_with_options(prompt, default)

        print()
        prompt = 'Specify how often to poll the device in seconds.'
        default = self.existing_options.get('poll_interval',
                                            DEFAULT_POLL_INTERVAL)
        poll_interval = int(weecfg.prompt_with_options(prompt, default))
        return {'ip_address': ip_address,
                'poll_interval': poll_interval
                }

    def modify_config(self, config_dict):
        """Make Ecowitt local HTTP API driver specific changes to WeeWX config."""

        import weecfg

        # set loop_on_init
        self.do_loop_on_init(config_dict)
        # configure rain calculations
        self.do_rain(config_dict)
        # configure lightning calculations
        self.do_lightning(config_dict)
        # configure archive record generation
        self.do_archive_record_generation(config_dict)
        # configure extractors
        self.do_extractors(config_dict)
        del weecfg

    @staticmethod
    def do_loop_on_init(config_dict):
        """Configure loop_on_init."""

        print()
        # obtain the current loop_on_init setting if it exists, default
        # to 1 (enabled)
        default = config_dict.get('loop_on_init', '1')
        prompt = """The Ecowitt HTTP driver requires a network connection to the device.
Consequently, the absence of a network connection when WeeWX starts will cause
WeeWX to exit. The WeeWX 'loop_on_init' setting can be used to mitigate such
problems by having WeeWX retry startup indefinitely. Set to '0' to attempt
startup once only or '1' to attempt startup indefinitely."""
        loop_on_init = int(EcowittHttpDriverConfEditor.ask('loop_on_init', prompt,
                                                            default, ['0', '1']))
        loop_on_init_config_str = f'loop_on_init = {loop_on_init:d}'
        loop_on_init_dict = configobj.ConfigObj(io.StringIO(loop_on_init_config_str))
        config_dict.merge(loop_on_init_dict)
        if len(config_dict.comments['loop_on_init']) == 0:
            config_dict.comments['loop_on_init'] = ['',
                                                    '# Whether to try indefinitely to load the driver']

    @staticmethod
    def do_rain(config_dict):
        """Configure rain calculations.

        Configure [StdWXCalculate] and [EcowittGateway] config stanzas to populate
        rain rate and per-period rain fields as per user requirements. A default
        install populates WeeWX fields t_rain and p_rain with per-period tipping
        and piezo rain respectively via the StdWXCalculate service. WeeWX
        fields t_rainrate and p_rainrate are populated with tipping and piezo
        rain rate data respectively via the driver field map.

        Consequently, setting the rain calculations is complex, possibly
        involving changes to StdWXCalculate and the driver config.
        """

        driver_config_dict = config_dict.get(DRIVER_NAME, {})
        # Get a HttpMapper object so we can access the field map being used. We
        # need the field map so we can 'talk' WeeWX field names with the
        # user - the user does not necessarily know the 'dotted' Ecowitt field
        # names.
        mapper = HttpMapper(driver_config_dict)
        # Determine the rain gauge type(s) paired with the device. To do this
        # we obtain an EcowittDevice and inspect the paired_rain_gauges
        # property.
        # first get the device IP address
        ip_address = driver_config_dict.get('ip_address')
        # make sure we have a [StdWXCalculate] section to work with
        if 'StdWXCalculate' not in config_dict:
            config_dict['StdWXCalculate'] = {}
        # Now get an EcowittDevice object and the paired_rain_gauges property,
        # an unreachable device is not fatal. There is no need to contact the
        # device if the paired gauges have been preset (eg by the installer).
        preset_paired = EcowittHttpDriverConfEditor.preset.get('paired_gauges')
        if preset_paired is not None:
            paired_str = preset_paired
        else:
            try:
                device = make_device(ip_address,
                                     api=driver_config_dict.get('api', DEFAULT_API),
                                     tcp_port=weeutil.weeutil.to_int(driver_config_dict.get('tcp_port',
                                                                                            DEFAULT_TCP_PORT)))
                paired = device.paired_rain_gauges
            except Exception as e:
                print()
                print(f"Unable to contact the device at '{ip_address}' to determine "
                      f"the paired rain gauges: {e}")
                if not EcowittHttpDriverConfEditor.prompting:
                    print("Rain calculations have not been changed.")
                    return
                # we can ask the user, offer 'both' as the default
                paired_str = 'both'
            else:
                # 'paired' is a tuple, it is empty if no gauges are paired
                paired_str = 'none'
                if paired:
                    paired_str = 'both' if len(paired) == 2 else paired[0]
        _prompt = """Ecowitt gateways/consoles can simultaneously support both tipping and 
piezoelectric (piezo) rain gauges. Select the gauge type(s) paired with this device. Set
to 'none' if no gauges are paired, 'tipping' if only a tipping gauge is paired, 
'piezo' if only a piezo gauge is paired or 'both' if both a tipping gauge and a 
piezo gauge are paired."""
        prompt = '\n'.join([i for i in textwrap.wrap(_prompt, 80, break_long_words=False)])
        if EcowittHttpDriverConfEditor.prompting:
            print()
        paired_gauges = EcowittHttpDriverConfEditor.ask('paired_gauges',
                                                        prompt,
                                                        paired_str,
                                                        ['none', 'tipping', 'piezo', 'both']).lower()
#        if len(paired) > 0:
        if paired_gauges in ('tipping', 'piezo', 'both'):
            # we have a least one paired gauge

            _deltas_config_dict = config_dict['StdWXCalculate'].get('Delta', {})
            curr_rain_w_src = _deltas_config_dict['rain'].get('input') if 'rain' in _deltas_config_dict else None
            curr_rain_e_src = mapper.field_map.get(curr_rain_w_src) if curr_rain_w_src is not None else None
            # Determine the rain gauge type used to calculate 'rain', it will
            # be either 'tipping' or 'piezo'. If we can't determine the type
            # then use the string 'none'.
            curr_gauge_type = 'none'
            if curr_rain_w_src is not None:
                if curr_rain_e_src in EcowittHttpDriverConfEditor.t_src_fields:
                    curr_gauge_type = 'tipping'
                elif curr_rain_e_src in EcowittHttpDriverConfEditor.p_src_fields:
                    curr_gauge_type = 'piezo'
            # Now determine the preferred WeeWX field name for tipping and
            # piezo gauges. The preferred field is the available cumulative
            # rain field with the longest reset interval. If no field is
            # available (unlikely) then use None.
            # tipping, set our preferred tipping field name to None until we
            # find a field
            pref_t_field = None
            for _field in EcowittHttpDriverConfEditor.t_src_fields:
                # does that field appear in the field map, it is of no use if it
                # does not
                if _field in mapper.field_map.values():
                    pref_t_field = mapper.field_map.inverse[_field]
                    # no need to search further, break out of the loop
                    break
            # piezo, set our preferred tipping field name to None until we find
            # a field
            pref_p_field = None
            for _field in EcowittHttpDriverConfEditor.p_src_fields:
                # does that field appear in the field map, it is of no use if it
                # does not
                if _field in mapper.field_map.values():
                    pref_p_field = mapper.field_map.inverse[_field]
                    # no need to search further, break out of the loop
                    break
            rate_field = None
            add_back = None
            if paired_gauges == 'both':
                # both tipping and piezo are paired
                select_1 = "'tipping' to populate the WeeWX rain fields from a paired tipping gauge"
                select_2 = "'piezo' to populate the WeeWX rain fields from a paired piezo gauge"
                select_3 = "'none' to not populate the WeeWX rain fields"
                punc = ", "
                conj = " or "
                possible = ['tipping', 'piezo', 'none']
            elif paired_gauges == 'tipping':
                select_1 = "'tipping' to populate the WeeWX rain fields from a paired tipping gauge"
                select_2 = ""
                select_3 = "'none' to not populate the WeeWX rain fields"
                punc = ""
                conj = " or "
                possible = ['tipping', 'none']
            else:
                # there must be a paired piezo gauge
                select_1 = ""
                select_2 = "'piezo' to populate the WeeWX rain fields from a paired piezo gauge"
                select_3 = "'none' to not populate the WeeWX rain fields"
                punc = ""
                conj = " or "
                possible = ['piezo', 'none']
            selection_str = f"Set to {select_1}{punc}{select_2}{conj}{select_3}."
            _prompt = f"""By default, per-period rainfall values and rain rates will appear in
fields 't_rain'/'t_rainrate' and 'p_rain'/'p_rainrate for paired tipping and 
piezo rain gauges respectively. WeeWX can populate the default WeeWX rain observations 
('rain' and 'rainRate') from either a paired tipping or piezo rain gauge. {selection_str}"""
            prompt = '\n'.join([i for i in textwrap.wrap(_prompt, 80, break_long_words=False)])
            if EcowittHttpDriverConfEditor.prompting:
                print()
            user_gauge_type = EcowittHttpDriverConfEditor.ask('rain_source',
                                                              prompt,
                                                              curr_gauge_type,
                                                              possible).lower()
            # a preset answer may not be possible with the paired gauge(s)
            if user_gauge_type not in possible:
                print(f"Rain source '{user_gauge_type}' is not available "
                      f"with paired gauge(s) '{paired_gauges}', using 'none'.")
                user_gauge_type = 'none'
            # given the rain gauge type selected, obtain the source field to be
            # used to calculate WeeWX field 'rain'
            if user_gauge_type in ('tipping', 'piezo'):
                if user_gauge_type == 'tipping':
                    # a tipping rainfall gauge was chosen

                    # Get the default WeeWX source field, it is the WeeWX field
                    # currently used to derive the WeeWX 'rain' field if we are
                    # currently using a tipping gauge. If the WeeWX field does
                    # not exist or we are currently using a piezo gauge use the
                    # preferred tipping field from earlier.
                    if curr_gauge_type == 'tipping':
                        # the default WeeWX source field is the field we are
                        # currently using or if that does not exist the preferred
                        # tipping source field
                        default_source = curr_rain_w_src if curr_rain_w_src is not None else pref_t_field
                    else:
                        # 'piezo' had been previously selected so choose the
                        # preferred tipping source field from earlier
                        default_source = pref_t_field
                        # the user has chosen 'tipping' and the current gauge type
                        # is 'piezo' so we will need to add 'p_rain' back to
                        # StdWXCalculate
                        add_back = 'p_rain'
                    # construct a string listing the available WeeWX tipping
                    # cumulative rain fields, the available fields consist of those
                    # fields in our tipping source field list that exist in the
                    # field map
                    _fields = [mapper.field_map.inverse[f] for f in EcowittHttpDriverConfEditor.t_src_fields
                               if f in mapper.field_map.values()]
                    # set the WeeWX field that will be replaced by 'rain', we will
                    # need to remove this field from StdWXCalculate before we are
                    # done
                    rain_field = 't_rain'
                    rate_field = 'rain.0x0E.val'
                else:
                    # a piezo rainfall gauge was chosen

                    # Get the default WeeWX source field, it is the WeeWX field
                    # currently used to derive the WeeWX 'rain' field if we are
                    # currently using a piezo gauge. If one does not exist or we are
                    # currently using a tipping gauge use the preferred piezo field
                    # from earlier.
                    if curr_gauge_type == 'piezo':
                        default_source = curr_rain_w_src if curr_rain_w_src is not None else pref_p_field
                    else:
                        default_source = pref_p_field
                        add_back = 't_rain'
                    _fields = [mapper.field_map.inverse[f] for f in EcowittHttpDriverConfEditor.p_src_fields
                               if f in mapper.field_map.values()]
                    # set the WeeWX field that will be replaced by 'rain', we will
                    # need to remove this field from StdWXCalculate before we are
                    # done
                    rain_field = 'p_rain'
                    rate_field = 'piezoRain.0x0E.val'
                # Construct a string consisting of a comma separated list of
                # available cumulative WeeWX fields. This needs a bit of
                # finessing to get the grammar correct.
                _quoted = [f"'{f}'" for f in _fields]
                if len(_quoted) <= 2:
                    # one field, or two fields separated by 'or'
                    options = ' or '.join(_quoted)
                else:
                    # comma separate the first n-1 and 'or' the last one
                    options = f"{', '.join(_quoted[:-1])} or {_quoted[-1]}"
                _prompt = f"""Select the WeeWX observation to be used to derive 
WeeWX observation 'rain'. Possible observations are {options}."""
                prompt = '\n'.join([i for i in textwrap.wrap(_prompt, 80, break_long_words=False)])
                if EcowittHttpDriverConfEditor.prompting:
                    print()
                rain_source_field = EcowittHttpDriverConfEditor.ask('rain_source_field',
                                                                    prompt,
                                                                    default_source,
                                                                    _fields)
                # Construct the rain config dict, it will comprise the user rain
                # selections, the rain rate field map extension and any default
                # rain fields that may need to be added back to StdWXCalculate.
                # Once we have everything we can do one merge to the config dict.
                # first construct a config string that reflect the user selected
                # rain calculations
                _rain_config_str = f"""
                    [StdWXCalculate]
                        [[Calculations]]
                            rain = prefer_hardware
                        [[Delta]]
                            [[[rain]]]
                                input = {rain_source_field}"""
                _rain_config_dict = configobj.ConfigObj(io.StringIO(_rain_config_str))
                if rate_field is not None:
                    _rate_config_str = f"""
                        [EcowittGateway]
                            [[field_map_extensions]]
                                rainRate = {rate_field}"""
                    _rain_config_dict.merge(configobj.ConfigObj(io.StringIO(_rate_config_str)))
                # if we have had a change from 'tipping' to 'piezo' or vice-versa
                # we need to add back the old 't_rain' or 'p_rain' calculation,
                # but only if we have 'both' gauges
                if add_back is not None and paired_gauges == 'both':
                    _change_config_str = f"""
                        [StdWXCalculate]
                            [[Calculations]]
                                {add_back} = prefer_hardware
                            [[Delta]]
                                [[[{add_back}]]]
                                    input = {add_back}year"""
                    _rain_config_dict.merge(configobj.ConfigObj(io.StringIO(_change_config_str)))
                # We now have the complete rain config so merge into our overall
                # config dict
                config_dict.merge(_rain_config_dict)
                # finally we need to remove any [StdWXCalculate] entries for
                # default rain field ('t_rain' or 'p_rain') that has now been
                # replaced with WeeWX field 'rain'
                # first remove any [[Delta]] entry that exists
                if rain_field in config_dict['StdWXCalculate'].get('Delta', {}):
                    _ = config_dict['StdWXCalculate']['Delta'].pop(rain_field)
                # now check to see if there is a corresponding [[Calculations]]
                # entry, if there is remove it
                if rain_field in config_dict['StdWXCalculate'].get('Calculations', {}):
                    _ = config_dict['StdWXCalculate']['Calculations'].pop(rain_field)
            else:
                # no rainfall gauge was selected, all we need do is remove any
                # (now) unused [StdWXCalculate] config stanzas/options

                if 'rain' in config_dict['StdWXCalculate'].get('Delta', {}) and \
                        config_dict['StdWXCalculate']['Delta']['rain'].get('input') in mapper.field_map:
                    _ = config_dict['StdWXCalculate']['Delta'].pop('rain')
#                # do we have a [[Calculation]] 'rain' entry, if so remove it
#                if 'rain' in config_dict['StdWXCalculate'].get('Calculations', {}):
#                    # we have a 'rain' config entry, we can safely delete it
#                    _ = config_dict['StdWXCalculate']['Calculations'].pop('rain')
                # if we went from a gauge to no gauge we need to restore the
                # default WeeWX per-period rain and rain rate fields
                if curr_gauge_type in ('tipping', 'piezo'):
                    # we went from a gauge to no gauge, construct a suitable config
                    # string to restore the default WeeWX per-period rain field
                    _rain_config_str = f"""
                        [StdWXCalculate]
                            [[Calculations]]
                                {curr_gauge_type[0]}_rain = prefer_hardware
                            [[Delta]]
                                [[[{curr_gauge_type[0]}_rain]]]
                                    input = {curr_gauge_type[0]}_rainyear"""
                    _rain_config_dict = configobj.ConfigObj(io.StringIO(_rain_config_str))
                    config_dict.merge(_rain_config_dict)
                # remove any rainRate field map extensions, this will restore the
                # original/default rain rate fields
                if 'field_map_extensions' in config_dict.get(DRIVER_NAME, {}):
                    if 'rainRate' in config_dict[DRIVER_NAME]['field_map_extensions']:
                        _ = config_dict[DRIVER_NAME]['field_map_extensions'].pop('rainRate')
                    if len(config_dict[DRIVER_NAME]['field_map_extensions']) == 0:
                        _ = config_dict[DRIVER_NAME].pop('field_map_extensions')
            if 'Delta' in config_dict['StdWXCalculate'] and \
                    len(config_dict['StdWXCalculate']['Delta']) == 0:
                _ = config_dict['StdWXCalculate'].pop('Delta')
        else:
            # we have no paired gauges
            # our config is straightforward, we should leave [Calculations]
            # 'rain' as is, remove any 'rainRate' field map extensions, remove
            # any 't_rain' or ''p_rain' deltas, remove any 'rain' deltas if
            # they use an Ecowitt HTTP driver sourced field
            if 'field_map_extensions' in config_dict.get(DRIVER_NAME, {}):
                _ = config_dict[DRIVER_NAME]['field_map_extensions'].pop('rainRate', None)
                # finally, if we have ended up with no [EcowittGateway] [[field_map_extensions]]
                # entries we can safely delete the entire [[field_map_extensions]] stanza
                if len(config_dict[DRIVER_NAME]['field_map_extensions']) == 0:
                    _ = config_dict[DRIVER_NAME].pop('field_map_extensions')
            if 'Delta' in config_dict['StdWXCalculate'].keys():
                if 'rain' in config_dict['StdWXCalculate']['Delta'] and \
                        config_dict['StdWXCalculate']['Delta']['rain'].get('input') in mapper.field_map:
                    # we an Ecowitt HTTP driver rain delta, remove it
                    _ = config_dict['StdWXCalculate']['Delta'].pop('rain', None)
                # remove any other Ecowitt sourced deltas
                _ = config_dict['StdWXCalculate']['Delta'].pop('t_rain', None)
                _ = config_dict['StdWXCalculate']['Delta'].pop('p_rain', None)
                if len(config_dict['StdWXCalculate']['Delta']) == 0:
                    _ = config_dict['StdWXCalculate'].pop('Delta')

    @staticmethod
    def do_lightning(config_dict):
        """Configure lightning calculations.

        Create [StdWXCalculate] config entries to calculate WeeWX field
        lightning_strike_count from a suitable cumulative field.
        """

        print()
        print("""Setting lightning_strike_count calculation.""")
        lightning_config_str = """
        [StdWXCalculate]
            [[Calculations]]
                lightning_strike_count = prefer_hardware
            [[Delta]]
                [[[lightning_strike_count]]]
                    input = lightningcount"""
        lightning_config_dict = configobj.ConfigObj(io.StringIO(lightning_config_str))
        config_dict.merge(lightning_config_dict)

    @staticmethod
    def do_archive_record_generation(config_dict):
        """Configure archive record generation.

        Create [StdArchive] config entry to force software record generation.
        """

        print()
        print("""Setting record_generation to software.""")
        config_dict['StdArchive']['record_generation'] = 'software'

    @staticmethod
    def do_extractors(config_dict):
        """Configure extractors.

        Create [Accumulator] config entries to set extractor functions for
        driver unique WeeWX fields requiring non-default (average) extractors.
        """

        print()
        print("""Setting accumulator extractor functions.""")
        # remove the entries earlier versions added for fields we don't produce
        removed, changed = tidy_accumulator(config_dict)
        if removed:
            print(f"Removed {len(removed)} unused [Accumulator] entries left by earlier versions.")
        for field in changed:
            print(f"[Accumulator] {field}: extractor is now '{config_dict['Accumulator'][field]['extractor']}'.")
        # construct our default accumulator config dict
        accum_config_dict = configobj.ConfigObj(io.StringIO(EcowittHttpDriverConfEditor.accum_config_str))
        # merge the existing config dict into our default accumulator config
        # dict before merging tje updated accumulator config dict into our
        # config dict, doing the merge in this manner is wasteful but preserves
        # any existing accumulator config settings originally in config_dict
        accum_config_dict.merge(config_dict)
        config_dict.merge(accum_config_dict)


# ============================================================================
#                        class EcowittHttpDriverConfigurator
# ============================================================================

class EcowittHttpDriverConfigurator(weewx.drivers.AbstractConfigurator):
    """Configures the Ecowitt device.

    This class is used by wee_device when interrogating a supported Ecowitt
    device.

    The Ecowitt local HTTP API supports both reading and setting various device
    parameters; however, at this time the Ecowitt local HTTP API driver only
    supports the reading these parameters. The Ecowitt local HTTP API driver
    does not support setting these parameters, rather this should be done via
    the Ecowitt WSView Plus or the Ecowitt device web page.

    When used with wee_device this configurator allows station hardware
    parameters to be displayed. The Ecowitt local HTTP API driver may also be
    run directly to test the Ecowitt local HTTP API driver operation as well as
    display various driver configuration options (as distinct from device
    hardware parameters).
    """

    @property
    def description(self):
        """Description displayed as part of weectl device help information."""

        return "Read data and configuration from an Ecowitt device."

    @property
    def usage(self):
        """weectl device usage information."""
        return f"""{bcolors.BOLD}%prog --help
       %prog --live-data
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--units=us|metric|metricwx]
            [--ip-address=IP_ADDRESS]
            [--show-all-batt]
            [--debug=0|1|2|3]
       %prog --sensors
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--ip-address=IP_ADDRESS]
            [--show-all-batt]
            [--debug=0|1|2|3]
       %prog --sensor-map|--active-map
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--ip-address=IP_ADDRESS]
            [--no-sensor-map]
            [--debug=0|1|2|3]
       %prog --firmware|--mac-address|--system-params|
            --get-rain-data|--get-all-rain_data
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--ip-address=IP_ADDRESS]
            [--debug=0|1|2|3]
       %prog --get-calibration|--get-mulch-th-cal|
            --get-mulch-soil-cal|--get-pm25-cal|
            --get-co2-cal|--get-lds-cal|
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--ip-address=IP_ADDRESS]
            [--debug=0|1|2|3]
       %prog --get-services
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--ip-address=IP_ADDRESS]
            [--unmask] [--debug=0|1|2|3]
       %prog --dump-api
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--ip-address=IP_ADDRESS]
            [--output=FILE] [--unmask]
       %prog --sd-files
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--ip-address=IP_ADDRESS] [--debug=0|1|2|3]
       %prog --catchup-dry-run
            [CONFIG_FILE|--config=CONFIG_FILE]
            [--ip-address=IP_ADDRESS] [--since=DATE]
            [--catchup-source=either|device|net]
            [--no-sensor-map] [--debug=0|1|2|3]
       %prog --sources
            [CONFIG_FILE|--config=CONFIG_FILE]

   With several gateways ([[sources]]), add --source=NAME to choose the
   gateway for the other commands (default: the first one).{bcolors.ENDC}"""

    @property
    def epilog(self):
        """Epilog displayed as part of wee_device help information."""

        return ""
        # return "Mutating actions will request confirmation before proceeding.\n"

    def add_options(self, parser):
        """Define wee_device option parser options."""

        parser.add_option('--live-data', dest='live', action='store_true',
                          help='display device live sensor data')
        parser.add_option('--sensors', dest='sensors', action='store_true',
                          help='display device sensor information')
        parser.add_option('--firmware', dest='firmware',
                          action='store_true',
                          help='display device firmware information')
        parser.add_option('--mac-address', dest='mac', action='store_true',
                          help='display device station MAC address')
        parser.add_option('--system-params', dest='sys_params', action='store_true',
                          help='display device system parameters')
        parser.add_option('--sensor-map', dest='sensor_map', action='store_true',
                          help='display the multi-channel sensors and the channels '
                               'they are reported on ([[sensor_map]])')
        parser.add_option('--no-sensor-map', dest='no_sensor_map', action='store_true',
                          help='ignore [[sensor_map]] and show device channels')
        parser.add_option('--active-map', dest='active_map', action='store_true',
                          help='display the field map entries the device is filling now')
        parser.add_option('--get-rain-data', dest='rain_totals', action='store_true',
                          help='display device rain data (same as --get-all-rain-data)')
        parser.add_option('--get-all-rain-data', dest='rain_totals', action='store_true',
                          help='display device traditional, piezo and rain reset '
                               'time data')
        parser.add_option('--get-calibration', dest='calibration',
                          action='store_true',
                          help='display device calibration data')
        parser.add_option('--get-mulch-th-cal', dest='mulch_offset',
                          action='store_true',
                          help='display device multi-channel temperature and '
                               'humidity calibration data')
        parser.add_option('--get-mulch-soil-cal', dest='soil_calibration',
                          action='store_true',
                          help='display device soil moisture calibration data')
        parser.add_option('--get-mulch-t-cal', dest='temp_calibration',
                          action='store_true',
                          help='display device temperature (WN34) calibration data')
        parser.add_option('--get-pm25-cal', dest='pm25_offset',
                          action='store_true',
                          help='display device PM2.5 calibration data')
        parser.add_option('--get-co2-cal', dest='co2_offset',
                          action='store_true',
                          help='display device CO2 (WH45) calibration data')
        parser.add_option('--get-lds-cal', dest='lds_offset',
                          action='store_true',
                          help='display device LDS (WH54) calibration data')
        parser.add_option('--get-services', dest='services',
                          action='store_true',
                          help='display device weather services configuration data')
        parser.add_option('--ip-address', dest='ip_address',
                          help='device IP address to use')
        parser.add_option('--api', dest='api', choices=['auto', 'http', 'tcp'],
                          help="device API to use: 'http', 'tcp' (GW1000) or 'auto'")
        parser.add_option('--max-tries', dest='max_tries', type=int,
                          help='max number of attempts to contact the device')
        parser.add_option('--retry-wait', dest='retry_wait', type=int,
                          help='how long to wait between attempts to contact '
                               'the device')
        parser.add_option('--timeout', dest='timeout', type=int,
                          help='how long to wait for a device to respond to a '
                               'HTTP request')
        parser.add_option('--registered-only', dest='registered_only', action='store_true',
                          help='show only registered sensors')
        parser.add_option('--show-all-batt', dest='show_battery',
                          action='store_true',
                          help='show all available battery state data regardless of '
                               'sensor state')
        parser.add_option('--unmask', dest='unmask', action='store_true',
                          help='unmask sensitive settings')
        parser.add_option('--dump-api', dest='dump_api', action='store_true',
                          help='save every raw API response as JSON (passwords and keys masked)')
        parser.add_option('--output', dest='output', metavar='FILE',
                          help='file for --dump-api output')
        parser.add_option('--sd-files', dest='sd_files', action='store_true',
                          help='display the SD card and the history files on it')
        parser.add_option('--catchup-dry-run', dest='catchup_dry_run', action='store_true',
                          help='display the records a catchup would save, without saving them')
        parser.add_option('--since', dest='since', metavar='DATE',
                          help="start for --catchup-dry-run: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' "
                               "(default 24 hours ago)")
        parser.add_option('--catchup-source', dest='catchup_source', choices=['either', 'device', 'net'],
                          help='catchup source for --catchup-dry-run (default: [[catchup]] in weewx.conf)')
        parser.add_option('--sources', dest='sources', action='store_true',
                          help='display the gateways in [[sources]] and whether they answer')
        parser.add_option('--source', dest='source', metavar='NAME',
                          help='with [[sources]]: the gateway to use (default: the first one)')
        parser.add_option('--units', dest='units', metavar='UNITS', default='metric',
                          help='unit system to use when displaying live data')
        parser.add_option('--config', dest='config_path', metavar='CONFIG_FILE',
                          help="use configuration file CONFIG_FILE.")
        parser.add_option('--debug', dest='debug', type=int,
                          help='how much status to display, 0-3')
        parser.add_option('--yes', '-y', dest="noprompt", action="store_true",
                          help="answer yes to every prompt")

    def do_options(self, options, parser, config_dict, prompt):
        """Process weectl device option parser options."""

        stn_dict = driver_config(config_dict)

        # set weewx.debug as necessary ('weectl device' stores --debug as 'debug'; the command line
        # parser in main() as 'driver_debug', which DirectEcowittDevice also reads)
        if options.debug is not None:
            _debug = weeutil.weeutil.to_int(options.debug)
        else:
            _debug = weeutil.weeutil.to_int(config_dict.get('debug', 0))
        options.driver_debug = options.debug
        weewx.debug = _debug
        if _debug > 0:
            print(f"debug level is '{int(_debug):d}'")

        weeutil.logger.setup('weewx', config_dict)

        define_units()

        try:
            direct_http_gw = DirectEcowittDevice(options, parser, stn_dict,
                                                 archive_interval=std_archive_interval(config_dict),
                                                 counters=counter_fields(config_dict))
        except weewx.ViolatedPrecondition as e:
            print()
            print(e)
            return
        direct_http_gw.process_options()


# ============================================================================
#                               class Catchup
# ============================================================================

class Catchup:
    """Base class for an object that performs a catchup on WeeWX startup."""

    def __init__(self):
        """Initialize the object."""

        pass

    @property
    def name(self):
        """Return the name of the Catchup object."""

        return 'Not implemented'

    @property
    def source(self):
        """The source used by the Catchup object."""

        return 'Not implemented'

    def do_debug_logging(self):
        """Do any debug logging that is relevant to my source."""

        pass

# ============================================================================
#                          class EcowittNetCatchup
# ============================================================================

class EcowittNetCatchup(Catchup):
    """Class that implements catchup via Ecowitt.net API.

    Ecowitt devices do not include a hardware logger per se, though some
    devices do have the ability to record observational data to on board
    non-volatile memory (eg SD card). Those devices that do not have this
    capability do have the ability to independently upload observation data to
    Ecowitt.net at various integer (1-5) minute intervals provided the device
    has an active internet connection. Ecowitt provides an API to access this
    history data at Ecowitt.net. The Ecowitt.net history data provides a means
    for obtaining historical archive data, the data will likely differ slightly
    in values and times to the device generated archive data, but it may
    provide an effective 'virtual' logger capability to support catchup on
    startup.

    Ecowitt.net uses an age-based approach for aggregating data as follows:
    - station data from the past 90 days is stored using a five-minute interval
    - station data older than 90 days but from the past 365 days is stored
      using a 30-minute interval
    - station data older than 365 days but from the past 730 days is stored
      using a four-hour interval
    - station data older than 760 days but from the past 1460 days is stored
      using a 30-minute interval
    """

    # Ecowitt.net API endpoint
    endpoint = 'https://api.ecowitt.net/api/v3/device'
    # available Ecowitt.net API commands
    commands = ('real_time', 'history', 'list', 'info')
    # Ecowitt.net API result codes
    api_result_codes = {
        -1: 'System is busy',
        0: 'success result',
        40000: 'Illegal parameter',
        40010: 'Illegal Application_Key Parameter',
        40011: 'Illegal Api_Key Parameter',
        40012: 'Illegal MAC/IMEI Parameter',
        40013: 'Illegal start_date Parameter',
        40014: 'Illegal end_date Parameter',
        40015: 'Illegal cycle_type Parameter',
        40016: 'Illegal call_back Parameter',
        40017: 'Missing Application_Key Parameter',
        40018: 'Missing Api_Key Parameter',
        40019: 'Missing MAC Parameter',
        40020: 'Missing start_date Parameter',
        40021: 'Missing end_date Parameter',
        40022: 'Illegal Voucher type',
        43001: 'Needs other service support',
        44001: 'Media file or data packet is null',
        45001: 'Over the limit or other error',
        46001: 'No existing request',
        47001: 'Parse JSON/XML contents error',
        48001: 'Privilege Problem'
    }
    # default history call back
    default_call_back = ('outdoor', 'indoor', 'solar_and_uvi', 'rainfall',
                         'rainfall_piezo', 'wind', 'pressure', 'lightning',
                         'indoor_co2', 'pm25_ch1', 'pm25_ch2', 'pm25_ch3',
                         'pm25_ch4', 'co2_aqi_combo', 'pm25_aqi_combo',
                         'pm10_aqi_combo', 'pm1_aqi_combo', 't_rh_aqi_combo',
                         'temp_and_humidity_ch1', 'temp_and_humidity_ch2',
                         'temp_and_humidity_ch3', 'temp_and_humidity_ch4',
                         'temp_and_humidity_ch5', 'temp_and_humidity_ch6',
                         'temp_and_humidity_ch7', 'temp_and_humidity_ch8',
                         'soil_ch1', 'soil_ch2', 'soil_ch3', 'soil_ch4',
                         'soil_ch5', 'soil_ch6', 'soil_ch7', 'soil_ch8',
                         'temp_ch1', 'temp_ch2', 'temp_ch3', 'temp_ch4',
                         'temp_ch5', 'temp_ch6', 'temp_ch7', 'temp_ch8',
                         'leaf_ch1', 'leaf_ch2', 'leaf_ch3', 'leaf_ch4',
                         'leaf_ch5', 'leaf_ch6', 'leaf_ch7', 'leaf_ch8',
                         'battery')
    # Map from Ecowitt.net history fields to internal driver fields. Map is
    # keyed by Ecowitt.net history 'data set'. Individual key: value pairs are
    # Ecowitt.net field:driver field.
    # Ecowitt.net rain set fields: 'hourly' is the past hour's rain and
    # 'daily' the day's (ecowitt_http.py 0.1.0a28 had these two the wrong way
    # round)
    _net_rain = (('rain_rate', '{p}.0x0E.val'), ('event', '{p}.0x0D.val'),
                 ('hourly', '{p}.0x7D.val'), ('daily', '{p}.0x10.val'),
                 ('24_hours', '{p}.0x7C.val'), ('weekly', '{p}.0x11.val'),
                 ('monthly', '{p}.0x12.val'), ('yearly', '{p}.0x13.val'))
    # Map from Ecowitt.net history fields to internal driver fields. Map is
    # keyed by Ecowitt.net history 'data set'. Individual key: value pairs are
    # Ecowitt.net field: driver field. The driver fields are those the device
    # data uses (and the SD card history is mapped to), so the field map maps
    # them to WeeWX fields. (In ecowitt_http.py 0.1.0a28 they were names such
    # as 'outtemp' and 'temp1' that are not in the field map, so a catchup
    # from Ecowitt.net saved records with no data in them.) The values are in
    # the units requested: degree_C, hPa, meter_per_second, mm and W/m2.
    net_to_driver_map = {
        'outdoor': {'temperature': 'common_list.0x02.val',
                    'humidity': 'common_list.0x07.val'},
        'indoor': {'temperature': 'wh25.intemp',
                   'humidity': 'wh25.inhumi'},
        'solar_and_uvi': {'solar': 'common_list.0x15.val',
                          'uvi': 'common_list.0x17.val'},
        'rainfall': {k: v.format(p='rain') for k, v in _net_rain},
        'rainfall_piezo': {k: v.format(p='piezoRain') for k, v in _net_rain},
        'wind': {'wind_speed': 'common_list.0x0B.val',
                 'wind_gust': 'common_list.0x0C.val',
                 'wind_direction': 'common_list.0x0A.val',
                 '10_minute_average_wind_direction': 'common_list.0x6D.val'},
        'pressure': {'absolute': 'wh25.abs',
                     'relative': 'wh25.rel'},
        'lightning': {'distance': 'lightning.distance',
                      'count': 'lightning.count'},
        'co2_aqi_combo': {'co2': 'co2.CO2',
                          '24_hours_average': 'co2.CO2_24H'},
        'pm25_aqi_combo': {'pm25': 'co2.PM25'},
        'pm10_aqi_combo': {'pm10': 'co2.PM10'},
        'pm1_aqi_combo': {'pm1': 'co2.PM1'},
        'pm4_aqi_combo': {'pm4': 'co2.PM4'},
        't_rh_aqi_combo': {'temperature': 'co2.temp',
                           'humidity': 'co2.humidity'},
        **{f'pm25_ch{i}': {'pm25': f'ch_pm25.{i}.PM25'} for i in range(1, 5)},
        **{f'temp_and_humidity_ch{i}': {'temperature': f'ch_aisle.{i}.temp',
                                        'humidity': f'ch_aisle.{i}.humidity'} for i in range(1, 9)},
        **{f'soil_ch{i}': {'soilmoisture': f'ch_soil.{i}.humidity'} for i in range(1, 17)},
        **{f'temp_ch{i}': {'temperature': f'ch_temp.{i}.temp'} for i in range(1, 9)},
        **{f'leaf_ch{i}': {'leaf_wetness': f'ch_leaf.{i}.humidity'} for i in range(1, 9)},
        # battery voltages
        'battery': {**{f'soilmoisture_sensor_ch{i}': f'ch_soil.{i}.voltage' for i in range(1, 17)},
                    **{f'temperature_sensor_ch{i}': f'ch_temp.{i}.voltage' for i in range(1, 9)},
                    **{f'leaf_wetness_sensor_ch{i}': f'ch_leaf.{i}.voltage' for i in range(1, 9)}}
    }

    def __init__(self, **options):
        """Initialise an EcowittNetCatchup object."""

        super().__init__()
        try:
            self.api_key = options['api_key']
            if not self.api_key:
                raise KeyError('api_key')
        except KeyError:
            # pre-requisite api_key is missing, raise a CatchupObjectError with
            # a suitable error message
            raise CatchupObjectError("API key not specified")
        try:
            self.app_key = options['app_key']
            if not self.app_key:
                raise KeyError('app_key')
        except KeyError:
            # pre-requisite app_key is missing, raise a CatchupObjectError with
            # a suitable error message
            raise CatchupObjectError("Application key not specified")
        try:
            self.mac = options['mac']
        except KeyError:
            # could not obtain the device MAC address, raise a
            # CatchupObjectError with a suitable error message
            raise CatchupObjectError('Device MAC address not found')

    @property
    def name(self):
        """The name of the Catchup object."""

        return 'Ecowitt.net Catchup'

    @property
    def source(self):
        """The source used by the Catchup object."""

        return 'Ecowitt.net'

    def do_debug_logging(self):
        """Do any debug logging that is relevant to my source."""

        log.info('EcowittNetCatchup: API key: %s '
                 'Application key: %s' % (obfuscate(self.api_key),
                                          obfuscate(self.app_key)))


    def gen_history_records(self, start_ts=None, stop_ts=None, **kwargs):
        """Generate archive-like records from Ecowitt.net API history data.

        Generator function that uses the Ecowitt.net API to obtain history data
        from Ecowitt.net and generate archive-like records suitable for catchup
        by the WeeWX Ecowitt local HTTP API driver. Generated records are
        timestamped from start_ts (earliest ts) to stop_ts (most recent ts)
        inclusive. If stop_ts is not specified records are generated up to and
        including the current system time.

        start_ts: Earliest timestamp for which archive-like records are to be
                  emitted. If earlier than midnight 90 days ago midnight 90
                  days ago is used. If not specified or specified as None
                  midnight at the start of the day 90 days earlier than the
                  system time is used. Optional, integer or None.
        stop_ts:  Latest timestamp for which archive-like records are to be
                  emitted. If not specified or specified as None the current
                  system time is used instead. Optional, integer or None.

        Keyword Arguments. Supported keyword arguments include:
        call_back: Tuple containing the Ecowitt.net history data set names to
                   be sought in the API request. If not specified the default
                   call back data sets (EcowittNetCatchup.default_call_back)
                   are used. Optional, tuple of strings.
        """

        start_90_dt = datetime.datetime.now() - datetime.timedelta(days=90)
        start_90_dt = start_90_dt.replace(minute=0, hour=0, second=0, microsecond=0)
        start_90_ts = time.mktime(start_90_dt.timetuple())
        # Check if we have a start timestamp and if so is it is earlier than
        # the timestamp for midnight at the start of the day 90 days ago.If we
        # do set the start timestamp to the timestamp for midnight at the start
        # of the day 90 days ago
        if start_ts is None or start_ts < start_90_ts:
            start_ts = start_90_ts
        adj_stop_ts = int(time.time()) if stop_ts is None else stop_ts
        # construct the call_back, this specifies the data sets to be included
        # in the API history request
        # first check if we were given a call_back to use, if not use the
        # default
        _call_back = kwargs.get('call_back') if 'call_back' in kwargs else self.default_call_back
        call_back = ','.join(_call_back)
        for t_span in weeutil.weeutil.genDaySpans(start_ts, adj_stop_ts):
            data = {
                'application_key': self.app_key,
                'api_key': self.api_key,
                'mac': self.mac,
                # weeutil.weeutil.genDaySpans returns a 'midnight to midnight'
                # timespan. If we use the generated day span start and stop
                # timestamps when querying the Ecowitt.net API, the first
                # record in each query will actually belong to the previous
                # day. Plus we will have the last midnight record of a given
                # day appearing as the first record in the next days API query
                # causing a unique constraint warnings when saving archive
                # records to database. To avoid this add one second to the day
                # span start timestamp.
                'start_date': datetime.datetime.fromtimestamp(t_span.start + 1).strftime('%Y-%m-%d %H:%M:%S'),
                'end_date': datetime.datetime.fromtimestamp(t_span.stop).strftime('%Y-%m-%d %H:%M:%S'),
                'call_back': call_back,
                'cycle_type': '5min',
                'temp_unitid': 1,
                'pressure_unitid': 3,
                'wind_speed_unitid': 6,
                'rainfall_unitid': 12,
                'solar_irradiance_unitid': 16
            }
            # Obtain a day of history data via the Ecowitt.net API. We will
            # either receive data or encounter an exception if the request
            # failed or the data invalid. So wrap in a try .. except to catch
            # and handle any exceptions.
            try:
                day_data = self.request(command_str='history', data=data, headers=None)
            except (socket.timeout, urllib.error.URLError, InvalidApiResponseError) as e:
                # A technical comms error was encountered or the response
                # received contains invalid data, either way we cannot
                # continue. Any logging has already occurred so just raise the
                # exception.
                raise
            else:
                parsed_day_data = self.parse_history(day_data.get('data', dict()))
                # traverse the timestamped data for the day and construct and
                # yield records, we need the timestamps in ascending order
                for ts in sorted(parsed_day_data.keys()):
                    # ensure we are only yielding timestamps within our span of
                    # interest
                    if start_ts <= ts <= adj_stop_ts:
                        # Construct an outline record, the timestamp is the
                        # current timestamp in our parsed data. We don't (yet)
                        # need a usUnits field nor an interval field
                        rec = {'datetime': ts,
                               'interval': 5}
                        rec.update(parsed_day_data[ts])
                        # yield the archive-like record
                        yield rec

    def request(self, command_str, data=None, headers=None, max_tries=3):
        """Send a HTTP request to the Ecowitt.net API and return the response.

        Create a HTTP request with optional data and headers. Send the HTTP
        request to the device as a GET request and obtain the response. If an
        exception occurs the exception is logged and the request attempted
        again up to a total of max_tries attempts. The deserialized JSON
        response is returned. If after max_tries attempts a valid response has
        been received but cannot be deserialized, or is otherwise invalid, a
        InvalidApiResponseError exception is raised.

        command_str: a string containing the command to be sent,
                     eg: 'get_livedata_info'
        data: a dict containing key:value pairs representing the data to be
              sent
        headers: a dict containing headers to be included in the HTTP request
        max_tries: the maximum number of attempts to send the request

        Returns a deserialized JSON object or raises an exception
        """

        # ensure we have dicts for data and headers
        data_dict = {} if data is None else data
        headers_dict = {} if headers is None else headers
        if command_str in EcowittNetCatchup.commands:
            # first convert any data to a percent-encoded ASCII text string
            data_enc = urllib.parse.urlencode(data_dict)
            endpoint_path = '/'.join([EcowittNetCatchup.endpoint, command_str])
            # Finally add the encoded data. We need to add the data in this manner
            # rather than using the Request object's 'data' parameter so that the
            # request is sent as a GET request rather than a POST request.
            url = '?'.join([endpoint_path, data_enc])
            req = urllib.request.Request(url=url, headers=headers_dict)
            for attempt in range(max_tries):
                # wrap the request in a try..except in case we encounter and
                # error
                try:
                    with urllib.request.urlopen(req) as w:
                        # get charset used so we can decode the stream correctly
                        char_set = w.headers.get_content_charset()
                        # Now get the response and decode it using the headers
                        # character set. Be prepared for charset==None.
                        if char_set is not None:
                            response = w.read().decode(char_set)
                        else:
                            response = w.read().decode()
                except (socket.timeout, urllib.error.URLError) as e:
                    # Log the error. If this was the last attempt raise the
                    # exception, otherwise continue with the next attempt.
                    log.error('Failed to obtain data from Ecowitt.net on attempt %d' % (attempt + 1))
                    log.error('   **** %s' % e)
                    if attempt < max_tries - 1:
                        continue
                    else:
                        raise
                try:
                    return self.check_response(response)
                except (InvalidApiResponseError, ApiResponseError) as e:
                    # The response is invalid or an explicit response error was
                    # returned or some other unexpected error occurred. Log the
                    # error. If this was the last attempt raise an
                    # InvalidApiResponseError exception, otherwise continue with
                    # the next attempt.
                    log.error('Invalid Ecowitt.net API response on attempt %d' % (attempt + 1))
                    log.error('   **** %s' % e)
                    if attempt < max_tries - 1:
                        continue
                    else:
                        raise InvalidApiResponseError(e) from e

    def check_response(self, response):
        """Check the validity of an API response.

        Checks the validity of an API response. Three checks are performed:

        1.  the response has a length > 0
        2.  the response is valid JSON
        3.  the response contains a field 'code' with the value 0

        If any check fails an appropriate exception is raised, if all checks
        pass the decoded JSON response is returned.

        response: Raw, character set decoded response from a HTTP request the
        Ecowitt.net API.

        Returns a deserialized JSON object or raises an exception.
        """

        if response is not None and len(response) > 0:
            try:
                json_resp = json.loads(response)
            except json.JSONDecodeError as e:
                # the response could not be decoded as JSON, raise an
                # InvalidApiResponseError exception
                raise InvalidApiResponseError(e)
            if json_resp.get('code') == 0:
                # we have valid JSON and a (sic) 'success result', return the
                # JSON format response
                return json_resp
            else:
                # we have a non-zero 'code', raise an ApiResponseError
                # exception with a suitable error message
                code = json_resp.get('code', 'no code')
                raise ApiResponseError(f"Received API response error code "
                                       f"'{code}': {self.api_result_codes.get(code)}")
        else:
            # response is None or zero length, raise an InvalidApiResponseError
            # exception
            raise InvalidApiResponseError(f"Invalid API response received")

    def parse_history(self, history_data):
        """Parse Ecowitt.net history data"""

        #        history_data = json.loads(self.d).get('data', dict())
        # initialise a dict to hold the parsed data
        result = dict()
        for set_name, set_data in history_data.items():
            parsed_set_data = self.parse_data_set(set_name, set_data)
            # the parsed set data is a dict of data keyed by timestamp, iterate
            # over each timestamp: data pair and add the data to the
            # corresponding timestamp in the parsed data accumulated so far
            for ts, ts_data in parsed_set_data.items():
                if ts not in result:
                    result[ts] = dict()
                result[ts].update(ts_data)
        return result

    def parse_data_set(self, set_name, data):
        """Parse a data set containing one or more observation types."""

        result = dict()
        for obs, obs_data in data.items():
            field_name = self.get_field_name(set_name, obs)
            # if the field name is not None then add the current obs type data
            # to our results, if the field name is None then skip this obs type
            if field_name is not None:
                parsed_data = self.parse_float(obs_data)
                for ts, value in parsed_data.items():
                    if ts not in result:
                        result[ts] = dict()
                    result[ts][field_name] = value
        return result

    @staticmethod
    def parse_float(data):
        """Parse an observation type consisting of floating point data.

        Each data set (eg 'outdoor', 'indoor', 'pressure' etc) equates to a
        JSON object (eg "outdoor", "indoor", "pressure" etc) and consists of
        one or more observation types (eg 'temperature', 'feels_like' etc)
        which also equate to JSON objects (eg "temperature", "feels_like" etc)
        containing timestamped observation values. Currently, this timestamped
        data exists in the JSON object "list" as a sequence of timestamp:value
        pairs where timestamp is a unix epoch timestamp enclosed in quotes and
        value is a numeric observation value again enclosed in quotes.

        The JSON "list" object is processed with each timestamp converted to an
        integer and the corresponding value converted to a floating point
        number. A dict of converted numeric timestamp:value pair is returned.
        The return dict may be in ascending timestamp order, but this is not
        guaranteed. If a timestamp string cannot be converted to an integer the
        timestamp:value pair is ignored. If a value cannot be converted to a
        floating point number the value is set to None.

        Example JSON data extract showing the 'outdoor' data set including the
        'temperature' and 'feels_like' observation types:

        ....
        "data": {
            "outdoor": {
                "temperature": {
                    "unit": "℃",
                    "list": {
                        "1722764400": "16.8",
                        "1722764700": "16.8",
                        "1722765000": "16.7",
                        "1722765300": "16.7"
                    }
                },
                "feels_like": {
                    "unit": "℃",
                    "list": {
                        "1722764400": "16.8",
                        "1722764700": "16.8",
                        "1722765000": "16.7",
                        "1722765300": "16.7"
                    }
                },
                ....
            },
            ....
        },
        ....
        """

        result = dict()
        for ts_string, value_str in data['list'].items():
            # the ts is a string, try to convert to an int, if we cannot
            # skip the ts
            try:
                ts = int(ts_string)
            except ValueError:
                continue
            try:
                result[ts] = float(value_str)
            except ValueError:
                result[ts] = None
        return result

    def get_field_name(self, set_name, obs):
        """Determine the destination field name to be used.

        The field names used in an Ecowitt.net API history response are
        different to those field names used internally within the Ecowitt local
        HTTP API driver. To allow Ecowitt.net API history obs data to be used
        by the Ecowitt local HTTP API driver each Ecowitt.net API obs data
        field must be mapped to an Ecowitt local HTTP API driver internal
        field.

        Some Ecowitt.net API history fields are not used by the Ecowitt local
        HTTP API driver and can be ignored. In these cases the value None is
        returned.

        Given an Ecowitt.net history set name and obs type a lookup table can
        be used to determine the applicable Ecowitt local HTTP API driver
        internal field.
        """

        try:
            return self.net_to_driver_map[set_name][obs]
        except KeyError:
            return None


# ============================================================================
#                         class EcowittDeviceCatchup
# ============================================================================

def device_host(address):
    """The host part of a device address: '192.168.1.100' from '192.168.1.100:8080'.

    An IPv6 address keeps its brackets ('[fe80::1]:80' gives '[fe80::1]'); a
    bare IPv6 address is returned in brackets, ready for a URL."""

    address = str(address).strip()
    if address.startswith('['):
        return address[:address.index(']') + 1] if ']' in address else address
    if address.count(':') > 1:
        return f'[{address}]'
    return address.split(':')[0]


def sd_file_url(address, file_name, port=DEFAULT_SD_FILE_PORT):
    """The URL of a history file on the SD card: port 81 of the device's host."""

    return f'http://{device_host(address)}:{port}/{file_name}'


def is_history_file(entry):
    """Whether a get_sdmmc_info file_list entry is a history file (named YYYYMM...)."""

    return (str(entry.get('type')) in ('file', '1')
            and re.match(r'\d{4}(0[1-9]|1[0-2])', str(entry.get('name', ''))) is not None)


class EcowittDeviceCatchup:
    """Class that implements catchup via local device history files.

    Some Ecowitt devices store history data files locally in non-volatile
    memory such as SD card. These history files record selected sensor data at
    regular user specified intervals. This history data can be used as a source
    for historical archive records used by WeeWX during the WeeWX catchup that
    is attempted during WeeWX startup.

    Class EcowittDeviceCatchup supports such a catchup from selected Ecowitt
    devices though:

    -   identifying and selecting history data files for use during catchup
    -   processing, parsing and mapping of history data records to produce
        archive like historical records suitable for use by the driver
        genArchiveRecords method
    """

    # history data file fields by WeeWX unit group that may require unit
    # conversion
    unit_groups_by_field = {
        'group_temperature': ('wh25.intemp', 'common_list.0x02.val', 'common_list.0x03.val', 'feelslike',
                              'ch_aisle.1.temp', 'ch_aisle.2.temp', 'ch_aisle.3.temp', 'ch_aisle.4.temp',
                              'ch_aisle.5.temp', 'ch_aisle.6.temp', 'ch_aisle.7.temp', 'ch_aisle.8.temp',
                              'dewpoint1', 'dewpoint2', 'dewpoint3', 'dewpoint4',
                              'dewpoint5', 'dewpoint6', 'dewpoint7', 'dewpoint8',
                              'heatindex1', 'heatindex2', 'heatindex3', 'heatindex4',
                              'heatindex5', 'heatindex6', 'heatindex7', 'heatindex8',
                              'ch_temp.1.temp', 'ch_temp.2.temp', 'ch_temp.3.temp', 'ch_temp.4.temp',
                              'ch_temp.5.temp', 'ch_temp.6.temp', 'ch_temp.7.temp', 'ch_temp.8.temp',
                              'co2.temperature'),
        'group_speed' : ('common_list.0x0B.val', 'common_list.0x0C.val'),
        'group_pressure': ('wh25.abs', 'wh25.rel', 'common_list.5.val'),
        'group_rain': ('rain.0x0D.val', 'rain.0x10.val', 'rain.0x11.val', 'rain.0x12.val',
                       'rain.0x13.val', 'rain.0x7C.val', 'rain.0x7D.val', 'piezoRain.0x0D.val',
                       'piezoRain.0x10.val', 'piezoRain.0x11.val', 'piezoRain.0x12.val',
                       'piezoRain.0x13.val', 'piezoRain.0x7C.val', 'piezoRain.0x7D.val'),
        'group_rainrate': ('rain.0x0E.val', 'piezoRain.0x0E.val'),
        'group_illuminance': ('common_list.0x15.val', ),
        'group_distance': ('lightning.distance', ),
        'group_depth': ('ch_lds.1.air', 'ch_lds.2.air', 'ch_lds.3.air', 'ch_lds.4.air',
                        'ch_lds.1.depth', 'ch_lds.2.depth', 'ch_lds.3.depth', 'ch_lds.4.depth')
    }

    def __init__(self, **options):
        """Initialise an EcowittDeviceCatchup object."""

        # save the device IP address, wrap in a try..except so we can catch an
        # exception if an IP address is not provided
        try:
            self.ip_address = options['ip_address']
        except KeyError:
            # A device IP address was not provided, that means we cannot go
            # on. Raise a CatchupObjectError exception with an error message.
            raise CatchupObjectError('Device IP address not found.')
        self.url_timeout = weeutil.weeutil.to_int(options.get('url_timeout',
                                                              DEFAULT_URL_TIMEOUT))
        self.device = EcowittDevice(ip_address=self.ip_address,
                                    url_timeout=self.url_timeout)
        # save the device unit system to be used, if not specified use the default
        self.unit_system = weeutil.weeutil.to_int(options.get('unit_system',
                                                              DEFAULT_UNIT_SYSTEM))
        self.catchup_grace = weeutil.weeutil.to_int(options.get('catchup_grace',
                                                                DEFAULT_CATCHUP_GRACE))
        # save the max number of retries when attempting to access a device
        # file
        # TODO. Look at making parameter, property and default names match better
        self.max_catchup_retries = weeutil.weeutil.to_int(options.get('max_retries',
                                                                      DEFAULT_CATCHUP_RETRIES))
        # the history files are served on their own port of the device's host
        # (81), whatever port ip_address gives for the API
        self.file_port = weeutil.weeutil.to_int(options.get('file_port', DEFAULT_SD_FILE_PORT))
        # Attempt to get the SD card info, this serves as a check whether the
        # devices support catchup via locally stored history data files. Wrap
        # in a try..except so an exception can be raised should the device not
        # support locally stored history data files.
        try:
            _ = self.device.get_sdmmc_info_data()
        except (DeviceIOError, ParseError):
            raise CatchupObjectError(f"{self.device.model} at '{self.ip_address}' does "
                                     f"not appear to support HTTP API based catchup")
        self.driver_debug = options.get('driver_debug', DebugOptions())
        # obtain a suitable Mapper object to map the SD card history data, the
        # default map is what we want so pass an empty config
        self.mapper = SdMapper(driver_debug=self.driver_debug,
                               mapper_config={})

    def gen_history_records(self, start_ts=None):
        """Generator to yield archive like records from device history files.

        Obtain details of available device history data files then selects the
        files to be processed based on start_ts. Iterate over the history files
        to be processed in ascending date order, downloading each file,
        processing, mapping and unit converting any applicable records and
        finally yielding records in ascending date-time order.

        History file record fields are mapped to 'dotted' field names
        compatible with the local device HTTP API and unit converted as per the
        unit system in use.

        Generated records are archive like dicts that are timestamped and unit
        converted.
        """

        # first get details of available history files from the device, be
        # prepared to catch exceptions if a response could not be obtained from
        # the device or the response could not be parsed
        try:
            sdmmc_info = self.device.get_sdmmc_info_data()
        except (DeviceIOError, ParseError) as e:
            raise CatchupObjectError(f"{self.device.model} at '{self.ip_address}' does "
                                     f"not appear to support HTTP API based catchup")
        files = self.get_file_list(sdmmc_info, start_ts)
        try:
            interval = int(sdmmc_info['info']['Interval'])
        except (KeyError, ValueError, TypeError) as e:
            # we could not find the 'Interval' key value or could not coalesce
            # an integer from the key value, either way we cannot continue,
            # raise a CatchupObjectError
            raise CatchupObjectError(f"Unable to determine history file record interval: {e}")
        for ym in sorted(files.keys()):
            month_lists_of_recs = []
            for file in files[ym]:
                if weewx.debug >= 2 or self.driver_debug.catchup:
                    log.info("Processing history file '%s' from %s at %s",
                             file, self.device.model, self.ip_address)
                # Obtain the records from all files belonging to this
                # year-month. The end result is a list of dicts keyed by the
                # record date-time.

                # first attempt to obtain the history file from the device, if
                # it cannot be obtained log it and continue
                try:
                    response = self.get_file(file)
                except DeviceIOError as e:
                    log.error("Unable to download history file '%s' from %s at %s",
                              file, self.device.model, self.ip_address)
                    continue
                try:
                    # decode the
                    lines = [l.decode('utf-8') for l in response.readlines()]
                except UnicodeDecodeError as e:
                    log.error("Unable to decode file '%s' from %s at %s",
                              file, self.device.model, self.ip_address)
                    continue
                except OSError as e:
                    # the connection was lost or timed out while the file
                    # was being read (ecowitt_http.py 0.1.0a28 let this end
                    # the catchup)
                    log.error("Unable to read history file '%s' from %s at %s: %s",
                              file, self.device.model, self.ip_address, e)
                    continue
                # Whilst the file contents have been downloaded and decoded
                # there might still be various control or special characters
                # (eg null characters, blank lines) that will cause the
                # DictReader to fail. Strip these problem characters/sequences
                # from the raw data.
                clean_lines = self.clean_data(lines)
                try:
                    csv_reader = csv.DictReader(clean_lines)
                except csv.Error as e:
                    log.error("Unable to parse CSV file '%s' from %s at %s",
                              file, self.device.model, self.ip_address)
                    continue
                month_lists_of_recs.append(self.process_raw_csv_data(csv_reader,
                                                                     start_ts,
                                                                     interval))
            # We now have all data for a given month. However, the data for a
            # given timestamp is potentially split across (possibly multiple)
            # lists of dicts. We need to coalesce the month data into a format
            # where all data for a given timestamp is in a single dict
            # ie record. We can do this efficiently using a defaultdict with
            # the result being a dict keyed by timestamp where each
            # corresponding value contains the amalgamated data for that
            # timestamp.

            # create a defaultdict using dict as the factory
            d = collections.defaultdict(dict)
            for file_list in month_lists_of_recs:
                for file_rec in file_list:
                    # 'update' the relevant defaultdict key (ie timestamp)
                    # value with corresponding data from the current file
                    d[file_rec['datetime']].update(file_rec)
            # the combined data will likely not be in date-time order, so sort
            # the data by ascending timestamp
            sorted_recs = sorted(list(d.values()), key=operator.itemgetter('datetime'))
            for rec in sorted_recs:
                yield rec

    def clean_data(self, raw_data):
        """Clean raw data to remove detritus that will upset a CSV dict reader."""

        # just in case the data has been sourced from the web we will remove
        # any HTML tags and blank lines that may exist
        clean_data = []
        for row in raw_data:
            # check for and remove any null bytes
            clean_row = row
            if "\x00" in row:
                clean_row = clean_row.replace("\x00", "")
                if weewx.debug >= 2 or self.driver_debug.catchup:
                    log.info('One or more null bytes found in and removed')
            if clean_row != "\n":
                # save anything that is not a blank line
                clean_data.append(clean_row)
        return clean_data

    def process_raw_csv_data(self, data_reader, start_ts, interval):
        """Process raw CSV data into archive like records.

        Process a csv reader object and return a list of archive like records.
        Each record will contain the key 'datetime' which holds the timestamp
        of the record and key 'interval' which holds the history file interval
        value. Other keys are present depending on available data.

        Returns a list of dicts (records).
        """

        result = []
        units = {}
        for row in data_reader:
            try:
                ts = datetime.datetime.strptime(row['Time'], '%Y-%m-%d %H:%M').timestamp()
            except ValueError:
                # the 'Time' field could not be parsed, so ignore it and
                # continue with the next record
                continue
            if start_ts is None or ts > start_ts + self.catchup_grace:
                if len(units) == 0:
                    units = self.get_units(row.keys())
                # map the record
                mapped_rec = self.mapper.map_data(rec=row)
                # Whilst the history record has now been mapped to a standard
                # set of field names, the units being used will be whatever
                # units the user has specified through the WS View+ app. We
                # need to convert as necessary so the record uses one of the
                # three WeeWX unit systems.
                converted_rec = self.convert_history_rec(mapped_rec, units)
                converted_rec['datetime'] = ts
                converted_rec['interval'] = interval
                result.append(converted_rec)
        # return our list of records, our list of recs may or may not be in
        # timestamp order, but it does not matter as the records are sorted
        # later once the month records are coalesced
        return result

    @staticmethod
    def get_units(keys):
        """Extract the history file units from the history file field names.

        Examines the field names for a history file (keys for a dictreader row)
        and determine the WeeWX unit name to be used for each WeeWX unit group.

        Returns a dict of WeeWX unit names keyed by WeeWX unit group.
        """

        # Create a dict for our result, pre-populate with those unit
        # groups/names for groups that only have a single unit (ie groups for
        # which Ecowitt only allows one possible unit, eg group_percent -
        # percent). Include groups/units not used by Ecowitt where WeeWX only
        # uses one unit for the group.
        units = {
            "group_altitude"    : "meter",
            "group_amp"         : "amp",
            "group_angle"       : "degree_angle",
            "group_boolean"     : "boolean",
            "group_concentration": "microgram_per_meter_cubed",
            "group_count"       : "count",
            "group_data"        : "byte",
            "group_db"          : "dB",
            # TODO. Should this group/unit be included?
            "group_degree_day"  : "degree_C_day",
            "group_deltatime"   : "second",
            "group_direction"   : "degree_compass",
            "group_elapsed"     : "second",
            "group_energy"      : "watt_hour",
            "group_energy2"     : "watt_second",
            "group_fraction"    : "ppm",
            "group_frequency"   : "hertz",
            "group_interval"    : "minute",
            "group_length"      : "cm",
            "group_moisture"    : "centibar",
            "group_percent"     : "percent",
            "group_power"       : "watt",
            "group_pressurerate": "mbar_per_hour",
            "group_time"        : "unix_epoch",
            "group_uv"          : "uv_index",
            "group_volt"        : "volt",
            "group_volume"      : "liter"
        }
        required_groups = {'group_temperature', 'group_speed', 'group_speed2',
                           'group_pressure', 'group_rain', 'group_rainrate',
                           'group_illuminance', 'group_distance', 'group_depth'
                           }
        found_groups = set()
        for _key in keys:
            # do all our unit label comparisons in lower case to guard against
            # firmware changes that might (inadvertently or deliberately)
            # change the case of unit labels in SD card files
            key = _key.lower()
            # For temperature we check for keys containing 'Temperature'. Is
            # the key a 'temperature' and have we already set group_temperature
            # units.
            if 'temperature' in key and 'group_temperature' not in units:
                if 'c)' in key or chr(8451) in key:
                    # we have temperature in C (chr(8451) is a legacy unit
                    # symbol used by Ecowitt)
                    units['group_temperature'] = 'degree_C'
                    found_groups.add('group_temperature')
                elif 'f)' in key or chr(8457) in key:
                    # we have temperature in F (chr(8457) is a legacy unit
                    # symbol used by Ecowitt)
                    units['group_temperature'] = 'degree_F'
                    found_groups.add('group_temperature')
                continue
            # For pressure we check for keys containing 'Pressure'. Is the key
            # a 'pressure' and have we already set group_pressure units.
            if 'pressure' in key and 'group_pressure' not in units:
                if '(hpa)' in key:
                    units['group_pressure'] = 'hPa'
                    found_groups.add('group_pressure')
                elif '(inhg)' in key:
                    units['group_pressure'] = 'inHg'
                    found_groups.add('group_pressure')
                elif '(mmhg)' in key:
                    units['group_pressure'] = 'mmHg'
                    found_groups.add('group_pressure')
                continue
            # For speed we check for keys containing 'Gust'. Is the key a
            # 'speed' and have we already set group_speed units.
            if 'gust' in key and 'group_speed' not in units:
                if '(km/h)' in key:
                    units['group_speed'] = 'km_per_hour'
                    units['group_speed2'] = units['group_speed']
                    found_groups.add('group_speed')
                    found_groups.add('group_speed2')
                elif '(mph)' in key:
                    units['group_speed'] = 'mile_per_hour'
                    units['group_speed2'] = units['group_speed']
                    found_groups.add('group_speed')
                    found_groups.add('group_speed2')
                elif '(m/s)' in key:
                    units['group_speed'] = 'meter_per_second'
                    units['group_speed2'] = units['group_speed']
                    found_groups.add('group_speed')
                    found_groups.add('group_speed2')
                elif '(knots)' in key:
                    units['group_speed'] = 'knot'
                    units['group_speed2'] = units['group_speed']
                    found_groups.add('group_speed')
                    found_groups.add('group_speed2')
                elif '(nhg)' in key:
                    units['group_speed'] = 'knot'
                    units['group_speed2'] = units['group_speed']
                    found_groups.add('group_speed')
                    found_groups.add('group_speed2')
                continue
            # For rain we check for keys containing 'Rain'. Is the key a 'rain'
            # and have we already set group_rain units.
            if 'rain' in key and 'group_rain' not in units:
                if '(mm)' in key:
                    # we have rain in mm
                    units['group_rain'] = 'mm'
                    units['group_rainrate'] = 'mm_per_hour'
                    found_groups.add('group_rain')
                    found_groups.add('group_rainrate')
                elif '(in)' in key:
                    units['group_rain'] = 'inch'
                    units['group_rainrate'] = 'inch_per_hour'
                    found_groups.add('group_rain')
                    found_groups.add('group_rainrate')
                continue
            # TODO. Operation of this section needs to be confirmed
            # Illuminance is a special case. Ecowitt sensors/suites do not
            # contain pyranometers, rather they contain a light sensor that
            # measures illuminance and then optionally uses this value to
            # approximate solar irradiance (or WeeWX field 'radiation') and
            # group_illuminance. Consequently, for 'illuminance' we need to
            # look for a Solar Radiation field. Is the key a 'Solar Radiation'
            # and have we already set group_illuminance units.
            if 'solar rad' in key and 'group_illuminance' not in units:
                if '(w/m2)' in key:
                    units['group_illuminance'] = 'watt_per_meter_squared'
                    found_groups.add('group_illuminance')
                elif '(klux)' in key:
                    units['group_illuminance'] = 'kilolux'
                    found_groups.add('group_illuminance')
                elif '(kfc)' in key:
                    units['group_illuminance'] = 'kfc'
                    found_groups.add('group_illuminance')
                continue
            # For distance we check for keys containing 'distance'. Is the key
            # a 'distance' and have we already set group_distance units.
            if 'distance' in key and 'group_distance' not in units:
                if '(km)' in key:
                    units['group_distance'] = 'km'
                    found_groups.add('group_distance')
                elif '(mi)' in key:
                    units['group_distance'] = 'mile'
                    found_groups.add('group_distance')
                elif '(nmi)' in key:
                    units['group_distance'] = 'nautical_mile'
                    found_groups.add('group_distance')
                continue
            # For depth we check for keys containing 'LDS'. Is the key a
            # 'depth' and have we already set group_depth units.
            if 'lds' in key and 'group_depth' not in units:
                if '(mm)' in key:
                    # we have depth in mm
                    units['group_depth'] = 'mm2'
                    found_groups.add('group_depth')
                elif 'cm)' in key:
                    # we have depth in cm
                    units['group_depth'] = 'cm2'
                    found_groups.add('group_depth')
                elif 'in)' in key:
                    units['group_depth'] = 'inch2'
                    found_groups.add('group_depth')
                elif '(ft)' in key:
                    units['group_depth'] = 'foot2'
                    found_groups.add('group_depth')
                continue
            if required_groups == found_groups:
                break
        return units

    def convert_history_rec(self, rec, units):
        """Convert a history record to a given unit system."""

        # make a copy of the source record as we may be altering it
        converted = dict(rec)
        for unit_group in self.unit_groups_by_field.keys():
            for field in self.unit_groups_by_field[unit_group]:
                if field in rec:
                    try:
                        # Ecowitt have decided that when pressure units are set
                        # to 'hPa' VPD (common_list.5.val) will be returned in
                        # kPa. This will occur when the 'group_pressure' unit
                        # setting is hPa. Unfortunately, WeeWX can only use one
                        # pressure unit at a time so check if we have VPD and
                        # 'group_pressure' is set to hPa (this means VPD is in
                        # kPa) and if so do a pre-convert of the VPD kPa value
                        # to hPa.
                        if field == 'common_list.5.val' and units.get(unit_group) == 'hPa':
                            _val = rec[field] * 10
                        else:
                            # no need to do any pre-conversion
                            _val = rec[field]
                        _vt = weewx.units.ValueTuple(_val,
                                                     units.get(unit_group),
                                                     unit_group)
                    except (KeyError, TypeError, ValueError) as e:
                        # an error occurred creating a ValueTuple, log it and
                        # skip this field
                        if weewx.debug >= 3 or self.driver_debug.catchup:
                            log.info("Could not create ValueTuple for field '%s' "
                                     "with unit group '%s': %s",
                                     field, unit_group, e)
                        continue
                    try:
                        converted[field] = weewx.units.convertStd(_vt,
                                                                  self.unit_system).value
                    except Exception as e:
                        # an error occurred converting this field, log it and
                        # skip this field
                        if weewx.debug >= 3 or self.driver_debug.catchup:
                            log.info("Could not convert field '%s' "
                                     "with unit group '%s' to '%s': %s",
                                     field,
                                     unit_group,
                                     weewx.units.unit_nicknames[self.unit_system],
                                     e)
                        continue
        return converted

    def get_file(self, file_name):
        """Attempt to obtain a file from a device with retries.

        Used to obtain history data files from devices that support local
        storage of history data files. Returns the file contents or raises a
        DeviceIOError if the file cannot be opened.
        """

        # (ecowitt_http.py 0.1.0a28 appended ':81' to ip_address, so an
        # address with a port gave 'host:PORT:81', which fails with 'Name or
        # service not known')
        url = sd_file_url(self.ip_address, file_name, self.file_port)
        for attempt in range(max(1, self.max_catchup_retries)):
            try:
                response = urllib.request.urlopen(url, timeout=self.url_timeout)
            except urllib.error.URLError as e:
                if attempt < self.max_catchup_retries - 1:
                    log.debug("Failed to obtain file '%s' after %d attempts: %s",
                              file_name, attempt + 1, e)
                    time.sleep(1)
                    continue
                else:
                    # we've used all our tries, log it and raise an exception
                    _msg = f"Failed to obtain file '{file_name}' after {attempt + 1} attempts: {e}"
                    log.error(_msg)
                    raise DeviceIOError(_msg)
            else:
                return response

    @staticmethod
    def get_file_list(sd_info, start_ts=None):
        """Extract a dict of history files from a get_sdmmc_info response.

        Extracts a dict of available history files from a get_sdmmc_info
        response. If start_ts is provided only names of files that may
        contain data on or after start_ts are returned. If start_ts is omitted
        or None then all available file names are returned.

        History file names are of the format 'YYYYMMxxxxxxxxZ.csv' where:

        YYYY    is the four digit year the file refers to
        MM      is the two digit (zero padded) month number the file refers to
        xxxxx   is an optional string which may be zero length or some other
                string (at present Ecowitt uses a zero length string or
                'Allsensors_' for xxxxx)
        Z       is an upper case alphabetical character to indicate changes to
                units, sensor names etc during a month. At the start of each
                month the character 'A' is used, then if there is a change to
                units or sensor name the character 'B' is used, then 'C' etc
                to 'Z'

        The resulting dict is keyed by an arbitrary calculated year-month
        index. Such an index facilitates easier discrimination and sorting of
        relevant history files. The index is a positive integer calculated
        using the following formula:

            YYYY * 100 + MM

            where YYYY is year number and MM is month number (1-12)

        The file list is then sorted by this index from low to high. A similar
        index is also calculated for start_ts and if start_ts is non-None only
        files whose index is >= the start_ts index are included in the
        response.

        Returns a dict keyed by year-month index with each entry containing a
        list of files for the year-month.
        """

        file_list = sd_info['file_list']
        # now extract a list of file names but only include those whose type
        # is 'file' (some firmware says '1'), and leave out files that are not
        # history files, such as 'log': their names don't start with a year
        # and month (ecowitt_http.py 0.1.0a28 stopped the catchup with a
        # ValueError at such a file)
        files = [f['name'] for f in file_list if is_history_file(f)]
        # Calculate an 'index' for start_ts based on the start_ts year and
        # month. The earlier the month and year the lower the index. This makes
        # it easier to sort files by date and exclude files that are too old.
        # If start_ts was not specified or is None we want all files so set the
        # index to zero.
        if start_ts is not None:
            ts_dt = datetime.datetime.fromtimestamp(start_ts)
            ts_tt = ts_dt.timetuple()
            ts_index = ts_tt.tm_year * 100 + ts_tt.tm_mon
        else:
            # start_ts is None so we want all files, set the index to zero
            ts_index = 0
        indexed_files = {}
        for file in files:
            index = int(file[0:4]) * 100 + int(file[4:6])
            # compare the file and start_ts indices to determine if the file
            # might contain required data
            if index >= ts_index:
                # this file might contain required data
                if index not in indexed_files.keys():
                    # we have not seen a file from this year and month before
                    # so add an empty entry for the current file year-month
                    # index to our dict
                    indexed_files[index] = []
                indexed_files[index].append(file)
        return indexed_files


# ============================================================================
#                          class EcowittHttpDriver
# ============================================================================

class EcowittHttpDriver(weewx.drivers.AbstractDevice, EcowittCommon):
    """Driver class for an Ecowitt device using the local HTTP API.

    A WeeWX driver to emit loop packets based on data obtained from an Ecowitt
    device using the local HTTP API. The EcowittHttpDriver should be used when
    the device is the sole WeeWX data source or where data from other sources
    is ingested by WeeWX via one or more WeeWX services.

    Data is obtained from the device via the device local HTTP API. The data is
    parsed and mapped to WeeWX fields and emitted as a WeeWX loop packet.

    The EcowittHttpCollector runs in a separate thread ensuring it does not
    block the main WeeWX processing loop. The EcowittHttpCollector uses child
    classes to interact directly with the HTTP API and parse the API responses
    respectively.
    """

    def __init__(self, archive_interval=None, counters=None, **stn_dict):
        """Initialise an EcowittHttpDriver object.

        archive_interval ([StdArchive], seconds; the loader passes it) is what
        catchup records are checked against, and with several gateways the
        interval of the merged catchup records. (It is kept as
        std_archive_interval: AbstractDevice.archive_interval is the
        hardware's interval, which WeeWX would then use.) counters: the
        cumulative fields WeeWX's [[Delta]] reads (the loader passes them,
        see counter_fields()); only used with several gateways."""

        self.std_archive_interval = weeutil.weeutil.to_int(archive_interval) if archive_interval else None
        # outages of every gateway (several gateways only)
        self.gap_mode = str(stn_dict.get('gap_mode', DEFAULT_GAP_MODE)).lower()
        self.max_gap_wait = weeutil.weeutil.to_int(stn_dict.get('max_gap_wait', DEFAULT_MAX_GAP_WAIT))
        self.min_restart_interval = weeutil.weeutil.to_int(stn_dict.get('min_restart_interval',
                                                                        DEFAULT_MIN_RESTART_INTERVAL))
        # Log our driver version first. Normally we would call our superclass
        # initialisation method first; however, that involves establishing a
        # network connection to the device and it may fail. Doing some basic
        # logging first will aid in debugging.

        log.info('EcowittHttpDriver: version is %s' % DRIVER_VERSION)
        self.unit_system = DEFAULT_UNIT_SYSTEM
        try:
            EcowittCommon.__init__(self, unit_system=self.unit_system, label='EcowittHttpDriver',
                                   counters=counters, **stn_dict)
        except weewx.ViolatedPrecondition as e:
            # we encountered an error during initialization and we cannot
            # continue, raise an InitializationError
            raise weewx.engine.InitializationError from e
        if self.multi:
            log.info('EcowittHttpDriver: when no gateway answers: gap_mode = %s (max_gap_wait %d s, '
                     'min_restart_interval %d s)', self.gap_mode, self.max_gap_wait, self.min_restart_interval)
        # start polling the device(s) (each collector runs in its own thread)
        for source in self.sources:
            source.start()

    def genLoopPackets(self):
        """Generator function that returns loop packets.

        Run a continuous loop checking the collector queue for data. Queue data
        will be either obs data, and exception or None. When obs data arrives
        map the raw data to a WeeWX loop packet and yield the packet. If an
        exception arrives hande any further logging and re-raising. None is the
        signal we need to close.
        """

        # several gateways: merged loop packets
        if self.multi:
            yield from self.gen_merged_loop_packets()
            return
        while True:
            # Get the next item from the queue. Wrap in a try to catch any
            # instances where the queue is empty as that is our signal to wait
            # and try again.
            try:
                queue_data = self.source.get(10)
            except queue.Empty:
                continue
            # a dict is sensor data
            if hasattr(queue_data, 'keys'):
                self.source.log_received(queue_data)
                yield self.source.loop_packet(queue_data)
            # an exception: a DeviceIOError means the device could not be
            # contacted, WeeWX will wait and restart the driver; anything else
            # is unexpected so log and raise it
            elif isinstance(queue_data, BaseException):
                if isinstance(queue_data, DeviceIOError):
                    raise weewx.WeeWxIOError from queue_data
                log.error('EcowittHttpDriver: Caught unexpected exception %s: %s' % (queue_data.__class__.__name__,
                                                                                     queue_data))
                raise queue_data
            # None is the signal the collector needs to shut down
            elif queue_data is None:
                if self.driver_debug.loop:
                    log.info('EcowittHttpDriver: Received shutdown signal from the Collector')
                self.closePort()
                raise DeviceIOError("EcowittHttpCollector needs to shutdown")

    def gen_merged_loop_packets(self):
        """Loop packets merged from several gateways.

        A packet is yielded whenever a gateway has delivered new data. One
        gateway not answering doesn't stop them (it is logged, and its
        gateway_online_<name> is 0). When no gateway has answered for longer
        than archive_interval, gap_mode decides what happens once they are
        back: 'restart' holds the loop packets back and then raises
        WeeWxIOError, so that WeeWX restarts the driver and its catchup fills
        the outage from the gateways' history."""

        gap = GapMonitor(self.std_archive_interval or 300, self.gap_mode, self.max_gap_wait,
                         self.min_restart_interval)
        history = {s.name for s in self.sources if (s.catchup_source or 'either').lower() != 'none'}
        while True:
            delivered = self.drain_sources('EcowittHttpDriver')
            now = time.time()
            action = gap.update(now, {s.name: s.status.last_seen for s in self.sources}, delivered, history)
            if action == GapMonitor.RESTART:
                raise weewx.WeeWxIOError('restarting so that catchup fills the outage')
            if action == GapMonitor.EMIT and delivered:
                packet = self.merger.merged(now, self.unit_system)
                packet.update(self.online_fields(now, self.merger.max_age))
                if self.driver_debug.loop or weewx.debug >= 2:
                    log_dict(f'EcowittHttpDriver: Merged packet {timestamp_to_string(packet["dateTime"])}: ',
                             packet)
                elif self.driver_debug.rain:
                    self.log_rain_data(packet, 'EcowittHttpDriver: Merged packet')
                yield packet
                continue
            time.sleep(0.5)

    def genStartupRecords(self, last_ts):
        """Generator to return archive records on WeeWX startup.

        genStartupRecords() is called on WeeWX startup to allow catchup of any
        missing records since WeeWX last stopped/restarted.

        last_ts is the timestamp of the last good archive record at time of
        startup/restart; last_ts will be None if the WeeWX archive is empty.
        The default driver genStartupRecords() action is to call
        genArchiveRecords(). In the case of the Ecowitt HTTP driver
        genStartupRecords() is overridden to cater for some additional debug
        output before genArchiveRecords() is called.

        last_ts: the timestamp after which startup archive records are to be
                 generated, may be None

        Returns a generator function yielding archive records timestamped after
        last_ts.
        """

        return self.genArchiveRecords(last_ts)

    def genArchiveRecords(self, lastgood_ts):
        """Generator to return archive records timestamped after a given time.

        Ecowitt now provides the ability to obtain archive-like records from
        either the local device or Ecowitt.net. Note that at the time of
        release the GW3000 is the only device that supports external access to
        stored archive-like records. Note also that Ecowitt.net uses a sliding
        scale of data aggregation such that data from the past 90 days is
        aggregated using a five-minute interval, but data older than 90 days is
        aggregated using a 30 minute, four hour or 24-hour aggregation period
        depending on age.

        genArchiveRecords() is called by genStartupRecords() on WeeWX startup
        to allow catchup of any missing archive records since WeeWX last
        stopped/restarted. The Ecowitt HTTP driver support catchup from the
        local device (if supported) or from Ecowitt.net. The default driver
        action is to first attempt local device catchup and if unsuccessful
        catchup from Ecowitt.net is attempted. If this is unsuccessful catchup
        is abandoned. This behaviour can be tailored by use of the driver
        catchup_source config option.

        lastgood_ts is the timestamp of the last good archive record at time of
        startup/restart; lastgood_ts will be None if the WeeWX archive is
        empty. At this time the Ecowitt.net catchup limits itself archive-like
        records within the last 90 days to ensure catchup records have a
        five-minute interval. The default driver genStartupRecords() action is to call
        genArchiveRecords().

        lastgood_ts: the timestamp after which historical archive records are
                     to be generated, may be None

        Returns a generator function yielding archive records timestamped after
        lastgood_ts.
        """

        # several gateways: their history is merged
        if self.multi:
            yield from self.gen_merged_archive_records(lastgood_ts)
            return
        # if necessary log the MAC address being used
        if self.driver_debug.catchup:
            log.info('genArchiveRecords: Using MAC address: %s' % self.mac_address)
        try:
            for rec in self.gen_ecowitt_archive_records(since_ts=lastgood_ts):
                # if necessary log the timestamp of the record being yielded
                if self.driver_debug.catchup:
                    log.info('genArchiveRecords: Yielding archive '
                             'record %s' % timestamp_to_string(rec['dateTime']))
                yield rec
        except DeviceIOError as e:
            log.error('genArchiveRecords: History access error: %s', e)

    def gen_merged_archive_records(self, lastgood_ts):
        """Catchup with several gateways.

        Each gateway's history is combined into archive_interval records, then
        merged by priority like loop packets. The totals WeeWX works rain out
        from are merged too (MergedTotals), from one interval before
        lastgood_ts on, so the first record's rain is known and the loop
        packets carry on from the last record. Records are only made for
        intervals after lastgood_ts."""

        interval = self.std_archive_interval or 300
        if lastgood_ts is None:
            log.info('EcowittHttpDriver: Catchup: the database is empty, so there is nothing to catch up')
            return
        now = time.time()
        per_source = {}
        for source in self.sources:
            try:
                records = list(source.history(since_ts=lastgood_ts - interval))
            except (DeviceIOError, OSError, InvalidApiResponseError) as e:
                log.error('%s: genArchiveRecords: History access error: %s', source.label, e)
                continue
            if records:
                per_source[source.name] = aggregate_records(records, interval, self.merger.counters)
        records = merge_history(per_source, self.merger.priority, interval, lastgood_ts, now,
                                self.merger.totals, self.unit_system)
        if per_source:
            log.info('EcowittHttpDriver: Catchup: %d merged %d-minute record(s) from %s', len(records),
                     interval // 60, ', '.join(n for n in self.merger.priority.names if n in per_source))
        for rec in records:
            if self.driver_debug.catchup:
                log.info('genArchiveRecords: Yielding archive record %s', timestamp_to_string(rec['dateTime']))
            yield rec

    def gen_ecowitt_archive_records(self, since_ts):
        """Generator to return Ecowitt archive records timestamped after since_ts.

        The records come from the gateway's catchup source (the device SD card
        or Ecowitt.net), see GatewaySource.history().
        """

        return self.source.history(since_ts, archive_interval=self.std_archive_interval)

    def catchup_factory(self):
        """Return a 'catchup source' object, see GatewaySource.catchup_factory()."""

        return self.source.catchup_factory()

    @property
    def hardware_name(self):
        """Return the device hardware name.

        Use the device model from our Collector's EcowittDevice object, but if
        this is None use the driver name.
        """

        if self.source.model is not None:
            return self.source.model
        return DRIVER_NAME

    @property
    def mac_address(self):
        """Return the device MAC address."""

        return self.source.mac_address

    def closePort(self):
        """Close down the driver port."""

        # in this case there is no port to close, just shutdown the collector(s)
        self.stop_sources()


# ============================================================================
#                              class Collector
# ============================================================================

class Collector:
    """Base class for a threaded client to pass data to a parent via a queue."""

    def __init__(self):
        self.queue = queue.Queue()

    def startup(self):
        pass

    def shutdown(self):
        pass


# ============================================================================
#                         class EcowittHttpCollector
# ============================================================================

class EcowittHttpCollector(Collector):
    """Class to collect and pass Ecowitt device data to a WeeWX service/driver.

    An EcowittHttpCollector object is responsible for collecting, parsing and
    dispatching data to a WeeWX driver/service. Specifically, an
    EcowittHttpCollector object:

    1. obtains data from an Ecowitt device via the local HTTP API,
    2. parses the data obtained from an Ecowitt device, and
    3. passes relevant parsed data to the parent driver/service.

    An EcowittHttpCollector object uses subordinate classes to obtain and parse
    data from the device. An EcowittHttpCollector object can also be used to
    obtain device data when the driver is operated in direct mode.
    """

    def __init__(self, ip_address,
                 poll_interval=DEFAULT_POLL_INTERVAL,
                 max_tries=DEFAULT_MAX_TRIES,
                 retry_wait=DEFAULT_RETRY_WAIT,
                 url_timeout=DEFAULT_URL_TIMEOUT,
                 unit_system=DEFAULT_UNIT_SYSTEM,
                 show_battery=DEFAULT_FILTER_BATTERY,
                 log_unknown_fields=False,
                 fw_update_check_interval=DEFAULT_FW_CHECK_INTERVAL,
                 sensor_map=None,
                 api=DEFAULT_API,
                 tcp_port=DEFAULT_TCP_PORT,
                 log_next_update=DEFAULT_LOG_NEXT_UPDATE,
                 debug = DebugOptions()):
        """Initialise a EcowittHttpCollector object."""

        super().__init__()

        # multi-channel sensors reported on fixed channels ([[sensor_map]])
        self.sensor_mapper = SensorMapper(sensor_map)
        if self.sensor_mapper:
            log.info('     %d sensor(s) reported on fixed channels by sensor ID (sensor_map)',
                     len(self.sensor_mapper.targets))

        # interval between polls of the API, defaults to DEFAULT_POLL_INTERVAL
        self.poll_interval = poll_interval
        # whether 'Next update in N seconds' is logged after each poll (debug)
        self.log_next_update = log_next_update
        self.debug = debug
        # how often to check for a firmware update, defaults to
        # DEFAULT_FW_CHECK_INTERVAL
        self.fw_update_check_interval = fw_update_check_interval
        # log our config options before obtaining an EcowittDevice object, this
        # will help in remote debugging should the device be uncontactable
        if self.fw_update_check_interval > 0:
            log.debug('     device firmware update checks will occur every %d '
                      'seconds' % self.fw_update_check_interval)
            log.debug('     available device firmware updates will be logged')
        else:
            log.debug('     device firmware update checks will not occur')
        if show_battery:
            log.debug('     battery state will be reported for all sensors')
        else:
            log.debug('     battery state will not be reported for sensors with no signal data')
        if log_unknown_fields:
            log.debug('     unknown fields will be reported')
        else:
            log.debug('     unknown fields will be ignored')

        # Obtain an object to handle interaction with the device: an
        # EcowittDevice (local HTTP API) or an EcowittTcpDevice (GW1000 TCP
        # API). If the API cannot be determined because the device cannot be
        # contacted, it is determined again when data is next requested.
        def _make_device():
            return make_device(ip_address, api=api, tcp_port=tcp_port,
                               unit_system=unit_system,
                               max_tries=max_tries,
                               retry_wait=retry_wait,
                               url_timeout=url_timeout,
                               show_battery=show_battery,
                               log_unknown_fields=log_unknown_fields,
                               debug=debug)
        self.make_device = _make_device
        self.device = self.make_device()

        self.log_failures = True
        self.thread = None
        # we start off not collecting data, it will be turned on later when we
        # are threaded
        self.collect_data = False

    def collect(self):
        """Collect and queue sensor data.

        Loop forever waking periodically to see if it is time to quit or
        collect more data. A dictionary of data is placed in the queue on each
        successful poll of the device. If an exception is raised when
        interacting with the device the exception is placed in the queue as a
        signal to our parent there is a problem.
        """

        last_poll = 0
        # initialise ts of last firmware check
        last_fw_check = 0
        # collect data continuously while we are told to collect data
        while self.collect_data:
            now = time.time()
            # is it time to poll?
            if now - last_poll > self.poll_interval:
                if self.debug.collector:
                    log.info("Polling the device, time '%d' "
                             "last poll '%d' elapsed '%d'", int(now), int(last_poll), int(now - last_poll))
                # it is time to poll, wrap in a try..except in case we get a
                # DeviceIOError exception
                try:
                    queue_data = self.get_current_data()
                except DeviceIOError as e:
                    # a DeviceIOError occurred, most likely because the
                    # EcowittDevice object could not contact the device
                    # first up log the event, but only if we are logging
                    # failures
                    if self.log_failures:
                        log.error('Unable to obtain live sensor data')
                    # assign the DeviceIOError exception to queue_data so it
                    # will be sent in the queue to our controlling object
                    queue_data = e
                except Exception as e:
                    # A response the parser could not handle (eg not JSON
                    # the driver expects). Treat it as a failed poll rather
                    # than let it end this thread, which would leave the
                    # driver waiting for data for ever.
                    if self.log_failures:
                        log.error('Unexpected response from the device: %s: %s', e.__class__.__name__, e)
                        weeutil.logger.log_traceback(log.debug, '    ****  ')
                    queue_data = DeviceIOError(f'Unexpected response from the device '
                                               f'({e.__class__.__name__}: {e})')
                if self.debug.collector:
                    if hasattr(queue_data, 'keys'):
                        log_dict('Collected data: ', queue_data)
                    else:
                        log.info('Collected data: %s', queue_data)
                # put the queue data in the queue
                self.queue.put(queue_data)
                # debug log when we will next poll the API ('log_next_update =
                # False' keeps debug logging without this line every poll)
                if self.debug.collector or (weewx.debug and self.log_next_update):
                    log.info('Next update in %d seconds', self.poll_interval)
                last_poll = now
                if self.debug.collector:
                    log.info("Firmware update check,  time '%d' "
                             "last check '%d' elapsed '%d'", int(now), int(last_fw_check), int(now - last_fw_check))
                # do a firmware update check if required
                if 0 < self.fw_update_check_interval < now - last_fw_check:
                    if self.debug.collector:
                        log.info('Performing firmware update check')
                    # the check contacts the device again: a failure here must
                    # not end this thread (it is tried again next interval)
                    try:
                        self.firmware_update_check()
                    except Exception as e:
                        log.debug('Firmware update check failed: %s: %s', e.__class__.__name__, e)
                    last_fw_check = now
                    if self.debug.collector:
                        log.info('Firmware update check complete')
            # sleep for a second and then see if it's time to poll again
            time.sleep(1)

    def firmware_update_check(self):
        """Log a firmware update if the device has one."""

        if self.device.firmware_update_avail:
            _msg = f"A firmware update is available, current {self.device.model} "\
                   f"firmware version is {self.device.firmware_version}"
            log.info(_msg)
            _msg = f"    update at http://{self.device.ip_address} or via "\
                   f"the WSView Plus app"
            log.info(_msg)
            curr_msg = self.device.firmware_update_message
            if curr_msg is not None:
                log.info("    firmware update message: '%s'", curr_msg)
            else:
                log.info('    no firmware update message found')

    def get_current_data(self):
        """Get all current sensor data.

        Return current sensor data, battery state data and signal state data
        for each sensor. The current sensor data consists of sensor data
        available through multiple API api_commands. Each API command response is
        parsed and the results accumulated in a dictionary. Battery and signal
        state for each sensor is added to this dictionary. The dictionary is
        timestamped and the timestamped accumulated data is returned. If the
        API does not return any data a suitable exception will have been
        raised.
        """

        _timestamp = int(time.time())
        # if the device API could not be determined, try again
        if not getattr(self.device, 'detected', True):
            self.device = self.make_device()
        # Now obtain the current data via the API. If the data cannot be
        # obtained we will see a DeviceIOError exception which we just let
        # bubble up. Otherwise, we are returned the parsed current live data.
        curr_data = self.device.get_live_data()
        curr_data['datetime'] = _timestamp
        # The current live data contains sensor battery state data, but no
        # signal level data. So obtain the sensor info and add it to the
        # current live data. We will end up with battery data for each sensor
        # appearing twice under two different fields, but later field mapping
        # will take care of this.
        curr_data.update(self.device.get_sensors_data())
        # report any mapped multi-channel sensors on their fixed channels
        curr_data = self.sensor_mapper.apply(curr_data)
        if weewx.debug >= 3:
            log_dict('Current data: ', curr_data, logging.DEBUG)
        return curr_data

    def startup(self):
        """Start a thread that collects data from the API."""

        try:
            self.thread = EcowittHttpCollector.CollectorThread(self)
            self.collect_data = True
            self.thread.daemon = True
            self.thread.name = 'EcowittHttpCollectorThread'
            self.thread.start()
        except threading.ThreadError:
            log.error('Unable to launch EcowittHttpCollector thread')
            self.thread = None

    def shutdown(self):
        """Shut down the thread that collects data from the API.

        Tell the thread to stop, then wait for it to finish.
        """

        # we only need do something if a thread exists
        if self.thread:
            self.collect_data = False
            # terminate the thread
            self.thread.join(10.0)
            if self.thread.is_alive():
                log.error('Unable to shut down EcowittHttpCollector thread')
            else:
                log.info('EcowittHttpCollector thread has been terminated')
        self.thread = None

    class CollectorThread(threading.Thread):
        """Class using a thread to collect data via the local HTTP API."""

        def __init__(self, client):
            threading.Thread.__init__(self)
            self.client = client
            self.name = 'weewx-ecowittGateway-collector'

        def run(self):
            # rather than letting the thread silently fail if an exception
            # occurs within the thread, wrap in a try..except so the exception
            # can be caught and available exception information displayed
            try:
                # kick the collection off
                self.client.collect()
            except Exception as e:
                # we have an exception so log what we can
                weeutil.logger.log_traceback(log.critical, '    ****  ')


# ============================================================================
#                            class EcowittHttpApi
# ============================================================================

class EcowittHttpApi:
    """Class to interact with an Ecowitt device via the local HTTP API."""

    # HTTP request commands
    commands = ['get_version', 'get_livedata_info', 'get_ws_settings',
                'get_calibration_data', 'get_rain_totals', 'get_device_info',
                'get_sensors_info', 'get_network_info', 'get_units_info',
                'get_cli_soilad', 'get_cli_multiCh', 'get_cli_pm25',
                'get_cli_co2', 'get_piezo_rain', 'get_cli_wh34', 'get_cli_lds',
                'get_sdmmc_info']
    # Ecowitt continues to refer to some sensors by multiple model numbers.
    # Create a map to map such sensor models to a common model number in any
    # API responses.
    sensor_rename_map = {
        'wh30': 'wn30',
        'wh31': 'wn31',
        'wh32': 'wn32',
        'wh34': 'wn34',
        'wh35': 'wn35',
        'wh36': 'wn36',
        'wh80': 'ws80',
        'wh85': 'ws85',
        'wh90': 'ws90'
    }


    def __init__(self, ip_address,
                 max_tries=DEFAULT_MAX_TRIES,
                 retry_wait=DEFAULT_RETRY_WAIT,
                 timeout=DEFAULT_URL_TIMEOUT):
        """Initialise an EcowittHttpApi object."""

        # the IP address to be used (stored as a string)
        self.ip_address = ip_address
        # max number of attempts to obtain data from the device
        self.max_tries = max_tries
        # wait time in seconds between attempt to contact the device
        self.retry_wait = retry_wait
        # timeout in seconds to be used for urlopen calls
        self.timeout = timeout
        # whether failed requests are logged as errors (the service turns this
        # off while contact is lost, see EcowittDevice.log_failures)
        self.log_failures = True

    def request(self, command_str, data=None, headers=None, rename=True):
        """Send a HTTP request to the device and return the response.

        Create a HTTP request with optional data and headers. Send the HTTP
        request to the device as a GET request, obtain the response and return
        the JSON deserialized response is returned. If the response cannot be
        deserialized the value None is returned. URL or timeout errors are
        logged and raised.

        command_str: a string containing the command to be sent,
                     eg: 'get_livedata_info'
        data: a dict containing key:value pairs representing the data to be
              sent
        headers: a dict containing headers to be included in the HTTP request
        rename: whether to rename sensor models to their current names (eg
                'wh90' to 'ws90'); False gives the response as the device sent
                it

        Returns a deserialized JSON object or None
        """

        # use empty dicts for data and headers if not provided
        data_dict = {} if data is None else data
        headers_dict = {} if headers is None else headers
        if command_str in EcowittHttpApi.commands:
            # first convert any data to a percent-encoded ASCII text string
            data_enc = urllib.parse.urlencode(data_dict)
            stem = ''.join(['http://', self.ip_address])
            url = '/'.join([stem, command_str])
            # Finally add the encoded data. We need to add the data in this manner
            # rather than using the Request object's 'data' parameter so the
            # request is sent as a GET request rather than a POST request.
            full_url = '?'.join([url, data_enc])
            req = urllib.request.Request(url=full_url, headers=headers_dict)
            for attempt in range(self.max_tries):
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as w:
                        # get charset used so we can decode the stream correctly
                        char_set = w.headers.get_content_charset()
                        # Now get the response and decode it using the headers
                        # character set. Be prepared for charset==None.
                        if char_set is not None:
                            resp = w.read().decode(char_set)
                        else:
                            resp = w.read().decode()
                except socket.timeout as e:
                    # we timed out and failed to obtain data on this attempt,
                    # log it
                    if weewx.debug >= 2:
                        log.debug('Failed to get device data on attempt %d of %d' % (attempt +1,
                                                                                     self.max_tries))
                except urllib.error.URLError as e:
                    # we encountered an error, log the error and raise it (as an
                    # error only while failures are logged: ecowitt_http.py
                    # logged it at every poll while contact was lost)
                    log_error = log.error if self.log_failures else log.debug
                    log_error('Failed to get device data on attempt %d of %d' % (attempt + 1,
                                                                                 self.max_tries))
                    log_error('   **** %s' % e)
                    raise
                else:
                    # our attempt was successful, break out of the for loop
                    break
            else:
                # the for loop terminated normally, so we exhausted all
                # attempts without success
                log.debug('Failed to get device data after %d attempts' % self.max_tries)
                # every attempt timed out, so there is no response
                raise socket.timeout(f'no response after {self.max_tries} attempt(s)')
            # Do a little massaging of the response, Ecowitt refers to some
            # wsxx devices as whxx in the API, fix this at the source. The
            # device model numbers are fairly unique so a simple replace will
            # suffice.
            if rename:
                for old, new in self.sensor_rename_map.items():
                    resp = resp.replace(old, new)
            # we have a response but can it be deserialized it to a python
            # object, wrap in a try..except in case it cannot be deserialized
            try:
                resp_json = json.loads(resp)
            except json.JSONDecodeError as e:
                # cannot deserialize the response, log it and return None
                log.error('Cannot deserialize device response')
                log.error('   **** %s' % e)
                return None
            if weewx.debug >= 3:
                log_long('Deserialized HTTP response: ', json.dumps(resp_json), logging.DEBUG)
            return resp_json
        # an invalid command
        raise UnknownApiCommand(f"Unknown HTTP API command '{command_str}'")

    def get_version(self):
        """Get the device firmware related information.

        Returns a dict or None if no valid data was returned by the device."""

        try:
            return self.request('get_version')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_version' data: {e}")

    def get_livedata_info(self):
        """Get live sensor data from the device.

        Returns a dict or None if no valid data was returned by the device."""

        try:
            return self.request('get_livedata_info')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_livedata_info' data: {e}")

    def get_ws_settings(self):
        """Get weather services settings from the device.

        Returns a dict or None if no valid data was returned by the device."""

        try:
            return self.request('get_ws_settings')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_ws_settings' data: {e}")

    def get_calibration_data(self):
        """Get calibration settings from the device.

        Returns a dict or None if no valid data was returned by the device."""

        try:
            return self.request('get_calibration_data')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_calibration_data' data: {e}")

    def get_rain_totals(self):
        """Get rainfall totals and settings from the device.

        Returns a dict or None if no valid data was returned by the device."""

        try:
            return self.request('get_rain_totals')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_rain_totals' data: {e}")

    def get_device_info(self):
        """Get device settings from the device.

        Returns a dict or None if no valid data was returned by the device."""

        try:
            return self.request('get_device_info')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_device_info' data: {e}")

    def get_sensors_info(self, page=0):
        """Get sensor ID data from the device.

        Obtains sensor state data via a 'get_sensors_info' API call. Sensor
        state data provided via the API consists of multiple pages with only
        one page being returned for any one API call. Currently there are two
        sensor state data pages; page 1 and page 2. This method returns page 1,
        page 2 or a combined all pages of sensor state data depending on the
        'page' parameter.

        page: specify which page of sensor state data to return, 0 = return all
              pages, 1 = return page 1, 2 = return page 2

        Returns a dict of data from the 'get_sensors_info' API command.
        """

        page_1 = page_2 = None
        if page in [0, 1]:
            try:
                page_1 = self.request('get_sensors_info', data={'page': 1})
            except (socket.timeout, urllib.error.URLError) as e:
                raise DeviceIOError(f"Failed to obtain 'get_sensors_info' page 1 data: {e}")
        if page in [0, 2]:
            try:
                page_2 = self.request('get_sensors_info', data={'page': 2})
            except (socket.timeout, urllib.error.URLError) as e:
                raise DeviceIOError(f"Failed to obtain 'get_sensors_info' page 2 data: {e}")
        if page_1 is not None and page_2 is not None:
            return page_1 + page_2
        if page_1 is None:
            return page_2
        return page_1

    def get_network_info(self):
        """Get network related data/settings from the device.

        Returns a dict or None if no valid data was returned by the device.
        """

        try:
            return self.request('get_network_info')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_network_info' data: {e}")

    def get_units_info(self):
        """Get units settings from the device.

        Returns a dict or None if no valid data was returned by the device.
        """

        try:
            return self.request('get_units_info')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_units_info' data: {e}")

    def get_cli_soilad(self):
        """Get multichannel soil moisture sensor calibration data from the device.

        Returns a list of dicts or None if no valid data was returned by the
        device.
        """

        try:
            return self.request('get_cli_soilad')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_cli_soilad' data: {e}")

    def get_cli_multi_ch(self):
        """Get multichannel temperature/humidity sensor calibration data from
        the device.

        Returns a list of dicts or None if no valid data was returned by the
        device.
        """

        try:
            return self.request('get_cli_multiCh')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_cli_multiCh' data: {e}")

    def get_cli_pm25(self):
        """Get PM2.5 sensor offset data from the device.

        Returns a list of dicts or None if no valid data was returned by the
        device.
        """

        try:
            return self.request('get_cli_pm25')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_cli_pm25' data: {e}")

    def get_cli_co2(self):
        """Get CO2 sensor offset data from the device.

        Returns a list of dicts or None if no valid data was returned by the
        device.
        """

        try:
            return self.request('get_cli_co2')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_cli_co2' data: {e}")

    def get_piezo_rain(self):
        """Get piezo rain sensor data/settings from the device.

        Returns a dict or None if no valid data was returned by the device.
        """

        try:
            return self.request('get_piezo_rain')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_piezo_rain' data: {e}")

    def get_cli_wh34(self):
        """Get WN34 sensor offset data from the device.

        Returns a list of dicts or None if no valid data was returned by the
        device.
        """

        try:
            return self.request('get_cli_wh34')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_cli_wh34' data: {e}")

    def get_cli_lds(self):
        """Get laser distance sensor (WH54) sensor offset data from the device.

        Returns a list of dicts or None if no valid data was returned by the
        device.
        """

        try:
            return self.request('get_cli_lds')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_cli_lds' data: {e}")

    def get_sdmmc_info(self):
        """Get SD card info from the device.

        Returns a dict or None if no valid data was returned by the device.
        """

        try:
            return self.request('get_sdmmc_info')
        except (socket.timeout, urllib.error.URLError) as e:
            raise DeviceIOError(f"Failed to obtain 'get_sdmmc_info' data: {e}")


# a field missing from an API response (EcowittHttpParser._obs())
MISSING = object()



# ============================================================================
#                          class EcowittHttpParser
# ============================================================================

class EcowittHttpParser:
    """Parse the JSON responses of the local HTTP API.

    Each parse_<call>() method takes the response to one API call (eg
    parse_get_livedata_info() for get_livedata_info) and returns its data as a
    dict, or list of dicts, with numbers as numbers. Readings in live data are
    converted to the driver's unit system. A reading that can't be read is
    None; a field missing from the response is missing from the result. A
    response that can't be parsed at all raises ParseError.

    get_livedata_info returns arrays of readings, each handled by a
    process_<array>_array() method; common_list (and rain, piezoRain) entries
    are each handled by the process_<kind>_object() method processor_fns
    names for their id.
    """

    # units the gateway reports some groups in whatever its settings
    default_device_units = {
        'group_volt': 'volt',
        'group_uv': 'uv_index',
        'group_percent': 'percent',
        'group_direction': 'degree_compass',
        'group_fraction': 'ppm',
        'group_concentration': 'microgram_per_meter_cubed',
        'group_boolean': 'boolean'
    }
    # WeeWX unit for each (lower case) Ecowitt unit string
    unit_lookup = {
        'c': 'degree_C',
        'f': 'degree_F',
        'km': 'km',
        'mi': 'mile',
        'nmi': 'nautical_mile',
        'hpa': 'hPa',
        'kfc': 'kfc',
        'klux': 'klux',
        'kpa': 'kPa',
        'inhg': 'inHg',
        'mmhg': 'mmHg',
        'mm': 'mm',
        'in': 'inch',
        'ft': 'foot',
        'mm/hr': 'mm_per_hour',
        'in/hr': 'inch_per_hour',
        'km/h': 'km_per_hour',
        'm/s': 'meter_per_second',
        'mph': 'mile_per_hour',
        'knots': 'knot',
        '%': 'percent',
        'w/m2': 'watt_per_meter_squared'
    }
    # processor for each common_list id
    processor_fns = {
        '0x01': 'process_temperature_object',   # inside temperature
        '0x02': 'process_temperature_object',   # outside temperature
        '0x03': 'process_temperature_object',   # dewpoint
        '3': 'process_temperature_object',      # feels like (WS View Plus shows it as that)
        '0x04': 'process_temperature_object',   # wind chill
        '4': 'process_temperature_object',      # not seen in responses
        '0x05': 'process_temperature_object',   # heat index
        '5': 'process_pressure_object',         # VPD (kPa, inHg or mmHg)
        '0x07': 'process_humidity_object',      # outdoor humidity
        '0x08': 'process_noop_object',          # absolute pressure: in the wh25 array
        '0x09': 'process_noop_object',          # relative pressure: in the wh25 array
        '0x0A': 'process_direction_object',     # wind direction
        '0x0B': 'process_speed_object',         # wind speed
        '0x0C': 'process_speed_object',         # gust speed
        '0x0D': 'process_rainfall_object',      # rain event
        '0x0E': 'process_rainrate_object',      # rain rate
        '0x0F': 'process_rainfall_object',      # rain hour
        '0x10': 'process_rainfall_object',      # rain day
        '0x11': 'process_rainfall_object',      # rain week
        '0x12': 'process_rainfall_object',      # rain month
        '0x13': 'process_rainfall_object',      # rain year
        '0x14': 'process_rainfall_object',      # rain totals
        '0x15': 'process_light_object',         # light
        '0x16': 'process_uv_radiation_object',  # uv
        '0x17': 'process_index_object',         # uv index
        '0x18': 'process_noop_object',          # date and time
        '0x19': 'process_speed_object',         # day max wind speed
        '0x6D': 'process_direction_object',     # 10 minute average wind direction
        '0x7C': 'process_rainfall_object',      # rain in the last 24 hours
        '0x7D': 'process_rainfall_object',      # rain in the last hour
        'srain_piezo': 'process_boolean_object'  # is raining
    }
    # get_rain_totals and get_piezo_rain fields
    rain_map = {
        'day_rain': 'rainDay',
        'week_rain': 'rainWeek',
        'month_rain': 'rainMonth',
        'year_rain': 'rainYear'
    }
    piezo_rain_map = {
        'day_rain': 'drain_piezo',
        'week_rain': 'wrain_piezo',
        'month_rain': 'mrain_piezo',
        'year_rain': 'yrain_piezo'
    }
    # ids of sensors that are learning/registering or disabled
    not_registered = ('fffffffe', 'ffffffff')

    def __init__(self, unit_system=DEFAULT_UNIT_SYSTEM, show_battery=DEFAULT_FILTER_BATTERY,
                 log_unknown_fields=True, debug=DebugOptions()):
        self.unit_system = unit_system
        self.show_battery = show_battery
        self.log_unknown_fields = log_unknown_fields
        self.debug = debug

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _as_dict(response, name):
        """Check a response is a JSON object."""
        try:
            return dict(response)
        except (TypeError, ValueError) as e:
            raise ParseError(f"Error parsing '{name}' data: {e}")

    @staticmethod
    def _set(out, dest, source, key, fn, error=None, errors=(ValueError,), get=False):
        """out[dest] = fn(source[key]), or error if fn raises one of errors.

        Nothing is set if source has no such key; with get, source.get(key) is
        used, so dest is always set."""
        if get:
            raw = source.get(key)
        else:
            try:
                raw = source[key]
            except KeyError:
                return
        try:
            out[dest] = fn(raw)
        except errors:
            out[dest] = error

    def _obs(self, item, key, unit_group, how='convert', device_units=None):
        """A reading with a unit, item[key], as: 'convert' a value in the
        driver's units, 'raw' the value as reported, 'int' that as an int, 'vt'
        a ValueTuple. MISSING if there is no such key, None if the value can't
        be read. A UnitError is raised (no unit for unit_group)."""
        try:
            vt = self.parse_obs_value(key=key, json_object=item, unit_group=unit_group,
                                      device_units=device_units)
        except KeyError:
            return MISSING
        except ParseError:
            return None
        return self._value(vt, how)

    def _value(self, vt, how):
        """A ValueTuple as _obs() returns it."""
        if how == 'convert':
            return weewx.units.convert(vt, weewx.units.std_groups[self.unit_system][vt.group]).value
        if how == 'int':
            return int(vt.value)
        return vt.value if how == 'raw' else vt

    def _add(self, out, item, key, unit_group, how='convert'):
        """out[key] = self._obs(...), unless missing. Returns the value."""
        value = self._obs(item, key, unit_group, how)
        if value is not MISSING:
            out[key] = value
        return value

    @staticmethod
    def _channel(sensor, key='channel'):
        """A sensor's channel number, or None."""
        try:
            return int(sensor[key])
        except (KeyError, TypeError, ValueError):
            return None

    # -- API responses ----------------------------------------------------------

    @staticmethod
    def parse_get_version(response):
        """get_version: {'version': 'Version: GW2000C_V3.1.2', 'newVersion': '0',
        'platform': 'ecowitt'}. Adds 'firmware_version' (V3.1.2)."""
        EcowittHttpParser._as_dict(response, 'parse_get_version')
        _parsed_data = dict()
        if 'version' in response:
            _parsed_data['version'] = str(response['version'])
            try:
                _parsed_data['firmware_version'] = str(response['version']).split('_')[1].strip()
            except IndexError:
                _parsed_data['firmware_version'] = None
        EcowittHttpParser._set(_parsed_data, 'newVersion', response, 'newVersion', weeutil.weeutil.to_int)
        _parsed_data['platform'] = response.get('platform')
        return _parsed_data

    def parse_get_livedata_info(self, response, flatten_data=True):
        """get_livedata_info: each array of readings processed by its
        process_<array>_array() method, flattened to 'array.key.field' keys
        unless flatten_data is False. Unknown arrays are logged and skipped."""
        if response is None:
            raise ParseError("Error parsing 'get_livedata_info' data: No raw data")
        data = dict()
        for group, group_data in response.items():
            try:
                _fn = getattr(self, '_'.join(['process', group, 'array']))
            except AttributeError:
                if weewx.debug or self.debug.parser or self.log_unknown_fields:
                    log.info("Skipped unknown livedata group '%s'", group)
                continue
            try:
                data[group] = _fn(group_data)
            except ProcessorError as e:
                if weewx.debug or self.debug.parser or self.log_unknown_fields:
                    log.info("Error processing livedata group '%s': %s", group, e)
        return flatten(data) if flatten_data else data

    def parse_get_sensors_info(self, response, connected_only=DEFAULT_ONLY_REGISTERED_SENSORS, flatten_data=True):
        """get_sensors_info (all pages): the state of each sensor, by model and,
        for multi-channel sensors, channel (eg 'wn31.ch1.signal')."""
        if response is None:
            raise ParseError("Error parsing 'get_sensors_info' data: No raw data")
        _parsed = dict()
        for sensor in response:
            model, channel, processed_data = self.process_sensor_array(sensor, connected_only=connected_only)
            if model is not None:
                _parsed.setdefault(model, dict())
                if channel is not None:
                    _parsed[model][channel] = processed_data
                else:
                    _parsed[model] = processed_data
        return flatten(_parsed) if flatten_data else _parsed

    @staticmethod
    def parse_get_ws_settings(response):
        """get_ws_settings: the upload settings, with intervals, ports and the
        like as ints, 'Customized' a bool and 'Protocol' lower case."""
        to_int_fields = ('ost_interval', 'wu_interval', 'wcl_interval', 'wow_interval', 'mqtt_transport',
                         'mqtt_port', 'mqtt_keepalive', 'mqtt_interval', 'ecowitt_port', 'ecowitt_upload',
                         'usr_wu_port', 'usr_wu_upload')
        _parsed = EcowittHttpParser._as_dict(response, 'parse_get_ws_settings')
        for field in to_int_fields:
            EcowittHttpParser._set(_parsed, field, response, field, weeutil.weeutil.to_int)
        EcowittHttpParser._set(_parsed, 'Customized', response, 'Customized', lambda v: v.lower() == 'enable',
                               errors=(AttributeError,))
        EcowittHttpParser._set(_parsed, 'Protocol', response, 'Protocol', lambda v: v.lower(),
                               errors=(AttributeError,))
        return _parsed

    @staticmethod
    def parse_get_calibration_data(response, device_units):
        """get_calibration_data: gains as floats, offsets (and altitude) as
        ValueTuples in the device's units (ValueTuple(None, None, None) if
        unreadable), and the '*_cli' flags as bools."""
        EcowittHttpParser._as_dict(response, 'parse_get_calibration_data')
        to_float, to_bool = weeutil.weeutil.to_float, weeutil.weeutil.to_bool
        _parsed_data = dict()
        for dest, src in (('solar_wave', 'SolarRadWave'), ('solar_gain', 'solarRadGain'),
                          ('uv_gain', 'uvGain'), ('wind_gain', 'windGain')):
            EcowittHttpParser._set(_parsed_data, dest, response, src, to_float, get=True)
        for dest, src, unit, group in (('intemp_offset', 'inTempOffset', None, 'group_deltat'),
                                       ('inhumid_offset', 'inHumiOffset', 'percent', 'group_percent'),
                                       ('abs_offset', 'absOffset', None, 'group_pressure'),
                                       ('rel_offset', 'relOffset', None, 'group_pressure'),
                                       ('altitude', 'altitude', None, 'group_altitude'),
                                       ('outtemp_offset', 'outTempOffset', None, 'group_deltat'),
                                       ('outhumid_offset', 'outHumiOffset', 'percent', 'group_percent'),
                                       ('winddir_offset', 'windDirOffset', 'degree_compass', 'group_direction')):
            if src in response:
                try:
                    _offset = to_float(response[src])
                except ValueError:
                    _parsed_data[dest] = weewx.units.ValueTuple(None, None, None)
                else:
                    _parsed_data[dest] = weewx.units.ValueTuple(_offset, unit or device_units[group], group)
        for flag in ('th_cli', 'wh34_cli', 'pm25_cli', 'soil_cli'):
            EcowittHttpParser._set(_parsed_data, flag, response, flag, to_bool)
        return _parsed_data

    @staticmethod
    def parse_get_rain_totals(response, device_units):
        """get_rain_totals: the tipping gauge's totals (ValueTuples in the
        device's rain unit), gain, reset times, rain priority and the list of
        gauges."""
        EcowittHttpParser._as_dict(response, 'get_rain_totals')
        to_int = weeutil.weeutil.to_int
        _parsed_data = dict()
        EcowittHttpParser._set(_parsed_data, 'rain_priority', response, 'rainFallPriority', to_int, get=True)
        if 'list' in response:
            _parsed_data['rain_list'] = response['list']
        for dest, src in EcowittHttpParser.rain_map.items():
            EcowittHttpParser._set(_parsed_data, dest, response, src,
                                   lambda v: weewx.units.ValueTuple(weeutil.weeutil.to_float(v),
                                                                    device_units['group_rain'], 'group_rain'),
                                   error=weewx.units.ValueTuple(None, None, None))
        EcowittHttpParser._set(_parsed_data, 'rain_gain', response, 'rainGain', weeutil.weeutil.to_float, get=True)
        for dest, src in (('rain_reset_day', 'rstRainDay'), ('rain_reset_week', 'rstRainWeek'),
                          ('rain_reset_year', 'rstRainYear'), ('rain_piezo', 'piezo')):
            EcowittHttpParser._set(_parsed_data, dest, response, src, to_int, get=True)
        for gauge in _parsed_data.get('rain_list', ()):
            try:
                gauge['value'] = to_int(gauge['value'])
            except KeyError:
                pass
        return _parsed_data

    @staticmethod
    def parse_get_device_info(response):
        """get_device_info: device settings (frequency, time zone, AP ...), with
        the AP password obfuscated and 'date' as a timestamp."""
        to_int = weeutil.weeutil.to_int
        _parsed_data = dict()
        for dest, src in (('sensor_type', 'sensorType'), ('rf_freq', 'rf_freq'), ('afc', 'AFC'),
                          ('tz_auto', 'tz_auto'), ('tz_name', None), ('tz_index', 'tz_index'),
                          ('dst', 'dst_stat'), ('rad_comp', 'radcompensation'), ('upgrade', 'upgrade'),
                          ('ap_auto', 'apAuto'), ('new_version', 'newVersion'), ('curr_msg', None),
                          ('ap', None), ('time', 'time')):
            if src is None:
                EcowittHttpParser._set(_parsed_data, dest, response, 'apName' if dest == 'ap' else dest,
                                       lambda v: v)
            else:
                EcowittHttpParser._set(_parsed_data, dest, response, src, to_int)
        if 'APpwd' in response:
            _parsed_data['ap_pwd'] = obfuscate(response['APpwd'])
        try:
            _date_dt = datetime.datetime.strptime(response['date'], '%Y-%m-%dT%H:%M')
        except KeyError:
            pass
        except (TypeError, ValueError):
            _parsed_data['date'] = None
        else:
            _parsed_data['date'] = int(time.mktime(_date_dt.timetuple()))
        return _parsed_data

    @staticmethod
    def parse_get_network_info(response):
        """get_network_info: the network settings, with the IP types as ints
        and the WiFi password obfuscated."""
        _parsed_data = EcowittHttpParser._as_dict(response, 'parse_get_network_info')
        wifi_pwd = _parsed_data.pop('wifi_pwd', None)
        EcowittHttpParser._set(_parsed_data, 'eth_ip_type', response, 'ethIpType', weeutil.weeutil.to_int)
        EcowittHttpParser._set(_parsed_data, 'staIpType', response, 'staIpType', weeutil.weeutil.to_int)
        if wifi_pwd is not None:
            _parsed_data['wifi_pwd'] = obfuscate(wifi_pwd)
        return _parsed_data

    @staticmethod
    def parse_get_units_info(response):
        """get_units_info: the device's unit settings, as ints."""
        _parsed_data = EcowittHttpParser._as_dict(response, 'parse_get_units_info')
        for key in _parsed_data.keys():
            EcowittHttpParser._set(_parsed_data, key, response, key, weeutil.weeutil.to_int)
        return _parsed_data

    @staticmethod
    def parse_get_cli_soilad(response):
        """get_cli_soilAd: soil moisture calibration for each channel, sorted
        by channel."""
        _parsed_data = []
        for sensor in response:
            try:
                dict(sensor)
            except (TypeError, ValueError):
                continue
            channel = EcowittHttpParser._channel(sensor, 'ch')
            if channel is None:
                continue
            _sensor_data = {'channel': channel}
            for k in ('id', 'name'):
                if k in sensor:
                    _sensor_data[k] = sensor[k]
            for k in ('soilVal', 'nowAd', 'minVal', 'maxVal'):
                EcowittHttpParser._set(_sensor_data, k, sensor, k, weeutil.weeutil.to_int)
            EcowittHttpParser._set(_sensor_data, 'checked', sensor, 'checked', weeutil.weeutil.to_bool)
            _parsed_data.append(_sensor_data)
        return sorted(_parsed_data, key=itemgetter('channel'))

    def _cli_channels(self, response, fields, device_units):
        """Offsets of a multi-channel sensor: for each channel with all of the
        fields ((key, unit group)), ValueTuples (None if unreadable), sorted by
        channel."""
        _parsed_data = []
        for sensor in response:
            try:
                dict(sensor)
            except (TypeError, ValueError):
                continue
            channel = self._channel(sensor)
            if channel is None:
                continue
            _sensor = {'channel': channel}
            for k in ('id', 'name'):
                if k in sensor:
                    _sensor[k] = sensor[k]
            for key, group in fields:
                _sensor[key] = self._obs(sensor, key, group, 'vt', device_units)
                if _sensor[key] is MISSING:
                    break
            else:
                _parsed_data.append(_sensor)
        return sorted(_parsed_data, key=itemgetter('channel'))

    def parse_get_cli_multich(self, response, device_units=None):
        """get_cli_multiCh: WN31 temperature and humidity offsets."""
        return self._cli_channels(response, (('temp', 'group_deltat'), ('humi', 'group_percent')), device_units)

    def parse_get_cli_pm25(self, response, device_units=None):
        """get_cli_pm25: WH41/WH43 PM2.5 offsets."""
        return self._cli_channels(response, (('val', 'group_concentration'),), device_units)

    def parse_get_cli_co2(self, response, device_units=None):
        """get_cli_co2: WH45/WH46 offsets (ValueTuples, None if unreadable)."""
        EcowittHttpParser._as_dict(response, 'parse_get_cli_co2')
        _parsed_data = dict()
        for k, group in (('pm1', 'group_concentration'), ('pm25', 'group_concentration'),
                         ('pm4', 'group_concentration'), ('pm10', 'group_concentration'), ('co2', 'group_fraction')):
            value = self._obs(response, k, group, 'vt', device_units)
            if value is not MISSING:
                _parsed_data[k] = value
        for k in ('id', 'name'):
            if k in response:
                _parsed_data[k] = response[k]
        return _parsed_data

    @staticmethod
    def parse_get_piezo_rain(response, device_unit_data):
        """get_piezo_rain: the piezo gauge's totals (ValueTuples in the device's
        rain unit) and its five gains ('gain1' ...)."""
        EcowittHttpParser._as_dict(response, 'get_piezo_rain')
        _parsed_data = dict()
        for dest, src in EcowittHttpParser.piezo_rain_map.items():
            EcowittHttpParser._set(_parsed_data, dest, response, src,
                                   lambda v: weewx.units.ValueTuple(weeutil.weeutil.to_float(v),
                                                                    device_unit_data['group_rain'], 'group_rain'),
                                   error=weewx.units.ValueTuple(None, None, None))
        for n in range(1, 6):
            EcowittHttpParser._set(_parsed_data, f'gain{n:d}', response, f'rain{n:d}_gain',
                                   weeutil.weeutil.to_float)
        return _parsed_data

    def parse_get_cli_wh34(self, response, device_units=None):
        """get_cli_wh34: WN34 temperature offsets, sorted by channel."""
        if device_units is None or 'group_temperature' not in device_units.keys():
            raise ParseError('No WN34 or device temperature unit information.')
        _parsed_data = []
        for sensor in response:
            channel = self._channel(sensor)
            if channel is None:
                continue
            temp = self._obs(sensor, 'temp', 'group_deltat', 'vt', device_units)
            if temp is MISSING:
                continue
            _parsed_data.append({'channel': channel, 'temp': temp, 'id': sensor.get('id'),
                                 'name': sensor.get('name')})
        return sorted(_parsed_data, key=itemgetter('channel'))

    def parse_get_cli_lds(self, response):
        """get_cli_lds: WH54 offsets and heights (ValueTuples), heating and
        level, sorted by channel."""
        _parsed_data = []
        for sensor in response:
            channel = self._channel(sensor, 'ch')
            if channel is None:
                continue
            _sensor = {'channel': channel}
            for k in ('offset', 'total_height'):
                value = self._obs(sensor, k, 'group_depth', 'vt')
                if value is not MISSING:
                    _sensor[k] = weewx.units.ValueTuple(None, None, None) if value is None else value
            for k in ('total_heat', 'level'):
                self._set(_sensor, k, sensor, k, int, errors=(TypeError, ValueError))
            if not set(_sensor.keys()) & {'offset', 'total_height', 'total_heat'}:
                continue
            _sensor['id'] = sensor.get('id')
            _sensor['name'] = sensor.get('name')
            _parsed_data.append(_sensor)
        return sorted(_parsed_data, key=itemgetter('channel'))

    @staticmethod
    def parse_get_sdmmc_info(response):
        """get_sdmmc_info: the SD card's details, with the logging interval
        (info: interval) as an int."""
        try:
            _parsed_data = dict(response)
            _parsed_data['info']['interval'] = int(response['info']['Interval'])
        except (KeyError, TypeError, ValueError) as e:
            raise ParseError(f"Error parsing 'get_sdmmc_info' data: {e}")
        return _parsed_data

    # -- get_livedata_info arrays ---------------------------------------------

    def process_common_list_array(self, response):
        """common_list (also rain, piezoRain): entries {'id': '0x02', 'val':
        '12.3', 'unit': 'C'}, each processed by the processor_fns method for
        its id. Returns {id: {'id':, 'val':, 'voltage': (if any)}}."""
        result = dict()
        for item in response:
            if 'id' not in item:
                continue
            try:
                processor_fn = self.processor_fns[item['id']]
            except KeyError:
                if weewx.debug or self.debug.parser or self.log_unknown_fields:
                    log.info("Skipped unknown livedata observation ID '%s'", item['id'])
                continue
            try:
                result[item['id']] = getattr(self, processor_fn)(item)
            except ProcessorError as e:
                if weewx.debug >= 2 or self.debug.parser:
                    log.info("Error processing common_list ID '%s': %s", item['id'], e)
                result[item['id']] = {'val': None}
        return result

    process_piezoRain_array = process_common_list_array
    process_rain_array = process_common_list_array

    def _channels(self, response, readings, required=None, voltage=False, name=True):
        """A multi-channel array: for each sensor with a channel, its readings
        ((key, unit group, how)), its name and battery voltage. A sensor is left
        out if it has none of the readings, or without a required one."""
        result = []
        for sensor in response:
            channel = self._channel(sensor)
            if channel is None:
                continue
            _sensor = {'channel': channel}
            for key, group, how in readings:
                if self._add(_sensor, sensor, key, group, how) is MISSING and key == required:
                    break
            else:
                if not set(_sensor.keys()) & {key for key, _, _ in readings}:
                    continue
                if name:
                    _sensor['name'] = sensor.get('name')
                if voltage:
                    self._add(_sensor, sensor, 'voltage', 'group_volt', 'raw')
                result.append(_sensor)
        return result

    def process_ch_aisle_array(self, response):
        """ch_aisle (WN31): [{'channel': '1', 'name': '', 'battery': '0',
        'temp': '31.7', 'unit': 'C', 'humidity': '53%'}, ...]."""
        return self._channels(response, (('temp', 'group_temperature', 'convert'),
                                         ('humidity', 'group_percent', 'int')))

    def process_ch_temp_array(self, response):
        """ch_temp (WN34): [{'channel': '1', 'name': 'Soil', 'temp': '23.0',
        'unit': 'C', 'battery': '3', 'voltage': '1.54'}, ...]."""
        return self._channels(response, (('temp', 'group_temperature', 'convert'),), required='temp',
                              voltage=True)

    def process_ch_lds_array(self, response):
        """ch_lds (WH54): [{'channel': '1', 'name': '', 'unit': 'mm', 'battery':
        '5', 'voltage': '3.22', 'air': '3735 mm', 'depth': '--.-'}, ...]."""
        return self._channels(response, (('air', 'group_depth', 'convert'), ('depth', 'group_depth', 'convert')),
                              voltage=True)

    def process_ch_soil_array(self, response):
        """ch_soil (WH51): [{'channel': '1', 'name': 'Trough', 'battery': '5',
        'voltage': '1.60', 'humidity': '23%'}, ...]."""
        return self._channels(response, (('humidity', 'group_percent', 'int'),), required='humidity',
                              voltage=True)

    @staticmethod
    def process_ch_pm25_array(response):
        """ch_pm25 (WH41/WH43): [{'channel': '1', 'PM25': '7.0',
        'PM25_RealAQI': '29', 'PM25_24HAQI': '28', 'PM25_24H': '6.1',
        'battery': '6'}, ...]."""
        result = []
        for sensor in response:
            channel = EcowittHttpParser._channel(sensor)
            if channel is None:
                continue
            _sensor = {'channel': channel}
            for key, fn in (('PM25', float), ('PM25_RealAQI', int), ('PM25_24HAQI', int), ('PM25_24H', float)):
                EcowittHttpParser._set(_sensor, key, sensor, key, fn, errors=(TypeError, ValueError))
            if len(_sensor) > 1:
                result.append(_sensor)
        return result

    @staticmethod
    def _first(response, name):
        """The one element of a single-sensor array."""
        try:
            return response[0]
        except (KeyError, TypeError) as e:
            raise ParseError(f"Cannot parse '{name}' array: {e}")

    def process_wh25_array(self, response):
        """wh25 (indoor sensor): [{'intemp': '30.6', 'unit': 'C', 'inhumi':
        '47%', 'abs': '1013.6 hPa', 'rel': '1019.1 hPa', 'CO2': '495',
        'CO2_24H': '538'}]."""
        item = self._first(response, 'wh25')
        _item = dict()
        for key, group, how in (('intemp', 'group_temperature', 'convert'), ('inhumi', 'group_percent', 'int'),
                                ('abs', 'group_pressure', 'convert'), ('rel', 'group_pressure', 'convert')):
            self._add(_item, item, key, group, how)
        for key in ('CO2', 'CO2_24H'):
            self._set(_item, key, item, key, int, errors=(TypeError, ValueError))
        return _item

    def process_lightning_array(self, response):
        """lightning (WH57): [{'distance': '14 km', 'date':
        '2024-03-21T20:45:37', 'timestamp': '03/21/2024 20:45:37', 'count':
        '0', 'battery': '4'}]. 'timestamp' becomes a timestamp."""
        item = self._first(response, 'lightning')
        _item = dict()
        self._set(_item, 'count', item, 'count', int, errors=(TypeError, ValueError))
        try:
            dist_vt = self.parse_obs_value(key='distance', json_object=item, unit_group='group_distance')
        except KeyError:
            pass
        except UnitError as e:
            log.error('process_lightning_array: Error processing distance unit: %s', e)
            _item['distance'] = None
        except ParseError as e:
            log.error('process_lightning_array: Error processing distance: %s', e)
            _item['distance'] = None
        else:
            _item['distance'] = weewx.units.convert(
                dist_vt, weewx.units.std_groups[self.unit_system]['group_distance']).value
        if 'date' in item:
            _item['date'] = item['date']
        try:
            _timestamp_dt = datetime.datetime.strptime(item['timestamp'], '%m/%d/%Y %H:%M:%S')
        except KeyError:
            pass
        except (TypeError, ValueError):
            _item['timestamp'] = None
        else:
            _item['timestamp'] = int(time.mktime(_timestamp_dt.timetuple()))
        return _item

    def process_co2_array(self, response):
        """co2 (WH45/WH46): [{'temp': '81.0', 'unit': 'F', 'humidity': '60%',
        'PM25': '15.0', 'PM25_RealAQI': '57', 'PM25_24HAQI': '57', 'PM10':
        '15.1', ..., 'CO2': '314', 'CO2_24H': '314', 'battery': '6'}]. Fields
        starting PM or CO2 become floats. A single sensor's dict is returned
        on its own."""
        result = []
        for item in response:
            _item = dict(item)
            if 'temp' in _item:
                try:
                    src_unit = self.get_weewx_unit(item['unit'], 'group_temperature')
                except UnitError as e:
                    log.error('process_co2_array: Error processing temperature unit: %s', e)
                    _item['temp'] = None
                else:
                    try:
                        temp_vt = weewx.units.ValueTuple(float(item['temp']), src_unit, 'group_temperature')
                    except (TypeError, ValueError):
                        _item['temp'] = None
                    else:
                        _item['temp'] = weewx.units.convert(
                            temp_vt, weewx.units.std_groups[self.unit_system]['group_temperature']).value
            if 'humidity' in item:
                self._set(_item, 'humidity', _item, 'humidity', lambda v: float(v.split('%')[0]),
                          errors=(TypeError, ValueError))
            for k, v in item.items():
                if k.startswith(('PM', 'CO2')):
                    self._set(_item, k, item, k, float, errors=(TypeError, ValueError))
            result.append(_item)
        return result[0] if len(result) == 1 else result

    @staticmethod
    def process_leaf_array(response):
        """leaf (WN35): [{'channel': '1', 'name': '', 'battery': '5',
        'humidity': '12%'}, ...], with humidity as a float."""
        result = []
        for item in response:
            _item = dict(item)
            if 'humidity' in item:
                EcowittHttpParser._set(_item, 'humidity', _item, 'humidity', lambda v: float(v.split('%')[0]),
                                       errors=(TypeError, ValueError))
            result.append(_item)
        return result

    def process_ch_leak_array(self, response):
        """ch_leak (WH55): [{'channel': '1', 'name': '', 'battery': '4',
        'status': 'Normal'}, ...], status 0 (normal) or 1 (leaking)."""
        result = []
        for item in response:
            _item = dict(item)
            if 'status' in item:
                _item['status'] = {'normal': 0, 'leaking': 1}.get(str(item['status']).lower())
            self._set(_item, 'battery', item, 'battery', int, errors=(TypeError, ValueError))
            try:
                self._add(_item, item, 'voltage', 'group_volt', 'raw')
            except UnitError:
                _item['voltage'] = None
            result.append(_item)
        return result

    @staticmethod
    def process_debug_array(response):
        """debug: [{'heap': '114512', 'runtime': '1009602', 'usr_interval':
        '30', 'is_cnip': false}]."""
        item = EcowittHttpParser._first(response, 'debug')
        _item = dict()
        for key in ('heap', 'runtime', 'usr_interval'):
            EcowittHttpParser._set(_item, key, item, key, int, errors=(TypeError, ValueError))
        EcowittHttpParser._set(_item, 'is_cnip', item, 'is_cnip', weeutil.weeutil.to_bool)
        return _item

    # -- get_sensors_info ------------------------------------------------------

    def process_sensor_array(self, sensor, connected_only):
        """One get_sensors_info entry: (model, channel ('ch1' ...) or None,
        {'address', 'id', 'battery', 'signal', 'enabled', 'version'}).
        (None, None, None) for a sensor not registered, if connected_only.
        Battery is None for a sensor with no signal, unless show_battery."""
        if connected_only and sensor.get('id').lower() in self.not_registered:
            return None, None, None
        data = {}
        try:
            data['address'] = int(sensor.get('type'))
        except (TypeError, ValueError):
            data['address'] = None
        data['id'] = sensor.get('id')
        try:
            if not self.show_battery and int(sensor.get('signal')) == 0:
                data['battery'] = None
            else:
                data['battery'] = int(sensor.get('batt'))
        except (TypeError, ValueError):
            data['battery'] = None
        try:
            data['signal'] = int(sensor.get('signal'))
        except (TypeError, ValueError):
            data['signal'] = None
        try:
            data['enabled'] = int(sensor.get('idst')) == 1
        except (TypeError, ValueError):
            data['enabled'] = None
        if 'version' in sensor.keys():
            data['version'] = sensor.get('version')
        channel = None
        if sensor.get('name') is not None:
            _match = re.search(r'CH\d+', sensor.get('name'))
            if _match is not None:
                channel = _match.group(0).lower()
        return sensor.get('img'), channel, data

    def get_model_from_firmware(self, firmware_string):
        """The device model in a firmware string, or None."""
        return self.get_model(firmware_string) if firmware_string is not None else None

    @staticmethod
    def get_model(t):
        """The first of KNOWN_DEVICES in string t, or None."""
        if t is not None:
            for model in KNOWN_DEVICES:
                if model in t.upper():
                    return model
        return None

    # -- common_list entries ---------------------------------------------------

    def _process_object(self, item, unit_group, how='convert'):
        """A common_list entry: {'id':, 'val': its reading ('how' as for
        _obs()), 'voltage': the battery voltage, if any}. ProcessorError if
        there is no id, no value or no unit for it."""
        try:
            _item = {'id': item['id']}
            vt = self.parse_obs_value('val', item, unit_group)
        except (KeyError, UnitError) as e:
            raise ProcessorError(e) from e
        except ParseError:
            _item['val'] = None
        else:
            _item['val'] = self._value(vt, how)
        self._add(_item, item, 'voltage', 'group_volt', 'raw')
        return _item

    def process_temperature_object(self, item):
        return self._process_object(item, 'group_temperature')

    def process_pressure_object(self, item):
        return self._process_object(item, 'group_pressure')

    def process_speed_object(self, item):
        return self._process_object(item, 'group_speed')

    def process_direction_object(self, item):
        return self._process_object(item, 'group_direction', 'int')

    def process_humidity_object(self, item):
        return self._process_object(item, 'group_percent', 'int')

    def process_boolean_object(self, item):
        return self._process_object(item, 'group_boolean', 'int')

    def process_light_object(self, item):
        return self._process_object(item, 'group_illuminance')

    def process_rainfall_object(self, item):
        return self._process_object(item, 'group_rain')

    def process_rainrate_object(self, item):
        return self._process_object(item, 'group_rainrate')

    process_uv_radiation_object = process_humidity_object
    process_index_object = process_direction_object

    @staticmethod
    def process_noop_object(item):
        """A common_list entry that is ignored."""
        return None

    # -- values and units ------------------------------------------------------

    def parse_obs_value(self, key, json_object, unit_group, device_units=None):
        """json_object[key], a number with or without its unit ('12.3 km/h'),
        as a ValueTuple. Without a unit in the value, the unit comes from the
        object's 'unit' field or, failing that, device_units (default
        default_device_units) for unit_group.

        Raises KeyError if there is no such key, ParseError if the value or
        its unit can't be read, UnitError if device_units has no unit for
        unit_group."""
        if device_units is None:
            device_units = self.default_device_units
        try:
            _value, _unit = re.match('([0-9.,+-]+)(.*)', json_object[key]).group(1, 2)
            _unit = _unit.strip().lower()
        except AttributeError:
            raise ParseError(f"Could not determine value and unit for '{key}' in JSON object '{json_object}'")
        try:
            _numeric = float(_value)
        except (TypeError, ValueError):
            raise ParseError("Could not convert '%s' to a float" % _value)
        if len(_unit) > 0:
            try:
                weewx_unit = self.get_weewx_unit(_unit, unit_group=unit_group)
            except UnitError as e:
                log.error("parse_obs_value: Could not determine unit applicable to field '%s: %s': %s",
                          key, _numeric, e)
                raise ParseError(f"Could not equate Ecowitt unit '{_unit}' with a WeeWX unit")
        elif 'unit' in json_object.keys():
            try:
                weewx_unit = self.get_weewx_unit(json_object['unit'], unit_group=unit_group)
            except UnitError:
                _msg = f"unknown Ecowitt unit string '{json_object['unit']}' in field 'unit'"
                log.error("parse_obs_value: Could not determine unit applicable to field '%s: %s': %s",
                          key, _numeric, _msg)
                raise ParseError(f"Could not equate Ecowitt unit '{json_object['unit']}' with a WeeWX unit")
        else:
            try:
                weewx_unit = device_units[unit_group]
            except KeyError:
                raise UnitError(f"Could not determine device units for '{unit_group}'")
        return weewx.units.ValueTuple(_numeric, weewx_unit, unit_group)

    @staticmethod
    def get_weewx_unit(unit_string, unit_group=None):
        """The WeeWX unit for an Ecowitt unit string ('2' units for
        group_depth and group_deltat). UnitError if unknown."""
        try:
            _unit = EcowittHttpParser.unit_lookup[unit_string.lower()]
        except (AttributeError, KeyError):
            raise UnitError("unknown Ecowitt unit string: '%s'" % unit_string)
        if unit_group is not None and unit_group.lower() in ('group_depth', 'group_deltat'):
            _unit = ''.join([_unit, '2'])
        return _unit


# ============================================================================
#                            class EcowittSensors
# ============================================================================

class EcowittSensors:
    """Class for processing Ecowitt sensor metadata.

    The Ecowitt local HTTP API provides various sensor metadata information via
    multiple API calls. Class EcowittSensors processes this disparate
    information and makes formatted sensor metadata available via a number of
    properties.

    To ensure up-to-data data an EcowittSensors object should be updated with
    current get_sensor_info and get_livedata_info before accessing the
    EcowittSensors object properties.
    """

    # tuple of lowercase sensor models for which Ecowitt has provided no low
    # battery state definition
    no_low = ('ws80', 'ws85', 'ws90')
    # sensors whose battery state is determined from a binary value (0|1)
    # (the API calls the WS69/WH65 sensor array 'wh69')
    batt_binary = ('wh65', 'wh69', 'wh25', 'wh26', 'wn31', 'wn32')
    # sensors whose battery state is determined from an integer value
    batt_int = ('wh40', 'wh41', 'wh43', 'wh45', 'wh55', 'wh57')
    # sensors whose battery state is determined from a battery voltage value
    batt_volt = ('wh68', 'wh51', 'wh54', 'wn34', 'wn35', 'ws80', 'ws85', 'ws90')
    # map of 'dotted' get_livedata_info sensor voltage fields to sensor address
    sensor_with_voltage = {
        'piezoRain.0x13.voltage': 48,
        'ch_soil.1.voltage': 14,
        'ch_soil.2.voltage': 15,
        'ch_soil.3.voltage': 16,
        'ch_soil.4.voltage': 17,
        'ch_soil.5.voltage': 18,
        'ch_soil.6.voltage': 19,
        'ch_soil.7.voltage': 20,
        'ch_soil.8.voltage': 21,
        'ch_soil.9.voltage': 58,
        'ch_soil.10.voltage': 59,
        'ch_soil.11.voltage': 60,
        'ch_soil.12.voltage': 61,
        'ch_soil.13.voltage': 62,
        'ch_soil.14.voltage': 63,
        'ch_soil.15.voltage': 64,
        'ch_soil.16.voltage': 65,
        'ch_temp.1.voltage': 31,
        'ch_temp.2.voltage': 32,
        'ch_temp.3.voltage': 33,
        'ch_temp.4.voltage': 34,
        'ch_temp.5.voltage': 35,
        'ch_temp.6.voltage': 36,
        'ch_temp.7.voltage': 37,
        'ch_temp.8.voltage': 38,
        'ch_lds.1.voltage': 66,
        'ch_lds.2.voltage': 67,
        'ch_lds.3.voltage': 68,
        'ch_lds.4.voltage': 69
    }
    # map of sensor address to composite sensor name (ie sensor model and
    # channel (as applicable))
    sensor_address = {
        0: 'ws69',
        1: 'wh68',
        2: 'ws80',
        3: 'wh40',
        4: 'wh25',
        5: 'wh26',
        6: 'wn31_ch1',
        7: 'wn31_ch2',
        8: 'wn31_ch3',
        9: 'wn31_ch4',
        10: 'wn31_ch5',
        11: 'wn31_ch6',
        12: 'wn31_ch7',
        13: 'wn31_ch8',
        14: 'wh51_ch1',
        15: 'wh51_ch2',
        16: 'wh51_ch3',
        17: 'wh51_ch4',
        18: 'wh51_ch5',
        19: 'wh51_ch6',
        20: 'wh51_ch7',
        21: 'wh51_ch8',
        22: 'wh41_ch1',
        23: 'wh41_ch2',
        24: 'wh41_ch3',
        25: 'wh41_ch4',
        26: 'wh57',
        27: 'wh55_ch1',
        28: 'wh55_ch2',
        29: 'wh55_ch3',
        30: 'wh55_ch4',
        31: 'wn34_ch1',
        32: 'wn34_ch2',
        33: 'wn34_ch3',
        34: 'wn34_ch4',
        35: 'wn34_ch5',
        36: 'wn34_ch6',
        37: 'wn34_ch7',
        38: 'wn34_ch8',
        39: 'wh45',
        40: 'wn35_ch1',
        41: 'wn35_ch2',
        42: 'wn35_ch3',
        43: 'wn35_ch4',
        44: 'wn35_ch5',
        45: 'wn35_ch6',
        46: 'wn35_ch7',
        47: 'wn35_ch8',
        48: 'ws90',
        49: 'ws85',
        58: 'wh51_ch9',
        59: 'wh51_ch10',
        60: 'wh51_ch11',
        61: 'wh51_ch12',
        62: 'wh51_ch13',
        63: 'wh51_ch14',
        64: 'wh51_ch15',
        65: 'wh51_ch16',
        66: 'wh54_ch1',
        67: 'wh54_ch2',
        68: 'wh54_ch3',
        69: 'wh54_ch4',
    }

    def __init__(self, all_sensor_data=None, live_data=None):
        """Initialise an EcowittSensors object."""

        self.all_sensor_data = None
        self.update_sensor_data(all_sensor_data=all_sensor_data,
                                live_data=live_data)

    def update_sensor_data(self, all_sensor_data, live_data=None):
        """Update the stored sensor data with fresh API data."""

        self.all_sensor_data = all_sensor_data if all_sensor_data is not None else {}
        self.merge_live_data(live_data)

    def merge_live_data(self, live_data):
        """Update our sensor data with live data."""

        if live_data is not None:
            for source_field, sensor_address in self.sensor_with_voltage.items():
                # does the live data have a key that matches the source field
                if source_field in live_data.keys():
                    found = False
                    for model, data in self.all_sensor_data.items():
                        # do we have a channelised or non-channelised sensor,
                        # a non-channelised sensor will have an 'address' key
                        if 'address' in data.keys():
                            # we have a non-channelised sensor, check if the
                            # sensor address in our metadata matches the
                            # livedata sensor address
                            if data['address'] == sensor_address:
                                data['voltage'] = live_data[source_field]
                                # set our flag
                                found = True
                        else:
                            # we have a channelised sensor, iterate over all of
                            # the possible channels
                            for channel, channel_data in data.items():
                                if int(channel_data.get('address', 999)) == sensor_address:
                                    channel_data['voltage'] = live_data[source_field]
                                    # set our flag
                                    found = True
                                    # break out of the 'channel' loop
                                    break
                        if found:
                            break

    @property
    def data(self):
        """Return the current sensor metadata.

        Return a dict, keyed by lowercase sensor model, of my current sensor
        metadata. Available key/value pairs for each sensor includes:

        'name':     ???
        'id':       The sensor ID, uppercase string. 'FFFFFFFE' for
                    disconnected sensors, 'FFFFFFFF' for sensors that are
                    'learning'/connecting.
        'battery':  Sensor battery state, integer. Anecdotally 0-6 and 9.
                    Meaning depends on the sensor, 9 (appears to) indicate no
                    battery data is available.
        'signal':   Sensor signal state, integer. 0-4. Indicates number of the
                    last four sensor data that were successfully received.
        'voltage':  Sensor battery voltage, float. Not supported for all
                    sensors. Key is omitted for unsupported sensors.

        For channelised sensors the 'model' key value consists of a dict,
        keyed by channel number, containing data in the same format as above.
        """

        return self.all_sensor_data

    @property
    def all_models(self):
        """Returns a tuple of known sensor models.

        Includes all sensor models returned in provided get_sensor_info API
        response whether connected or not connected. Sensor model is short form
        only, eg. 'wh51', 'wn34' etc.
        """

        # our models are the keys in all_sensor_data, return a sorted tuple of
        # these keys
        return tuple(sorted(self.all_sensor_data.keys()))

    @property
    def all(self):
        """Returns a tuple of known sensors.

        Includes all sensors returned in provided get_sensor_info API response
        whether connected or not connected. Sensor data includes channel number
        where applicable, eg. 'wh51_ch1', 'wn34_ch3' etc.
        """

        all_sensors = []
        # iterate over each sensor_data key value pair, this will yield the
        # sensor model and either a dict sensor metadata or, for channelised
        # sensors, a dict keyed by channel of channel data
        for model, data in self.all_sensor_data.items():
            for ch, ch_data in data.items():
                if hasattr(ch_data, 'keys'):
                    # we have channelised sensor, add the sensor and channel
                    # number
                    all_sensors.append(f'{model}_{ch}')
                else:
                    # we have a non-channelised sensor
                    all_sensors.append(model)
                    break
        return tuple(sorted(all_sensors))

    @property
    def enabled(self):
        """Returns a tuple of enabled sensors.

        Includes all sensors returned in provided get_sensor_info API response
        that are enabled whether connected or not connected. Sensor data
        includes channel number where applicable, eg. 'wh51_ch1', 'wn34_ch3'
        etc.
        """

        enabled = []
        # iterate over each sensor_data key value pair, this will yield the
        # sensor model and either a dict sensor metadata or, for channelised
        # sensors, a dict keyed by channel of channel data
        for model, data in self.all_sensor_data.items():
            for ch, ch_data in data.items():
                if hasattr(ch_data, 'keys'):
                    # we have channelised sensor, now check if the sensor is
                    # enabled
                    if ch_data.get('enabled'):
                        # the sensor is connected, add it to our list
                        enabled.append(f'{model}_{ch}')
                else:
                    # we have a non-channelised sensor
                    if data.get('enabled'):
                        # the sensor is connected, add it to our list
                        enabled.append(model)
                    break
        return tuple(sorted(enabled))

    @property
    def disabled(self):
        """Returns a tuple of disabled sensors.

        Includes all sensors returned in provided get_sensor_info API response
        that are disabled. Sensor data includes channel number where
        applicable, eg. 'wh51_ch1', 'wn34_ch3' etc.
        """

        disabled = []
        # iterate over each sensor_data key value pair, this will yield the
        # sensor model and either a dict sensor metadata or, for channelised
        # sensors, a dict keyed by channel of channel data
        for model, data in self.all_sensor_data.items():
            for ch, ch_data in data.items():
                if hasattr(ch_data, 'keys'):
                    # we have channelised sensor, now check if the sensor is
                    # disabled
                    if not ch_data.get('enabled'):
                        # the sensor is disabled, add it to our list
                        disabled.append(f'{model}_{ch}')
                else:
                    # we have a non-channelised sensor
                    if not data.get('enabled'):
                        # the sensor is disabled, add it to our list
                        disabled.append(model)
                    break
        return tuple(sorted(disabled))

    @property
    def learning(self):
        """Returns a tuple of sensors that are 'learning'.

        Includes all sensors returned in provided get_sensor_info API response
        that are 'learning'. Sensor data includes channel number where
        applicable, eg. 'wh51_ch1', 'wn34_ch3' etc.
        """

        learning = []
        # iterate over each sensor_data key value pair, this will yield the
        # sensor model and either a dict sensor metadata or, for channelised
        # sensors, a dict keyed by channel of channel data
        for model, data in self.all_sensor_data.items():
            for ch, ch_data in data.items():
                if hasattr(ch_data, 'keys'):
                    # we have channelised sensor, now check if the sensor is connected
                    if ch_data.get('id') == 'FFFFFFFF':
                        # the sensor is learning, add it to our list
                        learning.append(f'{model}_{ch}')
                else:
                    # we have a non-channelised sensor
                    if data.get('id') == 'FFFFFFFF':
                        # the sensor is learning, add it to our list
                        learning.append(model)
                    break
        return tuple(sorted(learning))

    @property
    def connected(self):
        """Returns a tuple of connected sensors.

        Includes all sensors returned in provided get_sensor_info API response
        that are connected. Sensor data includes channel number where
        applicable, eg. 'wh51_ch1', 'wn34_ch3' etc.
        """

        connected = []
        # iterate over each sensor_data key value pair, this will yield the
        # sensor model and either a dict sensor metadata or, for channelised
        # sensors, a dict keyed by channel of channel data
        for model, data in self.all_sensor_data.items():
            for ch, ch_data in data.items():
                if hasattr(ch_data, 'keys'):
                    # we have channelised sensor, now check if the sensor is connected
                    if (ch_data.get('enabled') and
                            ch_data.get('id') != 'FFFFFFFE' and
                            ch_data.get('id') != 'FFFFFFFF'):
                        # the sensor is connected, add it to our list
                        connected.append(f'{model}_{ch}')
                else:
                    # we have a non-channelised sensor
                    if (data.get('enabled') and
                            data.get('id') != 'FFFFFFFE'
                            and data.get('id') != 'FFFFFFFF'):
                        # the sensor is connected, add it to our list
                        connected.append(model)
                    break
        return tuple(sorted(connected))

    def batt_state_desc(self, model, sensor_data):
        """Return the descriptive text for the battery state of a given sensor.

        Different sensor models report different battery state
        data, eg binary (0|1), integer or battery volts. Each of these
        different battery states is interpreted differently when determining
        whether the battery state is normal or low.

        This method determines the descriptive state for a sensor given the
        battery state value and returns descriptive text for the battery state.
        If a descriptive battery state cannot be determined (eg an unknown
        sensor or battery state data is invalid) the value None is returned.
        """

        try:
            if model in self.no_low:
                return "--"
            elif model in self.batt_binary:
                if sensor_data['battery'] == 0:
                    # battery is OK
                    return "OK"
                elif sensor_data['battery'] == 1:
                    # battery is low
                    return "low"
                else:
                    # we do not know how to interpret binary battery state data
                    # that is not 0 or 1, we have battery state data from a
                    # known sensor so return 'unknown'
                    return '--'
            elif model in self.batt_int:
                if sensor_data['battery'] <= 1:
                    # 0 or 1 is considered low
                    return "low"
                elif sensor_data['battery'] == 6:
                    # 6 means we are on DC power
                    return "DC"
                elif sensor_data['battery'] <= 5:
                    # 2, 3, 4 or 5 is OK
                    return "OK"
                elif sensor_data['battery'] == 9:
                    # 9 sometimes appears for sensors that do not exist but
                    # are included in API responses, we likely will not see
                    # any but if we do return 'unknown'
                    return '--'
                else:
                    # we do not know how to interpret integer battery state data
                    # that is not 0 to 6 or 9, we have battery state data from a
                    # known sensor so return 'unknown'
                    return '--'
            elif model in self.batt_volt:
                if 'voltage' in sensor_data.keys():
                    # 1.2V or less is considered low
                    if sensor_data['voltage'] <= 1.2:
                        return "low"
                    # greater than 1.2V is considered OK
                    else:
                        return "OK"
                elif sensor_data['battery'] <= 1:
                    # 0 or 1 is considered low
                    return "low"
                elif sensor_data['battery'] <= 5:
                    # 2, 3, 4 or 5 is OK
                    return "OK"
                else:
                    # we do not know how to interpret integer battery state data
                    # that is not 0 to 6 or 9, we have battery state data from a
                    # known sensor so return 'unknown'
                    return '--'
            else:
                return "Unknown sensor"
        except KeyError as e:
            raise


# ============================================================================
#                             class EcowittDevice
# ============================================================================

class EcowittDevice:
    """Class to obtain and parse data from an Ecowitt device.

    Some Ecowitt consoles (including gateway devices) can be interrogated
    directly via one or both of the following APIs:
    1. the legacy Ecowitt LAN/Wi-Fi Gateway API
    2. the Ecowitt local HTTP API

    The Ecowitt HTTP driver predominantly uses the Ecowitt local HTTP API to
    obtain observation and metadata from the Ecowitt device. The Ecowitt HTTP
    driver also has the ability to use limited portions of the legacy Ecowitt
    LAN/Wi-Fi Gateway API with compatible gateway devices to obtain additional
    metadata not available via the local HTTP API.

    These APIs use a library of commands for reading and setting various
    parameters in the device. The WeeWX Ecowitt HTTP driver uses a small subset
    of these commands to obtain sensor data which is presented to WeeWX via
    loop packets emitted by the driver.

    The local HTTP API uses HTTP GET requests and involves the decoding/parsing
    of JSON format message data. The legacy Ecowitt LAN/Wi-Fi Gateway API uses
    socket based requests and involves exchange of data that must be
    encoded/decoded at the byte/bit level.

    An EcowittDevice object uses the following classes for interacting with the
    device:

    - class EcowittHttpApi. Communicates directly with the device via the
                            device local HTTP API and obtains and validates
                            device responses.
    - class EcowittHttpParser. Parses local HTTP API responses from Ecowitt
                               devices and returns data suitable for passing to
                               a WeeWX driver/service.
    """

    # list of dicts of weather services that I know about
    services = [{'name': 'ecowitt_net_params',
                 'long_name': 'Ecowitt.net'
                 },
                {'name': 'wunderground_params',
                 'long_name': 'Wunderground'
                 },
                {'name': 'weathercloud_params',
                 'long_name': 'Weathercloud'
                 },
                {'name': 'wow_params',
                 'long_name': 'Weather Observations Website'
                 },
                {'name': 'custom_params',
                 'long_name': 'Customized'
                 }
                ]
    # lookup for the type of battery state data provided for each sensor type
    sensor_battery_type = {'wh25': 'binary', 'wh26': 'binary',
                           'wn31': 'binary', 'wn34': 'integer',
                           'wn35': 'integer', 'wh40': 'integer',
                           'wh41': 'integer', 'wh45': 'integer',
                           'wh51': 'integer', 'wh55': 'integer',
                           'wh57': 'integer', 'wh65': 'binary', 'wh69': 'binary',
                           'wh68': 'integer', 'ws80': 'integer',
                           'ws85': 'integer', 'ws90': 'integer'}
    # Ecowitt device units to WeeWX unit group/name lookup
    unit_code_to_string = {'temperature': {'group': 'group_temperature',
                                           'unit': {0: 'degree_C',
                                                    1: 'degree_F'}
                                           },
                           'pressure': {'group': 'group_pressure',
                                        'unit': {0: 'hPa',
                                                 1: 'inHg',
                                                 2: 'mmHg'}
                                        },
                           'wind': {'group': 'group_speed',
                                    'unit': {0: 'meter_per_second',
                                             1: 'km_per_hour',
                                             2: 'mile_per_hour',
                                             3: 'knot'}
                                    },
                           'rain': {'group': 'group_rain',
                                    'unit': {0: 'mm',
                                             1: 'inch'}
                                    },
                           'light': {'group': 'group_illuminance',
                                     'unit': {0: 'klux',
                                              1: 'watt_per_meter_squared',
                                              2: 'kfc'}
                                     },
                           'deltat': {'group': 'group_deltat',
                                      'unit': {0: 'degree_C2',
                                               1: 'degree_F2'}
                                      },
                           'rain_rate': {'group': 'group_rainrate',
                                         'unit': {0: 'mm_per_hour',
                                                  1: 'inch_per_hour'}
                                         },
                           'depth': {'group': 'group_depth',
                                     'unit': {0: 'mm2',
                                              1: 'foot2'}
                                    },
                           'altitude': {'group': 'group_altitude',
                                        'unit': {0: 'meter',
                                                 1: 'foot'}
                                       }
                           }
    # tuple of lowercase sensor models known to have user updatable firmware
    sensors_with_firmware = ('ws80', 'ws85', 'ws90')

    def __init__(self, ip_address,
                 unit_system=DEFAULT_UNIT_SYSTEM,
                 max_tries=DEFAULT_MAX_TRIES,
                 retry_wait=DEFAULT_RETRY_WAIT,
                 url_timeout=DEFAULT_URL_TIMEOUT,
                 show_battery=DEFAULT_FILTER_BATTERY,
                 log_unknown_fields=False,
                 debug=DebugOptions()):
        """Initialise an EcowittDevice object."""

        # dow we have a non-None IP address, we cannot continue unless we have
        # an IP address
        if ip_address is None:
            raise weewx.ViolatedPrecondition('device IP address cannot be None')
        self.api = EcowittHttpApi(ip_address=ip_address,
                                  max_tries=max_tries,
                                  retry_wait=retry_wait,
                                  timeout=url_timeout)
        self.parser = EcowittHttpParser(unit_system=unit_system,
                                        show_battery=show_battery,
                                        log_unknown_fields=log_unknown_fields,
                                        debug=debug)
        # get an EcowittSensors object to handle the specialised processing of
        # sensor metadata
        self.sensors = EcowittSensors()
        self.log_failures = True

    @property
    def log_failures(self):
        """Whether failed requests are logged as errors (the API object's setting)."""

        return self.api.log_failures

    @log_failures.setter
    def log_failures(self, value):
        self.api.log_failures = value

    def get_live_data(self, flatten_data=True):
        """Return live sensor observation data.

        Obtain live sensor data via the API. The live sensor data is parsed and
        optionally flattened. Returns a dict containing the parsed data.

        Raises a DeviceIOError exception if the device could not be contacted.
        Raises a ParseError exception if there was no sensor metadata to parse.
        """

        live_data = self.api.get_livedata_info()
        return self.parser.parse_get_livedata_info(live_data,
                                                   flatten_data=flatten_data)

    def get_sensors_data(self, connected_only=DEFAULT_ONLY_REGISTERED_SENSORS, flatten_data=True):
        """Return sensor metadata.

        Obtain live sensor metadata via the API. The sensor metadata is parsed
        and optionally flattened. Returns a dict containing the parsed data.

        Raises a DeviceIOError exception if the device could not be contacted.
        Raises a ParseError exception if there was no sensor metadata to parse.
        """

        sensors_data = self.api.get_sensors_info()
        return self.parser.parse_get_sensors_info(sensors_data,
                                                  connected_only=connected_only,
                                                  flatten_data=flatten_data)

    def get_rain_totals(self):
        """Return traditional rainfall aggregate and setting data.

        Obtain traditional rainfall aggregate, gain and reset data via the API.
        The data is parsed and returned as a dict.

        Raises a DeviceIOError exception if the device could not be contacted.
        Raises a ParseError exception if the raw device response is not a dict.
        """

        rain_data = self.api.get_rain_totals()
        _unit_data = self.get_device_units()
        return self.parser.parse_get_rain_totals(rain_data,
                                                 device_units=_unit_data)

    def get_piezo_rain_data(self):
        """Return piezo rainfall aggregate and gain data.

        Obtain piezo rainfall aggregate and gain data via the API. The data is
        parsed and returned as a dict.

        Raises a DeviceIOError exception if the device could not be contacted.
        Raises a ParseError exception if the raw device response is not a dict.
        """

        rain_data = self.api.get_piezo_rain()
        _unit_data = self.get_device_units()
        return self.parser.parse_get_piezo_rain(rain_data, _unit_data)

    def get_wn34_offset_data(self):
        """Return offset data for connected WN34 sensors.

        Obtain WN34 offset data via the API. The data is
        parsed and returned as a list of dicts in ascending channel number
        order.

        Raises a DeviceIOError exception if the device could not be contacted.
        Raises a ParseError exception if device unit information is not
        available or if temperature data not included in the raw device
        response.
        """

        offset_data = self.api.get_cli_wh34()
        _unit_data = self.get_device_units()
        return self.parser.parse_get_cli_wh34(offset_data,
                                              device_units=_unit_data)

    def get_pm25_offset_data(self):
        """Return PM2.5 offset data for connected WH41/WH43 sensors..

        Obtain WH41/WH43 PM2.5 offset data via the API. The data is parsed and
        returned as a list of dicts in ascending channel number order.

        Raises a DeviceIOError exception if the device could not be contacted.
        Raises a ParseError exception if device unit information is not
        available or if temperature data not included in the raw device
        response.
        """

        offset_data = self.api.get_cli_pm25()
        _unit_data = self.get_device_units()
        return self.parser.parse_get_cli_pm25(offset_data,
                                              device_units=_unit_data)

    def get_co2_offset_data(self):
        """Return offset data for the WH45 sensor.

        Obtain WH45 offset data via the API. The data is parsed and
        returned as a dict.

        Raises a DeviceIOError exception if the device could not be contacted.
        Raises a ParseError exception if the raw device response is not a dict.
        """

        offset_data = self.api.get_cli_co2()
        _unit_data = self.get_device_units()
        return self.parser.parse_get_cli_co2(offset_data,
                                             device_units=_unit_data)

    def get_lds_offset_data(self):
        """Return offset data for the WH54 sensor.

        Obtain WH54 offset data via the API. The data is parsed and returned as
        a dict.

        Raises a DeviceIOError exception if the device could not be contacted.
        Raises a ParseError exception if the raw device response is not a dict.
        """

        offset_data = self.api.get_cli_lds()
        return self.parser.parse_get_cli_lds(offset_data)

    def get_calibration_data(self):
        """Get device calibration data."""

        cal_data = self.api.get_calibration_data()
        _unit_data = self.get_device_units()
        return self.parser.parse_get_calibration_data(cal_data,
                                                      device_units=_unit_data)

    def get_multich_calibration_data(self):
        """Get multi-channel temperature-humidity calibration data."""

        cal_data = self.api.get_cli_multi_ch()
        _unit_data = self.get_device_units()
        return self.parser.parse_get_cli_multich(cal_data,
                                                 device_units=_unit_data)

    def get_soil_calibration_data(self):
        """Get soil moisture calibration data."""

        cal_data = self.api.get_cli_soilad()
        return self.parser.parse_get_cli_soilad(cal_data)

    def get_device_info_data(self):
        """Get device info data."""

        device_info_data = self.api.get_device_info()
        return self.parser.parse_get_device_info(device_info_data)

    def get_ws_settings(self):
        """Get weather services settings."""

        ws_settings = self.api.get_ws_settings()
        return self.parser.parse_get_ws_settings(ws_settings)

    def get_sdmmc_info_data(self):
        """Get SD card info data."""

        sd_info = self.api.get_sdmmc_info()
        return self.parser.parse_get_sdmmc_info(sd_info)

    def get_device_units(self):
        """Obtain details of units used by device API calls.

        Unlike the so called Ecowitt telnet API where response units were
        fixed, the Ecowitt local HTTP API responses use units based on the
        device units settings set by the user. These unit settings are normally
        made via the WS View+ app or the local device web page.

        The majority of Ecowitt HTTP API responses identify units for variable
        unit data by including a unit string either appended to the value
        concerned (eg '16mm') or in another field, often named 'units'
        (eg 'units': 'mm'). However, at time of release at least one HTTP API
        response (response to 'get_cli_wh34') does not include any unit
        information meaning it is not possible to determine the units used for
        some data values. To properly parse such responses unit information is
        obtained via the HTTP API command 'get_units_info'.

        This method obtains the device units information via the
        'get_units_info' API command. The raw response is parsed and then
        translated via look-up table to provide a dict containing WeeWX unit
        names for each group of observation types. If a unit cannot be
        determined the value None is used. Not all WeeWX unit names are
        supported, the only WeeWX unit names supported are those that are also
        supported by the Ecowitt HTTP API.

        Returns a dict keyed as follows:

        temperature: WeeWX temperature unit being used, 'degree_C' or
                     'degree_F'. String, may be None.
        pressure:    WeeWX pressure unit being used, 'hPa', 'inHg' or 'mmHg'.
                     String, may be None.
        wind:        WeeWX speed unit being used, 'meter_per_second',
                     'km_per_hour', 'mile_per_hour' or 'knot'. String, may be
                     None.
        rain:        WeeWX rain unit being used, 'mm' or 'inch'. String, may be
                     None.
        light:       WeeWX light unit being used, 'lux',
                     'watt_per_meter_squared' or 'foot_candle'. String, may be
                     None.
        """

        _units_resp = self.api.get_units_info()
        _parsed_units = self.parser.parse_get_units_info(_units_resp)
        device_units = {'group_percent': 'percent',
                        'group_direction': 'degree_compass',
                        'group_uv': 'uv_index',
                        'group_fraction': 'ppm',
                        'group_concentration': 'microgram_per_meter_cubed'}
        for ecowitt_group, ecowitt_unit in _parsed_units.items():
            try:
                _lookup = self.unit_code_to_string[ecowitt_group]
            except KeyError as e:
                continue
            unit_group = _lookup['group']
            try:
                _unit = _lookup['unit'][ecowitt_unit]
            except KeyError:
                continue
            device_units[unit_group] = _unit
        # add in implied derived units
        # rain rate, depth and altitude derived from and implied by rain
        if 'group_rain' in device_units.keys():
            _lookup = self.unit_code_to_string['rain']
            device_units['group_rainrate'] = _lookup['unit'][_parsed_units['rain']]
            _lookup = self.unit_code_to_string['depth']
            device_units['group_depth'] = _lookup['unit'][_parsed_units['rain']]
            _lookup = self.unit_code_to_string['altitude']
            device_units['group_altitude'] = _lookup['unit'][_parsed_units['rain']]
        # delta temperature derived from and implied by temperature
        if 'group_temperature' in device_units.keys():
            _lookup = self.unit_code_to_string['deltat']
            device_units['group_deltat'] = _lookup['unit'][_parsed_units['temperature']]
        return device_units

    @property
    def ip_address(self):
        """The device IP address."""

        return self.api.ip_address

    @property
    def model(self):
        """Device model."""

        version_data = self.api.get_version()
        version = self.parser.parse_get_version(version_data).get('version')
        return self.parser.get_model_from_firmware(version)

    @property
    def mac_address(self):
        """Device MAC address."""

        network_data = self.api.get_network_info()
        return self.parser.parse_get_network_info(network_data).get('mac')

    @property
    def firmware_version(self):
        """Device firmware version."""

        version_data = self.api.get_version()
        return self.parser.parse_get_version(version_data).get('firmware_version')

    @property
    def firmware_update_avail(self):
        """Whether a device firmware update is available or not.

        Return True if a device firmware update is available, False if there is
        no available firmware update or None if firmware update availability
        cannot be determined.
        """

        # get firmware version info
        version = self.api.get_version()
        # do we have current firmware version info and availability of a new
        # firmware version ?
        if version is not None and 'newVersion' in version:
            # we can now determine with certainty whether there is a new
            # firmware update or not
            return version['newVersion'] == '1'
        # we cannot determine the availability of a firmware update so return
        # None
        return None

    @property
    def firmware_update_message(self):
        """The device firmware update message.

        Returns the 'curr_msg' field in the 'get_device_info' response in the
        device HTTP API. This field is usually used for firmware update release
        notes.

        Returns a string containing the 'curr_msg' field contents of the
        'get_device_info' response. Return None if the 'get_device_info'
        response could not be obtained or the 'curr_msg' field was not included
        in the 'get_device_info' response.
        """

        # get device info
        device_info = self.api.get_device_info()
        return device_info.get('curr_msg') if device_info is not None else None

    @property
    def sensor_firmware_versions(self):
        """Obtain sensor firmware versions.

        Return a dict, keyed by uppercase sensor version, of the installed
        firmware version for attached sensors that have a user updatable
        firmware. If the firmware version for an attached sensor with updatable
        firmware cannot be obtained, the string 'not available' is returned for
        that sensor. If no attached sensors have user updatable firmware an
        empty dict is returned.
        """

        # first obtain the sensors data
        sensors_data = self.get_sensors_data()
        fw_data = dict()
        for sensor in self.sensors_with_firmware:
            sensor_battery = sensors_data.get('.'.join([sensor, 'battery']))
            # if the sensor battery state is 9 the sensor does not exist (the
            # HTTP API returns placeholders for all sensors including those
            # that do not exist, those that don't exist have battery == 9)
            if sensor_battery is not None and sensor_battery != 9:
                # obtain the sensor firmware version and save it to our dict
                fw_data[sensor.upper()] = sensors_data.get('.'.join([sensor, 'version']),
                                                           'not available')
        return fw_data

    @property
    def paired_rain_gauges(self):
        """Obtain paired rain gauge types.

        Returns a tuple containing the types of paired rain gauges. An Ecowitt
        device may be paired with either a tipping rain gauge, a piezoelectric
        (piezo) rain gauge, both tipping and piezo rain gauges or no rain
        gauge. A rain gauge is considered paired if the gauge is included in a
        'get_sensors_info' response and the ID key/value is not
        disabled ('FFFFFFFE') or not 'is registering' ('FFFFFFFF').

        Known rain gauge models and their type are:

            WH40: tipping gauge
            WH69: tipping gauge
            WS85: piezo gauge
            WS90: piezo gauge

        Returns a tuple. Possible responses are:

            ()                  - no rain gauges are attached
            ('tipping',)        - a tipping gauge only is attached
            ('piezo',)          - a piezo gauge only is attached
            ('tipping', 'piezo) - both a tipping and piezo gauge is attached
        """

        result = set()
        sensor_info = self.get_sensors_data(connected_only=True,
                                            flatten_data=False)
        if 'wh69' in sensor_info.keys() or 'wh40' in sensor_info.keys():
            result.add('tipping')
        if 'ws85' in sensor_info.keys() or 'ws90' in sensor_info.keys():
            result.add('piezo')
        return tuple(result)


# ============================================================================
#                 GW1000 binary TCP API (no local HTTP API)
# ============================================================================

class UnsupportedFeatureError(DeviceIOError):
    """The device's API does not provide the requested information."""


def _tcp_live_items():
    """TCP API live data item id -> (byte length, kind, flattened field name)."""
    items = {0x01: (2, 'temp', 'wh25.intemp'), 0x06: (1, 'hum', 'wh25.inhumi'), 0x08: (2, 'press', 'wh25.abs'),
             0x09: (2, 'press', 'wh25.rel'), 0x07: (1, 'hum', 'common_list.0x07.val'),
             0x0A: (2, 'int', 'common_list.0x0A.val'), 0x0F: (2, 'gain', 'rain_gain'),
             0x15: (4, 'lux', 'common_list.0x15.val'), 0x16: (2, 'uv', 'common_list.0x16.val'),
             0x17: (1, 'int', 'common_list.0x17.val'), 0x18: (6, None, None), 0x4C: (16, None, None),
             0x60: (1, 'dist', 'lightning.distance'), 0x61: (4, 'utc', 'lightning.timestamp'),
             0x62: (4, 'int', 'lightning.count'), 0x6B: (24, 'wh46', None), 0x6C: (4, 'int', 'debug.heap'),
             0x70: (16, 'wh45', None), 0x71: (None, None, None), 0x7A: (1, 'int', 'rain_priority'),
             0x7B: (1, 'int', 'radcompensation'), 0x82: (2, None, None), 0x87: (20, 'gains', None),
             0x88: (3, 'reset', None)}
    for code in (0x02, 0x03, 0x04, 0x05):
        items[code] = (2, 'temp', f'common_list.0x{code:02X}.val')
    for code in (0x0B, 0x0C, 0x19):
        items[code] = (2, 'speed', f'common_list.0x{code:02X}.val')
    for array, codes in (('rain', (0x0D, 0x0E, 0x10, 0x11)), ('piezoRain', (0x81, 0x80))):
        for code, obs in zip(codes, ('0x0D', '0x0E', '0x10', '0x11')):
            items[code] = (2, 'rate' if obs == '0x0E' else 'rain', f'{array}.{obs}.val')
    for code, obs in ((0x12, '0x12'), (0x13, '0x13'), (0x14, '0x14')):
        items[code] = (4, 'rain', f'rain.{obs}.val')
    for code, obs in ((0x83, '0x10'), (0x84, '0x11'), (0x85, '0x12'), (0x86, '0x13')):
        items[code] = (4, 'rain', f'piezoRain.{obs}.val')
    for ch in range(1, 9):
        items[0x19 + ch] = (2, 'temp', f'ch_aisle.{ch}.temp')
        items[0x21 + ch] = (1, 'hum', f'ch_aisle.{ch}.humidity')
        items[0x62 + ch] = (3, 'wn34', ch)
        items[0x71 + ch] = (1, 'hum', f'ch_leaf.{ch}.humidity')
    for ch in range(1, 17):
        items[0x29 + 2 * ch] = (2, None, None)  # soil temperature (no current sensor)
        items[0x2A + 2 * ch] = (1, 'hum', f'ch_soil.{ch}.humidity')
    for ch in range(1, 5):
        items[0x2A if ch == 1 else 0x4F + ch] = (2, 'pm', f'ch_pm25.{ch}.PM25')
        items[0x4C + ch] = (2, 'pm', f'ch_pm25.{ch}.PM25_24H')
        items[0x57 + ch] = (1, 'int', f'ch_leak.{ch}.status')
    return items


def _tcp_sensor_addresses():
    """TCP API sensor index -> (model, channel or None); the same numbers as the HTTP API 'type'."""
    table = {0: ('wh69', None), 1: ('wh68', None), 2: ('ws80', None), 3: ('wh40', None), 4: ('wh25', None),
             5: ('wh26', None), 26: ('wh57', None), 39: ('wh45', None), 48: ('ws90', None)}
    # (model, address of the first channel, first channel, last channel)
    for model, address, first, last in (('wn31', 6, 1, 8), ('wh51', 14, 1, 8), ('wh41', 22, 1, 4),
                                        ('wh55', 27, 1, 4), ('wn34', 31, 1, 8), ('wn35', 40, 1, 8),
                                        ('wh51', 58, 9, 16)):
        for ch in range(first, last + 1):
            table[address + ch - first] = (model, f'ch{ch}')
    return table


class EcowittTcpApi:
    """Client for the binary Ecowitt TCP API (port 45000), used by the GW1000 and WH2650."""

    commands = {'CMD_READ_STATION_MAC': 0x26, 'CMD_GW1000_LIVEDATA': 0x27, 'CMD_READ_SSSS': 0x30,
                'CMD_READ_RAINDATA': 0x34, 'CMD_READ_SENSOR_ID_NEW': 0x3C, 'CMD_READ_FIRMWARE_VERSION': 0x50,
                'CMD_READ_RAIN': 0x57}
    long_size = (0x12, 0x27, 0x3C, 0x57, 0x58)

    def __init__(self, ip_address, port=DEFAULT_TCP_PORT, max_tries=DEFAULT_MAX_TRIES, retry_wait=DEFAULT_RETRY_WAIT,
                 timeout=DEFAULT_URL_TIMEOUT):
        self.ip_address = ip_address
        self.host = str(ip_address).split(':')[0]
        self.port = int(port or DEFAULT_TCP_PORT)
        self.max_tries = max_tries or DEFAULT_MAX_TRIES
        self.retry_wait = DEFAULT_RETRY_WAIT if retry_wait is None else retry_wait
        self.timeout = timeout or DEFAULT_URL_TIMEOUT

    @staticmethod
    def packet(cmd, payload=b''):
        body = bytes([cmd, 3 + len(payload)]) + payload
        return b'\xff\xff' + body + bytes([calc_checksum(body)])

    def request(self, cmd):
        """Send a command once and return the whole validated response packet."""
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(self.packet(cmd))
            size_len = 2 if cmd in self.long_size else 1
            resp = b''
            total = None
            while total is None or len(resp) < total:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp += chunk
                if total is None and len(resp) >= 3 + size_len:
                    total = 2 + int.from_bytes(resp[3:3 + size_len], 'big')
        if len(resp) < 4 + size_len or resp[:2] != b'\xff\xff' or resp[2] != cmd:
            raise InvalidApiResponseError(f'invalid response to command 0x{cmd:02X}: {bytes_to_hex(resp[:16])}')
        resp = resp[:total]
        if len(resp) != total or calc_checksum(resp[2:-1]) != resp[-1]:
            raise InvalidApiResponseError(f'incomplete response or bad checksum for command 0x{cmd:02X}')
        return resp

    def call(self, cmd):
        """Return the data part of the response to cmd, retrying; raises DeviceIOError on failure."""
        error = None
        for attempt in range(1, self.max_tries + 1):
            try:
                resp = self.request(cmd)
            except (OSError, InvalidApiResponseError) as e:
                error = e
                if weewx.debug >= 2:
                    log.debug('TCP API command 0x%02X failed on attempt %d of %d: %s', cmd, attempt, self.max_tries, e)
                if attempt < self.max_tries:
                    time.sleep(self.retry_wait)
                continue
            return resp[(5 if cmd in self.long_size else 4):-1]
        raise DeviceIOError(f'TCP API command 0x{cmd:02X} to {self.host}:{self.port} failed: {error}')


class EcowittTcpDevice:
    """An Ecowitt GW1000/WH2650 accessed via the binary TCP API.

    Returns data with the same field names and units as EcowittDevice, so the rest of the
    driver (field map, sensor map, rain and lightning handling) is shared.
    """

    tcp = True
    detected = True
    live_items = _tcp_live_items()
    # sensors whose battery byte is a voltage (volts per unit); the others report a flag or a 0-6 level
    battery_volts = {'wh68': 0.02, 'ws80': 0.02, 'ws90': 0.02, 'wn34': 0.02, 'wn35': 0.02, 'wh40': 0.1, 'wh51': 0.1}
    # live data voltage fields the HTTP API provides, filled from the sensor battery voltage
    voltage_fields = {'wh40': 'rain.0x13.voltage', 'ws90': 'piezoRain.0x13.voltage', 'wh51': 'ch_soil.{}.voltage',
                      'wn35': 'ch_leaf.{}.voltage'}
    sensor_addresses = _tcp_sensor_addresses()
    _groups = {'temp': 'group_temperature', 'hum': 'group_percent', 'press': 'group_pressure',
               'speed': 'group_speed', 'rain': 'group_rain', 'rate': 'group_rainrate', 'lux': 'group_illuminance',
               'pm': 'group_concentration', 'dist': 'group_distance'}
    _units = {'group_temperature': 'degree_C', 'group_percent': 'percent', 'group_pressure': 'hPa',
              'group_speed': 'meter_per_second', 'group_rain': 'mm', 'group_rainrate': 'mm_per_hour',
              'group_illuminance': 'lux', 'group_concentration': 'microgram_per_meter_cubed',
              'group_distance': 'km'}

    def __init__(self, ip_address, unit_system=DEFAULT_UNIT_SYSTEM, max_tries=DEFAULT_MAX_TRIES,
                 retry_wait=DEFAULT_RETRY_WAIT, url_timeout=DEFAULT_URL_TIMEOUT, show_battery=DEFAULT_FILTER_BATTERY,
                 tcp_port=DEFAULT_TCP_PORT, debug=None, **_):
        if ip_address is None:
            raise weewx.ViolatedPrecondition('device IP address cannot be None')
        self.api = EcowittTcpApi(ip_address, port=tcp_port, max_tries=max_tries, retry_wait=retry_wait,
                                 timeout=url_timeout)
        self.unit_system = unit_system
        self.show_battery = show_battery
        self.sensors = EcowittSensors()
        self.unknown = OnceLogger('TCP API live data')
        self._model = None
        self._firmware = None

    @property
    def ip_address(self):
        return self.api.ip_address

    def _value(self, kind, raw):
        group = self._groups.get(kind)
        value = {'temp': lambda: int.from_bytes(raw, 'big', signed=True) / 10,
                 'gain': lambda: int.from_bytes(raw, 'big') / 100,
                 'dist': lambda: raw[0] if 0 < raw[0] <= 40 else None,
                 'utc': lambda: None if raw == b'\xff\xff\xff\xff' else int.from_bytes(raw, 'big'),
                 'int': lambda: int.from_bytes(raw, 'big'), 'hum': lambda: raw[0],
                 'uv': lambda: int(int.from_bytes(raw, 'big') / 10)}.get(kind, lambda: int.from_bytes(raw, 'big') / 10)()
        if group is None or value is None:
            return value
        vt = weewx.units.ValueTuple(value, self._units[group], group)
        return weewx.units.convert(vt, weewx.units.std_groups[self.unit_system][group]).value

    def _co2(self, data, raw):
        """WH45 (16 bytes) or WH46 (24 bytes) CO2/PM sensor block."""
        conv = self._value
        data.update({'co2.temp': conv('temp', raw[0:2]), 'co2.humidity': raw[2], 'co2.PM10': conv('pm', raw[3:5]),
                     'co2.PM10_24H': conv('pm', raw[5:7]), 'co2.PM25': conv('pm', raw[7:9]),
                     'co2.PM25_24H': conv('pm', raw[9:11]), 'co2.CO2': int.from_bytes(raw[11:13], 'big'),
                     'co2.CO2_24H': int.from_bytes(raw[13:15], 'big')})
        if len(raw) >= 24:
            data.update({'co2.PM1': conv('pm', raw[16:18]), 'co2.PM1_24H': conv('pm', raw[18:20]),
                         'co2.PM4': conv('pm', raw[20:22]), 'co2.PM4_24H': conv('pm', raw[22:24])})

    def parse_live_data(self, payload):
        data, i = {}, 0
        while i < len(payload):
            code = payload[i]
            if code not in self.live_items:
                self.unknown.error(f'unknown item 0x{code:02X}; the rest of the data in this packet is ignored')
                break
            length, kind, key = self.live_items[code]
            if length is None:  # 0x71: a length byte, then that many bytes
                length = 1 + (payload[i + 1] if i + 1 < len(payload) else 0)
            raw = payload[i + 1:i + 1 + length]
            i += 1 + length
            if len(raw) < length:
                self.unknown.error(f'item 0x{code:02X} is truncated')
                break
            if kind == 'wn34':
                data[f'ch_temp.{key}.temp'] = self._value('temp', raw[0:2])
                data[f'ch_temp.{key}.voltage'] = round(raw[2] * 0.02, 2)
            elif kind in ('wh45', 'wh46'):
                self._co2(data, raw)
            elif kind == 'gains':
                data.update({f'gain{n + 1}': int.from_bytes(raw[2 * n:2 * n + 2], 'big') / 100 for n in range(5)})
            elif kind == 'reset':
                data.update({'rain_reset_day': raw[0], 'rain_reset_week': raw[1], 'rain_reset_year': raw[2]})
            elif kind is not None:
                data[key] = self._value(kind, raw)
        for ch in range(1, 9):
            if f'ch_aisle.{ch}.temp' in data or f'ch_aisle.{ch}.humidity' in data:
                data[f'ch_aisle.{ch}.channel'] = ch
        self.unknown.ok('data decoded again')
        return data

    def get_live_data(self, flatten_data=True):
        return self.parse_live_data(self.api.call(0x27))

    def get_sensors_data(self, connected_only=DEFAULT_ONLY_REGISTERED_SENSORS, flatten_data=True):
        payload, parsed, volts = self.api.call(0x3C), {}, {}
        for n in range(0, len(payload) - 6, 7):
            address, sid, batt, signal = payload[n], int.from_bytes(payload[n + 1:n + 5], 'big'), \
                payload[n + 5], payload[n + 6]
            if address not in self.sensor_addresses:
                continue
            unregistered = sid in (0xFFFFFFFE, 0xFFFFFFFF)
            if connected_only and unregistered:
                continue
            model, channel = self.sensor_addresses[address]
            sensor = {'address': address, 'id': f'{sid:X}', 'enabled': sid != 0xFFFFFFFE, 'rssi': None,
                      'signal': None if unregistered else signal,
                      'battery': None if unregistered or (not self.show_battery and signal == 0) else batt}
            if sensor['battery'] is not None and model in self.battery_volts:
                sensor['battery'] = sensor['voltage'] = round(batt * self.battery_volts[model], 2)
                if model in self.voltage_fields:
                    volts[self.voltage_fields[model].format(channel[2:] if channel else '')] = sensor['voltage']
            if channel is None:
                parsed[model] = sensor
            else:
                parsed.setdefault(model, {})[channel] = sensor
        return {**flatten(parsed), **volts} if flatten_data else parsed

    @property
    def firmware(self):
        """The firmware string, eg 'GW1000B_V1.7.7'."""

        if self._firmware is None:
            data = self.api.call(0x50)
            self._firmware = data[1:1 + data[0]].decode('ascii', 'replace').strip() if data else ''
        return self._firmware

    @property
    def model(self):
        """The device model, eg 'GW1000'."""

        if self._model is None:
            firmware = self.firmware.upper()
            self._model = next((m for m in KNOWN_DEVICES if firmware.startswith(m)),
                               firmware.split('_')[0]) or None
        return self._model

    @property
    def firmware_version(self):
        parts = self.firmware.split('_')
        return parts[1] if len(parts) > 1 else None

    @property
    def mac_address(self):
        return bytes_to_hex(self.api.call(0x26)[0:6], separator=':')

    firmware_update_avail = firmware_update_message = None

    @property
    def sensor_firmware_versions(self):
        return {}

    @property
    def paired_rain_gauges(self):
        info = self.get_sensors_data(connected_only=True, flatten_data=False)
        return tuple(g for g, models in (('tipping', ('wh40', 'wh69')), ('piezo', ('ws85', 'ws90')))
                     if any(m in info for m in models))

    def __getattr__(self, name):
        if name.startswith('get_'):
            def unsupported(*_args, **_kwargs):
                raise UnsupportedFeatureError(f'{self._model or "this device"} (TCP API) does not provide '
                                              f"'{name[4:]}' information")
            return unsupported
        raise AttributeError(name)


def make_device(ip_address, api=DEFAULT_API, tcp_port=DEFAULT_TCP_PORT, **kwargs):
    """An EcowittDevice (HTTP API) or EcowittTcpDevice (GW1000 TCP API).

    api is 'http', 'tcp' or 'auto'. Auto asks the TCP API for the model first (quick, and it
    avoids HTTP errors in the log for a GW1000): a device without the local HTTP API
    (TCP_ONLY_DEVICES) uses the TCP API, anything else the HTTP API, with the TCP API as a
    fallback if the HTTP API does not answer. If neither answers the device is returned with
    detected = False and the collector tries again later.
    """
    api = str(api or DEFAULT_API).lower()
    if api == 'tcp':
        return EcowittTcpDevice(ip_address, tcp_port=tcp_port, **kwargs)
    if api == 'http':
        return EcowittDevice(ip_address, **kwargs)
    tcp = EcowittTcpDevice(ip_address, tcp_port=tcp_port, **{**kwargs, 'max_tries': 1})
    try:
        model = tcp.model
    except (DeviceIOError, OSError):
        model = None
    tcp.api.max_tries = kwargs.get('max_tries') or DEFAULT_MAX_TRIES
    if model and any(m in model.upper() for m in TCP_ONLY_DEVICES):
        log.info('     %s has no local HTTP API; using the TCP API on port %d', model, tcp.api.port)
        return tcp
    device = EcowittDevice(ip_address, **kwargs)
    try:
        if device.model:
            return device
    except Exception:
        pass
    if model:
        log.info('     %s did not answer the HTTP API; using the TCP API on port %d', model, tcp.api.port)
        return tcp
    device.detected = False  # neither API answered: the collector tries again later
    return device



# ============================================================================
#                             Utility functions
# ============================================================================

def define_units():
    """Define formats and conversions used by the driver.

    This could be done in user/extensions.py or the driver. The
    user/extensions.py approach will make the conversions and formats available
    for all drivers and services, but requires manual editing of the file by
    the user. Inclusion in the driver removes the need for the user to edit
    user/extensions.py, but means the conversions and formats are only defined
    when the driver is being used. Given the specialised nature of the
    conversions and formats the latter is an acceptable approach. In any case,
    there is nothing preventing the user manually adding these entries to
    user/extensions.py.

    As of v5.0.0 WeeWX defines the unit group 'group_data' with member units
    'byte' and 'bit'. We will define additional group_data member units of
    'kilobyte' and 'megabyte'.

    All additions to the core conversion, label and format dicts are done in a
    way that do not overwrite and previous customisations the user may have
    made through another driver or user/extensions.py.
    """

    # create group for depth
    weewx.units.USUnits['group_depth'] = 'foot2'
    weewx.units.MetricUnits['group_depth'] = 'mm2'
    weewx.units.MetricWXUnits['group_depth'] = 'mm2'

    # set default formats and labels for depth
    weewx.units.default_unit_format_dict['foot2'] = '%.2f'
    weewx.units.default_unit_label_dict['foot2'] = ' ft'
    weewx.units.default_unit_format_dict['mm2'] = '%.0f'
    weewx.units.default_unit_label_dict['mm2'] = ' mm'

    weewx.units.conversionDict['mm2'] = {'inch2': lambda x: x / 25.4,
                                         'foot2': lambda x: x / 304.8,
                                         'cm2': lambda x: x / 10.0,
                                         'meter2': lambda x: x / 1000.0}
    weewx.units.conversionDict['cm2'] = {'inch2': lambda x: x / 2.54,
                                         'foot2': lambda x: x / 30.48,
                                         'mm2': lambda x: x * 10.0,
                                         'meter2': lambda x: x / 100.0}
    weewx.units.conversionDict['m2'] = {'inch2': lambda x: x / 0.0254,
                                        'foot2': lambda x: x / 0.3048,
                                        'mm2': lambda x: x * 1000.0,
                                        'cm2': lambda x: x * 100.0}
    weewx.units.conversionDict['inch2'] = {'foot2': lambda x: x / 12.0,
                                           'mm2': lambda x: x * 25.4,
                                           'cm2': lambda x: x * 2.54,
                                           'meter2': lambda x: x * 0.0254}
    weewx.units.conversionDict['foot2'] = {'inch2': lambda x: x * 12.0,
                                           'mm2': lambda x: x * 304.8,
                                           'cm2': lambda x: x * 30.48,
                                           'meter2': lambda x: x * 0.3048}

    # add 'nautical_mile' conversions
    if 'nautical_mile' not in weewx.units.conversionDict:
        # 'nautical_mile' is not a key in the conversion dict, so we add all
        # conversions
        weewx.units.conversionDict['nautical_mile'] = {'meter': lambda x: x * 1852.0,
                                                       'km': lambda x: x * 1.852,
                                                       'mile': lambda x: x * 1.151}
    else:
        # 'nautical_mile' already exists as a key in the conversion dict, so we
        # add all conversions individually if they do not already exist
        if 'meter' not in weewx.units.conversionDict['nautical_mile'].keys():
            weewx.units.conversionDict['nautical_mile']['meter'] = lambda x: x * 1852.0
        if 'km' not in weewx.units.conversionDict['nautical_mile'].keys():
            weewx.units.conversionDict['nautical_mile']['km'] = lambda x: x * 1.852
        if 'mile' not in weewx.units.conversionDict['nautical_mile'].keys():
            weewx.units.conversionDict['nautical_mile']['mile'] = lambda x: x * 1.151
    # The 'nautical_mile' entry in the conversion dict but the 'meter', 'km'
    # and 'mile' entries do not have a 'nautical_mile' entry. We need to add
    # them.
    if 'nautical_mile' not in weewx.units.conversionDict['meter'].keys():
        weewx.units.conversionDict['meter']['nautical_mile'] = lambda x: x / 1852.0
    if 'nautical_mile' not in weewx.units.conversionDict['km'].keys():
        weewx.units.conversionDict['km']['nautical_mile'] = lambda x: x / 1.852
    if 'nautical_mile' not in weewx.units.conversionDict['mile'].keys():
        weewx.units.conversionDict['mile']['nautical_mile'] = lambda x: x / 1.151

    # add 'knot' conversions
    if 'knot' not in weewx.units.conversionDict:
        # 'knot' is not a key in the conversion dict, so we add all conversions
        weewx.units.conversionDict['knot'] = {'meter_per_second': lambda x: x * 1852.0,
                                              'km_per_hour': lambda x: x * 1.852,
                                              'mile_per_hour': lambda x: x * 1.151}
    else:
        # 'knot' already exists as a key in the conversion dict, so we add all
        # conversions individually if they do not already exist
        if 'meter_per_second' not in weewx.units.conversionDict['knot'].keys():
            weewx.units.conversionDict['knot']['meter_per_second'] = lambda x: x * 1852.0
        if 'km_per_hour' not in weewx.units.conversionDict['knot'].keys():
            weewx.units.conversionDict['knot']['km_per_hour'] = lambda x: x * 1.852
        if 'mile_per_hour' not in weewx.units.conversionDict['knot'].keys():
            weewx.units.conversionDict['knot']['mile_per_hour'] = lambda x: x * 1.151
    # The 'knot' entry in the conversion dict but the 'meter_per_second',
    # 'km_per_hour' and 'mile_per_hour' entries do not have a 'nautical_mile'
    # entry. We need to add them.
    if 'knot' not in weewx.units.conversionDict['meter_per_second'].keys():
        weewx.units.conversionDict['meter_per_second']['knot'] = lambda x: x / 1852.0
    if 'knot' not in weewx.units.conversionDict['km_per_hour'].keys():
        weewx.units.conversionDict['km_per_hour']['knot'] = lambda x: x / 1.852
    if 'knot' not in weewx.units.conversionDict['mile'].keys():
        weewx.units.conversionDict['mile_per_hour']['knot'] = lambda x: x / 1.151

    # add kilobyte and megabyte conversions
    if 'byte' not in weewx.units.conversionDict:
        # 'byte' is not a key in the conversion dict, so we add all conversions
        weewx.units.conversionDict['byte'] = {'bit': lambda x: x * 8,
                                              'kilobyte': lambda x: x / 1024.0,
                                              'megabyte': lambda x: x / 1024.0 ** 2}
    else:
        # byte already exists as a key in the conversion dict, so we add all
        # conversions individually if they do not already exist
        if 'bit' not in weewx.units.conversionDict['byte'].keys():
            weewx.units.conversionDict['byte']['bit'] = lambda x: x * 8
        if 'kilobyte' not in weewx.units.conversionDict['byte'].keys():
            weewx.units.conversionDict['byte']['kilobyte'] = lambda x: x / 1024.0
        if 'megabyte' not in weewx.units.conversionDict['byte'].keys():
            weewx.units.conversionDict['byte']['megabyte'] = lambda x: x / 1024.0 ** 2
    if 'kilobyte' not in weewx.units.conversionDict:
        weewx.units.conversionDict['kilobyte'] = {'bit': lambda x: x * 8192,
                                                  'byte': lambda x: x * 1024,
                                                  'megabyte': lambda x: x / 1024.0}
    else:
        # kilobyte already exists as a key in the conversion dict, so we add
        # all conversions individually if they do not already exist
        if 'bit' not in weewx.units.conversionDict['kilobyte'].keys():
            weewx.units.conversionDict['kilobyte']['bit'] = lambda x: x * 8192
        if 'byte' not in weewx.units.conversionDict['kilobyte'].keys():
            weewx.units.conversionDict['kilobyte']['byte'] = lambda x: x * 1024
        if 'megabyte' not in weewx.units.conversionDict['kilobyte'].keys():
            weewx.units.conversionDict['kilobyte']['megabyte'] = lambda x: x / 1024.0
    if 'megabyte' not in weewx.units.conversionDict:
        weewx.units.conversionDict['megabyte'] = {'bit': lambda x: x * 8 * 1024 ** 2,
                                                  'byte': lambda x: x * 1024 ** 2,
                                                  'kilobyte': lambda x: x * 1024}
    else:
        # megabyte already exists as a key in the conversion dict, so we add
        # all conversions individually if they do not already exist
        if 'bit' not in weewx.units.conversionDict['megabyte'].keys():
            weewx.units.conversionDict['megabyte']['bit'] = lambda x: x * 8 * 1024 ** 2
        if 'byte' not in weewx.units.conversionDict['megabyte'].keys():
            weewx.units.conversionDict['megabyte']['byte'] = lambda x: x * 1024 ** 2
        if 'kilobyte' not in weewx.units.conversionDict['megabyte'].keys():
            weewx.units.conversionDict['megabyte']['kilobyte'] = lambda x: x * 1024

    # add lux, klux, watt_per_meter_squared and kfc conversions
    if 'klux' not in weewx.units.conversionDict:
        # 'klux' is not a key in the conversion dict, so we add all conversions
        weewx.units.conversionDict['klux'] = {'lux': lambda x: x * 1000,
                                              'watt_per_meter_squared': lambda x: x * 1000 / 126.7,
                                              'kfc': lambda x: x * 10.76 / 0.1267 ** 2}
    else:
        # klux already exists as a key in the conversion dict, so we add all
        # conversions individually if they do not already exist
        if 'lux' not in weewx.units.conversionDict['klux'].keys():
            weewx.units.conversionDict['klux']['lux'] = lambda x: x * 1000
        if 'watt_per_meter_squared' not in weewx.units.conversionDict['klux'].keys():
            weewx.units.conversionDict['klux']['watt_per_meter_squared'] = lambda x: x * 1000 / 126.7
        if 'kfc' not in weewx.units.conversionDict['klux'].keys():
            weewx.units.conversionDict['klux']['kfc'] = lambda x: x * 10.76 / 0.1267 ** 2
    if 'lux' not in weewx.units.conversionDict:
        # 'lux' is not a key in the conversion dict, so we add all conversions
        weewx.units.conversionDict['lux'] = {'klux': lambda x: x / 1000.0,
                                             'watt_per_meter_squared': lambda x: x / 126.7,
                                             'kfc': lambda x: x * 0.01076 / 0.1267 ** 2}
    else:
        # lux already exists as a key in the conversion dict, so we add all
        # conversions individually if they do not already exist
        if 'klux' not in weewx.units.conversionDict['lux'].keys():
            weewx.units.conversionDict['lux']['klux'] = lambda x: x / 1000.0
        if 'watt_per_meter_squared' not in weewx.units.conversionDict['lux'].keys():
            weewx.units.conversionDict['lux']['watt_per_meter_squared'] = lambda x: x / 126.7
        if 'kfc' not in weewx.units.conversionDict['lux'].keys():
            weewx.units.conversionDict['lux']['kfc'] = lambda x: x * 0.01076 / 0.1267 ** 2
    if 'watt_per_meter_squared' not in weewx.units.conversionDict:
        # 'watt_per_meter_squared' is not a key in the conversion dict, so we add all conversions
        weewx.units.conversionDict['watt_per_meter_squared'] = {'klux': lambda x: x * 0.1267,
                                                                'lux': lambda x: x * 126.7,
                                                                'kfc': lambda x: x * 10.76 / 0.1267}
    else:
        # watt_per_meter_squared already exists as a key in the conversion
        # dict, so we add all conversions individually if they do not already
        # exist
        if 'klux' not in weewx.units.conversionDict['watt_per_meter_squared'].keys():
            weewx.units.conversionDict['watt_per_meter_squared']['klux'] = lambda x: x * 0.1267
        if 'lux' not in weewx.units.conversionDict['watt_per_meter_squared'].keys():
            weewx.units.conversionDict['watt_per_meter_squared']['lux'] = lambda x: x * 126.7
        if 'kfc' not in weewx.units.conversionDict['watt_per_meter_squared'].keys():
            weewx.units.conversionDict['watt_per_meter_squared']['kfc'] = lambda x: x * 10.76 / 0.1267
    if 'kfc' not in weewx.units.conversionDict:
        # 'kfc' is not a key in the conversion dict, so we add all conversions
        weewx.units.conversionDict['kfc'] = {'klux': lambda x: x * 0.1267 ** 2 / 10.76,
                                             'lux': lambda x: x * 126.7 ** 2 / 10.76,
                                             'watt_per_meter_squared': lambda x: x * 0.1267 / 10.76}
    else:
        # kfc already exists as a key in the conversion dict, so we add all
        # conversions individually if they do not already exist
        if 'klux' not in weewx.units.conversionDict['kfc'].keys():
            weewx.units.conversionDict['kfc']['klux'] = lambda x: x * 0.1267 ** 2 / 10.76
        if 'lux' not in weewx.units.conversionDict['kfc'].keys():
            weewx.units.conversionDict['kfc']['lux'] = lambda x: x * 126.7 ** 2 / 10.76
        if 'watt_per_meter_squared' not in weewx.units.conversionDict['kfc'].keys():
            weewx.units.conversionDict['kfc']['watt_per_meter_squared'] = lambda x: x * 0.1267 / 10.76

    # set default formats and labels for byte, kilobyte and megabyte, but only
    # if they do not already exist
    weewx.units.default_unit_format_dict['byte'] = weewx.units.default_unit_format_dict.get('byte') or '%.d'
    weewx.units.default_unit_label_dict['byte'] = weewx.units.default_unit_label_dict.get('byte') or ' B'
    weewx.units.default_unit_format_dict['kilobyte'] = weewx.units.default_unit_format_dict.get('kilobyte') or '%.3f'
    weewx.units.default_unit_label_dict['kilobyte'] = weewx.units.default_unit_label_dict.get('kilobyte') or ' kB'
    weewx.units.default_unit_format_dict['megabyte'] = weewx.units.default_unit_format_dict.get('megabyte') or '%.3f'
    weewx.units.default_unit_label_dict['megabyte'] = weewx.units.default_unit_label_dict.get('megabyte') or ' MB'

    # create group for delta temperatures
    weewx.units.USUnits['group_deltat'] = 'degree_F2'
    weewx.units.MetricUnits['group_deltat'] = 'degree_C2'
    weewx.units.MetricWXUnits['group_deltat'] = 'degree_C2'

    # set default formats and labels for depth
    weewx.units.default_unit_format_dict['degree_C2'] = '%.1f'
    weewx.units.default_unit_label_dict['degree_C2'] = '°C'
    weewx.units.default_unit_format_dict['degree_E2'] = '%.1f'
    weewx.units.default_unit_label_dict['degree_E2'] = '°C'
    weewx.units.default_unit_format_dict['degree_F2'] = '%.1f'
    weewx.units.default_unit_label_dict['degree_F2'] = '°F'
    weewx.units.default_unit_format_dict['degree_K2'] = '%.1f'
    weewx.units.default_unit_label_dict['degree_K2'] = '°C'

    weewx.units.conversionDict['degree_C2'] = {'degree_F2': lambda x: x * 9 / 5,
                                               'degree_E2': lambda x: x * 7 / 5,
                                               'degree_K2': lambda x: x}
    weewx.units.conversionDict['degree_E2'] = {'degree_C2': lambda x: x * 5 / 7,
                                               'degree_F2': lambda x: x * 9 / 7,
                                               'degree_K2': lambda x: x * 5 / 7}
    weewx.units.conversionDict['degree_F2'] = {'degree_C2': lambda x: x * 5 / 9,
                                               'degree_E2': lambda x: x * 7 / 9,
                                               'degree_K2': lambda x: x * 5 / 9}
    weewx.units.conversionDict['degree_K2'] = {'degree_C2': lambda x: x,
                                               'degree_E2': lambda x: x * 7 / 5,
                                               'degree_F2': lambda x: x * 9 / 5}

    # Unit groups of fields that are not in the field map: those WeeWX
    # calculates from the gateway's totals ([StdWXCalculate] [[Delta]]) and
    # the driver's own gateway_online. Without a group WeeWX's unit conversion
    # passes a field through unchanged, so eg t_rain in ecwLoop.json/MQTT
    # stayed in inches when the export asked for mm. Existing entries (eg from
    # user/extensions.py) are kept.
    for field, group in (('t_rain', 'group_rain'), ('p_rain', 'group_rain'),
                         ('t_rainrate', 'group_rainrate'), ('p_rainrate', 'group_rainrate'),
                         ('gateway_online', 'group_boolean')):
        if field not in weewx.units.obs_group_dict:
            weewx.units.obs_group_dict[field] = group


def natural_sort_keys(source_dict):
    """Return a naturally sorted list of keys for a dict."""

    def atoi(text):
        return int(text) if text.isdigit() else text

    def natural_keys(text):
        """Natural key sort.

        Allows use of key=natural_keys to sort a list in human order, eg:
            alist.sort(key=natural_keys)

        https://nedbatchelder.com/blog/200712/human_sorting.html (See
        Toothy's implementation in the comments)
        """

        return [atoi(c) for c in re.split(r'(\d+)', text.lower())]

    keys_list = list(source_dict.keys())
    # naturally sort the list of keys where, for example, xxxxx16 appears in the
    # correct order
    keys_list.sort(key=natural_keys)
    return keys_list


def natural_sort_dict(source_dict):
    """Return a string representation of a dict sorted naturally by key.

    When represented as a string a dict is displayed in the format:
        {key a:value a, key b: value b ... key z: value z}
    but the order of the key:value pairs is unlikely to be alphabetical.
    Displaying dicts of key:value pairs in logs or on the console in
    alphabetical order by key assists in the analysis of the dict data.
    Where keys are strings with leading digits a natural sort is useful.
    """

    # first obtain a list of key:value pairs as string sorted naturally by key
    sorted_dict_fields = [f"'{k}': '{source_dict[k]}'" for k in natural_sort_keys(source_dict)]
    return f'{{{", ".join(sorted_dict_fields)}}}'


# Longest text put in one log message. Syslog daemons cut long messages off
# (rsyslog's default $MaxMessageSize is 8 kB), so longer text, such as the
# field map or a loop packet, is logged over several numbered lines.
LOG_LINE_MAX = 3000


def log_long(label, text, level=logging.INFO, width=LOG_LINE_MAX):
    """Log label followed by text, over several numbered lines if text is long.

    Lines are broken after a ', ' where there is one, so a dict is split
    between entries: '<label>(part 1 of 3) {...,'."""

    text = str(text)
    if len(text) <= width:
        log.log(level, '%s%s', label, text)
        return
    parts = []
    while len(text) > width:
        cut = text.rfind(', ', 0, width)
        cut = cut + 1 if cut > 0 else width
        parts.append(text[:cut])
        text = text[cut:].lstrip(' ')
    parts.append(text)
    for n, part in enumerate(parts, 1):
        log.log(level, '%s(part %d of %d) %s', label, n, len(parts), part)


def log_dict(label, source_dict, level=logging.INFO):
    """Log a dict, naturally sorted by key, over several lines if it is long."""

    log_long(label, natural_sort_dict(source_dict), level)


def active_field_map(field_map, data):
    """The entries of a field map the device is filling now: the device field
    is in data and has a value. (A device also reports empty signal and battery
    fields for sensors that aren't paired; those are left out.)"""

    return {w: s for w, s in field_map.items() if data.get(s) is not None}


def bytes_to_hex(iterable, separator=' ', caps=True):
    """Produce a hex string representation of a sequence of bytes."""

    # assume 'iterable' can be iterated by iterbytes and the individual
    # elements can be formatted with {:02X}
    format_str = "{:02X}" if caps else "{:02x}"
    try:
        return separator.join(format_str.format(c) for c in iterable)
    except ValueError:
        # most likely we are running python3 and iterable is not a bytestring,
        # try again coercing iterable to a bytestring
        return separator.join(format_str.format(c) for c in str.encode(iterable))
    except (TypeError, AttributeError):
        # TypeError - 'iterable' is not iterable
        # AttributeError - likely because separator is None
        # either way we can't represent as a string of hex bytes
        return f"cannot represent '{iterable}' as hexadecimal bytes"


def obfuscate(plain, obf_char='*'):
    """Obfuscate all but the last x characters in a string.

    Obfuscate all but (at most) the last four characters of a string. Always
    reveal no more than 50% of the characters. The obfuscation character
    defaults to '*' but can be set when the function is called.
    """

    if plain is not None and len(plain) > 0:
        stem = 4
        stem = 3 if len(plain) < 8 else stem
        stem = 2 if len(plain) < 6 else stem
        stem = 1 if len(plain) < 4 else stem
        stem = 0 if len(plain) < 3 else stem
        if stem > 0:
            # we are retaining some characters so do a little string
            # manipulation
            obfuscated = obf_char * (len(plain) - stem) + plain[-stem:]
        else:
            # we are obfuscating everything
            obfuscated = obf_char * len(plain)
        return obfuscated
    return plain


_SECRET_KEY = re.compile(r'pwd|pass|key|token|secret|_id$', re.IGNORECASE)


def mask_secrets(value, key=''):
    """A copy of a decoded API response with passwords, keys and station IDs obfuscated."""

    if isinstance(value, dict):
        return {k: mask_secrets(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v, key) for v in value]
    return obfuscate(str(value)) if value and _SECRET_KEY.search(str(key)) else value


def flatten(dictionary, parent_key=False, separator='.'):
    """
    Turn a nested dictionary into a flattened dictionary
    :param dictionary: The dictionary to flatten
    :param parent_key: The string to prepend to dictionary's keys
    :param separator: The string used to separate flattened keys
    :return: A flattened dictionary
    """

    # do a check to ensure we have a dict
    if not hasattr(dictionary, 'keys'):
        # 'dictionary' is not a dict, return the value None
        return None
    else:
        items = []
        for key, value in dictionary.items():
            new_key = str(parent_key) + separator + key if parent_key else key
            if isinstance(value, MutableMapping):
                if not value.items():
                    items.append((new_key, None))
                else:
                    items.extend(flatten(value, new_key, separator).items())
            elif isinstance(value, list):
                if len(value):
                    for k, v in channelise_enumerate(value, channelise=True):
                        items.extend(flatten({str(k): v}, new_key, separator).items())
                else:
                    items.append((new_key,None))
            else:
                items.append((new_key, value))
        return dict(items)


def channelise_enumerate(iterable, start=0, channelise=False):
    """Specialised version of enumerate to yield channel/ID numbers.

    The Python enumerate function is a generator that emits tuples containing a
    count (from start which defaults to 0) and the values obtained from
    iterating over the argument. This specialised version of enumerate looks
    for a 'channel' key or 'id' key in each iterable element and if found uses
    this key value in place of the count used by the enumerate function. This
    facilitates processing of lists of non-consecutive channelised sensor data.

    If 'channelise' is True the function looks for (in order) key 'channel' or
    key 'id' in iterable element. If either key is found the corresponding key
    value is used in place of the incrementing count value in the emitted
    tuples. If both 'channel' and 'id' keys exist the 'channel' key value takes
    precedence over the 'id' key value.

    If 'channelise' is not set or set to False or iterable is not a dict, the
    function operates in a manner identical to the built-in function enumerate.
    """

    n = start
    for elem in iterable:
        i = None
        if channelise and 'channel' in elem:
            i = int(elem['channel'])
        elif channelise and 'id' in elem:
            i = int(elem['id'])
        p = i if i is not None else n
        yield p, elem
        n += 1


def calc_checksum(data):
    """Calculate the checksum for an API call or response.

    The checksum used in an API response is simply the LSB of the sum
    of the command, size and data bytes. The fixed header and checksum
    bytes are excluded.

    data: The data on which the checksum is to be calculated. Byte
          string.

    Returns the checksum as an integer.
    """

    checksum = 0
    for b in data:
        # add the byte to the running total
        checksum += b
    # we are only interested in the least significant byte
    return checksum % 256


# ============================================================================
#                         class DirectEcowittDevice
# ============================================================================

class DirectEcowittDevice:
    """Class to interact with Ecowitt HTTP driver when run directly.

    Would normally run a driver directly by calling from main() only, but when
    run directly the Ecowitt HTTP driver has many options so pushing the detail
    into its own class/object makes sense. Also simplifies some test suite
    routines/calls.

    Once created the process_options() method is called to process the
    respective command line options.
    """
    known_fields = ['rain.0x0D.val', 'rain.0x0D.voltage',
                    'rain.0x0E.val', 'rain.0x0E.voltage',
                    'rain.0x10.val', 'rain.0x10.voltage',
                    'rain.0x11.val', 'rain.0x11.voltage',
                    'rain.0x12.val', 'rain.0x12.voltage',
                    'rain.0x13.val', 'rain.0x13.voltage',
                    'piezoRain.srain_piezo.val',
                    'piezoRain.0x0D.val', 'piezoRain.0x0D.voltage',
                    'piezoRain.0x0E.val', 'piezoRain.0x0E.voltage',
                    'piezoRain.0x10.val', 'piezoRain.0x10.voltage',
                    'piezoRain.0x11.val', 'piezoRain.0x11.voltage',
                    'piezoRain.0x12.val', 'piezoRain.0x12.voltage',
                    'piezoRain.0x13.val', 'piezoRain.0x13.voltage',
                    'wh25.intemp', 'wh25.inhumi', 'wh25.abs', 'wh25.rel',
                    'wh25.CO2', 'wh25.CO2_24H',
                    'common_list.0x02.val', 'common_list.0x02.voltage',
                    'common_list.0x03.val', 'common_list.0x03.voltage',
                    'common_list.0x07.val', 'common_list.0x07.voltage',
                    'common_list.0x0A.val', 'common_list.0x0A.voltage',
                    'common_list.0x0B.val', 'common_list.0x0B.voltage',
                    'common_list.0x0C.val', 'common_list.0x0C.voltage',
                    'common_list.0x19.val', 'common_list.0x19.voltage',
                    'common_list.0x15.val', 'common_list.0x15.voltage',
                    'common_list.0x16.val', 'common_list.0x16.voltage',
                    'common_list.0x17.val', 'common_list.0x17.voltage',
                    'lightning.distance', 'lightning.timestamp', 'lightning.count',
                    'ch_aisle.1.temp', 'ch_aisle.1.humidity', 'ch_aisle.2.temp', 'ch_aisle.2.humidity',
                    'ch_aisle.3.temp', 'ch_aisle.3.humidity', 'ch_aisle.4.temp', 'ch_aisle.4.humidity',
                    'ch_aisle.5.temp', 'ch_aisle.5.humidity', 'ch_aisle.6.temp', 'ch_aisle.6.humidity',
                    'ch_aisle.7.temp', 'ch_aisle.7.humidity', 'ch_aisle.8.temp', 'ch_aisle.8.humidity',
                    'ch_pm25.1.PM25', 'ch_pm25.1.PM25_RealAQI', 'ch_pm25.1.PM25_24HAQI',
                    'ch_pm25.2.PM25', 'ch_pm25.2.PM25_RealAQI', 'ch_pm25.2.PM25_24HAQI',
                    'ch_pm25.3.PM25', 'ch_pm25.3.PM25_RealAQI', 'ch_pm25.3.PM25_24HAQI',
                    'ch_pm25.4.PM25', 'ch_pm25.4.PM25_RealAQI', 'ch_pm25.4.PM25_24HAQI',
                    'ch_soil.1.humidity', 'ch_soil.1.voltage', 'ch_soil.2.humidity', 'ch_soil.2.voltage',
                    'ch_soil.3.humidity', 'ch_soil.3.voltage', 'ch_soil.4.humidity', 'ch_soil.4.voltage',
                    'ch_soil.5.humidity', 'ch_soil.5.voltage', 'ch_soil.6.humidity', 'ch_soil.6.voltage',
                    'ch_soil.7.humidity', 'ch_soil.7.voltage', 'ch_soil.8.humidity', 'ch_soil.8.voltage',
                    'ch_temp.1.temp', 'ch_temp.1.voltage', 'ch_temp.2.temp', 'ch_temp.2.voltage',
                    'ch_temp.3.temp', 'ch_temp.3.voltage', 'ch_temp.4.temp', 'ch_temp.4.voltage',
                    'ch_temp.5.temp', 'ch_temp.5.voltage', 'ch_temp.6.temp', 'ch_temp.6.voltage',
                    'ch_temp.7.temp', 'ch_temp.7.voltage', 'ch_temp.8.temp', 'ch_temp.8.voltage',
                    'ch_lds.1.air', 'ch_lds.1.depth', 'ch_lds.1.battery', 'ch_lds.1.voltage',
                    'ch_lds.2.air', 'ch_lds.2.depth', 'ch_lds.2.battery', 'ch_lds.2.voltage',
                    'ch_lds.3.air', 'ch_lds.3.depth', 'ch_lds.3.battery', 'ch_lds.3.voltage',
                    'ch_lds.4.air', 'ch_lds.4.depth', 'ch_lds.4.battery', 'ch_lds.4.voltage',
                    'ch_leak.1.status', 'ch_leak.1.voltage', 'ch_leak.2.status', 'ch_leak.2.voltage',
                    'ch_leak.3.status', 'ch_leak.3.voltage', 'ch_leak.4.status', 'ch_leak.4.voltage',
                    'debug.heap', 'debug.runtime', 'debug.is_cnip',
                    'wh24.battery', 'wh24.signal', 'wh25.battery', 'wh25.signal',
                    'wh26.battery', 'wh26.signal',
                    'wn31.ch1.battery', 'wn31.ch1.signal', 'wn31.ch2.battery', 'wn31.ch2.signal',
                    'wn31.ch3.battery', 'wn31.ch3.signal', 'wn31.ch4.battery', 'wn31.ch4.signal',
                    'wn31.ch5.battery', 'wn31.ch5.signal', 'wn31.ch6.battery', 'wn31.ch6.signal',
                    'wn31.ch7.battery', 'wn31.ch7.signal', 'wn31.ch8.battery', 'wn31.ch8.signal',
                    'wn32.battery', 'wn32.signal',
                    'wn34.ch1.battery', 'wn34.ch1.signal', 'wn34.ch2.battery', 'wn34.ch2.signal',
                    'wn34.ch3.battery', 'wn34.ch3.signal', 'wn34.ch4.battery', 'wn34.ch4.signal',
                    'wn34.ch5.battery', 'wn34.ch5.signal', 'wn34.ch6.battery', 'wn34.ch6.signal',
                    'wn34.ch7.battery', 'wn34.ch7.signal', 'wn34.ch8.battery', 'wn34.ch8.signal',
                    'wn35.ch1.battery', 'wn35.ch1.signal', 'wn35.ch2.battery', 'wn35.ch2.signal',
                    'wn35.ch3.battery', 'wn35.ch3.signal', 'wn35.ch4.battery', 'wn35.ch4.signal',
                    'wn35.ch5.battery', 'wn35.ch5.signal', 'wn35.ch6.battery', 'wn35.ch6.signal',
                    'wn35.ch7.battery', 'wn35.ch7.signal', 'wn35.ch8.battery', 'wn35.ch8.signal',
                    'wh40.battery', 'wh40.signal',
                    'wh41.ch1.battery', 'wh41.ch1.signal', 'wh41.ch2.battery', 'wh41.ch2.signal',
                    'wh41.ch3.battery', 'wh41.ch3.signal', 'wh41.ch4.battery', 'wh41.ch4.signal',
                    'wh45.battery', 'wh45.signal', 'wh46.battery', 'wh46.signal',
                    'wh51.ch1.battery', 'wh51.ch1.signal', 'wh51.ch2.battery', 'wh51.ch2.signal',
                    'wh51.ch3.battery', 'wh51.ch3.signal', 'wh51.ch4.battery', 'wh51.ch4.signal',
                    'wh51.ch5.battery', 'wh51.ch5.signal', 'wh51.ch6.battery', 'wh51.ch6.signal',
                    'wh51.ch7.battery', 'wh51.ch7.signal', 'wh51.ch8.battery', 'wh51.ch8.signal',
                    'wh54.ch1.battery', 'wh54.ch1.signal', 'wh54.ch2.battery', 'wh54.ch2.signal',
                    'wh54.ch3.battery', 'wh54.ch3.signal', 'wh54.ch4.battery', 'wh54.ch4.signal',
                    'wh55.ch1.battery', 'wh55.ch1.signal', 'wh55.ch2.battery', 'wh55.ch2.signal',
                    'wh55.ch3.battery', 'wh55.ch3.signal', 'wh55.ch4.battery', 'wh55.ch4.signal',
                    'wh57.battery', 'wh57.signal',
                    'wh65.battery', 'wh65.signal', 'wh68.battery', 'wh68.signal',
                    'wh69.battery', 'wh69.signal', 'ws80.battery', 'ws80.signal',
                    'ws85.battery', 'ws85.signal', 'ws90.battery', 'ws90.signal'
                    ]

    sensor_display_order = ('ws85', 'ws90', 'ws69', 'ws68', 'wh40', 'wh25',
                            'wh26', 'ws80', 'wh57', 'wh41', 'wh55', 'wn31',
                            'wh51', 'wn34', 'wn35', 'wh54')
    def __init__(self, namespace, arg_parser, stn_dict, **kwargs):
        """Initialise a DirectEcowittDevice object."""

        self.namespace = namespace
        # save a reference to our argparse instance
        self.arg_parser = arg_parser
        # With [[sources]] (several gateways) the commands about one gateway
        # use --source (default: the first gateway) and its settings, while
        # --test-driver, --test-service, --weewx-fields and --catchup-dry-run
        # use every gateway unless --source is given.
        self.all_stn_dict = stn_dict
        self.source_names = []
        if stn_dict.get('sources'):
            configs = source_configs(stn_dict)
            self.source_names = [n for n, _ in configs]
            wanted = getattr(namespace, 'source', None) or configs[0][0]
            chosen = dict(configs).get(wanted)
            if chosen is None:
                raise weewx.ViolatedPrecondition(f"--source={wanted}: no such gateway in [[sources]] "
                                                 f"({', '.join(self.source_names)})")
            stn_dict = dict(chosen)
        elif getattr(namespace, 'source', None):
            raise weewx.ViolatedPrecondition(f'--source={namespace.source}: there is no [[sources]] '
                                             f'section, the driver uses one gateway')
        # the cumulative fields merged as totals, with several gateways
        self.counters = kwargs.get('counters')
        # save the WeeWX station dict (the chosen gateway's, with several)
        self.stn_dict = stn_dict
        self.unit_system = DEFAULT_UNIT_SYSTEM
        self.discovery_port = kwargs.get('discovery_port',
                                         DEFAULT_DISCOVERY_PORT)
        self.discovery_period = kwargs.get('discovery_period',
                                           DEFAULT_DISCOVERY_PERIOD)
        self.discovery_timeout = kwargs.get('discovery_timeout',
                                            DEFAULT_DISCOVERY_TIMEOUT)

        self.ip_address = self.ip_from_config_opts()
        self.show_battery, source = self.bool_from_config('show_battery',
                                                          DEFAULT_FILTER_BATTERY)
        if weewx.debug >= 1:
            _state_str = 'disabled' if self.show_battery else 'enabled'
            if source == 'default':
                _src_str = 'using the default'
            elif source == 'station':
                _src_str = 'obtained from station config'
            elif source == 'command':
                _src_str = 'obtained from command line options'
            else:
                # we should never end up here but just in case...
                _src_str = 'unknown config source'
            print(f'Battery state filtering is {_state_str} ({_src_str})')
        self.only_registered, source = self.bool_from_config('only_registered',
                                                             DEFAULT_ONLY_REGISTERED_SENSORS)
        if weewx.debug >= 1:
            _state_str = 'disabled' if not self.only_registered else 'enabled'
            if source == 'default':
                _src_str = 'using the default'
            elif source == 'station':
                _src_str = 'obtained from station config'
            elif source == 'command':
                _src_str = 'obtained from command line options'
            else:
                # we should never end up here but just in case...
                _src_str = 'unknown config source'
            if self.only_registered:
                _reg_str = "Only registered sensors will be shown"
            else:
                _reg_str = "All sensors will be shown"
            print(f'{_reg_str} ({_src_str})')
        # [StdArchive] archive_interval (seconds), for --catchup-dry-run
        self.archive_interval = kwargs.get('archive_interval')
        # set our debug level
        # the driver's debug options ([EcowittGateway] debug = ...); the --debug level itself has already
        # been put in weewx.debug. (This was the --debug number, or None, which the parser then failed on
        # with 'NoneType' object has no attribute 'parser' at the first field it did not know.)
        self.driver_debug = DebugOptions(**stn_dict)

    def ip_from_config_opts(self):
        """Obtain the IP address from station config or command line options.

        Determine the IP address to use given a station config dict and command
        line options. The IP address is chosen in order as follows:
        - if specified the command line IP address is used
        - if a command line IP address was not specified the IP address is
          obtained from the station config dict
        - if the station config dict does not specify an IP address the value
          None is returned

        Returns a string or the value None.
        """

        ip_address = self.namespace.ip_address if self.namespace.ip_address else None
        if ip_address is None:
            ip_address = self.stn_dict.get('ip_address')
            # if the station config dict specifies some variation of 'auto'
            # then we need to return None to force device discovery
            if ip_address is not None:
                if weewx.debug >= 1:
                    print()
                    print('IP address obtained from station config')
            elif weewx.debug >= 1:
                print()
                print('IP address not specified')
        else:
            if weewx.debug >= 1:
                print()
                print('IP address obtained from command line options')
        return ip_address

    def bool_from_config(self, option, default):
        """Obtain a boolean option from the command line, station config or default.

        Determine an option value as follows:
        - if specified use the option from the command line
        - if the option was not specified on the command line obtain the option
          value from the station config dict
        - if the station config dict does not specify the option use the
          default value
        """

        # get the option value, default to None if the option does not exist
        _opt = getattr(self.namespace, option, None)
        # if the option exists and it is not None
        if _opt is not None:
            try:
                _opt_bool = weeutil.weeutil.tobool(_opt)
            except ValueError:
                # we could not get the option from the command line
                pass
            else:
                return _opt_bool, 'command'
        # we couldn't get the option fromm the command line, now try the
        # station config
        try:
            _opt_bool = weeutil.weeutil.tobool(self.stn_dict.get(option))
        except ValueError:
            # we could not get the option from the station config
            pass
        else:
            return _opt_bool, 'station'
        # we could not get the option from the command line or station
        # config so use the default
        return default, 'default'

    def get_device(self):
        """Get an EcowittDevice object.

        Attempts to obtain an EcowittDevice object. If successful an
        EcowittDevice instance is returned, otherwise the value None is
        returned.
        """

        device = None
        try:
            # get an EcowittDevice object
            # the command line, else [EcowittGateway], else the default ('weectl device' has no
            # defaults for these options)
            def setting(option, key, default):
                value = getattr(self.namespace, option, None)
                return value if value is not None else weeutil.weeutil.to_int(self.stn_dict.get(key, default))

            device = make_device(self.ip_address,
                                 api=self.api(),
                                 tcp_port=weeutil.weeutil.to_int(self.stn_dict.get('tcp_port',
                                                                                   DEFAULT_TCP_PORT)),
                                 max_tries=setting('max_tries', 'max_tries', DEFAULT_MAX_TRIES),
                                 retry_wait=setting('retry_wait', 'retry_wait', DEFAULT_RETRY_WAIT),
                                 url_timeout=setting('timeout', 'url_timeout', DEFAULT_URL_TIMEOUT),
                                 unit_system=self.unit_system,
                                 debug=self.driver_debug)
        except weewx.ViolatedPrecondition as e:
            print()
            print(f'Unable to obtain EcowittDevice object: {e}')
        except DeviceIOError as e:
            print()
            print(f'Unable to connect to device at {self.ip_address}: {e}')
        except socket.timeout:
            print()
            print(f'Timeout. Device at {self.ip_address} did not respond.')
        return device

    def process_options(self):
        """Call the appropriate method based on the optparse options."""

        # run the driver
        if hasattr(self.namespace, 'test_driver') and self.namespace.test_driver:
            self.test_driver()
        # run the service with simulator
        elif hasattr(self.namespace, 'test_service') and self.namespace.test_service:
            self.test_service()
        elif hasattr(self.namespace, 'weewx_fields') and self.namespace.weewx_fields:
            self.weewx_fields()
        elif hasattr(self.namespace, 'sys_params') and self.namespace.sys_params:
            self.display_system_params()
        elif hasattr(self.namespace, 'rain_totals') and self.namespace.rain_totals:
            self.display_rain_totals()
        elif hasattr(self.namespace, 'mulch_offset') and self.namespace.mulch_offset:
            self.display_mulch_offset()
        elif hasattr(self.namespace, 'temp_calibration') and self.namespace.temp_calibration:
            self.display_mulch_t_offset()
        elif hasattr(self.namespace, 'pm25_offset') and self.namespace.pm25_offset:
            self.display_pm25_offset()
        elif hasattr(self.namespace, 'co2_offset') and self.namespace.co2_offset:
            self.display_co2_offset()
        elif hasattr(self.namespace, 'lds_offset') and self.namespace.lds_offset:
            self.display_lds_offset()
        elif hasattr(self.namespace, 'calibration') and self.namespace.calibration:
            self.display_calibration()
        elif hasattr(self.namespace, 'soil_calibration') and self.namespace.soil_calibration:
            self.display_soil_calibration()
        elif hasattr(self.namespace, 'services') and self.namespace.services:
            self.display_services()
        elif hasattr(self.namespace, 'mac') and self.namespace.mac:
            self.display_mac()
        elif hasattr(self.namespace, 'firmware') and self.namespace.firmware:
            self.display_firmware()
        elif hasattr(self.namespace, 'sensors') and self.namespace.sensors:
            self.display_sensors()
        elif getattr(self.namespace, 'sensor_map', False):
            self.display_sensor_map()
        elif getattr(self.namespace, 'active_map', False):
            self.display_active_field_map()
        elif getattr(self.namespace, 'dump_api', False):
            self.dump_api()
        elif getattr(self.namespace, 'sd_files', False):
            self.display_sd_files()
        elif getattr(self.namespace, 'catchup_dry_run', False):
            self.catchup_dry_run()
        elif getattr(self.namespace, 'sources', False):
            self.display_sources()
        elif hasattr(self.namespace, 'live') and self.namespace.live:
            self.display_live_data()
        elif hasattr(self.namespace, 'discover') and self.namespace.discover:
            self.display_discovered_devices()
        elif hasattr(self.namespace, 'map') and self.namespace.map:
            self.display_field_map()
        elif hasattr(self.namespace, 'driver_map') and self.namespace.driver_map:
            self.display_driver_field_map()
        elif hasattr(self.namespace, 'service_map') and self.namespace.service_map:
            self.display_service_field_map()
        else:
            print()
            print('No option selected, nothing done')
            print()
            self.arg_parser.print_help()
            return

    def display_system_params(self):
        """Display system parameters.

        Obtain and display the device system parameters.
        """

        # dict for decoding system parameters frequency byte, at present all we
        # know is 0 = 433MHz
        freq_decode = {
            0: '433MHz',
            1: '868Mhz',
            2: '915MHz',
            3: '920MHz'
        }
        afc_decode = {
            0: 'off',
            1: 'on'
        }
        # as of GW3000 firmware v1.0.3/WS View+ v2.0.53 TZ auto decode is shown
        # as 'off' in WS View+ but the device HTTP API returns '1' for tz_auto
        tz_auto_decode = {
            0: 'on',
            1: 'off'
        }
        temp_comp_decode = dst_decode = afc_decode
        upgrade_decode = ap_auto_decode  = afc_decode
        sensor_decode = {0: 'WH24',
                         1: 'WH65'}
        device = self.get_device()
        if device is not None:
            print()
            try:
                print(f'Interrogating {bcolors.BOLD}{device.model}{bcolors.ENDC} '
                      f'at {bcolors.BOLD}{device.ip_address}{bcolors.ENDC}')
                # get the device system parameters, wrap in a try..except to catch
                # any exceptions
                device_info = device.get_device_info_data()
            except DeviceIOError as e:
                print()
                print(f'Error obtaining a response from the device: {e}')
            except ParseError as e:
                print()
                print(f'Error parsing device response: {e}')
            except Exception as e:
                raise
            else:
                afc = device_info.get('afc')
                temp_comp = device_info.get('rad_comp')
                tz_auto = device_info.get('tz_auto')
                dst_status = device_info.get("dst")
                upgrade = device_info.get('upgrade')
                ap_auto = device_info.get('ap_auto')
                freq_str = freq_decode.get(device_info.get('rf_freq'), 'Unknown')
                # If the sensor_type field is 0 there is a WH24 connected, if it's
                # a 1 there is a WH65, if the sensor_type field is missing it's
                # unknown
                _type = device_info.get('sensor_type') if device_info.get('sensor_type') is not None else None
                _type_str = sensor_decode.get(_type) if sensor_decode.get(_type) is not None else 'unknown'
                print()
                print(f'{"sensor type":>35}: {device_info.get("sensor_type")} ({_type_str})')
                print(f'{"frequency":>35}: {device_info.get("rf_freq")} ({freq_str})')
                if afc is not None:
                    print(f'{"automatic frequency control (AFC)":>35}: {afc} '
                          f'({afc_decode.get(afc, "unknown")})')
                else:
                    print(f'{"automatic frequency control (AFC)":>35}: unavailable')
                if temp_comp is not None:
                    print(f'{"temperature compensation":>35}: {temp_comp} '
                          f'({temp_comp_decode.get(temp_comp, "unknown")})')
                else:
                    print(f'{"temperature compensation":>35}: unavailable')
                if tz_auto is not None:
                    print(f'{"auto timezone":>35}: {tz_auto} '
                          f'({tz_auto_decode.get(tz_auto, "unknown")})')
                else:
                    print(f'{"auto timezone":>35}: unavailable')
                _tz_name = device_info.get("tz_name", "name not provided")
                tz_name = _tz_name if len(_tz_name) > 0 else "name not provided"
                print(f'{"timezone index":>35}: {device_info.get("tz_index")} ({tz_name})')
                print(f'{"DST status":>35}: {dst_status} '
                      f'({dst_decode.get(dst_status, "unknown")})')
                # The HTTP API returns 'date' as a date-time string, but it is
                # parsed to an epoch timestamp. Format the timestamp as a string.
                date_time_str = time.strftime("%-d %B %Y %H:%M:%S",
                                              time.localtime(device_info['date']))
                print(f'{"date-time":>35}: {date_time_str}')
                if upgrade is not None:
                    print(f'{"auto upgrade":>35}: {upgrade} '
                          f'({upgrade_decode.get(upgrade, "unknown")})')
                else:
                    print(f'{"auto upgrade":>35}: unavailable')
                if ap_auto is not None:
                    print(f'{"device AP auto off":>35}: {ap_auto} '
                          f'({ap_auto_decode.get(ap_auto, "unknown")})')
                else:
                    print(f'{"device AP auto off":>35}: unavailable')
                print(f'{"device AP SSID":>35}: {device_info.get("ap")}')

    def display_rain_totals(self):
        """Display the device rain gauge rain totals and rain settings.

        Obtain and display rainfall priority and traditional and piezo rain
        gauge totals and gains. The displayed data is obtained via the
        'get_rain_totals' and 'get_piezo_rain' HTTP API commands.
        """

        device = self.get_device()
        if device is not None:
            print()
            data = self.fetch(device, 'get_rain_totals', 'get_piezo_rain_data', 'get_device_units')
            if data is not None:
                rain_totals_data, rain_piezo_data, device_units = data
                c = weewx.units.StdUnitConverters[weewx.METRICWX]
                # now get a formatter, the default string formats and labels will
                # be fine
                f = weewx.units.Formatter(unit_format_dict=weewx.defaults.defaults['Units']['StringFormats'],
                                          unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
                gain_label = dict()
                if device_units.get('rain') == 'inch':
                    units = ('inch', 'mm')
                    gain_label[1] = '(< 0.16 in/hr / < 4 mm/hr)'
                    gain_label[2] = '(< 0.39 in/hr / < 10 mm/hr)'
                    gain_label[3] = '(< 1.18 in/hr / < 30 mm/hr)'
                    gain_label[4] = '(< 2.36 in/hr / < 60 mm/hr)'
                    gain_label[5] = '(> 2.36 in/hr / > 60 mm/hr)'
                else:
                    units = ('mm', 'inch')
                    gain_label[1] = '(< 4 mm/hr / < 0.16 in/hr)'
                    gain_label[2] = '(< 10 mm/hr / < 0.39 in/hr)'
                    gain_label[3] = '(< 30 mm/hr / < 1.18 in/hr)'
                    gain_label[4] = '(< 60 mm/hr / < 2.36 in/hr)'
                    gain_label[5] = '(> 60 mm/hr / > 2.36 in/hr)'
                # get priority rainfall data gauge name
                gauge_name = 'Unable to determine rainfall data priority'
                # the device does not always send a gauge list (the parser leaves it out then)
                for gauge in rain_totals_data.get('rain_list', []):
                    if gauge.get('value') == rain_totals_data.get('rain_priority'):
                        gauge_name = gauge['gauge']
                print()
                print(f'Rainfall data priority: {gauge_name} ({rain_totals_data.get("rain_priority")})')
                print()
                def show_total(label, data, key):
                    # a total the device left out is shown as '---'
                    if data.get(key) is None or data[key].value is None:
                        print(f'{label:>15}: ---')
                        return
                    _vh = weewx.units.ValueHelper(data[key], formatter=f, converter=c)
                    print(f'{label:>15}: {_vh.convert(units[0]).toString()} ({_vh.convert(units[1]).toString()})')

                print(f'  Traditional gauge rain data:')
                for label, key in (('Day rain', 'day_rain'), ('Week rain', 'week_rain'),
                                   ('Month rain', 'month_rain'), ('Year rain', 'year_rain')):
                    show_total(label, rain_totals_data, key)
                _data = rain_totals_data.get('rain_gain')
                _data_str = f'{_data:.2f}' if _data is not None else '---'
                print(f'{"Rain gain":>15}: {_data_str}')
                print()
                print(f'  Piezo gauge rain data:')
                for label, key in (('Day rain', 'day_rain'), ('Week rain', 'week_rain'),
                                   ('Month rain', 'month_rain'), ('Year rain', 'year_rain')):
                    show_total(label, rain_piezo_data, key)
                for gain_channel in range(5):
                    gain_field = f"gain{gain_channel + 1:d}"
                    _data = rain_piezo_data.get(gain_field)
                    _data_str = f'{_data:.2f} {gain_label[gain_channel + 1]}' if _data is not None \
                        else f'-- {gain_label[gain_channel + 1]}'
                    _label_str = f'Rain{gain_channel + 1:d} gain'
                    print(f'{_label_str:>15}: {_data_str}')
                print()
                print(f'  Rainfall reset times:')
                _data = rain_totals_data.get('rain_reset_day')
                _data_str = f'{_data:02d}:00' if _data is not None else '-----'
                print(f'{"Daily rainfall reset time":>30}: {_data_str}')
                _data = rain_totals_data.get('rain_reset_week')
                _data_str = f'{calendar.day_name[(_data + 6) % 7]}' if _data is not None else '-----'
                print(f'{"Weekly rainfall reset":>30}: {_data_str}')
                _data = rain_totals_data.get('rain_reset_year')
                _data_str = f'{calendar.month_name[_data + 1]}' if _data is not None else '-----'
                print(f'{"Annual rainfall reset":>30}: {_data_str}')

    def display_mulch_offset(self):
        """Display device multichannel temperature and humidity offset data.

        Obtain and display the multichannel temperature and humidity offset
        data from the selected device.
        """

        device = self.get_device()
        if device is not None:
            print()
            data = self.fetch(device, 'get_multich_calibration_data', 'get_device_units')
            if data is not None:
                mulch_offset_data, device_units = data
                if mulch_offset_data is not None:
                    c = weewx.units.StdUnitConverters[weewx.METRICWX]
                    # now get a formatter, the default string formats and labels will
                    # be fine
                    f = weewx.units.Formatter(unit_format_dict=weewx.defaults.defaults['Units']['StringFormats'],
                                              unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
                    if device_units['group_deltat'] == 'degree_F2':
                        dt_units = ['degree_F2', 'degree_C2']
                    else:
                        dt_units = ['degree_C2', 'degree_F2']
                    print()
                    print('Multi-channel Temperature and Humidity Calibration')
                    if len(mulch_offset_data) > 0:
                        for sensor in mulch_offset_data:
                            channel_str = f'{"Channel":>11} {sensor["channel"]:d}'
                            _vh = weewx.units.ValueHelper(sensor["temp"], formatter=f, converter=c)
                            print(f'{channel_str:>13}: {"Temperature offset":>18}: '
                                  f'{_vh.convert(dt_units[0]).toString()} '
                                  f'({_vh.convert(dt_units[1]).toString()})')
                            _vh = weewx.units.ValueHelper(sensor["humi"], formatter=f, converter=c)
                            print(f'{"":>13}  {"Humidity offset":>18}: {_vh.toString()}')
                    else:
                        print(f'{"No Multi-channel temperature and humidity sensors found":>59}')
                else:
                    print()
                    print(f'Device at {self.ip_address} did not respond.')

    def display_mulch_t_offset(self):
        """Display device multichannel temperature (WN34) offset data.

        Obtain and display the multichannel temperature (WN34) offset data from
        the selected device.
        """

        device = self.get_device()
        if device is not None:
            print()
            data = self.fetch(device, 'get_wn34_offset_data', 'get_device_units')
            if data is not None:
                wn34_offset_data, device_units = data
                if wn34_offset_data is not None:
                    c = weewx.units.StdUnitConverters[weewx.METRICWX]
                    # now get a formatter, the default string formats and labels will
                    # be fine
                    f = weewx.units.Formatter(unit_format_dict=weewx.defaults.defaults['Units']['StringFormats'],
                                              unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
                    if device_units['group_deltat'] == 'degree_F2':
                        dt_units = ['degree_F2', 'degree_C2']
                    else:
                        dt_units = ['degree_C2', 'degree_F2']
                    print()
                    print('Multi-channel Temperature Calibration')
                    if len(wn34_offset_data) > 0:
                        for sensor in wn34_offset_data:
                            # Print the channel and offset data. The API returns
                            # channels starting at 0x63, but the WSView Plus app
                            # displays channels starting at 1, so subtract 0x62
                            # (or 98) from our channel number
                            channel_str = f'{"Channel":>11} {sensor["channel"]:d}'
                            _vh = weewx.units.ValueHelper(sensor['temp'], formatter=f, converter=c)
                            print(f'{channel_str:>13}: {"Temperature offset":>18}: '
                                  f'{_vh.convert(dt_units[0]).toString()} '
                                  f'({_vh.convert(dt_units[1]).toString()})')
                    else:
                        print(f'{"No Multi-channel temperature sensors found":>46}')
                    print()
                else:
                    print()
                    print(f'Device at {self.ip_address} did not respond.')
                    print()

    def display_pm25_offset(self):
        """Display the device PM2.5 offset data.

        Obtain and display the PM2.5 offset data from the selected device.
        """

        device = self.get_device()
        if device is not None:
            print()
            data = self.fetch(device, 'get_pm25_offset_data')
            if data is not None:
                pm25_offset_data, = data
                if pm25_offset_data is not None:
                    c = weewx.units.StdUnitConverters[weewx.METRICWX]
                    # now get a formatter, the default string formats and labels will
                    # be fine
                    f = weewx.units.Formatter(unit_format_dict=weewx.defaults.defaults['Units']['StringFormats'],
                                              unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
                    if len(pm25_offset_data) > 0:
                        print()
                        print('PM2.5 Calibration')
                        for sensor in pm25_offset_data:
                            channel_str = f'{"Channel":>11} {sensor["channel"]:d}'
                            _vh = weewx.units.ValueHelper(sensor['val'], formatter=f, converter=c)
                            print(f'{channel_str:>13}: {"PM2.5 offset":>15}: '
                                  f'{_vh.toString()}')
                    else:
                        print(f'{"No PM2.5 sensors found":>26}')
                    print()
                else:
                    print()
                    print(f'Device at {self.ip_address} did not respond.')
                    print()

    def display_co2_offset(self):
        """Display the device WH45 CO2, PM10 and PM2.5 offset data.

        Obtain and display the WH45 CO2, PM10 and PM2.5 offset data from the
        selected device.
        """

        device = self.get_device()
        if device is not None:
            print()
            data = self.fetch(device, 'get_co2_offset_data')
            if data is not None:
                co2_offset_data, = data
                if co2_offset_data is not None:
                    c = weewx.units.StdUnitConverters[weewx.METRICWX]
                    # Now get a formatter, we could use the default formatter, but
                    # we need microgram_per_meter_cubed formatted with one decimal
                    # places. So create a custom formatter using a modified
                    # defaults StringFormats stanza and the defaults Labels stanza.
                    # obtain a copy of the defaults StringFormats stanza
                    unit_format_dict = dict(weewx.defaults.defaults['Units']['StringFormats'])
                    unit_format_dict['microgram_per_meter_cubed'] = '%.1f'
                    f = weewx.units.Formatter(unit_format_dict=unit_format_dict,
                                              unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
                    print()
                    print('CO2 Calibration')
                    _vh = weewx.units.ValueHelper(co2_offset_data['co2'],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"CO2 offset":>16}: {_vh.toString()}')
                    if co2_offset_data.get("pm1") is not None:
                        _vh = weewx.units.ValueHelper(co2_offset_data['pm1'],
                                                      formatter=f,
                                                      converter=c)
                        print(f'{"PM1 offset":>16}: {_vh.toString()}')
                    _vh = weewx.units.ValueHelper(co2_offset_data['pm25'],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"PM2.5 offset":>16}: {_vh.toString()}')
                    if co2_offset_data.get("pm4") is not None:
                        _vh = weewx.units.ValueHelper(co2_offset_data['pm4'],
                                                      formatter=f,
                                                      converter=c)
                        print(f'{"PM4 offset":>16}: {_vh.toString()}')
                    _vh = weewx.units.ValueHelper(co2_offset_data['pm10'],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"PM10 offset":>16}: {_vh.toString()}')
                else:
                    print()
                    print(f'Device at {self.ip_address} did not respond.')
                print()

    def display_lds_offset(self):
        """Display the device WH54 LDS offset data.

        Obtain and display the WH54 LDS offset and calibration data from the
        selected device.
        """

        device = self.get_device()
        if device is not None:
            print()
            data = self.fetch(device, 'get_lds_offset_data', 'get_device_units')
            if data is not None:
                lds_offset_data, device_units = data
                if lds_offset_data is not None:
                    if len(lds_offset_data) > 0:
                        c = weewx.units.StdUnitConverters[weewx.METRICWX]
                        # now get a formatter, the default string formats and labels will
                        # be fine
                        f = weewx.units.Formatter(unit_format_dict=weewx.defaults.defaults['Units']['StringFormats'],
                                                  unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
                        if device_units['group_depth'] == 'foot2':
                            units = ['foot2', 'mm2']
                        else:
                            units = ['mm2', 'foot2']
                        print()
                        print('LDS Calibration')
                        for sensor in lds_offset_data:
                            _name = sensor.get("name")
                            if _name is not None and len(_name) > 0:
                                _name_str = f' ({_name})'
                            else:
                                _name_str = ''
                            print(f'    Channel {sensor["channel"]:d}{_name_str}')
                            _vh = weewx.units.ValueHelper(sensor['offset'],
                                                          formatter=f,
                                                          converter=c)
                            print(f'{"Offset":>15}: {_vh.convert(units[0]).toString()} '
                                  f'({_vh.convert(units[1]).toString()})')
                            _vh = weewx.units.ValueHelper(sensor['total_height'],
                                                          formatter=f,
                                                          converter=c)
                            print(f'{"Height":>15}: {_vh.convert(units[0]).toString()} '
                                  f'({_vh.convert(units[1]).toString()})')
                            print(f'{"Heat":>15}: {sensor["total_heat"]:d}')
                            print(f'{"Level":>15}: {sensor["level"]:d}')
                    else:
                        print(f'{"No LDS sensors found":>26}')
                    print()
                else:
                    print()
                    print(f'Device at {self.ip_address} did not respond.')
                print()

    def display_calibration(self):
        """Display the device calibration data.

        Obtain and display the calibration data from the selected device.
        """

        device = self.get_device()
        if device is not None:
            print()
            data = self.fetch(device, 'get_calibration_data', 'get_device_units')
            if data is not None:
                calibration_data, device_units = data
                if calibration_data is not None:
                    c = weewx.units.StdUnitConverters[weewx.METRICWX]
                    # now get a formatter, the default string formats and labels will
                    # be fine
                    f = weewx.units.Formatter(unit_format_dict=weewx.defaults.defaults['Units']['StringFormats'],
                                              unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
                    if device_units['group_deltat'] == 'degree_F2':
                        dt_units = ['degree_F2', 'degree_C2']
                    else:
                        dt_units = ['degree_C2', 'degree_F2']
                    if device_units['group_pressure'] == 'mmHg':
                        p_units = ['mmHg', 'hPa', 'inHg']
                    elif device_units['group_pressure'] == 'inHg':
                        p_units = ['inHg', 'hPa', 'mmHg']
                    else:
                        p_units = ['hPa', 'inHg', 'mmHg']
                    if device_units['group_altitude'] == 'foot':
                        a_units = ['foot', 'meter']
                    else:
                        a_units = ['meter', 'foot']
                    print()
                    print('Calibration')
                    print(f'{"Irradiance gain":>26}: {calibration_data["solar_gain"]:.2f}')
                    print(f'{"UV gain":>26}: {calibration_data["uv_gain"]:.2f}')
                    print(f'{"Wind gain":>26}: {calibration_data["wind_gain"]:.2f}')
                    _vh = weewx.units.ValueHelper(calibration_data["intemp_offset"],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"Inside temperature offset":>26}: {_vh.convert(dt_units[0]).toString()} '
                          f'({_vh.convert(dt_units[1]).toString()})')
                    _vh = weewx.units.ValueHelper(calibration_data["inhumid_offset"],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"Inside temperature offset":>26}: {_vh.toString()}')
                    _vh = weewx.units.ValueHelper(calibration_data["outtemp_offset"],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"Outside temperature offset":>26}: {_vh.convert(dt_units[0]).toString()} '
                          f'({_vh.convert(dt_units[1]).toString()})')
                    _vh = weewx.units.ValueHelper(calibration_data["outhumid_offset"],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"Outside temperature offset":>26}: {_vh.toString()}')
                    _vh = weewx.units.ValueHelper(calibration_data["abs_offset"],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"Absolute pressure offset":>26}: {_vh.convert(p_units[0]).toString()} '
                          f'({_vh.convert(p_units[1]).toString()}/{_vh.convert(p_units[2]).toString()})')
                    # older firmware uses a simple relative pressure calibration
                    # procedure that uses a fixed offset, check if we have a fixed
                    # offset in key value 'rel_offset'
                    if 'rel_offset' in calibration_data:
                        _vh = weewx.units.ValueHelper(calibration_data["rel_offset"],
                                                      formatter=f,
                                                      converter=c)
                        print(f'{"Relative pressure offset":>26}: {_vh.convert(p_units[0]).toString()} '
                              f'({_vh.convert(p_units[1]).toString()}/{_vh.convert(p_units[2]).toString()})')
                    # newer firmware (eg GW3000 v1.0.5 and later) uses an altitude
                    # based pressure calibration procedure that uses an altitude,
                    # check if we have an 'altitude' key value
                    if 'altitude' in calibration_data:
                        _vh = weewx.units.ValueHelper(calibration_data["altitude"],
                                                      formatter=f,
                                                      converter=c)
                        print(f'{"Altitude for REL":>26}: {_vh.convert(a_units[0]).toString()} '
                              f'({_vh.convert(a_units[1]).toString()})')
                    _vh = weewx.units.ValueHelper(calibration_data["winddir_offset"],
                                                  formatter=f,
                                                  converter=c)
                    print(f'{"Wind direction offset":>26}: {_vh.toString(useThisFormat="%d")}')
                else:
                    print()
                    print(f'Device at {self.ip_address} did not respond.')
                print()

    def display_soil_calibration(self):
        """Display the device soil moisture sensor calibration data.

        Obtain and display the soil moisture sensor calibration data from the
        selected device.
        """

        device = self.get_device()
        if device is not None:
            print()
            data = self.fetch(device, 'get_soil_calibration_data')
            if data is not None:
                calibration_data, = data
                if calibration_data is not None:
                    print()
                    print('Soil Calibration')
                    for sensor in calibration_data:
                        print(f'    Channel {sensor["channel"]:d} ({sensor["soilVal"]:d}%)')
                        print(f'{"Now AD":>16}: {sensor["nowAd"]:d}')
                        print(f'{"0% AD":>16}: {sensor["minVal"]:d}')
                        print(f'{"100% AD":>16}: {sensor["maxVal"]:d}')
                else:
                    print()
                    print(f'Device at {self.ip_address} did not respond.')
                print()

    def display_services(self):
        """Display the device Weather Services settings.

        Obtain and display the settings for the various weather services
        supported by the device. Firmware versions differ in what they report,
        so a setting the device leaves out ends the list with a note rather
        than a traceback.
        """

        try:
            self._display_services()
        except KeyError as e:
            print()
            print(f'The device did not report the {e} setting; the rest of the weather '
                  f'services settings cannot be shown.')

    def _display_services(self):
        """Display the device Weather Services settings (see display_services())."""

        device = self.get_device()
        if device is not None:
            print()
            # obtain an EcowittDevice object and fetch the weather services data
            # from the device, wrap in a try..except in case there is an error
            data = self.fetch(device, 'get_ws_settings')
            if data is not None:
                services_data, = data
                if len(services_data) > 0:
                    # we did, now format and display the data
                    print()
                    print('Weather Services')
                    print()

                    # Ecowitt.net
                    print('  Ecowitt.net')
                    # upload interval, 0 means disabled, otherwise it's the number
                    # of minutes
                    if services_data['ost_interval'] == 0:
                        print(f'{"Upload Interval":>22}: Upload to Ecowitt.net is disabled')
                    elif services_data['ost_interval'] > 1:
                        print(f'{"Upload Interval":>22}: {services_data["ost_interval"]:d} minutes')
                    else:
                        print(f'{"Upload Interval":>22}: {services_data["ost_interval"]:d} minute')
                    # device MAC
                    print(f'{"MAC":>22}: {services_data["sta_mac"]}')
                    print()

                    # Weather Underground
                    print('  Wunderground')
                    # upload interval
                    interval = services_data['wu_interval']
                    if interval is not None:
                        if interval == 0:
                            interval_str = '0 minutes (disabled)'
                        elif interval == 1:
                            interval_str = '1 minute'
                        elif 1 < interval < 16:
                            interval_str = f'{interval:d} minutes'
                        elif interval >= 16:
                            interval_str = f'{interval:d} seconds'
                        else:
                            interval_str = '--'
                    else:
                        interval_str = '--'
                    print(f'{"Upload Interval":>22}: {interval_str}')
                    # Station ID, obfuscate part of the Station ID unless told not
                    # to
                    wu_id = services_data['wu_id'] if self.namespace.unmask \
                        else obfuscate(services_data['wu_id'])
                    print(f'{"Station ID":>22}: {wu_id}')
                    # Station key, obfuscate part of the Station key unless told
                    # not to
                    wu_key = services_data['wu_key'] if self.namespace.unmask \
                        else obfuscate(services_data['wu_key'])
                    print(f'{"Station Key":>22}: {wu_key}')
                    print()

                    # Weather Cloud
                    print('  Weathercloud')
                    # upload interval in minutes
                    interval = services_data['wcl_interval']
                    if interval is not None:
                        if interval == 0:
                            interval_str = f'{interval:d} minutes (disabled)'
                        elif interval > 1:
                            interval_str = f'{interval:d} minutes'
                        elif interval == 1:
                            interval_str = '1 minute'
                        else:
                            interval_str = '--'
                    else:
                        interval_str = '--'
                    print(f'{"Upload Interval":>22}: {interval_str}')
                    # Weathercloud ID (WSView+ refers to it as Station ID),
                    # obfuscate part of the Weathercloud ID unless told not to
                    wcl_id = services_data['wcl_id'] if self.namespace.unmask \
                        else obfuscate(services_data['wcl_id'])
                    print(f'{"Station ID":>22}: {wcl_id}')
                    # Weathercloud key (WSView+ refers to it as Station key),
                    # obfuscate part of the Weathercloud ID unless told not to
                    wcl_key = services_data['wcl_key'] if self.namespace.unmask \
                        else obfuscate(services_data['wcl_key'])
                    print(f'{"Station Key":>22}: {wcl_key}')
                    print()

                    # Weather Observations Website
                    # upload interval in minutes
                    print('  Weather Observations Website')
                    interval = services_data['wow_interval']
                    if interval is not None:
                        if interval == 0:
                            interval_str = f"{interval:d} minutes (disabled)"
                        elif interval > 1:
                            interval_str = f"{interval:d} minutes"
                        elif interval == 1:
                            interval_str = "1 minute"
                        else:
                            interval_str = "--"
                    else:
                        interval_str = "--"
                    print(f'{"Upload Interval":>22}: {interval_str}')
                    # Station ID, obfuscate part of the Station ID unless told not
                    # to
                    wow_id = services_data['wow_id'] if self.namespace.unmask \
                        else obfuscate(services_data['wow_id'])
                    print(f'{"Station ID":>22}: {wow_id}')
                    # Station key, obfuscate part of the Station key unless told
                    # not to
                    wow_key = services_data['wow_key'] if self.namespace.unmask \
                        else obfuscate(services_data['wow_key'])
                    print(f'{"Station Key":>22}: {wow_key}')
                    print()

                    # Customised
                    print('  Customized')
                    # Is upload enabled, WSView+ only displays enabled/disabled,
                    # but include 'unknown' just in case
                    if services_data['Customized'] is None:
                        print(f'{"Upload":>22}: {"Unknown"}')
                    elif services_data['Customized']:
                        print(f'{"Upload":>22}: {"Enabled"}')
                    else:
                        print(f'{"Upload":>22}: {"Disabled"}')
                    # upload protocol, WSView+ only displays Ecowitt, Wunderground
                    # and MQTT, but include 'unknown' just in case
                    if services_data['Protocol'].lower() in ('ecowitt', 'wunderground'):
                        if services_data['Protocol'].lower() == 'ecowitt':
                            print(f'{"Upload Protocol":>22}: {"Ecowitt"}')
                            remote_server = services_data['ecowitt_ip']
                            path = services_data['ecowitt_path']
                            port_str = f"{services_data['ecowitt_port']:d}"
                            interval_str = f"{services_data['ecowitt_upload']:d}"
                        elif services_data['Protocol'].lower() == 'wunderground':
                            print(f'{"Upload Protocol":>22}: {"Wunderground"}')
                            remote_server = services_data['usr_wu_ip']
                            path = services_data['usr_wu_path']
                            port_str = f"{services_data['usr_wu_port']:d}"
                            interval_str = f"{services_data['usr_wu_upload']:d}"
                        else:
                            print(f'{"Upload Protocol":>22}: {"Unknown"}')
                            remote_server = "Unknown"
                            path = "Unknown"
                            port_str = "Unknown"
                            interval_str = "Unknown"
                        # remote server IP address
                        print(f'{"Server IP/Hostname":>22}: {remote_server}')
                        # remote server path, if using wunderground protocol we have
                        # Station ID and Station key as well
                        print(f'{"Path":>22}: {path}')
                        # if using the Wunderground protocol then Station ID and
                        # Station key are also available for use
                        if services_data['Protocol'] == 'wunderground':
                            usr_wu_id = services_data['usr_wu_id'] if self.namespace.unmask \
                                else obfuscate(services_data['usr_wu_id'])
                            print(f'{"Station ID":>22}: {usr_wu_id}')
                            usr_wu_key = services_data['usr_wu_key'] if self.namespace.unmask \
                                else obfuscate(services_data['usr_wu_key'])
                            print(f'{"Station Key":>22}: {usr_wu_key}')
                        # port
                        print(f'{"Port":>22}: {port_str}')
                        # upload interval in seconds
                        print(f'{"Upload Interval":>22}: {interval_str}')
                    elif services_data['Protocol'].lower() == 'mqtt':
                            print(f'{"Upload Protocol":>22}: MQTT')
                            print(f'{"Host":>22}: {services_data["mqtt_host"]}')
                            print(f'{"Port":>22}: {services_data["mqtt_port"]:d}')
                            print(f'{"Topic":>22}: {services_data["mqtt_topic"]}')
                            print(f'{"Upload Interval":>22}: {services_data["mqtt_interval"]:d}')
                            print(f'{"Keep Alive":>22}: {services_data["mqtt_keepalive"]:d}')
                            mqtt_name = services_data["mqtt_name"] if self.namespace.unmask \
                                else obfuscate(services_data["mqtt_name"])
                            print(f'{"Client Name":>22}: {mqtt_name}')
                            mqtt_clientid = services_data["mqtt_clientid"] if self.namespace.unmask \
                                else obfuscate(services_data["mqtt_clientid"])
                            print(f'{"Client ID":>22}: {mqtt_clientid}')
                            print(f'{"Username":>22}: {services_data["mqtt_username"]}')
                            mqtt_password = services_data["mqtt_password"] if self.namespace.unmask \
                                else obfuscate(services_data["mqtt_password"])
                            print(f'{"Password":>22}: {mqtt_password}')
                else:
                    # we received no data, the assumption being the device did not
                    # respond
                    print()
                    print(f'Device at {self.ip_address} did not respond.')

    def display_mac(self):
        """Display the device hardware MAC address.

        Obtain and display the hardware MAC address of the selected device.
        """

        device = self.get_device()
        print()
        if device is not None:
            try:
                print(f'Interrogating {bcolors.BOLD}{device.model}{bcolors.ENDC} '
                      f'at {bcolors.BOLD}{device.ip_address}{bcolors.ENDC}')
                print()
                print(f'{"MAC address":>15}: {device.mac_address}')
            except weewx.ViolatedPrecondition as e:
                print()
                print(f'Unable to obtain EcowittDevice object: {e}')
            except DeviceIOError as e:
                print()
                self.print_device_error(e)
            except socket.timeout:
                print()
                print(f'Timeout. Device at {self.ip_address} did not respond.')

    def display_firmware(self):
        """Display device firmware details.

        Obtain and display the firmware version string from the selected
        device. User is advised whether a firmware update is available or not.
        """

        device = self.get_device()
        if device is not None:
            print()
            try:
                model = device.model
                print(f'Interrogating {bcolors.BOLD}{device.model}{bcolors.ENDC} '
                      f'at {bcolors.BOLD}{device.ip_address}{bcolors.ENDC}')
                print()
                # get the firmware version via the API
                model_str = f'installed {model} firmware version'
                print(f'{model_str:>35}: {device.firmware_version}')
                sensor_fw = device.sensor_firmware_versions
                if sensor_fw is not None:
                    for sensor, sensor_fw_ver in sensor_fw.items():
                        sensor_fw_model_str = f"installed {sensor} firmware version"
                        print(f'{sensor_fw_model_str:>35}: {sensor_fw_ver}')
                print()
                fw_update_avail = device.firmware_update_avail
                if fw_update_avail:
                    # we have an available firmware update
                    # obtain the 'curr_msg' from the device HTTP API
                    # 'get_device_info' command, this field usually contains the
                    # firmware change details
                    curr_msg = device.firmware_update_message
                    # now print the firmware update details
                    print(f'    a firmware update is available for this {model}')
                    print(f'    update at http://{self.ip_address} or via the WSView Plus app')
                    # if we have firmware update details print them
                    if curr_msg is not None:
                        print()
                        # Ecowitt have not documented the HTTP API calls so we are
                        # not exactly sure what the 'curr_msg' field is used for,
                        # it might be for other things as well
                        print('    likely firmware update message:')
                        # multi-line messages seem to have \r\n at the end of each
                        # line, split the string so we can format it a little better
                        if '\r\n' in curr_msg:
                            for line in curr_msg.split('\r\n'):
                                # print each line
                                print(f'      {line}')
                        else:
                            print(f'      {curr_msg}')
                    else:
                        # we had no 'curr_msg' for one reason or another
                        print('    no firmware update message found')
                elif fw_update_avail is None:
                    # we don't know if we have an available firmware update
                    print(f'    could not determine if a firmware update is available for this {model}')
                else:
                    # there must be no available firmware update
                    print(f'    the firmware is up to date for this {model}')
            except weewx.ViolatedPrecondition as e:
                print()
                print(f'Unable to obtain EcowittDevice object: {e}')
                print()
                self.device_connection_help()
            except DeviceIOError as e:
                print()
                self.print_device_error(e)
                print()
                self.device_connection_help()
            except socket.timeout:
                print()
                print(f'Timeout. Device at {self.ip_address} did not respond.')
                print()
                self.device_connection_help()

    def display_sensors(self):
        """Display the device sensor ID information.

        Obtain and display the sensor ID information from the selected device.
        Output is provided for all possible sensors unless registered_only is
        True.

        Outputs a formatted summary of sensor metadata for a device, one line
        per sensor.
        """

        def is_disabled(sensor_data):
            """Is the sensor disabled.

            Given a stanza of sensor data determine is a sensor is disabled.

            Returns True or False.
            """

            # a sensor is disabled if the sensor ID == 'fffffffe'
            if sensor_data['id'].lower() == 'fffffffe':
                return True
            return False

        def is_registering(sensor_data):
            """Is the sensor registering.

            Given a stanza of sensor data determine is a sensor is registering.

            Returns True or False.
            """

            # a sensor is registering if the sensor ID == 'ffffffff'
            if sensor_data['id'].lower() == 'ffffffff':
                return True
            return False

        def get_sensor_id_string(sensor_data):
            """Create a sensor ID string based on a sensor ID."""

            if sensor_data is not None and 'id' in sensor_data:
                if is_disabled(sensor_data):
                    # the sensor is disabled
                    id_str = 'sensor is disabled'
                elif is_registering(sensor_data):
                    # the sensor is registering
                    id_str = 'sensor is registering...'
                else:
                    # the sensor is registered, state the sensor ID
                    id_str = f"sensor ID: {sensor_data['id']}"
            else:
                id_str = 'unknown sensor ID'
            return id_str

        def print_sensor(model, sensor_data, channel=''):
            """Print sensor state.

            Print a formatted line of sensor metadata.
            """

            sensor_name_str = ' '.join([model.upper(), channel])
            # create signal strength and battery state text
            if is_disabled(sensor_data) or is_registering(sensor_data):
                # the sensor is disabled or registering so do not provide any
                # signal strength or battery state text
                if getattr(self.namespace, 'registered_only', False):
                    return
                signal_str = ''
                battery_str = ''
            else:
                # the sensor is registered
                # create the signal strength text
                signal_str = f"signal: {sensor_data.get('signal', '--')}"
                # create suitable descriptive battery state text
                desc_str = device.sensors.batt_state_desc(model=model,
                                                          sensor_data=sensor_data)
                volt_str = f" ({sensor_data['voltage']}V)" if 'voltage' in sensor_data else ""
                if sensor_data.get('battery', '--') is None:
                    _batt_str = '--'
                else:
                    _batt_str = sensor_data.get('battery', '--')
                if desc_str is not None:
                    battery_str = f"battery: {_batt_str}{volt_str} ({desc_str})"
                else:
                    battery_str = f"battery: {_batt_str}{volt_str}"
            sensor_id_str = get_sensor_id_string(sensor_data)
            print(f'{sensor_name_str.upper():<10} {sensor_id_str:<25} {signal_str} {battery_str}')

        device = self.get_device()
        if device is not None:
            print()
            try:
                print(f'Interrogating {bcolors.BOLD}{device.model}{bcolors.ENDC} '
                      f'at {bcolors.BOLD}{device.ip_address}{bcolors.ENDC}')
            except weewx.ViolatedPrecondition as e:
                print()
                print(f'Unable to obtain EcowittDevice object: {e}')
            except DeviceIOError as e:
                print()
                self.print_device_error(e)
                print()
                self.device_connection_help()
            except socket.timeout:
                print()
                print(f'Timeout. Device at {self.ip_address} did not respond.')
                print()
                self.device_connection_help()
            else:
                sensors_data = device.get_sensors_data(connected_only=False,
                                                       flatten_data=False)
                live_data = device.get_live_data(flatten_data=True)
                device.sensors.update_sensor_data(all_sensor_data=sensors_data,
                                                  live_data=live_data)
                sensors_metadata = device.sensors.data
                if sensors_metadata is not None and len(sensors_metadata) > 0:
                    # iterate over each sensor model in display order
                    # the usual order first, then any other model the device reported
                    order = list(dict.fromkeys(self.sensor_display_order))
                    order += natural_sort_keys({m: None for m in sensors_metadata if m not in order})
                    for model in order:
                        data = sensors_metadata.get(model)
                        if data is None:
                            continue
                        # do we have a channelised or non-channelised model
                        if 'address' in data.keys():
                            # we have a non-channelised sensor
                            print_sensor(model=model, sensor_data=data)
                        else:
                            # we have a channelised sensor
                            for channel, channel_data in data.items():
                                print_sensor(model=model,
                                             sensor_data=channel_data,
                                             channel=channel)
                elif len(sensors_data) == 0:
                    # we were given no sensor data, inform the user
                    print()
                    print(f'Device at {self.ip_address} did not return any sensor data.')
                else:
                    # the device did not respond, inform the user
                    print()
                    print(f'Device at {self.ip_address} did not respond.')

    def dump_api(self):
        """Save every raw API response as one JSON document.

        The format is the one the test suite's fake gateway reads
        (tests/fixtures), so a dump from a real gateway can be used as test
        data, or sent with a fault report. Passwords, keys and station IDs are
        masked unless --unmask is used."""

        if not self.ip_address:
            print()
            print('No device IP address: use --ip-address or set ip_address in weewx.conf')
            return
        device = self.get_device()
        if device is None:
            return
        unmask = bool(getattr(self.namespace, 'unmask', False))
        result = {'_about': {'driver': f'{DRIVER_NAME} {DRIVER_VERSION}',
                             'model': device.model,
                             'ip_address': self.ip_address,
                             'time': timestamp_to_string(int(time.time())),
                             'secrets_masked': not unmask}}
        print()
        print(f'Reading every API response from {device.model} at {self.ip_address}...')
        if isinstance(device, EcowittTcpDevice):
            result['_about']['api'] = (f'TCP port {device.api.port}: the data part of each response, '
                                       f'as hex')
            for name, cmd in device.api.commands.items():
                try:
                    result[name] = bytes_to_hex(device.api.call(cmd))
                except DeviceIOError as e:
                    result[name] = {'_error': str(e)}
        else:
            api = device.api
            for command in api.commands:
                pages = range(1, 6) if command == 'get_sensors_info' else (None,)
                for page in pages:
                    label = command if page is None else f'{command}?page={page}'
                    try:
                        resp = api.request(command, data=None if page is None else {'page': page},
                                           rename=False)
                    except (OSError, urllib.error.URLError) as e:
                        resp = {'_error': str(e)}
                    result[label] = resp if unmask else mask_secrets(resp)
        text = json.dumps(result, indent=2)
        output = getattr(self.namespace, 'output', None)
        if output:
            with open(output, 'w', encoding='utf-8') as f:
                f.write(text + '\n')
            print(f'Saved {len(result) - 1} responses to {output}')
        else:
            print()
            print(text)
        if not unmask:
            print('Passwords, keys and station IDs are masked; use --unmask to include them.')

    def display_sd_files(self):
        """Display the device's SD card and the history files on it.

        These are the files a catchup from the device ([[catchup]] source =
        device or either) reads."""

        if not self.ip_address:
            print()
            print('No device IP address: use --ip-address or set ip_address in weewx.conf')
            return
        device = self.get_device()
        if device is None:
            return
        if isinstance(device, EcowittTcpDevice):
            print()
            print(f'{device.model} has no SD card or local HTTP API: catchup can only come from '
                  f'Ecowitt.net.')
            return
        try:
            data = device.get_sdmmc_info_data()
        except (DeviceIOError, ParseError) as e:
            print()
            print(f'{device.model} at {self.ip_address} did not report an SD card: {e}')
            return
        info = data.get('info') or {}
        print()
        print(f'SD card in {device.model} at {self.ip_address}:')
        for key in ('Name', 'Type', 'Speed', 'Size', 'Interval'):
            if key in info:
                label = 'Interval (minutes)' if key == 'Interval' else key
                print(f'{label:>20}: {info[key]}')
        entries = [f for f in data.get('file_list') or [] if str(f.get('type')) in ('file', '1')]
        print()
        if not entries:
            print('No files on the SD card.')
            return
        port = weeutil.weeutil.to_int((self.stn_dict.get('catchup') or {}).get('file_port',
                                                                                DEFAULT_SD_FILE_PORT))
        history = [f for f in entries if is_history_file(f)]
        print(f'{len(history)} history file(s), read from {sd_file_url(self.ip_address, "<name>", port)}:')
        for f in sorted(entries, key=lambda f: str(f.get('name', ''))):
            note = '' if is_history_file(f) else '  (not a history file, skipped by catchup)'
            print(f"    {str(f.get('name', '?')):<28} {str(f.get('size', '')):>10}{note}")

    @staticmethod
    def parse_since(text):
        """A timestamp from 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DDTHH:MM' (local time)."""

        for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
            try:
                return int(datetime.datetime.strptime(str(text).strip(), fmt).timestamp())
            except ValueError:
                pass
        raise ValueError(f"'{text}' is not a date such as 2026-11-05 or 2026-11-05 14:30")

    def catchup_dry_run(self):
        """Display the records a catchup would save after --since, without saving them.

        --since defaults to 24 hours ago; --catchup-source overrides
        [[catchup]] source. The records are those the driver would give WeeWX
        at startup: mapped, with [[sensor_map]] and [[limits]] applied."""

        since_text = getattr(self.namespace, 'since', None)
        try:
            since = self.parse_since(since_text) if since_text else int(time.time()) - 86400
        except ValueError as e:
            print()
            print(e)
            return
        if self.use_all_sources:
            self.merged_catchup_dry_run(since)
            return
        config = dict(self.stn_dict)
        config['ip_address'] = self.ip_address
        config['catchup'] = dict(config.get('catchup') or {})
        catchup_source = getattr(self.namespace, 'catchup_source', None)
        if catchup_source:
            config['catchup']['source'] = catchup_source
        if getattr(self.namespace, 'no_sensor_map', False):
            config.pop('sensor_map', None)
        for option, key in (('max_tries', 'max_tries'), ('retry_wait', 'retry_wait'),
                            ('timeout', 'url_timeout')):
            if getattr(self.namespace, option, None) is not None:
                config[key] = getattr(self.namespace, option)
        try:
            source = GatewaySource('gateway', config, unit_system=self.unit_system,
                                   label='catchup dry run')
        except (weewx.ViolatedPrecondition, DeviceIOError) as e:
            print()
            print(f'Unable to connect to device at {self.ip_address}: {e}')
            return
        fields = ('outTemp', 'outHumidity', 'windSpeed', 'windGust', 'relbarometer',
                  't_rainyear', 'p_rainyear')
        print()
        print(f"Catchup source: {config['catchup'].get('source') or 'either'}; "
              f"records after {timestamp_to_string(since)}")
        print('Nothing is saved.')
        print()
        print(f"{'time':<16} {'min':>3} " + ' '.join(f'{f:>12}' for f in fields))
        count, first, last = 0, {}, {}
        try:
            for rec in source.history(since, archive_interval=self.archive_interval):
                count += 1
                for f in ('t_rainyear', 'p_rainyear'):
                    if rec.get(f) is not None:
                        first.setdefault(f, rec[f])
                        last[f] = rec[f]
                values = ' '.join(f"{'' if rec.get(f) is None else round(rec[f], 2):>12}" for f in fields)
                when = time.strftime('%Y-%m-%d %H:%M', time.localtime(rec['dateTime']))
                print(f"{when:<16} {rec.get('interval', ''):>3} {values}")
        except (DeviceIOError, CatchupObjectError, InvalidApiResponseError, OSError) as e:
            print()
            print(f'The catchup stopped: {e}')
        print()
        if not count:
            print('No records: check [[catchup]] in weewx.conf, the SD card (--sd-files) or the '
                  'Ecowitt.net keys.')
            return
        print(f'{count} record(s).')
        for f, gauge in (('t_rainyear', 'tipping'), ('p_rainyear', 'piezo')):
            if f in first and last[f] >= first[f]:
                print(f'The {gauge} gauge counted {round(last[f] - first[f], 2)} mm of rain over these '
                      f'records ({f}); WeeWX works out rain from it.')

    @property
    def use_all_sources(self):
        """Whether a command that can use every gateway should: [[sources]] and no --source."""

        return len(self.source_names) > 1 and not getattr(self.namespace, 'source', None)

    def all_sources_config(self):
        """[EcowittGateway] with [[sources]], with the command line overrides that apply to every gateway."""

        config = dict(self.all_stn_dict)
        for option, key in (('poll_interval', 'poll_interval'), ('max_tries', 'max_tries'),
                            ('retry_wait', 'retry_wait'), ('timeout', 'url_timeout')):
            if getattr(self.namespace, option, None):
                config[key] = getattr(self.namespace, option)
        if getattr(self.namespace, 'no_sensor_map', False):
            config['sources'] = {n: {k: v for k, v in dict(c).items() if k != 'sensor_map'}
                                 for n, c in config['sources'].items()}
        catchup_source = getattr(self.namespace, 'catchup_source', None)
        if catchup_source:
            config['catchup'] = {**dict(config.get('catchup') or {}), 'source': catchup_source}
            config['sources'] = {n: {k: v for k, v in dict(c).items() if k != 'history'}
                                 for n, c in config['sources'].items()}
            for own in config['sources'].values():
                if hasattr(own.get('catchup'), 'keys'):
                    own['catchup'] = {**dict(own['catchup']), 'source': catchup_source}
        return config

    def all_sources_driver(self):
        """An EcowittHttpDriver for every gateway in [[sources]]; prints the gateways."""

        driver = EcowittHttpDriver(archive_interval=self.archive_interval, counters=self.counters,
                                   **self.all_sources_config())
        print()
        print('Gateways, in priority order: ' + ', '.join(
            f'{bcolors.BOLD}{s.name}{bcolors.ENDC} ({s.model} at {s.ip_address})' for s in driver.sources))
        return driver

    def merged_catchup_dry_run(self, since):
        """--catchup-dry-run with several gateways: the merged records catchup would save."""

        fields = ('outTemp', 'outHumidity', 'windSpeed', 'windGust', 'relbarometer',
                  't_rainyear', 'p_rainyear')
        driver = None
        try:
            driver = self.all_sources_driver()
            interval = (self.archive_interval or 300) // 60
            print(f'Merged {interval}-minute records from all the gateways after {timestamp_to_string(since)}')
            print('Nothing is saved.')
            print()
            print(f"{'time':<16} {'min':>3} " + ' '.join(f'{f:>12}' for f in fields))
            count, first, last = 0, {}, {}
            for rec in driver.genArchiveRecords(since):
                count += 1
                for f in ('t_rainyear', 'p_rainyear'):
                    if rec.get(f) is not None:
                        first.setdefault(f, rec[f])
                        last[f] = rec[f]
                values = ' '.join(f"{'' if rec.get(f) is None else round(rec[f], 2):>12}" for f in fields)
                when = time.strftime('%Y-%m-%d %H:%M', time.localtime(rec['dateTime']))
                print(f"{when:<16} {rec.get('interval', ''):>3} {values}")
        except (weewx.engine.InitializationError, weewx.ViolatedPrecondition, DeviceIOError) as e:
            print()
            print(f'Unable to start the driver: {e}')
            return
        finally:
            if driver is not None:
                driver.closePort()
        print()
        if not count:
            print('No records: check [[catchup]] in weewx.conf, the SD cards (--sd-files --source=NAME) '
                  'or the Ecowitt.net keys.')
            return
        print(f'{count} record(s).')
        for f, gauge in (('t_rainyear', 'tipping'), ('p_rainyear', 'piezo')):
            if f in first and last[f] >= first[f]:
                print(f'{round(last[f] - first[f], 2)} mm of {gauge} rain counted once over these records '
                      f'(merged {f}); WeeWX works out rain from it.')

    def display_sources(self):
        """Display the gateways in [[sources]], in priority order, and whether each one answers."""

        print()
        if not self.source_names:
            print('There is no [[sources]] section: the driver uses one gateway, ip_address = '
                  f"{self.all_stn_dict.get('ip_address')}")
            return
        print(f"{'gateway':<12} {'ip_address':<22} {'catchup':<8} model")
        for name, config in source_configs(self.all_stn_dict):
            try:
                device = make_device(config['ip_address'], api=config.get('api', DEFAULT_API),
                                     tcp_port=weeutil.weeutil.to_int(config.get('tcp_port', DEFAULT_TCP_PORT)),
                                     url_timeout=weeutil.weeutil.to_int(config.get('url_timeout',
                                                                                   DEFAULT_URL_TIMEOUT)),
                                     max_tries=1)
                model = f'{device.model} {device.firmware_version or ""}'.strip() if device.model \
                    else 'not answering'
            except Exception as e:
                model = f'not answering ({e})'
            catchup = str((config.get('catchup') or {}).get('source', 'either'))
            print(f"{name:<12} {config['ip_address']:<22} {catchup:<8} {model}")
        disabled = [n for n, c in (self.all_stn_dict.get('sources') or {}).items() if n not in self.source_names]
        if disabled:
            print(f"Disabled: {', '.join(disabled)}")
        priority = self.all_stn_dict.get('priority') or {}
        if priority:
            print()
            print('[[priority]]:')
            for field, order in priority.items():
                print(f"    {field} = {', '.join(as_names(order))}")
        print()
        print("Commands about one gateway use the first one; add --source=NAME for another.")

    def print_device_error(self, e):
        """Print a device error: the reason alone for a feature the device's API lacks."""

        if isinstance(e, UnsupportedFeatureError):
            print(e)
        else:
            print(f'Unable to connect to device at {self.ip_address}: {e}')

    def fetch(self, device, *getters):
        """Print which device is being read, then return a list of what each
        of its getter methods (named) returns, or None after printing why they
        failed."""

        try:
            # reading the model is the first contact with the device
            print(f'Interrogating {bcolors.BOLD}{device.model}{bcolors.ENDC} at '
                  f'{bcolors.BOLD}{device.ip_address}{bcolors.ENDC}')
            return [getattr(device, getter)() for getter in getters]
        except weewx.ViolatedPrecondition as e:
            print()
            print(f'Unable to obtain EcowittDevice object: {e}')
        except DeviceIOError as e:
            print()
            self.print_device_error(e)
        except socket.timeout:
            print()
            print(f'Timeout. Device at {self.ip_address} did not respond.')
        return None

    def api(self):
        """The device API to use: --api, else [EcowittGateway] api, else 'auto'."""

        return getattr(self.namespace, 'api', None) or self.stn_dict.get('api', DEFAULT_API)

    def sensor_map_config(self):
        """The [[sensor_map]] config, or None if --no-sensor-map was used."""

        if getattr(self.namespace, 'no_sensor_map', False):
            return None
        return self.stn_dict.get('sensor_map')

    @staticmethod
    def print_sensor_map(mapper):
        """Print a summary of the channel moves planned by a SensorMapper."""

        if not mapper:
            return
        print()
        print(f'Sensor map: {len(mapper.targets)} sensor(s) reported on fixed '
              f'channels by sensor ID')
        for line in mapper.problems + mapper.lines or \
                ['every mapped sensor is already on its channel']:
            print(f'    {line}')

    def display_active_field_map(self):
        """Display the field map entries the device is filling now.

        The whole field map has over 200 entries, most of them for sensors a
        station doesn't have. This lists only the entries whose device field
        has a value in the device's current data, using the field map and
        sensor map in [EcowittGateway] (--no-sensor-map to leave the sensor
        map out).
        """

        try:
            collector = EcowittHttpCollector(ip_address=self.ip_address,
                                             show_battery=self.show_battery,
                                             sensor_map=self.sensor_map_config(),
                                             api=self.api(),
                                             tcp_port=weeutil.weeutil.to_int(self.stn_dict.get('tcp_port',
                                                                                               DEFAULT_TCP_PORT)))
            print()
            print(f'Interrogating {collector.device.model} at {self.ip_address}')
            current_data = collector.get_current_data()
            self.print_sensor_map(collector.sensor_mapper)
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
        # the field map the driver/service would use: the default map changed
        # by any [[field_map]] and [[field_map_extensions]]
        mapper = HttpMapper(driver_debug=None, **self.stn_dict)
        active = FieldMapper.to_station_columns(active_field_map(mapper.field_map, current_data))
        print()
        print('Active field map (the entries the device is filling now):')
        print('(format is WeeWX field name: Ecowitt HTTP driver field name)')
        print()
        for key in natural_sort_keys(active):
            print(f'{key:>23}: {active[key]}')
        print()
        print(f'{len(active)} of the {len(mapper.field_map)} field map entries are in use. Sensors that '
              f'are not paired,')
        print('offline or still registering are not shown; the driver run directly with --driver-map')
        print('lists the whole map. The driver adds gateway_online, and WeeWX the fields it calculates')
        print('(rain, t_rain, p_rain, lightning_strike_count ...), to each loop packet.')
        if FieldMapper.station_columns:
            print()
            print('These driver fields go to standard station database columns (battery levels as 0 OK, 1 low):')
            for column, fields in FieldMapper.station_columns.items():
                print(f"{column:>23}: {' or '.join(fields)}")

    def display_sensor_map(self):
        """Display multi-channel sensors, their device and reported channels."""

        device = self.get_device()
        if device is None:
            return
        try:
            sensors = device.get_sensors_data()
        except Exception as e:
            print()
            print(f'Unable to obtain sensor data from the device at {self.ip_address}: {e}')
            print()
            self.device_connection_help()
            return
        mapper = SensorMapper(self.sensor_map_config())
        mapper.plan(sensors)
        paired = SensorMapper.paired(sensors)
        print()
        print(f'Multi-channel sensors paired with the {device.model} at {self.ip_address}:')
        if not paired:
            print('    none')
        else:
            print()
            print(f"    {'Sensor':<8} {'ID':<10} {'Device channel':<16} Reported as")
            for model in SENSOR_GROUPS:
                for channel, sid in sorted(paired.get(model, {}).items()):
                    reported = mapper.reported_channel(model, channel)
                    note = '' if reported == channel else ' (mapped)'
                    print(f'    {model.upper():<8} {sid:<10} {channel:<16} '
                          f'channel {reported}{note}')
        if mapper:
            self.print_sensor_map(mapper)
        else:
            print()
            print('No [[sensor_map]] is configured, sensors are reported on their device channels.')
            print("To fix a sensor to a channel, add '<sensor ID> = <channel>' entries to")
            print(f'[{DRIVER_NAME}] [[sensor_map]], for example:')
            print()
            print(f'[{DRIVER_NAME}]')
            print('    [[sensor_map]]')
            for model in SENSOR_GROUPS:
                for channel, sid in sorted(paired.get(model, {}).items())[:1]:
                    print(f'        {sid} = {channel}')
        print()

    def display_live_data(self):
        """Display the device live sensor data.

        Obtain and display live sensor data from the selected device. Data is
        presented as read from the device except for conversion to US customary
        or Metric units and the addition of unit labels.
        """

        try:
            # get an EcowittHttpCollector object, normally we would reach into
            # an EcowittDevice object for data but in this case the
            # EcowittHttpCollector.get_current_data() method can be used to
            # assemble all disparate pieces of data for us
            collector = EcowittHttpCollector(ip_address=self.ip_address,
                                             show_battery=self.show_battery,
                                             sensor_map=self.sensor_map_config(),
                                             api=self.api(),
                                             tcp_port=weeutil.weeutil.to_int(self.stn_dict.get('tcp_port',
                                                                                               DEFAULT_TCP_PORT)))
            print()
            print(f'Interrogating {collector.device.model} at '
                  f'{self.ip_address}')
            # call the EcowittHttpCollector.get_current_data() method to obtain
            # the live sensor data, any sensor map is planned at the same time
            current_data = collector.get_current_data()
            self.print_sensor_map(collector.sensor_mapper)
        except weewx.ViolatedPrecondition as e:
            print()
            print(f'Unable to obtain EcowittDevice object: {e}')
        except DeviceIOError as e:
            print()
            print(f'Unable to connect to device at {self.ip_address}: {e}')
            print()
            self.device_connection_help()
        except socket.timeout:
            print()
            print(f'Timeout. Device at {self.ip_address} did not respond.')
            print()
            self.device_connection_help()
        else:
            # Obtain a HttpMapper object to map our data, use the default field
            # map
            mapper = HttpMapper(driver_debug=None)
            mapped_data = mapper.map_data(current_data)
            # we have a data dict to work with, but we need to format the
            # values and may need to convert units

            # the live sensor data dict is a dict of sensor values and a
            # timestamp only, whilst all sensor values are in a given unit
            # system there is no usUnits field present. We need usUnits to do
            # our unit conversion so add in the usUnits field.
            mapped_data['usUnits'] = self.unit_system
            datetime = mapped_data.pop('datetime', int(time.time()))
            # TODO. Is this needed?
            # extend the WeeWX obs_group_dict with our Ecowitt device
            # obs_group_dict
            weewx.units.obs_group_dict.prepend(DEFAULT_GROUPS)
            # the live data is in self.unit_system units, if required get a
            # suitable converter based on our display units
            display_unit_system = weewx.units.unit_constants[self.namespace.units.upper()]
            c = weewx.units.StdUnitConverters[display_unit_system]
            # Now get a formatter, we could use the default formatter, but we
            # need voltages formatted to two decimal places. So create a custom
            # formatter using a modified defaults StringFormats stanza and the
            # defaults Labels stanza.
            # obtain a copy of the defaults StringFormats stanza
            unit_format_dict = dict(weewx.defaults.defaults['Units']['StringFormats'])
            unit_format_dict['volt'] = '%.2f'
            f = weewx.units.Formatter(unit_format_dict=unit_format_dict,
                                      unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
            result = {}
            for key in mapped_data:
                if key == 'usUnits':
                    continue
                key_vt = weewx.units.as_value_tuple(mapped_data, key)
                key_vh = weewx.units.ValueHelper(key_vt, formatter=f, converter=c)
                result[key] = key_vh.toString(None_string='None')
            print()
            print(f'Displaying data using the WeeWX '
                  f'{weewx.units.unit_nicknames.get(display_unit_system)} unit group.')
            print()
            print(f'{collector.device.model} live sensor data ({timestamp_to_string(datetime)}): '
                  f'{weeutil.weeutil.to_sorted_string(result)}')

    def display_discovered_devices(self):
        """Display discovered devices.

        Discover and display details of Ecowitt devices on the local network.
        """

        print()
        print('Discovering Ecowitt devices...')
        print()
        # discover the devices
        device_list = self.discover()
        if len(device_list) > 0:
            # we have at least one result
            # Check if any of the discovered devices might be supported (ie the
            # device is not included in the supported or unsupported lists).
            # First obtain a list of such devices.
            possible = [d for d in device_list if d['model'] not in KNOWN_DEVICES]
            possible_supported_ip = []
            if len(possible) > 0:
                # we have some unknown devices that may be supported
                for device in possible:
                    if self.is_supported(device):
                        possible_supported_ip += [device['ip_address'],]
            # first sort our list by IP address
            sorted_list = sorted(device_list, key=itemgetter('ip_address'))
            printed_anything = False
            # first the supported devices
            print_label = False
            for device in sorted_list:
                if (device['ip_address'] is not None and
                        device['model'] is not None and
                        device['model'] in SUPPORTED_DEVICES):
                    if not print_label:
                        print('Supported Devices')
                        printed_anything = True
                        print_label = True
                    print(f'  {device["model"]} discovered at IP address {device["ip_address"]}')
            print_label = False
            for device in sorted_list:
                if (device['ip_address'] is not None and
                        device['ip_address'] in possible_supported_ip):
                    if not print_label:
                        if not printed_anything:
                            print()
                        print('Possibly Supported Devices')
                        printed_anything = True
                        print_label = True
                    # By definition, we don't know the model of 'possibly'
                    # supported devices. However, we can take an educated guess
                    # at the device model from the device AP SSID.
                    # do we have a model
                    if device['model'] is None:
                        if device.get('ssid') is not None:
                            # The model is usually the first six characters of
                            # the SSID. Construct a string to use when
                            # displaying the device model, add a rider that the
                            # model is unconfirmed.
                            model_string = ' '.join([device.get('ssid')[0:6], '(model unconfirmed)'])
                        else:
                            # we don't have an SSID for some reason, we cannot
                            # guess the model so leave it as 'unknown'
                            model_string = 'unknown'
                    else:
                        model_string = device['model']
                    print(f'  {model_string} discovered at IP address {device["ip_address"]}')
            print_label = False
            for device in sorted_list:
                if (device['ip_address'] is not None and
                        device['model'] is not None and
                        device['model'] in UNSUPPORTED_DEVICES):
                    if not print_label:
                        if not printed_anything:
                            print()
                        print('Unsupported Devices')
                        printed_anything = True
                        print_label = True
                    print(f'  {device["model"]} discovered at IP address {device["ip_address"]}')
        else:
            print('No devices were discovered.')

    @staticmethod
    def is_supported(device):
        """Perform a basic check to see if a device supports the HTTP API."""

        ip_address = device.get('ip_address')
        # try to obtain an EcowittDevice object using the IP address we
        # received, be prepared to handle any exceptions raised
        try:
            device = EcowittDevice(ip_address=ip_address)
        except Exception:
            # We could not obtain an Ecowitt Device object, this means we
            # cannot support the device. For now just continue.
            pass
        else:
            # We have an EcowittDevice object. Try to obtain the device info
            # data via the HTTP API, be prepared to catch any exceptions.
            try:
                device_info_data = device.get_device_info_data()
            except (DeviceIOError, ParseError):
                pass
            else:
                # We have some device info. Do a basic validity check on the
                # data, in this case check that the 'ap' field is not None and
                # non-zero length.
                if device_info_data['ap'] is not None and len(device_info_data['ap']) > 0:
                    # our basic check passed, so assume this device is
                    # supported
                    # now take a guess at the model
                    return True
        # we encountered an exception somewhere, assume this device is not
        # supported
        return False

    def discover(self):
        """Discover any devices on the local network.

        There is no published method of discovering Ecowitt devices on the
        local network using the Ecowitt HTTP API. According to the Ecowitt
        telnet API the CMD_BROADCAST API command can be used to identify
        Ecowitt devices on the local network segment; however, this approach
        has been found to be unreliable and the telnet API may not be supported
        by all devices in the future. Another more reliable approach is to make
        use of the regular UDP broadcast made by each Ecowitt device on
        port 59387.

        To discover Ecowitt devices monitor UDP port 59387 for a set period of
        time and capture all port 59387 UDP broadcasts received. Decode each
        reply to obtain details of any devices on the local network. Create a
        dict of details for each device including a derived model name.
        Construct a list of dicts with details of each unique (ie each unique
        MAC address) device that responded. When complete return the list of
        devices found.
        """

        parser = EcowittHttpParser()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # set timeout
        s.settimeout(self.namespace.discovery_timeout)
        s.bind(("", self.namespace.discovery_port))
        # initialise a list for the results as multiple devices may respond
        result_list = []
        start_ts = time.time()
        while True:
            # wrap in try .. except to capture any errors
            try:
                # receive a response
                response = s.recv(1024)
                # # if required display the response packet
                # if self.debug:
                #     _first_row = True
                #     for row in gen_pretty_bytes_as_hex(response, quote=False):
                #         if _first_row:
                #             print()
                #             print(f'Received broadcast packet: {row["hex"]}')
                #             _first_row = False
                #         else:
                #             print(f'                           {row["hex"]}')
                #         print(f'                           {row["printable"]}')
            except socket.timeout:
                break
            except socket.error:
                raise
            # Check the response is valid. As it happens the broadcast from
            # each device on port 59387 is identical to the response to a
            # device response to CMD_BROADCAST telnet API command. The validity
            # of the response can be checked by (1) confirming byte 2 is 0x12
            # and (2) verifying the packet checksum in last byte.
            # first check we have a response
            if response is not None and len(response) > 3:
                # now check that byte 2 == 0x12
                if response[2] != 0x12:
                    continue
                if calc_checksum(response[2:-1]) != response[-1]:
                    continue
            else:
                continue
            found_device_dict = self.decode_broadcast_response(response)
            if not any((d['mac'] == found_device_dict['mac']) for d in result_list):
                found_device_dict['model'] = parser.get_model_from_firmware(found_device_dict.get('ssid'))
                result_list.append(found_device_dict)
            # has our discovery period elapsed, if it has break out of the
            # loop
            if time.time() - start_ts > self.discovery_period:
                break
        # we are done, close our socket
        s.close()
        return result_list

    @staticmethod
    def decode_broadcast_response(raw_data):
        """Decode a broadcast response and return the results as a dict.

        A device response to a CMD_BROADCAST API command consists of a
        number of control structures around a payload of a data. The API
        response is structured as follows:

        bytes 0-1 incl                  preamble, literal 0xFF 0xFF
        byte 2                          literal value 0x12
        bytes 3-4 incl                  payload size (big endian short integer)
        bytes 5-5+payload size incl     data payload (details below)
        byte 6+payload size             checksum

        The data payload is structured as follows:

        bytes 0-5 incl      device MAC address
        bytes 6-9 incl      device IP address
        bytes 10-11 incl    device port number
        bytes 11-           device AP SSID

        Note: The device AP SSID for a given device is fixed in size but
        this size can vary from device to device and across firmware
        versions.

        There also seems to be a peculiarity in the CMD_BROADCAST response
        data payload whereby the first character of the device AP SSID is a
        non-printable ASCII character. The WSView app appears to ignore or
        not display this character nor does it appear to be used elsewhere.
        Consequently, this character is ignored.

        raw_data:   a bytestring containing a validated (structure and
                    checksum verified) raw data response to the
                    CMD_BROADCAST API command

        Returns a dict with decoded data keyed as follows:
            'mac':          device MAC address (string)
            'ip_address':   device IP address (string)
            'port':         device port number (integer)
            'ssid':         device AP SSID (string)
        """

        # obtain the response size, it's a big endian short (two byte)
        # integer
        resp_size = struct.unpack('>H', raw_data[3:5])[0]
        data = raw_data[5:resp_size + 2]
        data_dict = {'mac': bytes_to_hex(data[0:6], separator=":"),
                     'ip_address': '%d.%d.%d.%d' % struct.unpack('>BBBB',
                                                                 data[6:10]),
                     'port': struct.unpack('>H', data[10: 12])[0]
                     }
        # get the SSID as a bytestring
        ssid_b = data[13:]
        # create a format string so the SSID string can be unpacked into its
        # bytes, remember the length can vary
        ssid_format = "B" * len(ssid_b)
        # unpack the SSID bytestring, we now have a tuple of integers
        # representing each of the bytes
        ssid_t = struct.unpack(ssid_format, ssid_b)
        # convert the sequence of bytes to unicode characters and assemble
        # as a string and return the result
        data_dict['ssid'] = "".join([chr(x) for x in ssid_t])
        return data_dict

    @staticmethod
    def display_field_map():
        """Display the default field map."""

        # first obtain a HttpMapper object without passing any config, this
        # will initialise the HttpMapper object with the default field map
        mapper = HttpMapper(driver_debug=None)
        print()
        print('Ecowitt HTTP driver/service default field map:')
        print('(format is WeeWX field name: Ecowitt HTTP driver field name)')
        print()
        # obtain a list of naturally sorted field map keys so that, for
        # example, xxxxx16 appears in the correct order
        keys_list = natural_sort_keys(mapper.field_map)
        for key in keys_list:
            print(f'{key:>23}: {mapper.field_map[key]}')

    def display_driver_field_map(self):
        """Display the driver field map that would be used.

        By default, the default field map is used by the driver; however, the
        user may alter the field map used by the driver via the [EcowittGateway]
        stanza. This method displays the actual field map that would be used by
        the driver.
        """

        # this may take a moment to set up so inform the user
        print()
        print('This may take a moment...')
        # place an entry in the log so that if we encounter errors that are
        # logged we can tell they were not caused by a live WeeWX instance
        log.info('Obtaining an Ecowitt HTTP driver...')
        driver = None
        try:
            driver = EcowittHttpDriver(**self.stn_dict)
        except DeviceIOError as e:
            print()
            print(f'Unable to connect to device: {e}')
            print()
            print('Unable to display actual driver field map')
            print()
            self.device_connection_help()
        except KeyboardInterrupt:
            if driver:
                driver.closePort()
            driver = None
        else:
            print()
            print('Ecowitt HTTP driver actual field map:')
            print('(format is WeeWX field name: driver field name)')
            print()
            # obtain a list of naturally sorted dict keys so that, for example,
            # xxxxx16 appears in the correct order
            keys_list = natural_sort_keys(driver.mapper.field_map)
            for key in keys_list:
                print(f'{key:>23}: {driver.mapper.field_map[key]}')
        finally:
            # stop the driver's collector thread (ecowitt_http.py only did
            # this on Ctrl-C, leaving the thread polling the device)
            if driver:
                driver.closePort()
        log.info('Finished using Ecowitt local HTTP driver')

    def display_service_field_map(self):
        """Display the Ecowitt HTTP driver service field map that would be used.

        By default, the default field map is used by the service; however, the
        user may alter the field map used by the service via the [EcowittGateway]
        stanza. This method displays the actual field map that would be used by
        the service.
        """

        # this may take a moment to set up so inform the user
        print()
        print('This may take a moment...')
        # place an entry in the log so that if we encounter errors that are
        # logged we can tell they were not caused by a live WeeWX instance
        log.info('Obtaining an Ecowitt HTTP service...')
        config = {
            'Station': {
                'station_type': 'Simulator',
                'altitude': [0, 'meter'],
                'latitude': 0,
                'longitude': 0},
            'Simulator': {
                'driver': 'weewx.drivers.simulator',
                'mode': 'simulator'},
            DRIVER_NAME: {},
            'Engine': {
                'Services': {
                    'archive_services': f'{MODULE}.EcowittHttpService',
                    'report_services': 'weewx.engine.StdPrint'}}}
        config[DRIVER_NAME]['ip_address'] = self.ip_address
        engine = None
        try:
            engine = weewx.engine.StdEngine(config)
            # Our driver service will have been instantiated by the engine
            # during its startup. Whilst access to the service is not normally
            # required we require access here, so we can obtain some info about
            # the station we are using for this test. The engine does not
            # provide a ready means to access the driver service, so we can
            # do a bit of guessing and iterate over all the engine's services
            # and select the one that has a 'collector' property. Unlikely to
            # cause a problem since there are only two services in the dummy
            # engine.
            http_svc = None
            for svc in engine.service_obj:
                if hasattr(svc, 'collector'):
                    http_svc = svc
            if http_svc is not None:
                print()
                print('Ecowitt HTTP driver service actual field map:')
                print('(format is WeeWX field name: service field name)')
                print()
                # obtain a list of naturally sorted dict keys so that, for example,
                # xxxxx16 appears in the correct order
                keys_list = natural_sort_keys(http_svc.mapper.field_map)
                for key in keys_list:
                    print(f'{key:>23}: {http_svc.mapper.field_map[key]}')
        except DeviceIOError as e:
            print()
            print(f'Unable to connect to device: {e}')
            print()
            print('Unable to display actual service field map')
            print()
            self.device_connection_help()
        except KeyboardInterrupt:
            pass
        finally:
            # stop the service's collector thread (ecowitt_http.py only did
            # this on Ctrl-C, leaving the thread polling the device)
            if engine:
                engine.shutDown()
        log.info('Finished using Ecowitt HTTP driver service')

    def test_driver(self):
        """Exercise the Ecowitt HTTP driver as a driver.

        Exercises the Ecowitt HTTP driver only. Loop packets, but no archive
        records, are emitted to the console continuously until a keyboard
        interrupt is received. A station config dict is coalesced from any
        relevant command line parameters and the config file in use with
        command line parameters overriding those in the config file.
        """

        log.info('Testing Ecowitt HTTP driver...')
        if self.use_all_sources:
            driver = None
            try:
                driver = self.all_sources_driver()
                print()
                for pkt in driver.genLoopPackets():
                    print(f'{weeutil.weeutil.timestamp_to_string(pkt["dateTime"])}: '
                          f'{weeutil.weeutil.to_sorted_string(pkt)}')
            except (weewx.engine.InitializationError, DeviceIOError, weewx.WeeWxIOError) as e:
                print()
                print(f'Unable to run the driver: {e}')
            except KeyboardInterrupt:
                pass
            finally:
                if driver is not None:
                    driver.closePort()
            log.info('Ecowitt HTTP driver testing complete')
            return
        self.stn_dict['ip_address'] = self.ip_address
        if self.namespace.poll_interval:
            self.stn_dict['poll_interval'] = self.namespace.poll_interval
        if self.namespace.max_tries:
            self.stn_dict['max_tries'] = self.namespace.max_tries
        if self.namespace.retry_wait:
            self.stn_dict['retry_wait'] = self.namespace.retry_wait
        if self.namespace.timeout:
            self.stn_dict['url_timeout'] = self.namespace.timeout
        driver = None
        try:
            driver = EcowittHttpDriver(**self.stn_dict)
            print()
            print(f'Interrogating {bcolors.BOLD}{driver.collector.device.model}{bcolors.ENDC} '
                  f'at {bcolors.BOLD}{driver.collector.device.ip_address}{bcolors.ENDC}')
            print()
            # continuously get loop packets and print them to console
            for pkt in driver.genLoopPackets():
                print(f'{weeutil.weeutil.timestamp_to_string(pkt["dateTime"])}: '
                      f'{weeutil.weeutil.to_sorted_string(pkt)}')
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
        log.info('Ecowitt HTTP driver testing complete')

    def test_service(self):
        """Exercise the Ecowitt HTTP driver as a service.

        Uses a dummy engine/simulator to generate arbitrary loop packets for
        augmenting. Use a 10-second loop interval so we don't get too many bare
        packets.
        """

        log.info('Testing Ecowitt HTTP driver as a service...')
        config = {
            'Station': {
                'station_type': 'Simulator',
                'altitude': [0, 'meter'],
                'latitude': 0,
                'longitude': 0},
            'Simulator': {
                'driver': 'weewx.drivers.simulator',
                'mode': 'simulator'},
            # the station's own settings (api, tcp_port, sensor_map ...), as
            # --test-driver uses them
            DRIVER_NAME: dict(self.stn_dict),
            'Engine': {
                'Services': {
                    'archive_services': f'{MODULE}.EcowittHttpService',
                    'report_services': 'weewx.engine.StdPrint'}}}
        if self.use_all_sources:
            # every gateway in [[sources]]
            config[DRIVER_NAME] = self.all_sources_config()
        else:
            # set the IP address and port in the dummy config
            config[DRIVER_NAME]['ip_address'] = self.ip_address
        # these command line options should only be added if they exist
        if self.namespace.poll_interval:
            config[DRIVER_NAME]['poll_interval'] = self.namespace.poll_interval
        if self.namespace.max_tries:
            config[DRIVER_NAME]['max_tries'] = self.namespace.max_tries
        if self.namespace.retry_wait:
            config[DRIVER_NAME]['retry_wait'] = self.namespace.retry_wait
        weewx.units.obs_group_dict['dummyTemp'] = 'group_temperature'
        engine = None
        try:
            engine = weewx.engine.StdEngine(config)
            # Our Ecowitt HTTP service will have been instantiated by the
            # engine during its startup. Whilst access to the service is not
            # normally required we require access here, so we can obtain some
            # info about the station we are using for this test. The engine
            # does not provide a ready means to access that service, so we can
            # do a bit of guessing and iterate over all the engine's services
            # and select the one that has a 'collector' property. Unlikely to
            # cause a problem since there are only two services in the dummy
            # engine.
            http_svc = None
            for svc in engine.service_obj:
                if hasattr(svc, 'collector'):
                    http_svc = svc
            if http_svc is not None and http_svc.multi:
                print()
                print('Gateways, in priority order: ' + ', '.join(
                    f'{bcolors.BOLD}{s.name}{bcolors.ENDC} ({s.model} at {s.ip_address})'
                    for s in http_svc.sources))
            elif http_svc is not None:
                print()
                print(f'Interrogating {bcolors.BOLD}{http_svc.collector.device.model}{bcolors.ENDC} '
                      f'at {bcolors.BOLD}{http_svc.collector.device.ip_address}{bcolors.ENDC}')
            print()
            while True:
                packet = {'dateTime': int(time.time()),
                          'usUnits': weewx.US,
                          'dummyTemp': 96.3
                          }
                # send out a NEW_LOOP_PACKET event with the dummy loop packet
                # to trigger the Ecowitt local HTTP API service to augment the
                # loop packet
                engine.dispatchEvent(weewx.Event(weewx.NEW_LOOP_PACKET,
                                                 packet=packet,
                                                 origin='software'))
                # sleep for a bit to emulate the simulator
                time.sleep(10)
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
        log.info('Ecowitt HTTP driver service testing complete')

    def weewx_fields(self):
        """Display WeeWX loop packet fields emitted when used as a driver.

        Exercises the Ecowitt HTTP driver as a driver and displays the WeeWX
        fields to be emitted in loop packets using the specified WeeWX config
        file. A station config dict is coalesced from any relevant command line
        parameters and the config file in use with command line parameters
        overriding those in the config file.
        """

        log.info('Displaying WeeWX loop packet fields emitted by the Ecowitt HTTP driver...')
        # we already have a station config dict, but we will accept a command
        # line specified device IP address if provided
        self.stn_dict['ip_address'] = self.ip_address
        driver = None
        # obtain an EcowittHttpDriver object and get a single loop packet, wrap
        # in a try..except in case there is an error
        try:
            if self.use_all_sources:
                # every gateway in [[sources]]: the merged packet
                driver = self.all_sources_driver()
            else:
                # get an EcowittHttpDriver object
                driver = EcowittHttpDriver(**self.stn_dict)
                # identify the device being used
                print()
                print(f'Interrogating {bcolors.BOLD}{driver.collector.device.model}{bcolors.ENDC} '
                      f'at {bcolors.BOLD}{driver.collector.device.ip_address}{bcolors.ENDC}')
            print()
            # continuously get loop packets but we will exit after we have the
            # first and presented our output
            for pkt in driver.genLoopPackets():
                if self.namespace.units.lower() == 'us':
                    _unit_system = weewx.US
                elif self.namespace.units.lower() == 'metric':
                    _unit_system = weewx.METRIC
                else:
                    _unit_system = weewx.METRICWX
                c = weewx.units.StdUnitConverters[_unit_system]
                # Now get a formatter, we could use the default formatter, but we
                # need voltages formatted to two decimal places. So create a custom
                # formatter using a modified defaults StringFormats stanza and the
                # defaults Labels stanza.
                # obtain a copy of the defaults StringFormats stanza
                _unit_format_dict = dict(weewx.defaults.defaults['Units']['StringFormats'])
                _unit_format_dict['volt'] = '%.2f'
                f = weewx.units.Formatter(unit_format_dict=_unit_format_dict,
                                          unit_label_dict=weewx.defaults.defaults['Units']['Labels'])
                result = {}
                for key in pkt.keys():
                    if key == 'usUnits':
                        continue
                    key_vt = weewx.units.as_value_tuple(pkt, key)
                    key_vh = weewx.units.ValueHelper(key_vt, formatter=f, converter=c)
                    try:
                        result[key] = key_vh.toString(None_string='None')
                    except:
                        result[key] = f"Unable to convert/format '{key}'"
                        continue
                for key in natural_sort_keys(result):
                    print(f'{key:>25}: {result[key]}')
                break
        except DeviceIOError as e:
            print()
            print(f'Unable to connect to device: {e}')
            print()
            self.device_connection_help()
        finally:
            # before we exit shutdown the driver
            if driver:
                driver.closePort()

    @staticmethod
    def device_connection_help():
        """Console output help message for device connection problems."""

        print('    Things to check include that the correct device IP address is being used,')
        print('    the device is powered on and the device is not otherwise disconnected from')
        print('    the local network.')


# To use this driver in standalone mode for testing or development, use one of
# the following commands (depending on your WeeWX install type):
#
#   for pip installs use:
#       $ source ~/weewx-venv/bin/activate
#       $ python3 -m user.weewx-ecowittGateway
#   for git installs use:
#       $ source ~/weewx-venv/bin/activate
#       $ PYTHONPATH=~/weewx/src:~/weewx-data/bin python3 -m user.weewx-ecowittGateway
#   for package installs use:
#       $ python3 -m user.weewx-ecowittGateway
#
# The above commands will run the driver directly and display details of
# available command line options.
#
# Note. Whilst the driver may be run independently of WeeWX the driver still
# requires WeeWX and it's dependencies be installed. Consequently, the driver
# must be run under the same Python installation (or Python virtual
# environment) as used by WeeWX.

def main():
    import argparse

    usage = f"""{bcolors.BOLD}%(prog)s --help
                --version
                --test-driver|--test-service
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS] [--poll-interval=INTERVAL]
                     [--max-tries=MAX_TRIES] [--retry-wait=RETRY_WAIT]
                     [--show-all-batt] [--debug=0|1|2|3]
                --live-data
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--units=us|metric|metricwx]
                     [--ip-address=IP_ADDRESS]
                     [--show-all-batt] [--debug=0|1|2|3]
                --weewx-fields
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS]
                     [--show-all-batt] [--debug=0|1|2|3]
                --default-map|--driver-map|--service-map
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--debug=0|1|2|3]
                --sensor-map|--active-map
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS] [--no-sensor-map]
                     [--debug=0|1|2|3]
                --sd-files
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS] [--debug=0|1|2|3]
                --catchup-dry-run
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--ip-address=IP_ADDRESS] [--since=DATE]
                     [--catchup-source=either|device|net]
                     [--no-sensor-map] [--debug=0|1|2|3]
                --sources
                     [CONFIG_FILE|--config=CONFIG_FILE]
                --discover
                     [CONFIG_FILE|--config=CONFIG_FILE]
                     [--debug=0|1|2|3]
        With several gateways ([[sources]]), add --source=NAME to choose the
        gateway for the other commands (default: the first one).{bcolors.ENDC}
    """
    description = """Interact with an Ecowitt device via the Ecowitt local HTTP API."""
    parser = argparse.ArgumentParser(usage=usage,
                                     description=description,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version',
                            dest='version',
                            action='store_true',
                            help='display driver version number')
    parser.add_argument('--discover',
                        dest='discover',
                        action='store_true',
                        help='display details of discovered devices')
    parser.add_argument('--live-data',
                        dest='live',
                        action='store_true',
                        help='display device live sensor data')
    parser.add_argument('--sensors',
                        dest='sensors',
                        action='store_true',
                        help='display device sensor data')
    parser.add_argument('--test-driver',
                        dest='test_driver',
                        action='store_true',
                        help='exercise the driver')
    parser.add_argument('--test-service',
                        dest='test_service',
                        action='store_true',
                        help='exercise the driver as a WeeWX service')
    parser.add_argument('--weewx-fields',
                        dest='weewx_fields',
                        action='store_true',
                        help='display WeeWX loop packet fields emitted by the '
                             'current configuration')
    parser.add_argument('--default-map',
                        dest='map',
                        action='store_true',
                        help='display the default field map')
    parser.add_argument('--driver-map',
                        dest='driver_map',
                        action='store_true',
                        help='display the field map that would be used by the '
                             'driver')
    parser.add_argument('--service-map',
                        dest='service_map',
                        action='store_true',
                        help='display the field map that would be used by the '
                             'service')
    parser.add_argument('--sensor-map',
                        dest='sensor_map',
                        action='store_true',
                        help='display the multi-channel sensors and the channels '
                             'they are reported on ([[sensor_map]])')
    parser.add_argument('--no-sensor-map',
                        dest='no_sensor_map',
                        action='store_true',
                        help='ignore [[sensor_map]] and show device channels')
    parser.add_argument('--active-map',
                        dest='active_map',
                        action='store_true',
                        help='display the field map entries the device is filling now')
    parser.add_argument('--firmware',
                        dest='firmware',
                        action='store_true',
                        help='display device firmware information')
    parser.add_argument('--mac',
                        dest='mac',
                        action='store_true',
                        help='display device MAC address')
    parser.add_argument('--system',
                        dest='sys_params',
                        action='store_true',
                        help='display device system parameters')
    parser.add_argument('--get-rain-totals',
                        dest='rain_totals',
                        action='store_true',
                        help='display rain gauge totals and gains, rainfall'
                             'priority and rainfall reset times')
    parser.add_argument('--get-calibration',
                        dest='calibration',
                        action='store_true',
                        help='display device calibration data')
    parser.add_argument('--get-th-cal',
                        dest='mulch_offset',
                        action='store_true',
                        help='display device multi-channel temperature and '
                             'humidity calibration data')
    parser.add_argument('--get-soil-cal',
                        dest='soil_calibration',
                        action='store_true',
                        help='display device multi-channel soil moisture '
                             'calibration data')
    parser.add_argument('--get-t-cal',
                        dest='temp_calibration',
                        action='store_true',
                        help='display device multi-channel temperature '
                             'calibration data')
    parser.add_argument('--get-pm25-cal',
                        dest='pm25_offset',
                        action='store_true',
                        help='display device multi-channel PM2.5 calibration '
                             'data')
    parser.add_argument('--get-co2-cal',
                        dest='co2_offset',
                        action='store_true',
                        help='display device CO2 (WH45) calibration data')
    parser.add_argument('--get-lds-cal',
                        dest='lds_offset',
                        action='store_true',
                        help='display device LDS (WH54) calibration data')
    parser.add_argument('--get-services',
                        dest='services',
                        action='store_true',
                        help='display device weather services configuration '
                             'data')
    parser.add_argument('--ip-address',
                        dest='ip_address',
                        help='device IP address to use')
    parser.add_argument('--api',
                        dest='api',
                        choices=['auto', 'http', 'tcp'],
                        help="device API to use: 'http', 'tcp' (GW1000) or 'auto'")
    parser.add_argument('--poll-interval',
                        dest='poll_interval',
                        type=int,
                        help='how often to poll the device API')
    parser.add_argument('--max-tries',
                        dest='max_tries',
                        type=int,
                        default=DEFAULT_MAX_TRIES,
                        help='max number of attempts to contact the device')
    parser.add_argument('--retry-wait',
                        dest='retry_wait',
                        type=int,
                        help='how long to wait between attempts to contact the device')
    parser.add_argument('--timeout',
                        dest='timeout',
                        type=int,
                        help='how long to wait for the device to respond to a HTTP request')
    parser.add_argument('--discovery-port',
                        dest='discovery_port',
                        type=int,
                        default=DEFAULT_DISCOVERY_PORT,
                        help='port to listen to when discovering devices')
    parser.add_argument('--discovery-timeout',
                        dest='discovery_timeout',
                        type=int,
                        default=DEFAULT_DISCOVERY_TIMEOUT,
                        help='how long to listen when discovering devices')
    parser.add_argument('--show-all-batt',
                        dest='show_battery',
                        action='store_true',
                        help='show all available battery state data regardless of '
                             'sensor state')
    parser.add_argument('--registered-only',
                        dest='registered_only',
                        action='store_true',
                        help='show only registered sensors')
    parser.add_argument('--unmask',
                        dest='unmask',
                        action='store_true',
                        help='unmask sensitive settings')
    parser.add_argument('--dump-api',
                        dest='dump_api',
                        action='store_true',
                        help='save every raw API response as JSON (passwords and keys masked)')
    parser.add_argument('--output',
                        dest='output',
                        metavar='FILE',
                        help='file for --dump-api output')
    parser.add_argument('--sd-files',
                        dest='sd_files',
                        action='store_true',
                        help='display the SD card and the history files on it')
    parser.add_argument('--catchup-dry-run',
                        dest='catchup_dry_run',
                        action='store_true',
                        help='display the records a catchup would save, without saving them')
    parser.add_argument('--since',
                        dest='since',
                        metavar='DATE',
                        help="start for --catchup-dry-run: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' "
                             "(default 24 hours ago)")
    parser.add_argument('--catchup-source',
                        dest='catchup_source',
                        choices=['either', 'device', 'net'],
                        help='catchup source for --catchup-dry-run (default: [[catchup]] in weewx.conf)')
    parser.add_argument('--sources',
                        dest='sources',
                        action='store_true',
                        help='display the gateways in [[sources]] and whether they answer')
    parser.add_argument('--source',
                        dest='source',
                        metavar='NAME',
                        help='with [[sources]]: the gateway to use (default: the first one)')
    parser.add_argument('--units',
                        dest='units',
                        metavar='UNIT SYSTEM',
                        default=weewx.units.unit_nicknames[DEFAULT_UNIT_SYSTEM],
                        help='unit system to use when displaying live data')
    parser.add_argument('--config',
                        dest='config',
                        metavar='CONFIG_FILE',
                        help="Use configuration file CONFIG_FILE.")
    parser.add_argument('--debug',
                        dest='driver_debug',
                        type=int,
                        help='How much status to display, 0-3')
    namespace = parser.parse_args()

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    if namespace.version:
        print(f'{DRIVER_NAME} driver version {DRIVER_VERSION}')
        sys.exit(0)

    # any other option will require the config_dict, get the config_dict now
    config_path, config_dict = weecfg.read_config(namespace.config)
    print(f'Using configuration file {bcolors.BOLD}{config_path}{bcolors.ENDC}')
    stn_config_dict = driver_config(config_dict)
    # set weewx.debug as necessary
    if namespace.driver_debug is not None:
        _debug = weeutil.weeutil.to_int(namespace.driver_debug)
    else:
        _debug = weeutil.weeutil.to_int(config_dict.get('debug', 0))
    weewx.debug = _debug
    if _debug > 0:
        print(f'debug level is {_debug:d}')

    weeutil.logger.setup('weewx-ecowittGateway', config_dict)

    define_units()
    use_station_columns(config_dict)

    try:
        direct_device = DirectEcowittDevice(namespace, parser, stn_config_dict,
                                            archive_interval=std_archive_interval(config_dict),
                                            counters=counter_fields(config_dict))
    except weewx.ViolatedPrecondition as e:
        print(e)
        sys.exit(1)
    direct_device.process_options()


if __name__ == '__main__':
    main()