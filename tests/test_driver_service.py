"""The driver and the service, running against the fake gateways."""

import time
import types

import pytest
import weewx

from conftest import load_dump


def next_packet(gen, timeout=15):
    start = time.time()
    packet = next(gen)
    assert time.time() - start < timeout
    return packet


def test_driver_loop_packets(driver, gateway):
    gen = driver.genLoopPackets()
    first = next_packet(gen)
    assert first['usUnits'] == weewx.METRICWX
    assert first['outTemp'] == 12.3 and first['windSpeed'] == 3.2 and first['relbarometer'] == 1015.3
    assert first['inTemp'] == 21.4 and first['extraTemp1'] == 19.8 and first['soilMoist2'] == 33
    assert first['t_rainyear'] == 450.6 and first['p_rainyear'] == 440.0
    assert first['windDir10'] == 225 and first['t_rain24'] == 1.2       # 0x6D and 0x7C
    gateway.live('rain', '0x13')['val'] = '451.0 mm'
    gateway.live('common_list', '0x02')['val'] = '13.0'
    second = next_packet(gen)
    assert second['t_rainyear'] == 451.0 and second['outTemp'] == 13.0
    assert second['dateTime'] > first['dateTime']


def test_driver_hardware_name_and_mac(driver):
    assert driver.hardware_name == 'GW2000'
    assert driver.mac_address == 'AA:BB:CC:DD:EE:FF'


def test_driver_raises_io_error_when_gateway_goes_away(driver, gateway):
    gen = driver.genLoopPackets()
    next_packet(gen)
    gateway.down = True
    with pytest.raises(weewx.WeeWxIOError):
        next_packet(gen)


def test_driver_raises_io_error_on_an_unexpected_response(driver, gateway, caplog):
    """ecowitt_http.py 0.1.0a28: a response that wasn't the expected JSON ended the collector
    thread, and the driver then waited for data for ever."""
    gen = driver.genLoopPackets()
    next_packet(gen)
    gateway.responses['get_livedata_info'] = 'not a dict'
    with pytest.raises(weewx.WeeWxIOError):
        next_packet(gen)
    assert 'Unexpected response from the device' in caplog.text


def test_collector_survives_a_failed_firmware_check(make_driver, station, gateway):
    """ecowitt_http.py 0.1.0a28: an error during the firmware update check (eg the gateway not
    answering just then) ended the collector thread."""
    del gateway.responses['get_version']
    drv = make_driver(**{**station, 'firmware_update_check_interval': '1'})
    gen = drv.genLoopPackets()
    for _ in range(3):
        next_packet(gen)
    assert drv.collector.thread.is_alive()


def test_driver_sensor_map(make_driver, station):
    drv = make_driver(**{**station, 'sensor_map': {'C0FFEE': '5'}})
    packet = next_packet(drv.genLoopPackets())
    assert packet['soilMoist5'] == 33 and 'soilMoist2' not in packet


def test_driver_field_map_extensions(make_driver, station):
    drv = make_driver(**{**station, 'field_map_extensions': {'extraTemp9': 'ch_aisle.1.temp'}})
    packet = next_packet(drv.genLoopPackets())
    assert packet['extraTemp9'] == 19.8 and 'extraTemp1' not in packet


def test_gw1000_driver_over_the_tcp_api(make_driver, gw1000):
    drv = make_driver(ip_address='127.0.0.1', api='tcp', tcp_port=str(gw1000.port), poll_interval='1',
                      max_tries='1', retry_wait='0', firmware_update_check_interval='0',
                      catchup={'source': 'none'})
    packet = next_packet(drv.genLoopPackets())
    assert drv.hardware_name == 'GW1000'
    assert packet['outTemp'] == 12.3 and packet['inTemp'] == 21.4 and packet['windSpeed'] == 3.2
    assert packet['t_rainyear'] == 450.6 and packet['extraTemp1'] == 19.8
    assert packet['usUnits'] == weewx.METRICWX


def test_api_auto_finds_the_gw1000(egw, gw1000):
    device = egw.make_device('127.0.0.1', api='auto', tcp_port=gw1000.port, max_tries=1, retry_wait=0)
    assert isinstance(device, egw.EcowittTcpDevice) and device.model == 'GW1000'


