"""Knowledge units (F1): a unit is shown only when every piece of its evidence is readable, and never without
evidence; and each kind's structure holds only its own keys, with its required ones, as short text."""
import pytest

from kl.units import check_structure, shown


def test_a_unit_is_shown_only_when_every_piece_of_its_evidence_is_readable():
    assert shown(["a", "b"], {"a", "b", "c"})
    assert not shown(["a", "b"], {"a"})
    assert not shown(["a", "b"], set())


def test_a_unit_without_evidence_is_never_shown():
    assert not shown([], {"a"})


def test_a_procedures_steps_keep_their_role_condition_and_form():
    got = check_structure("procedure", {"steps": [{"text": " Call the manager on duty ", "role": "Associate"}, {"text": "Fill in IR-7", "form": "IR-7"}]})
    assert got == {"steps": [{"text": "Call the manager on duty", "role": "Associate"}, {"text": "Fill in IR-7", "form": "IR-7"}]}


@pytest.mark.parametrize("kind, structure", [
    ("procedure", {"steps": []}),                              # no steps
    ("procedure", {"steps": [{"role": "Associate"}]}),         # a step without its text
    ("procedure", {"steps": [{"text": "x", "owner": "y"}]}),   # a key a step doesn't hold
    ("procedure", {"steps": [{"text": "x"}] * 101}),           # too many steps
    ("contact", {"reach": "555-0170"}),                        # a contact without its role
    ("definition", {"term": "Sell-through", "unit": "%"}),     # a key a definition doesn't hold
    ("fact", {"anything": "x"}),                               # a fact holds no structure
    ("decision", {"decided_on": "last Tuesday"}),              # not a date
    ("contact", {"role": ""}),                                 # empty text
    ("contact", {"role": "x" * 501}),                          # too long
    ("contact", {"role": 7}),                                  # not text
])
def test_a_structure_its_kind_doesnt_allow_is_refused(kind, structure):
    with pytest.raises(ValueError):
        check_structure(kind, structure)


def test_any_kind_may_hold_no_structure_and_a_decisions_date_is_normalized():
    assert check_structure("policy", {}) == {}
    assert check_structure("decision", {"decided_by": "Kai Moreno", "decided_on": "2026-05-11"}) == {"decided_by": "Kai Moreno", "decided_on": "2026-05-11"}
