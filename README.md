# haau3 Research

Open research on caregiving, with and without AI. This repository builds the site at
[research.haau3.com](https://research.haau3.com) with [Quarto](https://quarto.org), and a merge to
`main` publishes it to GitHub Pages.

```sh
uv sync                    # the Quarto version uv.lock pins
uv run quarto preview
```

[CONTRIBUTING.md](CONTRIBUTING.md) has the rules every page keeps and the checks every pull
request runs. The Library's entries are one CSL-JSON file, `library/references.json`, which
reference managers such as Zotero can import.

## Licences

- **Writing, library entries, synthetic datasets and results:** [CC BY 4.0](LICENSE-CC-BY-4.0).
- **Code:** [MIT](LICENSE).
- **Fonts** (`brand/fonts/`): Fraunces, Geist and Geist Mono, each under the SIL Open Font
  Licence 1.1 beside it.

Copyright 2026 Brian K. Fung Media LLC, doing business as haau3. To cite this work, see
[CITATION.cff](CITATION.cff).

## Public text

Everything in this repository is public, and it says only what the repository itself shows.
`scripts/check_public_text.py` checks the files, the commits of a pull request and its title and
description, using only Python's standard library:

```sh
python3 -I scripts/check_public_text.py
python3 -I -m unittest discover -s tests
```
