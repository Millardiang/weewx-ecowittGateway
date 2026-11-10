"""Several gateways: [[sources]], priority merge, counters, outages, catchup, service and commands."""

import argparse
import time
import types

import pytest
import weewx

from conftest import FakeGateway, load_dump
from test_driver_service import FakeEngine, next_packet


# -- configuration -------------------------------------------------------------------------------

def test_sources_inherit_and_override(egw):
    conf = {'poll_interval': '20', 'rain_source': 'both', 'sensor_map': {'AA': '1'}, 'catchup': {'source': 'either',
                                                                                                 'grace': '5'},
            'source_priority': 'roof, garden',
            'sources': {'garden': {'ip_address': '192.0.2.1', 'sensor_map': {'BB': '2'}},
                        'roof': {'host': '192.0.2.2', 'port': '8080', 'poll_interval': '30', 'history': 'cloud'},
                        'shed': {'ip_address': '192.0.2.3', 'enabled': 'False'}}}
    configs = egw.source_configs(conf)
    assert [n for n, _ in configs] == ['roof', 'garden']
    roof, garden = dict(configs)['roof'], dict(configs)['garden']
    assert roof['ip_address'] == '192.0.2.2:8080' and roof['poll_interval'] == '30'
    assert roof['catchup'] == {'source': 'net', 'grace': '5'}          # weewx-ecowitt-multi 'history = cloud'
    assert 'sensor_map' not in roof and garden['sensor_map'] == {'BB': '2'}   # hardware IDs are per gateway
    assert garden['rain_source'] == 'both' and garden['poll_interval'] == '20'
    assert 'sources' not in garden and 'source_priority' not in garden


def test_a_source_needs_an_address(egw):
    with pytest.raises(weewx.ViolatedPrecondition, match='needs an ip_address'):
        egw.source_configs({'sources': {'a': {'poll_interval': '5'}}})


def test_priority_exact_and_prefix(egw):
    p = egw.SourcePriority(['a', 'b', 'c'], {'outTemp': 'c, a', 'soil*': 'b', 'soilMoist*': 'c', 'x': 'zz'})
    assert p.order('outTemp') == ['c', 'a', 'b']
    assert p.order('soilTemp1') == ['b', 'a', 'c']
    assert p.order('soilMoist1') == ['c', 'a', 'b']           # the longest prefix wins
    assert p.order('inTemp') == ['a', 'b', 'c']
    assert p.order('x') == ['a', 'b', 'c']                    # unknown gateway names are ignored


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
    assert c.update('gw1', 't_rain', 3.0, 120) is None        # overlaps what gw2 counted
    assert c.update('gw1', 't_rain', 0.5, 140) == 0.5         # the better gateway is back
    assert c.provider['t_rain'] == 'gw1'


# -- merging ---------------------------------------------------------------------------------------

def merger(egw, **kwargs):
    return egw.GatewayMerger(['gw1', 'gw2'], **kwargs)


def test_fallback_priority_and_stale_data(egw):
    m = merger(egw, overrides={'inTemp': 'gw2'}, max_age=60)
    m.add('gw1', {'dateTime': 100, 'outTemp': None, 'inTemp': 20.0, 'UV': 1}, set())
    m.add('gw2', {'dateTime': 100, 'outTemp': 5.0, 'inTemp': 21.0}, set())
    packet = m.merged(100)
    assert (packet['outTemp'], packet['inTemp'], packet['UV']) == (5.0, 21.0, 1)
    assert m.provenance == {'outTemp': 'gw2', 'inTemp': 'gw2', 'UV': 'gw1'}
    assert 'UV' not in m.merged(200) and 'outTemp' not in m.merged(200)   # both are now 100 s old


def test_merged_rain(egw):
    m = merger(egw, rain_source='piezo')
    for ts, d1, d2 in ((0, None, None), (20, 0.5, 0.4), (40, 0.3, 0.3)):
        m.add('gw1', {'dateTime': ts, 't_rain': d1, 'p_rain': d1}, {'t_rain', 'p_rain'})
        m.add('gw2', {'dateTime': ts + 1, 't_rain': d2, 'p_rain': d2}, {'t_rain', 'p_rain'})
    packet = m.merged(41)
    assert packet['t_rain'] == pytest.approx(0.8) and packet['rain'] == packet['p_rain'] == packet['hail']
    assert m.merged(42)['t_rain'] == 0.0                      # counted once only (0.0, not None: see below)


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


# -- history ---------------------------------------------------------------------------------------

def test_aggregation(egw):
    egw.define_units()
    recs = [{'dateTime': 60 * m, 'outTemp': 10.0 + m, 't_rain': 0.2, 'windGust': 3.0 + m, 'windDir': d,
             'dayRain': 1.0 + m} for m, d in ((1, 350.0), (2, 10.0), (3, 20.0))]
    (end, rec), = egw.aggregate_records(recs, 300).items()
    assert end == 300
    assert rec['outTemp'] == 12.0 and rec['t_rain'] == pytest.approx(0.6) and rec['windGust'] == 6.0
    assert rec['dayRain'] == 4.0 and rec['windDir'] == pytest.approx(6.7, abs=0.1)


