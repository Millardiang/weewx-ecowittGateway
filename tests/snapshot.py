"""Snapshots of what the gateway API parser and the command-line tool produce, to show that a change
meant to keep their behaviour (tidying, refactoring) keeps it. 'python3 tests/snapshot.py --write'
records them (tests/fixtures/snapshot.json); tests/test_snapshot.py compares."""

import contextlib
import copy
import hashlib
import io
import json
import logging
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, 'fixtures', 'snapshot.json')

# sensors the fixture's gateway doesn't have, from the API examples in the driver's docstrings
EXTRA_LIVEDATA = {
    'ch_temp': [{'channel': '1', 'name': 'Flower Soil', 'temp': '60.0', 'unit': 'C', 'battery': '3',
                 'voltage': '1.54'},
                {'channel': '2', 'name': 'Vegetable Soil', 'temp': '23.0', 'unit': 'C', 'battery': '3',
                 'voltage': '1.54'}],
    'ch_lds': [{'channel': '1', 'name': '', 'unit': 'mm', 'battery': '5', 'voltage': '3.22', 'air': '3735 mm',
                'depth': '--.-'},
               {'channel': '2', 'name': '', 'unit': 'mm', 'battery': '4', 'voltage': '3.23', 'air': '735 mm',
                'depth': '341 mm'}],
    'leaf': [{'channel': '1', 'name': 'Rose', 'battery': '5', 'humidity': '12%'},
             {'channel': '2', 'name': '', 'battery': '2', 'humidity': '0%'}],
    'ch_leak': [{'channel': '1', 'name': '', 'battery': '4', 'status': 'Normal'},
                {'channel': '2', 'name': 'Bath', 'battery': '1', 'status': 'Leaking'}],
    'debug': [{'heap': '114512', 'runtime': '1009602', 'usr_interval': '30', 'is_cnip': False}],
}
CO2_F = [{'temp': '81.0', 'unit': 'F', 'humidity': '60%', 'PM25': '15.0', 'PM25_RealAQI': '57',
          'PM25_24HAQI': '57', 'PM10': '15.1', 'PM10_RealAQI': '14', 'PM10_24HAQI': '14', 'CO2': '314',
          'CO2_24H': '314', 'battery': '6'}]
# other units the gateway can report in
UNIT_SWAPS = ((' hPa', ' inHg'), (' km/h', ' mph'), (' m/s', ' knots'), (' mm/Hr', ' in/Hr'),
              (' mm', ' in'), (' Klux', ' W/m2'), (' W/m2', ' Klux'), (' km', ' mi'))

# (method of EcowittDevice, API calls it reads)
GETTERS = (('get_live_data', ('get_livedata_info',)),
           ('get_sensors_data', tuple(f'get_sensors_info?page={n}' for n in range(1, 6))),
           ('get_rain_totals', ('get_rain_totals', 'get_units_info')),
           ('get_piezo_rain_data', ('get_piezo_rain', 'get_units_info')),
           ('get_wn34_offset_data', ('get_cli_wh34', 'get_units_info')),
           ('get_pm25_offset_data', ('get_cli_pm25', 'get_units_info')),
           ('get_co2_offset_data', ('get_cli_co2', 'get_units_info')),
           ('get_lds_offset_data', ('get_cli_lds',)),
           ('get_calibration_data', ('get_calibration_data', 'get_units_info')),
           ('get_multich_calibration_data', ('get_cli_multiCh', 'get_units_info')),
           ('get_soil_calibration_data', ('get_cli_soilad',)),
           ('get_device_info_data', ('get_device_info',)),
           ('get_ws_settings', ('get_ws_settings',)),
           ('get_sdmmc_info_data', ('get_sdmmc_info',)),
           ('get_device_units', ('get_units_info',)))

# command-line options, run against the fake gateway (output with the address replaced)
CLI_OPTIONS = ('--version', '--live-data', '--sensors', '--registered-only', '--weewx-fields', '--default-map',
               '--driver-map', '--service-map', '--sensor-map', '--active-map', '--firmware', '--mac', '--system',
               '--get-rain-totals', '--get-calibration', '--get-th-cal', '--get-soil-cal', '--get-t-cal',
               '--get-pm25-cal', '--get-co2-cal', '--get-lds-cal', '--get-services', '--sources')
# extra arguments run with some options
CLI_EXTRA = {'--live-data': (('--units', 'metricwx'), ('--units', 'us'), ('--show-all-batt',)),
             '--sensors': (('--show-all-batt',), ('--unmask',)),
             '--system': (('--unmask',),), '--active-map': (('--no-sensor-map',),)}


def leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, path + (i,))
    else:
        yield path, obj


def put(obj, path, value, delete=False):
    for p in path[:-1]:
        obj = obj[p]
    if delete:
        del obj[path[-1]]
    else:
        obj[path[-1]] = value


