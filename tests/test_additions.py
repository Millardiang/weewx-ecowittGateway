"""EcowittAdditions: the fields the station's database has no column for, archived in a database of
their own (ecowitt_additions.sdb), with columns added as fields first have a value."""

import logging
import sqlite3
import types

import configobj
import pytest
import weewx
import weewx.manager


@pytest.fixture(autouse=True)
def restore_xtypes():
    """Each EcowittAdditions adds its AdditionsXType; WeeWX runs one."""
    import weewx.xtypes
    saved = list(weewx.xtypes.xtypes)
    yield
    weewx.xtypes.xtypes[:] = saved


def config(tmp_path, enable=True):
    return configobj.ConfigObj({
        'WEEWX_ROOT': str(tmp_path),
        'StdArchive': {'data_binding': 'wx_binding'},
        'EcowittGateway': {'additions': {'enable': str(enable)}},
        'DataBindings': {
            'wx_binding': {'database': 'archive_sqlite', 'table_name': 'archive',
                           'manager': 'weewx.manager.DaySummaryManager',
                           'schema': 'weewx.schemas.wview_extended.schema'},
            'ecowitt_binding': {'database': 'ecowitt_sqlite', 'table_name': 'archive',
                                'manager': 'weewx.manager.DaySummaryManager',
                                'schema': 'user.weewx-ecowittGateway.ADDITIONS_SCHEMA'}},
        'Databases': {'archive_sqlite': {'database_name': 'weewx.sdb', 'database_type': 'SQLite'},
                      'ecowitt_sqlite': {'database_name': 'ecowitt_additions.sdb', 'database_type': 'SQLite'}},
        'DatabaseTypes': {'SQLite': {'driver': 'weedb.sqlite', 'SQLITE_ROOT': str(tmp_path)}},
    })


class Engine:
    """The parts of the WeeWX engine a service uses."""

    def __init__(self, config_dict):
        self.db_binder = weewx.manager.DBBinder(config_dict)
        self.handlers = {}

    def bind(self, event_type, callback):
        self.handlers.setdefault(event_type, []).append(callback)

    def archive(self, record, origin='software'):
        """As StdArchive and the services after it see a new archive record."""
        self.db_binder.get_manager('wx_binding', initialize=True).addRecord(record)
        for callback in self.handlers.get(weewx.NEW_ARCHIVE_RECORD, []):
            callback(types.SimpleNamespace(record=record, origin=origin))


@pytest.fixture
def station(egw, tmp_path):
    conf = config(tmp_path)
    engine = Engine(conf)
    service = egw.EcowittAdditions(engine, conf)
    yield engine, service, tmp_path
    engine.db_binder.close()


def rows(path, sql):
    with sqlite3.connect(str(path)) as db:
        return db.execute(sql).fetchall()


def record(ts, **fields):
    return dict(dateTime=ts, usUnits=weewx.US, interval=5, **fields)


def test_fields_without_a_column_go_to_the_additions_database(station):
    engine, _, tmp_path = station
    engine.archive(record(1_700_000_100, outTemp=50.0, wh40_batt=1.6, t_rainday=0.12, wh51_ch9_sig=None,
                          model='GW2000'))
    db = tmp_path / 'ecowitt_additions.sdb'
    columns = [c[1] for c in rows(db, 'PRAGMA table_info(archive)')]
    # outTemp has a column in the station's database; a field with no value yet gets none; text is left out
    assert columns == ['dateTime', 'usUnits', 'interval', 'wh40_batt', 't_rainday']
    assert rows(db, 'SELECT dateTime, wh40_batt, t_rainday FROM archive') == [(1_700_000_100, 1.6, 0.12)]
    assert rows(tmp_path / 'weewx.sdb', 'SELECT outTemp FROM archive') == [(50.0,)]
    # daily summaries, so skins can use $day($data_binding='ecowitt_binding').wh40_batt.max and the like
    tables = {t[0] for t in rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'archive_day_wh40_batt', 'archive_day_t_rainday'} <= tables


def test_a_column_is_added_when_a_field_first_has_a_value(station, caplog):
    engine, _, tmp_path = station
    caplog.set_level(logging.INFO)
    engine.archive(record(1_700_000_100, wh40_batt=1.6, wh51_ch9_sig=None))
    engine.archive(record(1_700_000_400, wh40_batt=1.5, wh51_ch9_sig=4))
    db = tmp_path / 'ecowitt_additions.sdb'
    assert rows(db, 'SELECT dateTime, wh40_batt, wh51_ch9_sig FROM archive ORDER BY dateTime') == [
        (1_700_000_100, 1.6, None), (1_700_000_400, 1.5, 4.0)]
    assert any('added wh51_ch9_sig to ecowitt_binding' in m for m in caplog.messages)
    manager = engine.db_binder.get_manager('ecowitt_binding')
    assert manager.getAggregate(weeutil_timespan(1_700_000_000, 1_700_001_000), 'wh40_batt', 'max')[0] == 1.6


