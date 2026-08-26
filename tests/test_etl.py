"""Unit tests for eval-etl.py's deterministic task_type classifier."""
import eval_etl as e


def test_classify_task_type_research():
    assert e.classify_task_type("Research the API rate limits") == "research"


def test_classify_task_type_code():
    assert e.classify_task_type("Fix the failing pytest test") == "code"


def test_classify_task_type_memory():
    assert e.classify_task_type("Recall the project fact from memory") == "memory"


def test_classify_task_type_cron():
    assert e.classify_task_type("List the current cron jobs") == "cron_check"


def test_classify_task_type_file_ops():
    assert e.classify_task_type("Write a file to the output dir") == "file_ops"


def test_classify_task_type_delegation():
    assert e.classify_task_type("Use delegate_task to spawn a subagent") == "delegation"


def test_classify_task_type_other():
    assert e.classify_task_type("hello world") == "other"
