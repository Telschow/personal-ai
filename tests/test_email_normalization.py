"""Tests for the deterministic email body normalization helpers."""

from personal_ai.sources.email_normalization import (
    fallback_message_key,
    html_text_with_breaks,
    normalize_email_body,
    normalize_message_id,
    strip_quoted_tail,
    strip_trailing_signature,
)


class TestNormalizeMessageId:
    def test_removes_angle_brackets(self) -> None:
        assert normalize_message_id("<abc@example.com>") == "abc@example.com"

    def test_lowercases_domain_only(self) -> None:
        assert normalize_message_id("<AbC@Example.COM>") == "AbC@example.com"

    def test_preserves_local_part_case(self) -> None:
        assert (
            normalize_message_id("<UPPER.Local@example.com>")
            == "UPPER.Local@example.com"
        )

    def test_folds_whitespace(self) -> None:
        assert normalize_message_id("\n\t< id@example.com > \n") == "id@example.com"

    def test_without_angle_brackets(self) -> None:
        assert normalize_message_id("abc@example.com") == "abc@example.com"

    def test_deterministic(self) -> None:
        assert normalize_message_id("<x@Y.example>") == normalize_message_id(
            "<x@Y.example>"
        )

    def test_none_and_empty(self) -> None:
        assert normalize_message_id(None) == ""
        assert normalize_message_id("") == ""

    def test_malformed_string_is_preserved(self) -> None:
        assert normalize_message_id("not-an-id") == "not-an-id"


class TestFallbackMessageKey:
    def test_is_prefixed_with_noid(self) -> None:
        assert fallback_message_key(b"payload").startswith("noid/")

    def test_same_payload_same_key(self) -> None:
        assert fallback_message_key(b"same body") == fallback_message_key(b"same body")

    def test_different_payloads_different_keys(self) -> None:
        assert fallback_message_key(b"one") != fallback_message_key(b"two")

    def test_str_and_bytes_equivalent(self) -> None:
        assert fallback_message_key("abc") == fallback_message_key(b"abc")

    def test_deterministic(self) -> None:
        assert fallback_message_key(b"abc") == fallback_message_key(b"abc")


class TestStripQuotedTail:
    def test_plain_quote_block(self) -> None:
        text = "Real content\n\n> quoted old message\n> more quoted\n"
        assert strip_quoted_tail(text) == "Real content"

    def test_quote_in_middle_is_preserved(self) -> None:
        text = "First thought\n> intermediate quote\nConcluding thought"
        assert strip_quoted_tail(text) == text

    def test_on_wrote_separator(self) -> None:
        text = (
            "Hello there\n\n"
            "On Tue, Jan 1 2026 at 9:00 AM, Someone <s@example.com> wrote:\n"
            "> old conversation\n> more of it"
        )
        assert strip_quoted_tail(text) == "Hello there"

    def test_nested_on_wrote_separator(self) -> None:
        text = "Reply body\n\n> On Tue, Jan 1 2026 wrote:\n> > nested quote"
        assert strip_quoted_tail(text) == "Reply body"

    def test_original_message_separator(self) -> None:
        text = (
            "Current text\n\n"
            "-----Original Message-----\n"
            "From: someone@example.com\n"
            "Sent: Tuesday\n"
            "To: me@example.com\n"
            "Subject: Old thread\n"
            "Date: Monday\n\n"
            "Original body"
        )
        assert strip_quoted_tail(text) == "Current text"

    def test_forwarded_message_separator(self) -> None:
        text = (
            "Check this out\n\n"
            "---------- Forwarded message ----------\n"
            "From: forward@example.com\n"
            "Forwarded body"
        )
        assert strip_quoted_tail(text) == "Check this out"

    def test_separator_opening_body_is_preserved(self) -> None:
        text = "-----Original Message-----\nFrom: x@example.com\nBody"
        assert strip_quoted_tail(text) == text

    def test_all_quoted_body_is_preserved(self) -> None:
        text = "> one\n> two"
        assert strip_quoted_tail(text) == text

    def test_empty_and_blank_text(self) -> None:
        assert strip_quoted_tail("") == ""
        assert strip_quoted_tail("   ") == "   "

    def test_deterministic(self) -> None:
        text = "Body\n\nOn Tue wrote:\n> q"
        assert strip_quoted_tail(text) == strip_quoted_tail(text)


