"""Several gateways: [[sources]], the priority merge, totals counted once, outages, catchup, the
service and the commands.

WeeWX works rain out from the gateways' totals ([StdWXCalculate] [[Delta]]), so the merge hands WeeWX
one total per counter field (MergedTotals) and the tests check what WeeWX's Delta makes of it.
"""

import argparse
import os
import time
import types

import pytest
import weewx
import weewx.wxxtypes

from conftest import FIXTURES, FakeGateway, load_dump
from test_driver_service import FakeEngine, next_packet


def weewx_delta(field='rain', total='t_rainyear'):
    """WeeWX's own Delta rule, as do_rain() configures it."""
    return weewx.wxxtypes.Delta({field: {'input': total}})


def delta_of(rule, packet, field='rain'):
    return rule.get_scalar(field, packet, None)[0]


# -- configuration -------------------------------------------------------------------------------

def test_sources_inherit_and_override(egw):
    conf = {'poll_interval': '20', 'ignore_no_signal': 'True', 'sensor_map': {'AA': '1'},
            'catchup': {'source': 'either', 'grace': '5'}, 'limits': {'outTemp': '-50, 60'},
            'source_priority': 'roof, garden',
            'sources': {'garden': {'ip_address': '192.0.2.1', 'sensor_map': {'BB': '2'},
                                   'limits': {'inTemp': '0, 40'}},
                        'roof': {'host': '192.0.2.2', 'port': '8080', 'poll_interval': '30', 'history': 'cloud'},
                        'shed': {'ip_address': '192.0.2.3', 'enabled': 'False'}}}
    configs = egw.source_configs(conf)
    assert [n for n, _ in configs] == ['roof', 'garden']
    roof, garden = dict(configs)['roof'], dict(configs)['garden']
    assert roof['ip_address'] == '192.0.2.2:8080' and roof['poll_interval'] == '30'
    assert roof['catchup'] == {'source': 'net', 'grace': '5'}          # weewx-ecowitt-multi 'history = cloud'
    assert 'sensor_map' not in roof and garden['sensor_map'] == {'BB': '2'}   # sensor IDs are per gateway
    assert garden['ignore_no_signal'] == 'True' and garden['poll_interval'] == '20'
    assert garden['limits'] == {'outTemp': '-50, 60', 'inTemp': '0, 40'}      # subsections are merged
    assert 'sources' not in garden and 'source_priority' not in garden


def test_without_sources_the_section_is_the_gateway(egw):
    conf = {'ip_address': '192.0.2.9'}
    assert egw.source_configs(conf) == [('gateway', conf)]


def test_a_source_needs_an_address(egw):
    with pytest.raises(weewx.ViolatedPrecondition, match='needs an ip_address'):
        egw.source_configs({'sources': {'a': {'poll_interval': '5'}}})
    with pytest.raises(weewx.ViolatedPrecondition, match='every gateway is disabled'):
        egw.source_configs({'sources': {'a': {'ip_address': 'x', 'enabled': 'no'}}})


def test_priority_exact_and_prefix(egw):
    p = egw.SourcePriority(['a', 'b', 'c'], {'outTemp': 'c, a', 'soil*': 'b', 'soilMoist*': 'c', 'x': 'zz'})
    assert p.order('outTemp') == ['c', 'a', 'b']
    assert p.order('soilTemp1') == ['b', 'a', 'c']
    assert p.order('soilMoist1') == ['c', 'a', 'b']           # the longest prefix wins
    assert p.order('inTemp') == ['a', 'b', 'c']
    assert p.order('x') == ['a', 'b', 'c']                    # unknown gateway names are ignored


def test_counter_fields_include_every_delta_input(egw):
    assert egw.counter_fields({}) == ('t_rainyear', 'p_rainyear', 'lightningcount')
    conf = {'StdWXCalculate': {'Delta': {'rain': {'input': 't_rainmonth'}, 't_rain': {'input': 't_rainyear'}}}}
    assert egw.counter_fields(conf) == ('t_rainyear', 'p_rainyear', 'lightningcount', 't_rainmonth')


