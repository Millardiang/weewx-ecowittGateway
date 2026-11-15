"""The station's firmware (stationtype), model, access point name (apName), rain gains (gain0-gain5)
and free memory (heap_free) in each loop packet, and WeeWX radiation as well as illuminance."""

import weeutil.weeutil
import weewx
import weewx.accum
import weewx.units


def test_get_station_info(egw, gateway):
    dev = egw.EcowittDevice(gateway.address, max_tries=1, retry_wait=0, tcp_port=1)
    assert dev.get_station_info() == {
        'station.stationtype': 'GW2000A_V3.1.4', 'station.model': 'GW2000A',
        'station.apName': 'GW2000A-WIFI1234', 'rain.gain0': 1.0,
        'piezoRain.gain1': 1.0, 'piezoRain.gain2': 1.0, 'piezoRain.gain3': 1.0,
        'piezoRain.gain4': 1.4, 'piezoRain.gain5': 1.4}


def test_station_info_without_piezo_or_device_info(egw, gateway):
    del gateway.responses['get_piezo_rain']
    del gateway.responses['get_device_info']
    info = egw.EcowittDevice(gateway.address, max_tries=1, retry_wait=0, tcp_port=1).get_station_info()
    assert info['station.stationtype'] == 'GW2000A_V3.1.4' and info['rain.gain0'] == 1.0
    assert 'station.apName' not in info and 'piezoRain.gain1' not in info


def test_loop_packet_fields(egw, station, gateway):
    # as Ian's GW2000 sends it
    gateway.responses['get_livedata_info']['debug'] = [{'heap': '61268', 'runtime': '4338823',
                                                        'usr_interval': '60', 'is_cnip': False}]
    source = egw.GatewaySource('gateway', station)
    try:
        packet = source.loop_packet(source.collector.get_current_data())
        # read again only every 10 minutes
        source.collector.get_current_data()
    finally:
        source.stop()
    assert packet['stationtype'] == 'GW2000A_V3.1.4' and packet['model'] == 'GW2000A'
    assert packet['apName'] == 'GW2000A-WIFI1234'
    assert packet['gain0'] == 1.0 and packet['gain4'] == 1.4
    assert packet['heap_free'] == 61268
    assert packet['runtime'] == 4338823 and egw.accumulator_extractors()['runtime'] == 'last'
    # radiation (W/m2) as well as illuminance (lux), by default
    groups = weewx.units.std_groups[packet['usUnits']]
    light = weewx.units.ValueTuple(packet['illuminance'], groups['group_illuminance'], 'group_illuminance')
    assert packet['radiation'] == weewx.units.convert(light, groups['group_radiation']).value


def test_station_info_is_read_every_ten_minutes(egw, station, gateway):
    source = egw.GatewaySource('gateway', station)
    try:
        source.collector.get_current_data()
        source.collector.get_current_data()
        assert gateway.requests.count('get_version') == 1
    finally:
        source.stop()


def test_gw1000_has_no_station_info(egw, gw1000):
    station = {'ip_address': '127.0.0.1', 'api': 'tcp', 'tcp_port': str(gw1000.port), 'max_tries': '1'}
    source = egw.GatewaySource('gateway', station)
    try:
        assert source.collector.station_info() == {}
    finally:
        source.stop()


def test_text_fields_are_archived_with_their_last_value(egw, monkeypatch):
    """WeeWX can't average text: stationtype, model and apName keep their last value, from the
    driver's [Accumulator] entries or, without them, the defaults the driver registers."""
    monkeypatch.setattr(weewx.accum, 'accum_dict',
                        weeutil.weeutil.ListOfDicts(dict(weewx.accum.defaults_dict['Accumulator'])))
    monkeypatch.setattr(egw.register_text_accumulators, 'done', False, raising=False)
    egw.register_text_accumulators()
    acc = weewx.accum.Accum(weeutil.weeutil.TimeSpan(0, 300))
    for i, fw in enumerate(('GW3000B_V1.2.4', 'GW3000B_V1.2.5')):
        acc.addRecord({'dateTime': 60 * (i + 1), 'usUnits': weewx.METRICWX, 'stationtype': fw,
                       'model': 'GW3000B', 'apName': 'GW3000B-WIFI', 'outTemp': 10.0 + i})
    record = acc.getRecord()
    assert record['stationtype'] == 'GW3000B_V1.2.5' and record['model'] == 'GW3000B'
    assert record['outTemp'] == 10.5
    conf = egw.accumulator_config_str()
    assert '[[stationtype]]\n        accumulator = firstlast\n        extractor = last' in conf
