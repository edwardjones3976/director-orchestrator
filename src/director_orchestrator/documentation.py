"""Read canonical HTML documentation without executing browser content.

HTML is the stored authority. The structured text below is only an in-memory
projection for documentation contracts: headings, list membership, table cells,
inline emphasis and code retain their boundaries. Required Markdown entrypoints
and machine-readable files are returned literally. No HTML or Markdown is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import re


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list["_Element | str"] = field(default_factory=list)


class _Parser(HTMLParser):
    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = _Element("document")
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        element = _Element(tag, dict(attrs))
        self.stack[-1].children.append(element)
        if tag not in self._VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Element(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def handle_comment(self, data):
        self.stack[-1].children.append(_Element("comment", children=[data]))


def _elements(element: _Element, tag: str):
    if element.tag == tag:
        yield element
    for child in element.children:
        if isinstance(child, _Element):
            yield from _elements(child, tag)


def _raw_text(element: _Element) -> str:
    return "".join(child if isinstance(child, str) else _raw_text(child)
                   for child in element.children if not isinstance(child, _Element) or child.tag != "comment")


def _render(element: _Element, *, structured: bool) -> str:
    tag = element.tag
    # Page furniture must never supply missing policy evidence. CSS and scripts
    # are not prose; the reader neither executes nor imports their contents.
    if tag in {"script", "style", "nav", "header", "footer", "template"} or "hidden" in element.attrs:
        return ""
    if tag == "comment":
        value = _raw_text(element).strip()
        if value.startswith(("director-doc-history", "TEAM_CHARTER_MANIFEST")):
            return "\n\n<!-- " + value + " -->\n\n"
        return ""
    children = lambda: "".join(child if isinstance(child, str) else _render(child, structured=structured)
                              for child in element.children)
    if tag == "pre":
        code = next(_elements(element, "code"), element)
        body = _raw_text(code).strip("\n")
        if not structured:
            return "\n\n" + body + "\n\n"
        language = next((item[9:] for item in (code.attrs.get("class") or "").split()
                         if item.startswith("language-")), "")
        return "\n\n```" + language + "\n" + body + "\n```\n\n"
    if tag in {"ul", "ol"}:
        rows = []
        number = int(element.attrs.get("start") or "1") if tag == "ol" else 1
        for child in element.children:
            if not isinstance(child, _Element) or child.tag != "li":
                continue
            body = _render(child, structured=structured).strip()
            prefix = f"{number}. " if tag == "ol" else "- "
            rows.append(prefix + body.replace("\n", "\n  "))
            number += 1
        return "\n\n" + "\n".join(rows) + "\n\n"
    if tag == "table":
        rows = []
        for row in _elements(element, "tr"):
            cells = [" ".join(_render(cell, structured=structured).split())
                     for cell in row.children if isinstance(cell, _Element) and cell.tag in {"th", "td"}]
            rows.append("| " + " | ".join(cells) + " |")
        return "\n\n" + "\n".join(rows) + "\n\n"
    body = children()
    if re.fullmatch(r"h[1-6]", tag):
        prefix = "#" * int(tag[1]) + " " if structured else ""
        return "\n\n" + prefix + body.strip() + "\n\n"
    if tag in {"p", "div", "section", "article", "blockquote", "details", "summary", "dl", "dt", "dd"}:
        return "\n\n" + body.strip() + "\n\n"
    if tag in {"strong", "b"} and structured:
        return "**" + body + "**"
    if tag in {"em", "i"} and structured:
        return "*" + body + "*"
    if tag == "code" and structured:
        return "`" + body + "`"
    if tag == "a" and element.attrs.get("href"):
        href = element.attrs["href"]
        return f"[{body}]({href})" if structured else body
    if tag == "br":
        return "\n"
    if tag == "hr":
        return "\n\n"
    if tag == "img":
        return element.attrs.get("alt") or ""
    return body


def html_text(source: str, *, structured: bool = True, require_main: bool = True) -> str:
    """Extract the sole main document, preserving semantic content boundaries.

    Missing/duplicate main elements fail instead of allowing page navigation to
    satisfy a content assertion. Fragment mode is explicit, for isolated guards.
    """
    root = _Parser(source).root
    mains = list(_elements(root, "main"))
    if len(mains) != 1 and (require_main or mains):
        raise ValueError("HTML documentation must have exactly one main element")
    content = mains[0] if mains else root
    return "\n" + re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n",
                         _render(content, structured=structured)).strip() + "\n"


def read_document(path: Path, *, structured: bool = True) -> str:
    """Read HTML semantic content or a required literal non-HTML contract."""
    source = path.read_text(encoding="utf-8")
    return html_text(source, structured=structured) if path.suffix.lower() in {".html", ".htm"} else source


def embedded_json(source: str, marker_name: str) -> str:
    """Return exact decoded JSON code text inside one marked HTML manifest.

    No JSON reserialization or whitespace normalization is applied beyond the
    code block's surrounding whitespace, so canonical-byte comparisons remain
    meaningful. Duplicate/missing markers or code blocks are errors.
    """
    begin = f"<!-- {marker_name}_BEGIN -->"
    end = f"<!-- {marker_name}_END -->"
    if source.count(begin) != 1 or source.count(end) != 1:
        raise ValueError("manifest markers must each occur exactly once")
    first = source.index(begin) + len(begin)
    last = source.index(end)
    if last < first:
        raise ValueError("manifest markers are out of order")
    fragment = _Parser(source[first:last]).root
    blocks = [code for pre in _elements(fragment, "pre") for code in _elements(pre, "code")]
    if len(blocks) != 1 or "language-json" not in (blocks[0].attrs.get("class") or "").split():
        raise ValueError("manifest requires exactly one JSON code block")
    return _raw_text(blocks[0]).strip()
