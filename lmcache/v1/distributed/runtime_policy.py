# SPDX-License-Identifier: Apache-2.0
"""Domain models and validation for runtime management-policy updates.

This module deliberately has no dependency on the HTTP API or controller
implementations.  ``StorageManager`` owns the live state and uses these
immutable values to validate an update before it mutates any controller.
"""

# Standard
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Sequence


@dataclass(frozen=True)
class RuntimePolicyError:
    """Structured reason why one runtime-policy field was rejected.

    Attributes:
        code: Stable machine-readable error code.
        field: Dot/bracket path of the rejected field.
        message: Human-readable explanation of the rejection.
        current: Current value, when useful to explain the conflict.
        requested: Requested value, when useful to explain the conflict.
    """

    code: str
    field: str
    message: str
    current: str | float | int | None = None
    requested: str | float | int | None = None


@dataclass(frozen=True)
class RuntimeTunableCapability:
    """Capability metadata for one numeric runtime tunable.

    Attributes:
        current: Current effective value.
        minimum: Inclusive lower bound accepted by the runtime.
        maximum: Inclusive upper bound accepted by the runtime.
        effective_on: Data-plane boundary at which the value takes effect.
    """

    current: float
    minimum: float = 0.0
    maximum: float = 1.0
    effective_on: str = "next_eviction_loop_tick"


@dataclass(frozen=True)
class EvictionTunablesCapabilities:
    """Capabilities for all hot-updateable eviction tunables.

    Attributes:
        trigger_watermark: Usage threshold which starts eviction.
        eviction_ratio: Allocated-memory fraction to evict when triggered.
    """

    trigger_watermark: RuntimeTunableCapability
    eviction_ratio: RuntimeTunableCapability


@dataclass(frozen=True)
class SelectorPolicyCapabilities:
    """Capability metadata for a stateless selector-plane policy.

    Attributes:
        current: Name of the currently selected policy.
        registered: Names accepted for this selector.
        hot_swappable: Whether the selector may change at runtime.
        stateful: Whether a policy instance owns migratable state.
        effective_on: Data-plane boundary at which a replacement takes effect.
    """

    current: str
    registered: tuple[str, ...]
    hot_swappable: bool = True
    stateful: bool = False
    effective_on: str = "next_plan"


@dataclass(frozen=True)
class EvictionPolicyCapabilities:
    """Capability metadata for an eviction controller.

    Attributes:
        policy: Current eviction policy class name.
        policy_hot_swappable: Whether the policy class can be migrated live.
        stateful: Whether the policy class owns internal state.
        runtime_tunables: Safe tunables exposed by the controller.
    """

    policy: str
    policy_hot_swappable: bool
    stateful: bool
    runtime_tunables: EvictionTunablesCapabilities


@dataclass(frozen=True)
class L2EvictionPolicyCapabilities(EvictionPolicyCapabilities):
    """Eviction capability metadata for one configured L2 adapter.

    Attributes:
        adapter_id: Stable index used to target the adapter in update requests.
        adapter_name: Human-readable registered adapter type name.
    """

    adapter_id: int
    adapter_name: str


@dataclass(frozen=True)
class RuntimePolicyCapabilities:
    """Full capability snapshot returned by a local storage manager.

    Attributes:
        store_policy: Store selector capabilities.
        prefetch_policy: Prefetch selector capabilities.
        l1_eviction: L1 eviction capabilities.
        l2_eviction: Per-adapter L2 eviction capabilities.
    """

    store_policy: SelectorPolicyCapabilities
    prefetch_policy: SelectorPolicyCapabilities
    l1_eviction: EvictionPolicyCapabilities
    l2_eviction: tuple[L2EvictionPolicyCapabilities, ...] = ()


@dataclass(frozen=True)
class EvictionTunables:
    """Current values for the eviction tunable plane.

    Attributes:
        trigger_watermark: Usage threshold which starts eviction.
        eviction_ratio: Allocated-memory fraction to evict when triggered.
    """

    trigger_watermark: float = 0.8
    eviction_ratio: float = 0.2


