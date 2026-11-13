"""The [Accumulator] entries and their tidying when installing over an earlier extension, sensor
battery and signal fields, the pm2_5 fallback, radiation and WeeWX's rule for 'rain'."""

import collections
import io
import logging

import configobj
import pytest
import weeutil.weeutil
import weewx
import weewx.accum

from conftest import load_dump
from test_installer import WEEWX_CONF, env, install, run

# ------------------------------------------------------------------ [Accumulator]


def accum_section(egw):
    return configobj.ConfigObj(io.StringIO(egw.accumulator_config_str()))['Accumulator']


def test_accumulator_entries_follow_the_field_map(egw):
    """An entry for each field that isn't averaged, and none for names the driver never produces
    (ecowitt_http.py's list had lightning_distance for lightningdist, no 24 hour AQIs, wh40_batt
    unmapped)."""
    accum = accum_section(egw)
    fmap = egw.HttpMapper.default_map
    for field, source in fmap.items():
        if source.endswith(('.battery', '.signal', '_24HAQI')):
            assert accum[field]['extractor'] == 'last', field
    for field in ('lightningcount', 'lightningdist', 'lightningdettime', 'daymaxwind', 'windDir10',
                  't_rainyear', 'p_rainday', 'is_raining', 'pm25_ch1_24h', 'ws90_sig'):
        assert accum[field]['extractor'] == 'last', field
    for field in ('t_rain', 'p_rain', 'lightning_strike_count'):
        assert accum[field]['extractor'] == 'sum', field
    for field in ('rainRate', 'p_rainrate', 'outTemp', 'ws90_volt', 'pm25_ch1_real'):
        assert field not in accum, field       # averaged
    renamed = {'wn32_batt', 'wn32_sig', 'wn32p_batt', 'wn32p_sig'}     # wn32_indoor/wn32_outdoor
    assert set(accum) - set(fmap) == set(egw.ACCUMULATOR_SUM) | renamed
    for name in egw.ECOWITT_HTTP_ACCUMULATOR_LAST:
        assert name not in accum


def test_installer_adds_the_drivers_accumulator_entries(egw):
    assert install.ecowitt_dict['Accumulator'].dict() == accum_section(egw).dict()


def archive(egw, monkeypatch, packets):
    """The archive record WeeWX's accumulator makes of these packets, with the driver's entries."""
    monkeypatch.setattr(weewx.accum, 'accum_dict', collections.ChainMap(weewx.accum.accum_dict.maps[-1]))
    weewx.accum.initialize({'Accumulator': accum_section(egw)})
    acc = weewx.accum.Accum(weeutil.weeutil.TimeSpan(0, 300))
    for i, packet in enumerate(packets):
        acc.addRecord(dict(packet, dateTime=60 * (i + 1), usUnits=weewx.METRICWX))
    return acc.getRecord()


def test_lightning_counts_in_the_archive(egw, monkeypatch):
    """lightningcount is the gateway's running count (last value kept), lightning_strike_count the count
    per packet (totalled)."""
    record = archive(egw, monkeypatch, [
        {'lightningcount': 10, 'lightning_strike_count': 0, 'lightningdettime': 1000, 'wh57_batt': 4},
        {'lightningcount': 13, 'lightning_strike_count': 3, 'lightningdettime': 1100, 'wh57_batt': 4},
        {'lightningcount': 14, 'lightning_strike_count': 1, 'lightningdettime': 1200, 'wh57_batt': 3},
        {'lightningcount': 14, 'lightning_strike_count': 0, 'lightningdettime': 1200, 'wh57_batt': 3}])
    assert record['lightningcount'] == 14 and record['lightning_strike_count'] == 4
    assert record['lightningdettime'] == 1200 and record['wh57_batt'] == 3


EARLIER_ACCUMULATOR = """
[Accumulator]
    [[lightningcount]]
        extractor = sum
    [[lightning_strike_count]]
        extractor = sum
    [[pm2_51_24hav]]
        extractor = last
    [[wh31_ch1_batt]]
        extractor = last
    [[soilMoistBatt3s]]
        extractor = last
    [[hourRain]]
        extractor = last
    [[dayRain]]
        extractor = max
    [[model]]
        accumulator = firstlast
        extractor = last
    [[heap_free]]
        extractor = last
    [[wh41_ch1_batt]]
        extractor = last
    [[wh69_batt]]
        extractor = last
    [[rainRate]]
        extractor = max
    [[myOwnField]]
        extractor = last
"""


