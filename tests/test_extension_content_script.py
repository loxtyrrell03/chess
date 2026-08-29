from pathlib import Path


CONTENT_SCRIPT = Path(__file__).parents[1] / "extension" / "content-script.js"


def test_lichess_scan_does_not_walk_every_round_descendant() -> None:
    source = CONTENT_SCRIPT.read_text(encoding="utf-8")

    assert 'queryAllSafe(document, ".round__app *")' not in source
    assert "MAX_MOVE_LIST_CANDIDATES" in source
    assert 'queryAllSafe(root, "z7yx")' in source


def test_mutation_observer_filters_unrelated_page_churn() -> None:
    source = CONTENT_SCRIPT.read_text(encoding="utf-8")

    assert "function mutationAffectsChessState" in source
    assert "mutations.some(mutationAffectsChessState)" in source
    assert "new MutationObserver(scheduleScan)" not in source
    assert "const trackedObserver" in source
    assert "subtree: true,\n      childList: true\n    });" in source
    assert "data-chess-trainer-owned" in source
