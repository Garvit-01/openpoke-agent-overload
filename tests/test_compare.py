"""evals/compare.py: each row must really switch its approach, and the judge must not switch with it."""

from evals import compare, harness, scenarios
from server.config import get_settings
from server.services.execution import entities


def test_rows_switch_every_setting_and_restore_them():
    before = compare.current_settings()
    try:
        all_three = next(values for key, _, values in compare.CONFIGS if key == "all")
        compare.apply_settings({**compare.OFF, **all_three})
        settings = get_settings()
        assert entities.FOLD_PLURALS and settings.agent_name_hints and settings.agent_index_requests
        assert settings.agent_retriever == "hybrid"
        compare.apply_settings(compare.OFF)
        assert not entities.FOLD_PLURALS and not settings.agent_name_hints and not settings.agent_index_requests
        assert settings.agent_retriever == "bm25"
        assert entities.tokenize("grocery lists") == ["grocery", "lists"]
    finally:
        compare.apply_settings(before)
    assert compare.current_settings() == before


def test_simulated_router_reads_words_as_written_whatever_the_setting():
    # If plural folding also changed the judge, a row would measure the judge, not the search
    before = compare.current_settings()
    try:
        for fold in ("0", "1"):
            compare.apply_settings({**compare.OFF, "OPENPOKE_FOLD_PLURALS": fold})
            assert harness.tokenize("the grocery lists") == ["grocery", "lists"]
            assert scenarios.tokenize("invoices") == ["invoices"]
    finally:
        compare.apply_settings(before)


def test_every_row_is_main_plus_named_switches():
    keys = [key for key, _, _ in compare.CONFIGS]
    assert keys[0] == "main" and compare.CONFIGS[0][2] == {}
    for _, _, values in compare.CONFIGS:
        assert set(values) <= set(compare.OFF)


def test_real_model_comparison_reads_the_files_run_eval_writes():
    from evals import compare_llm

    path = compare_llm.result_path("anthropic/claude-haiku-5.5", "paraphrase", "embeddings", hybrid=True)
    assert path.name == "llm_results_anthropic-claude-haiku-5-5_paraphrase_phrases_hybrid_cmp_embeddings.json"
    path = compare_llm.result_path("anthropic/claude-haiku-5.5", "generated", "main", hybrid=False)
    assert path.name == "llm_results_anthropic-claude-haiku-5-5_phrases_cmp_main.json"


def test_app_searches_by_meaning_by_default_but_evals_use_keywords():
    # The app turns meaning search on (it falls back to keywords without fastembed); the eval
    # defaults to keywords so it runs anywhere and reproduces the committed result files
    import os
    import subprocess
    import sys
    from pathlib import Path

    from evals.run_eval import parse_args

    # A fresh process without the setting, since other tests change the environment
    env = {key: value for key, value in os.environ.items() if key != "OPENPOKE_AGENT_RETRIEVER"}
    code = "from server.config import Settings; print(Settings().agent_retriever)"
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True)
    assert result.stdout.strip() == "hybrid", result.stderr
    assert parse_args([]).retriever == "bm25"
    assert parse_args(["--retriever", "hybrid"]).retriever == "hybrid"