def test_history_merge(egw):
    p = egw.SourcePriority(['gw1', 'gw2'])
    per_source = {'gw1': {300: {'outTemp': 1.0, 't_rain': 0.2}, 600: {'t_rain': None}},
                  'gw2': {300: {'outTemp': 2.0, 'inTemp': 20.0, 't_rain': 0.4}, 600: {'outTemp': 3.0, 't_rain': 0.1}}}
    recs = egw.merge_history(per_source, p, 300, 'tipping', 0, 1000)
    assert recs[0] == {'dateTime': 300, 'usUnits': weewx.METRICWX, 'interval': 5, 'outTemp': 1.0, 'inTemp': 20.0,
                       't_rain': 0.2, 'rain': 0.2, 'hail': None}
    assert recs[1]['t_rain'] == 0.1 and recs[1]['outTemp'] == 3.0


# -- the driver and service with two fake gateways -----------------------------------------------

@pytest.fixture
def two(egw):
    g1, g2 = FakeGateway(load_dump('gw2000_ws90.json')), FakeGateway(load_dump('gw2000_ws90.json'))
    g2.live('common_list', '0x02')['val'] = '13.5'
    g2.live('wh25')['intemp'] = '25.0'
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


def test_driver_merges_two_gateways(egw, two):
    g1, g2 = two
    drv = egw.EcowittHttpDriver(archive_interval=300, **multi_config(two, priority={'inTemp': 'gw2'}))
    try:
        gen = drv.genLoopPackets()
        packet = packets_until(gen, lambda p: p.get('gateway_online_gw2') == 1 and p.get('gateway_online_gw1') == 1)
        assert packet['outTemp'] == 12.3 and packet['inTemp'] == 25.0
        g1.responses['get_livedata_info']['common_list'] = [i for i in g1.responses['get_livedata_info']['common_list']
                                                             if i['id'] != '0x02']
        packet = packets_until(gen, lambda p: p.get('outTemp') == 13.5)      # gw1 lost it: gw2's is used
        assert drv.merger.provenance['outTemp'] == 'gw2'
    finally:
        drv.closePort()


def test_rain_from_two_gateways_is_counted_once(egw, two):
    g1, g2 = two
    drv = egw.EcowittHttpDriver(archive_interval=300, **multi_config(two))
    try:
        gen = drv.genLoopPackets()
        packets_until(gen, lambda p: p.get('gateway_online_gw2') == 1 and p.get('gateway_online_gw1') == 1)
        time.sleep(1.5)
        for g in two:                                            # both hear the same gauge
            g.live('rain', '0x13')['val'] = '451.6 mm'
        total, start = 0.0, time.time()
        while time.time() - start < 6:
            total += next_packet(gen).get('t_rain') or 0.0
        assert total == pytest.approx(1.0)
    finally:
        drv.closePort()


def test_one_gateway_down_does_not_stop_the_driver(egw, two):
    g1, g2 = two
    drv = egw.EcowittHttpDriver(archive_interval=300, **multi_config(two, max_age='3'))
    try:
        gen = drv.genLoopPackets()
        packets_until(gen, lambda p: p.get('gateway_online_gw1') == 1)
        g1.down = True
        packet = packets_until(gen, lambda p: p.get('gateway_online_gw1') == 0 and p.get('outTemp') == 13.5)
        assert packet['gateway_online'] == 1               # gw2 is still answering
        assert drv.sources[0].status.online is False
    finally:
        drv.closePort()


