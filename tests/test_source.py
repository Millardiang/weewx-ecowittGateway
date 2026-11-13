"""GatewaySource: everything that belongs to one gateway (the base for several gateways)."""

import queue
import time
import types

import pytest
import weewx


@pytest.fixture
def source(egw, station):
    src = egw.GatewaySource('gateway', station, label='test')
    yield src
    src.stop()


def test_driver_and_service_use_one_source(egw, station):
    drv = egw.EcowittHttpDriver(**station)
    try:
        assert isinstance(drv.source, egw.GatewaySource)
        # ecowitt_http.py names still work
        assert drv.collector is drv.source.collector and drv.mapper is drv.source.mapper
        assert drv.driver_debug is drv.source.driver_debug
        assert drv.ip_address == station['ip_address'] and drv.poll_interval == 1
        assert drv.source.label == 'EcowittHttpDriver'
    finally:
        drv.closePort()


def test_status_follows_the_polls(source, gateway):
    assert source.status.online is None
    source.start()
    item = source.get(10)
    assert hasattr(item, 'keys')
    assert source.status.online is True and source.status.last_seen == item['datetime']
    since = source.status.since
    gateway.down = True
    while hasattr(item, 'keys'):
        item = source.get(10)
    assert isinstance(item, Exception)
    assert source.status.online is False and '404' in source.status.last_error
    assert source.status.since >= since


def test_get_raises_empty_when_there_is_nothing(source):
    with pytest.raises(queue.Empty):
        source.get(0.1)


def test_loop_packet_is_in_the_sources_units(egw, station):
    source = egw.GatewaySource('gateway', station, unit_system=weewx.US)
    data = source.collector.get_current_data()
    packet = source.loop_packet(data)
    assert packet['usUnits'] == weewx.US and round(packet['outTemp'], 2) == 54.14
    assert packet['dateTime'] == data['datetime']


def test_loop_packet_without_a_time_gets_the_current_time(source):
    packet = source.loop_packet({'common_list.0x02.val': 1.0})
    assert abs(packet['dateTime'] - time.time()) < 2 and packet['outTemp'] == 1.0


def test_mapped_has_the_unit_system(source):
    assert source.mapped({'wh25.intemp': 20.0}) == {'inTemp': 20.0, 'usUnits': weewx.METRICWX}


def test_history_maps_the_catchup_records(source):
    rows = [{'datetime': 1000, 'interval': 5, 'common_list.0x02.val': 10.0}]
    source.catchup_factory = lambda: types.SimpleNamespace(gen_history_records=lambda start_ts=None: iter(rows))
    assert list(source.history(900)) == [{'dateTime': 1000, 'usUnits': weewx.METRICWX, 'interval': 5,
                                          'outTemp': 10.0}]


def test_no_history_without_a_catchup_source(source):
    assert list(source.history(0)) == []


def test_catchup_settings(egw, station):
    source = egw.GatewaySource('gateway', {**station, 'api_key': 'old',
                                           'catchup': {'source': 'device', 'grace': '30', 'retries': '5',
                                                       'app_key': 'app'}})
    assert (source.catchup_source, source.catchup_grace, source.catchup_retries) == ('device', 30, 5)
    assert (source.api_key, source.app_key) == ('old', 'app')


def test_device_catchup_gets_the_settings(egw, station):
    """ecowitt_http.py 0.1.0a28 passed the catchup retries under a name EcowittDeviceCatchup didn't
    read, and catchup source 'device' left out the grace period."""
    for source_name in ('device', 'either'):
        source = egw.GatewaySource('gateway', {**station, 'catchup': {'source': source_name, 'grace': '30',
                                                                       'retries': '5'}})
        catchup = source.catchup_factory()
        assert isinstance(catchup, egw.EcowittDeviceCatchup)
        assert catchup.catchup_grace == 30 and catchup.max_catchup_retries == 5


def test_catchup_from_ecowitt_net_needs_the_keys(egw, station):
    source = egw.GatewaySource('gateway', {**station, 'catchup': {'source': 'net'}})
    with pytest.raises(egw.CatchupObjectError):
        source.catchup_factory()


def test_either_falls_back_to_ecowitt_net(egw, station, gateway):
    del gateway.responses['get_sdmmc_info']                # no SD card
    source = egw.GatewaySource('gateway', {**station, 'catchup': {'source': 'either', 'api_key': 'k' * 36,
                                                                   'app_key': 'a' * 32}})
    assert isinstance(source.catchup_factory(), egw.EcowittNetCatchup)


def test_gw1000_catchup_only_from_ecowitt_net(egw, gw1000):
    config = {'ip_address': '127.0.0.1', 'api': 'tcp', 'tcp_port': str(gw1000.port), 'max_tries': '1',
              'retry_wait': '0', 'firmware_update_check_interval': '0'}
    with pytest.raises(egw.CatchupObjectError, match='no SD card'):
        egw.GatewaySource('gw1000', {**config, 'catchup': {'source': 'device'}}).catchup_factory()
    source = egw.GatewaySource('gw1000', {**config, 'catchup': {'source': 'either', 'api_key': 'k' * 36,
                                                                 'app_key': 'a' * 32}})
    assert isinstance(source.catchup_factory(), egw.EcowittNetCatchup)


def test_failure_logging_reaches_the_collector_and_device(source):
    source.set_failure_logging(False)
    assert not source.log_failures and not source.collector.log_failures
    assert not source.collector.device.log_failures
    source.set_failure_logging(True)
    assert source.collector.device.log_failures
