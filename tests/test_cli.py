"""'weectl device' and the driver's own command line, against the fake gateways.

Every command is run against an HTTP API gateway (GW2000) and a GW1000 (TCP API); none may end in
a traceback. ecowitt_http.py 0.1.0a28 had several that did (see the CHANGELOG)."""

import json
import optparse
import sys

import configobj
import pytest
import weeutil.logger
import weewx

WEECTL_COMMANDS = ['--live-data', '--sensors', '--firmware', '--mac-address', '--system-params',
                   '--sensor-map', '--active-map', '--get-rain-data', '--get-all-rain-data',
                   '--get-calibration', '--get-mulch-th-cal', '--get-mulch-soil-cal', '--get-mulch-t-cal',
                   '--get-pm25-cal', '--get-co2-cal', '--get-lds-cal', '--get-services', '--dump-api',
                   '--sd-files', '--catchup-dry-run']
DIRECT_COMMANDS = ['--live-data', '--sensors', '--firmware', '--mac', '--system', '--get-rain-totals',
                   '--get-calibration', '--get-th-cal', '--get-soil-cal', '--get-t-cal', '--get-pm25-cal',
                   '--get-co2-cal', '--get-lds-cal', '--get-services', '--sensor-map', '--active-map',
                   '--default-map', '--driver-map', '--service-map', '--weewx-fields', '--dump-api',
                   '--sd-files', '--catchup-dry-run']


@pytest.fixture(autouse=True)
def no_logging_setup(monkeypatch):
    # weeutil.logger.setup() would replace the test's log handlers
    monkeypatch.setattr(weeutil.logger, 'setup', lambda *args, **kwargs: None)


@pytest.fixture(params=['http', 'tcp'])
def config(request, gateway, gw1000):
    if request.param == 'http':
        section = {'ip_address': gateway.address, 'api': 'http'}
    else:
        section = {'ip_address': '127.0.0.1', 'api': 'tcp', 'tcp_port': str(gw1000.port)}
    section.update({'max_tries': '1', 'retry_wait': '0', 'url_timeout': '2', 'sensor_map': {'0xC0FFEE': '3'},
                    'catchup': {'retries': '1'}})
    return configobj.ConfigObj({'debug': '0', 'EcowittGateway': section})


def weectl_device(egw, config, *args):
    configurator = egw.configurator_loader(config)
    parser = optparse.OptionParser()
    configurator.add_options(parser)
    options, _ = parser.parse_args(list(args))
    configurator.do_options(options, parser, config, False)


@pytest.mark.parametrize('command', WEECTL_COMMANDS)
def test_weectl_device(egw, config, command, capsys):
    weectl_device(egw, config, command)
    out = capsys.readouterr().out
    assert 'Traceback' not in out
    if config['EcowittGateway']['api'] == 'http':
        assert 'Unable to connect' not in out and 'Error' not in out


@pytest.mark.parametrize('command', DIRECT_COMMANDS)
def test_direct_command_line(egw, config, command, capsys, monkeypatch, tmp_path):
    path = tmp_path / 'weewx.conf'
    config.filename = str(path)
    config.write()
    monkeypatch.setattr(sys, 'argv', ['weewx-ecowittGateway.py', command, f'--config={path}'])
    egw.main()
    out = capsys.readouterr().out
    assert 'Traceback' not in out
    if config['EcowittGateway']['api'] == 'http':
        assert 'Unable to connect' not in out and 'Error' not in out


def run(egw, config, capsys, *args):
    weectl_device(egw, config, *args)
    return capsys.readouterr().out


@pytest.fixture
def http_config(gateway):
    return configobj.ConfigObj({'EcowittGateway': {'ip_address': gateway.address, 'api': 'http',
                                                   'max_tries': '1', 'retry_wait': '0'}})


