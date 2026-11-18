from scripts.cleanup_business_data import BUSINESS_TABLES, PRESERVED_TABLES, _report


def test_cleanup_allowlists_are_disjoint_and_preserve_catalog() -> None:
    assert set(BUSINESS_TABLES).isdisjoint(PRESERVED_TABLES)
    assert set(PRESERVED_TABLES) == {
        "alembic_version", "knowledge_points", "questions",
    }
    assert {
        "school_classes", "students", "assignments", "paper_instances",
        "paper_instance_items", "submissions", "attempts", "attempt_revisions",
        "student_masteries", "student_literacy_scores", "student_knowledge_statuses",
        "recognition_runs", "recognition_items",
        "assessment_inputs", "assessment_tasks", "assessment_calls",
        "model_quality_feedback", "model_quality_feedback_revisions",
    } == set(BUSINESS_TABLES)


def test_cleanup_report_exposes_database_and_targets() -> None:
    report = _report(
        database="math_agent",
        user="math_agent",
        mode="preview",
        snapshot={"preserved": {}, "business": {}, "alembic_versions": ["0025"]},
        unclassified_tables=[],
    )
    assert report["mode"] == "preview"
    assert report["database"] == "math_agent"
    assert report["preserved_tables"] == list(PRESERVED_TABLES)
    assert report["business_tables_to_clear"] == list(BUSINESS_TABLES)
