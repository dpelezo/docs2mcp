import pytest

from docs2mcp.ingest.convert import main_content, strip_permalinks, to_markdown


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            "<body><nav>menu</nav><main><p>docs</p></main><footer>f</footer></body>",
            "<main><p>docs</p></main>",
        ),
        ("<body><article><p>a</p></article></body>", "<article><p>a</p></article>"),
        ('<body><div role="main"><p>r</p></div></body>', '<div role="main"><p>r</p></div>'),
        (
            "<main><main>inner</main>outer</main><p>after</p>",
            "<main><main>inner</main>outer</main>",
        ),
        ("<body><p>no main</p></body>", "<body><p>no main</p></body>"),
        ("<body><main><p>unclosed", "<main><p>unclosed"),
    ],
    ids=["main", "article", "role-main", "nested-main", "no-main", "unclosed"],
)
def test_main_content(html: str, expected: str) -> None:
    assert main_content(html) == expected


def test_main_content_handles_multiline_pages() -> None:
    html = "<html>\n<body>\n<header>x</header>\n<main>\n<h1>Docs</h1>\n</main>\n</body>\n</html>"
    assert main_content(html) == "<main>\n<h1>Docs</h1>\n</main>"


def test_to_markdown_keeps_docs_and_drops_page_furniture() -> None:
    html = """
    <header>Site header</header>
    <main>
      <nav><h2>Contents</h2><a href="#a">A</a></nav>
      <a href="#latest"><h2 id="latest">Latest rates</h2></a>
      <p>Call <mark>/v1/latest</mark> with a <a href="/v1/currencies">currency</a>.</p>
      <pre><code class="language-bash">curl https://api.example.com/v1/latest</code></pre>
      <button>Copy</button>
      <table><tr><th>Param</th><th>Type</th></tr><tr><td>base_currency</td><td>string</td></tr></table>
      <img src="chart.png" alt="chart">
    </main>
    <footer>Footer</footer>
    """
    result = to_markdown(html, base_url="https://docs.example.com/v1/")
    md = result.markdown

    assert "Call /v1/latest with a [currency](https://docs.example.com/v1/currencies)." in md
    assert "```bash\ncurl https://api.example.com/v1/latest\n```" in md
    assert "| base_currency | string |" in md  # underscores not escaped, compact table
    for furniture in ("Site header", "Contents", "Copy", "Footer", "chart.png", "=="):
        assert furniture not in md
    assert [(h.level, h.text, h.anchor) for h in result.headings] == [(2, "Latest rates", "latest")]


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        (
            "## Quick Start[\u200b](https://postcodes.io/docs/overview/#quick-start"
            ' "Direct link to Quick Start")',
            "## Quick Start",
        ),
        ("## Rates [¶](#rates)", "## Rates "),
        ("## Rates [#](https://x.dev/page#rates)", "## Rates "),
        ("## Rates[](#rates)", "## Rates"),
        ("See [the guide](https://x.dev/a#b).", "See [the guide](https://x.dev/a#b)."),
        ("See [#](https://x.dev/a) for more.", "See [#](https://x.dev/a) for more."),
        ("Zero\u200bwidth\ufeff", "Zerowidth"),
    ],
    ids=["docusaurus", "pilcrow", "hash", "empty", "real-link", "no-fragment", "invisible"],
)
def test_strip_permalinks(markdown: str, expected: str) -> None:
    assert strip_permalinks(markdown) == expected


def test_to_markdown_drops_permalinks_from_text_and_headings() -> None:
    html = (
        '<main><h2 id="quick-start">Quick Start'
        '<a href="#quick-start" class="hash-link">\u200b</a></h2>'
        "<p>Body</p></main>"
    )
    converted = to_markdown(html, "https://postcodes.io/docs/overview")
    assert "[" not in converted.markdown
    assert "\u200b" not in converted.markdown
    assert [h.text for h in converted.headings] == ["Quick Start"]
