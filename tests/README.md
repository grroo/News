# Offline regression tests

Use Python 3.12 and Node 22+ with dependencies installed as in the root README:

```sh
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.cjs
npm run check --prefix cloudflare/news-scheduler
```

Set `NEWS_TEST_PYTHON` to the Python 3.12 executable when it is not `python3` on PATH.
The cross-language release test runs the real guard/builder/provider parser with
fixture feeds and synthetic HTTP responses; it makes no paid call. Temporary
build directories are removed. CI runs tests; production generation is separate.

Optional real-browser smoke (Playwright and Chrome installed):

```sh
NEWS_PLAYWRIGHT_MODULE=/absolute/path/to/playwright node tests/release/browser.cjs
```

This tests local UI simulation, not a deployed Access login. Exact coverage and
pending live checks are in [T09 validation](../docs/release/validation.md).
