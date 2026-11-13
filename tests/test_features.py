"""Limits, ignoring sensors with no signal, gateway_online, catchup (SD card and Ecowitt.net) with its
checks and the sensor map, --sd-files, --catchup-dry-run, and rain in service mode."""

import argparse
import datetime
import os
import time

import pytest
import weewx
import weewx.wxxtypes

from conftest import FIXTURES
from test_driver_service import FakeEngine, loop_event, next_packet, wait_for_data

NOV_2 = int(datetime.datetime(2026, 11, 2).timestamp())     # only the November file is read


# ---------------------------------------------------------------- [[limits]]

def test_limits_drop_readings_outside(egw, info_log):
    limits = egw.FieldLimits({'outTemp': ['-50', '60'], 'windSpeed': ['', '50'], 't_rainyear': ['0', '2000']})
    packet = {'usUnits': weewx.METRICWX, 'outTemp': 85.0, 'windSpeed': 60.0, 't_rainyear': 450.6,
              'outHumidity': 200}
    limits.apply(packet)
    assert packet == {'usUnits': weewx.METRICWX, 'outTemp': None, 'windSpeed': None, 't_rainyear': 450.6,
                      'outHumidity': 200}
    assert 'outTemp 85.0 is outside -50.0 to 60.0, dropped' in info_log.text


def test_limits_with_a_unit(egw):
    limits = egw.FieldLimits({'outTemp': ['-40', '130', 'degree_F']})
    ok, hot = {'usUnits': weewx.METRICWX, 'outTemp': 50.0}, {'usUnits': weewx.METRICWX, 'outTemp': 60.0}
    limits.apply(ok)
    limits.apply(hot)                                   # 60 C = 140 F
    assert ok['outTemp'] == 50.0 and hot['outTemp'] is None


def test_limits_log_once_without_debug(egw, info_log):
    limits = egw.FieldLimits({'outTemp': ['', '10']})
    for _ in range(3):
        limits.apply({'usUnits': weewx.METRICWX, 'outTemp': 20.0})
    assert info_log.text.count('is outside') == 1


@pytest.mark.parametrize('value', [['1'], ['5', '1'], ['a', '1'], ['1', '2', 'furlongs']])
def test_bad_limits_are_ignored(egw, value, caplog):
    assert not egw.FieldLimits({'outTemp': value})
    assert 'ignoring' in caplog.text


def test_limits_in_the_driver(make_driver, station):
    drv = make_driver(**{**station, 'limits': {'outTemp': ['-50', '10'], 'inTemp': ['0', '40']}})
    packet = next_packet(drv.genLoopPackets())
    assert packet['outTemp'] is None and packet['inTemp'] == 21.4


def test_limits_in_the_service(egw, station):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittGateway': {**station, 'limits': {'outTemp': ['', '10']}}})
    try:
        wait_for_data(svc)
        event = loop_event(usUnits=weewx.US)
        svc.new_loop_packet(event)
        assert event.packet['outTemp'] is None and event.packet['inTemp'] == pytest.approx(70.52)
    finally:
        svc.shutDown()


# ------------------------------------------------------------ ignore_no_signal

def test_no_signal_sensor_readings_are_ignored(make_driver, station, gateway, info_log):
    gateway.responses['get_sensors_info?page=2'][0]['signal'] = '0'      # WN31 CH1
    for ignore, expected in (('False', 19.8), ('True', None)):
        packet = next_packet(make_driver(**{**station, 'ignore_no_signal': ignore}).genLoopPackets())
        assert packet.get('extraTemp1') == expected and packet.get('extraHumid1') == (52 if expected else None)
        assert packet['wn31_ch1_sig'] == 0              # the signal itself is still reported
        assert packet['soilMoist2'] == 33               # other sensors are unaffected
    assert 'WN31 CH1 has no signal' in info_log.text


def test_no_signal_sensor_is_logged_when_heard_again(make_driver, station, gateway, info_log):
    sensor = gateway.responses['get_sensors_info?page=2'][0]
    sensor['signal'] = '0'
    gen = make_driver(**{**station, 'ignore_no_signal': 'True'}).genLoopPackets()
    assert next_packet(gen).get('extraTemp1') is None
    sensor['signal'] = '3'
    assert next_packet(gen)['extraTemp1'] == 19.8
    assert 'WN31 CH1 has a signal again' in info_log.text


