"""Real pipeline tool integrations.

Each tool gets a module with a small, dependency-light client. Steps that
have a registered runner in `runner.py` execute the real tool during a run;
unregistered steps keep using the simulated engine.
"""