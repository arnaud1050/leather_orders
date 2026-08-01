"""
Automatic handling of mail from known senders.

Two questions the lead inbox can't answer for itself, because they're about
the sender rather than the message:

- Is this a newsletter I will never act on?  →  hide it.
- Is this address *only ever* a genuine enquiry — a website contact form,
  a marketplace's "you have a new message" relay — where the app already
  knows the answer a person would give?  →  make the client.

The rules are per company and matched at sync time (see
`email_sync._apply_sender_rules`). Everything here is about *deciding*;
acting on the decision belongs to the sync, which is the only thing that
knows a thread is new.
"""

import logging

from models import db

from communications.models import (
    AUDIT_SENDER_RULE_CHANGED, FIELD_FIRST_NAME, FIELD_IGNORE, FIELD_LAST_NAME,
    FIELD_MESSAGE, FIELD_NAME, FIELD_TARGET_LABELS, RULE_CONVERT, RULE_HIDE,
    AutoCreatedClient, SenderRule, SenderRuleField, utcnow,
)
from communications.services import audit

logger = logging.getLogger(__name__)

VALID_ACTIONS = (RULE_CONVERT, RULE_HIDE)


class SenderRuleError(Exception):
    """Something the user should be shown, not a bug."""


def rules_for(company_id: int) -> list[SenderRule]:
    """Every rule for one company, hides first then conversions, A-Z.

    Grouped by action rather than by when they were added, because the page
    that lists them is read as two lists — "ignore these" and "trust these".
    """
    return (
        SenderRule.query.filter_by(company_id=company_id)
        .order_by(SenderRule.action, SenderRule.pattern)
        .all()
    )


def rules_by_action(company_id: int, action: str) -> list[SenderRule]:
    return [rule for rule in rules_for(company_id) if rule.action == action]


def match(rules, address: str | None) -> SenderRule | None:
    """The rule that applies to an address, or None.

    An exact address wins over a domain rule covering the same address, so
    "hide everything from @squarespace.info **except** the contact form" is
    expressible. Without that precedence the more specific rule would be
    unreachable, which is the sort of thing people only discover after a
    week of enquiries went quietly into the dismissed pile.
    """
    address = (address or "").strip().lower()
    if not address:
        return None
    exact = [rule for rule in rules if not rule.is_domain_rule and rule.matches(address)]
    if exact:
        return exact[0]
    for rule in rules:
        if rule.matches(address):
            return rule
    return None


def add_rule(company_id: int, pattern: str, action: str, note: str = "") -> SenderRule:
    """Create a rule. Commits.

    Raises SenderRuleError for anything the user can fix — a blank or
    malformed pattern, an unknown action, a duplicate.
    """
    pattern = _clean_pattern(pattern)
    if action not in VALID_ACTIONS:
        raise SenderRuleError("Choose what the rule should do.")

    existing = SenderRule.query.filter_by(company_id=company_id, pattern=pattern).first()
    if existing is not None:
        raise SenderRuleError(f"There's already a rule for {pattern}.")

    rule = SenderRule(
        company_id=company_id, pattern=pattern, action=action,
        note=(note or "").strip()[:255] or None,
    )
    db.session.add(rule)
    audit.record(
        company_id, AUDIT_SENDER_RULE_CHANGED,
        f"Added: {pattern} → {rule.action_label.lower()}",
    )
    db.session.commit()
    return rule


def delete_rule(company_id: int, rule_id: int) -> SenderRule:
    """Remove a rule. Commits.

    Deleting is safe here, unlike a SourceOption: a rule is an instruction
    for future mail, not a historical answer anything else references.
    Threads it already hid stay hidden, and clients it already created stay
    clients — removing it only stops it applying again.
    """
    rule = SenderRule.query.filter_by(id=rule_id, company_id=company_id).first()
    if rule is None:
        raise SenderRuleError("That rule no longer exists.")
    audit.record(
        company_id, AUDIT_SENDER_RULE_CHANGED,
        f"Removed: {rule.pattern} → {rule.action_label.lower()}",
    )
    db.session.delete(rule)
    db.session.commit()
    return rule


