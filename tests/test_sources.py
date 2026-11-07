"""The per-gateway layer (GatewaySource) and merging, the base for a multi-gateway mode."""

import time
import types

import weewx

from test_driver_service import FakeEngine


def test_one_source_from_the_flat_config(egw, station):
    assert egw.source_configs(station) == [('gateway', station)]


def test_add_deltas(egw):
    total = {}
    egw.add_deltas(total, {'t_rain': None, 'p_rain': 0.2})
    egw.add_deltas(total, {'t_rain': 0.5, 'p_rain': 0.3, 'outTemp': 1})
    assert total == {'t_rain': 0.5, 'p_rain': 0.5}


def test_status_follows_the_polls(egw, station, gateway):
    source = egw.GatewaySource('gateway', station)
    try:
        assert source.status.online is None
        source.start()
        item = source.get(10)
        assert hasattr(item, 'keys') and source.status.online is True and source.status.last_seen
        gateway.down = True
        while hasattr(item, 'keys'):
            item = source.get(10)
        assert source.status.online is False and '404' in source.status.last_error
    finally:
        source.stop()


def test_loop_packet_is_in_the_sources_units(egw, station):
    source = egw.GatewaySource('gateway', station, unit_system=weewx.US)
    data = source.collector.get_current_data()
    packet, mapped = source.loop_packet(data)
    assert packet['usUnits'] == weewx.US and round(packet['outTemp'], 2) == 54.14
    assert 'outTemp' in mapped and 't_rain' not in mapped


def stopped_service(egw, station):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittGateway': station})
    svc.source.stop()
    while not svc.source.collector.queue.empty():
        svc.source.collector.queue.get()
    return svc


def queue_poll(svc, gateway, rain_total):
    gateway.live('rain', '0x13')['val'] = f'{rain_total} mm'
    data = svc.source.collector.get_current_data()
    data['datetime'] = int(time.time())
    svc.source.collector.queue.put(data)


def test_service_counts_rain_from_every_poll_between_packets(egw, station, gateway):
    svc = stopped_service(egw, station)
    try:
        queue_poll(svc, gateway, 100.0)
        svc.new_loop_packet(types.SimpleNamespace(packet={'dateTime': int(time.time()), 'usUnits': weewx.METRICWX}))
        for total in (100.5, 101.0, 101.2):            # three polls before the next loop packet
            queue_poll(svc, gateway, total)
        packet = {'dateTime': int(time.time()), 'usUnits': weewx.METRICWX}
        svc.new_loop_packet(types.SimpleNamespace(packet=packet))
        assert round(packet['t_rain'], 3) == 1.2       # 1.2.0 and earlier: 0.2 (the last poll only)
    finally:
        svc.shutDown()


def test_service_rain_is_converted_to_the_packets_units(egw, station, gateway):
    svc = stopped_service(egw, station)
    try:
        queue_poll(svc, gateway, 100.0)
        queue_poll(svc, gateway, 125.4)
        packet = {'dateTime': int(time.time()), 'usUnits': weewx.US}
        svc.new_loop_packet(types.SimpleNamespace(packet=packet))
        assert round(packet['t_rain'], 3) == 1.0       # 25.4 mm; 1.2.0 and earlier put 25.4 in an inch field
        assert 'rain' not in packet and 'hail' not in packet
    finally:
        svc.shutDown()
