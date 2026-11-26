"""Tests for the LandIS web-page cleaner. No network."""

from __future__ import annotations

from pipeline.web_capture import html_to_clean_markdown, should_strip_prices

SAMPLE = """
<nav><a>Home</a></nav>
<!-- InstanceBeginEditable name="pageContent" -->
<div>
<h2>NATMAP</h2>
<p>The national map. See <a href="https://www.landis.org.uk/data/">the data page</a>.</p>
<h3>How much does this product cost?</h3>
<p>Lease from 500 pounds.</p>
<h3>NATMAP Products</h3>
<ul><li>NATMAP vector</li></ul>
</div>
<!-- InstanceEndEditable -->
<h5>About Us</h5>
<p>Cranfield Environment Centre</p>
<p><em>Cookies:</em> This site uses cookie. No personal information is gathered.</p>
<img alt="QR Code" src="qr.png">
"""


def test_main_text_drops_chrome_and_the_price_section():
    markdown, removed = html_to_clean_markdown(SAMPLE, strip_prices=True)
    assert "The national map." in markdown
    assert "https://www.landis.org.uk/data/" in markdown
    assert "NATMAP vector" in markdown
    assert "Lease from 500" not in markdown
    assert "About Us" not in markdown
    assert "Cookies" not in markdown
    assert "QR Code" not in markdown
    assert "How much does this product cost?" in removed


def test_price_pages_are_recognised_from_the_url():
    assert should_strip_prices("https://www.landis.org.uk/data/nmvector.cfm")
    assert should_strip_prices("https://www.landis.org.uk/data/series.cfm")
    assert should_strip_prices("https://www.landis.org.uk/data/nsi.cfm")
    assert not should_strip_prices("https://www.landis.org.uk/overview/index.cfm")