def _clean_pattern(pattern: str) -> str:
    """Normalise what someone typed into something matchable.

    Deliberately forgiving about the two things people actually paste: a
    display-name form (`Squarespace <form-submission@squarespace.info>`) and
    a bare domain (`squarespace.info`, meaning "anything from there").
    Deliberately *not* forgiving about anything else — a pattern that
    silently matches nothing is a rule that appears to be working.
    """
    pattern = (pattern or "").strip().lower()
    if "<" in pattern and ">" in pattern:
        pattern = pattern[pattern.index("<") + 1:pattern.index(">")].strip()
    if not pattern:
        raise SenderRuleError("Enter an email address or a domain.")

    if pattern.startswith("@"):
        domain = pattern[1:]
    elif "@" in pattern:
        local, _, domain = pattern.partition("@")
        if not local:
            raise SenderRuleError(f"{pattern} isn't a valid address.")
        if "." not in domain:
            raise SenderRuleError(f"{pattern} isn't a valid address.")
        return pattern
    else:
        # No "@" at all reads as a domain: nobody means a local part here.
        domain = pattern
        pattern = f"@{domain}"

    if not domain or "." not in domain or " " in domain:
        raise SenderRuleError(f"{pattern} isn't a valid address or domain.")
    return pattern


# ---------------------------------------------------------------------------
# Field mapping: reading a contact form out of the body of an email.
# ---------------------------------------------------------------------------

def add_field(company_id: int, rule_id: int, label: str, target: str) -> SenderRuleField:
    """Map one label to one client field. Commits."""
    rule = SenderRule.query.filter_by(id=rule_id, company_id=company_id).first()
    if rule is None:
        raise SenderRuleError("That rule no longer exists.")
    label = _clean_label(label)
    if target not in FIELD_TARGET_LABELS:
        raise SenderRuleError("Choose where that field should go.")
    if any(_normalise(field.label) == _normalise(label) for field in rule.fields):
        raise SenderRuleError(f"{label} is already mapped for this rule.")

    field = SenderRuleField(rule_id=rule.id, label=label, target=target)
    db.session.add(field)
    audit.record(
        company_id, AUDIT_SENDER_RULE_CHANGED,
        f"{rule.pattern}: {label} → {field.target_label.lower()}",
    )
    db.session.commit()
    return field


def delete_field(company_id: int, field_id: int) -> SenderRuleField:
    """Remove one mapping. Commits."""
    field = (
        SenderRuleField.query.join(SenderRule)
        .filter(SenderRuleField.id == field_id, SenderRule.company_id == company_id)
        .first()
    )
    if field is None:
        raise SenderRuleError("That field mapping no longer exists.")
    audit.record(
        company_id, AUDIT_SENDER_RULE_CHANGED,
        f"{field.rule.pattern}: stopped mapping {field.label}",
    )
    db.session.delete(field)
    db.session.commit()
    return field


def _clean_label(label: str) -> str:
    label = " ".join((label or "").split()).rstrip(":").strip()
    if not label:
        raise SenderRuleError("Enter the label as it appears in the email.")
    return label[:200]


def _normalise(label: str) -> str:
    """Labels are compared loosely — case, spacing and a trailing colon are
    not things anyone should have to get right by hand."""
    return " ".join((label or "").split()).rstrip(":").strip().lower()


