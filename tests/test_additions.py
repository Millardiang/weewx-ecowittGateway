"""EcowittAdditions: the fields the station's database has no column for, archived in a database of
their own (ecowitt_additions.sdb), with columns added as fields first have a value."""

import logging
import sqlite3
import types

import configobj
import pytest
import weewx
import weewx.manager


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
