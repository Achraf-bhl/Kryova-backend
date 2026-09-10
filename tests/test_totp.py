"""RFC 6238 arithmetic, and the three guards that are not in the RFC.

No database and no clock: every function under test takes `now`, which is the
whole reason the replay guard is testable at all.
"""

from __future__ import annotations

import base64

import pytest

from app.core.security import decrypt_at_rest, encrypt_at_rest
from app.core.totp import (
    DIGITS,
    RECOVERY_CODE_COUNT,
    STEP_SECONDS,
    code_at,
    consume,
    generate_recovery_codes,
    generate_secret,
    matching_step,
    normalise_recovery_code,
    provisioning_uri,
    step_for,
)

#: The RFC 6238 appendix-B seed for SHA-1: the ASCII string "12345678901234567890".
#: Published as hex there; base32 is what this implementation takes.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")

#: Appendix B's table, SHA-1 rows only, truncated to the six digits this
#: implementation emits (the RFC prints eight).
RFC_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
    (20000000000, "353130"),
]


class TestTheRfcVectors:
    """If these pass, the algorithm is right. They are not our numbers."""

    @pytest.mark.parametrize(("unix_time", "expected"), RFC_VECTORS)
    def test_the_published_vectors_reproduce(self, unix_time: int, expected: str) -> None:
        assert code_at(RFC_SECRET, step_for(unix_time)) == expected

    def test_a_step_is_thirty_seconds_wide(self) -> None:
        assert step_for(0) == step_for(29) != step_for(30)
        assert step_for(STEP_SECONDS) == 1


class TestASecret:
    def test_a_generated_secret_is_unpadded_base32_an_app_will_accept(self) -> None:
        secret = generate_secret()
        assert "=" not in secret
        assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
        # It must survive a round trip through our own decoder.
        assert len(code_at(secret, 1)) == DIGITS

    def test_two_secrets_are_never_the_same(self) -> None:
        assert len({generate_secret() for _ in range(50)}) == 50

    def test_a_secret_retyped_in_lowercase_with_spaces_still_works(self) -> None:
        # People type these off a screen when the QR code will not scan.
        spaced = " ".join(RFC_SECRET[i : i + 4] for i in range(0, len(RFC_SECRET), 4))
        assert code_at(spaced.lower(), 100) == code_at(RFC_SECRET, 100)

    def test_something_that_is_not_base32_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="base32"):
            code_at("not a secret!", 1)


class TestTheProvisioningUri:
    def test_the_issuer_appears_both_as_a_prefix_and_as_a_parameter(self) -> None:
        # Older apps read only the label prefix, newer ones only the parameter.
        # An app that reads neither files the account under a bare email
        # address with no clue what service it belongs to.
        uri = provisioning_uri(secret="ABCD", account="eng@kryova.dev")
        assert uri.startswith("otpauth://totp/Kryova%3Aeng%40kryova.dev?")
        assert "issuer=Kryova" in uri

    def test_the_parameters_an_app_needs_are_all_present(self) -> None:
        uri = provisioning_uri(secret="ABCD", account="a@b.c")
        for parameter in ("secret=ABCD", "algorithm=SHA1", "digits=6", "period=30"):
            assert parameter in uri


class TestClockSkewIsTolerated:
    def test_the_code_from_the_previous_step_is_still_accepted(self) -> None:
        now = 1_700_000_000.0
        previous = code_at(RFC_SECRET, step_for(now) - 1)
        assert matching_step(RFC_SECRET, previous, now=now) == step_for(now) - 1

    def test_the_code_from_the_next_step_is_accepted(self) -> None:
        now = 1_700_000_000.0
        ahead = code_at(RFC_SECRET, step_for(now) + 1)
        assert matching_step(RFC_SECRET, ahead, now=now) == step_for(now) + 1

    def test_two_steps_away_is_refused(self) -> None:
        now = 1_700_000_000.0
        stale = code_at(RFC_SECRET, step_for(now) - 2)
        assert matching_step(RFC_SECRET, stale, now=now) is None

    def test_something_that_is_not_six_digits_is_refused_without_hashing(self) -> None:
        now = 1_700_000_000.0
        for rubbish in ("", "12345", "1234567", "abcdef", "12 34 56 78"):
            assert matching_step(RFC_SECRET, rubbish, now=now) is None

    def test_a_code_typed_with_a_space_in_the_middle_is_accepted(self) -> None:
        # Authenticator apps display "123 456"; people copy what they see.
        now = 1_700_000_000.0
        code = code_at(RFC_SECRET, step_for(now))
        assert matching_step(RFC_SECRET, f"{code[:3]} {code[3:]}", now=now) == step_for(now)


