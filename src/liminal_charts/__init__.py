"""liminal-charts, the chart app: its front and pages, the data API and the command line (`liminal`),
on liminal-libs (the library: the stores, the plugin contract, the collector and the scheme), with
the fetcher in liminal-integrator. The user, 2026-11-02: "lets rename the app to liminal-charts", "not
liminal", "refactor": the app's own modules are cli, deps, gui and trading; everything else is the
library's, imported as liminal_libs (the alias package `liminal` is gone)."""

import os
from pathlib import Path

import liminal_libs

__version__ = "0.2.5"
liminal_libs.limit_threads()  # before numpy loads (and again on a reload of this package)
# The GitHub-hosted datasets' git submodules stay in this repository (data/github): the library reads
# them from here unless LIMINAL_DATA_REPOS says where else.
_repos = Path(__file__).resolve().parents[2] / "data" / "github"
if _repos.is_dir():
    os.environ.setdefault("LIMINAL_DATA_REPOS", str(_repos))