# -- counters (cases from weewx-ecowitt-multi's tests, as deltas) ------------------------------------

def order(field):
    return ['gw1', 'gw2']


def test_counters_of_two_gateways_are_not_double_counted(egw):
    c = egw.CounterArbiter(order, stale_after=300)
    c.update('gw1', 't_rain', None, 0)
    c.update('gw2', 't_rain', None, 1)
    total = 0
    for ts in (20, 40):
        total += c.update('gw1', 't_rain', 1.0, ts) or 0
        total += c.update('gw2', 't_rain', 1.0, ts + 1) or 0
    assert total == 2.0 and c.provider['t_rain'] == 'gw1'


def test_held_deltas_are_dropped_when_the_provider_comes_back(egw):
    c = egw.CounterArbiter(order, stale_after=60)
    c.update('gw1', 't_rain', None, 0)
    c.update('gw1', 't_rain', 1.0, 20)
    c.update('gw2', 't_rain', None, 30)
    assert c.update('gw2', 't_rain', 0.5, 50) is None         # held
    assert c.update('gw1', 't_rain', 0.5, 60) == 0.5          # gw1 counts (20, 60]: gw2's held 0.5 overlaps
    assert c.held == {}


def test_counter_handover_when_the_provider_goes_quiet(egw):
    c = egw.CounterArbiter(order, stale_after=60)
    c.update('gw1', 't_rain', None, 0)
    assert c.update('gw1', 't_rain', 1.0, 20) == 1.0
    c.update('gw2', 't_rain', None, 30)
    assert c.update('gw2', 't_rain', 0.5, 50) is None         # gw1 is still the provider: held
    assert c.update('gw2', 't_rain', 1.0, 100) == 1.5         # gw1 quiet for 80 s: gw2 takes over, with the
    #                                                           held 0.5 (weewx-ecowitt-multi drops it)
    assert c.update('gw2', 't_rain', 0.2, 110) == 0.2
    assert c.update('gw1', 't_rain', 3.0, 110) is None        # all of it overlaps what gw2 counted
    assert c.update('gw1', 't_rain', 0.4, 140) == pytest.approx(0.4)   # the better gateway is back
    assert c.provider['t_rain'] == 'gw1'


def test_a_partly_counted_delta_keeps_the_rest(egw):
    """gw1 has counted to 30 when gw2 takes over with the delta (20, 40]: (30, 40] is kept, not
    dropped."""
    c = egw.CounterArbiter(order, stale_after=5)
    c.update('gw1', 't_rain', None, 0)
    c.update('gw2', 't_rain', None, 20)
    assert c.update('gw1', 't_rain', 0.3, 30) == 0.3
    assert c.update('gw2', 't_rain', 0.4, 40) == pytest.approx(0.2)    # gw1 quiet: half of it is new
    assert c.update('gw1', 't_rain', 0.6, 60) == pytest.approx(0.6 * 20 / 30)   # the better one, after 40


# -- merged totals ---------------------------------------------------------------------------------

def test_merged_total_is_the_counting_gateways_own_total(egw):
    t = egw.MergedTotals(order, ('t_rainyear',), stale_after=60)
    for ts, v1, v2 in ((0, 450.0, 120.0), (20, 450.5, 120.5), (40, 451.0, 121.0)):
        t.update('gw1', 't_rainyear', v1, ts)
        t.update('gw2', 't_rainyear', v2, ts + 1)
        assert t.total['t_rainyear'] == v1                    # gw2's lower total (paired later) is ignored


