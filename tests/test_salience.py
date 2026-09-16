"""The salience gate, on the exact junk E0 measured (experiments/RESULTS.jsonl).

A 90-day companion history produced 37 facts, 21 of them needed by no question:
seven phrasings of one intention, five of finishing a book, facts about the
conversation itself, relation echoes on the other entity. The gate must drop
those and must not merge two facts that differ in a value.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud import salience
from cloud.salience import gate, same_fact, reason_to_drop


PLUMBER = ["wants to call a plumber", "wants to call the plumber", "asked to be reminded to call the plumber",
           "planned to call the plumber", "wants a reminder to call the plumber", "needs to call the plumber",
           "needs a reminder to call the plumber", "plans to call the plumber", "has a task to call the plumber"]
BOOK = ["finished the book", "finished reading a book", "finished a book"]


def test_one_intention_is_one_fact():
    kept, dropped = gate(PLUMBER, entity="Ali")
    assert kept == ["wants to call a plumber"]
    assert all(d["reason"].startswith("same as:") for d in dropped) and len(dropped) == 8


def test_a_rephrasing_of_an_existing_fact_is_not_written_again():
    kept, dropped = gate(["needs to call the plumber", "finished reading a book"],
                         existing=["wants to call the plumber", "finished the book"], entity="Ali")
    assert kept == [] and len(dropped) == 2


def test_facts_that_differ_in_a_value_are_both_kept():
    pairs = [("is allergic to kiwi", "is allergic to gluten"),
             ("birthday is in March", "has a birthday in June"),
             ("has a dog called Pixel", "has a cat called Pixel"),
             ("is an architect", "is a chef"),
             ("has a goal this year of running a 10K under 50 minutes", "is training for a 5K"),
             ("always orders an oat latte", "tried a mocha and did not like it"),
             ("has played the bass since school", "started guitar lessons"),
             ("moved to Bergen last spring", "moved to Riga last spring"),
             ("lives at 12 Elm Street", "lives at 14 Elm Street")]
    for a, b in pairs:
        assert not same_fact(a, b), (a, b)
        assert gate([a, b], entity="Ali")[0] == [a, b]


def test_every_needed_fact_from_e0_passes_the_gate():
    needed = ["is allergic to kiwi", "birthday is in March", "has a goal this year of running a 10K under 50 minutes",
              "has a sister who moved to Bergen last spring", "has played the bass since school",
              "always orders an oat latte", "has a dog called Pixel", "is an architect", "moved to Bergen last spring",
              "is allergic to kiwi and does not want it suggested", "uses PostgreSQL 16 in production",
              "order #48213 was refunded on 2026-03-02", "prefers dark mode", "works at Zalando as a data engineer"]
    for f in needed:
        assert reason_to_drop(f, "Ali") is None, f
    assert gate(needed, entity="Ali")[0] == needed


def test_conversation_talk_and_questions_are_not_memory():
    for f in ["mentioned finishing the same book multiple times",
              "finished reading the same book again in the conversation",
              "planned to recommend or watch a film tonight",
              "asks for stretches to do after running",
              "runs"]:
        assert reason_to_drop(f, "Ali") is not None, f


def test_relation_echo_on_the_other_entity_is_dropped_but_its_own_facts_stay():
    assert reason_to_drop("is where User's sister moved last spring", "Bergen")
    assert reason_to_drop("is the User's dog", "Pixel")
    assert reason_to_drop("belongs to the User's neighbour", "Toby")
    assert reason_to_drop("keeps barking at night", "Toby") is None
    assert reason_to_drop("is a city in Norway", "Bergen") is None


def test_assistant_log_lines_are_dropped_but_standing_preferences_stay():
    assert reason_to_drop("confirmed the tests were green", "Assistant")
    assert reason_to_drop("plans to refactor the parser next", "Assistant")
    assert reason_to_drop("prefers concise answers", "Assistant") is None
    assert reason_to_drop("confirmed the tests were green", "Ali") is None  # a person may confirm things


def test_gate_is_on_unless_switched_off(monkeypatch):
    monkeypatch.delenv("MENGRAM_SALIENCE_GATE", raising=False)
    assert salience.enabled()
    monkeypatch.setenv("MENGRAM_SALIENCE_GATE", "0")
    assert not salience.enabled()
    monkeypatch.setenv("MENGRAM_SALIENCE_GATE", "1")
    assert salience.enabled()


def test_second_round_of_e0_leftovers():
    """What an L run with the first gate still stored (2026-09-16 01:20)."""
    for f in ["feels tired", "is tired", "feeling tired", "had a pretty good day", "has a good day",
              "mentioned the weather looks grim", "noted the weather looks grim", "commented on the grim weather",
              "observed grim weather", "mentioned grey skies all week according to the forecast",
              "interested in film recommendations", "seeking film recommendations",
              "wants a film recommendation for tonight", "asked for a good stretch after running"]:
        assert reason_to_drop(f, "Ali") is not None, f
    assert reason_to_drop("is the dog of User", "Pixel")
    kept, _ = gate(["is tired", "feels tired", "feeling tired"], entity="Ali")
    assert kept == []
    # states that are not passing
    for f in ["is diabetic", "is a vegetarian", "has a long commute to Oslo", "feels strongly about privacy",
              "is good at chess", "was born in Riga"]:
        assert reason_to_drop(f, "Ali") is None, f