def mapped(obj, fn):
    if isinstance(obj, dict):
        return {k: mapped(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mapped(v, fn) for v in obj]
    return fn(obj)


def drop_alternate(obj):
    if isinstance(obj, dict):
        return {k: drop_alternate(v) for i, (k, v) in enumerate(obj.items()) if i % 2 == 0}
    if isinstance(obj, list):
        return [drop_alternate(v) for v in obj]
    return obj


def swap_units(value):
    if not isinstance(value, str):
        return value
    if value in ('C', 'F'):
        return 'F' if value == 'C' else 'C'
    for a, b in UNIT_SWAPS:
        if value.endswith(a):
            return value[:-len(a)] + b
    return value


def variants(name, response):
    """(label, response) variants of one API response."""
    yield 'base', response
    yield 'dash', mapped(response, lambda v: '--' if isinstance(v, str) else v)
    yield 'junk', mapped(response, lambda v: 'abc' if isinstance(v, str) else v)
    yield 'empty', mapped(response, lambda v: '' if isinstance(v, str) else v)
    yield 'drop', drop_alternate(response)
    yield 'units', mapped(response, swap_units)
    yield 'none', None
    if name == 'get_livedata_info':
        full = dict(response, **EXTRA_LIVEDATA)
        yield 'extra', full
        yield 'extra-units', mapped(full, swap_units)
        yield 'co2-F', dict(response, co2=CO2_F)
        yield 'unknown-group', dict(response, newthing=[{'x': '1'}])
        for path, value in leaves(full):
            for label, new, delete in (('dash', '--', False), ('junk', 'x1', False), ('del', None, True)):
                v = copy.deepcopy(full)
                put(v, path, new, delete)
                yield f'{label}:{"/".join(map(str, path))}', v


# what changes from run to run: times, the temporary config file
TIMES = re.compile(r'\d{4}-\d\d-\d\d[ T]\d\d:\d\d(:\d\d)?( \w+)?( \(\d{9,11}\))?|\d\d-\w{3}-\d{4} \d\d:\d\d'
                   r'|\b1[789]\d{8}\b')
TEMP_DIR = re.compile(r'/tmp/\S+/weewx\.conf')


def canonical(obj):
    return json.dumps(obj, sort_keys=True, default=repr)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()[:16]


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        try:
            self.messages.append(record.getMessage())
        except Exception as e:                 # a broken log call is behaviour too
            self.messages.append(f'LOGERROR {type(e).__name__}')


def parser_cases(egw, fake_gateway, fixture):
    """{case: digest} for every getter, response variant and unit system."""
    import weewx
    results = {}
    logger = logging.getLogger()
    gw = fake_gateway(fixture)
    for unit_name, unit_system in (('US', weewx.US), ('METRIC', weewx.METRIC), ('METRICWX', weewx.METRICWX)):
        for getter, calls in GETTERS:
            for call in calls[:1]:
                for label, response in variants(call, fixture[call]):
                    gw.responses = dict(copy.deepcopy(fixture), **{call: response})
                    capture = Capture()
                    logger.addHandler(capture)
                    level = logger.level
                    logger.setLevel(logging.DEBUG)
                    try:
                        dev = egw.EcowittDevice(ip_address=gw.address, unit_system=unit_system, max_tries=1,
                                                retry_wait=0)
                        try:
                            out = getattr(dev, getter)()
                            if isinstance(out, dict):
                                out = {k: v for k, v in out.items() if k != 'datetime'}
                        except Exception as e:
                            out = f'EXC {type(e).__name__}: {e}'
                    finally:
                        logger.removeHandler(capture)
                        logger.setLevel(level)
                    results[f'{unit_name} {getter} {label}'] = digest([out, capture.messages])
    gw.close()
    return results


# gateway states for the command-line runs: as the fixture, only the calls a device object needs,
# every call answering null, no gateway
GATEWAY_STATES = ('ok', 'sparse', 'null', 'down')
FAST = ('--max-tries', '1', '--retry-wait', '0', '--timeout', '2')


def gateway_responses(fixture, state):
    if state == 'sparse':
        return {k: v for k, v in fixture.items() if k in ('get_version', 'get_network_info', 'get_units_info')}
    if state == 'null':
        return {k: (v if k == 'get_version' else None) for k, v in fixture.items()}
    return copy.deepcopy(fixture)


def cli_cases(egw, fake_gateway, fixture, config_file):
    results = {}
    gw = fake_gateway(fixture)
    down = fake_gateway(fixture)
    down_address = down.address
    down.close()
    try:
        for option, state in [(o, st) for o in CLI_OPTIONS for st in GATEWAY_STATES]:
            gw.responses = gateway_responses(fixture, state)
            address = down_address if state == 'down' else gw.address
            for extra in ((),) + (CLI_EXTRA.get(option, ()) if state == 'ok' else ()):
                argv = ['weewx-ecowittGateway.py', option, '--ip-address', address, *extra, '--config',
                        config_file] + ([] if state == 'ok' else list(FAST))
                out = io.StringIO()
                old_argv = sys.argv
                sys.argv = argv
                try:
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                        try:
                            egw.main()
                        except SystemExit:
                            pass
                        except Exception as e:
                            print(f'EXC {type(e).__name__}: {e}')
                finally:
                    sys.argv = old_argv
                text = TIMES.sub('TIME', out.getvalue().replace(address, 'GATEWAY'))
                text = TEMP_DIR.sub('CONFIG', text)
                key = ' '.join([option, *extra]) + ('' if state == 'ok' else f' [{state}]')
                results[key] = {'digest': digest(text), 'lines': len(text.splitlines())}
    finally:
        gw.close()
    return results


def collect(config_file):
    sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), 'bin')]
    import conftest
    egw = conftest.egw_module
    egw.define_units()          # as the driver does when it starts
    fixture = conftest.load_dump('gw2000_ws90.json')
    return {'parser': parser_cases(egw, conftest.FakeGateway, fixture),
            'cli': cli_cases(egw, conftest.FakeGateway, fixture, config_file)}


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        conf = os.path.join(d, 'weewx.conf')
        with open(conf, 'w') as f:
            f.write('[Station]\n    station_type = EcowittGateway\n[EcowittGateway]\n'
                    '    driver = user.weewx-ecowittGateway\n')
        snap = collect(conf)
    if '--show' in sys.argv:
        for k, v in snap['cli'].items():
            print(f"{v['lines']:5} {k}")
    if '--write' in sys.argv:
        with open(SNAPSHOT, 'w') as f:
            json.dump(snap, f, indent=0, sort_keys=True)
    print(len(snap['parser']), 'parser cases,', len(snap['cli']), 'command-line cases')
