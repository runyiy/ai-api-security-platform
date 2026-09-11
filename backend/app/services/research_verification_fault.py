"""One durable clock/expiry fence per exact intent; never an approval/event claim."""
from sqlalchemy import select,text,func
from sqlalchemy.dialects.postgresql import insert
from app.db.models.research_context import ResearchContext
from app.db.models.research_intent import IntentVersion
from app.db.models.research_verification import VerificationClockFault as Fault
from app.schemas.research_intent import IntentError


def check(db,context_id,reference):
    row=db.scalar(select(Fault.id).join(IntentVersion,IntentVersion.id==Fault.intent_id).where(
        Fault.context_id==context_id,Fault.digest==reference['digest'],
        IntentVersion.context_id==context_id,IntentVersion.number==reference['number'],
        IntentVersion.version==reference['version'],IntentVersion.digest==reference['digest']).limit(1))
    if row is not None:raise IntentError('verification_clock_invalidated')


def record(bind,project,context_id,reference):
    # No row/table locks from the consumer are reacquired here. The soft exact
    # reference comes only from a matching committed owned intent, never input
    # text. A failed conversion has no committed intent to fence.
    with bind.begin() as db:
        db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
        db.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        row=db.execute(select(IntentVersion.id,IntentVersion.digest).join(
            ResearchContext,ResearchContext.id==IntentVersion.context_id).where(
            ResearchContext.project_number==project,ResearchContext.id==context_id,
            IntentVersion.context_id==context_id,IntentVersion.number==reference['number'],
            IntentVersion.version==reference['version'],IntentVersion.digest==reference['digest']).limit(1)).first()
        if row is None:return
        db.execute(insert(Fault).values(context_id=context_id,intent_id=row.id,digest=row.digest,
            recorded_at=func.clock_timestamp()).on_conflict_do_nothing(index_elements=['intent_id']))