def test_sensors_lists_every_paired_sensor_once(egw, http_config, capsys):
    """ecowitt_http.py 0.1.0a28 listed the WH57 twice and left out the WH51 and WH45."""
    lines = run(egw, http_config, capsys, '--sensors').splitlines()
    for sensor in ('WS90 ', 'WH40 ', 'WH57 ', 'WH41 CH1', 'WN31 CH1', 'WH51 CH2', 'WH45 '):
        assert sum(line.startswith(sensor) for line in lines) == 1, sensor
    assert 'C0FFEE' in '\n'.join(lines)


def test_rain_totals(egw, http_config, capsys):
    out = run(egw, http_config, capsys, '--get-rain-data')
    assert 'Year rain: 450.6 mm' in out and 'Year rain: 440.0 mm' in out
    assert 'Rain4 gain: 1.40' in out


def test_rain_totals_without_piezo_totals(egw, http_config, gateway, capsys):
    """ecowitt_http.py 0.1.0a28 failed with KeyError: 'day_rain' when the device left out a total."""
    gateway.responses['get_piezo_rain'] = {'rain1_gain': '1.00'}
    out = run(egw, http_config, capsys, '--get-rain-data')
    assert 'Year rain: 450.6 mm' in out and 'Rain1 gain: 1.00' in out


def test_services_with_a_setting_missing(egw, http_config, gateway, capsys):
    """ecowitt_http.py 0.1.0a28 failed with a KeyError if the firmware left out a setting."""
    del gateway.responses['get_ws_settings']['wu_interval']
    out = run(egw, http_config, capsys, '--get-services')
    assert 'Ecowitt.net' in out and "did not report the 'wu_interval' setting" in out


def test_services_masks_ids(egw, http_config, gateway, capsys):
    gateway.responses['get_ws_settings']['wu_id'] = 'ISTATION123'
    assert 'ISTATION123' not in run(egw, http_config, capsys, '--get-services')
    assert 'ISTATION123' in run(egw, http_config, capsys, '--get-services', '--unmask')


def test_gw1000_says_what_the_tcp_api_lacks(egw, gw1000, capsys):
    config = configobj.ConfigObj({'EcowittGateway': {'ip_address': '127.0.0.1', 'api': 'tcp',
                                                     'tcp_port': str(gw1000.port), 'max_tries': '1'}})
    out = run(egw, config, capsys, '--get-calibration')
    assert 'GW1000 (TCP API) does not provide' in out and 'Unable to connect' not in out


def test_active_map_with_and_without_the_sensor_map(egw, http_config, capsys):
    http_config['EcowittGateway']['sensor_map'] = {'C0FFEE': '3'}
    assert 'soilMoist3' in run(egw, http_config, capsys, '--active-map')
    out = run(egw, http_config, capsys, '--active-map', '--no-sensor-map')
    assert 'soilMoist2' in out and 'soilMoist3' not in out


def test_live_data_in_us_units(egw, http_config, capsys):
    out = run(egw, http_config, capsys, '--live-data', '--units=us')
    assert 'outTemp' in out and '54.1' in out


def test_unreachable_gateway_is_reported(egw, capsys):
    config = configobj.ConfigObj({'EcowittGateway': {'ip_address': '127.0.0.1:9', 'api': 'http',
                                                     'max_tries': '1', 'retry_wait': '0', 'url_timeout': '1'}})
    out = run(egw, config, capsys, '--firmware')
    assert 'Traceback' not in out and ('Unable' in out or 'not' in out)


def test_direct_debug_comes_from_weewx_conf(egw, http_config, capsys, monkeypatch, tmp_path):
    """The driver's own command line ignored 'debug' in weewx.conf (--debug defaulted to 0)."""
    http_config['debug'] = '1'
    http_config.filename = str(tmp_path / 'weewx.conf')
    http_config.write()
    monkeypatch.setattr(sys, 'argv', ['weewx-ecowittGateway.py', '--firmware', f'--config={http_config.filename}'])
    egw.main()
    assert 'debug level is 1' in capsys.readouterr().out
    assert weewx.debug == 1


