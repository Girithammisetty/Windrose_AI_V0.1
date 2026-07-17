"""Auto-generated model card assembly (EXP-FR-040, AC-6/AC-14).

``build_auto_fields`` composes the immutable/auto portion of a model card from
the source run + version + experiment. The editable overlay
(intended_use / limitations / evaluation_summary / ethical_considerations) is
merged at read time; ``render_markdown`` produces the ?format=markdown export.
"""

from __future__ import annotations

from app.domain.entities import (
    MODEL_TYPE_LABELS,
    STAGE_LABELS,
    Experiment,
    ModelVersion,
    RegisteredModel,
    Run,
)

OVERLAY_FIELDS = ("intended_use", "limitations", "evaluation_summary", "ethical_considerations")


def build_auto_fields(
    *,
    model: RegisteredModel,
    version: ModelVersion,
    experiment: Experiment,
    run: Run,
    visible_params: dict[str, str],
    final_metrics: dict[str, float],
    metric_chart_artifacts: list[str],
    promotion_history: list[dict],
    via_agent: dict | None,
) -> dict:
    return {
        "model_name": model.name,
        "version": version.version,
        "stage": STAGE_LABELS[version.stage],
        "algorithm": run.algorithm,
        "model_type": MODEL_TYPE_LABELS[model.model_type],
        "owner_id": model.owner_id,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "stage_updated_at": (
            version.stage_updated_at.isoformat() if version.stage_updated_at else None
        ),
        "source_run_urn": f"wr:{run.tenant_id}:experiment:run/{run.id}",
        "source_mlflow_run_id": run.mlflow_run_id,
        "experiment_id": experiment.id,
        "experiment_name": experiment.name,
        "training_pipeline_urn": experiment.training_pipeline_urn,
        "model_pipeline_urn": experiment.model_pipeline_urn,
        "feature_engineering_pipeline_urn": experiment.feature_engineering_pipeline_urn,
        "input_dataset_urns": run.input_dataset_urns,
        "params": visible_params,
        "final_metrics": final_metrics,
        "metric_chart_artifacts": metric_chart_artifacts,
        "input_schema": version.input_schema,
        "output_schema": version.output_schema,
        "flavor": version.flavor,
        "mlflow_model_ref": version.mlflow_model_ref,
        "promotion_history": promotion_history,
        "via_agent": via_agent,
        "training_data_unavailable": False,
    }


def merge_card(auto_fields: dict, overlay: dict) -> dict:
    merged = dict(auto_fields)
    merged["overlay"] = {k: overlay.get(k) for k in OVERLAY_FIELDS}
    return merged


def render_markdown(auto_fields: dict, overlay: dict) -> str:
    lines: list[str] = []
    lines.append(f"# Model Card — {auto_fields.get('model_name')} v{auto_fields.get('version')}")
    lines.append("")
    lines.append(f"- **Stage:** {auto_fields.get('stage')}")
    lines.append(f"- **Model type:** {auto_fields.get('model_type')}")
    lines.append(f"- **Algorithm:** {auto_fields.get('algorithm')}")
    lines.append(f"- **Owner:** {auto_fields.get('owner_id')}")
    lines.append(f"- **Experiment:** {auto_fields.get('experiment_name')}")
    lines.append(f"- **Source run:** {auto_fields.get('source_mlflow_run_id')}")
    lines.append(f"- **Training pipeline:** {auto_fields.get('training_pipeline_urn')}")
    if auto_fields.get("training_data_unavailable"):
        lines.append("- **WARNING:** training data has been deleted (right-to-erasure).")
    lines.append("")
    lines.append("## Input datasets")
    for urn in auto_fields.get("input_dataset_urns") or []:
        lines.append(f"- {urn}")
    lines.append("")
    lines.append("## Parameters")
    for k, v in (auto_fields.get("params") or {}).items():
        lines.append(f"- `{k}` = `{v}`")
    lines.append("")
    lines.append("## Metrics")
    for k, v in (auto_fields.get("final_metrics") or {}).items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("## Evaluation & governance (editable)")
    for field in OVERLAY_FIELDS:
        value = overlay.get(field)
        lines.append(f"### {field.replace('_', ' ').title()}")
        lines.append(value if value else "_not provided_")
        lines.append("")
    lines.append("## Promotion history")
    for entry in auto_fields.get("promotion_history") or []:
        lines.append(
            f"- {entry.get('from_stage')} -> {entry.get('to_stage')} "
            f"({entry.get('status')}) by {entry.get('decision_actor')}"
        )
    return "\n".join(lines)
