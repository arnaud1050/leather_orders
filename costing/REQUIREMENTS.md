# Costing module — business requirements & rules

> Part of the `leather_orders` app. [CLAUDE.md](CLAUDE.md) is the *why*;
> this file is the *what must hold true*, as numbered, checkable statements,
> with a test-coverage map at the end. Changing behaviour means changing a
> rule here in the same commit.

Real costs per order: time logged against an order, the studio's labour
rate and overhead, the margin that leaves, and a calculator that suggests
the price that would have made the target margin. It exists because makers
underprice, and a margin nobody computes is a margin nobody notices is
negative.

## 0. Boundary

- **RC1.** The module imports only `db` from the host and `track` from
  `usage`, and no sibling module (not `billing`, not `inventory`). The host
  reads an order's revenue from billing and its material cost from
  inventory and passes both in as plain numbers.
- **RC2.** `costing/pricing.py` is the arithmetic, and imports the standard
  library only, so a future quote can use it without an order.

## 1. Time

- **RC3.** A time entry is an order, a day, a duration in whole minutes (more
  than 0, at most 24 hours), an optional one-line note (200 characters,
  whitespace collapsed) and the user who logged it. Logged from the order
  page's **Costs** tab as hours and minutes, either of which may be left
  empty (empty counts as 0); a missing day, a negative number or a total
  outside the range is refused with a red notice in the Time section and
  stores nothing. Entries are manual only — there is no start/stop timer.
- **RC4.** **Each entry snapshots the company's hourly rate when it's
  logged**, the way `OrderMaterial` snapshots its unit price. Changing the
  rate in Settings changes what *new* time costs and never what logged time
  cost. The labour cost itself (minutes ÷ 60 × that rate) is computed on
  every read, never stored (hard rule 10).
- **RC5.** An entry is deleted with the trash icon on its row (no edit;
  delete and re-log). The delete is scoped by order: the right entry id
  under the wrong order does nothing. Hard-deleting an order (tentative only,
  core OR1e) deletes its entries and its estimate with it.

## 2. What an order cost