def test_tidy_accumulator(egw):
    conf = configobj.ConfigObj(io.StringIO("""
[EcowittGateway]
    [[field_map_extensions]]
        hourRain = rain.0x0F.val
""" + EARLIER_ACCUMULATOR))
    removed, changed = egw.tidy_accumulator(conf)
    accum = conf['Accumulator']
    # earlier extensions' entries for fields this driver doesn't produce, as they were added
    assert sorted(removed) == ['heap_free', 'model', 'pm2_51_24hav', 'soilMoistBatt3s', 'wh31_ch1_batt']
    # kept: a field in the user's field map, an entry the user changed, fields the driver produces
    # (whatever their extractor), and entries no version added
    for field in ('hourRain', 'dayRain', 'wh41_ch1_batt', 'wh69_batt', 'rainRate', 'myOwnField',
                  'lightning_strike_count'):
        assert field in accum, field
    assert accum['dayRain']['extractor'] == 'max'
    assert changed == ['lightningcount'] and accum['lightningcount']['extractor'] == 'last'
    assert egw.tidy_accumulator(conf) == ([], [])


def test_reconfigure_tidies_the_accumulator(egw, capsys):
    conf = configobj.ConfigObj(io.StringIO(EARLIER_ACCUMULATOR))
    egw.EcowittHttpDriverConfEditor.do_extractors(conf)
    accum = conf['Accumulator']
    assert 'pm2_51_24hav' not in accum and accum['lightningcount']['extractor'] == 'last'
    assert accum['wh40_batt']['extractor'] == 'last'           # the driver's own entries added
    assert 'Removed 6 unused [Accumulator] entries' in capsys.readouterr().out


def test_install_over_an_earlier_extension_tidies_the_accumulator(monkeypatch, gateway):
    """Installing over an earlier extension as the station driver: WeeWX only adds missing settings, so
    the installer removes its unused entries and corrects lightningcount."""
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver')
    conf, lines = run(WEEWX_CONF.replace('station_type = Simulator', 'station_type = EcowittGateway')
                      + "[EcowittGateway]\n    driver = user.weewx-ecowittGateway\n    rain_source = tipping\n"
                      + EARLIER_ACCUMULATOR)
    accum = conf['Accumulator']
    assert 'wh31_ch1_batt' not in accum and accum['lightningcount']['extractor'] == 'last'
    assert any('unused [Accumulator] entries' in line for line in lines)
    # the earlier rain_source is kept, and WeeWX 'rain' is worked out from that gauge's total
    assert conf['StdWXCalculate']['Delta']['rain']['input'] == 't_rainyear'
    assert 'rain_source' not in conf['EcowittGateway']


def test_install_as_a_service_leaves_the_accumulator(monkeypatch, gateway):
    """As a service, the station's own driver may produce the fields those entries are for."""
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='service')
    conf, _ = run(WEEWX_CONF + EARLIER_ACCUMULATOR)
    assert conf['Accumulator']['wh31_ch1_batt']['extractor'] == 'last'
    assert conf['Accumulator']['lightningcount']['extractor'] == 'sum'

# ------------------------------------------------------------------ sensor battery and signal fields


def test_every_reported_sensor_state_is_mapped(egw, gateway):
    """ecowitt_http.py 0.1.0a28 mapped no battery or signal fields for the WH40, WH45, WH68, WS69, WS80,
    WS85, WN35 or WH51 channels 9-16, nor signal for WH54/WH55 channel 4."""
    dev = egw.EcowittDevice(ip_address=gateway.address, max_tries=1, retry_wait=0)
    sources = set(egw.HttpMapper.default_map.values())
    for key in dev.get_sensors_data(connected_only=False):
        if key.endswith(('.battery', '.signal')):
            assert key in sources, key
    for model in ('wn35', 'wh51', 'wh54', 'wh55'):
        last = 16 if model == 'wh51' else 8 if model == 'wn35' else 4
        for ch in range(1, last + 1):
            assert f'{model}.ch{ch}.battery' in sources and f'{model}.ch{ch}.signal' in sources, (model, ch)


def test_new_sensor_fields_in_loop_packets(egw, make_driver, station):
    """The fixture's WH40 and WH45 are paired."""
    drv = make_driver(**station)
    packet = next(drv.genLoopPackets())
    assert packet['wh40_batt'] == 4 and packet['wh40_sig'] == 4
    assert packet['wh45_batt'] == 6 and packet['wh45_sig'] == 4


