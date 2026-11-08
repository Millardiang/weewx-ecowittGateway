"""Log output: long lines, the active field map and 'Next update' messages."""

import types

import weewx


def test_log_dict_short_is_one_line(egw, info_log):
    egw.log_dict('map is ', {'b': 2, 'a': 1})
    assert info_log.messages == ["map is {'a': '1', 'b': '2'}"]


def test_log_dict_long_is_split_under_the_limit(egw, info_log):
    data = {f'field{n}': 'x' * 40 for n in range(400)}
    egw.log_dict('map is ', data)
    assert len(info_log.messages) > 1
    assert all(len(m) < egw.LOG_LINE_MAX + 100 for m in info_log.messages)
    assert info_log.messages[0].startswith('map is (part 1 of ')
    text = ''.join(m.split(') ', 1)[1] for m in info_log.messages)
    assert text.count("'field") == 400 and text.startswith('{') and text.endswith('}')


def test_field_map_logged_as_a_summary_at_debug_1(egw, info_log):
    weewx.debug = 1
    mapper = egw.HttpMapper()
    assert any(f'field map has {len(mapper.field_map)} entries' in m for m in info_log.messages)
    assert not any('field map is' in m for m in info_log.messages)


def test_full_field_map_logged_at_debug_2(egw, info_log):
    weewx.debug = 2
    egw.HttpMapper()
    assert sum('field map is (part' in m for m in info_log.messages) > 1


def common(egw):
    return types.SimpleNamespace(debug=egw.DebugOptions(), mapper=egw.HttpMapper(), active_fields=set(), label='')


def test_active_map_logged_once_then_only_additions(egw, info_log):
    weewx.debug = 1
    c = common(egw)
    info_log.clear()
    egw.GatewaySource.log_active_map(c, {'common_list.0x02.val': 1, 'datetime': 1})
    egw.GatewaySource.log_active_map(c, {'common_list.0x02.val': 1, 'datetime': 1})
    egw.GatewaySource.log_active_map(c, {'common_list.0x02.val': 1, 'datetime': 1, 'wh25.intemp': 2})
    assert len(info_log.messages) == 2
    assert 'active field map (2 of' in info_log.messages[0]
    assert "1 more field(s) now reported: {'inTemp': 'wh25.intemp'}" in info_log.messages[1]


def test_active_map_silent_without_debug(egw, info_log):
    c = common(egw)
    info_log.clear()
    egw.GatewaySource.log_active_map(c, {'common_list.0x02.val': 1})
    assert info_log.messages == []


def test_next_update_message_can_be_turned_off(egw, station, info_log, monkeypatch):
    weewx.debug = 1
    for setting, expected in (('True', True), ('False', False)):
        drv = egw.EcowittHttpDriver(**{**station, 'log_next_update': setting})
        try:
            info_log.clear()
            next(drv.genLoopPackets())
            assert any('Next update in' in m for m in info_log.messages) is expected
        finally:
            drv.closePort()


def test_active_map_leaves_out_sensors_that_are_not_paired(egw, info_log):
    weewx.debug = 1
    c = common(egw)
    info_log.clear()
    # a gateway sends empty signal/battery fields for sensor slots with nothing paired
    egw.GatewaySource.log_active_map(c, {'common_list.0x02.val': 1, 'ws80.signal': None, 'wn31.ch2.signal': None})
    assert 'active field map (1 of' in info_log.messages[0]
    assert 'ws80' not in info_log.text and 'wn31' not in info_log.text