def test_no_signal_keys(egw):
    data = {'wn31.ch1.signal': 0, 'wn31.ch1.battery': 0, 'ch_aisle.1.temp': 19.8, 'ch_aisle.1.humidity': 52,
            'wh51.ch2.signal': 2, 'ch_soil.2.humidity': 33, 'ch_aisle.10.temp': 5.0}
    assert egw.no_signal_keys(data) == {('wn31', 1): ['ch_aisle.1.temp', 'ch_aisle.1.humidity']}


# -------------------------------------------------------------- gateway_online

def test_gateway_online_in_driver_packets(driver):
    assert next_packet(driver.genLoopPackets())['gateway_online'] == 1


def test_gateway_online_in_service_packets(egw, station, gateway):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittGateway': {**station, 'max_age': '60'}})
    try:
        wait_for_data(svc)
        event = loop_event(usUnits=weewx.US)
        svc.new_loop_packet(event)
        assert event.packet['gateway_online'] == 1
        event = loop_event(usUnits=weewx.US, dateTime=int(time.time()) + 120)   # data now 2 minutes old
        svc.new_loop_packet(event)
        assert event.packet['gateway_online'] == 0 and 'outTemp' not in event.packet
    finally:
        svc.shutDown()


def test_gateway_online_has_a_unit_group(egw):
    assert weewx.units.obs_group_dict['gateway_online'] == 'group_boolean'


# ----------------------------------------------------------- SD card catchup

@pytest.fixture
def sd_card(gateway):
    """The fake gateway serving the SD card history file (port 81 on a real gateway) on its own port."""
    with open(os.path.join(FIXTURES, 'sd_202611A.csv'), encoding='utf-8') as f:
        gateway.files['202611A.csv'] = f.read()
    return gateway


@pytest.fixture
def sd_station(station, sd_card):
    return {**station, 'catchup': {'source': 'device', 'file_port': str(sd_card.port), 'retries': '1'}}


def test_sd_file_url_ignores_the_api_port(egw):
    """ecowitt_http.py 0.1.0a28 built 'http://host:PORT:81/...' ('Name or service not known')."""
    assert egw.sd_file_url('192.168.1.100:8080', '202611A.csv') == 'http://192.168.1.100:81/202611A.csv'
    assert egw.sd_file_url('192.168.1.100', '202611A.csv') == 'http://192.168.1.100:81/202611A.csv'
    assert egw.sd_file_url('fe80::1', 'x.csv', 8081) == 'http://[fe80::1]:8081/x.csv'
    assert egw.sd_file_url('[fe80::1]:80', 'x.csv') == 'http://[fe80::1]:81/x.csv'


def test_sd_file_list_skips_files_that_are_not_history_files(egw):
    """ecowitt_http.py 0.1.0a28 stopped the catchup with a ValueError at a file such as 'log'."""
    sd_info = {'file_list': [{'name': 'log', 'type': 'file'}, {'name': '202611A.csv', 'type': 'file'},
                             {'name': '202610A.csv', 'type': '1'}, {'name': 'System', 'type': 'dir'},
                             {'name': '202613A.csv', 'type': 'file'}]}
    assert egw.EcowittDeviceCatchup.get_file_list(sd_info) == {202610: ['202610A.csv'], 202611: ['202611A.csv']}
    assert egw.EcowittDeviceCatchup.get_file_list(sd_info, NOV_2) == {202611: ['202611A.csv']}


def test_sd_card_catchup_records(make_driver, sd_station, sd_card, info_log):
    sd_card.responses['get_sdmmc_info']['file_list'].append({'name': 'log', 'type': 'file', 'size': '1 KB'})
    records = list(make_driver(**sd_station).genStartupRecords(NOV_2))
    assert [r['outTemp'] for r in records] == [10.5, 10.7, 10.8]
    assert [r['t_rainyear'] for r in records] == [449.4, 449.8, 450.0]
    assert all(isinstance(r['dateTime'], int) and r['interval'] == 5 for r in records)
    assert records[1]['dateTime'] - records[0]['dateTime'] == 300
    assert 'Catchup: 3 record(s) from the SD card' in info_log.text
    assert '202611A.csv' in sd_card.requests and 'log' not in sd_card.requests


