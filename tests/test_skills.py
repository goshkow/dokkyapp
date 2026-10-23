from koddi.skills import apply_skill, create_skill


def test_apply_skill_creates_task_with_checklist() -> None:
    skill = create_skill(
        "release-check", "Release sequence", {"mode": "review", "steps": ["lint", "tests"]}
    )
    result = apply_skill(skill["id"], "Validate release")
    assert result["ok"] is True
    assert result["task"]["title"] == "Validate release"
    assert [item["title"] for item in result["task"]["checklist"]] == ["lint", "tests"]