def weeutil_timespan(start, stop):
    import weeutil.weeutil
    return weeutil.weeutil.TimeSpan(start, stop)


def test_catchup_records_too(station):
    engine, _, tmp_path = station
    engine.archive(record(1_700_000_100, wh40_batt=1.6), origin='hardware')
    assert rows(tmp_path / 'ecowitt_additions.sdb', 'SELECT wh40_batt FROM archive') == [(1.6,)]


def test_nothing_to_add(station):
    engine, _, tmp_path = station
    engine.archive(record(1_700_000_100, outTemp=50.0, wh40_batt=None))
    assert not (tmp_path / 'ecowitt_additions.sdb').exists()


def test_off_unless_enabled(egw, tmp_path):
    conf = config(tmp_path, enable=False)
    engine = Engine(conf)
    egw.EcowittAdditions(engine, conf)
    assert not engine.handlers
    engine.archive(record(1_700_000_100, wh40_batt=1.6))
    assert not (tmp_path / 'ecowitt_additions.sdb').exists()
    engine.db_binder.close()


def test_an_error_never_stops_weewx(station, monkeypatch, caplog):
    engine, service, _ = station
    monkeypatch.setattr(service, 'binding', 'no_such_binding')
    engine.archive(record(1_700_000_100, wh40_batt=1.6))
    assert any('could not archive the additional fields' in m for m in caplog.messages)


# ---- driver fields that go to a matching station database column first ----

@pytest.fixture
def columns_map(egw):
    """Leave FieldMapper.station_columns as it was."""
    saved = egw.FieldMapper.station_columns
    yield egw
    egw.FieldMapper.station_columns = saved


def test_station_columns_reads_the_schema_when_there_is_no_database_yet(egw, tmp_path):
    columns = egw.station_columns(config(tmp_path))
    assert {'inTempBatteryStatus', 'batteryStatus1', 'signal8', 'lightning_distance'} <= columns
    assert not (tmp_path / 'weewx.sdb').exists()


def test_station_columns_reads_the_database(egw, tmp_path):
    conf = config(tmp_path)
    with weewx.manager.open_manager_with_config(conf, 'wx_binding', initialize=True) as manager:
        manager.add_column('wh40_batt', 'REAL')
    assert 'wh40_batt' in egw.station_columns(conf)


def test_station_column_map(egw, tmp_path):
    conf = config(tmp_path)
    found = egw.station_column_map(conf)
    assert found['inTempBatteryStatus'] == ('wh25_batt', 'wn32_batt')
    assert found['outTempBatteryStatus'] == ('wh26_batt', 'wn32p_batt', 'wh69_batt')
    assert found['batteryStatus3'] == ('wn31_ch3_batt',) and found['signal3'] == ('wn31_ch3_sig',)
    assert found['lightning_distance'] == ('lightningdist',)
    assert found['rainBatteryStatus'] == ('wh40_batt',) and found['hailBatteryStatus'] == ('ws90_batt',)
    assert found['windBatteryStatus'] == ('ws80_batt', 'ws85_batt', 'ws90_batt', 'wh68_batt')
    assert set(found) == set(egw.STATION_COLUMNS)


def test_station_column_map_with_or_without_the_additions_database(egw, tmp_path):
    assert egw.station_column_map(config(tmp_path, enable=False)) == egw.station_column_map(config(tmp_path))
    conf = config(tmp_path)
    conf['EcowittGateway']['additions']['use_station_columns'] = 'False'
    assert egw.station_column_map(conf) == {}


def test_station_column_map_leaves_columns_and_fields_the_user_mapped(egw, tmp_path):
    conf = config(tmp_path)
    conf['EcowittGateway']['field_map_extensions'] = {'batteryStatus1': 'wh57.battery',
                                                      'wn31_ch2_batt': 'wn31.ch2.battery'}
    found = egw.station_column_map(conf)
    assert 'batteryStatus1' not in found
    assert 'batteryStatus2' not in found


def test_station_column_map_leaves_fields_the_database_has(egw, tmp_path):
    conf = config(tmp_path)
    found = egw.station_column_map(conf, columns=egw.station_columns(conf) | {'wh25_batt'})
    assert found['inTempBatteryStatus'] == ('wn32_batt',)
    found = egw.station_column_map(conf, columns=egw.station_columns(conf) - {'signal4'})
    assert 'signal4' not in found