@dataclass(frozen=True)
class L1EvictionPolicyState:
    """Current runtime state for the L1 eviction policy plane.

    Attributes:
        policy: Configured eviction policy class name.
        tunables: Hot-updateable policy knobs.
    """

    policy: str = "LRU"
    tunables: EvictionTunables = field(default_factory=EvictionTunables)


@dataclass(frozen=True)
class L2EvictionPolicyState:
    """Current runtime state for one L2 adapter's eviction policy plane.

    Attributes:
        adapter_id: Stable index used to target the adapter in updates.
        adapter_name: Human-readable registered adapter type name.
        policy: Configured eviction policy class name.
        tunables: Hot-updateable policy knobs.
    """

    adapter_id: int
    adapter_name: str
    policy: str = "LRU"
    tunables: EvictionTunables = field(default_factory=EvictionTunables)


@dataclass(frozen=True)
class RuntimePolicyState:
    """Current local runtime management-policy state.

    Attributes:
        version: Monotonically increasing optimistic-concurrency version.
        store_policy: Active L2 store selector name.
        prefetch_policy: Active L2 prefetch selector name.
        l1_eviction: L1 eviction policy state.
        l2_eviction: Per-adapter L2 eviction policy states.
    """

    version: int = 1
    store_policy: str = "default"
    prefetch_policy: str = "default"
    l1_eviction: L1EvictionPolicyState = field(default_factory=L1EvictionPolicyState)
    l2_eviction: tuple[L2EvictionPolicyState, ...] = ()


@dataclass(frozen=True)
class EvictionTunablesUpdate:
    """Requested changes to the hot-updateable eviction tunable plane.

    Attributes:
        trigger_watermark: Replacement usage threshold, if supplied.
        eviction_ratio: Replacement eviction fraction, if supplied.
    """

    trigger_watermark: float | None = None
    eviction_ratio: float | None = None


@dataclass(frozen=True)
class EvictionPolicyUpdate:
    """Requested update for one eviction policy plane.

    ``policy`` is present so callers receive a structured
    ``state_migration_required`` error instead of silently ignoring a
    state-plane request.  Phase 1 only applies ``tunables``.

    Attributes:
        policy: Requested eviction policy class name, if any.
        tunables: Requested tunable-plane changes, if any.
    """

    policy: str | None = None
    tunables: EvictionTunablesUpdate | None = None


@dataclass(frozen=True)
class L2EvictionUpdate:
    """Requested eviction update for one L2 adapter.

    Attributes:
        adapter_id: Stable index of the target L2 adapter.
    """

    adapter_id: int
    policy: str | None = None
    tunables: EvictionTunablesUpdate | None = None


@dataclass(frozen=True)
class RuntimePolicyUpdate:
    """Requested local runtime management-policy update.

    All populated fields are validated before any state is changed.  In Phase
    1, selectors and eviction tunables can change; eviction policy classes are
    explicitly rejected unless they already match the active class.

    Attributes:
        expected_version: Required current version for optimistic concurrency.
        store_policy: Replacement store selector name, if any.
        prefetch_policy: Replacement prefetch selector name, if any.
        l1_eviction: Requested L1 eviction update, if any.
        l2_eviction: Requested per-adapter L2 eviction updates.
    """

    expected_version: int | None = None
    store_policy: str | None = None
    prefetch_policy: str | None = None
    l1_eviction: EvictionPolicyUpdate | None = None
    l2_eviction: tuple[L2EvictionUpdate, ...] = ()


@dataclass(frozen=True)
class RuntimePolicyValidationResult:
    """Outcome of validating a runtime management-policy update.

    Attributes:
        valid: Whether the complete update can be applied atomically.
        applied_fields: Fields that would change if the update is applied.
        errors: Structured errors that prevented the update.
    """

    valid: bool
    applied_fields: tuple[str, ...] = ()
    errors: tuple[RuntimePolicyError, ...] = ()


