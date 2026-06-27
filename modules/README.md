# Domain Modules

Domain modules follow the `ODP-SD-04` internal shape:

```text
domain/
application/
infrastructure/
api/
workers/
tests/
README.md
```

Modules must not import another module's infrastructure implementation
directly. Use APIs, events, shared DTOs, model-ready views, or workflow
orchestration for cross-domain behavior.
