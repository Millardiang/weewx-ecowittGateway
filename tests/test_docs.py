"""The plain-text copies of the documents (tools/md_to_txt.py) match the Markdown."""

import importlib.util
import os

import pytest

from conftest import ROOT

spec = importlib.util.spec_from_file_location('md_to_txt', os.path.join(ROOT, 'tools', 'md_to_txt.py'))
md_to_txt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md_to_txt)

DOCS = sorted(n for n in os.listdir(ROOT) if n.endswith('.md'))


def test_every_document_has_a_copy():
    assert set(DOCS) >= {'README.md', 'INSTALL.md', 'CHANGELOG.md', 'DEVELOPMENT.md'}
    for name in DOCS:
        assert os.path.isfile(os.path.join(ROOT, name[:-3] + '.txt')), name


@pytest.mark.parametrize('name', DOCS)
def test_copy_is_up_to_date(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        expected = md_to_txt.convert(f.read())
    with open(os.path.join(ROOT, name[:-3] + '.txt'), encoding='utf-8') as f:
        assert f.read() == expected, f"run 'python3 tools/md_to_txt.py' after changing {name}"