@dataclass(frozen=True)
class RuntimePolicyApplyResult:
    """Outcome of applying a fully validated runtime-policy update.

    Attributes:
        state: State after the operation; unchanged when validation fails.
        applied_fields: Fields changed by the operation.
        errors: Structured errors when the operation was rejected.
    """

    state: RuntimePolicyState
    applied_fields: tuple[str, ...] = ()
    errors: tuple[RuntimePolicyError, ...] = ()


def build_runtime_policy_capabilities(
    state: RuntimePolicyState,
    *,
    registered_store_policies: Sequence[str],
    registered_prefetch_policies: Sequence[str],
) -> RuntimePolicyCapabilities:
    """Build the capability snapshot corresponding to ``state``.

    Args:
        state: Current runtime policy state.
        registered_store_policies: Store selector names installed in this node.
        registered_prefetch_policies: Prefetch selector names installed in this node.

    Returns:
        Capability metadata that describes the Phase 1 safe update surface.
    """

    return RuntimePolicyCapabilities(
        store_policy=SelectorPolicyCapabilities(
            current=state.store_policy,
            registered=tuple(registered_store_policies),
            effective_on="next_store_plan",
        ),
        prefetch_policy=SelectorPolicyCapabilities(
            current=state.prefetch_policy,
            registered=tuple(registered_prefetch_policies),
            effective_on="next_prefetch_request",
        ),
        l1_eviction=_build_eviction_capabilities(state.l1_eviction),
        l2_eviction=tuple(
            _build_l2_eviction_capabilities(entry) for entry in state.l2_eviction
        ),
    )


def validate_runtime_policy_update(
    update: RuntimePolicyUpdate,
    state: RuntimePolicyState,
    *,
    registered_store_policies: Sequence[str],
    registered_prefetch_policies: Sequence[str],
) -> RuntimePolicyValidationResult:
    """Validate an update without changing its target policy state.

    Args:
        update: Requested update to validate.
        state: Current runtime policy state.
        registered_store_policies: Store selector names installed in this node.
        registered_prefetch_policies: Prefetch selector names installed in this node.

    Returns:
        Validation result with all discovered errors and changed field paths.
    """

    errors: list[RuntimePolicyError] = []
    _validate_expected_version(update, state, errors)
    _validate_selector(
        update.store_policy,
        state.store_policy,
        "store_policy",
        registered_store_policies,
        errors,
    )
    _validate_selector(
        update.prefetch_policy,
        state.prefetch_policy,
        "prefetch_policy",
        registered_prefetch_policies,
        errors,
    )
    _validate_eviction_update(
        update.l1_eviction,
        state.l1_eviction,
        "l1_eviction",
        errors,
    )

    l2_state_by_id = {entry.adapter_id: entry for entry in state.l2_eviction}
    updated_adapter_ids: set[int] = set()
    for entry in update.l2_eviction:
        field_prefix = f"l2_eviction[{entry.adapter_id}]"
        if entry.adapter_id in updated_adapter_ids:
            errors.append(
                RuntimePolicyError(
                    code="duplicate_adapter_id",
                    field=f"{field_prefix}.adapter_id",
                    message="Each L2 adapter may be updated at most once per request",
                    requested=entry.adapter_id,
                )
            )
            continue
        updated_adapter_ids.add(entry.adapter_id)

        current = l2_state_by_id.get(entry.adapter_id)
        if current is None:
            errors.append(
                RuntimePolicyError(
                    code="unknown_adapter_id",
                    field=f"{field_prefix}.adapter_id",
                    message=f"Unknown L2 adapter_id {entry.adapter_id}",
                    requested=entry.adapter_id,
                )
            )
            continue
        _validate_eviction_update(entry, current, field_prefix, errors)

    if errors:
        return RuntimePolicyValidationResult(valid=False, errors=tuple(errors))
    return RuntimePolicyValidationResult(
        valid=True,
        applied_fields=_get_applied_fields(update, state),
    )