def test_merged_total_carries_on_when_another_gateway_counts(egw):
    t = egw.MergedTotals(order, ('t_rainyear',), stale_after=60)
    t.update('gw1', 't_rainyear', 450.0, 0)
    t.update('gw2', 't_rainyear', 120.0, 1)
    t.update('gw1', 't_rainyear', 450.5, 20)                  # gw1 stops answering after this
    for ts, v2 in ((41, 120.7), (61, 120.9), (101, 121.4)):
        t.update('gw2', 't_rainyear', v2, ts)
    # gw2's rise over (1, 41] is counted for (20, 41] only, held with its rise to 61 s until gw1 has
    # been quiet for 60 s, then counted with its rise to 101 s. Nothing twice.
    after_gw1 = 0.7 * 21 / 40 + 0.2 + 0.5
    assert t.total['t_rainyear'] == pytest.approx(450.5 + after_gw1)
    t.update('gw1', 't_rainyear', 451.9, 101)                 # back: all of it overlaps what gw2 counted
    assert t.total['t_rainyear'] == pytest.approx(450.5 + after_gw1)
    t.update('gw1', 't_rainyear', 452.0, 121)                 # the better gateway counts again
    assert t.total['t_rainyear'] == pytest.approx(450.6 + after_gw1)
    assert t.arbiter.provider['t_rainyear'] == 'gw1'


def test_merged_total_follows_a_reset_of_the_counting_gateway(egw):
    t = egw.MergedTotals(order, ('lightningcount',), stale_after=60)
    for ts, v1, v2 in ((0, 7, 7), (20, 9, 9), (40, 0, 9), (60, 1, 0), (80, 2, 1)):   # midnight at 40 s
        t.update('gw1', 'lightningcount', v1, ts)
        t.update('gw2', 'lightningcount', v2, ts + 1)
    assert t.total['lightningcount'] == 2


def test_none_from_limits_is_skipped(egw):
    t = egw.MergedTotals(order, ('t_rainyear',), stale_after=600)
    t.update('gw1', 't_rainyear', 450.0, 0)
    t.update('gw1', 't_rainyear', None, 20)                   # out of [[limits]]
    t.update('gw1', 't_rainyear', 450.4, 40)
    assert t.total['t_rainyear'] == 450.4


def test_weewx_delta_counts_rain_once(egw):
    """Four gateways hear one gauge, each with its own
    total (paired at different times, polled at different times); WeeWX's Delta on the merged total
    must give the rain the gauge measured, once. (Taking each gateway's total by priority as it comes
    would look to WeeWX like a reset or a downpour at every switch.)"""
    egw.define_units()
    names = ['gw1', 'gw2', 'gw3', 'gw4']
    offsets = {'gw1': 300.0, 'gw2': 0.0, 'gw3': 150.0, 'gw4': 42.0}
    m = egw.GatewayMerger(names, max_age=60)
    rule = weewx_delta()
    weewx_rain = 0.0
    polls = sorted((offset + period * k, n) for n, offset, period in
                   (('gw1', 0, 20), ('gw2', 5, 20), ('gw3', 9, 21), ('gw4', 14, 19)) for k in range(30))
    for ts, name in polls:
        if name == 'gw1' and 200 < ts < 400:                  # gw1 goes quiet for a while
            continue
        gauge = round(ts * 0.01, 1)
        m.add(name, {'dateTime': ts, 'usUnits': weewx.METRICWX, 't_rainyear': offsets[name] + gauge})
        packet = m.merged(ts)
        weewx_rain += delta_of(rule, packet) or 0.0
    # the gauge's rise up to the last count (the rest comes with the counting gateway's next poll); the
    # totals are in 0.1 mm, and a partly counted rise is shared out by time
    counted_to = m.totals.arbiter.covered_until['t_rainyear']
    assert weewx_rain == pytest.approx(counted_to * 0.01, abs=0.1)
    assert weewx_rain <= polls[-1][0] * 0.01


# -- merging ---------------------------------------------------------------------------------------

def merger(egw, **kwargs):
    return egw.GatewayMerger(['gw1', 'gw2'], **kwargs)


