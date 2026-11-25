"""Faults in ecowitt_http.py 0.1.0a28's API parser and live-data handling."""

import datetime
import logging
import os
import time

import pytest


@pytest.fixture
def parser(egw):
    return egw.EcowittHttpParser()


def test_unreadable_device_time(parser):
    """An unreadable 'time' set rain_reset_day to None instead of time."""
    parsed = parser.parse_get_device_info({'time': 'abc', 'rstRainDay': '0'})
    assert parsed['time'] is None and 'rain_reset_day' not in parsed


def test_unreadable_leak_sensor_voltage(parser):
    """A WH55 voltage that couldn't be read (ParseError) stopped all live data being parsed."""
    data = parser.parse_get_livedata_info({'ch_leak': [{'channel': '1', 'battery': '4', 'status': 'Normal',
                                                        'voltage': '--'}],
                                           'wh25': [{'intemp': '20.0', 'unit': 'C'}]})
    assert data['ch_leak.1.voltage'] is None and data['ch_leak.1.status'] == 0
    assert data['wh25.intemp'] is not None


def test_livedata_group_error_is_logged(parser, monkeypatch, caplog):
    """The message had two placeholders and one argument, a logging error instead of the message."""
    def broken(response):
        raise egw_processor_error('bad array')
    egw_processor_error = type(parser).__init__.__globals__['ProcessorError']
    monkeypatch.setattr(parser, 'process_wh25_array', broken)
    caplog.set_level(logging.INFO)
    parser.parse_get_livedata_info({'wh25': [{}]})
    assert "Error processing livedata group 'wh25': bad array" in caplog.messages


def test_lightning_placeholders_before_first_strike(parser, caplog):
    """Until the WH57 detects a strike the gateway gives '--.-' for the distance; each poll logged
    'Error processing distance: Could not convert '--.-' to a float' (2.1.6 and earlier)."""
    caplog.set_level(logging.DEBUG)
    data = parser.parse_get_livedata_info({'lightning': [{'distance': '--.-', 'date': '',
                                                          'timestamp': '--/--/---- --:--:--',
                                                          'count': '0', 'battery': '5'}]})
    assert data['lightning.count'] == 0
    assert data['lightning.distance'] is None and data['lightning.timestamp'] is None
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]


def test_lightning_distance_after_a_strike(parser):
    data = parser.parse_get_livedata_info({'lightning': [{'distance': '14 km', 'count': '2',
                                                          'timestamp': '11/05/2026 14:12:01'}]})
    assert data['lightning.distance'] == pytest.approx(14.0) and data['lightning.count'] == 2
    assert isinstance(data['lightning.timestamp'], int)


# ---------------------------------------------- lightning time (issue #21)

ISSUE_21 = {'lightning': [{'distance': '20 km', 'date': '2026-08-17T19:27:30', 'timestamp': '08/17/2026 19:27:30',
                           'count': '0', 'battery': '1'}]}
TCP_TIME = 1786991250          # the TCP API's 0x61: 2026-08-17 18:27:30 UTC, 20:27:30 CEST
NOV_23 = 1795420000            # 2026-11-23, CET


@pytest.fixture
def rome():
    old = os.environ.get('TZ')
    os.environ['TZ'] = 'Europe/Rome'
    time.tzset()
    yield
    if old is None:
        os.environ.pop('TZ')
    else:
        os.environ['TZ'] = old
    time.tzset()


def test_lightning_time_read_with_the_offset_in_effect_now(egw, rome):
    """The HTTP string for a summer strike, read in winter, is in CET; time.mktime() read it as CEST,
    an hour early (1786987650)."""
    dt = datetime.datetime(2026, 8, 17, 19, 27, 30)
    assert egw.device_time_to_ts(dt, now=NOV_23) == TCP_TIME
    assert int(time.mktime(dt.timetuple())) == TCP_TIME - 3600


def _tcp_payload(ts):
    """A TCP API live data payload: indoor temperature, lightning distance, time and count."""
    return (b'\x01\x00\xd7' + b'\x60\x14' + b'\x61' + ts.to_bytes(4, 'big') + b'\x62' + (0).to_bytes(4, 'big'))


