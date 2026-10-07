"""The orchestrator.

Plain code decides which phase runs next, what runs in parallel, and when to
stop. Models do work inside a phase; they never choose the next one. (INV-6)

Several of the guarantees this system sells live in this package as pure
functions: `compute_trace` and `compute_unclaimed` (INV-3, read forwards and
backwards), `merge_adversary` (AC-8.7), `restore_packet` (INV-4) and
`compute_rework` (INV-6, at the human boundary). None of them asks a model
anything.

Each group of pure functions has a file of its own, the checks are a
subpackage split by theme, and `Factory` is assembled from one mixin per
stretch of the run in `factory/`. Every public name is re-exported here, so
`from factory.pipeline import X` and `pipeline.X` both reach it. Patching a
name here does not reach the code that uses it: patch the module that code
lives in.
"""

from __future__ import annotations

from .text import (
    PHASE_NAMES,
    INTAKE_PHASES,
    BUILD_PHASES,
    _env_label,
    reset_phases,
    VERDICT_RANK,
    AS_BUILT_WAIT_S,
    _AS_BUILT_TASKS,
    _now,
    _slug,
    _json,
    gate_status_line,
    files_section,
    _PROSE_IN_PATH,
    classify_claimed_path,
    tree_section,
    _first_lines,
)
from .trace import (
    criterion_files,
    disclaimed_criteria,
    compute_trace,
    DiffStat,
    change_size,
    compute_unclaimed,
)
from .flags import compute_rework, human_flags, _one_line, _flag_note, flag_findings, seed_flags
from .records import (
    current_record,
    current_payload,
    _DID,
    irreversible_changes,
    promoted_this_pass,
    resume_round,
    recorded_attempt,
    guides_held,
)
from .oracle_suite import (
    _PATH_IN_PROSE,
    _disclaimed_paths,
    _title_key,
    _CRITERION_ID,
    assemble_oracle_suite,
    assemble_breaker_suite,
    _suite_detail,
    oracle_files,
    place_blind_file,
    _commit_all,
    set_aside,
    runner_naming,
    blind_write_targets,
    feature_test_dir,
    unruled,
    usable_suite,
    normalize_criterion_ids,
    retarget_command,
)
from .protection import protected_paths, project_files_in, is_protected
from .ledger import (
    restore_dispositions,
    _CLEARABLE_OUTCOMES,
    _SETTLED_BUT_CHECKABLE,
    _LEGACY_CHECK_IDS,
    FindingLedger,
    SUSPECT_TEST,
    suspect_tests_to_a_person,
    _agent_of,
)
from .probes import (
    probe_evidence,
    _PROBE_SOURCE_CHARS,
    probe_budget,
    probe_finding_id,
    breaker_findings,
    probes_proven_passing,
    breaker_detail,
)
from .repairs import oracle_problems, plan_repairs, reopen_after_panel
from .ownership import (
    this_process,
    _blocked_reason,
    HarnessBlocked,
    owner_is_alive,
    _pid_started,
    is_orphaned,
)
from .checks.gate_results import (
    check_test_execution,
    check_attribution,
    _raised_in,
    check_blind_helper_errors,
    check_leaks,
    check_unreset_sort,
    check_oracle_file_failures,
    check_attribution_wiring,
    check_testing_conventions,
)
from .checks.agents import (
    check_setup,
    check_unit_environments,
    AGENT_FAILURES,
    check_agent_failures,
    HEADROOM_TIGHT,
    check_headroom,
    observed_draw,
    check_route_fallbacks,
    _ENV_IN_COMMAND,
    _ENV_IN_FILE,
    check_environment_overrides,
    _gap_line,
    check_unit_delivery,
    check_independence,
    check_budget_coverage,
    check_harness_delivery,
    check_write_collisions,
)
from .checks.seams import (
    NO_SEAM_CHECK,
    SEAM_GATE,
    settle_integration,
    is_seam_check,
    seam_checks,
    integration_detail,
    seam_gates,
    check_seams,
)
from .checks.repo import (
    check_verification_surface,
    check_settings,
    check_settings_files,
    settings_section,
    check_guides_changed,
    check_settings_changed,
    check_new_suppressions,
    _git_lines,
    _HUNK,
    check_hollow_tests,
    changed_lines,
    check_new_quality_findings,
    _ranges,
    check_patch_coverage,
    check_destructive_writes,
    check_declared_changes,
)
from .checks.cut import (
    _UNIT_SYMBOL,
    _unit_symbols,
    unit_dependencies,
    check_unit_independence,
    CHECK_LIMIT,
    anchored_objections,
    settle_objections,
    objection_block,
    criteria_changed,
    units_changed,
    cut_facts,
    merge_units,
    cut_open,
    pending_ruling,
    check_cut,
)
from .checks.blind import (
    check_oracle_discards,
    blind_summary,
    check_blind_attribution,
    available_fixtures,
    _leading_name,
    unmet_setup,
    promised_setup,
    check_spec_testability,
    check_blind_suite_missing,
    browser_ran,
    current_recordings,
    check_trace_coverage,
    check_recording_coverage,
    check_blind_tags,
    check_oracle_requirements,
    check_blind_suite_loads,
)
from .packet import (
    merge_scouts,
    merge_reviews,
    what_landed,
    dedupe_findings,
    _severity_rank,
    order_packet,
    restore_packet,
    _gate_evidence,
    _why_unsettled,
    case_verdicts,
    reported_in,
    compute_qa,
    OPEN_OUTCOMES,
    _open_blockers,
    hold_for_manual_checks,
    compute_stats,
    collect_disclosures,
)
from .recordings import (
    _TRACE_PLUMBING,
    _TRACE_FRAMES_MAX,
    _TRACE_BLANK_BYTES_PER_PIXEL,
    _jpeg_size,
    _without_leading_blanks,
    trace_player,
)
from .helpers import _dedupe_writes, _round_unit_id, _renumber_decisions
from .factory import Factory, ProgressCallback
# Names imported from elsewhere rather than defined in this package, which code
# and tests take from here.
from ..containers import runner_for
from ..gates import match_case, run_gates
from ..projects import ProjectError
from ..schemas import Spec
from ..workspace import testing_context, verify_context
