"""The GW1000 binary TCP API (EcowittTcpDevice) and make_device()."""

import unittest.mock

import pytest

from conftest import FakeGw1000


def be(value, size):
    return int(value).to_bytes(size, 'big', signed=value < 0)


RESPONSES = {
    0x50: bytes([14]) + b'GW1000B_V1.7.7',
    0x27: b''.join(bytes([code]) + value for code, value in (
        (0x01, be(214, 2)), (0x06, be(45, 1)), (0x08, be(10012, 2)), (0x02, be(-15, 2)),
        (0x07, be(81, 1)), (0x0A, be(231, 2)), (0x0B, be(32, 2)), (0x15, be(272912, 4)),
        (0x0D, be(12, 2)), (0x13, be(4506, 4)), (0x86, be(4400, 4)), (0x1A, be(198, 2)),
        (0x22, be(52, 1)), (0x60, be(12, 1)), (0x61, be(1793887921, 4)), (0x62, be(3, 4)))),
    0x3C: (bytes([48]) + be(0xD5A2, 4) + bytes([161, 4]) + bytes([6]) + be(0x5E, 4) + bytes([0, 4])
           + bytes([3]) + be(0xFFFFFFFE, 4) + bytes([0, 0])),
}


@pytest.fixture
def device(egw):
    dev = egw.EcowittTcpDevice('192.0.2.99')
    dev.api.call = lambda cmd: RESPONSES[cmd]
    return dev


def test_packet_checksum(egw):
    assert egw.EcowittTcpApi.packet(0x27) == bytes.fromhex('FFFF27032A')


def test_live_data(device):
    live = device.get_live_data()
    expected = {'wh25.intemp': 21.4, 'wh25.inhumi': 45, 'wh25.abs': 1001.2, 'common_list.0x02.val': -1.5,
                'common_list.0x07.val': 81, 'common_list.0x0A.val': 231, 'common_list.0x0B.val': 3.2,
                'common_list.0x15.val': 27291.2, 'rain.0x0D.val': 1.2, 'rain.0x13.val': 450.6,
                'piezoRain.0x13.val': 440.0, 'ch_aisle.1.temp': 19.8, 'ch_aisle.1.humidity': 52,
                'ch_aisle.1.channel': 1, 'lightning.distance': 12, 'lightning.timestamp': 1793887921,
                'lightning.count': 3}
    for key, value in expected.items():
        assert live[key] == pytest.approx(value), key


def test_sensors(device):
    sensors = device.get_sensors_data()
    assert sensors['ws90.id'] == 'D5A2' and sensors['ws90.voltage'] == 3.22
    assert sensors['piezoRain.0x13.voltage'] == 3.22
    assert sensors['wn31.ch1.id'] == '5E'
    assert 'wh40.id' not in sensors
    assert device.paired_rain_gauges == ('piezo',)


def test_model_and_firmware(device):
    assert device.model == 'GW1000' and device.firmware_version == 'V1.7.7'


def test_unsupported_feature(egw, device):
    with pytest.raises(egw.UnsupportedFeatureError, match="does not provide 'sdmmc_info_data'"):
        device.get_sdmmc_info_data()
    assert issubclass(egw.UnsupportedFeatureError, egw.DeviceIOError)


def test_make_device(egw):
    assert isinstance(egw.make_device('192.0.2.99', api='tcp'), egw.EcowittTcpDevice)
    assert isinstance(egw.make_device('192.0.2.99', api='http'), egw.EcowittDevice)
    # auto: a GW1000 answering the TCP API uses the TCP API
    with unittest.mock.patch.object(egw.EcowittTcpDevice, 'firmware',
                                    new_callable=unittest.mock.PropertyMock) as firmware:
        firmware.return_value = 'GW1000B_V1.7.7'
        assert isinstance(egw.make_device('192.0.2.99'), egw.EcowittTcpDevice)
        # anything else uses the HTTP API
        firmware.return_value = 'GW2000A_V3.1.4'
        with unittest.mock.patch.object(egw.EcowittDevice, 'model',
                                        new_callable=unittest.mock.PropertyMock) as model:
            model.return_value = 'GW2000'
            assert isinstance(egw.make_device('192.0.2.99'), egw.EcowittDevice)


def test_over_a_socket(egw, gw1000):
    device = egw.EcowittTcpDevice('127.0.0.1', tcp_port=gw1000.port, max_tries=1, retry_wait=0)
    assert device.model == 'GW1000' and device.mac_address == 'AA:BB:CC:DD:EE:FF'
    live = device.get_live_data()
    assert live['common_list.0x02.val'] == 12.3 and live['rain.0x13.val'] == 450.6
    assert device.get_sensors_data()['wh51.ch2.id'] == 'C0FFEE'


def test_no_answer_is_a_device_io_error(egw):
    gw = FakeGw1000()
    gw.down = True
    try:
        device = egw.EcowittTcpDevice('127.0.0.1', tcp_port=gw.port, max_tries=1, retry_wait=0)
        with pytest.raises(egw.DeviceIOError):
            device.get_live_data()
    finally:
        gw.close()
