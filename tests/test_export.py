"""ecwLoop.json and MQTT loop data export (EcowittGatewayExport)."""

import json
import os
import time
import types

import pytest

from conftest import FakeBroker

PACKET = {'dateTime': 1700000000, 'usUnits': 17, 'outTemp': 20.0, 'rain': 2.0, 'windSpeed': float('nan')}


def test_export_packet_native_and_converted(egw):
    native = egw.export_packet(PACKET, 'native')
    assert native['outTemp'] == 20.0 and native['windSpeed'] is None
    us = egw.export_packet(PACKET, 'us')
    assert us['usUnits'] == 1
    assert us['outTemp'] == pytest.approx(68.0) and us['rain'] == pytest.approx(2.0 / 25.4)


def test_export_packet_with_units_labels(egw):
    packet = dict(PACKET, outHumidity=80.0, windDir=142.0, pm2_5=1.8, wh40_batt=4, co2=834.0, windSpeed=3.1)
    labelled = egw.export_packet(packet, 'metricwx', append_units_label=True)
    # as the weewx-mqtt extension names them; no label for fields without a unit
    assert labelled == {'dateTime': 1700000000, 'usUnits': 17, 'outTemp_C': 20.0, 'rain_mm': 2.0,
                        'windSpeed_mps': pytest.approx(3.1), 'outHumidity': 80.0, 'windDir': 142.0,
                        'pm2_5_microgram_per_meter_cubed': 1.8, 'wh40_batt_count': 4, 'co2_ppm': 834.0}
    us = egw.export_packet(packet, 'us', append_units_label=True)
    assert us['outTemp_F'] == pytest.approx(68.0) and 'rain_in' in us and 'windSpeed_mph' in us
    assert 'outTemp' in egw.export_packet(packet, 'native')


def test_check_units(egw):
    assert egw.check_units('MetricWX', 'x') == 'metricwx'
    assert egw.check_units('kelvin', 'x') == 'native'


def test_loop_json_writer(egw, tmp_path):
    # a relative path is relative to HTML_ROOT, a folder gets the default name
    writer = egw.LoopJsonWriter({'enable': 'True', 'path': 'sub/', 'units': 'metric'}, html_dir=str(tmp_path))
    assert writer.path == os.path.join(str(tmp_path), 'sub', 'ecwLoop.json')
    os.mkdir(os.path.join(str(tmp_path), 'sub'))
    writer.write(PACKET)
    with open(writer.path) as f:
        data = json.load(f)
    assert data['usUnits'] == 16 and data['rain'] == pytest.approx(0.2) and data['windSpeed'] is None


def test_disabled_export_does_nothing(egw):
    writer = egw.LoopJsonWriter({}, html_dir='/nonexistent')
    assert not writer.enabled
    writer.write(PACKET)
    assert not egw.MqttPublisher({}).enabled


class FakeEngine:
    def bind(self, event, callback):
        pass


def test_export_service_writes_json_and_publishes(egw, tmp_path):
    pytest.importorskip('paho.mqtt.client')
    broker = FakeBroker()
    try:
        config = {'WEEWX_ROOT': str(tmp_path), 'StdReport': {'HTML_ROOT': str(tmp_path)},
                  'EcowittGateway': {'loop_json': {'enable': 'True', 'units': 'native'},
                                     'mqtt': {'enable': 'True', 'host': '127.0.0.1', 'port': str(broker.port),
                                              'topic': 'weather/test', 'format': 'both', 'units': 'native',
                                              'append_units_label': 'True'}}}
        svc = egw.EcowittGatewayExport(FakeEngine(), config)
        try:
            assert broker.connected.wait(10)
            svc.new_loop_packet(types.SimpleNamespace(packet=dict(PACKET, windSpeed=1.5)))
            deadline = time.time() + 10
            while 'weather/test/outTemp_C' not in dict(broker.published) and time.time() < deadline:
                time.sleep(0.1)
        finally:
            svc.shutDown()
        with open(os.path.join(str(tmp_path), 'ecwLoop.json')) as f:
            assert json.load(f)['outTemp'] == 20.0
        topics = dict(broker.published)
        statuses = [p for t, p in broker.published if t == 'weather/test/status']
        assert statuses[0] == 'online' and statuses[-1] == 'offline'      # offline on shutdown
        assert json.loads(topics['weather/test/loop'])['windSpeed_mps'] == 1.5
        assert topics['weather/test/outTemp_C'] == '20.0'
    finally:
        broker.close()
