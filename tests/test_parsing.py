"""Parsing gateway responses and mapping them to WeeWX fields."""

import weewx

from conftest import load_dump


def parser(egw, **kwargs):
    return egw.EcowittHttpParser(unit_system=weewx.METRICWX, **kwargs)


def test_livedata_is_flattened_and_converted(egw):
    data = parser(egw).parse_get_livedata_info(load_dump('gw2000_ws90.json')['get_livedata_info'])
    assert data['common_list.0x02.val'] == 12.3          # outdoor temperature, deg C
    assert data['common_list.0x07.val'] == 81            # humidity, '%' stripped
    assert data['common_list.0x0B.val'] == 3.2           # m/s stays m/s in METRICWX
    assert data['common_list.5.val'] == 2.7              # VPD 0.27 kPa -> hPa
    assert data['rain.0x13.val'] == 450.6
    assert data['rain.0x13.battery'] == 4
    assert data['piezoRain.0x13.voltage'] == 3.22
    assert data['piezoRain.0x13.ws90_ver'] == 153
    assert data['wh25.rel'] == 1015.3
    assert data['lightning.count'] == 3
    assert data['ch_aisle.1.temp'] == 19.8
    assert data['ch_soil.2.humidity'] == 33


def test_fahrenheit_and_inches_are_converted(egw):
    live = {'common_list': [{'id': '0x02', 'val': '50.0', 'unit': 'F'}, {'id': '0x0B', 'val': '10.0 mph'}],
            'rain': [{'id': '0x13', 'val': '1.00 in'}]}
    data = parser(egw).parse_get_livedata_info(live)
    assert round(data['common_list.0x02.val'], 2) == 10.0
    assert round(data['common_list.0x0B.val'], 3) == 4.470
    assert round(data['rain.0x13.val'], 1) == 25.4


def test_bad_values_become_none(egw):
    live = {'common_list': [{'id': '0x02', 'val': '--', 'unit': 'C'}, {'id': '0x07', 'val': '---'}]}
    data = parser(egw).parse_get_livedata_info(live)
    assert data['common_list.0x02.val'] is None
    assert data['common_list.0x07.val'] is None


def test_unknown_groups_and_ids_are_skipped(egw):
    live = {'mystery': [{'x': 1}], 'common_list': [{'id': '0x99', 'val': '1'}, {'id': '0x02', 'val': '1', 'unit': 'C'}]}
    data = parser(egw).parse_get_livedata_info(live)
    assert data == {'common_list.0x02.val': 1.0, 'common_list.0x02.id': '0x02'}


def test_sensor_info(egw):
    dump = load_dump('gw2000_ws90.json')
    pages = [s for n in (1, 2) for s in dump[f'get_sensors_info?page={n}']]
    pages = [{k: (v.replace('wh90', 'ws90').replace('wh31', 'wn31') if isinstance(v, str) else v)
              for k, v in s.items()} for s in pages]
    data = parser(egw).parse_get_sensors_info(pages)
    assert data['ws90.id'] == 'D5A2' and data['ws90.signal'] == 4 and data['ws90.version'] == '153'
    assert data['wn31.ch1.id'] == '5E'
    assert data['wh51.ch2.id'] == 'C0FFEE'
    assert data['wn31.ch2.signal'] is None                # unregistered
    assert data['wn31.ch2.battery'] is None               # no signal, battery hidden by default


def test_default_field_map(egw):
    mapper = egw.HttpMapper()
    mapped = mapper.map_data({'common_list.0x02.val': 12.3, 'wh25.intemp': 21.4, 'rain.0x13.val': 450.6,
                              'datetime': 100, 'not.mapped': 1})
    assert mapped['outTemp'] == 12.3 and mapped['inTemp'] == 21.4 and mapped['dateTime'] == 100
    assert mapped['yearRain'] == 450.6
    assert 'not.mapped' not in mapped


def test_field_map_extensions_move_a_source(egw):
    mapper = egw.HttpMapper(field_map_extensions={'extraTemp9': 'ch_aisle.1.temp'})
    mapped = mapper.map_data({'ch_aisle.1.temp': 19.8})
    assert mapped == {'extraTemp9': 19.8}


def test_field_map_replaces_the_default(egw):
    mapper = egw.HttpMapper(field_map={'outTemp': 'common_list.0x02.val'})
    assert set(mapper.field_map) == {'outTemp', 'dateTime'}


def test_wn32_options_rename_battery_fields(egw):
    mapper = egw.HttpMapper(wn32_indoor=True)
    assert 'wn32_batt' in mapper.field_map and 'wh25_batt' not in mapper.field_map


def test_sd_card_csv(egw):
    catchup = egw.EcowittDeviceCatchup.__new__(egw.EcowittDeviceCatchup)
    catchup.unit_system, catchup.catchup_grace = weewx.METRICWX, 0
    catchup.driver_debug = egw.DebugOptions()
    catchup.mapper = egw.SdMapper()
    import csv
    import os
    from conftest import FIXTURES
    with open(os.path.join(FIXTURES, 'sd_202611A.csv'), encoding='utf-8') as f:
        rows = catchup.process_raw_csv_data(csv.DictReader(catchup.clean_data(f.readlines())), None, 5)
    assert len(rows) == 3
    first, last = rows[0], rows[-1]
    assert first['interval'] == 5
    assert first['common_list.0x02.val'] == 10.5
    assert first['rain.0x13.val'] == 449.4
    assert first['piezoRain.0x13.val'] == 438.8
    assert first['ch_aisle.1.temp'] == 19.5
    assert 'common_list.0x0B.val' not in last              # '--' is left out
    assert last['datetime'] - first['datetime'] == 600


def test_sd_card_file_list(egw):
    info = load_dump('gw2000_ws90.json')['get_sdmmc_info']
    import datetime
    start = datetime.datetime(2026, 11, 2).timestamp()
    assert egw.EcowittDeviceCatchup.get_file_list(info, start) == {202611: ['202611A.csv']}
    assert set(egw.EcowittDeviceCatchup.get_file_list(info)) == {202610, 202611}
