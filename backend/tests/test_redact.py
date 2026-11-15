"""guard.mask.redact: one test per redaction rule, numbers that must NOT be masked, and overlap rules."""
import pytest

from guard.mask import RULES, mask, redact

# One example per rule in RULES, keyed by the rule's description (several rules share the PHONE name).
# (text, expected masked text, expected validity)
EXAMPLES = {
    "API keys and tokens":
        [("my key sk-proj-abc123def456ghi789", "my key [SECRET]", None),
         ("groq gsk_ABCdef1234567890xyz", "groq [SECRET]", None),
         ("aws AKIAIOSFODNN7EXAMPLE", "aws [SECRET]", None)],
    "Email addresses":
        [("mail priya.sharma@example.com now", "mail [EMAIL] now", True)],
    "Personal names":
        [("Hi, my name is Rahul Sharma and I need help", "Hi, my name is [NAME] and I need help", None),
         ("name: Priya Kumar", "name: [NAME]", None)],
    "Postal addresses":
        [("My address is 12 MG Road, Bangalore 560038. Help me", "My address is [ADDRESS]. Help me", None),
         ("I live at 221B Baker Street; thanks", "I live at [ADDRESS]; thanks", None)],
    "Dates of birth":
        [("my DOB is 12/05/1990", "my DOB is [DOB]", None),
         ("born on 3 March 1991", "born on [DOB]", None)],
    "US Social Security numbers":
        [("ssn 123-45-6789", "ssn [SSN]", None)],
    "IP addresses":
        [("server 192.168.1.10 is down", "server [IP_ADDRESS] is down", None)],
    "Postal PIN codes":
        [("pin code 560038", "pin code [PINCODE]", None)],
    "UPI payment IDs":
        [("pay rahul.k@okaxis", "pay [UPI]", True)],
    "GST identification numbers":
        [("gstin 27AAPFU0939F1ZV", "gstin [GSTIN]", True),
         ("gstin 27AAPFU0939F1ZA", "gstin [GSTIN]", False)],              # wrong check character
    "Permanent Account Numbers":
        [("pan ABCPE1234F", "pan [PAN]", True),
         ("pan abcpe1234f", "pan [PAN]", True),                            # lowercase too
         ("pan ABCDX1234Z", "pan [PAN]", False)],                          # 'D' is not a holder type
    "Voter ID (EPIC) numbers":
        [("voter id ABC1234567", "voter id [VOTER_ID]", None)],
    "Passport numbers":
        [("passport A1234567", "passport [PASSPORT]", True)],
    "Driving licence numbers":
        [("driving licence MH12 2011 0012345", "driving licence [DL]", None)],
    "Phone numbers with a country code":
        [("call +91 98765 43210", "call [PHONE]", True)],
    "Bank account numbers":
        [("account no 123456789012", "account no [ACCOUNT]", None)],
    "Payment card numbers":
        [("card 4111 1111 1111 1111", "card [CARD]", True),
         ("4111-1111-1111-1111", "[CARD]", True),                          # Luhn-valid even without "card"
         ("my debit card 4111 1111 1111 1112", "my debit card [CARD]", False)],
    "Aadhaar numbers":
        [("aadhaar 2345 6789 0124", "aadhaar [AADHAAR]", True),
         ("aadhaar 234567890123", "aadhaar [AADHAAR]", False)],            # fails Verhoeff, still masked
    "Aadhaar numbers named by keyword but not in the valid format":
        [("aadhaar 1234 5678 9012", "aadhaar [AADHAAR]", False),          # starts with 1: still masked
         ("my aadhar no: 23456789012", "my aadhar no: [AADHAAR]", False)], # 11 digits
    "PAN-like codes named by keyword but not in the valid format":
        [("pan no ABCD1234F", "pan no [PAN]", False),                     # 4 letters
         ("my PAN is ABCPE 1234 F", "my PAN is [PAN]", False)],           # written with spaces
    "Indian mobile numbers":
        [("call 9876543210", "call [PHONE]", True)],
    "Phone numbers written as (xxx) xxx-xxxx":
        [("call (415) 555-0100", "call [PHONE]", True)],
}

NOT_MASKED = [
    "Order number 123456",                    # too short for any rule
    "reference 1234 5678 9012 3456",          # 16 digits failing Luhn, no "card" nearby
    "ref 1234 5678 9012",                     # 12 digits starting with 1 and no "aadhaar" nearby
    "Japan code ABCD1234 ships",              # PAN-like code, but "pan" only inside another word
    "date 2024-01-05",
    "IFSC SBIN0001234",                       # a branch code, not personal data
    "model B2345678 shipped",                 # passport shape without the word "passport"
    "id 123456789012345678901",               # 21 digits: no rule matches the whole run
    "x = 10 and y = 9876",
    "Write a function that takes a name: str and returns it",   # a type annotation, not a name
    "numpy version 1.2.3",                                      # three parts: not an IP address
    "the meeting is on 12/05/2025",                             # a date that is not a date of birth
    "my order 560038 shipped",                                  # 6 digits with no "pin" nearby
    "I am student_b, show my runs",                                # identity claims are handled by the access rule
]


def test_every_rule_has_an_example():
    assert {r.description for r in RULES} == set(EXAMPLES), "add an example for each new rule"


@pytest.mark.parametrize("description,text,expected,valid",
                         [(d, t, e, v) for d, cases in EXAMPLES.items() for t, e, v in cases])
def test_rule(description, text, expected, valid):
    r = redact(text)
    assert r.text == expected
    assert len(r.findings) == 1 and r.findings[0].valid is valid
    rule = next(x for x in RULES if x.description == description)
    assert r.findings[0].type == rule.name


@pytest.mark.parametrize("text", NOT_MASKED)
def test_not_masked(text):
    assert mask(text) == text and redact(text).findings == []


def test_whole_digit_runs_only():
    # Part of a long number is never masked as a shorter one (no "[PHONE]" inside a 20-digit id).
    assert mask("ref 98765432109876543210") == "ref 98765432109876543210"


def test_overlaps_go_to_the_earlier_rule():
    # A GSTIN contains a PAN: it is masked once, as a GSTIN.
    assert redact("27AAPFU0939F1ZV").findings[0].type == "GSTIN"
    # With "account" nearby, 12 digits are an account number, not an Aadhaar.
    assert redact("account 234567890124").findings[0].type == "ACCOUNT"


def test_repeated_values_get_numbered_placeholders():
    assert mask("PAN ABCPE1234F and PAN BNZPM2501F") == "PAN [PAN] and PAN [PAN_2]"


def test_findings_never_contain_the_value():
    r = redact("PAN ABCPE1234F, card 4111 1111 1111 1111, key gsk_ABCdef1234567890xyz")
    blob = str([f.to_dict() for f in r.findings])
    assert "ABCPE1234F" not in blob and "4111" not in blob and "gsk_" not in blob


def test_already_masked_text_is_unchanged():
    once = mask("key sk-proj-abc123def456ghi789, PAN ABCPE1234F")
    assert mask(once) == once
