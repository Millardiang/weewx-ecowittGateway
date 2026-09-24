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

Install with:
    weectl extension install weewx-EcowittGateway.zip      (WeeWX 5)
    wee_extension --install=weewx-EcowittGateway.zip       (WeeWX 4)
then select and configure the driver with:
    weectl station reconfigure --driver=user.weewx-EcowittGateway
"""

import io

import configobj
import weewx

try:
    from weecfg.extension import ExtensionInstaller     # WeeWX 5
except ImportError:
    from setup import ExtensionInstaller                # WeeWX 4

VERSION = '0.0.1b1'
MODULE = 'weewx-EcowittGateway'
REQUIRED_WEEWX = 4


def _rng(fmt, n, start=1):
    return [fmt.format(i) for i in range(start, n + 1)]


# accumulator extractor: fields (as used by the driver's config editor)
EXTRACTORS = {
    'sum': ['lightning_strike_count', 'lightning_noise_count', 't_rain', 'p_rain', 'hail'],
    'max': ['rainRate', 'rrain_piezo', 'p_rainrate'],
    'last': [*_rng('gain{}', 5, 0), 'lightning_distance', 'lightning_last_det_time', 'lightningcount',
             'maxdailygust', 'daymaxwind', 'windspdmph_avg10m', 'winddir_avg10m', 'stormRain', 'hourRain',
             'dayRain', 'weekRain', 'monthRain', 'yearRain', 'totalRain', 'erain_piezo', 'hrain_piezo',
             'drain_piezo', 'wrain_piezo', 'mrain_piezo', 'yrain_piezo', 'totalRain_piezo', 'p_eventrain',
             'p_hourrain', 'p_dayrain', 'p_weekrain', 'p_monthrain', 'p_yearrain', 'dayHail', 'vpd',
             *_rng('depth_ch{}', 4), *_rng('pm2_5{}_24hav', 4), '24havpm255', *_rng('pm2_5{}_24h_avg', 5),
             'pm10_24h_avg', 'co2_24h_avg', 'wh25_batt', 'wh26_batt', *_rng('wh31_ch{}_batt', 8),
             *_rng('wn35_ch{}_batt', 8), 'wh40_batt', 'wn20_batt', 'wn38_batt', *_rng('wh41_ch{}_batt', 4),
             'wh45_batt', *_rng('wh51_ch{}_batt', 16), *_rng('wh55_ch{}_batt', 4), 'wh57_batt', 'wh65_batt',
             'wh68_batt', 'wh69_batt', 'ws80_batt', 'ws85_batt', 'ws90_batt', 'ws85cap_volt', 'ws90cap_volt',
             'ws1900batt', 'console_batt', 'consoleext_batt', *_rng('ldsbatt{}', 4)],
}
FIRSTLAST = ('model', 'stationtype', 'apName')

DRIVER_CONFIG = f"""
[EcowittHttp]
    # This section is for the weewx-EcowittGateway driver/service.

    # the driver to use
    driver = user.{MODULE}

    # IP address of the gateway/console, eg 192.168.1.100
    ip_address = replace_me

    # how often to poll the device (seconds)
    poll_interval = 20
    # how many attempts to contact the device before giving up
    max_tries = 3
    # wait time in seconds between retries to contact the device
    retry_wait = 2
    # max wait for device to respond to a HTTP request (seconds)
    url_timeout = 10

    # whether to show battery state data for sensors with no signal
    show_all_batt = False
    # whether to log unknown API fields at the info level
    log_unknown_fields = False
    # how often to check for device firmware updates (seconds), 0 disables
    firmware_update_check_interval = 86400
"""


def loader():
    return EcowittGatewayInstaller()


class EcowittGatewayInstaller(ExtensionInstaller):
    def __init__(self):
        if int(weewx.__version__.split('.')[0]) < REQUIRED_WEEWX:
            raise weewx.UnsupportedFeature(f'WeeWX {REQUIRED_WEEWX} or later is required, '
                                           f'found {weewx.__version__}')
        config = configobj.ConfigObj(io.StringIO(DRIVER_CONFIG))
        accum = {f: {'extractor': x} for x, fields in EXTRACTORS.items() for f in fields}
        for f in FIRSTLAST:
            accum[f] = {'accumulator': 'firstlast', 'extractor': 'last'}
        config['Accumulator'] = accum
        super().__init__(
            version=VERSION,
            name='weewx-EcowittGateway',
            description='WeeWX driver/service for Ecowitt gateways and consoles using the local HTTP API.',
            author='Ian Millard',
            author_email='',
            files=[('bin/user', [f'bin/user/{MODULE}.py'])],
            config=config,
        )

    def configure(self, engine):
        """Tell the user how to finish setting up; existing settings are never changed."""
        engine.printer.out('')
        engine.printer.out('weewx-EcowittGateway installed. To use it as the station driver run:')
        engine.printer.out(f'    weectl station reconfigure --driver=user.{MODULE}')
        engine.printer.out('This sets the gateway IP address and poll interval, rain gauge handling,')
        engine.printer.out("software record generation and 'loop_on_init'.")
        engine.printer.out('To use it as a service instead, set ip_address in [EcowittHttp] and add')
        engine.printer.out(f'    user.{MODULE}.EcowittHttpService')
        engine.printer.out('to data_services in [Engine] [[Services]].')
        return False
