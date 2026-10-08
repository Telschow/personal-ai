"""Tests for the deterministic people/identity reference extractor."""

from __future__ import annotations

from personal_ai.documents.models import compute_document_id
from personal_ai.people.extract import (
    EMAIL_ROLE,
    FINANCIAL_ROLE,
    extract_person_references,
)
from personal_ai.sources.models import SourceRecord

CREATED_AT = "2026-01-01T10:00:00+00:00"


def make_email_record(
    *,
    sender: str = "Alice Anders <alice@example.com>",
    to: str = "Bob Berger <bob@example.com>, Dana <dana@example.com>",
    cc: str = "Claire <claire@example.com>",
    source_key: str = "msg-1@example.com",
    content_hash: str = "hash-email-1",
) -> SourceRecord:
    metadata: dict[str, object] = {
        "subject": "Hello",
        "sender": sender,
        "to": to,
        "message_id": "<msg-1@example.com>",
    }
    if cc:
        metadata["cc"] = cc
    return SourceRecord(
        source_type="email",
        source_key=source_key,
        content_hash=content_hash,
        created_at=CREATED_AT,
        modified_at=CREATED_AT,
        payload=b"Subject: Hello\n\nbody",
        metadata=metadata,
    )


def make_financial_record(
    *,
    payload: bytes = b"payer: Max Mustermann\npayee: Landlord GmbH\n",
    source_key: str = "2025-01",
    content_hash: str = "hash-fin-1",
) -> SourceRecord:
    return SourceRecord(
        source_type="financial",
        source_key=source_key,
        content_hash=content_hash,
        created_at=CREATED_AT,
        modified_at=CREATED_AT,
        payload=payload,
    )


def test_email_sender_to_cc_are_extracted_with_names() -> None:
    refs = extract_person_references(make_email_record())
    by_name = {ref.name: ref for ref in refs}
    assert by_name["Alice Anders"].email == "alice@example.com"
    assert by_name["Bob Berger"].email == "bob@example.com"
    assert by_name["Dana"].email == "dana@example.com"
    assert by_name["Claire"].email == "claire@example.com"
    for ref in refs:
        assert ref.role == EMAIL_ROLE
        assert ref.source_type == "email"
        assert ref.seen_at == CREATED_AT


def test_email_extract_uses_stable_document_id() -> None:
    record = make_email_record()
    expected = compute_document_id("email", record.source_key, record.content_hash)
    for ref in extract_person_references(record):
        assert ref.document_id == expected


def test_email_bare_addresses_without_display_name_are_skipped() -> None:
    record = make_email_record(
        sender="bare@example.com",
        to="<no-name@example.com>",
        cc="",
    )
    assert extract_person_references(record) == ()


def test_email_name_like_address_forms_are_skipped() -> None:
    record = make_email_record(
        sender="alice@example.com <alice@example.com>",
        to="List: <list@example.com>",
        cc="",
    )
    assert extract_person_references(record) == ()


def test_email_non_person_addresses_are_skipped() -> None:
    record = make_email_record(
        sender="MAILER-DAEMON <MAILER-DAEMON@example.com>",
        to="no-reply <no-reply@example.com>, noreply@example.com, "
        "do-not-reply@example.com, notifications@example.com, postmaster@example.com, "
        "bounce-123@example.com",
        cc="",
    )
    assert extract_person_references(record) == ()


def test_email_non_person_names_alone_are_kept_with_person_address() -> None:
    record = make_email_record(
        sender="Amy <no-reply@example.com>",
        to="Amy <amy@example.com>",
        cc="",
    )
    refs = extract_person_references(record)
    assert [ref.email for ref in refs] == ["amy@example.com"]


def test_email_duplicate_references_deduplicated_per_record() -> None:
    record = make_email_record(
        to="Bob Berger <bob@example.com>",
        cc="Bob Berger <bob@example.com>",
    )
    refs = extract_person_references(record)
    assert sum(1 for ref in refs if ref.name == "Bob Berger") == 1


def test_email_quoted_and_multiword_names_are_cleaned() -> None:
    record = make_email_record(
        sender='"Alice   Anders" <alice@example.com>',
        to="",
        cc="",
    )
    refs = extract_person_references(record)
    assert refs[0].name == "Alice Anders"
    assert refs[0].email == "alice@example.com"


def test_email_missing_headers_yield_empty() -> None:
    record = make_email_record(sender="", to="", cc="")
    assert extract_person_references(record) == ()


def test_financial_payer_payee_counterparty_extracted() -> None:
    payload = (
        b"payer: Max Mustermann\n"
        b"payee: TRADEREPUBLIC\n"
        b"counterparty_name: Netflix\n"
        b"amount: 10.0\n"
    )
    refs = extract_person_references(make_financial_record(payload=payload))
    names = {ref.name for ref in refs}
    assert names == {"Max Mustermann", "TRADEREPUBLIC", "Netflix"}
    for ref in refs:
        assert ref.role == FINANCIAL_ROLE
        assert ref.email == ""
        assert ref.source_type == "financial"


def test_financial_empty_values_and_other_lines_are_skipped() -> None:
    payload = b"payer: \npayee: \ncomment: someone the merchant\n"
    assert extract_person_references(make_financial_record(payload=payload)) == ()


def test_financial_duplicate_labels_deduplicated() -> None:
    payload = b"payer: Max Mustermann\npayee: Max Mustermann\n"
    refs = extract_person_references(make_financial_record(payload=payload))
    assert len(refs) == 1
    assert refs[0].name == "Max Mustermann"


def test_financial_no_payload_yields_empty() -> None:
    record = make_financial_record(payload=None)
    assert extract_person_references(record) == ()


def test_unsupported_source_type_yields_empty() -> None:
    record = SourceRecord(
        source_type="file",
        source_key="notes/x.md",
        content_hash="hash",
        created_at=CREATED_AT,
        modified_at=CREATED_AT,
        payload=b"ignore",
        metadata={},
    )
    assert extract_person_references(record) == ()
