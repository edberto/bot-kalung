"""Keep tests away from the worker's real machine-local config.

`AppContext.create()` writes %LOCALAPPDATA%\\BotKalung\\bootstrap.json, which is
machine-global. A test creating a context in a temp folder therefore used to
repoint the installed app at a directory that vanished when the test ended, so
the next launch found no database and re-ran the setup wizard — reported on
2026-07-20 as "it asks for the Drive path again every time I rebuild".

Importing this module redirects that pointer into a temp directory. Import it
straight after the `sys.path` insert, before anything creates a context.
"""

import atexit
import os
import shutil
import tempfile

from bot_kalung.core.bootstrap import HOME_ENV_VAR

if not os.environ.get(HOME_ENV_VAR):
    _dir = tempfile.mkdtemp(prefix="botkalung-test-home-")
    os.environ[HOME_ENV_VAR] = _dir
    atexit.register(shutil.rmtree, _dir, ignore_errors=True)

BOOTSTRAP_HOME = os.environ[HOME_ENV_VAR]
