"""
Save a LandIS web page as raw HTML and cleaned Markdown.

www.landis.org.uk pages are fetched over HTTP. The main text is the
Dreamweaver ``pageContent`` region, so the navigation, footer, cookie
notice, About Us block, and QR code are left out.

portal.landis.org.uk pages are rendered with Playwright. FAQ accordions
are expanded before the text is taken.

NATMAP, SERIES, HORIZON, and NSI pages drop any
"How much does this product cost?" section. That removal is recorded
by the caller in meta.json.

**Created:** 30-10-2026 (UK style).
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

USER_AGENT = "LandIS-corpus/0.1 (Cranfield University; knowledge-base capture)"
REQUEST_GAP_SECONDS = 2.0

_PRICE_HEADING = re.compile(r"how much does this product cost\??", re.IGNORECASE)
_DROP_HEADINGS = {"about us", "quick links"}
_COOKIE = re.compile(r"this site uses cookies?", re.IGNORECASE)
_SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "svg", "form"}

# URL stems whose product pages carry a lease-price section.
_PRICE_STEMS = {
    "natmap.cfm",
    "nmvector.cfm",
    "nm1000.cfm",
    "nm2000.cfm",
    "nm5000.cfm",
    "nmcarbon.cfm",
    "nmhost.cfm",
    "nmsoilscapes.cfm",
    "nmsubsoiltexture.cfm",
    "nmtopsoiltexture.cfm",
    "nmwetness.cfm",
    "nmap.cfm",
    "nmwrb.cfm",
    "series.cfm",
    "ssinfo.cfm",
    "sshydrology.cfm",
    "ssagronomy.cfm",
    "sspesticides.cfm",
    "ssleacs.cfm",
    "horfundamentals.cfm",
    "horhydraulics.cfm",
    "nsi.cfm",
    "nsisite.cfm",
    "nsiprofile.cfm",
    "nsitopsoil.cfm",
    "nsitextures.cfm",
    "nsifeatures.cfm",
    "nsimagnetic.cfm",
}


class RequestPace:
    """Leave at least two seconds between outbound requests."""

    def __init__(self, gap: float = REQUEST_GAP_SECONDS) -> None:
        self.gap = gap
        self._last = 0.0

    def wait(self) -> None:
        if self._last:
            remaining = self.gap - (time.monotonic() - self._last)
            if remaining > 0:
                time.sleep(remaining)
        self._last = time.monotonic()


def should_strip_prices(url: str) -> bool:
    """True for NATMAP, SERIES, HORIZON, and NSI product pages."""
    name = Path(unquote(urlparse(url).path)).name.lower()
    return name in _PRICE_STEMS


def _collapse(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


class _MainTextParser(HTMLParser):
    """Turn a fragment into headings, paragraphs, and list items."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple] = []
        self._skip = 0
        self._buf: list[str] = []
        self._heading = 0
        self._in_list_item = False
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush_paragraph()
            self._heading = int(tag[1])
        elif tag == "li":
            self._flush_paragraph()
            self._in_list_item = True
        elif tag == "br":
            self._buf.append("\n")
        elif tag == "img":
            return
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a" and self._href.startswith("http"):
            self._buf.append(f" ({self._href})")
            self._href = ""
            return
        if self._skip:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._heading:
            text = _collapse("".join(self._buf))
            self._buf = []
            if text:
                self.blocks.append(("h", self._heading, text))
            self._heading = 0
        elif tag == "li" and self._in_list_item:
            text = _collapse("".join(self._buf))
            self._buf = []
            self._in_list_item = False
            if text:
                self.blocks.append(("li", text))
        elif tag in {"p", "div"} and not self._heading and not self._in_list_item:
            self._flush_paragraph()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush_paragraph()

    def _flush_paragraph(self) -> None:
        text = _collapse("".join(self._buf))
        self._buf = []
        if text and not self._heading and not self._in_list_item:
            self.blocks.append(("p", text))


def page_content_html(html: str) -> str:
    """The editable page body when the LandIS template marks one."""
    match = re.search(
        r'<!-- InstanceBeginEditable name="pageContent" -->(.*)<!-- InstanceEndEditable -->',
        html,
        re.DOTALL,
    )
    if match:
        return match.group(1)
    return html


def _drop_sections(blocks: list[tuple], predicate, removed_label: str) -> tuple[list[tuple], list[str]]:
    """Drop a heading and the blocks under it until the next heading of the same rank."""
    kept: list[tuple] = []
    removed: list[str] = []
    skipping = False
    level = 0
    for block in blocks:
        if block[0] == "h" and predicate(block[2]):
            skipping = True
            level = block[1]
            removed.append(removed_label)
            continue
        if skipping and block[0] == "h" and block[1] <= level:
            skipping = False
        if not skipping:
            kept.append(block)
    return kept, removed


def clean_blocks(html: str, *, strip_prices: bool) -> tuple[list[tuple], list[str]]:
    """Main-text blocks, plus a list of sections that were removed."""
    fragment = page_content_html(html)
    parser = _MainTextParser()
    parser.feed(fragment)
    parser.close()
    blocks = [
        block
        for block in parser.blocks
        if not (block[0] in {"p", "li"} and _COOKIE.search(block[-1]))
    ]
    removed: list[str] = []
    blocks, about = _drop_sections(
        blocks,
        lambda text: text.strip().lower() in _DROP_HEADINGS,
        "About Us",
    )
    removed.extend(about)
    if strip_prices:
        blocks, prices = _drop_sections(
            blocks,
            lambda text: _PRICE_HEADING.search(text) is not None,
            "How much does this product cost?",
        )
        removed.extend(prices)
    return blocks, removed