def test_dump_api_round_trip(egw, http_config, gateway, capsys, tmp_path):
    """--dump-api writes the format FakeGateway reads: a dump of the fake gateway gives the same data."""
    from conftest import FakeGateway
    output = tmp_path / 'dump.json'
    out = run(egw, http_config, capsys, '--dump-api', f'--output={output}')
    assert f'Saved 21 responses to {output}' in out            # 16 commands, 5 sensor pages
    dump = json.loads(output.read_text())
    assert dump['_about']['model'] == 'GW2000' and dump['_about']['secrets_masked'] is True
    assert dump['get_sensors_info?page=1'][1]['img'] == 'wh90'          # as the device sent it
    assert dump['get_livedata_info'] == gateway.responses['get_livedata_info']
    copy = FakeGateway(dump)
    try:
        station = {'ip_address': copy.address, 'api': 'http', 'max_tries': '1', 'retry_wait': '0'}
        source_a = egw.GatewaySource('a', {**station, 'ip_address': gateway.address})
        source_b = egw.GatewaySource('b', station)
        a = source_a.loop_packet(source_a.collector.get_current_data())
        b = source_b.loop_packet(source_b.collector.get_current_data())
        a.pop('dateTime'), b.pop('dateTime')
        assert a == b
    finally:
        copy.close()


def test_dump_api_masks_secrets(egw, http_config, gateway, capsys):
    gateway.responses['get_ws_settings'].update({'wu_key': 'secretkey123', 'mqtt_password': 'hunter22'})
    out = run(egw, http_config, capsys, '--dump-api')
    assert 'secretkey123' not in out and 'hunter22' not in out and '********y123' in out
    assert 'AA:BB:CC:DD:EE:FF' in out                                    # the MAC address is not a secret
    assert 'secretkey123' in run(egw, http_config, capsys, '--dump-api', '--unmask')


def test_dump_api_from_a_gw1000(egw, gw1000, capsys):
    config = configobj.ConfigObj({'EcowittGateway': {'ip_address': '127.0.0.1', 'api': 'tcp',
                                                     'tcp_port': str(gw1000.port), 'max_tries': '1'}})
    out = run(egw, config, capsys, '--dump-api')
    dump = json.loads(out[out.index('{'):out.rindex('}') + 1])
    assert dump['CMD_READ_FIRMWARE_VERSION'].replace(' ', '').startswith('0E475731303030')
    assert dump['CMD_READ_RAINDATA'] == {'_error': dump['CMD_READ_RAINDATA']['_error']}  # not in the fake


def test_a_gateway_that_never_answers_is_a_device_io_error(egw, gateway):
    """ecowitt_http.py 0.1.0a28: when every attempt timed out the API failed with
    UnboundLocalError ('resp') instead of reporting the timeout."""
    gateway.delay = 1.5
    api = egw.EcowittHttpApi(ip_address=gateway.address, max_tries=1, timeout=0.5)
    with pytest.raises(egw.DeviceIOError, match='get_version'):
        api.get_version()


@pytest.mark.parametrize('command', ['--driver-map', '--service-map'])
def test_field_map_commands_stop_polling(egw, http_config, command, capsys, monkeypatch, tmp_path):
    """ecowitt_http.py left the driver's or service's collector thread polling the device after
    --driver-map and --service-map (it was only stopped on Ctrl-C)."""
    import threading
    path = tmp_path / 'weewx.conf'
    http_config.filename = str(path)
    http_config.write()
    monkeypatch.setattr(sys, 'argv', ['weewx-ecowittGateway.py', command, f'--config={path}'])
    egw.main()
    assert 'Traceback' not in capsys.readouterr().out
    assert not any(t.name == 'EcowittHttpCollectorThread' and t.is_alive() for t in threading.enumerate())
