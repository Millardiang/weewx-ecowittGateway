"""Log output: long lines, the field map, the active field map and 'Next update' messages."""

import logging
from unittest.mock import patch

import pytest
import weewx


def test_log_dict_short_is_one_line(egw, info_log):
    egw.log_dict('map is ', {'b': 2, 'a': 1})
    assert info_log.messages == ["map is {'a': '1', 'b': '2'}"]


def test_log_dict_long_is_split_under_the_limit(egw, info_log):
    data = {f'field{n}': 'x' * 40 for n in range(400)}
    egw.log_dict('map is ', data)
    messages = info_log.messages
    assert len(messages) > 1
    assert all(len(m) < egw.LOG_LINE_MAX + 50 for m in messages)
    assert messages[0].startswith(f'map is (part 1 of {len(messages)}) {{')
    # the parts put back together are the whole dict, split between entries
    assert ' '.join(m.split(') ', 1)[1] for m in messages) == egw.natural_sort_dict(data)
    assert all(m.endswith(',') for m in messages[:-1])


def test_log_long_without_commas_and_at_debug_level(egw, caplog):
    caplog.set_level(logging.DEBUG)
    egw.log_long('resp ', 'x' * 7000, logging.DEBUG)
    assert [r.levelno for r in caplog.records] == [logging.DEBUG] * 3
    assert ''.join(r.getMessage().split(') ', 1)[1] for r in caplog.records) == 'x' * 7000


def test_field_map_size_logged_at_debug_1(egw, info_log):
    weewx.debug = 1
    mapper = egw.HttpMapper(driver_debug=egw.DebugOptions())
    assert f'field map has {len(mapper.field_map)} entries' in info_log.text
    assert 'field map is' not in info_log.text


def test_full_field_map_logged_at_debug_2(egw, info_log):
    weewx.debug = 2
    egw.HttpMapper(driver_debug=egw.DebugOptions())
    parts = [m for m in info_log.messages if 'field map is (part' in m]
    assert len(parts) > 1 and all(len(m) < egw.LOG_LINE_MAX + 50 for m in parts)


@pytest.fixture
def source(egw, station):
    src = egw.GatewaySource('gateway', station, label='EcowittHttpDriver')
    yield src
    src.stop()


def test_active_map_logged_once_then_only_additions(source, info_log):
    weewx.debug = 1
    info_log.clear()
    data = {'common_list.0x02.val': 1, 'datetime': 1}
    source.log_active_map(dict(data))
    source.log_active_map(dict(data))
    source.log_active_map(dict(data, **{'wh25.intemp': 2}))
    assert len(info_log.messages) == 2
    assert 'EcowittHttpDriver: active field map (2 of' in info_log.messages[0]
    assert "1 more field(s) now reported: {'inTemp': 'wh25.intemp'}" in info_log.messages[1]


def test_active_map_leaves_out_sensors_that_are_not_paired(source, info_log):
    weewx.debug = 1
    info_log.clear()
    # a gateway sends empty signal/battery fields for sensors that aren't paired
    source.log_active_map({'common_list.0x02.val': 1, 'wh25.signal': None, 'wn31.ch2.signal': None})
    assert 'active field map (1 of' in info_log.messages[0]
    assert 'wh25' not in info_log.text and 'wn31' not in info_log.text


def test_active_map_silent_without_debug(source, info_log):
    info_log.clear()
    source.log_active_map({'common_list.0x02.val': 1})
    assert info_log.messages == []


def test_active_map_logged_by_the_driver(egw, station, info_log):
    weewx.debug = 1
    drv = egw.EcowittHttpDriver(**station)
    try:
        next(drv.genLoopPackets())
        assert any('EcowittHttpDriver: active field map (' in m for m in info_log.messages)
    finally:
        drv.closePort()


def test_active_field_map(egw):
    field_map = {'outTemp': 'common_list.0x02.val', 'inTemp': 'wh25.intemp', 'wh25_sig': 'wh25.signal'}
    assert egw.active_field_map(field_map, {'common_list.0x02.val': 0, 'wh25.signal': None}) == \
        {'outTemp': 'common_list.0x02.val'}


def run_one_poll(egw, caplog, **settings):
    """Run the collector's poll loop once; returns the log messages."""
    collector = egw.EcowittHttpCollector.__new__(egw.EcowittHttpCollector)
    egw.Collector.__init__(collector)
    collector.poll_interval = 20
    collector.fw_update_check_interval = 0
    collector.log_failures = True
    collector.debug = egw.DebugOptions(**settings)
    collector.log_next_update = settings.get('log_next_update', egw.DEFAULT_LOG_NEXT_UPDATE)
    collector.collect_data = True

    def poll():
        collector.collect_data = False
        return {'datetime': 1, 'common_list.0x02.val': 20.0}
    collector.get_current_data = poll
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    with patch('time.sleep'):
        collector.collect()
    assert collector.queue.get_nowait()['datetime'] == 1
    return caplog.messages


def test_next_update_logged_with_debug(egw, caplog):
    weewx.debug = 1
    assert 'Next update in 20 seconds' in run_one_poll(egw, caplog)


def test_next_update_can_be_turned_off(egw, caplog):
    weewx.debug = 1
    assert 'Next update in 20 seconds' not in run_one_poll(egw, caplog, log_next_update=False)
    # still logged when debugging the collector itself
    messages = run_one_poll(egw, caplog, log_next_update=False, debug='collector')
    assert 'Next update in 20 seconds' in messages
    # the polling message used to fail ('not enough arguments for format string')
    assert any(m.startswith("Polling the device, time '") for m in messages)


def test_next_update_not_logged_without_debug(egw, caplog):
    assert 'Next update in 20 seconds' not in run_one_poll(egw, caplog)


def test_next_update_setting_reaches_the_collector(egw, station):
    for setting, expected in (('True', True), ('False', False)):
        source = egw.GatewaySource('gateway', {**station, 'log_next_update': setting})
        assert source.collector.log_next_update is expected


def test_debug_options_from_a_quoted_string(egw):
    options = egw.DebugOptions(debug='sensors, collector')
    assert options.sensors and options.collector and not options.rain


def test_loop_debug_logs_received_mapped_and_packet(egw, station, info_log):
    drv = egw.EcowittHttpDriver(**{**station, 'debug': 'loop'})
    try:
        next(drv.genLoopPackets())
    finally:
        drv.closePort()
    text = info_log.text
    assert 'EcowittHttpDriver: Received GW2000 data: ' in text
    assert 'EcowittHttpDriver: Mapped GW2000 data: ' in text
    assert 'EcowittHttpDriver: Packet ' in text


def test_rain_debug_logs_rain_fields_only(egw, station, info_log):
    drv = egw.EcowittHttpDriver(**{**station, 'debug': 'rain'})
    try:
        next(drv.genLoopPackets())
    finally:
        drv.closePort()
    received = [m for m in info_log.messages if m.startswith('EcowittHttpDriver: Received GW2000 data: ')]
    assert received and 't_rainyear' in info_log.text and 'outTemp' not in received[0]
