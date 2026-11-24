from docs2mcp.ingest.chunk import chunk_page, estimate_tokens, plain_text
from docs2mcp.ingest.convert import Heading
from docs2mcp.ingest.models import Chunk

URL = "https://docs.example.com/api"


def chunks_of(md: str, headings: list[Heading] | None = None, **limits: int) -> list[Chunk]:
    options = {"max_tokens": 1500, "min_tokens": 0} | limits
    return chunk_page(
        md, url=URL, title="Example API", headings=headings or [], id_prefix="p01", **options
    )


def test_estimate_tokens_is_about_four_characters_each() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_plain_text_removes_link_and_code_markup() -> None:
    assert plain_text("[Latest rates](https://x.dev#latest)") == "Latest rates"
    assert plain_text("The `**GET**` endpoint") == "The GET endpoint"


def test_heading_paths_follow_the_heading_levels() -> None:
    md = (
        "Intro text.\n\n## Rates\n\nAbout rates.\n\n### Latest\n\nNewest.\n\n## Currencies\n\nList."
    )
    chunks = chunks_of(md)
    assert [c.heading_path for c in chunks] == [
        ["Example API"],
        ["Example API", "Rates"],
        ["Example API", "Rates", "Latest"],
        ["Example API", "Currencies"],
    ]
    assert [c.id for c in chunks] == ["p01-c001", "p01-c002", "p01-c003", "p01-c004"]
    assert chunks[2].markdown == "### Latest\n\nNewest."


def test_hash_lines_inside_code_are_not_headings() -> None:
    md = "## Setup\n\n```bash\n# install\npip install x\n```"
    chunks = chunks_of(md)
    assert len(chunks) == 1
    assert "# install" in chunks[0].markdown


def test_anchors_come_from_the_converter_even_for_linked_headings() -> None:
    md = "## [Latest rates](https://x.dev#latest)\n\nText.\n\n## No id\n\nText."
    headings = [Heading(2, "Latest rates", "latest"), Heading(2, "No id", None)]
    chunks = chunks_of(md, headings)
    assert [(c.heading_path[-1], c.anchor) for c in chunks] == [
        ("Latest rates", "latest"),
        ("No id", None),
    ]
    assert chunks[0].source_url == f"{URL}#latest"
    assert chunks[1].source_url == URL


def test_long_sections_split_between_blocks() -> None:
    paragraphs = [f"Paragraph {i}. " + "word " * 60 for i in range(6)]
    md = "## Guide\n\n" + "\n\n".join(paragraphs)
    chunks = chunks_of(md, max_tokens=200)
    assert len(chunks) > 1
    assert all(c.tokens <= 200 for c in chunks)
    assert all(c.heading_path == ["Example API", "Guide"] for c in chunks)
    joined = "\n\n".join(c.markdown for c in chunks)
    assert all(p.strip() in joined for p in paragraphs)


def test_long_tables_split_between_rows_and_keep_their_header_and_intro() -> None:
    rows = "\n".join(f"| param_{i} | string | Description of parameter {i} |" for i in range(40))
    md = (
        "## Parameters\n\nThe query accepts these parameters.\n\n"
        f"| Name | Type | Description |\n| --- | --- | --- |\n{rows}"
    )
    chunks = chunks_of(md, max_tokens=250)

    assert len(chunks) > 2
    assert "The query accepts these parameters." in chunks[0].markdown
    assert "| param_0 |" in chunks[0].markdown
    for chunk in chunks:
        table = chunk.markdown[chunk.markdown.index("| Name") :]
        assert table.startswith("| Name | Type | Description |\n| --- | --- | --- |\n| param_")
        assert chunk.tokens <= 250
    all_rows = "".join(c.markdown for c in chunks)
    assert all(f"| param_{i} |" in all_rows for i in range(40))


def test_oversized_code_blocks_are_never_cut() -> None:
    code = "```json\n" + "\n".join(f'  "field_{i}": {i},' for i in range(200)) + "\n```"
    md = f"## Example response\n\nShort intro.\n\n{code}\n\nAfter the code."
    chunks = chunks_of(md, max_tokens=200)
    assert any(c.markdown == code for c in chunks)
    assert all(c.markdown.count("```") % 2 == 0 for c in chunks)


def test_small_sections_join_the_next_section() -> None:
    md = "## Berries\n\n### Berry Firmnesses\n\n" + "Firmness details. " * 40
    chunks = chunks_of(md, min_tokens=80)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ["Example API", "Berries", "Berry Firmnesses"]
    assert chunks[0].markdown.startswith("## Berries\n\n### Berry Firmnesses")


def test_small_siblings_are_labelled_with_their_shared_parent() -> None:
    md = "## Rates\n\n### Latest\n\nShort.\n\n### Time Series\n\nAlso short."
    chunks = chunks_of(md, min_tokens=80)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ["Example API", "Rates"]


def test_a_small_last_section_joins_the_previous_one() -> None:
    md = "## Rates\n\n" + "Rate details. " * 40 + "\n\n## Notes\n\nShort."
    chunks = chunks_of(md, min_tokens=80)
    assert len(chunks) == 1
    assert chunks[0].markdown.endswith("## Notes\n\nShort.")


def test_small_sections_stay_separate_when_joining_would_exceed_the_limit() -> None:
    md = "## Small\n\nTiny.\n\n## Big\n\n" + "word " * 158  # 200 tokens on its own
    chunks = chunks_of(md, min_tokens=80, max_tokens=200)
    assert [c.heading_path[-1] for c in chunks] == ["Small", "Big"]


def test_a_lone_small_last_section_is_kept() -> None:
    chunks = chunks_of("## Only\n\nTiny.", min_tokens=80)
    assert [c.markdown for c in chunks] == ["## Only\n\nTiny."]


def test_chunking_is_deterministic() -> None:
    md = "## A\n\n" + "text " * 500 + "\n\n## B\n\nmore"
    assert chunks_of(md, max_tokens=300) == chunks_of(md, max_tokens=300)