def apply_runtime_policy_update(
    update: RuntimePolicyUpdate,
    state: RuntimePolicyState,
    *,
    registered_store_policies: Sequence[str],
    registered_prefetch_policies: Sequence[str],
) -> RuntimePolicyApplyResult:
    """Apply a runtime-policy update only when full validation succeeds.

    Args:
        update: Requested update to apply.
        state: Current runtime policy state.
        registered_store_policies: Store selector names installed in this node.
        registered_prefetch_policies: Prefetch selector names installed in this node.

    Returns:
        Apply result.  On validation failure, its state is the unmodified
        input state and its errors describe every rejected field.
    """

    validation = validate_runtime_policy_update(
        update,
        state,
        registered_store_policies=registered_store_policies,
        registered_prefetch_policies=registered_prefetch_policies,
    )
    if not validation.valid:
        return RuntimePolicyApplyResult(state=state, errors=validation.errors)
    if not validation.applied_fields:
        return RuntimePolicyApplyResult(state=state)

    new_l1_eviction = _apply_eviction_update(state.l1_eviction, update.l1_eviction)
    updates_by_adapter_id = {
        entry.adapter_id: entry for entry in update.l2_eviction
    }
    new_l2_eviction = tuple(
        _apply_eviction_update(entry, updates_by_adapter_id.get(entry.adapter_id))
        for entry in state.l2_eviction
    )
    new_state = replace(
        state,
        version=state.version + 1,
        store_policy=update.store_policy or state.store_policy,
        prefetch_policy=update.prefetch_policy or state.prefetch_policy,
        l1_eviction=new_l1_eviction,
        l2_eviction=new_l2_eviction,
    )
    return RuntimePolicyApplyResult(
        state=new_state,
        applied_fields=validation.applied_fields,
    )


def _build_eviction_capabilities(
    state: L1EvictionPolicyState | L2EvictionPolicyState,
) -> EvictionPolicyCapabilities:
    """Build Phase 1 capabilities for one eviction policy state."""

    return EvictionPolicyCapabilities(
        policy=state.policy,
        policy_hot_swappable=False,
        stateful=True,
        runtime_tunables=EvictionTunablesCapabilities(
            trigger_watermark=RuntimeTunableCapability(
                current=state.tunables.trigger_watermark,
            ),
            eviction_ratio=RuntimeTunableCapability(
                current=state.tunables.eviction_ratio,
            ),
        ),
    )


def _build_l2_eviction_capabilities(
    state: L2EvictionPolicyState,
) -> L2EvictionPolicyCapabilities:
    """Build Phase 1 capabilities for one L2 eviction policy state."""

    capabilities = _build_eviction_capabilities(state)
    return L2EvictionPolicyCapabilities(
        adapter_id=state.adapter_id,
        adapter_name=state.adapter_name,
        policy=capabilities.policy,
        policy_hot_swappable=capabilities.policy_hot_swappable,
        stateful=capabilities.stateful,
        runtime_tunables=capabilities.runtime_tunables,
    )


def _validate_expected_version(
    update: RuntimePolicyUpdate,
    state: RuntimePolicyState,
    errors: list[RuntimePolicyError],
) -> None:
    """Add an optimistic-concurrency error when the version is stale."""

    if update.expected_version is not None and update.expected_version != state.version:
        errors.append(
            RuntimePolicyError(
                code="version_conflict",
                field="expected_version",
                message="expected_version does not match the current policy version",
                current=state.version,
                requested=update.expected_version,
            )
        )


def _validate_selector(
    requested: str | None,
    current: str,
    field: str,
    registered: Sequence[str],
    errors: list[RuntimePolicyError],
) -> None:
    """Validate one stateless selector update."""

    if requested is not None and requested not in registered:
        errors.append(
            RuntimePolicyError(
                code="unknown_policy",
                field=field,
                message=f"Unknown {field} {requested!r}",
                current=current,
                requested=requested,
            )
        )