class TestACodeWorksExactlyOnce:
    """The replay guard. Not in the RFC, and the difference between a real
    second factor and a decorative one."""

    def test_a_correct_code_is_accepted_when_nothing_has_been_used(self) -> None:
        now = 1_700_000_000.0
        code = code_at(RFC_SECRET, step_for(now))
        assert consume(RFC_SECRET, code, now=now, last_step=None) == step_for(now)

    def test_the_same_code_is_refused_the_second_time(self) -> None:
        # 90 seconds of validity means a code read over a shoulder, or off a
        # real-time phishing page, is replayable for that window unless the
        # step is burned.
        now = 1_700_000_000.0
        code = code_at(RFC_SECRET, step_for(now))
        first = consume(RFC_SECRET, code, now=now, last_step=None)
        assert first is not None
        assert consume(RFC_SECRET, code, now=now, last_step=first) is None

    def test_an_older_step_is_refused_even_though_it_is_arithmetically_correct(
        self,
    ) -> None:
        # Within the skew window the previous step's code still validates. If
        # the current one has already been spent, accepting it would reopen the
        # window that was just closed.
        now = 1_700_000_000.0
        current = step_for(now)
        previous_code = code_at(RFC_SECRET, current - 1)
        assert matching_step(RFC_SECRET, previous_code, now=now) == current - 1
        assert consume(RFC_SECRET, previous_code, now=now, last_step=current) is None

    def test_the_next_step_is_accepted_after_the_current_one_was_burned(self) -> None:
        # Burning a step must not lock the user out for good, only for that
        # step. Thirty seconds later they sign in again.
        now = 1_700_000_000.0
        current = step_for(now)
        later = now + STEP_SECONDS
        assert consume(RFC_SECRET, code_at(RFC_SECRET, current + 1), now=later,
                       last_step=current) == current + 1


class TestRecoveryCodes:
    def test_ten_are_issued_and_all_are_different(self) -> None:
        codes = generate_recovery_codes()
        assert len(codes) == RECOVERY_CODE_COUNT
        assert len(set(codes)) == RECOVERY_CODE_COUNT

    def test_a_code_is_grouped_for_transcription_but_hashes_without_the_grouping(
        self,
    ) -> None:
        # Case and the hyphen carry no information, and somebody reading one off
        # paper will get at least one of them wrong. Normalising has to happen
        # before hashing -- afterwards the two values differ completely.
        code = generate_recovery_codes(1)[0]
        assert "-" in code
        assert normalise_recovery_code(code.upper()) == normalise_recovery_code(code)
        assert normalise_recovery_code(f" {code.replace('-', '')} ") == (
            normalise_recovery_code(code)
        )


class TestTheSecretIsEncryptedAtRest:
    def test_a_secret_round_trips(self) -> None:
        secret = generate_secret()
        assert decrypt_at_rest(encrypt_at_rest(secret)) == secret

    def test_the_ciphertext_does_not_contain_the_secret(self) -> None:
        secret = generate_secret()
        assert secret not in encrypt_at_rest(secret)

    def test_encrypting_the_same_secret_twice_gives_different_ciphertext(self) -> None:
        # A fresh nonce per call. Without it, equal secrets are visibly equal in
        # the database, which leaks that two accounts share one.
        secret = generate_secret()
        assert encrypt_at_rest(secret) != encrypt_at_rest(secret)

    def test_a_tampered_ciphertext_reads_back_as_nothing_rather_than_as_garbage(
        self,
    ) -> None:
        # AES-GCM is authenticated: a flipped byte fails the tag rather than
        # decrypting to a different secret, which would silently validate
        # nobody's codes.
        sealed = encrypt_at_rest("ABCDEFGH")
        version, nonce, body = sealed.split(":", 2)
        flipped = f"{version}:{nonce}:{'A' if body[0] != 'A' else 'B'}{body[1:]}"
        assert decrypt_at_rest(flipped) is None

    def test_an_unversioned_or_malformed_envelope_reads_back_as_nothing(self) -> None:
        assert decrypt_at_rest("not-an-envelope") is None
        assert decrypt_at_rest("v9:AAAA:BBBB") is None

    def test_a_rotated_secret_key_makes_the_secret_unreadable_rather_than_wrong(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The realistic operational event. The caller's honest response is "ask
        # the user to re-enrol", which needs None rather than an exception.
        from app.core.config import settings

        sealed = encrypt_at_rest("ABCDEFGH")
        monkeypatch.setattr(settings, "secret_key", "a-completely-different-key-value")
        assert decrypt_at_rest(sealed) is None