def parse_fields(body: str | None, labels, prose_labels=()) -> dict[str, str]:
    """Read `Label: value` blocks out of a form email.

    Returns `{normalised label: value}`, stripped at the ends.

    **Only the labels passed in are labels.** That's the whole design: a
    generic "anything before a colon" parser would cut a message in half at
    "Delivery: end of March", and there is no way to tell those apart from
    the text alone. Passing the rule's own mapping in means the studio has
    said what its form calls things, and everything else is prose.

    A value ends at the next mapped label, **or at a blank line** — because
    these emails put a blank line between fields and then carry on with a
    footer ("Manage Submissions", an unsubscribe link) that belongs to
    nothing. Without that, whichever field happens to be last swallows the
    footer, and "Google Search" stops matching the source option it names.

    `prose_labels` are the exception: a customer's own message is the one
    field that may legitimately contain blank lines, so those run on to the
    next label. Which is why mapping the label *after* the message matters —
    `Ignore` exists for exactly that (see FIELD_IGNORE).

    Leading whitespace is ignored, because a forwarded form arrives
    indented, and matching is case-insensitive.
    """
    wanted = {_normalise(label): label for label in labels if _normalise(label)}
    if not body or not wanted:
        return {}
    prose = {_normalise(label) for label in prose_labels}

    # Longest first, so "How did you hear about us?" wins over a shorter
    # label that happens to be a prefix of it.
    ordered = sorted(wanted, key=len, reverse=True)

    values: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        stripped = line.strip()
        matched = _match_label(stripped, ordered)
        if matched is not None:
            current, remainder = matched
            values[current] = [remainder] if remainder else []
        elif current is None:
            continue
        elif not stripped and current not in prose:
            current = None  # the field ended; what follows belongs to nobody
        else:
            values[current].append(stripped)

    return {
        label: "\n".join(lines).strip()
        for label, lines in values.items()
        if "\n".join(lines).strip()
    }


def _match_label(line: str, ordered_labels) -> tuple[str, str] | None:
    """(label, rest of the line) if this line opens one of the labels."""
    lowered = line.lower()
    for label in ordered_labels:
        if lowered.startswith(label) and line[len(label):].lstrip().startswith(":"):
            return label, line[len(label):].lstrip()[1:].strip()
    return None


def client_fields_from(rule, body: str | None) -> dict[str, str]:
    """What a rule's mapping says this email's body means.

    Returns keyword arguments for `create_client_from_thread` — so an
    unmapped rule returns `{}` and the caller falls back to the sender's
    own address, exactly as before mapping existed.
    """
    if not rule.fields:
        return {}

    parsed = parse_fields(
        body,
        [field.label for field in rule.fields],
        # Only the message may run across blank lines — see parse_fields.
        prose_labels=[f.label for f in rule.fields if f.target == FIELD_MESSAGE],
    )
    targets = {_normalise(field.label): field.target for field in rule.fields}

    result: dict[str, str] = {}
    for label, value in parsed.items():
        target = targets.get(label)
        if target is None or target == FIELD_IGNORE:
            continue
        if target == FIELD_NAME:
            first, last = _split_full_name(value)
            result.setdefault(FIELD_FIRST_NAME, first)
            result.setdefault(FIELD_LAST_NAME, last)
        else:
            result[target] = value
    return result


def _split_full_name(value: str) -> tuple[str, str]:
    """"Haejung Kim" -> ("Haejung", "Kim").

    First word first, everything else last — wrong for some names, and
    deliberately not clever about it: a guess that can be corrected in one
    click on the client page beats a heuristic nobody can predict.
    """
    parts = value.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def acknowledge_all(company_id: int) -> int:
    """Mark every automatically-created client as seen. Commits.

    Called when someone opens the client list — that's the whole clearing
    rule for the "new clients" badge, and it's a company-wide action for the
    same reason the lead badge is: one studio, one roster.
    """
    pending = AutoCreatedClient.query.filter_by(
        company_id=company_id, seen_at=None,
    ).all()
    if not pending:
        return 0
    now = utcnow()
    for row in pending:
        row.seen_at = now
    db.session.commit()
    return len(pending)


def unseen_client_count(company_id: int) -> int:
    """How many clients appeared without anyone deciding to create them."""
    return AutoCreatedClient.query.filter_by(company_id=company_id, seen_at=None).count()