def test_merged_catchup(egw, two):
    drv = egw.EcowittHttpDriver(archive_interval=300, **multi_config(two))
    base = (int(time.time()) // 300 - 4) * 300
    hist = {'gw1': [{'dateTime': base + 60 * n, 'usUnits': weewx.METRICWX, 'interval': 1, 'outTemp': 10.0,
                     't_rain': None if n == 0 else 0.1} for n in range(11)],
            'gw2': [{'dateTime': base + 300 * n, 'usUnits': weewx.METRICWX, 'interval': 5, 'outTemp': 20.0,
                     'inTemp': 21.0, 't_rain': 0.5} for n in range(3)]}
    for source in drv.sources:
        source.history = lambda since_ts, archive_interval=None, n=source.name: iter(
            r for r in hist[n] if r['dateTime'] > since_ts)
    try:
        records = list(drv.genArchiveRecords(base))
    finally:
        drv.closePort()
    assert [r['dateTime'] - base for r in records] == [300, 600]
    assert all(r['interval'] == 5 and r['outTemp'] == 10.0 and r['inTemp'] == 21.0 for r in records)
    assert records[0]['t_rain'] == pytest.approx(0.5) and records[0]['rain'] == records[0]['t_rain']
    assert list(egw.EcowittHttpDriver.gen_merged_archive_records(drv, None)) == []


def test_service_with_two_gateways(egw, two):
    svc = egw.EcowittHttpService(FakeEngine(), {'EcowittGateway': multi_config(two, priority={'inTemp': 'gw2'})})
    try:
        start = time.time()
        while any(s.collector.queue.empty() for s in svc.sources):
            assert time.time() - start < 10
            time.sleep(0.1)
        packet = {'dateTime': int(time.time()), 'usUnits': weewx.US, 'outTemp': 99.0}
        svc.new_loop_packet(types.SimpleNamespace(packet=packet))
        assert packet['outTemp'] == 99.0 and round(packet['inTemp'], 1) == 77.0
        assert packet['gateway_online_gw1'] == packet['gateway_online_gw2'] == packet['gateway_online'] == 1
    finally:
        svc.shutDown()


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


def test_sources_command(egw, two, capsys):
    direct(egw, multi_config(two, priority={'outTemp': 'gw2'})).display_sources()
    out = capsys.readouterr().out
    assert out.count('GW2000A_V3.1.4') == 2 and 'outTemp = gw2' in out


# -- SD card fixes taken from weewx-ecowitt-multi --------------------------------------------------

def test_sd_files_not_named_by_month_are_skipped(egw):
    info = {'file_list': [{'name': 'log', 'type': 'file'}, {'name': '202611A.csv', 'type': 'file'}]}
    assert egw.EcowittDeviceCatchup.get_file_list(info) == {202611: ['202611A.csv']}


def test_sd_files_use_port_81_when_the_address_has_a_port(egw, monkeypatch):
    seen = []
    monkeypatch.setattr('urllib.request.urlopen', lambda url, timeout=None: seen.append(url) or 'ok')
    catchup = egw.EcowittDeviceCatchup.__new__(egw.EcowittDeviceCatchup)
    catchup.ip_address, catchup.max_catchup_retries, catchup.url_timeout = '192.0.2.1:8080', 1, 1
    catchup.get_file('202611A.csv')
    assert seen == ['http://192.0.2.1:81/202611A.csv']


def test_outage_of_every_gateway_restarts_for_catchup(egw, two):
    egw._LAST_GAP_RESTART['ts'] = None
    drv = egw.EcowittHttpDriver(archive_interval=3, **multi_config(two, catchup={'source': 'device'}))
    try:
        gen = drv.genLoopPackets()
        packets_until(gen, lambda p: p.get('gateway_online_gw1') == 1 and p.get('gateway_online_gw2') == 1)
        for g in two:
            g.down = True
        time.sleep(6)                                      # longer than archive_interval
        for g in two:
            g.down = False
        with pytest.raises(weewx.WeeWxIOError, match='catchup fills the outage'):
            for packet in gen:                             # nothing is emitted until the restart
                assert time.time() < 0, 'a loop packet was emitted during the outage'
    finally:
        drv.closePort()


def test_weewx_delta_rule_cannot_add_rain_of_its_own(egw):
    """1.3.1 reported rain twice with four gateways: merged packets with nothing new had rain = None,
    so WeeWX's default [[Delta]] rule (rain from totalRain) filled them in on top of the counted rain."""
    import weewx.wxxtypes
    egw.define_units()
    names = ['gw1', 'gw2', 'gw3', 'gw4']
    m = egw.GatewayMerger(names, max_age=60)
    trackers = {n: egw.DeltaTracker('rain', ('rain.0x13.val',)) for n in names}
    delta_rule = weewx.wxxtypes.Delta({'rain': {'input': 'totalRain'}})
    weewx_rain = counted = 0.0
    polls = sorted((offset + period * k, n) for n, offset, period in
                   (('gw1', 0, 20), ('gw2', 5, 20), ('gw3', 9, 21), ('gw4', 14, 19)) for k in range(30))
    for ts, name in polls:
        total = round(ts * 0.01, 1)                          # one gauge heard by every gateway
        m.add(name, {'dateTime': ts, 't_rain': trackers[name].update({'rain.0x13.val': total}),
                     'totalRain': total}, {'t_rain'})
        packet = m.merged(ts)
        counted += packet.get('t_rain') or 0.0
        if packet.get('rain') is None and packet.get('totalRain') is not None:   # what StdWXCalculate does
            packet['rain'] = delta_rule.get_scalar('rain', packet, None)[0]
        weewx_rain += packet.get('rain') or 0.0
    gauge_rise = round(polls[-1][0] * 0.01, 1) - round(polls[0][0] * 0.01, 1)
    assert weewx_rain == pytest.approx(counted)               # 1.3.1: about twice as much
    assert gauge_rise - 0.4 <= counted <= gauge_rise          # less only the rain since the last count


def test_delta_rule_warning(egw, caplog):
    egw.warn_about_delta({'StdWXCalculate': {'Delta': {'rain': {'input': 'totalRain'}}}}, {'sources': {'a': {}}})
    assert '[[Delta]] has a rule for rain' in caplog.text
    caplog.clear()
    egw.warn_about_delta({'StdWXCalculate': {'Delta': {'rain': {'input': 'totalRain'}}}}, {'ip_address': 'x'})
    assert caplog.text == ''
