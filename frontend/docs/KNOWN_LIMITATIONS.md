# Known limitations (v0.1 beta)

What the web app does not do yet. Items marked "needs API" also need backend work.

| Area                                                       | Today                                                                                   | Needed                                                   |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| Ticket statuses                                            | Fixed: open, pending, resolved                                                          | Custom statuses (needs API)                              |
| Live reply collision                                       | Warns before sending if someone else replied after you started typing (checked on send) | Real-time "who is viewing / typing" presence (needs API) |
| Companies                                                  | Customer-level history only                                                             | Company/account view for B2B (needs API)                 |
| Live chat                                                  | Chat tickets can be handled; no embeddable website widget                               | Native widget (needs API)                                |
| Backups                                                    | None in the UI                                                                          | Backup list and restore verification (needs API)         |
| Phone                                                      | Not supported                                                                           | Not planned for v0.1                                     |
| Multi-brand portals, article approvals, incident broadcast | Not supported                                                                           | Roadmap                                                  |

Feature presence is not the same as production verification. Self-hosting, migration and
delivery reliability still need the release-gate tests (fresh install, backup/restore,
migration reconciliation, load test).
