"""The gateway API parser and the command-line tool produce what the snapshot recorded
(tests/snapshot.py: over 1,600 parser cases, each response variant in each unit system, with what is
logged, and 99 command-line runs)."""

import json

import snapshot


def test_parser_and_command_line_unchanged(tmp_path):
    conf = tmp_path / 'weewx.conf'
    conf.write_text('[Station]\n    station_type = EcowittGateway\n[EcowittGateway]\n'
                    '    driver = user.weewx-ecowittGateway\n')
    with open(snapshot.SNAPSHOT) as f:
        expected = json.load(f)
    got = snapshot.collect(str(conf))
    for part in ('parser', 'cli'):
        changed = sorted(k for k in expected[part] if got[part].get(k) != expected[part][k])
        assert not changed, f'{part}: {len(changed)} changed, eg {changed[:10]}'
        assert set(got[part]) == set(expected[part])
