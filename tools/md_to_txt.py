#!/usr/bin/env python3
"""Make a plain-text copy (NAME.txt) of each Markdown document (NAME.md) in the repository.

    python3 tools/md_to_txt.py            # every .md file in the repository root
    python3 tools/md_to_txt.py README.md  # just these

Standard library only. Handles the Markdown these documents use: headings, paragraphs, lists,
tables, code blocks, block quotes, links, inline code and bold. Run it again after changing a .md
file; the .txt files are not meant to be edited by hand.
"""

import os
import re
import sys
import textwrap

WIDTH = 100
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def inline(text):
    """Markdown inline markup to plain text."""
    code = []                                                                  # protect `code` first

    def keep(m):
        code.append(m.group(1))
        return f'\0{len(code) - 1}\0'
    text = re.sub(r'`([^`]+)`', keep, text)
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)                       # images: alt text

    def link(m):
        label, url = m.group(1), m.group(2)
        if url.startswith('#') or url.endswith('.md') and '://' not in url:
            return label                                                       # a link within these docs
        plain = re.sub('\0(\\d+)\0', lambda c: code[int(c.group(1))], label)
        if plain == url or url == f'mailto:{plain}':
            return url
        return f'{label} ({url})'
    text = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', link, text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)                                # bold
    text = re.sub(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])', r'\1', text)   # italics
    text = text.replace('\\|', '|').replace('\\*', '*').replace('\\_', '_')
    text = re.sub('\0(\\d+)\0', lambda m: code[int(m.group(1))].replace('\\|', '|'), text)
    return text


def wrap(text, indent='', first=None):
    first = indent if first is None else first
    return textwrap.fill(text, WIDTH, initial_indent=first, subsequent_indent=indent,
                         break_long_words=False, break_on_hyphens=False) or first.rstrip()


def split_row(line):
    line = line.strip().strip('|')
    cells, cell, i = [], '', 0
    while i < len(line):
        if line[i] == '\\' and i + 1 < len(line) and line[i + 1] == '|':
            cell += '\\|'
            i += 2
            continue
        if line[i] == '|':
            cells.append(cell)
            cell = ''
        else:
            cell += line[i]
        i += 1
    cells.append(cell)
    return [inline(c.strip()) for c in cells]


def table(lines):
    rows = [split_row(l) for l in lines if not re.match(r'^\s*\|?\s*:?-{2,}', l)]
    header, body = rows[0], rows[1:]
    ncol = len(header)
    rows = [r + [''] * (ncol - len(r)) for r in [header] + body]
    widths = [max(len(r[c]) for r in rows) for c in range(ncol)]
    if sum(widths) + 2 * (ncol - 1) <= WIDTH:
        out = ['  '.join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows[:1]]
        out.append('  '.join('-' * w for w in widths))
        out += ['  '.join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows[1:]]
        return out
    # too wide for columns: one block per row, the first column as its title
    out = []
    for r in rows[1:]:
        out.append(wrap(r[0]) if r[0] else '-')
        for n, (name, value) in enumerate(zip(header[1:], r[1:]), 2):
            if value:   # the last column (usually a description) needs no label
                text = value if n == ncol else f'{name}: {value}'
                out.append(wrap(text, '        ' if n < ncol else '    ', '    '))
        out.append('')
    return out[:-1]


def convert(md):
    lines = md.splitlines()
    out, para, i = [], [], 0
    indent = ['']

    def flush():
        if para:
            out.append(wrap(inline(' '.join(para)), indent[0]))
            out.append('')
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith('```'):                                         # code block
            flush()
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                out.append(('    ' + lines[i]).rstrip())
                i += 1
            out.append('')
            i += 1
            continue
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:                                                                  # heading
            flush()
            level, text = len(m.group(1)), inline(m.group(2).strip())
            if out and out[-1] != '':
                out.append('')
            if level == 1:
                out += [text.upper(), '=' * len(text)]
            elif level == 2:
                out += ['', text, '=' * len(text)]
            elif level == 3:
                out += [text, '-' * len(text)]
            else:
                out += [text]
            out.append('')
            i += 1
            continue
        if stripped in ('---', '***', '___'):                                  # rule
            flush()
            i += 1
            continue
        if stripped.startswith('|'):                                           # table
            flush()
            block = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                block.append(lines[i])
                i += 1
            out += table(block) + ['']
            continue
        if stripped.startswith('>'):                                           # block quote
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(wrap(inline(' '.join(quote)), '    | ', '    | '))
            out.append('')
            continue
        m = re.match(r'^(\s*)([-*]|\d+\.)\s+(.*)', line)
        if m:                                                                  # list item
            flush()
            depth = len(m.group(1)) // 2
            bullet = '-' if m.group(2) in '-*' else m.group(2)
            text = [m.group(3)]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r'^\s*([-*]|\d+\.)\s+', lines[i]) \
                    and lines[i].startswith(' ') and not lines[i].strip().startswith(('```', '|')):
                text.append(lines[i].strip())
                i += 1
            pad = '  ' * depth
            out.append(wrap(inline(' '.join(text)), pad + ' ' * (len(bullet) + 1), f'{pad}{bullet} '))
            if i < len(lines) and not lines[i].strip():
                out.append('')
            continue
        if not stripped:
            flush()
            if out and out[-1] != '':
                out.append('')
            i += 1
            continue
        if re.match(r'^\[[^\]]+\]:\s', stripped):                                # link reference definition
            i += 1
            continue
        if re.match(r'^<[^>]+>$', stripped):                                   # bare HTML
            i += 1
            continue
        if not para:
            indent[0] = ' ' * min(len(line) - len(line.lstrip()), 8)
        para.append(stripped)
        i += 1
    flush()
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip('\n')
    return text + '\n'


def main(names):
    names = names or sorted(n for n in os.listdir(ROOT) if n.endswith('.md'))
    for name in names:
        path = os.path.join(ROOT, name) if not os.path.isabs(name) else name
        with open(path, encoding='utf-8') as f:
            text = convert(f.read())
        target = path[:-3] + '.txt'
        with open(target, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'{os.path.relpath(target, ROOT)}: {text.count(chr(10))} lines')


if __name__ == '__main__':
    main(sys.argv[1:])
