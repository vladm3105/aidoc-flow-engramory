# Adapters

One subpackage per environment, each implementing the `engramory.ports.*` protocols.
A factory wires the active set from `ENGRAMORY_PROFILE`. Migrating clouds = add/select
an adapter set; the core and MCP layer are untouched.