class TestStripTrailingSignature:
    def test_removes_signature_after_delimiter(self) -> None:
        text = "Actual email body\n\n--\nBest,\nAlice"
        assert strip_trailing_signature(text) == "Actual email body"

    def test_delimiter_with_trailing_space(self) -> None:
        assert strip_trailing_signature("Body\n\n-- \nName") == "Body"

    def test_more_than_six_trailing_lines_is_preserved(self) -> None:
        text = "Body\n\n--\nA\nB\nC\nD\nE\nF\nG"
        assert strip_trailing_signature(text) == text

    def test_delimiter_in_middle_is_preserved(self) -> None:
        text = (
            "Opening thought\n"
            "--\n"
            "This continues the thought.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
            "And keeps going.\n"
        )
        assert strip_trailing_signature(text) == text

    def test_delimiter_far_from_end_is_preserved(self) -> None:
        text = "Body start\n\n--\n" + "filler line\n" * 20 + "end"
        assert strip_trailing_signature(text) == text

    def test_no_delimiter_is_unchanged(self) -> None:
        text = "Body without signature\nKeep me."
        assert strip_trailing_signature(text) == text

    def test_deterministic(self) -> None:
        text = "Body\n\n--\nJane"
        assert strip_trailing_signature(text) == strip_trailing_signature(text)


class TestHtmlTextWithBreaks:
    def test_paragraphs_become_two_lines(self) -> None:
        result = html_text_with_breaks("<p>First</p><p>Second</p>")
        assert result == "\nFirst\n\nSecond\n"

    def test_br_break(self) -> None:
        result = html_text_with_breaks("First<br>Second")
        assert result == "First\nSecond"

    def test_list_items(self) -> None:
        result = html_text_with_breaks("<ul><li>one</li><li>two</li></ul>")
        assert result.count("one") == 1
        assert "one\n" in result
        assert "two" in result

    def test_table_rows(self) -> None:
        result = html_text_with_breaks(
            "<table><tr><td>a</td></tr><tr><td>b</td></tr></table>"
        )
        assert "a" in result
        assert "b" in result
        assert result.count("\n") >= 2

    def test_headings(self) -> None:
        result = html_text_with_breaks("<h1>Title</h1><h2>Section</h2>")
        assert result == "\nTitle\n\nSection\n"

    def test_script_and_style_removed(self) -> None:
        result = html_text_with_breaks(
            "<style>.x {}</style><p>Keep me</p><script>var y = 1;</script>"
        )
        assert "Keep me" in result
        assert ".x" not in result
        assert "var y" not in result

    def test_deterministic(self) -> None:
        html = "<p>a</p><p>b</p>"
        assert html_text_with_breaks(html) == html_text_with_breaks(html)


class TestNormalizeEmailBody:
    def test_crlf_normalized(self) -> None:
        assert normalize_email_body("line one\r\nline two\r\n") == "line one\nline two"

    def test_blank_runs_collapsed(self) -> None:
        assert normalize_email_body("a\n\n\n\n\nb\n") == "a\n\nb"

    def test_trimmed(self) -> None:
        assert normalize_email_body("  \ncontent\n  ") == "content"

    def test_internal_whitespace_preserved(self) -> None:
        assert normalize_email_body("a  b\n   indented") == "a  b\n   indented"

    def test_preserves_single_blank_lines(self) -> None:
        assert normalize_email_body("one\n\ntwo") == "one\n\ntwo"

    def test_strips_quoted_tail(self) -> None:
        assert normalize_email_body("Body\n\n> quoted") == "Body"

    def test_strips_signature(self) -> None:
        assert normalize_email_body("Body\n\n--\nJane") == "Body"

    def test_combined_quote_then_signature(self) -> None:
        text = "Real notes\n\nOn Mon wrote:\n> old\n\n--\nJane"
        assert normalize_email_body(text) == "Real notes"

    def test_deterministic(self) -> None:
        text = "Body\r\n\r\n> q\r\n"
        assert normalize_email_body(text) == normalize_email_body(text)
