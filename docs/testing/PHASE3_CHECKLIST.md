# CeDesk: the 42 checklist items, with the tests that prove them

Item 9 of the Phase 3 list (End of Phase 3 check, step 1), and the list to show Amol in
item 10. The 42 items are the mock's test checklist (feature doc, section 11: 19 core,
21 enterprise, plus 41 and 42). Each one works through the API, and the tests linked
next to it prove it. The tests call the API with a real Postgres (row-level security
on), except the few unit tests marked *unit*.

**Run:** the full suite with the database on `main` at `7109e6a` (16 Nov 2026): 1575
passed, 2 skipped, 0 failed. Every test linked below passed in that run (107 tests,
checked by name).

| Status | Count | Meaning |
|---|---|---|
| **Pass** | 38 | Works through the API; its tests pass |
| **Partial** | 3 | Works; one part waits on a Cevanos route or a decision (named in the row) |
| **Waiting** | 1 | Not built yet: waits on Ritesh's Cevanos routes |

---

## Core (19)

| # | Item | Status | Proved by | Notes |
|---|---|---|---|---|
| 1 | Help desk tab | Pass | [test_sign_in_and_me](../../tests/db/test_auth_api.py#L20) | The tab itself is the frontend; the backend part is sign-in and `GET /me` |
| 2 | Cevanos Inbox chat turned into a CeDesk ticket | Pass | [test_an_agent_turns_a_chat_into_a_ticket](../../tests/db/test_cevanos.py#L570) | Through the connector |
| 3 | Handover from Cevanos AI | Pass | [test_a_handover_becomes_a_routed_ticket_waiting_for_accept](../../tests/db/test_cevanos.py#L310), [test_reply_waits_for_accept_and_accept_takes_the_chat_over](../../tests/db/test_cevanos.py#L353) | The customer's real name and email on chat tickets (AI6) comes with item 3 |
| 4 | Priority | Pass | [test_list_tabs_filters_search_and_order](../../tests/db/test_tickets_api.py#L245), [test_changes_write_system_lines](../../tests/db/test_tickets_api.py#L173) | Change it, filter by it, sort by it |
| 5 | Ticket types with custom fields | Pass | [test_ticket_types_and_field_checks](../../tests/db/test_tickets_api.py#L83), [test_type_change_records_every_cleared_field](../../tests/db/test_hardening.py#L149) | |
| 6 | Teams and auto-assignment | Pass | [test_round_robin_with_reason_and_notification](../../tests/db/test_routing_api.py#L52), [test_rule_1_refund_goes_to_billing_and_is_auto_assigned](../../tests/db/test_rules_api.py#L85) | |
| 7 | Notes with @mentions | Pass | [test_note_mentions_notify_the_mentioned_member](../../tests/db/test_messages_api.py#L99) | |
| 8 | Agent roles | Pass | [test_assignment_rules](../../tests/db/test_tickets_api.py#L216), [test_roles_on_member_routes](../../tests/db/test_auth_api.py#L77), [test_role_action](../../tests/unit/test_permissions.py#L43) *unit* | An agent assigns only to themselves; Reports and Settings are refused |
| 9 | Canned responses | Pass | [test_the_list_narrows_to_what_was_typed_after_the_slash](../../tests/db/test_saved_replies_api.py#L33), [test_reply_fills_placeholders_and_keeps_status](../../tests/db/test_messages_api.py#L56) | |
| 10 | Macros | Pass | [test_refund_approved_does_all_five_steps_in_one_go](../../tests/db/test_macros_api.py#L79) | |
| 11 | Automation rules | Pass | [test_the_mocks_six_rules_are_there_in_order](../../tests/db/test_rules_api.py#L69), [test_a_reply_on_pending_reopens_it_and_tells_the_assignee](../../tests/db/test_rules_api.py#L239) | |
| 12 | SLA with business hours | Pass | [test_every_ticket_shows_its_sla](../../tests/db/test_sla.py#L80), [test_a_breach_shows_and_stays](../../tests/db/test_sla.py#L240), [test_admins_set_business_hours](../../tests/db/test_sla_editors.py#L87) | Plus 31 unit tests of the clock in [test_sla.py](../../tests/unit/test_sla.py) |
| 13 | CSAT survey | Pass | [test_the_survey_link_rates_the_ticket](../../tests/db/test_csat.py#L79), [test_rating_in_the_portal](../../tests/db/test_csat.py#L197) | |
| 14 | Team reports | Pass | [test_the_team_report_numbers](../../tests/db/test_reports.py#L102) | |
| 15 | Email (From, CC, signature) | Pass | [test_reply_comes_from_the_address_written_to_keeps_cc_and_threads](../../tests/db/test_email_out.py#L210), [test_a_reply_on_an_email_ticket_is_queued_with_signature_and_label](../../tests/db/test_email_out.py#L159) | |
| 16 | Public help center | Pass | [test_search_finds_words_and_prefixes](../../tests/db/test_help_center.py#L153), [test_votes_are_one_per_visitor_and_counted](../../tests/db/test_help_center.py#L174) | |
| 17 | Import from other help desks | Pass | [test_a_csv_import_end_to_end](../../tests/db/test_imports.py#L191), [test_a_help_desks_records_are_matched_and_mapped](../../tests/db/test_imports.py#L425), [test_a_hubspot_import_end_to_end](../../tests/db/test_import_hubspot.py#L40), [test_a_zoho_desk_import_end_to_end](../../tests/db/test_import_zoho.py#L35) | Intercom, Zendesk, Freshdesk, Gorgias, Help Scout, HubSpot, Zoho Desk and CSV; each connector also has its own unit tests |
| 18 | Orders in the sidebar | **Partial** | [test_the_sidebar_reads_orders_and_payments](../../tests/db/test_apps.py#L161), [test_with_cevanos_connected_the_apps_step_aside](../../tests/db/test_apps.py#L209) | Self-hosted (Shopify and Stripe apps) works. With Cevanos connected, orders come from Cevanos: item 2 (AI5, C12), waiting on its route |
| 19 | Customer portal | Pass | [test_an_emailed_code_signs_a_new_customer_in](../../tests/db/test_portal_sign_in.py#L63), [test_customers_only_ever_see_their_own_tickets](../../tests/db/test_portal_tickets.py#L111) | |

## Enterprise (21)

| # | Item | Status | Proved by | Notes |
|---|---|---|---|---|
| 20 | Agent statuses and channel capacity | Pass | [test_status_capacity_and_team_limit](../../tests/db/test_routing_api.py#L68), [test_workspace_default_capacity](../../tests/db/test_routing_api.py#L200) | |
| 21 | Skill-based routing | Pass | [test_skill_and_channel](../../tests/db/test_routing_api.py#L97), [test_hindi_tickets_go_first_to_hindi_speakers](../../tests/db/test_triage.py#L179) | |
| 22 | Multiple SLA policies | Pass | [test_admins_save_the_ordered_policies](../../tests/db/test_sla_editors.py#L42), [test_vip_customers_get_their_policy_and_an_event](../../tests/db/test_sla.py#L145) | |
| 23 | Merge | Pass | [test_merge_copies_the_conversation_and_resolves_the_duplicate](../../tests/db/test_ticket_tools.py#L82), [test_merge_refuses_what_it_should](../../tests/db/test_ticket_tools.py#L130) | |
| 24 | Linked and child tickets | Pass | [test_links_work_both_ways_once](../../tests/db/test_ticket_tools.py#L156), [test_a_child_ticket_copies_the_customer_type_and_fields_for_another_team](../../tests/db/test_ticket_tools.py#L191), [test_a_parent_waits_for_its_children_and_hears_when_one_is_resolved](../../tests/db/test_ticket_tools.py#L234) | |
| 25 | Side conversations | Pass | [test_a_side_conversation_goes_out_and_its_reply_comes_back](../../tests/db/test_side_conversations.py#L42), [test_a_side_conversation_in_slack](../../tests/db/test_slack_side.py#L158) | By email and in Slack. Slack not yet tried with a real Slack workspace (item 12) |
| 26 | Approval requests | Pass | [test_an_agent_asks_a_supervisor_who_approves](../../tests/db/test_ticket_tools.py#L295), [test_who_may_approve](../../tests/db/test_ticket_tools.py#L343) | Sending a Governance hold's approval to Cevanos (X7) is item 5 |
| 27 | Tasks and time logs | Pass | [test_tasks_have_an_owner_a_due_time_and_a_done_line](../../tests/db/test_ticket_tools.py#L396), [test_the_time_log_adds_up_and_billable_is_counted_apart](../../tests/db/test_ticket_tools.py#L473) | |
| 28 | Cevanos AI summary and writing help | **Waiting** | — | Item 1 (AI1–AI4, X11): summary, improve writing, translate and suggested reply. Waits on Ritesh's Cevanos routes. Only when Cevanos is connected; else 409 `cevanos_not_connected` |
| 29 | Topic and sentiment | Pass | [test_a_new_ticket_is_triaged_before_the_rules_run](../../tests/db/test_triage.py#L51), [test_every_customer_reply_triages_again](../../tests/db/test_triage.py#L81), [test_topic](../../tests/unit/test_triage.py#L25) *unit*, [test_sentiment](../../tests/unit/test_triage.py#L60) *unit* | From keyword rules, not AI (as the doc says) |
| 30 | Light agents | Pass | [test_light_agent_can_note_but_not_reply](../../tests/db/test_messages_api.py#L184), [test_light_agents_cant_run_macros](../../tests/db/test_macros_api.py#L158) | |
| 31 | Custom roles with scope | Pass | [test_team_only_hides_other_teams_tickets](../../tests/db/test_custom_roles.py#L135), [test_team_edit_views_other_tickets_read_only](../../tests/db/test_custom_roles.py#L182) | |
| 32 | Security settings and score | Pass | [test_defaults_score_and_admin_only](../../tests/db/test_security_settings.py#L55), [test_ip_allowlist_is_enforced_on_sign_in_and_every_request](../../tests/db/test_security_settings.py#L139), [test_the_workspace_can_require_it](../../tests/db/test_two_factor.py#L151), [test_signing_in_through_the_identity_provider](../../tests/db/test_sso.py#L193) | Enforced, not just shown: password rules, IP list, 2FA, SSO, session length |
| 33 | Card redaction | Pass | [test_card_numbers_never_reach_the_database](../../tests/db/test_triage.py#L101), [test_a_card_number_cut_by_the_subject_is_still_removed](../../tests/db/test_web_form.py#L174), [test_redact](../../tests/unit/test_redaction.py#L28) *unit* | |
| 34 | Audit log with tamper check | Pass | [test_the_chain_verifies_and_shows_tampering](../../tests/db/test_audit.py#L137), [test_a_settings_change_is_recorded](../../tests/db/test_audit.py#L41) | |
| 35 | Sandbox | Pass | [test_copy_edit_compare_try_and_deploy](../../tests/db/test_sandbox.py#L47) | |
| 36 | API keys and webhooks | Pass | [test_a_key_acts_as_its_creator_within_its_level](../../tests/db/test_api_keys.py#L107), [test_ticket_events_reach_the_url_signed](../../tests/db/test_webhooks.py#L144), [test_a_failing_url_is_retried_then_given_up](../../tests/db/test_webhooks.py#L187) | |
| 37 | Marketplace apps | Pass | [test_admins_install_change_and_uninstall](../../tests/db/test_apps.py#L109), [test_jira_issues_from_tickets](../../tests/db/test_jira_slack.py#L99), [test_slack_alerts_for_urgent_and_high](../../tests/db/test_jira_slack.py#L149), [test_installing_calendly_adds_book](../../tests/db/test_calendly.py#L41), [test_installing_the_slack_app](../../tests/db/test_slack_side.py#L136) | Shopify, Stripe, Jira, Slack alerts, Slack side conversations, Calendly. Translate was removed from CeDesk (it is AI, in Cevanos) |
| 38 | Mobile agent app | Pass | [test_the_queue](../../tests/db/test_mobile.py#L59), [test_the_mobile_reply](../../tests/db/test_mobile.py#L106), [test_devices](../../tests/db/test_push.py#L100), [test_banners](../../tests/db/test_push.py#L146) | Banners not yet tried on a real phone with Firebase (item 12) |
| 39 | Richer reports | **Partial** | [test_the_metrics_numbers](../../tests/db/test_report_metrics.py#L112), [test_every_report_as_csv](../../tests/db/test_report_metrics.py#L227), [test_the_article_votes](../../tests/db/test_report_metrics.py#L274) | More metrics, per team, article votes and CSV work. The AI numbers from Cevanos (AI7) are item 4, waiting on its route |
| 40 | Governance report | Pass | [test_the_governance_link_needs_cevanos](../../tests/db/test_report_metrics.py#L161) | The report lives in Cevanos; CeDesk links to it only when Cevanos is connected |

## Added in the doc (2)

| # | Item | Status | Proved by | Notes |
|---|---|---|---|---|
| 41 | CeDesk's own channels (email, portal, web form, API) | Pass | [test_a_new_email_becomes_a_ticket](../../tests/db/test_email_in.py#L83), [test_a_customer_opens_a_ticket_like_any_other_channel](../../tests/db/test_portal_tickets.py#L73), [test_a_form_post_is_a_web_ticket_answered_by_email](../../tests/db/test_web_form.py#L84), [test_another_system_opens_a_ticket_for_its_customer](../../tests/db/test_api_keys.py#L223) | One test per channel |
| 42 | Self-hosted CeDesk without Cevanos | **Partial** | [test_not_connected_changes_nothing](../../tests/db/test_cevanos.py#L594), [test_the_sidebar_reads_orders_and_payments](../../tests/db/test_apps.py#L161), [test_every_setting_in_env_example_reaches_a_container](../../tests/unit/test_deploy_files.py#L26) *unit* | Runs without Cevanos, with its own apps, from `docs/SELF_HOSTING.md` (edition `self_hosted`). Still to come: `GET /me` `ai: false` so the frontend hides the AI buttons (item 1), and the licence headers (item 8, waits on Amol confirming AGPL) |

---

## Guard tests (End of Phase 3 check, step 2)

| Guard | Tests |
|---|---|
| Tenancy (every table has `workspace_id` and forced row-level security) | [test_every_table_has_workspace_id_and_forced_rls](../../tests/db/test_tenancy.py#L36) |
| Roles (the app's database role can't cross workspaces) | [test_app_role_is_plain](../../tests/db/test_roles.py#L15), [test_a_workspace_sees_only_itself](../../tests/db/test_roles.py#L60), [test_writing_into_another_workspace_is_refused](../../tests/db/test_roles.py#L106) |
| Webhook signatures | [test_the_signature_covers_the_timestamp_and_the_body](../../tests/unit/test_webhook_rules.py#L58) *unit*, [test_ticket_events_reach_the_url_signed](../../tests/db/test_webhooks.py#L144), [test_the_webhook_needs_a_good_signature](../../tests/db/test_cevanos.py#L289) (Cevanos in), [test_the_signature_is_checked_first](../../tests/db/test_slack_side.py#L219) (Slack in) |
| Codes and secrets never logged | [test_codes_are_stored_only_as_a_keyed_hash](../../tests/db/test_portal_sign_in.py#L114), [test_request_lines_carry_ids_not_content](../../tests/db/test_logs.py#L33), [test_secrets_never_reach_the_log](../../tests/db/test_audit.py#L75), [test_statement_values_stay_out_of_logs](../../tests/db/test_push.py#L410) |
| Egress (outbound calls reach public addresses only) | [test_a_host_that_resolves_to_a_private_address_is_refused](../../tests/unit/test_egress.py#L149) *unit*, [test_the_pinned_transport_connects_to_the_address_it_checked](../../tests/unit/test_egress.py#L159) *unit* |

## Still to do for the End of Phase 3 check

| Step | What | Waits on |
|---|---|---|
| 1 | Items 28, and the open parts of 18, 39 and 42 | Items 1–4 (Cevanos routes), item 8 (Amol) |
| 3 | `uv run cx check --full` | This repo has no `cx` command; ask Ritesh where to run it |
| 4 | Load test: list, open ticket, reply and live updates under load | Can start now |
| 5 | The AI routes in `docs/frontend/FRONTEND_HANDOFF_CEDESK.md` and `openapi.json` | Item 1 |
| 6 | Tick the items in the plan md ("End of Phase 3 check") | The steps above |