def test_tcp_live_item(egw):
    assert egw.tcp_live_item(_tcp_payload(TCP_TIME), 0x61) == TCP_TIME.to_bytes(4, 'big')
    assert egw.tcp_live_item(b'\x01\x00\xd7', 0x61) is None
    assert egw.tcp_live_item(b'\x01\x00\xd7\xee\x00', 0x61) is None              # an unknown item stops the walk
    assert egw.tcp_live_item(b'\x61\x00\x01', 0x61) is None                      # truncated


def _device(egw, monkeypatch, answers):
    """An EcowittDevice whose TCP API gives each of answers in turn (bytes, or an exception to raise)."""
    dev = egw.EcowittDevice('127.0.0.1')
    calls = []

    def call(cmd):
        calls.append(cmd)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(dev.tcp_api, 'call', call)
    return dev, calls


def _poll(egw, dev, live):
    data = dev.parser.parse_get_livedata_info(live)
    dev.fix_lightning_time(live, data)
    return data['lightning.timestamp']


def test_lightning_time_from_the_tcp_api(egw, monkeypatch):
    """Whatever the host's time zone, the TCP API's UTC time is used; it is asked once per strike."""
    dev, calls = _device(egw, monkeypatch, [_tcp_payload(TCP_TIME), _tcp_payload(TCP_TIME + 7200)])
    http_ts = egw.device_time_to_ts(datetime.datetime(2026, 8, 17, 19, 27, 30))
    monkeypatch.setattr(egw, 'device_time_to_ts', lambda dt, now=None: http_ts - 3600)   # the hour out
    assert [_poll(egw, dev, ISSUE_21) for _ in range(3)] == [TCP_TIME] * 3
    assert calls == [0x27]
    later = {'lightning': [{**ISSUE_21['lightning'][0], 'timestamp': '08/17/2026 21:27:30'}]}
    assert _poll(egw, dev, later) == TCP_TIME + 7200 and calls == [0x27, 0x27]


def test_lightning_time_without_the_tcp_api(egw, monkeypatch, caplog):
    """No TCP API: the HTTP string's time, logged once, tried again after TCP_VOLTAGE_RETRY."""
    caplog.set_level(logging.INFO)
    dev, calls = _device(egw, monkeypatch, [egw.DeviceIOError('refused'), _tcp_payload(TCP_TIME)])
    http_ts = egw.device_time_to_ts(datetime.datetime(2026, 8, 17, 19, 27, 30))
    assert [_poll(egw, dev, ISSUE_21) for _ in range(3)] == [http_ts] * 3
    assert calls == [0x27] and caplog.text.count('did not answer') == 1
    dev._lightning_tcp_next = 0                                     # the retry time has passed
    assert _poll(egw, dev, ISSUE_21) == TCP_TIME and calls == [0x27, 0x27]


def test_lightning_time_kept_when_the_tcp_api_disagrees(egw, monkeypatch):
    """A TCP time that isn't the HTTP time give or take whole quarter hours (a strike between the two
    requests), or no 0x61 item, leaves the HTTP time, without asking again for the same strike."""
    http_ts = egw.device_time_to_ts(datetime.datetime(2026, 8, 17, 19, 27, 30))
    for payload in (_tcp_payload(http_ts + 95), b'\x01\x00\xd7', _tcp_payload(0xFFFFFFFF)):
        dev, calls = _device(egw, monkeypatch, [payload])
        assert [_poll(egw, dev, ISSUE_21) for _ in range(2)] == [http_ts] * 2 and calls == [0x27]


def test_no_tcp_request_without_a_strike(egw, monkeypatch):
    dev, calls = _device(egw, monkeypatch, [])
    live = {'lightning': [{'distance': '--.-', 'date': '', 'timestamp': '--/--/---- --:--:--', 'count': '0'}]}
    assert _poll(egw, dev, live) is None and calls == []