def test_mapped_data_uses_the_station_columns(columns_map, tmp_path):
    egw = columns_map
    egw.use_station_columns(config(tmp_path))
    mapper = egw.HttpMapper(driver_debug=egw.DebugOptions(), quiet=True)
    data = mapper.map_data({'wh25.battery': 0, 'wh69.battery': 1, 'wn31.ch1.battery': 0, 'wn31.ch1.signal': 4,
                            'wn31.ch5.battery': 1, 'lightning.distance': 12, 'wh40.battery': 5})
    # battery levels keep their field and add the 0 (OK) / 1 (low) status
    assert data == {'inTempBatteryStatus': 0, 'outTempBatteryStatus': 1, 'batteryStatus1': 0,
                    'signal1': 4, 'batteryStatus5': 1, 'lightning_distance': 12, 'wh40_batt': 5,
                    'rainBatteryStatus': 0}
    data = mapper.map_data({'ws90.battery': 1, 'wh40.battery': 6})
    assert data == {'ws90_batt': 1, 'hailBatteryStatus': 1, 'windBatteryStatus': 1, 'wh40_batt': 6,
                    'rainBatteryStatus': 0}
    # nothing renamed when turned off
    conf = config(tmp_path)
    conf['EcowittGateway']['additions']['use_station_columns'] = 'False'
    egw.use_station_columns(conf)
    assert mapper.map_data({'wh25.battery': 0}) == {'wh25_batt': 0}


@pytest.mark.parametrize('level, status', [(0, 1), (1, 1), (2, 0), (5, 0), (6, 0), (9, None), (None, None)])
def test_battery_status(egw, level, status):
    assert egw.battery_status(level) == status


def test_station_columns_keep_their_last_value(egw):
    extractors = egw.accumulator_extractors()
    for column in egw.STATION_COLUMNS:
        assert extractors[column] == 'last'


def test_station_columns_stay_out_of_the_additions_database(columns_map, tmp_path):
    egw = columns_map
    conf = config(tmp_path)
    egw.use_station_columns(conf)
    engine = Engine(conf)
    egw.EcowittAdditions(engine, conf)
    mapper = egw.HttpMapper(driver_debug=egw.DebugOptions(), quiet=True)
    data = mapper.map_data({'wh25.battery': 1, 'wn31.ch2.signal': 3, 'wh40.battery': 4})
    engine.archive(record(1_700_000_100, **data))
    engine.db_binder.close()
    assert rows(tmp_path / 'weewx.sdb', 'SELECT inTempBatteryStatus, signal2, rainBatteryStatus FROM archive') \
        == [(1.0, 3.0, 0.0)]
    db = tmp_path / 'ecowitt_additions.sdb'
    assert [c[1] for c in rows(db, 'PRAGMA table_info(archive)')] == ['dateTime', 'usUnits', 'interval',
                                                                      'wh40_batt']


# ---- skins find the additions database's fields by name ----

def test_skins_read_additions_fields_by_name(egw, tmp_path):
    import weewx.xtypes
    from weeutil.weeutil import TimeSpan
    conf = config(tmp_path)
    engine = Engine(conf)
    service = egw.EcowittAdditions(engine, conf)
    try:
        assert service.xtype in weewx.xtypes.xtypes
        engine.archive(record(1_700_000_100, wh40_batt=4, co2_temp=20.5))
        engine.archive(record(1_700_000_400, wh40_batt=3, co2_temp=21.5))
        main = engine.db_binder.get_manager('wx_binding')
        # as $current does: not in the station's record, so the xtypes are asked
        rec = main.getRecord(1_700_000_400)
        assert 'wh40_batt' not in rec
        assert weewx.xtypes.get_scalar('wh40_batt', rec, main)[0] == 3
        span = TimeSpan(1_700_000_000, 1_700_000_400)
        assert weewx.xtypes.get_aggregate('co2_temp', span, 'max', main)[0] == 21.5
        assert weewx.xtypes.has_data('co2_temp', span, main)
        assert not weewx.xtypes.has_data('wh40_batt', TimeSpan(1_600_000_000, 1_600_000_400), main)
        times, _, values = weewx.xtypes.get_series('wh40_batt', span, main)
        assert values[0] == [4, 3]
        # unknown fields stay unknown
        with pytest.raises(weewx.UnknownType):
            service.xtype.get_scalar('nosuchfield', rec, main)
    finally:
        service.shutDown()
        service.xtype.shut_down()
        engine.db_binder.close()
    assert service.xtype not in weewx.xtypes.xtypes