- **RC6.** **Revenue is the order's pre-tax price, net of its discount**
  (`Order.pre_tax_total`, billing's subtotal): once its invoice is sent or
  void, the invoice's frozen `issued_subtotal`, never the lines re-added —
  lines edited after issue (core N5) don't move it. A draft invoice still
  follows the lines.
- **RC7.** Material cost is inventory's `total_material_cost` for the order:
  stock materials at their snapshot prices plus one-off "Other" costs.
- **RC8.** Overhead is the company's overhead percentage of materials +
  labour, read **live** (unlike the rate, it isn't snapshotted: it's a
  planning assumption about the shop, not a fact about one session of work).
  Cost = materials + labour + overhead; profit = revenue − cost; margin =
  profit ÷ revenue; **earned per hour** = (revenue − materials − overhead) ÷
  hours, which is the labour rate plus profit per hour.
- **RC9.** Nothing here is stored. The Margin section shows margin, profit,
  earned per hour and the breakdown, with "as invoiced" on revenue once the
  invoice is out. With no revenue there's no margin ("—"); with no time
  there's no earned-per-hour, and the section says the margin counts no
  labour. With time logged, it compares the price with what the company's
  target margin needed (cost ÷ (1 − target)): an amber `.warning-note` when
  it came in under, a plain note otherwise.

## 3. Settings → Costs

- **RC10.** One row of settings per company: hourly labour rate, overhead
  percentage, default target margin. A company that has never saved them
  reads $0, 0% and 30% **without a row being written** — a page that only
  reads never writes. Saving writes the row.
- **RC11.** Rate 0–10,000, overhead 0–1000%, target margin 0–95% (100% has
  no price that reaches it); anything outside, or anything that isn't a
  finite number, is refused with a red notice and nothing is saved. A field
  the form doesn't send is left alone (hard rule 9).
- **RC12.** **No rate, no time.** While the hourly rate is $0, the Costs
  tab's Time section shows an amber `.warning-note` ("Set your hourly rate
  to start") and a form asking for the rate alone, in place of the log
  form; the Pricing section shows a note in place of the calculator. The
  server refuses logging time and saving an estimate too, with a red notice
  in the section, and nothing is written: an entry snapshots its rate
  (`RC4`), so time logged at $0 would count the work as free for good.
  Saving the rate there (`POST /orders/<id>/costs/rate`, more than $0, up
  to the RC11 cap) writes only the rate, leaving overhead and the target
  margin alone, and the panel is gone from then on. Entries already logged,
  the Margin section and Analytics are untouched. A studio that really
  wants a $0 rate can't log time; that's accepted.

## 4. The pricing calculator

- **RC20.** The calculator takes estimated hours, a materials figure (empty
  = the materials already on the order) and a target margin (empty = the
  company default), and keeps them per order (`OrderEstimate`), so the
  suggestion stays on the page next to the actual price. A field the form
  doesn't send keeps its saved value; out-of-range input is refused in the
  Pricing section.
- **RC21.** Suggested price = (materials + hours × **today's** rate) × (1 +
  overhead) ÷ (1 − target margin). It prices work ahead, so it uses the
  current rate, not any snapshot. Computed on every read.
- **RC22.** The suggestion is shown against the order's pre-tax price; when
  the price is below it, an amber `.warning-note` says by how much.
- **RC23.** **The suggestion reaches the order only as a new line, never by
  editing one.** "Add as a line item" adds one line with an editable
  description and amount: the whole suggestion on an order with no lines,
  the difference on one that has lines. It's offered only while the price is
  below the suggestion, refused (blank description, amount ≤ 0 or not a
  number) with a red notice, and locked with the other lines once the
  invoice is sent or void (core OR7a).

## 5. Analytics

- **RC30.** `/analytics` has a **Margins** section with three cards:
  margin YTD (with profit), earned per hour YTD (with hours logged), and
  margin by order type (with "No type" for untyped orders, largest revenue
  first, each with its order count).
- **RC31.** **Only orders with time logged count**, and the section says so
  with the number of orders: an order with no time would count its labour as
  free and flatter every figure. "YTD" is the order's due date in the
  current year; cancelled orders are left out.
- **RC32.** Totals are summed before dividing (`pricing.combine`), so a large
  order weighs more than a small one; it is not an average of percentages.
- **RC33.** Every figure is scoped to the signed-in company, like the rest
  of the page (AN1).

## 6. Tenancy, privacy, usage

- **RC40.** Costs are internal. Showcase's website serializer, its catalog
  mode and `showcase_adapter.py` never import or read this module, and no
  page a client sees shows a cost, rate or margin.
- **RC41.** Every route needs sign-in. Every order-scoped route resolves the
  order through the host's `get_order_or_404`, so another tenant's order is
  a 404 and nothing is written. Settings are per company.
- **RC42.** Usage events: `view.order_costs` (the tab, daily),
  `order.time_logged`, `order.price_estimated`, `settings.changed` with
  `section="costs"`, and `order.line_added` with `source="calculator"`.
  None carries an amount, a rate or a note.

## 7. Deliberate non-requirements

- **RCN1.** No start/stop timer. Manual entries were chosen for the first
  version; a server-side timer (Start stores a running row, Stop turns it
  into an entry) fits the stack if it's ever wanted.
- **RCN2.** One company-wide rate; no override per order type or per user.
- **RCN3.** No quote document. The calculator is built for one to reuse
  (`pricing.suggest_price` takes plain numbers).
- **RCN4.** Overhead and the target margin aren't snapshotted (RC8): changing
  them re-reads every past order's margin, which Settings → Costs says.

---

## Test coverage map

All in `tests/test_costing.py` unless named.

| Rule | Test(s) |
| --- | --- |
| RC1 | `test_costing_imports_only_db_from_the_host_and_no_sibling_module`; `tests/test_usage.py::test_modules_import_only_the_dependency_free_half[costing]` |
| RC2 | `test_pricing_uses_the_standard_library_only` |
| RC3 | `test_logging_time_through_the_tab`, `test_a_missing_minutes_field_is_just_hours`, `test_a_duration_that_isnt_one_is_refused`, `test_a_bad_time_entry_is_refused_in_its_section` |
| RC4 | `test_an_entry_keeps_the_rate_it_was_logged_at`, `test_order_costs_is_derived_on_every_read` |
| RC5 | `test_deleting_an_entry_is_scoped_to_its_order`, `test_deleting_an_order_takes_its_time_with_it` |
| RC6 | `test_revenue_is_pre_tax_and_net_of_the_discount`, `test_an_issued_order_keeps_its_invoiced_revenue_when_lines_change`, `test_a_draft_invoice_still_follows_the_lines`, `test_the_tab_shows_the_margin_from_the_frozen_figure` |
| RC7–RC8 | `test_a_breakdown_adds_up`, `test_the_tab_shows_the_margin_from_the_frozen_figure` |
| RC9 | `test_no_hours_means_no_hourly_rate_rather_than_zero`, `test_no_revenue_means_no_margin_rather_than_a_division_error`, `test_a_loss_reads_as_a_negative_margin`, `test_an_order_with_no_time_says_its_margin_counts_no_labour`, `test_underpricing_is_flagged` |
| RC10 | `test_a_company_that_never_saved_reads_the_defaults_without_a_row`, `test_the_settings_form_saves`, `test_settings_are_per_company` |
| RC11 | `test_settings_out_of_range_are_refused_and_nothing_is_written`, `test_a_settings_value_that_isnt_a_usable_number_is_refused`, `test_a_settings_field_the_form_left_out_is_left_alone` |
| RC12 | `test_with_no_rate_the_tab_asks_for_one_instead_of_time_and_the_calculator`, `test_once_a_rate_is_set_the_panel_is_gone`, `test_with_no_rate_time_and_estimates_are_refused`, `test_setting_the_rate_from_the_tab_saves_only_the_rate`, `test_a_rate_from_the_tab_that_isnt_one_is_refused`, `test_another_tenants_order_cant_take_a_rate` |
| RC20 | `test_saving_an_estimate_shows_the_suggestion`, `test_an_estimate_field_left_off_the_form_keeps_its_value`, `test_a_bad_estimate_is_refused` |
| RC21 | `test_the_suggested_price_makes_exactly_the_target_margin`, `test_a_target_margin_is_capped_below_one_hundred_percent`, `test_the_estimate_uses_todays_rate` |
| RC22 | `test_saving_an_estimate_shows_the_suggestion` |
| RC23 | `test_adding_the_suggestion_adds_a_line_and_edits_none`, `test_the_suggestion_form_offers_the_difference_when_lines_exist`, `test_an_unusable_suggested_line_is_refused`, `test_the_suggested_line_is_locked_with_the_invoice` |
| RC30–RC32 | `test_analytics_counts_only_orders_with_time`, `test_analytics_with_no_time_anywhere_still_renders`, `test_combined_figures_weigh_big_orders_more` |
| RC33 | `test_analytics_margins_ignore_another_tenant` |
| RC40 | `test_showcase_never_reads_costs` |
| RC41 | `test_another_tenants_order_is_not_found`, `test_labour_by_order_is_scoped_to_the_company`, `test_every_costs_route_needs_sign_in` |
| RC42 | `test_the_costs_actions_are_recorded` |
| Notices (MOD8) | `tests/test_save_notices.py::test_every_tenant_page_is_wired` (the Costs tab and Settings → Costs), `test_every_form_that_leaves_a_message_names_its_section` |
| Layout (hard rule 7a) | `e2e/tests/section-gaps.spec.ts` — the Costs tab empty and populated, Settings → Costs |
