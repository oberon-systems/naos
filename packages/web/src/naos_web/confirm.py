from dataclasses import dataclass

from naos_web.rows import Fact, RunDetailRow

LEAD = "A new run will be created with these options:"


@dataclass(frozen=True)
class Confirm:
    title: str
    question: str
    action: str
    back: str
    footer: str
    danger: bool = False
    note: str | None = None
    lead: str | None = None
    facts: tuple[Fact, ...] = ()
    key: str | None = None


def stop(run: RunDetailRow) -> Confirm:
    return Confirm(
        title="Stop run?",
        question=f"Are you sure you want to stop {run.id}?",
        note="The runner shuts the VM down and the run ends as STOPPED. Nothing is retried.",
        action=f"/runs/{run.id}/stop",
        back=f"/runs/{run.id}",
        footer="the audit keeps who stopped it",
        danger=True,
    )


# The rerun carries the spec and nothing else, so the new run waits for whichever
# runner is free rather than the one that held this one.
def rerun(run: RunDetailRow, key: str) -> Confirm:
    facts = {fact.label: fact for fact in run.run} | {fact.label: fact for fact in run.policy}
    options = [facts[label] for label in ("Spec", "Image", "Profile") if label in facts]
    options.append(Fact("Runner", "any live runner with a free slot"))
    if "Timeouts" in facts:
        options.append(facts["Timeouts"])
    return Confirm(
        title="Rerun?",
        question=f"Are you sure you want to rerun {run.id}?",
        lead=LEAD,
        facts=tuple(options),
        action=f"/runs/{run.id}/rerun",
        back=f"/runs/{run.id}",
        footer="a new run id is issued · this run is kept",
        key=key,
    )
