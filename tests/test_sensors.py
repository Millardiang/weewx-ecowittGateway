"""The [[sensor_map]] (SensorMapper): multi-channel sensors on fixed channels by sensor ID."""


def sensors(**channels):
    """Sensor ID data, eg sensors(wn31_1='AA') gives {'wn31.ch1.id': 'AA'}."""
    data = {}
    for key, sid in channels.items():
        model, ch = key.split('_')
        data[f'{model}.ch{ch}.id'] = sid
    return data


def test_moves_a_sensor_and_its_data(egw):
    mapper = egw.SensorMapper({'C0FFEE': '3'})
    data = {**sensors(wh51_2='C0FFEE'), 'ch_soil.2.humidity': 33, 'ch_soil.2.channel': 2, 'wh51.ch2.signal': 2}
    moved = mapper.apply(data)
    assert moved['ch_soil.3.humidity'] == 33 and moved['ch_soil.3.channel'] == 3
    assert moved['wh51.ch3.signal'] == 2 and moved['wh51.ch3.id'] == 'C0FFEE'
    assert 'ch_soil.2.humidity' not in moved


def test_swaps_with_a_sensor_in_the_way(egw):
    mapper = egw.SensorMapper({'AA': 'ch1'})
    data = {**sensors(wn31_1='BB', wn31_2='AA'), 'ch_aisle.1.temp': 10.0, 'ch_aisle.2.temp': 20.0}
    moved = mapper.apply(data)
    assert moved['ch_aisle.1.temp'] == 20.0 and moved['ch_aisle.2.temp'] == 10.0
    assert mapper.reported_channel('wn31', 1) == 2


def test_other_models_and_fields_untouched(egw):
    mapper = egw.SensorMapper({'AA': '2'})
    data = {**sensors(wn31_1='AA', wn34_1='CC'), 'ch_temp.1.temp': 5.0, 'common_list.0x02.val': 12.3}
    moved = mapper.apply(data)
    assert moved['ch_temp.1.temp'] == 5.0 and moved['common_list.0x02.val'] == 12.3
    assert moved['wn31.ch2.id'] == 'AA'


def test_reports_problems(egw):
    mapper = egw.SensorMapper({'AA': '9', 'CC': '1', 'not-hex': '1'})
    mapper.plan(sensors(wn31_1='AA'))
    assert any('outside' in p for p in mapper.problems)
    assert any('not paired' in p for p in mapper.problems)
    assert set(mapper.targets) == {'AA', 'CC'}


def test_unregistered_sensors_ignored(egw):
    paired = egw.SensorMapper.paired(sensors(wn31_1='FFFFFFFE', wn31_2='FFFFFFFF', wn31_3='AB'))
    assert paired == {'wn31': {3: 'AB'}}


def test_no_map_returns_data_unchanged(egw):
    data = {**sensors(wn31_1='AA'), 'ch_aisle.1.temp': 1.0}
    assert egw.SensorMapper(None).apply(data) is data


def test_sensor_ids_compare_loosely(egw):
    assert egw.sensor_id('0x00c0ffee') == egw.sensor_id('C0FFEE') == 'C0FFEE'


def test_sensor_map_with_the_fake_gateway(egw, station):
    source = egw.GatewaySource('gateway', {**station, 'sensor_map': {'5E': '4', '0xC0FFEE': '1'}})
    packet = source.loop_packet(source.collector.get_current_data())
    assert packet['extraTemp4'] == 19.8 and 'extraTemp1' not in packet
    assert packet['soilMoist1'] == 33 and 'soilMoist2' not in packet
    assert packet['wn31_ch4_sig'] == 4 and packet['wh51_ch1_sig'] == 2