def test_fallback_priority_and_stale_data(egw):
    m = merger(egw, overrides={'inTemp': 'gw2'}, max_age=60)
    m.add('gw1', {'dateTime': 100, 'outTemp': None, 'inTemp': 20.0, 'UV': 1, 'gateway_online': 1})
    m.add('gw2', {'dateTime': 100, 'outTemp': 5.0, 'inTemp': 21.0})
    packet = m.merged(100)
    assert (packet['outTemp'], packet['inTemp'], packet['UV']) == (5.0, 21.0, 1)
    assert 'gateway_online' not in packet                     # the driver sets it for all the gateways
    assert m.provenance == {'outTemp': 'gw2', 'inTemp': 'gw2', 'UV': 'gw1'}
    assert 'UV' not in m.merged(200) and 'outTemp' not in m.merged(200)   # both are now 100 s old


def test_merged_counters(egw):
    m = merger(egw)
    m.add('gw1', {'dateTime': 0, 't_rainyear': 450.0, 'p_rainyear': None, 't_rainday': 1.0})
    m.add('gw2', {'dateTime': 1, 't_rainyear': 100.0, 'p_rainyear': 30.0, 't_rainday': 1.5})
    packet = m.merged(1)
    assert packet['t_rainyear'] == 450.0 and packet['t_rainday'] == 1.0   # t_rainday: by priority
    assert packet['p_rainyear'] == 30.0                       # only gw2 has a piezo gauge
    assert 'lightningcount' not in packet                     # no gateway reports it


def test_merge_debug_logs_where_fields_came_from(egw, info_log):
    m = merger(egw, debug=True)
    m.add('gw2', {'dateTime': 0, 'outTemp': 5.0})
    m.merged(0)
    assert 'outTemp<gw2' in info_log.text


# -- outages ---------------------------------------------------------------------------------------

@pytest.fixture
def gap(egw):
    egw._LAST_GAP_RESTART['ts'] = None
    return lambda **kw: egw.GapMonitor(archive_interval=300, **kw)


def test_gap_restart_once_every_gateway_is_back(egw, gap):
    g, G = gap(max_gap_wait=600), egw.GapMonitor
    assert g.update(0, {'a': 0, 'b': 0}, {'a', 'b'}, {'a', 'b'}) == G.EMIT
    assert g.update(200, {'a': 0, 'b': 0}, set(), {'a', 'b'}) == G.EMIT        # a short silence
    assert g.update(400, {'a': 0, 'b': 0}, set(), {'a', 'b'}) == G.HOLD        # outage
    assert g.update(500, {'a': 500, 'b': 0}, {'a'}, {'a', 'b'}) == G.HOLD      # waiting for b
    assert g.update(503, {'a': 500, 'b': 503}, {'b'}, {'a', 'b'}) == G.RESTART
    g.update(600, {'a': 600, 'b': 600}, {'a', 'b'}, {'a', 'b'})
    g.update(1000, {'a': 600, 'b': 600}, set(), {'a', 'b'})
    assert g.update(1100, {'a': 1100, 'b': 1100}, {'a', 'b'}, {'a', 'b'}) == G.EMIT   # too soon to restart


def test_gap_max_wait_and_mode_none(egw, gap):
    g, G = gap(max_gap_wait=600), egw.GapMonitor
    g.update(0, {'a': 0, 'b': 0}, {'a', 'b'}, {'a'})
    g.update(400, {'a': 0, 'b': 0}, set(), {'a'})
    assert g.update(500, {'a': 500, 'b': 0}, {'a'}, {'a'}) == G.HOLD
    assert g.update(1100, {'a': 1100, 'b': 0}, {'a'}, {'a'}) == G.RESTART
    g = gap(mode='none')
    g.update(0, {'a': 0}, {'a'}, {'a'})
    g.update(400, {'a': 0}, set(), {'a'})
    assert g.update(500, {'a': 500}, {'a'}, {'a'}) == G.EMIT


def test_no_restart_without_history(egw, gap):
    g, G = gap(), egw.GapMonitor
    g.update(0, {'a': 0}, {'a'}, set())
    g.update(400, {'a': 0}, set(), set())
    assert g.update(500, {'a': 500}, {'a'}, set()) == G.EMIT


