Run the regression suite after installing requirements.txt (Python 3.12 and Node 20+):

    python -m unittest discover -s tests -p 'test_*.py'
    node --test tests/freshness.test.cjs

Tests cover priority-source selection, direct-versus-aggregated copies, publisher
attribution, validated citation IDs, feed fallback and failure status, migration
of legacy seen history, an isolated offline build, and Rome freshness status
through both daylight saving changes. The pipeline test generates fresh fixtures
from the current config in a temporary directory; no network or API key is used.
The deployment workflow runs both suites before generating a real briefing.

For manual offline exploration, write fixtures to a scratch directory:

    python tests/make_fixtures.py --output-dir /tmp/briefing-fixtures
    python scripts/build.py --mock --fixtures /tmp/briefing-fixtures --now 2026-09-04T17:00:00+00:00

The second command writes mock data/ in your checkout; do not commit that output.