def test_catchup_warns_about_a_different_interval(make_driver, sd_station, info_log):
    list(make_driver(archive_interval=600, **sd_station).genStartupRecords(NOV_2))
    assert 'records from the SD card are every 5 minutes, but archive_interval is 10.0 minutes' in info_log.text


def test_catchup_with_the_same_interval_does_not_warn(make_driver, sd_station, info_log):
    list(make_driver(archive_interval=300, **sd_station).genStartupRecords(NOV_2))
    assert 'but archive_interval' not in info_log.text


def test_the_loader_passes_archive_interval(egw, sd_station, info_log):
    drv = egw.loader({'StdArchive': {'archive_interval': '600'}, 'EcowittGateway': sd_station}, None)
    try:
        assert drv.std_archive_interval == 600
    finally:
        drv.closePort()


def test_catchup_applies_limits(make_driver, sd_station):
    records = make_driver(**{**sd_station, 'limits': {'outTemp': ['', '10.6']}}).genStartupRecords(NOV_2)
    assert [r['outTemp'] for r in records] == [10.5, None, None]


def test_catchup_applies_the_sensor_map(make_driver, sd_station, sd_card):
    """The WN31 on device channel 1 (ID 5E) is reported as channel 3, in live data and in catchup records."""
    with open(os.path.join(FIXTURES, 'sd_202611A.csv'), encoding='utf-8') as f:
        lines = f.read().splitlines()
    lines[0] += ',CH1 Temperature(℃),CH1 Humidity(%)'
    lines[1:] = [line + ',19.5,51' for line in lines[1:]]
    sd_card.files['202611A.csv'] = '\n'.join(lines) + '\n'
    drv = make_driver(**{**sd_station, 'sensor_map': {'5E': '3'}})
    records = list(drv.genStartupRecords(NOV_2))
    assert records[0]['extraTemp3'] == 19.5 and 'extraTemp1' not in records[0]
    assert next_packet(drv.genLoopPackets())['extraTemp3'] == 19.8


def test_sd_card_catchup_without_the_file(make_driver, sd_station, sd_card, caplog):
    del sd_card.files['202611A.csv']
    assert list(make_driver(**sd_station).genStartupRecords(NOV_2)) == []
    assert "Unable to download history file '202611A.csv'" in caplog.text


def test_sd_card_file_lost_while_reading(egw, make_driver, sd_station, monkeypatch, caplog):
    """A connection lost while a history file was being read ended the catchup with the error."""
    class Broken:
        def readlines(self):
            raise TimeoutError('timed out')
    monkeypatch.setattr(egw.EcowittDeviceCatchup, 'get_file', lambda self, name: Broken())
    assert list(make_driver(**sd_station).genStartupRecords(NOV_2)) == []
    assert "Unable to read history file '202611A.csv'" in caplog.text and 'timed out' in caplog.text


# --------------------------------------------------------- Ecowitt.net catchup

def net_day(timestamps):
    def values(*v):
        return {'unit': '', 'list': {str(t): str(x) for t, x in zip(timestamps, v)}}
    return {'code': 0, 'msg': 'success', 'data': {
        'outdoor': {'temperature': values(10.1, 10.2, 10.3), 'humidity': values(80, 81, 82)},
        'indoor': {'temperature': values(21.0, 21.1, 21.2)},
        'pressure': {'relative': values(1015.1, 1015.2, 1015.3)},
        'wind': {'wind_speed': values(2.1, 2.2, 2.3)},
        'rainfall': {'yearly': values(450.0, 450.2, 450.6), 'hourly': values(0.1, 0.2, 0.3),
                     'daily': values(1.0, 1.2, 1.6)},
        'rainfall_piezo': {'yearly': values(440.0, 440.1, 440.2)},
        'temp_and_humidity_ch1': {'temperature': values(19.0, 19.1, 19.2)},
        'soil_ch2': {'soilmoisture': values(33, 34, 35)}}}


