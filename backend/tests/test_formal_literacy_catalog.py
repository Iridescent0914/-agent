"""The formal taxonomy follows the graph, independently of legacy scores."""

from copy import deepcopy

import pytest

from app.services import knowledge_catalog


def test_formal_literacies_follow_the_nine_html_dimensions():
    dimensions = knowledge_catalog.formal_literacy_dimensions()
    assert dimensions == (
        "抽象能力", "运算能力", "几何直观", "空间观念", "推理能力",
        "数据观念", "模型观念", "应用意识", "创新意识",
    )
    catalog = knowledge_catalog.load_catalog()
    assert list(dimensions) == catalog["html_core_competencies"]
    assert all(
        set(point["core_competencies"]) <= set(dimensions)
        for chapter in catalog["chapters"]
        for point in chapter["knowledge_points"]
    )


def test_assignment_filters_use_formal_names_and_reject_primary_school_alias():
    from app.schemas.assignments import PersonalizedQuestionFilter, RequiredQuestionFilter
    for name in ("空间观念", "数据观念", "创新意识"):
        assert RequiredQuestionFilter(core_literacies=[name, name]).core_literacies == [name]
        assert PersonalizedQuestionFilter(knowledge_point_codes=["test.kp"], primary_competency=name).primary_competency == name
    with pytest.raises(ValueError):
        RequiredQuestionFilter(core_literacies=["数据意识"])
    with pytest.raises(ValueError):
        PersonalizedQuestionFilter(knowledge_point_codes=["test.kp"], primary_competency="数据意识")


@pytest.mark.parametrize("names", [
    ["空间观念"],
    ["数据观念"],
    ["数据观念", "创新意识"],
])
def test_formal_question_labels_accept_graph_competencies(monkeypatch, names):
    monkeypatch.setattr(
        knowledge_catalog, "formal_kp", lambda code: {"core_competencies": names}
    )
    assert knowledge_catalog.formal_question_literacy("test.kp") == "、".join(names)


def test_formal_question_labels_do_not_alias_primary_school_data_awareness(monkeypatch):
    monkeypatch.setattr(
        knowledge_catalog, "formal_kp",
        lambda code: {"core_competencies": ["数据意识"]},
    )
    with pytest.raises(ValueError, match="formal question-label mapping"):
        knowledge_catalog.formal_question_literacy("test.kp")


@pytest.mark.parametrize("change", ["legacy_eight", "missing", "duplicate", "reordered", "blank"])
def test_formal_dimensions_reject_a_stale_or_invalid_export(monkeypatch, change):
    catalog = deepcopy(knowledge_catalog.load_catalog())
    dimensions = catalog["formal_question_labels"]["core_literacies"]
    if change == "legacy_eight":
        dimensions.remove("空间观念")
        dimensions[dimensions.index("数据观念")] = "数据意识"
    elif change == "missing":
        dimensions.pop()
    elif change == "duplicate":
        dimensions.append(dimensions[0])
    elif change == "reordered":
        dimensions.reverse()
    else:
        dimensions[0] = " "
    monkeypatch.setattr(knowledge_catalog, "load_catalog", lambda: catalog)
    with pytest.raises(ValueError, match="differ from the HTML knowledge graph"):
        knowledge_catalog.formal_literacy_dimensions()