# -- history ---------------------------------------------------------------------------------------

def test_aggregation(egw):
    egw.HttpMapper()                                          # gives the WeeWX fields their unit groups
    recs = [{'dateTime': 60 * m, 'outTemp': 10.0 + m, 't_rainyear': 450.0 + m * 0.2, 'windGust': 3.0 + m,
             'windDir': d, 't_rainday': 1.0 + m, 't_rainrate': r, 'lightningcount': m}
            for m, d, r in ((1, 350.0, 1.0), (2, 10.0, 4.0), (3, 20.0, 2.0))]
    (end, rec), = egw.aggregate_records(recs, 300).items()
    assert end == 300
    assert rec['outTemp'] == 12.0 and rec['windGust'] == 6.0 and rec['t_rainrate'] == 4.0
    assert rec['t_rainyear'] == pytest.approx(450.6) and rec['t_rainday'] == 4.0 and rec['lightningcount'] == 3
    assert rec['windDir'] == pytest.approx(6.7, abs=0.1)


def test_history_merge(egw):
    p = egw.SourcePriority(['gw1', 'gw2'])
    totals = egw.MergedTotals(p.order, ('t_rainyear',))
    per_source = {'gw1': {300: {'outTemp': 1.0, 't_rainyear': 450.0}, 600: {'t_rainyear': 450.2},
                          1200: {'outTemp': 4.0, 't_rainyear': 450.9}},
                  'gw2': {300: {'outTemp': 2.0, 'inTemp': 20.0, 't_rainyear': 100.0},
                          600: {'outTemp': 3.0, 't_rainyear': 100.2},
                          900: {'outTemp': 3.5, 't_rainyear': 100.5}, 1200: {'t_rainyear': 100.7}}}
    recs = egw.merge_history(per_source, p, 300, 300, 1200, totals)
    assert [r['dateTime'] for r in recs] == [600, 900, 1200]  # 300 only sets the totals up
    assert recs[0] == {'dateTime': 600, 'usUnits': weewx.METRICWX, 'interval': 5, 'outTemp': 3.0,
                       't_rainyear': 450.2}
    # gw1 has no 900 record: gw2 counts it; gw1's rise to 1200 overlaps it, so gw2's is used
    assert [r['t_rainyear'] for r in recs] == pytest.approx([450.2, 450.5, 450.7])
    assert recs[2]['outTemp'] == 4.0
    # the loop packets carry on from there
    assert totals.arbiter.provider['t_rainyear'] == 'gw2' and totals.arbiter.covered_until['t_rainyear'] == 1200
    totals.update('gw1', 't_rainyear', 451.0, 1220)
    assert totals.total['t_rainyear'] == pytest.approx(450.8)


# -- the driver and service with two fake gateways -----------------------------------------------

@pytest.fixture
def two(egw):
    g1, g2 = FakeGateway(load_dump('gw2000_ws90.json')), FakeGateway(load_dump('gw2000_ws90.json'))
    g2.live('common_list', '0x02')['val'] = '13.5'
    g2.live('wh25')['intemp'] = '25.0'
    g2.live('rain', '0x13')['val'] = '120.0 mm'              # paired later than gw1: a lower year total
    yield g1, g2
    g1.close()
    g2.close()


def multi_config(two, **extra):
    g1, g2 = two
    return {'api': 'http', 'poll_interval': '1', 'max_tries': '1', 'retry_wait': '0', 'url_timeout': '2',
            'firmware_update_check_interval': '0', 'catchup': {'source': 'none'}, 'max_age': '30',
            'sources': {'gw1': {'ip_address': g1.address}, 'gw2': {'ip_address': g2.address}}, **extra}


def packets_until(gen, test, limit=20):
    for _ in range(limit):
        packet = next_packet(gen)
        if test(packet):
            return packet
    raise AssertionError('no packet matched')