def test_ecowitt_net_catchup_records(egw, make_driver, station, monkeypatch, info_log):
    """ecowitt_http.py 0.1.0a28 mapped Ecowitt.net data to names that are not in the field map
    ('outtemp', 'temp1' ...), so its records had no readings; it also swapped hourly and daily rain."""
    now = int(time.time()) // 300 * 300
    timestamps = [now - 900, now - 600, now - 300]
    requests = []

    def request(self, command_str, data=None, headers=None, max_tries=3):
        requests.append((command_str, data))
        return net_day(timestamps)
    monkeypatch.setattr(egw.EcowittNetCatchup, 'request', request)
    drv = make_driver(**{**station, 'catchup': {'source': 'net', 'api_key': 'key', 'app_key': 'app'}})
    records = list(drv.genStartupRecords(now - 1000))
    assert [r['dateTime'] for r in records] == timestamps
    first = records[0]
    assert first['outTemp'] == 10.1 and first['outHumidity'] == 80 and first['inTemp'] == 21.0
    assert first['relbarometer'] == 1015.1 and first['windSpeed'] == 2.1
    assert first['t_rainyear'] == 450.0 and first['p_rainyear'] == 440.0
    assert first['t_rainhour'] == 0.1 and first['t_rainday'] == 1.0
    assert first['extraTemp1'] == 19.0 and first['soilMoist2'] == 33
    assert requests[0][0] == 'history' and requests[0][1]['mac'] == 'AA:BB:CC:DD:EE:FF'
    assert 'Catchup: 3 record(s) from Ecowitt.net' in info_log.text


def test_ecowitt_net_catchup_without_keys_saves_nothing(make_driver, station):
    assert list(make_driver(**{**station, 'catchup': {'source': 'net'}}).genStartupRecords(NOV_2)) == []


# ----------------------------------------------------------- command line

def direct(egw, station, archive_interval=300, **options):
    namespace = argparse.Namespace(**{'ip_address': None, 'show_battery': None, 'only_registered': None,
                                      'max_tries': None, 'retry_wait': None, 'timeout': None, 'api': None,
                                      'since': None, 'catchup_source': None, 'no_sensor_map': None,
                                      'poll_interval': None, **options})
    return egw.DirectEcowittDevice(namespace, None, dict(station), archive_interval=archive_interval)


def test_sd_files_command(egw, sd_station, sd_card, capsys):
    sd_card.responses['get_sdmmc_info']['file_list'].append({'name': 'log', 'type': 'file', 'size': '1 KB'})
    direct(egw, sd_station, sd_files=True).process_options()
    out = capsys.readouterr().out
    assert 'SC16G' in out and '202610A.csv' in out and '202611A.csv' in out and 'Interval (minutes): 5' in out
    assert '2 history file(s)' in out and 'not a history file' in out


def test_sd_files_on_a_gw1000(egw, gw1000, capsys):
    station = {'ip_address': '127.0.0.1', 'api': 'tcp', 'tcp_port': str(gw1000.port), 'max_tries': '1'}
    direct(egw, station, sd_files=True).process_options()
    assert 'GW1000 has no SD card' in capsys.readouterr().out


def test_catchup_dry_run_command(egw, sd_station, capsys, info_log):
    direct(egw, {**sd_station, 'catchup': {**sd_station['catchup'], 'source': 'net'}}, archive_interval=600,
           since='2026-11-06 10:02', catchup_source='device', catchup_dry_run=True).process_options()
    out = capsys.readouterr().out
    assert 'Catchup source: device' in out and 'Nothing is saved.' in out
    assert '2026-11-06 10:05' in out and '2026-11-06 10:00' not in out
    assert '2 record(s).' in out and 'The tipping gauge counted 0.2 mm' in out
    assert 'but archive_interval is 10.0 minutes' in info_log.text


def test_catchup_dry_run_with_no_records(egw, station, capsys):
    direct(egw, station, catchup_dry_run=True).process_options()
    assert 'No records' in capsys.readouterr().out


