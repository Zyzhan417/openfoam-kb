"""index FTS policy: which chunks enter chunks_fts (comment-only blocks stay out)."""

from foamkb import index
from foamkb.config import build_config


# --- FTS indexing policy: what enters chunks_fts ------------------------------

SEPARATOR_FILE = (
    "\\*---------------------------------------------*/\n"
    "// * * * * * * * * * * * * * * * * * * * * * * * *\n"
    "\n"
    "class wallFn\n"
    "{\n"
    "    void correct();\n"
    "};\n"
)


def test_comment_only_chunks_never_enter_fts(tmp_path, corpus):
    """Comment separators must not become search candidates.

    A separator block carries no code and no symbol, yet its path matches an identifier
    query while bm25's short-document bias ranks it above the definition. Such blocks stay
    in `chunks` (so `files`/coverage stay exact) but never enter `chunks_fts`.
    """
    root = corpus({"wallFn/wallFn.C": SEPARATOR_FILE})
    cfg = build_config(root, tmp_path / "kb.sqlite", ("**/*.C",))
    index.build(cfg)
    reader = index.open_reader(cfg.index_path)

    ids = reader.search_lexical('"wallFn"', 10)
    assert ids, "the declaration must stay searchable"
    texts = [reader.get_chunk(chunk_id)["text"] for chunk_id in ids]
    assert not any("****" in text or text.lstrip().startswith("\\*") for text in texts)
    assert any("class wallFn" in text for text in texts)


def test_a_short_chunk_with_a_symbol_is_still_indexed(tmp_path, corpus):
    """A symbol is worth indexing on its own, however small the body is."""
    root = corpus({"a.C": "void shorty()\n{\n}\n"})
    cfg = build_config(root, tmp_path / "kb.sqlite", ("**/*.C",))
    index.build(cfg)
    reader = index.open_reader(cfg.index_path)

    ids = reader.search_lexical('"shorty"', 10)
    assert ids
    assert reader.get_chunk(ids[0])["symbol"] == "shorty"


def test_a_path_only_match_ranks_below_a_text_match_of_equal_length(tmp_path, corpus):
    """The path column is a file-level fact, so it must never outvote the body.

    Two files with the same token budget and no symbol: A matches `kOmegaSST` only through
    a directory name, B only through its body. Equal length cancels bm25's length
    normalisation, isolating the column weight. With path weighted 1.5 the content-free
    block won (that is the defect); it must stay below the text match.
    """
    pad = "    scalar alpha = 1.0;\n" * 6
    root = corpus({
        "plugins/kOmegaSST/a.C": "void setupA()\n{\n" + pad + "}\n",
        "plugins/neutral/b.C": 'void setupB()\n{\n    const word kOmegaSST = "x";\n' + pad + "}\n",
    })
    cfg = build_config(root, tmp_path / "kb.sqlite", ("**/*.C",))
    index.build(cfg)
    reader = index.open_reader(cfg.index_path)

    ids = reader.search_lexical('"kOmegaSST"', 5)
    top = reader.get_chunk(ids[0])
    assert "kOmegaSST" in top["text"], (
        f"a path-only match outranked the body: {top['path']} / {top['text'][:40]!r}")