def test_ws69_array(egw, gateway):
    """The API calls the WS69/WH65 array 'wh69' (and the rain gauge check used that name), but battery
    decoding and the GW1000 TCP API used 'wh65'."""
    for page in range(1, 6):
        for sensor in gateway.responses[f'get_sensors_info?page={page}']:
            if sensor['img'] == 'wh69':
                sensor.update(id='A1B2', batt='0', rssi='-70', signal='4')
    dev = egw.EcowittDevice(ip_address=gateway.address, max_tries=1, retry_wait=0)
    data = dev.get_sensors_data()
    assert data['wh69.battery'] == 0 and data['wh69.signal'] == 4
    mapped = egw.HttpMapper(driver_debug=egw.DebugOptions()).map_data(data)
    assert mapped['wh69_batt'] == 0 and mapped['wh69_sig'] == 4
    assert egw.EcowittSensors.batt_state_desc(egw.EcowittSensors.__new__(egw.EcowittSensors), 'wh69',
                                              {'battery': 1}) == 'low'
    assert egw.EcowittDevice.sensor_battery_type['wh69'] == 'binary'


def test_gw1000_ws69_is_a_tipping_gauge(egw):
    """Address 0 of the GW1000 TCP API is the WS69 array, whose rain gauge is a tipping gauge."""
    dev = egw.EcowittTcpDevice('192.0.2.99')
    dev.api.call = lambda cmd: {0x3C: bytes([0]) + (0xA1B2).to_bytes(4, 'big') + bytes([0, 4])}[cmd]
    assert dev.get_sensors_data()['wh69.id'] == 'A1B2'
    assert dev.paired_rain_gauges == ('tipping',)

# ------------------------------------------------------------------ pm2_5


@pytest.fixture
def source(egw, station):
    src = egw.GatewaySource('gateway', station)
    yield src
    src.stop()


def test_pm2_5_from_the_wh45_without_a_channel_1_sensor(source):
    """The map puts the WH41/WH43 on channel 1 in pm2_5; without a channel 1 sensor, the WH45/WH46
    fills it."""
    assert source.mapped({'co2.PM25': 8.0})['pm2_5'] == 8.0
    both = source.mapped({'co2.PM25': 8.0, 'ch_pm25.1.PM25': 5.0})
    assert both['pm2_5'] == 5.0 and both['pm2_55'] == 8.0
    assert 'pm2_5' not in source.mapped({'outTemp': 1})


def test_no_pm2_5_fallback_with_a_remapped_pm2_5(egw, station):
    src = egw.GatewaySource('gateway', dict(station, field_map_extensions={'pm2_5': 'ch_pm25.2.PM25'}))
    try:
        assert src.mapped({'co2.PM25': 8.0}).get('pm2_5') is None
    finally:
        src.stop()


def test_pm2_5_fallback_in_catchup_records(source):
    assert source.pm25_from_wh45
    record = {'pm2_55': 7.0}
    source.pm25_fallback(record)
    assert record['pm2_5'] == 7.0

# ------------------------------------------------------------------ WeeWX 'rain'


@pytest.mark.parametrize('stdwxcalculate, warned', [
    ({}, 'totalRain'),                                                            # WeeWX's own default
    ({'Delta': {'rain': {'input': 'totalRain'}}}, 'totalRain'),
    ({'Delta': {'rain': {'input': 't_rainyear'}}}, None),
    ({'Delta': {'rain': {'input': 'p_rainyear'}}}, None),
    ({'Calculations': {'rain': 'hardware'}}, None),
])
def test_rain_rule_warning(egw, caplog, stdwxcalculate, warned):
    """'rain' always comes from WeeWX's [[Delta]] rule, so one naming a total the driver doesn't produce
    (WeeWX's default is totalRain) leaves 'rain' empty."""
    caplog.set_level(logging.WARNING)
    fmap = egw.HttpMapper(driver_debug=egw.DebugOptions()).field_map
    assert egw.check_rain_rule({'StdWXCalculate': stdwxcalculate}, fmap) == warned
    assert any("'rain' will be empty" in m for m in caplog.messages) == bool(warned)


def test_loader_checks_the_rain_rule(egw, station, caplog):
    caplog.set_level(logging.WARNING)
    drv = egw.loader({'EcowittGateway': station}, None)
    try:
        assert any('input is totalRain' in m for m in caplog.messages)
    finally:
        drv.closePort()


def test_fixture_has_the_paired_sensors_these_tests_use():
    sensors = {s['img']: s for n in range(1, 6) for s in load_dump('gw2000_ws90.json')[f'get_sensors_info?page={n}']}
    assert sensors['wh40']['id'] == '4E2B' and sensors['wh45']['id'] == '2A1'

