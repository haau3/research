# haau3 Research

Open research on caregiving, with and without AI. This repository builds the site at
[research.haau3.com](https://research.haau3.com).

## Licences

- **Writing, library entries, synthetic datasets and results:** [CC BY 4.0](LICENSE-CC-BY-4.0).
- **Code:** [MIT](LICENSE).

Copyright 2026 Brian K. Fung Media LLC, doing business as haau3.

## Public text

Everything in this repository is public, and it says only what the repository itself shows.
`scripts/check_public_text.py` checks the files, the commits of a pull request and its title and
description, using only Python's standard library:

```sh
python3 -I scripts/check_public_text.py
python3 -I -m unittest discover -s tests
```
