---
name: Publish foreign-key ordering
description: A Replit Publish schema diff can emit a new foreign key before the unique constraint on a newly added referenced column.
---

When a publish-time validation fails because a referenced column lacks a unique constraint, inspect the generated statement order instead of assuming the dev database lacks uniqueness. In this project the development database had a unique index, but the generated production diff still placed the foreign key before the new unique constraint. A defaulted new key column can avoid truncating existing rows, but the FK still needs a staged publish: publish the column and unique constraint first, then add the foreign key in a later Publish after the target has that uniqueness.

**Why:** the Publish diff is ordered in phases that may place foreign keys before unique constraints created on newly added columns, causing validation to fail despite a valid final schema.

**How to apply:** use read-only production inspection and `explainSchemaDiff()`; never patch production directly or approve a truncate to work around the ordering. Keep the first phase data-preserving, and restore the FK in development only after the first publish has succeeded.