def _validate_eviction_update(
    update: EvictionPolicyUpdate | L2EvictionUpdate | None,
    state: L1EvictionPolicyState | L2EvictionPolicyState,
    field_prefix: str,
    errors: list[RuntimePolicyError],
) -> None:
    """Validate a Phase 1 eviction update without mutating its state."""

    if update is None:
        return
    if update.policy is not None and update.policy != state.policy:
        errors.append(
            RuntimePolicyError(
                code="state_migration_required",
                field=f"{field_prefix}.policy",
                message=(
                    "Changing an eviction policy class requires a policy "
                    "snapshot/import migration contract"
                ),
                current=state.policy,
                requested=update.policy,
            )
        )
    if update.tunables is None:
        return
    for tunable_name in ("trigger_watermark", "eviction_ratio"):
        _validate_tunable(
            getattr(update.tunables, tunable_name),
            f"{field_prefix}.{tunable_name}",
            errors,
        )


def _validate_tunable(
    value: float | None,
    field: str,
    errors: list[RuntimePolicyError],
) -> None:
    """Add an error when a supplied tunable is not a finite value in [0, 1]."""

    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(
            RuntimePolicyError(
                code="invalid_tunable",
                field=field,
                message=f"{field} must be a finite number between 0.0 and 1.0",
            )
        )
        return
    if not isfinite(value) or value < 0.0 or value > 1.0:
        errors.append(
            RuntimePolicyError(
                code="invalid_tunable",
                field=field,
                message=f"{field} must be a finite number between 0.0 and 1.0",
                requested=value,
            )
        )


def _get_applied_fields(
    update: RuntimePolicyUpdate,
    state: RuntimePolicyState,
) -> tuple[str, ...]:
    """Return changed field paths for a valid update without applying it."""

    fields: list[str] = []
    if update.store_policy is not None and update.store_policy != state.store_policy:
        fields.append("store_policy")
    if update.prefetch_policy is not None and update.prefetch_policy != state.prefetch_policy:
        fields.append("prefetch_policy")
    fields.extend(
        _get_eviction_tunable_fields(
            update.l1_eviction,
            state.l1_eviction,
            "l1_eviction",
        )
    )
    l2_state_by_id = {entry.adapter_id: entry for entry in state.l2_eviction}
    for update_entry in update.l2_eviction:
        current = l2_state_by_id.get(update_entry.adapter_id)
        if current is None:
            continue
        fields.extend(
            _get_eviction_tunable_fields(
                update_entry,
                current,
                f"l2_eviction[{update_entry.adapter_id}]",
            )
        )
    return tuple(fields)


def _get_eviction_tunable_fields(
    update: EvictionPolicyUpdate | L2EvictionUpdate | None,
    state: L1EvictionPolicyState | L2EvictionPolicyState,
    field_prefix: str,
) -> tuple[str, ...]:
    """Return tunable field paths which differ from one eviction state."""

    if update is None or update.tunables is None:
        return ()
    fields: list[str] = []
    for tunable_name in ("trigger_watermark", "eviction_ratio"):
        if (
            value := getattr(update.tunables, tunable_name)
        ) is not None and value != getattr(state.tunables, tunable_name):
            fields.append(f"{field_prefix}.{tunable_name}")
    return tuple(fields)


def _apply_eviction_update(
    state: L1EvictionPolicyState | L2EvictionPolicyState,
    update: EvictionPolicyUpdate | L2EvictionUpdate | None,
) -> L1EvictionPolicyState | L2EvictionPolicyState:
    """Apply valid tunable changes to one eviction policy state."""

    if update is None or update.tunables is None:
        return state
    changes = {
        name: value
        for name in ("trigger_watermark", "eviction_ratio")
        if (value := getattr(update.tunables, name)) is not None
    }
    if not changes:
        return state
    return replace(state, tunables=replace(state.tunables, **changes))
