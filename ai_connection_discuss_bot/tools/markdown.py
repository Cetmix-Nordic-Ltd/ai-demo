# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Small Markdown to HTML converter for AI answers shown in Discuss.

Supports what chat answers use: paragraphs and line breaks, headings (shown
as bold lines), bold, italic, strikethrough, inline code, fenced code blocks,
bullet and numbered lists (nested by indentation), block quotes, pipe tables,
horizontal rules, links and bare URLs.

All text is HTML-escaped first; only the tags generated here are emitted, and
links accept http(s), mailto and same-site paths only. Odoo sanitizes the
message body again when it is stored.

Pure Python (only markupsafe), so it can be tested without Odoo.
"""

import re

from markupsafe import Markup, escape

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_LINK = re.compile(r"\[([^\]\n]+)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_BARE_URL = re.compile(r"(?<![\w/@])(https?://[^\s<>()\[\]]+[^\s<>()\[\].,;:!?'\"])")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")
_ITALIC_UNDERSCORE = re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")


def _safe_url(url):
    """Return ``url`` if it may become a link, else None."""
    lowered = url.strip().lower()
    if lowered.startswith(("http://", "https://", "mailto:")):
        return url.strip()
    # Same-site paths such as /mail/view?model=...; '//' would be another host
    if lowered.startswith("/") and not lowered.startswith("//"):
        return url.strip()
    return None


def _link_html(text, url):
    safe = _safe_url(url)
    if not safe:
        return str(escape(text))
    attributes = ""
    if not safe.startswith("/"):
        attributes = ' target="_blank" rel="noopener noreferrer"'
    return f'<a href="{escape(safe)}"{attributes}>{escape(text)}</a>'


def render_inline(text):
    """Inline Markdown of one line or paragraph to HTML (a str, escaped)."""
    stored = []

    def keep(html):
        stored.append(html)
        return f"\x00{len(stored) - 1}\x00"

    # Raw-text constructs first, so their content is not formatted further
    text = _CODE_SPAN.sub(
        lambda m: keep(f"<code>{escape(m.group(2).strip())}</code>"), text
    )
    text = _LINK.sub(lambda m: keep(_link_html(m.group(1), m.group(2))), text)
    text = _BARE_URL.sub(lambda m: keep(_link_html(m.group(1), m.group(1))), text)
    html = str(escape(text))
    html = _BOLD.sub(r"<strong>\2</strong>", html)
    html = _STRIKE.sub(r"<del>\1</del>", html)
    html = _ITALIC_STAR.sub(r"<em>\1</em>", html)
    html = _ITALIC_UNDERSCORE.sub(r"<em>\1</em>", html)
    return _PLACEHOLDER.sub(lambda m: stored[int(m.group(1))], html)


def _split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line)]


def _alignments(separator):
    result = []
    for cell in _split_row(separator):
        if cell.startswith(":") and cell.endswith(":"):
            result.append("center")
        elif cell.endswith(":"):
            result.append("right")
        else:
            result.append(None)
    return result


def _table_html(header, separator, rows):
    aligns = _alignments(separator)

    def cell(tag, text, index):
        align = aligns[index] if index < len(aligns) else None
        style = f' style="text-align: {align}"' if align else ""
        return f"<{tag}{style}>{render_inline(text)}</{tag}>"

    columns = len(_split_row(header))
    parts = ['<table class="table table-sm table-bordered"><thead><tr>']
    parts += [cell("th", text, i) for i, text in enumerate(_split_row(header))]
    parts.append("</tr></thead><tbody>")
    for row in rows:
        cells = (_split_row(row) + [""] * columns)[:columns]
        parts.append("<tr>")
        parts += [cell("td", text, i) for i, text in enumerate(cells)]
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _list_html(items):
    """``items``: (indent, ordered, text) tuples of consecutive list lines."""
    html = []
    stack = []  # (indent, tag) of the open lists
    for indent, ordered, text in items:
        tag = "ol" if ordered else "ul"
        while stack and indent < stack[-1][0]:
            html.append(f"</li></{stack.pop()[1]}>")
        if stack and indent == stack[-1][0] and tag != stack[-1][1]:
            html.append(f"</li></{stack.pop()[1]}>")
        if not stack or indent > stack[-1][0]:
            stack.append((indent, tag))
            html.append(f"<{tag}><li>")
        else:
            html.append("</li><li>")
        html.append(render_inline(text))
    while stack:
        html.append(f"</li></{stack.pop()[1]}>")
    return "".join(html)


def _fence_block(lines, i):
    fence = _FENCE.match(lines[i])
    if not fence:
        return None
    marker = fence.group(1)
    code = []
    i += 1
    while i < len(lines) and not lines[i].strip().startswith(marker):
        code.append(lines[i])
        i += 1
    # i + 1 skips the closing fence (or runs past the end of the text)
    return f"<pre><code>{escape(chr(10).join(code))}</code></pre>", i + 1


def _heading_block(lines, i):
    heading = _HEADING.match(lines[i])
    if not heading:
        return None
    return f"<p><strong>{render_inline(heading.group(2))}</strong></p>", i + 1


def _rule_block(lines, i):
    return ("<hr>", i + 1) if _RULE.match(lines[i]) else None


def _table_block(lines, i):
    if not (
        "|" in lines[i]
        and i + 1 < len(lines)
        and "-" in lines[i + 1]
        and _TABLE_SEPARATOR.match(lines[i + 1])
    ):
        return None
    header, separator = lines[i], lines[i + 1]
    i += 2
    rows = []
    while i < len(lines) and "|" in lines[i] and lines[i].strip():
        rows.append(lines[i])
        i += 1
    return _table_html(header, separator, rows), i


def _quote_block(lines, i):
    if not _QUOTE.match(lines[i]):
        return None
    quoted = []
    while i < len(lines) and _QUOTE.match(lines[i]):
        quoted.append(_QUOTE.match(lines[i]).group(1))
        i += 1
    return f"<blockquote>{markdown_to_html(chr(10).join(quoted))}</blockquote>", i


def _list_block(lines, i):
    if not _LIST_ITEM.match(lines[i]):
        return None
    items = []
    while i < len(lines):
        item = _LIST_ITEM.match(lines[i])
        if item:
            indent = len(item.group(1).expandtabs(4))
            ordered = item.group(2)[0].isdigit()
            items.append([indent, ordered, item.group(3)])
        elif lines[i].strip() and items and lines[i][:1].isspace():
            # Continuation line of the previous item
            items[-1][2] += " " + lines[i].strip()
        else:
            break
        i += 1
    return _list_html(items), i


# Tried in this order on each line that is not part of a paragraph yet
BLOCK_PARSERS = (
    _fence_block,
    _heading_block,
    _rule_block,
    _table_block,
    _quote_block,
    _list_block,
)


def _paragraph_html(paragraph):
    return "<p>" + "<br>".join(render_inline(line) for line in paragraph) + "</p>"


def markdown_to_html(text):
    """Convert Markdown ``text`` to safe HTML (a Markup)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    paragraph = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            if paragraph:
                blocks.append(_paragraph_html(paragraph))
                paragraph = []
            i += 1
            continue
        for parser in BLOCK_PARSERS:
            result = parser(lines, i)
            if result:
                if paragraph:
                    blocks.append(_paragraph_html(paragraph))
                    paragraph = []
                html, i = result
                blocks.append(html)
                break
        else:
            paragraph.append(lines[i].strip())
            i += 1
    if paragraph:
        blocks.append(_paragraph_html(paragraph))
    return Markup("".join(blocks))
