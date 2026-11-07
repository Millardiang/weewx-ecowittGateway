"""Rain/lightning counters and the hardware ID sensor map."""


def test_delta_tracker(egw):
    tracker = egw.DeltaTracker('rain', ('rain.0x13.val', 'rain.0x12.val'))
    assert tracker.update({'rain.0x13.val': 10.0}) is None        # first value: no delta
    assert round(tracker.update({'rain.0x13.val': 10.6}), 6) == 0.6
    assert tracker.update({'rain.0x13.val': 10.6}) == 0.0
    assert tracker.update({'rain.0x13.val': 0.4}) == 0.4          # counter reset: the new total is the delta
    assert tracker.field == 'rain.0x13.val'


def test_delta_tracker_falls_back_to_the_next_counter(egw):
    tracker = egw.DeltaTracker('rain', ('rain.0x13.val', 'rain.0x12.val'))
    tracker.update({'rain.0x12.val': 5.0})
    assert tracker.field == 'rain.0x12.val'
    assert tracker.update({'rain.0x12.val': 5.5}) == 0.5


def test_delta_tracker_gives_no_delta_when_the_counter_is_missing(egw):
    tracker = egw.DeltaTracker('rain', ('rain.0x13.val',))
    tracker.update({'rain.0x13.val': 1.0})
    assert tracker.update({'rain.0x13.val': 2.0}) == 1.0
    assert tracker.update({}) is None                         # 1.2.0 and earlier repeated 1.0 here
    assert tracker.update({'rain.0x13.val': 2.5}) == 0.5


def sensors(**channels):
    data = {}
    for key, sid in channels.items():
        model, ch = key.split('_')
        data[f'{model}.ch{ch}.id'] = sid
    return data


def test_sensor_map_moves_a_sensor_and_its_data(egw):
    mapper = egw.SensorMapper({'C0FFEE': '3'})
    data = {**sensors(wh51_2='C0FFEE'), 'ch_soil.2.humidity': 33, 'ch_soil2nowAd': 231, 'wh51.ch2.signal': 2}
    moved = mapper.apply(data)
    assert moved['ch_soil.3.humidity'] == 33
    assert moved['ch_soil3nowAd'] == 231
    assert moved['wh51.ch3.signal'] == 2
    assert 'ch_soil.2.humidity' not in moved


def test_sensor_map_swaps_with_a_sensor_in_the_way(egw):
    mapper = egw.SensorMapper({'AA': '1'})
    data = {**sensors(wn31_1='BB', wn31_2='AA'), 'ch_aisle.1.temp': 10.0, 'ch_aisle.2.temp': 20.0}
    moved = mapper.apply(data)
    assert moved['ch_aisle.1.temp'] == 20.0 and moved['ch_aisle.2.temp'] == 10.0


def test_sensor_map_reports_problems(egw):
    mapper = egw.SensorMapper({'AA': '9', 'CC': '1', 'not-hex': '1'})
    mapper.plan(sensors(wn31_1='AA'))
    assert any('outside' in p for p in mapper.problems)
    assert any('not paired' in p for p in mapper.problems)
    assert set(mapper.targets) == {'AA', 'CC'}


def test_sensor_ids_compare_loosely(egw):
    assert egw.sensor_id('0x00c0ffee') == egw.sensor_id('C0FFEE') == 'C0FFEE'


def test_wind_voltage_goes_to_the_ws68_without_a_ws80(egw):
    data = {'wh68.id': '12', 'ws80.id': 'FFFFFFFE', 'common_list.0x0A.voltage': 2.9}
    egw.route_wind_voltage(data, egw.wind_array(data))
    assert data == {'wh68.id': '12', 'ws80.id': 'FFFFFFFE', 'wh68.voltage': 2.9}
    data = {'wh68.id': '12', 'ws80.id': '34', 'common_list.0x0A.voltage': 2.9}
    egw.route_wind_voltage(data, egw.wind_array(data))
    assert data['common_list.0x0A.voltage'] == 2.9
