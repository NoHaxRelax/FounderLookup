"""Atomic durable acceptance for terminal live Screening outputs and safe audit."""

from founderlookup.application.service import LiveScreeningContext
from founderlookup.domain.assessment import AssessmentEnvelope
from founderlookup.domain.runs import PipelineRun
from founderlookup.infrastructure.persistence import NewRecord, RecordCategory, SQLiteMemory
from founderlookup.screening.inbound_graph import InboundStageAudit


class SQLiteLiveScreeningStore:
    def __init__(self, memory: SQLiteMemory) -> None:
        self._memory = memory

    def persist(
        self,
        context: LiveScreeningContext,
        assessment: AssessmentEnvelope,
        run: PipelineRun,
        audit: tuple[InboundStageAudit, ...] | None,
    ) -> None:
        records = [
            NewRecord(
                category=RecordCategory.ASSESSMENT,
                record_id=assessment.assessment_id,
                version_id=assessment.assessment_version_id,
                subject_id=context.opportunity_id,
                recorded_at=assessment.created_at,
                payload=assessment.model_dump(mode="json"),
            ),
            NewRecord(
                category=RecordCategory.PIPELINE_RUN,
                record_id=run.run_id,
                version_id=f"{run.run_id}:terminal",
                subject_id=run.input_snapshot_id,
                recorded_at=run.completed_at or assessment.created_at,
                payload=run.model_dump(mode="json"),
            ),
            NewRecord(
                category=RecordCategory.INBOUND_ANALYSIS_AUDIT,
                record_id=f"inbound-audit:{run.run_id}",
                version_id="inbound-audit.v1",
                subject_id=context.opportunity_id,
                recorded_at=run.completed_at or assessment.created_at,
                payload={
                    "request_id": context.request.request_id,
                    "input_snapshot_id": context.snapshot.input_snapshot_id,
                    "topology": (
                        ("start", "market"),
                        ("start", "idea"),
                        ("start", "founder"),
                        ("market+idea+founder", "adversarial"),
                        ("adversarial", "memo"),
                        ("memo", "end"),
                    ),
                    "stages": (
                        tuple(
                            {
                                "stage": item.stage,
                                "status": item.status,
                                "safe_code": item.safe_code,
                            }
                            for item in audit
                        )
                        if audit is not None
                        else tuple(
                            {
                                "stage": "live_intelligence",
                                "status": "fallback",
                                "safe_code": failure.safe_code,
                            }
                            for failure in run.failures
                        )
                    ),
                    "contains_human_decision": False,
                },
            ),
        ]
        if assessment.memo is not None:
            records.append(
                NewRecord(
                    category=RecordCategory.MEMO,
                    record_id=assessment.memo.memo_id,
                    version_id=assessment.memo.memo_version_id,
                    subject_id=assessment.memo.opportunity_id,
                    recorded_at=assessment.memo.generated_at,
                    payload=assessment.memo.model_dump(mode="json"),
                )
            )
        if assessment.recommendation is not None:
            records.append(
                NewRecord(
                    category=RecordCategory.RECOMMENDATION,
                    record_id=assessment.recommendation.recommendation_id,
                    version_id=assessment.recommendation.recommendation_version_id,
                    subject_id=context.opportunity_id,
                    recorded_at=assessment.recommendation.created_at,
                    payload=assessment.recommendation.model_dump(mode="json"),
                )
            )
        self._memory.append_many_idempotent(records)


__all__ = ["SQLiteLiveScreeningStore"]