def both_online(packet):
    return packet.get('gateway_online_gw1') == 1 and packet.get('gateway_online_gw2') == 1


def test_driver_merges_two_gateways(make_driver, two):
    g1, g2 = two
    drv = make_driver(archive_interval=300, **multi_config(two, priority={'inTemp': 'gw2'}))
    assert drv.multi and [s.name for s in drv.sources] == ['gw1', 'gw2'] and drv.source.name == 'gw1'
    gen = drv.genLoopPackets()
    packet = packets_until(gen, both_online)
    assert packet['outTemp'] == 12.3 and packet['inTemp'] == 25.0 and packet['gateway_online'] == 1
    assert packet['t_rainyear'] == 450.6 and packet['usUnits'] == weewx.METRICWX
    g1.responses['get_livedata_info']['common_list'] = [i for i in g1.responses['get_livedata_info']['common_list']
                                                         if i['id'] != '0x02']
    packets_until(gen, lambda p: p.get('outTemp') == 13.5)      # gw1 lost it: gw2's is used
    assert drv.merger.provenance['outTemp'] == 'gw2'


def test_rain_from_two_gateways_is_counted_once(make_driver, two):
    drv = make_driver(archive_interval=300, **multi_config(two))
    gen = drv.genLoopPackets()
    rule = weewx_delta()
    delta_of(rule, packets_until(gen, both_online))
    time.sleep(1.5)
    g1, g2 = two
    g1.live('rain', '0x13')['val'] = '451.6 mm'                 # both hear the same 1 mm
    g2.live('rain', '0x13')['val'] = '121.0 mm'
    total, start = 0.0, time.time()
    while time.time() - start < 6:
        total += delta_of(rule, next_packet(gen)) or 0.0
    assert total == pytest.approx(1.0)


def test_one_gateway_down_does_not_stop_the_driver(make_driver, two, info_log):
    g1, g2 = two
    drv = make_driver(archive_interval=300, **multi_config(two, max_age='3'))
    gen = drv.genLoopPackets()
    packets_until(gen, both_online)
    g1.down = True
    packet = packets_until(gen, lambda p: p.get('gateway_online_gw1') == 0 and p.get('outTemp') == 13.5)
    assert packet['gateway_online'] == 1                        # gw2 is still answering
    assert drv.sources[0].status.online is False

    def errors():
        # (max_tries is 1 here: other tests' collector threads may still be logging their own)
        return len([r for r in info_log.records if r.levelname == 'ERROR'
                    and 'Failed to get device data on attempt 1 of 1' in r.message])
    logged = errors()
    for _ in range(4):                                         # a few more failed polls of gw1
        next_packet(gen)
    assert info_log.text.count('gateway gw1 is not answering') == 1
    # once the failure is logged, the failed polls are not (ecowitt_http.py's API logged every one)
    assert errors() == logged
    g1.down = False
    packets_until(gen, lambda p: p.get('gateway_online_gw1') == 1 and p.get('outTemp') == 12.3)
    assert 'gateway gw1 is answering again' in info_log.text


