"""Faults in ecowitt_http.py 0.1.0a28's API parser and live-data handling."""

import logging

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
