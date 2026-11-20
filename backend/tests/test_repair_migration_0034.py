import pytest

from scripts.repair_migration_0034 import migration_action


def test_old_s4_0034_needs_relabel_before_linear_upgrade():
    assert migration_action("0034", True, False) == "relabel_s4_to_0033"
    assert migration_action("0034", False, True) == "unchanged"
    assert migration_action("0034", True, True) == "unchanged"
    assert migration_action("0038", True, True) == "unchanged"
    with pytest.raises(ValueError, match="均不存在"):
        migration_action("0034", False, False)
