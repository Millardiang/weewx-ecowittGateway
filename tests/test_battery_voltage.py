"""Sensor battery voltages: the WH40's from the HTTP API's live rain data, and the WH68's and
WS80's from the TCP API's sensor list (the HTTP API gives only a level for those)."""

import logging

import pytest

from conftest import FakeGateway, FakeGw1000, load_dump


@pytest.fixture
def gateways():
    """A fake HTTP gateway with a WH68 and WS80 paired, and a fake TCP API on the same host."""
    dump = load_dump('gw2000_ws90.json')
    # as a GW3000 reports them (Ian's and Paul's stations): the WH40's voltage in its rain data,
    # a paired WH68 and WS80
    for item in dump['get_livedata_info']['rain']:
        if item['id'] == '0x13':
            item['voltage'] = '1.6'
    dump['get_sensors_info?page=1'].append({'img': 'wh68', 'type': '1', 'name': 'Solar & Wind', 'id': '45A',
                                            'batt': '5', 'rssi': '-53', 'signal': '4', 'idst': '1'})
    for sensor in dump['get_sensors_info?page=1']:
        if sensor['img'] == 'wh80':
            sensor.update(id='C1', batt='5', rssi='-60', signal='4')
    http = FakeGateway(dump)
    tcp = FakeGw1000()
    # WH68 (address 1) 95 x 0.02 = 1.90 V, WS80 (2) 160 x 0.02 = 3.20 V, WH40 (3) registering
    tcp.data[0x3C] = (FakeGw1000.sensor(1, 0x45A, 95, 4) + FakeGw1000.sensor(2, 0xC1, 160, 4)
                      + FakeGw1000.sensor(3, 0xFFFFFFFF, 0, 0))
    yield http, tcp
    http.close()
    tcp.close()


def device(egw, gateways, **kwargs):
    http, tcp = gateways
    return egw.EcowittDevice(http.address, max_tries=1, retry_wait=0, tcp_port=tcp.port, **kwargs)


def test_rssi_is_kept(egw, gateways):
    data = device(egw, gateways).get_sensors_data()
    assert data['wh40.rssi'] == -71 and data['wh68.rssi'] == -53


def test_wh68_voltage_from_the_tcp_api(egw, gateways):
    dev = device(egw, gateways)
    data = dev.get_sensors_data()
    assert data['wh68.voltage'] == 1.9 and data['wh68.battery'] == 5
    nested = dev.get_sensors_data(flatten_data=False)
    assert nested['wh68']['voltage'] == 1.9


def test_voltages_are_read_again_only_every_few_minutes(egw, gateways):
    _, tcp = gateways
    dev = device(egw, gateways)
    dev.get_sensors_data()
    dev.get_sensors_data()
    assert tcp.requests.count(0x3C) == 1


def test_no_tcp_api_is_logged_once_and_harmless(egw, gateways, caplog):
    caplog.set_level(logging.INFO)
    http, tcp = gateways
    tcp.close()
    dev = egw.EcowittDevice(http.address, max_tries=1, retry_wait=0, tcp_port=tcp.port)
    data = dev.get_sensors_data()
    dev._tcp_next = 0
    dev.get_sensors_data()
    assert 'wh68.voltage' not in data and data['wh68.battery'] == 5
    assert sum('did not answer' in r.getMessage() for r in caplog.records) == 1


def test_voltage_fields_are_mapped(egw, gateways):
    http, tcp = gateways
    source = egw.GatewaySource('gateway', {'ip_address': http.address, 'api': 'http', 'tcp_port': str(tcp.port),
                                           'max_tries': '1', 'retry_wait': '0',
                                           'firmware_update_check_interval': '0',
                                           'catchup': {'source': 'none'}})
    try:
        packet = source.loop_packet(source.collector.get_current_data())
    finally:
        source.stop()
    assert packet['wh68_volt'] == 1.9 and packet['wh40_volt'] == 1.6


def test_rssi_fields_in_loop_packets(egw, gateways):
    """Each sensor's rssi (dBm) beside its signal: wh40_rssi, wn31_ch1_rssi ..."""
    http, tcp = gateways
    source = egw.GatewaySource('gateway', {'ip_address': http.address, 'api': 'http', 'tcp_port': str(tcp.port),
                                           'max_tries': '1', 'retry_wait': '0',
                                           'firmware_update_check_interval': '0',
                                           'catchup': {'source': 'none'}})
    try:
        packet = source.loop_packet(source.collector.get_current_data())
    finally:
        source.stop()
    assert packet['wh40_rssi'] == -71 and packet['wh68_rssi'] == -53 and packet['ws90_rssi'] == -62
    assert packet['wh40_sig'] == 4
    assert egw.accumulator_extractors()['wh40_rssi'] == 'last'