def test_driver_catchup_records(driver):
    rows = [{'datetime': 1000 + 300 * n, 'interval': 5, 'common_list.0x02.val': 10.0 + n,
             'rain.0x13.val': 100.0 + n * 0.5} for n in range(3)]
    driver.source.catchup_factory = lambda: types.SimpleNamespace(
        gen_history_records=lambda start_ts=None: iter(rows))
    records = list(driver.genStartupRecords(900))
    assert [r['dateTime'] for r in records] == [1000, 1300, 1600]
    assert all(r['interval'] == 5 and r['usUnits'] == weewx.METRICWX for r in records)
    assert records[2]['outTemp'] == 12.0 and records[2]['t_rainyear'] == 101.0


def test_driver_without_catchup_yields_nothing(driver):
    assert list(driver.genStartupRecords(0)) == []


def test_loader_reads_the_legacy_section(egw, station):
    drv = egw.loader({'EcowittHttp': station}, None)
    try:
        assert drv.ip_address == station['ip_address']
    finally:
        drv.closePort()


class FakeEngine:
    def __init__(self):
        self.bound = {}

    def bind(self, event, callback):
        self.bound[event] = callback


@pytest.fixture
def service(egw, station):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittGateway': station})
    yield svc
    svc.shutDown()


def wait_for_data(svc, timeout=10):
    start = time.time()
    while svc.source.queue.empty():
        assert time.time() - start < timeout, 'no data from the fake gateway'
        time.sleep(0.1)


def loop_event(**packet):
    return types.SimpleNamespace(packet={'dateTime': int(time.time()), 'usUnits': weewx.US, **packet})


def test_service_is_bound_to_loop_packets(service):
    assert weewx.NEW_LOOP_PACKET in service.engine.bound


def test_service_augments_packets_in_their_own_units(service):
    wait_for_data(service)
    event = loop_event(outTemp=99.0)
    service.new_loop_packet(event)
    packet = event.packet
    assert packet['outTemp'] == 99.0                       # the main driver's value wins
    assert round(packet['inTemp'], 1) == 70.5              # 21.4 C in deg F
    assert round(packet['relbarometer'], 2) == 29.98       # hPa -> inHg
    assert round(packet['t_rainyear'], 2) == 17.74         # mm -> in
    assert packet['usUnits'] == weewx.US


def test_service_ignores_stale_data(service):
    wait_for_data(service)
    event = loop_event()
    event.packet['dateTime'] += 3600
    service.new_loop_packet(event)
    assert 'inTemp' not in event.packet


def test_service_carries_on_when_the_gateway_goes_away(service, gateway):
    wait_for_data(service)
    gateway.down = True
    while not service.source.queue.empty():
        service.source.queue.get()
    deadline = time.time() + 10
    while service.source.status.online is not False:
        assert time.time() < deadline, 'the service never saw the gateway go away'
        event = loop_event()
        service.new_loop_packet(event)                     # no exception
    assert 'inTemp' not in event.packet
    assert service.log_failures is False                   # logged once, not every loop packet
    gateway.down = False
    wait_for_data(service)
    event = loop_event()
    service.new_loop_packet(event)
    assert 'inTemp' in event.packet and service.source.status.online is True


def test_service_reads_ecowitthttpservice_first(egw, station):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittHttpService': station,
                                                'EcowittGateway': {'ip_address': '192.0.2.1'}})
    try:
        assert svc.ip_address == station['ip_address']
    finally:
        svc.shutDown()


def test_dump_fixture_is_complete():
    """Every API command the driver and 'weectl device' use is in the test fixture."""
    dump = load_dump('gw2000_ws90.json')
    for command in ('get_version', 'get_livedata_info', 'get_sensors_info?page=1', 'get_rain_totals',
                    'get_piezo_rain', 'get_device_info', 'get_units_info', 'get_network_info',
                    'get_sdmmc_info', 'get_ws_settings', 'get_calibration_data', 'get_cli_soilad',
                    'get_cli_multiCh', 'get_cli_pm25', 'get_cli_wh34', 'get_cli_co2', 'get_cli_lds'):
        assert command in dump, command