def test_catchup_dry_run_bad_date(egw, station, capsys):
    direct(egw, station, catchup_dry_run=True, since='yesterday').process_options()
    assert "'yesterday' is not a date" in capsys.readouterr().out


def test_since_parsing(egw):
    parse = egw.DirectEcowittDevice.parse_since
    assert parse('2026-11-05') == parse('2026-11-05 00:00') == parse('2026-11-05T00:00')
    with pytest.raises(ValueError):
        parse('yesterday')


# --------------------------------------- service-mode rain
#
# The service adds the gauges' yearly totals and WeeWX works out t_rain/p_rain/rain from them
# ([StdWXCalculate] [[Delta]]). These tests check that no rain is lost, doubled or put in the wrong
# units that way.

def delta_rain(packets):
    """t_rain for each packet as WeeWX's [[Delta]] works it out from t_rainyear."""
    delta = weewx.wxxtypes.Delta({'t_rain': {'input': 't_rainyear'}})
    result = []
    for packet in packets:
        try:
            result.append(delta.get_scalar('t_rain', packet, None).value)
        except weewx.CannotCalculate:
            result.append('-')
    return result


def wait_for_polls(svc, count=2, timeout=15):
    """Wait until the collector has queued count more polls (so the last one has any change made now)."""
    start, target = time.time(), svc.source.queue.qsize() + count
    while svc.source.queue.qsize() < target:
        assert time.time() - start < timeout, 'no data from the fake gateway'
        time.sleep(0.1)


@pytest.fixture
def service(egw, station):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittGateway': {**station, 'max_age': '600'}})
    yield svc
    svc.shutDown()


def test_service_rain_from_several_polls_is_all_counted(service, gateway):
    """The rain of every poll between two loop packets is counted, not just the last poll's."""
    wait_for_data(service)
    first = loop_event(usUnits=weewx.METRICWX)
    service.new_loop_packet(first)
    for total in ('451.0 mm', '451.4 mm'):                  # two polls before the next loop packet
        gateway.live('rain', '0x13')['val'] = total
        wait_for_polls(service)
    second = loop_event(usUnits=weewx.METRICWX)
    service.new_loop_packet(second)
    assert delta_rain([first.packet, second.packet]) == [None, pytest.approx(0.8)]


def test_service_rain_totals_are_converted(service):
    """t_rain/p_rain are in the packet's units (inches in US-unit packets)."""
    wait_for_data(service)
    event = loop_event(usUnits=weewx.US)
    service.new_loop_packet(event)
    assert event.packet['t_rainyear'] == pytest.approx(450.6 / 25.4)
    assert event.packet['p_rainyear'] == pytest.approx(440.0 / 25.4)


def test_service_poll_without_a_rain_total_counts_nothing_twice(service, gateway):
    """A poll with no rain total doesn't repeat the previous poll's rain."""
    wait_for_data(service)
    packets = []
    for total in ('450.6 mm', None, '451.0 mm'):
        rain = gateway.responses['get_livedata_info']['rain']
        if total is None:
            gateway.responses['get_livedata_info']['rain'] = [r for r in rain if r['id'] != '0x13']
        else:
            if not any(r['id'] == '0x13' for r in rain):
                rain.append({'id': '0x13', 'val': total})
            gateway.live('rain', '0x13')['val'] = total
        wait_for_polls(service)
        event = loop_event(usUnits=weewx.METRICWX)
        service.new_loop_packet(event)
        packets.append(event.packet)
    assert 't_rainyear' not in packets[1]
    assert delta_rain(packets) == [None, '-', pytest.approx(0.4)]


def test_rain_fields_weewx_calculates_have_unit_groups(egw):
    """t_rain and p_rain had no unit group, so unit conversion (ecwLoop.json, MQTT) left them as they were."""
    packet = {'dateTime': 1700000000, 'usUnits': weewx.US, 't_rain': 0.1, 'p_rain': 0.2}
    exported = egw.export_packet(packet, 'metricwx')
    assert exported['t_rain'] == pytest.approx(2.54) and exported['p_rain'] == pytest.approx(5.08)