def test_merged_catchup(make_driver, two):
    drv = make_driver(archive_interval=300, **multi_config(two))
    base = (int(time.time()) // 300 - 4) * 300
    hist = {'gw1': [{'dateTime': base + 60 * n, 'usUnits': weewx.METRICWX, 'interval': 1, 'outTemp': 10.0,
                     't_rainyear': 450.0 + 0.1 * n} for n in range(11)],
            'gw2': [{'dateTime': base + 300 * n, 'usUnits': weewx.METRICWX, 'interval': 5, 'outTemp': 20.0,
                     'inTemp': 21.0, 't_rainyear': 100.0 + 0.5 * n} for n in range(3)]}
    for source in drv.sources:
        source.history = lambda since_ts, archive_interval=None, n=source.name: iter(
            r for r in hist[n] if r['dateTime'] > since_ts)
    records = list(drv.genArchiveRecords(base + 300))
    assert [r['dateTime'] - base for r in records] == [600]
    assert records[0]['interval'] == 5 and records[0]['outTemp'] == 10.0 and records[0]['inTemp'] == 21.0
    assert records[0]['t_rainyear'] == pytest.approx(451.0)    # gw1's 1-minute records, the last of the 5
    assert list(drv.genArchiveRecords(None)) == []             # an empty database: nothing to catch up


def test_merged_catchup_from_two_sd_cards(make_driver, two):
    """Both gateways' SD cards: gw1 has no 10:05 record, so gw2's counts for it, and the rain WeeWX works
    out over the records is the gauge's rise, once."""
    g1, g2 = two
    with open(os.path.join(FIXTURES, 'sd_202611A.csv'), encoding='utf-8') as f:
        text = f.read()
    lines = text.splitlines()
    g1.files['202611A.csv'] = '\n'.join(line for line in lines if not line.startswith('2026-11-06 10:05')) + '\n'
    g2.files['202611A.csv'] = text.replace(',449.', ',149.').replace(',450.', ',150.')
    config = multi_config(two, catchup={'source': 'device', 'retries': '1'})
    config['sources']['gw1']['catchup'] = {'file_port': str(g1.port)}
    config['sources']['gw2']['catchup'] = {'file_port': str(g2.port)}
    drv = make_driver(archive_interval=300, **config)
    start = int(time.mktime(time.strptime('2026-11-06 10:00', '%Y-%m-%d %H:%M')))
    records = list(drv.genArchiveRecords(start))
    assert [r['dateTime'] - start for r in records] == [300, 600]
    assert [r['t_rainyear'] for r in records] == pytest.approx([449.8, 450.0])
    assert records[0]['outTemp'] == 10.7 and records[1]['outTemp'] == 10.8
    rule = weewx_delta()
    delta_of(rule, {'usUnits': weewx.METRICWX, 't_rainyear': 449.4})
    assert sum(delta_of(rule, r) for r in records) == pytest.approx(0.6)


def test_service_with_two_gateways(egw, two):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittGateway': multi_config(two, priority={'inTemp': 'gw2'})})
    try:
        start = time.time()
        while any(s.queue.empty() for s in svc.sources):
            assert time.time() - start < 10
            time.sleep(0.1)
        packet = {'dateTime': int(time.time()), 'usUnits': weewx.US, 'outTemp': 99.0}
        svc.new_loop_packet(types.SimpleNamespace(packet=packet))
        assert packet['outTemp'] == 99.0 and round(packet['inTemp'], 1) == 77.0
        assert round(packet['t_rainyear'], 2) == 17.74          # gw1's 450.6 mm, in inches
        assert packet['gateway_online_gw1'] == packet['gateway_online_gw2'] == packet['gateway_online'] == 1
        assert packet['usUnits'] == weewx.US and 'dateTime' in packet
    finally:
        svc.shutDown()


def test_one_section_in_sources_is_one_gateway(make_driver, two):
    config = multi_config(two)
    del config['sources']['gw2']
    drv = make_driver(**config)
    assert not drv.multi and drv.merger is None and drv.ip_address == two[0].address
    assert 'gateway_online_gw1' not in next_packet(drv.genLoopPackets())


def test_outage_of_every_gateway_restarts_for_catchup(egw, make_driver, two):
    egw._LAST_GAP_RESTART['ts'] = None
    drv = make_driver(archive_interval=3, **multi_config(two, catchup={'source': 'device'}))
    gen = drv.genLoopPackets()
    packets_until(gen, both_online)
    for g in two:
        g.down = True
    time.sleep(6)                                              # longer than archive_interval
    for g in two:
        g.down = False
    with pytest.raises(weewx.WeeWxIOError, match='catchup fills the outage'):
        for _ in gen:                                          # nothing is emitted until the restart
            assert False, 'a loop packet was emitted during the outage'


def test_loader_passes_the_delta_inputs(egw, two):
    conf = {'EcowittGateway': multi_config(two),
            'StdWXCalculate': {'Delta': {'rain': {'input': 't_rainmonth'}}}}
    drv = egw.loader(conf, None)
    try:
        assert 't_rainmonth' in drv.merger.counters
    finally:
        drv.closePort()


# -- commands --------------------------------------------------------------------------------------

def direct(egw, config, **options):
    namespace = argparse.Namespace(**{'ip_address': None, 'show_battery': None, 'only_registered': None,
                                      'source': None, **options})
    return egw.DirectEcowittDevice(namespace, None, config, archive_interval=300)


def test_source_option_picks_the_gateway(egw, two):
    config = multi_config(two)
    assert direct(egw, dict(config)).ip_address == two[0].address
    assert direct(egw, dict(config), source='gw2').ip_address == two[1].address
    with pytest.raises(weewx.ViolatedPrecondition, match='no such gateway'):
        direct(egw, dict(config), source='roof')
    with pytest.raises(weewx.ViolatedPrecondition, match='there is no'):
        direct(egw, {'ip_address': two[0].address}, source='gw2')


def test_sources_command(egw, two, capsys):
    two[1].down = True
    direct(egw, multi_config(two, priority={'outTemp': 'gw2'})).display_sources()
    out = capsys.readouterr().out
    assert out.count('GW2000 V3.1.4') == 1 and 'not answering' in out and 'outTemp = gw2' in out


@pytest.fixture
def cli_config(two, tmp_path):
    import configobj
    config = configobj.ConfigObj({'debug': '0', 'EcowittGateway': multi_config(two)})
    config.filename = str(tmp_path / 'weewx.conf')
    config.write()
    return config


@pytest.fixture(autouse=True)
def no_logging_setup(monkeypatch):
    import weeutil.logger
    monkeypatch.setattr(weeutil.logger, 'setup', lambda *args, **kwargs: None)


def weectl_device(egw, config, *args):
    import optparse
    configurator = egw.configurator_loader(config)
    parser = optparse.OptionParser()
    configurator.add_options(parser)
    options, _ = parser.parse_args(list(args))
    configurator.do_options(options, parser, config, False)


@pytest.mark.parametrize('args', [('--sources',), ('--firmware', '--source=gw2'), ('--live-data',),
                                  ('--catchup-dry-run',), ('--source=nope', '--firmware')])
def test_weectl_device_with_sources(egw, cli_config, args, capsys):
    weectl_device(egw, cli_config, *args)
    out = capsys.readouterr().out
    assert 'Traceback' not in out
    if args == ('--sources',):
        assert out.count('GW2000 V3.1.4') == 2
    if args[-1] == '--source=gw2':
        assert two_address(cli_config, 'gw2') in out or 'GW2000' in out
    if args == ('--catchup-dry-run',):
        assert 'Gateways, in priority order' in out and 'Merged 5-minute records' in out
    if args[0] == '--source=nope':
        assert 'no such gateway' in out


def two_address(config, name):
    return config['EcowittGateway']['sources'][name]['ip_address']


@pytest.mark.parametrize('command', ['--sources', '--weewx-fields', '--driver-map', '--catchup-dry-run'])
def test_direct_command_line_with_sources(egw, cli_config, command, capsys, monkeypatch):
    import sys
    monkeypatch.setattr(sys, 'argv', ['weewx-ecowittGateway.py', command, f'--config={cli_config.filename}'])
    egw.main()
    out = capsys.readouterr().out
    assert 'Traceback' not in out and 'Unable' not in out
    if command == '--weewx-fields':
        assert 'gateway_online_gw2' in out


def test_direct_command_line_unknown_source(egw, cli_config, capsys, monkeypatch):
    import sys
    monkeypatch.setattr(sys, 'argv', ['weewx-ecowittGateway.py', '--firmware', '--source=nope',
                                      f'--config={cli_config.filename}'])
    with pytest.raises(SystemExit):
        egw.main()
    assert 'no such gateway' in capsys.readouterr().out
