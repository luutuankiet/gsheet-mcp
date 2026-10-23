# Copied from google-docs-mcp@2fb4652 src/<package>/sandbox/__init__.py; renamed.
"""The script sandbox: a worker process holding one V8 isolate per call.

`process.Worker` starts `worker.py` in a child process and owns its timeout;
`prelude.js` is everything a script sees besides `input` and `model`.
"""
