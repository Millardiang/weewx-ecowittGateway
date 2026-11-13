"""Parsing gateway responses and mapping them to WeeWX fields."""

import csv
import datetime
import os

import weewx

from conftest import FIXTURES, load_dump


def parser(egw):
    return egw.EcowittHttpParser(unit_system=weewx.METRICWX)


def test_livedata_is_flattened_and_converted(egw):
    data = parser(egw).parse_get_livedata_info(load_dump('gw2000_ws90.json')['get_livedata_info'])
    assert data['common_list.0x02.val'] == 12.3          # outdoor temperature, deg C
    assert data['common_list.0x07.val'] == 81            # humidity, '%' stripped
    assert data['common_list.0x0B.val'] == 3.2           # m/s stays m/s in METRICWX
    assert data['common_list.5.val'] == 2.7              # VPD 0.27 kPa -> hPa
    assert data['common_list.0x6D.val'] == 225           # 10 minute average wind direction
    assert data['rain.0x13.val'] == 450.6
    assert data['rain.0x7C.val'] == 1.2                  # 24 hour rain
    assert data['piezoRain.0x13.val'] == 440.0
    assert data['wh25.intemp'] == 21.4 and data['wh25.rel'] == 1015.3
    assert data['lightning.count'] == 3
    assert data['ch_aisle.1.temp'] == 19.8
    assert data['ch_soil.2.humidity'] == 33


def test_fahrenheit_inches_and_mph_are_converted(egw):
    live = {'common_list': [{'id': '0x02', 'val': '50.0', 'unit': 'F'}, {'id': '0x0B', 'val': '10.0 mph'}],
            'rain': [{'id': '0x13', 'val': '1.00 in'}]}
    data = parser(egw).parse_get_livedata_info(live)
    assert round(data['common_list.0x02.val'], 2) == 10.0
    assert round(data['common_list.0x0B.val'], 3) == 4.470
    assert round(data['rain.0x13.val'], 1) == 25.4


def test_bad_values_become_none(egw):
    live = {'common_list': [{'id': '0x02', 'val': '--', 'unit': 'C'}, {'id': '0x07', 'val': '---'}]}
    data = parser(egw).parse_get_livedata_info(live)
    assert data.get('common_list.0x02.val') is None
    assert data.get('common_list.0x07.val') is None


def test_unknown_ids_are_skipped(egw):
    live = {'common_list': [{'id': '0x99', 'val': '1'}, {'id': '0x02', 'val': '1', 'unit': 'C'}]}
    data = parser(egw).parse_get_livedata_info(live)
    assert data['common_list.0x02.val'] == 1.0
    assert not any('0x99' in key for key in data)


def test_sensor_info(egw):
    dump = load_dump('gw2000_ws90.json')
    pages = [s for n in (1, 2, 3, 4, 5) for s in dump[f'get_sensors_info?page={n}']]
    data = parser(egw).parse_get_sensors_info(pages)       # the API's own model names
    assert data['wh90.id'] == 'D5A2' and data['wh90.signal'] == 4 and data['wh90.version'] == '153'
    assert data['wh31.ch1.id'] == '5E'
    assert data['wh51.ch2.id'] == 'C0FFEE'
    assert 'wh31.ch2.signal' not in data                  # registering: left out by default
    everything = parser(egw).parse_get_sensors_info(pages, connected_only=False)
    assert everything['wh31.ch2.id'] == 'FFFFFFFF'      # still registering


def test_device_sensor_data_uses_current_model_names(egw, gateway):
    device = egw.EcowittDevice(ip_address=gateway.address, max_tries=1, retry_wait=0)
    data = device.get_sensors_data()
    assert data['ws90.id'] == 'D5A2' and data['ws90.signal'] == 4
    assert data['wn31.ch1.id'] == '5E' and data['wh51.ch2.id'] == 'C0FFEE'
    assert device.model == 'GW2000' and device.mac_address == 'AA:BB:CC:DD:EE:FF'
    assert set(device.paired_rain_gauges) == {'tipping', 'piezo'}


