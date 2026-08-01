import pytest
from lmcache.v1.distributed.config import (
    RuntimePolicyState,
    RuntimePolicyUpdate,
)


def test_runtime_policy_config_defaults():
    state = RuntimePolicyState()
    assert state.version == 1
    assert state.store_policy == "default"
    assert state.trigger_watermark == 0.8
    assert state.eviction_ratio == 0.2


def test_runtime_policy_update_schema():
    update = RuntimePolicyUpdate(
        expected_version=1,
        store_policy="skip_l1",
        trigger_watermark=0.9,
    )
    assert update.expected_version == 1
    assert update.store_policy == "skip_l1"
    assert update.trigger_watermark == 0.9


def test_storage_manager_runtime_policy_updates():
    from lmcache.v1.distributed.storage_manager import StorageManager
    from lmcache.v1.config import LMCacheEngineConfig

    config = LMCacheEngineConfig.from_defaults()
    manager = StorageManager(config)

    # 1. Initial State
    policy = manager.get_runtime_policy()
    assert policy.version == 1

    # 2. Valid Update
    update = RuntimePolicyUpdate(
        expected_version=1,
        store_policy="skip_l1",
        trigger_watermark=0.9,
    )
    res = manager.update_runtime_policy(update)
    assert res["status"] == "updated"
    assert res["version"] == 2
    assert "store_policy" in res["applied"]

    # 3. Version Conflict Check
    stale_update = RuntimePolicyUpdate(
        expected_version=1,
        store_policy="default",
    )
    conflict = manager.update_runtime_policy(stale_update)
    assert conflict["valid"] is False
    assert conflict["error"] == "version_conflict"