"""Shared building blocks for the Usiphus engines.

Every ``usiphus_vNN.py`` engine used to carry its own copy of the same small helpers (JSON reading,
atomic writes, canonical hashing, whitespace cleaning).  They live here once so a fix lands everywhere:

* ``errors``  - ``UsiphusError``, the common base of every engine error (still a ``ValueError``)
* ``files``   - ``atomic_write_text``, ``write_json``, ``read_json_file`` + ``JsonReadPolicy``
* ``hashing`` - ``canonical_json``, ``sha256_hex``, ``digest_value``, ``sha256_file``
* ``text``    - ``clean`` (collapse whitespace) and ``strip_text`` (strip only)
* ``ids``     - ``objective_id``, the stable learning-objective identifier
* ``ollama``  - ``local_ollama_url``, the loopback-only Ollama address guard

The package is named ``usiphus_lib`` (not ``usiphus``) because ``usiphus.py`` is the command dispatcher.
It uses only the standard library, like the rest of the project.
"""
