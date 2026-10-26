# Agent protocol for the OpenFOAM retrieval layer

This page is for an agent that knows nothing about CFD. The tool is a **lexical index** over
OpenFOAM source: you give it words, it gives you code blocks. It does not translate concepts,
it does not paraphrase, and it does not know what you mean. Everything below is about turning
a human question into words that exist in the source, and about reading the answer honestly.

## 1. What one call gives you

`search` returns `hits`, each with exactly these fields:

- `ref` — opaque string; quote it back verbatim, never rebuild it by hand.
- `path` — file the block came from. **Weak relevance signal** (the index deliberately weights
  the path low); do not judge a hit by its directory name.
- `start_line` / `end_line` / `text_start_line` — 1-based, inclusive.
- `text` — the block, verbatim. May be truncated (see `truncated`).
- `symbol` — name of the code unit or the declaration the block carries, else `null`.
- `kind` — `class` | `function` | `text` | `filedoc` | `lines` | `macro`.
- `truncated` — `true` means `text` is only the first part of the block.
- `doc` — the file's description paragraph (`{start_line, end_line, text}`) for `class` and
  `function` hits when the file has one, else `null`. **Never truncated.**

`hits: []` is a normal answer. An error code is not a normal answer.

## 2. Making a query

The engine ANDs your words: every word you type must occur in a block, or you get nothing.
So the user's sentence is usually the wrong query.

1. **Do not send the user's question as-is.** Words like `how`, `is`, `the`, `are`, `from`,
   `to` are almost never in source blocks, and one of them is enough to zero the whole query.
2. **Reduce to the subject phrase.** Strip the question frame and keep the two or three
   content words that name the thing and the action. A question shaped `how are <A> done for
   <B>` becomes the query `<A> <B>`; a question shaped `what stops <A> when <B>` becomes the
   query `<A> <B>`. This page deliberately gives **no concrete vocabulary examples** — the
   point is the transformation, and worked examples would hand you words instead of teaching
   you to find them.
3. **Search identifiers when you have one.** Class/function/type names are indexed whole
   (`SomeCamelCaseName`) *and* split into words (`Some`, `Camel`, `Case`, `Name`). An exact
   identifier usually lands at rank 1–2. If the question does not contain an identifier, you
   do not have one yet — go to step 4.
4. **When you do not know the identifier, ask the index for vocabulary.** Query the concept's
   noun phrase, then read the `symbol` values that come back. Those are the codebase's own
   names for things. Re-query with one of those names — that is how you get from a general
   phrase to the class that actually implements it.
5. **Change one variable per retry.** Drop the most specific word; then swap the noun for a
   synonym; then try the likely identifier. Changing the whole query tells you nothing about
   which word was the problem.
6. Spellings are English and American variants are not unified for you: if `modelling` fails,
   `modeling` may work (`model`/`models`/`modelling` are stemmed together, other pairs are not).

## 3. Reading an empty result

An empty `hits` list means: *no block contains all of those words*. It is a statement about
your query, not about the corpus. It is the only signal you get that your words are wrong, so
do not try to argue with it — change the words. Three retries with one word changed each is
usually enough to tell "wrong vocabulary" from "this concept really is not stated anywhere".

## 4. Judging the evidence you got

- `kind=class` / `kind=function` — the block *declares* that unit, `symbol` is its name. If the
  block's `doc` is non-null, that paragraph is the author's own description: read it first, it
  is the best plain-English statement you will get from this tool.
- `kind=filedoc` — the file's description paragraph on its own. Cheap and rich in vocabulary.
- `kind=text` with a non-null `symbol` — **a declaration point** (a `typedef`/`using` alias, for
  example). The name is defined here; this is the block to cite for "where is X defined".
- `kind=text` with `symbol=null` — prose or mixed lines. Read it before believing it; a block
  that merely contains your word is not a block that answers the question.
- `kind=lines` — a fixed-width fallback chunk from a file the parser could not structure.
- `truncated=true` — you got a prefix. Re-run the same query with a larger `--max-chars`
  (up to 8000) before quoting; quoting half a sentence as if it were the whole statement is the
  most common way to produce a wrong citation.

## 5. When to stop

Stop when you hold **one block whose text actually states the answer** and you can copy a
verbatim fragment from it. Two signs you should stop rather than keep going:

- you have found a block that states the concept — more searching will not improve the quote;
- you have spent several calls without any hit whose `text` is about the topic — the vocabulary
  is wrong, not the corpus, and another paraphrase of the same sentence will not help.

Never answer from memory, from general knowledge of CFD, or from what a file *should* contain.
If nothing was found, say so and list the queries you tried.

## 6. Output contract

- `ref`: copy the string exactly as returned. Do not reformat it, do not fix its line number.
- `quote`: a fragment copied character-for-character from that block's `text`. Include the
  indentation if you start mid-line; do not add ellipses, do not join non-adjacent lines, do not
  correct spelling or punctuation.
- Cite only a `ref` that appeared in your own search results. A citation assembled from
  elsewhere cannot be checked and will be treated as fabricated.
- Do not read the corpus directory directly and do not grep it. The index is the tool; a quote
  that did not come through it is not evidence.
