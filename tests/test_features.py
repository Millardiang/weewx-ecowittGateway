"""1.3.0 features: limits, ignoring sensors with no signal, gateway_online and catchup checks."""

import argparse
import datetime
import io
import os
import time
import types

import pytest
import weewx

from conftest import FIXTURES
from test_driver_service import FakeEngine, next_packet, wait_for_data


def test_limits_drop_readings_outside(egw, info_log):
    limits = egw.FieldLimits({'outTemp': ['-50', '60'], 'windSpeed': ['', '50'], 'rain': ['0', '25']})
    packet = {'usUnits': weewx.METRICWX, 'outTemp': 85.0, 'windSpeed': 60.0, 'rain': 0.4, 'outHumidity': 200}
    limits.apply(packet)
    assert packet == {'usUnits': weewx.METRICWX, 'outTemp': None, 'windSpeed': None, 'rain': 0.4,
                      'outHumidity': 200}
    assert 'outTemp 85.0 is outside -50.0 to 60.0, dropped' in info_log.text


def test_limits_with_a_unit(egw):
    limits = egw.FieldLimits({'outTemp': ['-40', '130', 'degree_F']})
    ok, hot = {'usUnits': weewx.METRICWX, 'outTemp': 50.0}, {'usUnits': weewx.METRICWX, 'outTemp': 60.0}
    limits.apply(ok)
    limits.apply(hot)                                   # 60 C = 140 F
    assert ok['outTemp'] == 50.0 and hot['outTemp'] is None


@pytest.mark.parametrize('value', [['1'], ['5', '1'], ['a', '1'], ['1', '2', 'furlongs']])
def test_bad_limits_are_ignored(egw, value, caplog):
    assert not egw.FieldLimits({'outTemp': value})
    assert 'ignoring' in caplog.text


def test_limits_in_the_driver(egw, station):
    drv = egw.EcowittHttpDriver(**{**station, 'limits': {'outTemp': ['-50', '10'], 'inTemp': ['0', '40']}})
    try:
        packet = next_packet(drv.genLoopPackets())
        assert packet['outTemp'] is None and packet['inTemp'] == 21.4
    finally:
        drv.closePort()


def test_no_signal_sensor_readings_are_ignored(egw, station, gateway, info_log):
    sensors = gateway.responses['get_sensors_info?page=2']
    sensors[0]['signal'] = '0'                          # WN31 CH1
    gateway.responses['get_livedata_info']['ch_aisle'][0]['temp'] = '19.8'
    for ignore, expected in (('False', 19.8), ('True', None)):
        drv = egw.EcowittHttpDriver(**{**station, 'ignore_no_signal': ignore})
        try:
            packet = next_packet(drv.genLoopPackets())
            assert packet.get('extraTemp1') == expected
            assert packet['wh31_ch1_sig'] == 0          # the signal itself is still reported
            assert packet['soilMoist2'] == 33           # other sensors are unaffected
        finally:
            drv.closePort()
    assert 'WN31 CH1 has no signal' in info_log.text


def test_gateway_online_in_driver_packets(egw, station):
    drv = egw.EcowittHttpDriver(**station)
    try:
        assert next_packet(drv.genLoopPackets())['gateway_online'] == 1
    finally:
        drv.closePort()


def test_gateway_online_in_service_packets(egw, station, gateway):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittGateway': {**station, 'max_age': '60'}})
    try:
        wait_for_data(svc)
        packet = {'dateTime': int(time.time()), 'usUnits': weewx.US}
        svc.new_loop_packet(types.SimpleNamespace(packet=packet))
        assert packet['gateway_online'] == 1
        packet = {'dateTime': int(time.time()) + 120, 'usUnits': weewx.US}   # last data now 2 minutes old
        svc.new_loop_packet(types.SimpleNamespace(packet=packet))
        assert packet['gateway_online'] == 0
    finally:
        svc.shutDown()


NOV_2 = int(datetime.datetime(2026, 11, 2).timestamp())    # only the November file is read


@pytest.fixture
def sd_card(egw, monkeypatch):
    """Serve the SD card CSV fixture for every file (the real files are on port 81)."""
    with open(os.path.join(FIXTURES, 'sd_202611A.csv'), 'rb') as f:
        content = f.read()
    monkeypatch.setattr(egw.EcowittDeviceCatchup, 'get_file', lambda self, name: io.BytesIO(content))


def test_catchup_from_the_sd_card_warns_about_a_different_interval(egw, station, sd_card, info_log):
    drv = egw.EcowittHttpDriver(archive_interval=600, **{**station, 'catchup': {'source': 'device'}})
    try:
        records = list(drv.genStartupRecords(NOV_2))
    finally:
        drv.closePort()
    assert len(records) == 3
    assert records[1]['interval'] == 5 and round(records[1]['rain'], 3) == 0.4
    assert 'records from the SD card are every 5 minutes, but archive_interval is 10.0 minutes' in info_log.text
    assert 'Catchup: 3 record(s) from the SD card' in info_log.text


def test_catchup_applies_limits(egw, station, sd_card):
    drv = egw.EcowittHttpDriver(**{**station, 'catchup': {'source': 'device'}, 'limits': {'outTemp': ['', '10.6']}})
    try:
        temps = [r['outTemp'] for r in drv.genStartupRecords(NOV_2)]
    finally:
        drv.closePort()
    assert temps == [10.5, None, None]


def direct(egw, station, **options):
    namespace = argparse.Namespace(**{'ip_address': None, 'show_battery': None, 'only_registered': None,
                                      'max_tries': None, 'retry_wait': None, 'timeout': None, 'api': None,
                                      'since': None, 'catchup_source': None, 'no_sensor_map': None,
                                      'poll_interval': None, **options})
    return egw.DirectEcowittDevice(namespace, None, dict(station), archive_interval=300)


def test_sd_files_command(egw, station, capsys):
    direct(egw, station, sd_files=True).display_sd_files()
    out = capsys.readouterr().out
    assert 'SC16G' in out and '202610A.csv' in out and '202611A.csv' in out and 'Interval (minutes): 5' in out


def test_catchup_dry_run_command(egw, station, sd_card, capsys):
    direct(egw, station, since='2026-11-06 10:02', catchup_source='device').catchup_dry_run()
    out = capsys.readouterr().out
    assert 'Nothing is saved.' in out
    assert '2026-11-06 10:05' in out and '2026-11-06 10:00' not in out
    assert '2 record(s)' in out


def test_since_parsing(egw):
    parse = egw.DirectEcowittDevice.parse_since
    assert parse('2026-11-05') == parse('2026-11-05 00:00') == parse('2026-11-05T00:00')
    with pytest.raises(ValueError):
        parse('yesterday')