def blocks_to_markdown(blocks: list[tuple]) -> str:
    lines: list[str] = []
    for block in blocks:
        if block[0] == "h":
            lines.append(f"{'#' * block[1]} {block[2]}")
            lines.append("")
        elif block[0] == "li":
            lines.append(f"- {block[1]}")
        elif block[1]:
            if lines and lines[-1].startswith("- "):
                lines.append("")
            lines.append(block[1])
            lines.append("")
    text = "\n".join(lines).strip()
    return text + ("\n" if text else "")


def html_to_clean_markdown(html: str, *, strip_prices: bool) -> tuple[str, list[str]]:
    """Return cleaned Markdown and the names of sections removed from it."""
    blocks, removed = clean_blocks(html, strip_prices=strip_prices)
    return blocks_to_markdown(blocks), removed


def pdf_name_from_response(url: str, disposition: str, fallback: str) -> str:
    """Prefer the server's filename. Fall back when the URL has none."""
    match = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", disposition, re.IGNORECASE)
    if not match:
        match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
    if match:
        name = unquote(match.group(1).strip().strip('"'))
    else:
        name = Path(unquote(urlparse(url).path)).name
    name = Path(name).name
    if not name.lower().endswith(".pdf"):
        name = fallback
    name = re.sub(r"[^\w.\-]+", "_", name).strip("._")
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf" if name else fallback
    return name or fallback


def download_bytes(url: str, timeout: int = 120) -> tuple[bytes, str, str]:
    """GET a file. Returns body, Content-Disposition, and Content-Type."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        disposition = response.headers.get("Content-Disposition") or ""
        content_type = response.headers.get("Content-Type") or ""
    return data, disposition, content_type


def fetch_html(url: str, timeout: int = 60) -> str:
    """GET a page. The caller is responsible for the two-second gap."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def render_portal(url: str, timeout_ms: int = 90000) -> tuple[str, str]:
    """Open a portal page, expand FAQ accordions, and return HTML plus visible text."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            page.evaluate(
                """() => {
                    document.querySelectorAll('calcite-accordion-item').forEach((item) => {
                        item.expanded = true;
                        item.setAttribute('expanded', '');
                    });
                    document.querySelectorAll('[aria-expanded="false"]').forEach((item) => {
                        try { item.click(); } catch (error) { /* closed control */ }
                    });
                }"""
            )
            page.wait_for_timeout(1500)
            return page.content(), page.locator("body").inner_text()
        finally:
            browser.close()


_PORTAL_NAV = {
    "skip navigation",
    "sign in",
    "home",
    "landis data",
    "case studies",
    "history of the soils data",
    "soils at cranfield",
    "user survey",
    "support",
}
_PORTAL_FOOTER = {
    "end user licence agreement",
    "privacy policy",
    "foi",
    "built with",
    "arcgis hub",
    "explore feeds",
    "landis",
    "cranfield university",
}


def _portal_footer_start(lines: list[str]) -> int:
    """Index where the portal footer begins, or the length of the list."""
    end = len(lines)
    while end > 0:
        lowered = lines[end - 1].lower()
        if lowered in _PORTAL_FOOTER or lowered.startswith("©"):
            end -= 1
            continue
        break
    return end


def rendered_text_to_markdown(text: str, *, strip_prices: bool) -> tuple[str, list[str]]:
    """Clean visible portal text. Accordions are already expanded."""
    removed: list[str] = []
    lines = [_collapse(line) for line in text.splitlines()]
    lines = [line for line in lines if line and not _COOKIE.search(line)]
    while lines and lines[0].lower() in _PORTAL_NAV:
        lines.pop(0)
    lines = lines[: _portal_footer_start(lines)]
    kept: list[str] = []
    skipping = False
    skip_level_label = ""
    for line in lines:
        if _PRICE_HEADING.search(line) and strip_prices:
            skipping = True
            skip_level_label = "How much does this product cost?"
            if skip_level_label not in removed:
                removed.append(skip_level_label)
            continue
        if line.lower() in _DROP_HEADINGS:
            skipping = True
            if "About Us" not in removed:
                removed.append("About Us")
            continue
        if skipping and line[:1].isupper() and len(line) < 80 and line.endswith(":"):
            skipping = False
        if not skipping:
            kept.append(line)
    markdown = "\n\n".join(kept).strip()
    return (markdown + "\n") if markdown else "", removed


def write_capture(
    dest: Path,
    *,
    raw_html: str,
    clean_md: str,
    meta: dict,
) -> None:
    """Write raw.html, clean.md, and meta.json into one document folder."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "raw.html").write_text(raw_html, encoding="utf-8")
    (dest / "clean.md").write_text(clean_md, encoding="utf-8")
    (dest / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def capture_url(url: str, dest: Path, *, doc_id: str, title: str, captured_on: str) -> dict:
    """Fetch one page and write the capture folder. Returns the meta record."""
    portal = "portal.landis.org.uk" in urlparse(url).netloc
    strip_prices = should_strip_prices(url)
    if portal:
        raw_html, visible = render_portal(url)
        clean_md, removed = rendered_text_to_markdown(visible, strip_prices=strip_prices)
    else:
        raw_html = fetch_html(url)
        clean_md, removed = html_to_clean_markdown(raw_html, strip_prices=strip_prices)
    if not clean_md.strip():
        raise RuntimeError(f"{doc_id}: cleaned text was empty ({url})")
    meta = {
        "doc_id": doc_id,
        "title": title,
        "source_url": url,
        "captured_on": captured_on,
        "method": "playwright" if portal else "http",
        "removed": removed,
    }
    write_capture(dest, raw_html=raw_html, clean_md=clean_md, meta=meta)
    return meta
