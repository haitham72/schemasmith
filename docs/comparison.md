# Choosing an internal-tools platform

Official sources checked 2026-10-26. Features and commercial editions change;
links below identify the evidence, rather than implying equivalent tiers. This is
an architectural comparison, not a benchmark or security certification. We did
not install or test the competing products.

| Area | schemasmith v0.1 | Retool | NocoDB | Appsmith | Budibase |
| --- | --- | --- | --- | --- | --- |
| Self-hosting / offline | Local web/Postgres; runtime needs no internet with local assets and model disabled | Self-hosting documented; validate disconnected operation for your edition [R1] | Self-hostable; verify dependencies/features for your edition [N1] | Self-hosting documented; air-gapped option requires contacting vendor [A1] | Self-hosting documented; cloud AI requires outbound access [B1] |
| Natural language / AI | Propose closed JSON entities/workflow/views; human reviews DDL | AI app builder and agents [R2] | NocoAI and MCP-connected AI tools; scope depends on edition [N2] | AI/agent offering is evolving; legacy Agents page currently closes new signups [A2] | AI agents, app generation and automations [B2] |
| Migration review | Deterministic DDL, classes, row counts, hash, archive default | Equivalent archive/hash contract not evaluated | Equivalent contract not evaluated | Equivalent contract not evaluated | Equivalent contract not evaluated |
| Approval workflows | Spec graph, line/header transitions, exact roles and ownership | Workflow tools available [R2] | Workflow product available [N2] | Workflow product documented [A1] | Requests/approval automation documented [B2] |
| UI breadth | Fixed views generated from a spec | Rich app editor/components [R2] | Tables, apps and workflows [N2] | Widget-based app builder [A3] | App builder and automation platform [B2] |
| Data integrations | One owned Postgres schema; optional model endpoint | API/database resources [R3] | External database connections depend on plan [N3] | Databases and APIs [A3] | Multiple data sources/connectors [B2] |
| Governance / SSO | Local accounts, roles; no SSO in v0.1 | SSO documented [R4] | SSO in licensed editions [N3] | OAuth/SAML/OIDC documented [A4] | SSO is plan-dependent [B3] |
| License | MIT | Commercial service/self-hosted terms; request current agreement [R1] | Sustainable Use License since January 2026 [N4] | Apache-2.0 repository license; commercial features have separate terms [A5] | GPLv3 overall with package-specific MPL/BSL terms [B4] |

Choose schemasmith when the problem fits a table, form, approval and export, and
you want a small engine whose schema changes you can inspect. The tradeoff is a
narrow UI/type set, Postgres only, local account administration, and an early
release whose real-model quality with small local models is unverified.

The other platforms offer broader UI and integration surfaces and established
documentation/release communities. That assessment follows the product scopes
and maintained public repositories below; it does not measure support quality or
compare adoption numbers. Their SSO and enterprise controls can be a better fit
for a large organization. Check feature availability, outbound dependencies and
licensing for the exact version/edition. None is described here as lacking AI.

[R1]: https://docs.retool.com/self-hosted
[R2]: https://docs.retool.com/classic-apps/concepts/intro
[R3]: https://docs.retool.com/data-sources
[R4]: https://docs.retool.com/sso/quickstart
[N1]: https://github.com/nocodb/nocodb
[N2]: https://nocodb.com/docs/changelog/2026.10.0
[N3]: https://nocodb.com/pricing
[N4]: https://nocodb.com/docs/self-hosting/license
[A1]: https://docs.appsmith.com/getting-started/setup
[A2]: https://agents.appsmith.com/
[A3]: https://github.com/appsmithorg/appsmith
[A4]: https://docs.appsmith.com/getting-started/setup/instance-configuration/authentication
[A5]: https://github.com/appsmithorg/appsmith/blob/release/LICENSE
[B1]: https://docs.budibase.com/docs/self-hosted-ai-features
[B2]: https://github.com/Budibase/budibase
[B3]: https://budibase.com/pricing/
[B4]: https://github.com/Budibase/budibase/blob/master/LICENSE
