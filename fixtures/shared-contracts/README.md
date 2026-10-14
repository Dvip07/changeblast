# shared-contracts

JSON Schemas for events exchanged over the message broker. Each schema's `$id` ends with the
topic name and a major version (`/v1`). Producers and consumers validate payloads against these
files at startup and in CI.

## Versioning rules

- Adding an optional field is a minor change and keeps the same `$id`.
- Removing or renaming a field, or changing its type, is a breaking change: publish a new `/v2`
  schema and dual-publish until every consumer has migrated.
- `additionalProperties` is `false`, so producers cannot send fields that are not in the schema.

## Events

| Topic | Schema | Producer |
|---|---|---|
| `billing.refund.approved` | `events/refund-approved.schema.json` | billing-service |