# ------------------------------------------------------------------ radiation


def test_radiation_from_illuminance_is_opt_in(source):
    assert 'radiation' not in source.mapped({'common_list.0x15.val': 12670.0})


def test_radiation_from_illuminance(egw, station):
    """The light sensor is recorded as illuminance (lux), and radiation (W/m2) on request."""
    src = egw.GatewaySource('gateway', dict(station, radiation_from_illuminance='True'))
    try:
        packet = src.mapped({'common_list.0x15.val': 12670.0})
        assert packet['illuminance'] == 12670.0 and packet['radiation'] == pytest.approx(100.0)
        assert 'radiation' not in src.mapped({'outTemp': 1})
        record = {'usUnits': weewx.US, 'illuminance': 25340.0}     # catchup records too
        src.derive_radiation(record)
        assert record['radiation'] == pytest.approx(200.0)
    finally:
        src.stop()


def test_radiation_limits_and_mapping(egw, station):
    src = egw.GatewaySource('gateway', dict(station, radiation_from_illuminance='True',
                                            limits={'radiation': ['0', '50']}))
    try:
        assert src.mapped({'common_list.0x15.val': 12670.0})['radiation'] is None     # over 50 W/m2
    finally:
        src.stop()
    src = egw.GatewaySource('gateway', dict(station, radiation_from_illuminance='True',
                                            field_map_extensions={'radiation': 'common_list.0x16.val'}))
    try:
        assert not src.radiation_from_illuminance
    finally:
        src.stop()


def test_installer_offers_radiation_over_an_earlier_extension(monkeypatch, gateway):
    """Yes by default over the earlier weewx-EcowittGateway extension (its settings have rain_source),
    which recorded radiation."""
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver')
    conf, _ = run(WEEWX_CONF + "[EcowittGateway]\n    rain_source = tipping\n")
    assert conf['EcowittGateway']['radiation_from_illuminance'] == 'True'
    conf, _ = run()
    assert conf['EcowittGateway']['radiation_from_illuminance'] == 'False'
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver', RADIATION='yes')
    conf, _ = run()
    assert conf['EcowittGateway']['radiation_from_illuminance'] == 'True'


@pytest.mark.parametrize('debug', [1, 2])
def test_driver_file_loads_with_debug_logging(debug, monkeypatch, caplog):
    """The [Accumulator] list is built when the file is loaded (eg by 'weectl station reconfigure'):
    with debug on, that logged field maps through log_dict(), not yet defined (NameError)."""
    import importlib.util
    import os
    from conftest import ROOT
    monkeypatch.setattr(weewx, 'debug', debug)
    caplog.set_level(logging.DEBUG)
    spec = importlib.util.spec_from_file_location(
        'egw_debug', os.path.join(ROOT, 'bin', 'user', 'weewx-ecowittGateway.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert '[[lightningcount]]' in module.EcowittHttpDriverConfEditor.accum_config_str
    assert not any('field map' in m for m in caplog.messages)


def test_last_hour_rain_0x7D(egw, caplog):
    """Current firmware reports the last hour's rain as 0x7D (rain and piezoRain), in place of 0x0F; it
    was logged as an unknown observation and t_rainhour/p_rainhour were never filled."""
    caplog.set_level(logging.INFO)
    egw.define_units()
    parser = egw.EcowittHttpParser(unit_system=weewx.METRICWX)
    live = parser.parse_get_livedata_info({
        'rain': [{'id': '0x0D', 'val': '0.0 mm'}, {'id': '0x0E', 'val': '1.2 mm/Hr'},
                 {'id': '0x7D', 'val': '0.6 mm'}, {'id': '0x7C', 'val': '3.0 mm'},
                 {'id': '0x13', 'val': '714.9 mm'}],
        'piezoRain': [{'id': '0x7D', 'val': '0.03 in'}, {'id': '0x7C', 'val': '0.12 in'}]})
    assert not any('0x7D' in m for m in caplog.messages)
    mapped = egw.HttpMapper(driver_debug=egw.DebugOptions()).map_data(live)
    assert mapped['t_rainhour'] == 0.6 and mapped['t_rain24'] == 3.0
    assert mapped['p_rainhour'] == pytest.approx(0.762) and mapped['p_rain24'] == pytest.approx(3.048)
    accum = accum_section(egw)
    assert accum['t_rainhour']['extractor'] == 'last' and accum['p_rainhour']['extractor'] == 'last'
