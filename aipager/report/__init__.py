"""Problem reports (roadmap 8.112).

A report is the only thing this feature ever sends off the user's machine,
and only after the user has seen it and tapped Send. It is built from an
allow-list of fixed-shape fields (versions, enums, counts, code locations
inside aipager's own files): never a chat, a prompt, an answer, a name, an
id, a path, a token or an exception's message.

- :mod:`aipager.report.fingerprint` names an error by its type and
  aipager's own frames (standard library only: the hook imports it).
- :mod:`aipager.report.schema` is the allow-list and its validator.
- :mod:`aipager.report.builder` builds a report from typed sources and
  renders the exact text the user previews.
"""