def test_default_field_map(egw):
    mapper = egw.HttpMapper(driver_debug=egw.DebugOptions())
    mapped = mapper.map_data({'common_list.0x02.val': 12.3, 'wh25.intemp': 21.4, 'rain.0x13.val': 450.6,
                              'piezoRain.0x13.val': 440.0, 'datetime': 100, 'not.mapped': 1})
    assert mapped['outTemp'] == 12.3 and mapped['inTemp'] == 21.4 and mapped['dateTime'] == 100
    # rain totals keep the gauge in the name; WeeWX 'rain' comes from [StdWXCalculate] [[Delta]]
    assert mapped['t_rainyear'] == 450.6 and mapped['p_rainyear'] == 440.0
    assert 'rain' not in mapped and 'not.mapped' not in mapped


def test_field_map_extensions_move_a_source(egw):
    mapper = egw.HttpMapper(driver_debug=egw.DebugOptions(),
                            field_map_extensions={'extraTemp9': 'ch_aisle.1.temp'})
    mapped = mapper.map_data({'ch_aisle.1.temp': 19.8})
    assert mapped == {'extraTemp9': 19.8}


def test_field_map_replaces_the_default(egw):
    mapper = egw.HttpMapper(driver_debug=egw.DebugOptions(), field_map={'outTemp': 'common_list.0x02.val'})
    assert set(mapper.field_map) == {'outTemp', 'dateTime'}       # dateTime is always mapped
    assert mapper.map_data({'common_list.0x02.val': 1.0, 'wh25.intemp': 2.0}) == {'outTemp': 1.0}


def test_wn32_options_rename_battery_fields(egw):
    default = egw.HttpMapper(driver_debug=egw.DebugOptions())
    indoor = egw.HttpMapper(driver_debug=egw.DebugOptions(), wn32_indoor=True)
    assert 'wh25_batt' in default.field_map and 'wh25_batt' not in indoor.field_map
    assert 'wn32_batt' in indoor.field_map


def sd_catchup(egw):
    catchup = egw.EcowittDeviceCatchup.__new__(egw.EcowittDeviceCatchup)
    catchup.unit_system, catchup.catchup_grace = weewx.METRICWX, 0
    catchup.driver_debug = egw.DebugOptions()
    catchup.mapper = egw.SdMapper(driver_debug=catchup.driver_debug)
    return catchup


def read_sd_file(catchup, start_ts=None):
    with open(os.path.join(FIXTURES, 'sd_202611A.csv'), encoding='utf-8') as f:
        lines = catchup.clean_data(f.readlines())
    return catchup.process_raw_csv_data(csv.DictReader(lines), start_ts, 5)


def test_sd_card_csv(egw):
    rows = read_sd_file(sd_catchup(egw))
    assert len(rows) == 3
    first, last = rows[0], rows[-1]
    assert first['interval'] == 5
    assert first['common_list.0x02.val'] == 10.5
    assert first['rain.0x13.val'] == 449.4
    assert first['piezoRain.0x13.val'] == 438.8
    assert first['ch_aisle.1.temp'] == 19.5
    assert last.get('common_list.0x0B.val') is None       # '--' in the file
    assert last['datetime'] - first['datetime'] == 600


def test_sd_card_csv_after_a_time(egw):
    catchup = sd_catchup(egw)
    first = read_sd_file(catchup)[0]['datetime']
    assert len(read_sd_file(catchup, start_ts=first)) == 2


def test_sd_card_file_list(egw):
    info = load_dump('gw2000_ws90.json')['get_sdmmc_info']
    start = datetime.datetime(2026, 11, 2).timestamp()
    assert egw.EcowittDeviceCatchup.get_file_list(info, start) == {202611: ['202611A.csv']}
    assert set(egw.EcowittDeviceCatchup.get_file_list(info)) == {202610, 202611}
