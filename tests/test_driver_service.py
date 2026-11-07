"""The driver and the service, running against the fake gateway."""

import time
import types

import pytest
import weewx


def next_packet(gen, timeout=15):
    start = time.time()
    packet = next(gen)
    assert time.time() - start < timeout
    return packet


@pytest.fixture
def driver(egw, station):
    drv = egw.EcowittHttpDriver(**station)
    yield drv
    drv.closePort()


def test_driver_loop_packets(driver, gateway):
    gen = driver.genLoopPackets()
    first = next_packet(gen)
    assert first['usUnits'] == weewx.METRICWX
    assert first['outTemp'] == 12.3 and first['windSpeed'] == 3.2 and first['barometer'] == 1015.3
    assert first['rain'] is None and first['t_rain'] is None     # no delta from the first counter value
    gateway.live('rain', '0x13')['val'] = '451.0 mm'
    gateway.live('piezoRain', '0x13')['val'] = '440.3 mm'
    second = next_packet(gen)
    assert round(second['rain'], 3) == 0.4 and round(second['t_rain'], 3) == 0.4
    assert round(second['p_rain'], 3) == 0.3 and second['hail'] == second['p_rain']
    assert second['dateTime'] > first['dateTime']


def test_driver_rain_source_piezo(egw, station, gateway):
    drv = egw.EcowittHttpDriver(**{**station, 'rain_source': 'piezo'})
    try:
        gen = drv.genLoopPackets()
        next_packet(gen)
        gateway.live('piezoRain', '0x13')['val'] = '441.0 mm'
        gateway.live('piezoRain', '0x0E')['val'] = '2.4 mm/Hr'
        packet = next_packet(gen)
        assert round(packet['rain'], 3) == 1.0
        assert packet['rainRate'] == 2.4
    finally:
        drv.closePort()


def test_driver_raises_io_error_when_gateway_goes_away(driver, gateway):
    gen = driver.genLoopPackets()
    next_packet(gen)
    gateway.down = True
    with pytest.raises(weewx.WeeWxIOError):
        next_packet(gen)


def test_driver_sensor_map(egw, station):
    drv = egw.EcowittHttpDriver(**{**station, 'sensor_map': {'C0FFEE': '5'}})
    try:
        packet = next_packet(drv.genLoopPackets())
        assert packet['soilMoist5'] == 33 and 'soilMoist2' not in packet
    finally:
        drv.closePort()


def test_driver_catchup_records(driver, egw):
    rows = [{'datetime': 1000 + 300 * n, 'interval': 5, 'common_list.0x02.val': 10.0 + n,
             'rain.0x13.val': 100.0 + n * 0.5, 'piezoRain.0x13.val': 50.0} for n in range(3)]
    driver.source.catchup_factory = lambda: types.SimpleNamespace(gen_history_records=lambda start_ts=None: iter(rows))
    records = list(driver.genStartupRecords(900))
    assert [r['dateTime'] for r in records] == [1000, 1300, 1600]
    assert all(r['interval'] == 5 and r['usUnits'] == weewx.METRICWX for r in records)
    assert records[0]['rain'] is None and records[1]['rain'] == 0.5 and records[2]['rain'] == 0.5
    assert records[2]['outTemp'] == 12.0


def test_driver_without_catchup_yields_nothing(driver):
    assert list(driver.genStartupRecords(0)) == []


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
    while svc.collector.queue.empty():
        assert time.time() - start < timeout, 'no data from the fake gateway'
        time.sleep(0.1)


def test_service_augments_packets_in_their_own_units(service):
    wait_for_data(service)
    packet = {'dateTime': int(time.time()), 'usUnits': weewx.US, 'outTemp': 99.0}
    service.new_loop_packet(types.SimpleNamespace(packet=packet))
    assert packet['outTemp'] == 99.0                       # the main driver's value wins
    assert round(packet['inTemp'], 1) == 70.5              # 21.4 C in deg F
    assert round(packet['barometer'], 2) == 29.98          # hPa -> inHg


def test_service_ignores_stale_data(service):
    wait_for_data(service)
    packet = {'dateTime': int(time.time()) + 3600, 'usUnits': weewx.METRICWX}
    service.new_loop_packet(types.SimpleNamespace(packet=packet))
    assert 'inTemp' not in packet
